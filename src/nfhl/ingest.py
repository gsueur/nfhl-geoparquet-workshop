"""Ingest: FEMA ZIP (data/downloads if pre-fetched, else straight into memory) -> Arrow -> DuckDB -> bronze.

Ingest keeps no ZIP: bronze is the copy, import_log has the file name and date.
`nfhl download` is the optional pre-fetch that stores ZIPs. No GDAL CLI, no disk extraction. GDAL is still there, embedded in pyogrio,
and that is fine: the point is the shape of the pipeline, not purity.

Rough edges surfaced here:
  - .cpg missing: GDAL guesses, we force latin1 and log it in import_log
  - shapefile field names are 10 chars, so the bronze schema is ugly on purpose
  - the FEMA portal is flaky: retries with backoff, and the error lands in import_log
  - a pre-fetched ZIP in data/downloads/<fileName> is used when present, never written by ingest
  - the geometry column comes out of GDAL as `wkb_geometry`, renamed to `geometry`
  - the CRS is asserted (EPSG:4269), because a wrong CRS is a silent error later
"""

from __future__ import annotations

import io
import os
import time
import zipfile
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pyarrow as pa
import pyogrio
from tenacity import retry, stop_after_attempt, wait_exponential

from . import config
from .db import connect, parquet_options, q


def zip_name(url: str) -> str:
    """FEMA's file name for a download URL, e.g. 25001C_20260119.zip.

    The date is part of the name, so a newer delivery is a new file and an old
    cached ZIP can never shadow it.
    """
    name = parse_qs(urlparse(url).query).get("fileName", [None])[0]
    if not name:
        raise ValueError(f"no fileName in {url}")
    return os.path.basename(name)


def cache_path(url: str) -> str:
    """Where the ZIP lives locally: data/downloads/<fileName>, never synced to R2."""
    return config.path(
        config.load("workshop")["fema"].get("downloads_dir", "downloads"), zip_name(url)
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
def _download(url: str) -> bytes:
    cfg = config.load("workshop")["fema"]
    with httpx.Client(timeout=cfg["timeout_s"], follow_redirects=True) as c:
        r = c.get(url, headers={"User-Agent": cfg["user_agent"]})
        r.raise_for_status()
        return r.content


def fetch_zip(url: str) -> tuple[str, str]:
    """Make sure the ZIP is on disk. Returns (path, 'cache' | 'download').

    Used by `nfhl download` only (the optional pre-fetch, e.g. the three workshop
    states the day before). A download is written atomically (temp file, then rename), so a killed run
    never leaves a half ZIP that a later run would trust.
    """
    path = cache_path(url)
    if os.path.exists(path):
        return path, "cache"
    data = _download(url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path, "download"


def read_zip(url: str) -> tuple[bytes, str]:
    """`nfhl ingest`: the ZIP bytes, from data/downloads when present, fetched into memory otherwise.

    Ingest never writes the ZIP: bronze is the copy we keep, the control database
    keeps the file name and the date. Only `nfhl download` stores ZIPs.
    """
    path = cache_path(url)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read(), "cache"
    return _download(url), "download"


def _members(zip_bytes: bytes, layer: str) -> dict[str, str]:
    """Archive members of one shapefile layer, keyed by lowercase extension."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        out = {}
        for n in z.namelist():
            stem, _, ext = n.rpartition(".")
            if stem.upper().endswith(layer):
                out[ext.lower()] = n
        return out


def read_layer(zip_bytes: bytes, layer: str) -> tuple[pa.Table, str, str]:
    """One shapefile layer from an in-memory ZIP as an Arrow table (WKB geometry).

    Returns (table, encoding note, CRS). pyogrio accepts the ZIP bytes directly
    and mounts them under /vsimem/, so nothing touches the disk.
    """
    members = _members(zip_bytes, layer)
    if "shp" not in members:
        raise FileNotFoundError(f"{layer}.shp not in archive")
    if "cpg" in members:
        meta, table = pyogrio.raw.read_arrow(zip_bytes, layer=layer)
        encoding = f"cpg:{meta.get('encoding') or 'unknown'}"
    else:
        meta, table = pyogrio.raw.read_arrow(zip_bytes, layer=layer, encoding="latin1")
        encoding = "latin1-fallback"
    geom_col = meta.get("geometry_name") or "wkb_geometry"
    if geom_col != "geometry":
        table = table.rename_columns(
            [("geometry" if c == geom_col else c) for c in table.column_names]
        )
    return table, encoding, str(meta.get("crs") or "")


def write_bronze(
    table: pa.Table, state: str, county: str, fema_update_date, crs: str
) -> tuple[str, int]:
    layer = config.load("workshop")["fema"]["layer"]
    out = config.path("bronze", f"state={state}", f"county={county}", f"{layer}.parquet")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    con = connect()
    con.register("src", table)
    # pyogrio tags the column as GeoArrow WKB with the CRS, and DuckDB 1.5 reads
    # that tag: the registered table already exposes GEOMETRY('EPSG:4269').
    # Plain WKB bytes (an Arrow table built by hand) still need ST_GeomFromWKB.
    geom_type = con.execute("SELECT typeof(geometry) FROM src LIMIT 1").fetchone()[0]
    # typeof() spells the CRS out: GEOMETRY('EPSG:4269'), read from the GeoArrow tag.
    geom = "geometry" if geom_type.startswith("GEOMETRY") else "ST_GeomFromWKB(geometry)"
    con.execute(
        f"""
        COPY (
            SELECT * EXCLUDE (geometry),
                   '{q(state)}' AS state, '{q(county)}' AS county,
                   DATE '{fema_update_date}' AS fema_update_date,
                   now() AS ingested_at,
                   ST_SetCRS({geom}, '{crs}') AS geometry
            FROM src
        ) TO '{q(out)}' ({parquet_options()})
        """
    )
    return out, table.num_rows


def ingest_county(state: str, county: str, url: str, fema_update_date) -> dict:
    t0 = time.perf_counter()
    layer = config.load("workshop")["fema"]["layer"]
    expected_crs = config.load("mapping")["source_crs"]
    zip_bytes, source = read_zip(url)
    table, encoding, crs = read_layer(zip_bytes, layer)
    if crs != expected_crs:
        raise ValueError(f"{layer} is in {crs!r}, expected {expected_crs}")
    path, rows = write_bronze(table, state, county, fema_update_date, crs)
    return {
        "bronze_path": path,
        "bronze_rows": rows,
        "bronze_encoding": f"{encoding} zip:{source}",
        "bronze_crs": crs,
        "bronze_at": datetime.now(),
        "status": "bronze",
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }

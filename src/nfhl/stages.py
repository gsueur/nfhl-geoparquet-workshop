"""Subdivide stage (Python), plus load / gold stages (DuckDB SQL).

Every gold output is derived data, rebuilt from scratch: the output directory
is removed first. The per-county idempotence lives upstream (bronze, silver,
silver_subdivided) and in import_log.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import duckdb

from . import config, control
from .db import connect, parquet_options, q
from .subdivide import subdivide_batch


def _source_crs() -> str:
    return config.load("mapping")["source_crs"]


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _reset_dir(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)


# ---------------------------------------------------------------- subdivide
# DuckDB 1.5.6 spatial ships ST_Subdivide: one SQL statement, no WKB round trip.
# It returns one multi-geometry per row; ST_Dump splits it, path[1] numbers the
# pieces from 1. Measured 2026-09-29 against the Python bisection on Middlesex
# (951,116-vertex polygon): valid polygons only, cap respected, area preserved
# to 1e-14 (Python: 2e-5), 5.4 s against 3.2 s. The Python version stays
# behind `--engine python` for the comparison.
SUBDIVIDE_SQL = """
COPY (
    SELECT * EXCLUDE (geometry, d),
           d.path[1] - 1 AS piece_id,
           ST_SetCRS(d.geom, '{crs}') AS geometry
    FROM (
        SELECT *, unnest(ST_Dump(ST_Subdivide(geometry, {n}))) AS d
        FROM read_parquet('{src}')
    )
    WHERE ST_Dimension(d.geom) = 2 AND NOT ST_IsEmpty(d.geom)
) TO '{out}' ({opts})
"""


def subdivide_county(state: str, county: str, max_vertices: int, engine: str = "duckdb") -> dict:
    src = config.path("silver", f"state={state}", f"county={county}.parquet")
    out = config.path("silver_subdivided", f"state={state}", f"county={county}.parquet")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    con = connect()
    t0 = time.perf_counter()
    if engine == "duckdb":
        con.execute(
            SUBDIVIDE_SQL.format(
                crs=_source_crs(),
                n=int(max_vertices),
                src=q(src),
                out=q(out),
                opts=parquet_options(),
            )
        )
        rows = con.execute(f"SELECT count(*) FROM read_parquet('{q(out)}')").fetchone()[0]
    elif engine == "python":
        # WKB in, WKB out: DuckDB and shapely agree on WKB, nothing else is needed.
        # The CRS tag does not survive WKB, so it is set again on the way out.
        table = con.execute(
            f"SELECT * EXCLUDE (geometry), ST_AsWKB(geometry) AS geometry FROM read_parquet('{q(src)}')"
        ).fetch_arrow_table()
        pieces, stats = subdivide_batch(table, max_vertices=max_vertices)
        con.register("pieces", pieces)
        con.execute(
            f"""
            COPY (
                SELECT * EXCLUDE (geometry),
                       ST_SetCRS(ST_GeomFromWKB(geometry), '{_source_crs()}') AS geometry
                FROM pieces
            ) TO '{q(out)}' ({parquet_options()})
            """
        )
        rows = stats.output_rows
    else:
        raise ValueError(f"unknown subdivide engine {engine!r}: duckdb or python")
    return {
        "subdivided_rows": rows,
        "subdivide_max_vertices": max_vertices,
        "subdivide_ms": _ms(t0),
        "subdivide_at": datetime.now(),
        "status": "subdivided",
    }


# ---------------------------------------------------------------- load
def load_duckdb(db_path: str | None = None) -> dict:
    """Land silver_subdivided in a .duckdb file: Hilbert order, RTree index, validation.

    An API serves point lookups from this file (ORDER BY geometry, RTree, ANALYZE).
    The consistency check against import_log is strict: every subdivided county
    must be in, nothing else.
    """
    db_path = db_path or config.path("duckdb", "flood.duckdb")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    for p in (db_path, db_path + ".wal"):
        if os.path.exists(p):
            os.remove(p)
    con = connect(db_path, preserve_order=True)
    src = config.path("silver_subdivided") + "/*/*.parquet"
    t0 = time.perf_counter()
    con.execute(
        f"""
        CREATE TABLE flood AS
        WITH e AS (SELECT ST_Extent(ST_Extent_Agg(geometry)) AS b FROM read_parquet('{q(src)}'))
        SELECT * FROM read_parquet('{q(src)}')
        ORDER BY ST_Hilbert(geometry, (SELECT b FROM e))
        """
    )
    load_ms = _ms(t0)
    t0 = time.perf_counter()
    con.execute("CREATE INDEX flood_rtree ON flood USING RTREE (geometry);")
    con.execute("ANALYZE;")
    index_ms = _ms(t0)
    checks = con.execute(
        """
        SELECT count(*) AS rows,
               count(*) FILTER (WHERE NOT ST_IsValid(geometry)) AS invalid,
               count(*) FILTER (WHERE risk IS NULL) AS null_risk,
               count(DISTINCT (state, county)) AS counties
        FROM flood
        """
    ).fetchone()
    loaded = set(con.execute("SELECT DISTINCT state, county FROM flood").fetchall())
    con.close()
    with control.control_db() as ctl:
        expected = set(control.completed(ctl, "subdivided"))
    return {
        **dict(zip(["rows", "invalid", "null_risk", "counties"], checks, strict=True)),
        "missing": sorted(expected - loaded),
        "unexpected": sorted(loaded - expected),
        "load_ms": load_ms,
        "index_ms": index_ms,
        "path": db_path,
        "bytes": os.path.getsize(db_path),
    }


# ---------------------------------------------------------------- gold analytic
# One file per H3 cell, Hilbert order within the file, an explicit bbox struct
# for row-group pruning (GeoParquet 2.0 has no covering).
# A piece goes into every cell it overlaps, not only the cell of its centroid:
# a point near a cell edge must find the straddling pieces in its own file.
# Overlap on subdivided pieces costs 1.3 percent extra rows (MA, LA, UT: 1.29
# percent of the pieces sit in more than one cell, the most in 20; measured
# 2026-10-03). Readers that union several cells de-duplicate on
# (state, county, zone_id, piece_id).
GOLD_ANALYTIC_SQL = """
COPY (
    WITH e AS (SELECT ST_Extent(ST_Extent_Agg(geometry)) AS b FROM read_parquet('{src}')),
    pieces AS (
        SELECT *, h3_polygon_wkt_to_cells_experimental_string(ST_AsText(geometry), 'overlap', {res}) AS cells
        FROM read_parquet('{src}')
    )
    SELECT * EXCLUDE (geometry, cells),
           UNNEST(cells) AS h3_r{res},
           {{xmin: ST_XMin(geometry), ymin: ST_YMin(geometry),
             xmax: ST_XMax(geometry), ymax: ST_YMax(geometry)}} AS bbox,
           ST_SetCRS(geometry, '{crs}') AS geometry
    FROM pieces
    ORDER BY h3_r{res}, ST_Hilbert(geometry, (SELECT b FROM e))
) TO '{out}' ({opts}, PARTITION_BY (h3_r{res}), ROW_GROUP_SIZE {rg})
"""


def gold_analytic() -> dict:
    """One file per H3 cell plus cells.json, the index a reader needs before it asks for a file."""
    cfg = config.load("workshop")["gold_analytic"]
    src = config.path("silver_subdivided") + "/*/*.parquet"
    con = connect(preserve_order=True)
    # One file per cell, always: past 100 open partitions DuckDB starts data_1.parquet
    # files, which no reader asks for (cells.json, the map, the benchmarks name data_0).
    con.execute("SET partitioned_write_max_open_files = 4096")
    out = {}
    for name, sql in (("gold_analytic", GOLD_ANALYTIC_SQL),):
        dest = config.path(name)
        _reset_dir(dest)
        t0 = time.perf_counter()
        con.execute(
            sql.format(
                res=cfg["h3_resolution"],
                src=q(src),
                out=q(dest),
                rg=cfg["row_group_size"],
                crs=_source_crs(),
                opts=parquet_options(),
            )
        )
        files = list(Path(dest).rglob("*.parquet"))
        out[name] = {
            "ms": _ms(t0),
            "files": len(files),
            "bytes": sum(f.stat().st_size for f in files),
        }
        _write_cells_index(con, dest, cfg["h3_resolution"], files)
    _write_layouts_index(con)
    return out


def _write_layouts_index(con: duckdb.DuckDBPyConnection) -> None:
    """data/layouts.json: the county files of silver and silver_subdivided with their bbox and size.

    The web map can then read the same viewport from any of the three layouts:
    a county file is picked when its bbox meets the viewport, a cell file when
    the cell overlaps it.
    """
    index: dict = {}
    for layout in ("silver", "silver_subdivided"):
        root = Path(config.path(layout))
        entries = {}
        for f in sorted(root.glob("state=*/county=*.parquet")):
            x0, y0, x1, y1 = con.execute(
                f"SELECT ST_XMin(b), ST_YMin(b), ST_XMax(b), ST_YMax(b) FROM (SELECT ST_Extent(ST_Extent_Agg(geometry)) AS b FROM read_parquet('{q(f)}'))"
            ).fetchone()
            entries[f"{f.parent.name}/{f.name}"] = {
                "bytes": f.stat().st_size,
                "bbox": [x0, y0, x1, y1],
            }
        index[layout] = entries
    Path(config.data_root(), "layouts.json").write_text(json.dumps(index, indent=1))


def _write_cells_index(
    con: duckdb.DuckDBPyConnection, dest: str, res: int, files: list[Path]
) -> None:
    """gold_analytic/cells.json: which cells exist and how big their files are.

    `read_parquet([list])` fails on a missing file, so a reader (the web map,
    the benchmarks) must only ask for cells that exist. The index is a few KB.
    """
    extra = [f for f in files if f.name != "data_0.parquet"]
    if extra:
        raise RuntimeError(
            f"{len(extra)} partitions got a second file, e.g. {extra[0]}: one file per cell is the contract"
        )
    cells = {f.parent.name.split("=", 1)[1]: f.stat().st_size for f in files}
    x0, y0, x1, y1 = con.execute(
        f"SELECT min(bbox.xmin), min(bbox.ymin), max(bbox.xmax), max(bbox.ymax) FROM read_parquet('{q(dest)}/*/*.parquet')"
    ).fetchone()
    index = {
        "res": res,
        "crs": _source_crs(),
        "bbox": [x0, y0, x1, y1],
        "cells": dict(sorted(cells.items())),
        "note": "a piece is filed under every cell it overlaps; de-duplicate on (state, county, zone_id, piece_id) when reading several cells",
    }
    Path(dest, "cells.json").write_text(json.dumps(index, indent=1))

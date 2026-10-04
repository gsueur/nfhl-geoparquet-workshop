"""Every file the pipeline writes is GeoParquet 2.0. A bare DuckDB COPY is not."""

import json
import re
from pathlib import Path

import duckdb

from nfhl.db import parquet_options

ROOT = Path(__file__).parents[1]


def _geo(con: duckdb.DuckDBPyConnection, path: Path) -> dict:
    value = con.execute(
        "SELECT value FROM parquet_kv_metadata(?) WHERE decode(key) = 'geo'", [str(path)]
    ).fetchone()[0]
    return json.loads(value.decode() if isinstance(value, bytes) else value)


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("LOAD spatial")
    return con


def test_parquet_options_writes_geoparquet_2(tmp_path):
    con = _con()
    out = tmp_path / "v2.parquet"
    con.execute(
        f"""COPY (SELECT ST_SetCRS(ST_Point(-71.1097, 42.3736), 'EPSG:4269') AS geometry)
            TO '{out}' ({parquet_options(ROW_GROUP_SIZE=2000)})"""
    )
    assert _geo(con, out)["version"] == "2.0.0"
    # Native GEOMETRY logical type, CRS carried by the type itself.
    assert con.execute(f"SELECT typeof(geometry) FROM '{out}'").fetchone()[0] == (
        "GEOMETRY('EPSG:4269')"
    )


def test_bare_copy_writes_geoparquet_1_0(tmp_path):
    """The trap the slides warn about. If DuckDB changes its default, update the deck."""
    con = _con()
    out = tmp_path / "bare.parquet"
    con.execute(f"COPY (SELECT ST_Point(1, 2) AS geometry) TO '{out}' (FORMAT parquet)")
    assert _geo(con, out)["version"] == "1.0.0"


def test_every_copy_goes_through_parquet_options():
    """No COPY ... TO in the pipeline or its scripts may set its own options."""
    copy_to = re.compile(r"\)\s*TO\s+'[^']*'\s*\((?P<opts>[^\n]*)")
    for path in [*ROOT.glob("src/nfhl/*.py"), *ROOT.glob("scripts/*.py")]:
        for m in copy_to.finditer(path.read_text()):
            opts = m.group("opts")
            assert "parquet_options(" in opts or opts.startswith("{opts}"), (
                f"{path.name}: COPY TO without parquet_options(): {m.group(0)!r}"
            )

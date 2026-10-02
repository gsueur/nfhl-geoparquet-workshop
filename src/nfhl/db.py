"""DuckDB connection factory with the extensions the workshop relies on."""

from __future__ import annotations

import os

import duckdb

# GeoParquet 2.0: native Parquet GEOMETRY logical type, CRS in the schema and in
# the `geo` key. DuckDB 1.5 writes 1.0 by default and 2.0 with this option.
# It never writes 1.1 (the bbox covering), which is why the analytic gold
# carries an explicit bbox struct column for pushdown.
GEOPARQUET_VERSION = "V2"


def parquet_options(**extra: str | int) -> str:
    """COPY ... TO options shared by every stage. Extra options are appended verbatim."""
    opts: dict[str, str | int] = {
        "FORMAT": "parquet",
        "COMPRESSION": "zstd",
        "GEOPARQUET_VERSION": f"'{GEOPARQUET_VERSION}'",
    }
    opts.update(extra)
    return ", ".join(f"{k} {v}" for k, v in opts.items())


def q(s: str) -> str:
    """Escape a value for a SQL single-quoted literal: O'Brien -> O''Brien.

    County names and therefore file paths carry apostrophes (IA O'Brien,
    MD Prince George's). Every f-string that puts a path or a name inside
    quotes goes through this.
    """
    return str(s).replace("'", "''")


def connect(
    path: str = ":memory:", threads: int | None = None, preserve_order: bool = False
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL h3 FROM community; LOAD h3;")
    # Two settings people discover through OOMs. Show them on purpose.
    # Insertion order off lets DuckDB stream; on when an ORDER BY must survive a COPY.
    con.execute(f"SET preserve_insertion_order = {'true' if preserve_order else 'false'};")
    threads = threads or int(os.getenv("NFHL_THREADS", "0") or 0) or None
    if threads:
        con.execute(f"SET threads = {threads};")
    if os.getenv("NFHL_MEMORY_LIMIT"):
        con.execute(f"SET memory_limit = '{os.environ['NFHL_MEMORY_LIMIT']}';")
    if os.getenv("R2_ACCESS_KEY_ID"):
        con.execute(
            f"""
            CREATE OR REPLACE SECRET r2 (
                TYPE r2,
                KEY_ID '{os.environ["R2_ACCESS_KEY_ID"]}',
                SECRET '{os.environ["R2_SECRET_ACCESS_KEY"]}',
                ACCOUNT_ID '{os.environ["R2_ACCOUNT_ID"]}'
            );
            """
        )
    return con

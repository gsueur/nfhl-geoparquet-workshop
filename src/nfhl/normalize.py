"""Normalize: bronze (raw shapefile schema) -> silver (unified flood layer), driven by YAML.

The mapping file has two sections:
  columns: target name, type, source expression (SQL), optional default / null_if
  derived: domain logic (SQL) written against the *target* column names

Both compile to one SELECT wrapped in a CTE, so the whole normalization is
inspectable as SQL (`nfhl normalize --show-sql`). Swap the YAML and the same
code handles another messy public source.

ST_MakeValid drops the CRS tag of a geometry, so the CRS is set again on the
way out: GeoParquet 2.0 carries it in the Parquet schema, and every later
stage relies on it.
"""

from __future__ import annotations

import os
from datetime import datetime

from . import config
from .db import connect, parquet_options, q


def build_select(mapping: dict, source: str) -> str:
    cols = []
    for c in mapping["columns"]:
        expr = c["expr"]
        if "null_if" in c:
            vals = ", ".join(repr(v) for v in c["null_if"])
            expr = f"CASE WHEN ({expr}) IN ({vals}) THEN NULL ELSE ({expr}) END"
        if "default" in c:
            default = {True: "true", False: "false"}.get(c["default"], repr(c["default"]))
            expr = f"coalesce({expr}, {default})"
        cols.append(f"CAST({expr} AS {c['type']}) AS {c['name']}")
    ctx = ", ".join(mapping["context"])
    derived = ",\n           ".join(
        f"CAST(({d['sql'].strip()}) AS {d['type']}) AS {d['name']}" for d in mapping["derived"]
    )
    return f"""
    WITH mapped AS (
        SELECT {ctx},
               {", ".join(cols)}
        FROM {source}
    )
    SELECT * EXCLUDE (geometry),
           {derived},
           ST_SetCRS(geometry, '{mapping["source_crs"]}') AS geometry
    FROM mapped
    """


def normalize_county(state: str, county: str) -> dict:
    mapping = config.load("mapping")
    layer = config.load("workshop")["fema"]["layer"]
    src = config.path("bronze", f"state={state}", f"county={county}", f"{layer}.parquet")
    out = config.path("silver", f"state={state}", f"county={county}.parquet")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    con = connect()
    # file_row_number feeds zone_id, the zone key (see config/mapping.yaml).
    select = build_select(mapping, f"read_parquet('{q(src)}', file_row_number = true)")
    invalid = con.execute(
        f"SELECT count(*) FROM read_parquet('{q(src)}') WHERE NOT ST_IsValid(geometry)"
    ).fetchone()[0]
    # 1,000-row groups: a county file becomes several groups, so DuckDB spreads
    # ST_Subdivide over threads (one group, one thread: Middlesex 9.3 s against 5.4 s).
    con.execute(f"COPY ({select}) TO '{q(out)}' ({parquet_options()}, ROW_GROUP_SIZE 1000)")
    rows = con.execute(f"SELECT count(*) FROM read_parquet('{q(out)}')").fetchone()[0]
    return {
        "silver_rows": rows,
        "silver_invalid_fixed": invalid,
        "silver_at": datetime.now(),
        "status": "silver",
    }


def show_sql() -> str:
    return build_select(config.load("mapping"), "read_parquet('bronze/.../S_FLD_HAZ_AR.parquet', file_row_number = true)")

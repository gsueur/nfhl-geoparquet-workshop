"""Control plane: one DuckDB file that tracks every (state, county) through every stage.

This is the idea the whole pipeline rests on. It makes the
pipeline idempotent (rerun a stage for one county), observable (`nfhl status`),
and it is where every benchmark number lands.

`status` is the last completed stage. A stage only picks up rows sitting at
the previous status, so a failure leaves the row where it was, with `error`
filled in, and the next run retries it. `--force` bypasses the status check.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime

import duckdb

from . import config
from .config import control_db_path

STAGES = ["new", "bronze", "silver", "subdivided"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS import_log (
    state              VARCHAR,
    county             VARCHAR,
    fema_update_date   DATE,
    source_url         VARCHAR,
    dfirm_id           VARCHAR,     -- FEMA id, '25001C': county-wide DFIRMs end with C
    zip_size_mb        DOUBLE,      -- as printed by the portal, before download
    zip_name           VARCHAR,     -- FEMA's file name, '25001C_20260119.zip': the date is in it
    bronze_path        VARCHAR,
    bronze_rows        BIGINT,
    bronze_encoding    VARCHAR,     -- what GDAL used, and whether a .cpg was there
    bronze_crs         VARCHAR,
    bronze_at          TIMESTAMP,
    silver_rows        BIGINT,
    silver_invalid_fixed BIGINT,    -- geometries that ST_MakeValid changed
    silver_at          TIMESTAMP,
    subdivided_rows    BIGINT,
    subdivide_max_vertices INTEGER,
    subdivide_ms       INTEGER,
    subdivide_at       TIMESTAMP,
    status             VARCHAR,     -- last completed stage: new | bronze | silver | subdivided
    error              VARCHAR,     -- last error at the next stage, NULL once it succeeds
    error_at           TIMESTAMP,
    PRIMARY KEY (state, county)
);

CREATE TABLE IF NOT EXISTS benchmarks (
    run_at      TIMESTAMP DEFAULT now(),
    stage       VARCHAR,     -- subdivide | rtree | gold_analytic | map
    name        VARCHAR,     -- e.g. 'point_in_polygon_100k'
    layout      VARCHAR,     -- e.g. 'silver', 'h3_r5', 'county_files'
    variant     VARCHAR,     -- e.g. 'max_vertices=100'
    scope       VARCHAR,     -- e.g. 'MA', 'MA+LA+VT', 'viewport:new_orleans'
    rows        BIGINT,
    files       BIGINT,
    bytes       BIGINT,
    elapsed_ms  DOUBLE,
    notes       VARCHAR
);
"""


@contextmanager
def control_db():
    path = control_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = duckdb.connect(path)
    con.execute(SCHEMA)
    # Columns added after the first control databases were created
    con.execute("ALTER TABLE import_log ADD COLUMN IF NOT EXISTS zip_name VARCHAR")
    try:
        yield con
    finally:
        con.close()


def register_catalog(
    con: duckdb.DuckDBPyConnection, datasets: list[dict], update_only: bool = False
) -> dict:
    """Register the portal's datasets. Returns counts: inserted, updated (known rows), newer.

    `update_only` (nfhl update) never inserts: rows the catalog does not hold
    yet are ignored, so a nightly run cannot clog the control database with
    2,500 counties nobody asked for. `nfhl catalog` is how a county gets in.
    """
    known = {
        (st, c): d
        for st, c, d in con.execute(
            "SELECT state, county, fema_update_date FROM import_log"
        ).fetchall()
    }
    inserted = updated = newer = 0
    for d in datasets:
        key = (d["state"], d["county"])
        if key in known:
            updated += 1
            newer += d["fema_update_date"] > known[key]
        elif update_only:
            continue
        else:
            inserted += 1
        con.execute(
            """
            INSERT INTO import_log
                (state, county, fema_update_date, source_url, dfirm_id, zip_size_mb, zip_name, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'new')
            ON CONFLICT (state, county) DO UPDATE SET
                source_url = excluded.source_url,
                zip_name = excluded.zip_name,
                dfirm_id = excluded.dfirm_id,
                zip_size_mb = excluded.zip_size_mb,
                -- a newer FEMA release invalidates everything downstream
                status = CASE WHEN excluded.fema_update_date > import_log.fema_update_date
                              THEN 'new' ELSE import_log.status END,
                fema_update_date = greatest(excluded.fema_update_date, import_log.fema_update_date)
            """,
            [
                d["state"],
                d["county"],
                d["fema_update_date"],
                d["url"],
                d["dfirm_id"],
                d["zip_size_mb"],
                d["zip_name"],
            ],
        )
    return {"inserted": inserted, "updated": updated, "newer": newer}


def to_process(
    con: duckdb.DuckDBPyConnection,
    stage: str,
    state: str | None,
    county: str | None,
    force: bool = False,
    loaded_only: bool = False,
) -> list[tuple[str, str, date, str]]:
    """Rows sitting at the status before `stage`, optionally filtered.

    `force` ignores status. `loaded_only` keeps the counties that went through
    bronze at least once (`nfhl update`: refresh what we have, never start on
    what the catalog merely lists).
    """
    prev = STAGES[STAGES.index(stage) - 1]
    q = "SELECT state, county, fema_update_date, source_url FROM import_log WHERE true"
    params: list = []
    if not force:
        q += " AND status = ?"
        params.append(prev)
    if loaded_only:
        q += " AND bronze_at IS NOT NULL"
    if state:
        q += " AND state = ?"
        params.append(state)
    if county:
        q += " AND county = ?"
        params.append(county)
    return con.execute(q + " ORDER BY state, county", params).fetchall()


def never_loaded(con: duckdb.DuckDBPyConnection, state: str | None) -> list[tuple[str, int]]:
    """Counties the catalog lists that never went through bronze, per state."""
    q = "SELECT state, count(*) FROM import_log WHERE bronze_at IS NULL"
    params: list = []
    if state:
        q += " AND state = ?"
        params.append(state)
    return con.execute(q + " GROUP BY state ORDER BY state", params).fetchall()


def mark(con: duckdb.DuckDBPyConnection, state: str, county: str, **cols) -> None:
    cols = {**cols, "error": None, "error_at": None}
    sets = ", ".join(f"{k} = ?" for k in cols)
    con.execute(
        f"UPDATE import_log SET {sets} WHERE state = ? AND county = ?",
        [*cols.values(), state, county],
    )


def fail(con: duckdb.DuckDBPyConnection, state: str, county: str, error: str) -> None:
    con.execute(
        "UPDATE import_log SET error = ?, error_at = ? WHERE state = ? AND county = ?",
        [error[:500], datetime.now(), state, county],
    )


def record_benchmark(con: duckdb.DuckDBPyConnection, **row) -> None:
    cols = ", ".join(row)
    vals = ", ".join("?" for _ in row)
    con.execute(f"INSERT INTO benchmarks ({cols}) VALUES ({vals})", list(row.values()))


def status_table(con: duckdb.DuckDBPyConnection):
    return con.execute(
        """
        SELECT state, status, count(*) AS counties,
               round(sum(zip_size_mb)) AS zip_mb,
               sum(bronze_rows) AS bronze_rows,
               sum(silver_rows) AS silver_rows,
               sum(subdivided_rows) AS subdivided_rows,
               round(sum(subdivided_rows) / nullif(sum(silver_rows), 0), 1) AS ratio,
               count(error) AS errors
        FROM import_log GROUP BY 1, 2 ORDER BY 1, 2
        """
    ).fetchall()


def completed(con: duckdb.DuckDBPyConnection, stage: str = "subdivided") -> list[tuple[str, str]]:
    return con.execute(
        "SELECT state, county FROM import_log WHERE status = ? ORDER BY 1, 2", [stage]
    ).fetchall()


def reconcile(con: duckdb.DuckDBPyConnection) -> int:
    """Downgrade rows whose files are gone (a wiped data/ tree, a partial checkpoint).

    A county missing from the data is a county to import again, whatever the
    log says. Returns the number of rows changed. Skipped on object-storage roots.
    """
    if config.data_root().startswith("s3://"):
        return 0
    rows = con.execute(
        "SELECT state, county, status, bronze_path FROM import_log WHERE status <> 'new'"
    ).fetchall()
    changed = 0
    for st, cty, status, bronze_path in rows:
        have = "new"
        if bronze_path and os.path.exists(bronze_path):
            have = "bronze"
        if have == "bronze" and os.path.exists(
            config.path("silver", f"state={st}", f"county={cty}.parquet")
        ):
            have = "silver"
        if have == "silver" and os.path.exists(
            config.path("silver_subdivided", f"state={st}", f"county={cty}.parquet")
        ):
            have = "subdivided"
        if STAGES.index(have) < STAGES.index(status):
            con.execute(
                "UPDATE import_log SET status = ?, error = ? WHERE state = ? AND county = ?",
                [have, f"reconcile: files for status {status} missing", st, cty],
            )
            changed += 1
    return changed

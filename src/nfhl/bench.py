"""Benchmarks. One per stage, numbers land in control.benchmarks, never in prose.

Each function returns rows for record_benchmark(). Keep them small and honest:
same query, same machine, three runs, median. Random points are seeded, drawn
from the extent of the data itself, so both layouts see the same workload.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable
from pathlib import Path

import duckdb

from . import config, control
from .db import connect, q


def timed(fn: Callable[[], int], runs: int = 3) -> tuple[float, int]:
    times, rows = [], 0
    for _ in range(runs):
        t0 = time.perf_counter()
        rows = fn()
        times.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(times), 1), rows


def _count(con: duckdb.DuckDBPyConnection, sql: str) -> Callable[[], int]:
    return lambda: con.execute(sql).fetchone()[0]


def _bytes(files: list[str | Path]) -> int:
    return sum(Path(f).stat().st_size for f in files)


def _points_from_extents(con: duckdb.DuckDBPyConnection, source: str, n: int) -> None:
    """Table pts(x, y, pt): n seeded random points, spread over the extent of each state."""
    con.execute("SELECT setseed(0.42)")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE pts AS
        WITH e AS (
            SELECT state, ST_XMin(g) AS xmin, ST_YMin(g) AS ymin, ST_XMax(g) AS xmax, ST_YMax(g) AS ymax,
                   count(*) OVER () AS n_states, dense_rank() OVER (ORDER BY state) - 1 AS k
            FROM (SELECT state, ST_Extent_Agg(geometry) AS g FROM read_parquet('{q(source)}') GROUP BY state)
        ),
        r AS (
            SELECT xmin + random() * (xmax - xmin) AS x, ymin + random() * (ymax - ymin) AS y
            FROM e, range({n}) t(i)
            WHERE i % n_states = k
        )
        SELECT x, y, ST_Point(x, y) AS pt FROM r
        """
    )


# 1. Subdivide: spatial join of N points against silver vs silver_subdivided.
#    This is the number on the opening slide.
def bench_subdivide(state: str = "MA") -> list[dict]:
    n = config.load("workshop")["bench"]["points"]
    con = connect()
    with control.control_db() as ctl:
        mv = ctl.execute(
            "SELECT max(subdivide_max_vertices) FROM import_log WHERE state = ?", [state]
        ).fetchone()[0]
    variant = f"max_vertices={mv}"
    layouts = {
        "original": config.path("silver", f"state={state}", "*.parquet"),
        "subdivided": config.path("silver_subdivided", f"state={state}", "*.parquet"),
    }
    _points_from_extents(con, layouts["original"], n)
    out = []
    for layout, src in layouts.items():
        con.execute(
            f"CREATE OR REPLACE TABLE polys AS SELECT risk, geometry FROM read_parquet('{q(src)}')"
        )
        rows, max_v = con.execute(
            "SELECT count(*), max(ST_NPoints(geometry)) FROM polys"
        ).fetchone()
        out.append(
            dict(
                stage="subdivide",
                name="max_vertices",
                layout=layout,
                variant=variant,
                scope=state,
                rows=rows,
                elapsed_ms=None,
                notes=f"max vertices per row: {max_v}",
            )
        )
        ms, hits = timed(
            _count(
                con, "SELECT count(*) FROM pts JOIN polys ON ST_Intersects(polys.geometry, pts.pt)"
            )
        )
        out.append(
            dict(
                stage="subdivide",
                name=f"point_in_polygon_{n}",
                layout=layout,
                variant=variant,
                scope=state,
                rows=hits,
                elapsed_ms=ms,
            )
        )
    return out


# 2. RTree: single-point lookups on the .duckdb table, the API access
#    pattern (one API call, one point). With the index and with the optimizer
#    extension that injects the index scan disabled.
def bench_rtree(lookups: int = 200) -> list[dict]:
    db = config.path("duckdb", "flood.duckdb")
    if not Path(db).exists():
        raise FileNotFoundError(f"{db}: run `nfhl load` first")
    con = connect(db)
    _points_from_extents(con, config.path("silver_subdivided") + "/*/*.parquet", lookups)
    pts = con.execute("SELECT x, y FROM pts").fetchall()
    scope = "+".join(
        r[0] for r in con.execute("SELECT DISTINCT state FROM flood ORDER BY 1").fetchall()
    )
    out = []
    for variant, setting in (("rtree", "''"), ("no_index", "'extension'")):
        con.execute(f"SET disabled_optimizers = {setting}")

        def lookup_all() -> int:
            hits = 0
            for x, y in pts:
                hits += con.execute(
                    "SELECT count(*) FROM flood WHERE ST_Intersects(geometry, ST_Point(?, ?))",
                    [x, y],
                ).fetchone()[0]
            return hits

        ms, hits = timed(lookup_all, runs=1 if variant == "no_index" else 3)
        out.append(
            dict(
                stage="rtree",
                name=f"point_lookup_x{lookups}",
                layout="duckdb_table",
                variant=variant,
                scope=scope,
                rows=hits,
                elapsed_ms=ms,
                notes=f"{ms / lookups:.2f} ms per lookup",
            )
        )
    con.execute("SET disabled_optimizers = ''")
    return out


# 3. Gold analytic: admin (state/flood_zone) vs h3_r5 partitions.
#    a. batch: 100k points, the files a lookup needs (h3: the cells of the points;
#       admin: everything), bytes and elapsed
#    b. single lookups with and without the bbox struct filter (row-group pruning)
#    c. refresh footprint: files touched to replace one county
def bench_gold(single_lookups: int = 100) -> list[dict]:
    """Three layouts of the same rows: silver (original polygons, one file per
    county), silver_subdivided (pieces, one file per county), gold_analytic
    (pieces, one file per H3 cell, Hilbert order, bbox column)."""
    cfg = config.load("workshop")
    res = cfg["gold_analytic"]["h3_resolution"]
    n = cfg["bench"]["points"]
    h3_root = Path(config.path("gold_analytic"))
    roots = {
        "silver": Path(config.path("silver")),
        "silver_subdivided": Path(config.path("silver_subdivided")),
    }
    if not h3_root.exists():
        raise FileNotFoundError("run `nfhl gold-analytic` first")
    con = connect()
    _points_from_extents(con, config.path("silver_subdivided") + "/*/*.parquet", n)
    con.execute("ALTER TABLE pts ADD COLUMN h3 VARCHAR")
    con.execute(f"UPDATE pts SET h3 = h3_latlng_to_cell_string(y, x, {res})")
    scope = "+".join(
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT state FROM read_parquet('{q(roots['silver'])}/*/*.parquet', hive_partitioning = true) ORDER BY 1"
        ).fetchall()
    )
    cells = [r[0] for r in con.execute("SELECT DISTINCT h3 FROM pts").fetchall()]
    layouts = {
        # a county layout has no way to map a point to a file: every file of the scope is read
        name: [str(p) for p in root.glob("state=*/county=*.parquet")]
        for name, root in roots.items()
    }
    layouts["h3_r5"] = [
        str(p) for c in cells for p in (h3_root / f"h3_r{res}={c}").glob("*.parquet")
    ]
    out = []

    # a. batch join: the two county layouts once (original polygons take minutes), H3 three times.
    #    The H3 layout files a straddling piece under every cell it overlaps, so a
    #    join over several cells de-duplicates the (point, piece) pairs.
    for layout, files in layouts.items():
        hit = (
            "count(DISTINCT (p.x, p.y, g.state, g.county, g.zone_id, g.piece_id))"
            if layout == "h3_r5"
            else "count(*)"
        )
        ms, hits = timed(
            _count(
                con,
                f"SELECT {hit} FROM pts p JOIN read_parquet({files}) g ON ST_Intersects(g.geometry, p.pt)",
            ),
            runs=3 if layout == "h3_r5" else 1,
        )
        out.append(
            dict(
                stage="gold_analytic",
                name=f"batch_lookup_{n}",
                layout=layout,
                variant="files_for_points",
                scope=scope,
                rows=hits,
                files=len(files),
                bytes=_bytes(files),
                elapsed_ms=ms,
            )
        )

    # b. single lookups; the bbox column exists in the H3 layout only
    sample = con.execute(f"SELECT x, y, h3 FROM pts USING SAMPLE {single_lookups} ROWS").fetchall()
    bbox = "AND {x} BETWEEN g.bbox.xmin AND g.bbox.xmax AND {y} BETWEEN g.bbox.ymin AND g.bbox.ymax"

    def single_lookups_fn(layout: str, extra: str) -> Callable[[], int]:
        def run() -> int:
            hits = 0
            for x, y, cell in sample:
                if layout == "h3_r5":
                    files = [str(p) for p in (h3_root / f"h3_r{res}={cell}").glob("*.parquet")]
                    if not files:
                        continue
                    src = f"read_parquet({files})"
                else:
                    src = f"read_parquet('{q(roots[layout])}/state=*/county=*.parquet')"
                hits += con.execute(
                    f"SELECT count(*) FROM {src} g WHERE ST_Intersects(g.geometry, ST_Point({x}, {y})) "
                    + extra.format(x=x, y=y)
                ).fetchone()[0]
            return hits

        return run

    variants = [("silver", "no_pushdown", ""), ("silver_subdivided", "no_pushdown", "")]
    variants += [("h3_r5", "bbox_pushdown", bbox), ("h3_r5", "no_pushdown", "")]
    for layout, variant, extra in variants:
        ms, hits = timed(single_lookups_fn(layout, extra), runs=1)
        out.append(
            dict(
                stage="gold_analytic",
                name=f"single_lookup_x{single_lookups}",
                layout=layout,
                variant=variant,
                scope=scope,
                rows=hits,
                elapsed_ms=ms,
                notes=f"{ms / single_lookups:.1f} ms per lookup",
            )
        )

    # c. refresh footprint for one county: the files a new FEMA delivery rewrites
    st, cty = "MA", "Barnstable"
    touched = {
        name: [str(p) for p in root.glob(f"state={st}/county={cty}.parquet")]
        for name, root in roots.items()
    }
    touched["h3_r5"] = [
        str(p)
        for (c,) in con.execute(
            f"SELECT DISTINCT h3_r{res} FROM read_parquet('{q(h3_root)}/*/*.parquet', hive_partitioning = true) "
            f"WHERE state = '{q(st)}' AND county = '{q(cty)}'"
        ).fetchall()
        for p in (h3_root / f"h3_r{res}={c}").glob("*.parquet")
    ]
    for layout, files in touched.items():
        out.append(
            dict(
                stage="gold_analytic",
                name="refresh_one_county",
                layout=layout,
                variant=f"{st}/{cty}",
                scope=scope,
                files=len(files),
                bytes=_bytes(files),
                elapsed_ms=None,
            )
        )
    return out


# 4. The map: what a viewport costs when the web client reads gold_analytic cells
#    directly (the cells overlapping the viewport, bbox predicate, de-duplicated),
#    against the county files of every state the viewport touches.
def _box_wkt(xmin: float, ymin: float, xmax: float, ymax: float, step: float = 0.05) -> str:
    """A bbox as a ring densified every `step` degrees, for H3 polyfills."""
    import numpy as np

    xs = np.linspace(xmin, xmax, max(2, int((xmax - xmin) / step) + 1))
    ys = np.linspace(ymin, ymax, max(2, int((ymax - ymin) / step) + 1))
    ring = (
        [(x, ymin) for x in xs]
        + [(xmax, y) for y in ys[1:]]
        + [(x, ymax) for x in xs[::-1][1:]]
        + [(xmin, y) for y in ys[::-1][1:]]
    )
    return "POLYGON((" + ", ".join(f"{x} {y}" for x, y in ring) + "))"


def bench_map() -> list[dict]:
    cfg = config.load("workshop")
    res = cfg["gold_analytic"]["h3_resolution"]
    root = Path(config.path("gold_analytic"))
    index_path = root / "cells.json"
    if not index_path.exists():
        raise FileNotFoundError("run `nfhl gold-analytic` first")
    present = json.loads(index_path.read_text())["cells"]
    county_root = Path(config.path("silver_subdivided"))
    con = connect()
    state_ext = con.execute(
        f"""SELECT state, ST_XMin(g), ST_YMin(g), ST_XMax(g), ST_YMax(g)
            FROM (SELECT state, ST_Extent_Agg(geometry) AS g
                  FROM read_parquet('{q(county_root)}/*/*.parquet', hive_partitioning = true) GROUP BY state)"""
    ).fetchall()
    viewports = dict(cfg["bench"]["viewports"])
    for st, sx0, sy0, sx1, sy1 in state_ext:
        viewports.setdefault(f"state_{st}", {"bbox": [sx0, sy0, sx1, sy1], "width_px": 1280})
    out = []
    for name, vp in viewports.items():
        xmin, ymin, xmax, ymax = vp["bbox"]
        # H3 walks polygon edges as great circles; a parallel is not one, so a wide
        # box loses cells along its north and south edges unless the ring is densified.
        wkt = _box_wkt(xmin, ymin, xmax, ymax)
        cells = [
            c
            for c in con.execute(
                f"SELECT h3_polygon_wkt_to_cells_experimental_string('{wkt}', 'overlap', {res})"
            ).fetchone()[0]
            if c in present
        ]
        env = f"ST_MakeEnvelope({xmin}, {ymin}, {xmax}, {ymax})"
        candidates = [
            ("h3_r5_cells", [str(root / f"h3_r{res}={c}" / "data_0.parquet") for c in cells]),
            (
                "county_files",
                [
                    str(p)
                    for st, sx0, sy0, sx1, sy1 in state_ext
                    if sx0 <= xmax and sx1 >= xmin and sy0 <= ymax and sy1 >= ymin
                    for p in county_root.glob(f"state={st}/county=*.parquet")
                ],
            ),
        ]
        for layout, files in candidates:
            if not files:
                out.append(
                    dict(
                        stage="map",
                        name="viewport_read",
                        layout=layout,
                        scope=f"viewport:{name}",
                        rows=0,
                        files=0,
                        bytes=0,
                        elapsed_ms=0.0,
                    )
                )
                continue
            pred = (
                f"bbox.xmin <= {xmax} AND bbox.xmax >= {xmin} AND bbox.ymin <= {ymax} AND bbox.ymax >= {ymin} AND "
                if layout == "h3_r5_cells"
                else ""
            )
            sql = f"""SELECT count(*) FROM (
                        SELECT DISTINCT state, county, zone_id, piece_id
                        FROM read_parquet({files}) WHERE {pred} ST_Intersects(geometry, {env}))"""
            ms, rows = timed(_count(con, sql))
            out.append(
                dict(
                    stage="map",
                    name="viewport_read",
                    layout=layout,
                    scope=f"viewport:{name}",
                    rows=rows,
                    files=len(files),
                    bytes=_bytes(files),
                    elapsed_ms=ms,
                )
            )
    return out

"""ST_Subdivide (DuckDB 1.5.6 spatial) on the geometries that broke the Python path."""

from pathlib import Path

import duckdb

FIXTURE = Path(__file__).with_name("fixtures") / "ne_nemaha_31127C_2337.wkb"


def _pieces(con: duckdb.DuckDBPyConnection, sql_geom: str, n: int = 100) -> list[tuple]:
    return con.execute(
        f"""SELECT d.path[1] - 1, ST_NPoints(d.geom), ST_IsValid(d.geom), ST_Area(d.geom), ST_GeometryType(d.geom)
            FROM (SELECT unnest(ST_Dump(ST_Subdivide({sql_geom}, {n}))) AS d FROM g)"""
    ).fetchall()


def test_sliver_that_broke_clip_by_rect():
    con = duckdb.connect()
    con.execute("LOAD spatial")
    con.execute("CREATE TABLE g AS SELECT ST_GeomFromWKB(?::BLOB) AS geom", [FIXTURE.read_bytes()])
    area = con.execute("SELECT ST_Area(geom) FROM g").fetchone()[0]
    pieces = _pieces(con, "geom")
    assert pieces
    assert all(valid and npts <= 100 and t == "POLYGON" for _, npts, valid, _, t in pieces)
    assert sorted(p[0] for p in pieces) == list(range(len(pieces)))
    assert abs(sum(p[3] for p in pieces) - area) / area < 1e-9


def test_small_polygon_is_one_piece():
    con = duckdb.connect()
    con.execute("LOAD spatial")
    con.execute("CREATE TABLE g AS SELECT ST_Buffer(ST_Point(0, 0), 1, 4) AS geom")
    pieces = _pieces(con, "geom")
    assert [(p[0], p[4]) for p in pieces] == [(0, "POLYGON")]

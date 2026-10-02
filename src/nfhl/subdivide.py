"""Polygon subdivide: recursive bbox bisection capped at N vertices per piece.

Why this exists: NFHL polygons can carry hundreds of thousands of vertices
(Louisiana parishes). Every point-in-polygon test pays for all of them. Cutting
polygons into small pieces makes each test cheap and lets bbox pruning do most
of the work. This is what PostGIS ST_Subdivide does, and DuckDB 1.5.6 ships it:
the pipeline uses the SQL version (stages.SUBDIVIDE_SQL). This module is the
`--engine python` alternative, kept for the comparison and the tests.

Algorithm (per input geometry):
  1. if vertex_count <= max_vertices: emit as is
  2. else split the bbox along its longer axis at the midpoint,
     clip the geometry to both halves (clip_by_rect), recurse on each half
  3. drop empty and non-polygonal leftovers (lines/points from touching edges)
  4. a leaf that comes out invalid (clip_by_rect does that rarely) is made valid

Measured on Middlesex (11,558 rows, 130,731 pieces): 10.6 s with
shapely.intersection, 2.6 s with clip_by_rect. Multiprocessing inside a county
does not pay (pickling the pieces back costs what it saves); across counties it
does, see --jobs on the stage commands.

Trade-offs to state in the workshop:
  - row count explodes (10x to 100x on big polygons)
  - feature identity is lost unless a zone key (zone_id) is carried along
  - area-based attributes become wrong on pieces
  - artificial straight edges appear when rendered without dissolve
  - bisection at the midpoint is simple but not balanced; a vertex-median split
    gives fewer pieces at higher cost. Midpoint is kept on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pyarrow as pa
import shapely
from shapely import Geometry, GeometryCollection, MultiPolygon, Polygon, box

MAX_DEPTH = 40  # safety net against degenerate geometries


@dataclass(frozen=True)
class SubdivideStats:
    input_rows: int
    output_rows: int
    max_input_vertices: int
    max_output_vertices: int

    @property
    def ratio(self) -> float:
        return self.output_rows / self.input_rows if self.input_rows else 0.0


def vertex_count(geom: Geometry) -> int:
    return int(shapely.get_num_coordinates(geom))


def _polygonal_parts(geom: Geometry) -> list[Polygon]:
    """Keep only polygons from an intersection result (drops lines/points).

    Clipping a polygon along an edge it shares with the split line yields a
    GeometryCollection with LineStrings or Points in it. Those are dropped,
    not recursed into: get_parts() of a LineString is the LineString itself.
    """
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    if isinstance(geom, GeometryCollection):
        out: list[Polygon] = []
        for p in geom.geoms:
            out.extend(_polygonal_parts(p))
        return out
    return []


def subdivide_one(geom: Geometry, max_vertices: int = 100, _depth: int = 0) -> list[Polygon]:
    """Subdivide a single (Multi)Polygon into pieces of at most max_vertices."""
    parts = _polygonal_parts(geom)
    if not parts:
        return []
    out: list[Polygon] = []
    for part in parts:
        if vertex_count(part) <= max_vertices or _depth >= MAX_DEPTH:
            out.extend(_valid_leaf(part))
            continue
        minx, miny, maxx, maxy = part.bounds
        if (maxx - minx) >= (maxy - miny):
            mid = (minx + maxx) / 2.0
            halves = ((minx, miny, mid, maxy), (mid, miny, maxx, maxy))
        else:
            mid = (miny + maxy) / 2.0
            halves = ((minx, miny, maxx, mid), (minx, mid, maxx, maxy))
        for half in halves:
            out.extend(subdivide_one(_clip(part, half), max_vertices, _depth + 1))
    return out


def _clip(part: Polygon, rect: tuple[float, float, float, float]) -> Geometry:
    """Clip a polygon to a rectangle.

    clip_by_rect is GEOS's rectangle clipper: no general overlay, 4x faster than
    intersection(part, box). Two known rough edges, both handled here or at the
    leaf: it may return an invalid piece now and then (25 of 130,731 on
    Middlesex), and on a thin sliver it can build a 3-point ring and throw
    (NE Nemaha, feature 31127C_2337, kept as a test fixture). The general
    overlay is the fallback for that split only.
    """
    try:
        return shapely.clip_by_rect(part, *rect)
    except shapely.errors.GEOSException:
        return shapely.intersection(part, box(*rect))


def _valid_leaf(part: Polygon) -> list[Polygon]:
    """A finished piece, made valid if needed; a piece GEOS cannot even assess is dropped."""
    try:
        if part.is_valid:
            return [part]
        return _polygonal_parts(shapely.make_valid(part))
    except shapely.errors.GEOSException:
        return []


def subdivide_batch(
    table: pa.Table,
    geometry_column: str = "geometry",
    id_column: str = "source_feature_id",
    max_vertices: int = 100,
) -> tuple[pa.Table, SubdivideStats]:
    """Subdivide every row of an Arrow table. Geometry column must be WKB.

    Returns a new table with the same attribute columns, the geometry replaced
    by pieces, plus `piece_id` (0..k-1 per source feature). Rows are repeated
    for each piece, which is what DuckDB needs to re-join attributes.
    """
    wkb = table.column(geometry_column).to_pylist()
    geoms = shapely.from_wkb(wkb)

    piece_wkb: list[bytes] = []
    piece_ids: list[int] = []
    row_index: list[int] = []
    max_in = 0
    max_out = 0

    for i, g in enumerate(geoms):
        if g is None:
            continue
        max_in = max(max_in, vertex_count(g))
        pieces = subdivide_one(g, max_vertices)
        for k, p in enumerate(pieces):
            max_out = max(max_out, vertex_count(p))
            piece_wkb.append(shapely.to_wkb(p))
            piece_ids.append(k)
            row_index.append(i)

    attrs = table.drop_columns([geometry_column]).take(pa.array(row_index, pa.int64()))
    out = attrs.append_column("piece_id", pa.array(piece_ids, pa.int32()))
    out = out.append_column(geometry_column, pa.array(piece_wkb, pa.binary()))

    stats = SubdivideStats(
        input_rows=table.num_rows,
        output_rows=out.num_rows,
        max_input_vertices=max_in,
        max_output_vertices=max_out,
    )
    return out, stats


def bbox_grid_estimate(geom: Geometry, max_vertices: int) -> int:
    """Rough upper bound on piece count, useful to predict row explosion.

    Assumes vertices are spread uniformly, which they are not, so treat as an
    order-of-magnitude estimate.
    """
    n = vertex_count(geom)
    return int(np.ceil(n / max_vertices)) * 2

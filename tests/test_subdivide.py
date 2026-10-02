import numpy as np
import pyarrow as pa
import shapely
from shapely import Point, Polygon

from nfhl.subdivide import subdivide_batch, subdivide_one, vertex_count


def blob(n_vertices: int, seed: int = 0) -> Polygon:
    """A big irregular polygon with roughly n_vertices vertices."""
    rng = np.random.default_rng(seed)
    angles = np.sort(rng.uniform(0, 2 * np.pi, n_vertices - 1))
    radii = 100 + rng.normal(0, 8, n_vertices - 1)
    pts = np.c_[radii * np.cos(angles), radii * np.sin(angles)]
    return Polygon(pts)


def test_small_polygon_untouched():
    p = Point(0, 0).buffer(10, quad_segs=4)
    out = subdivide_one(p, max_vertices=100)
    assert len(out) == 1
    assert out[0].equals(p)


def test_pieces_respect_cap_and_cover_area():
    p = blob(5000)
    assert p.is_valid
    pieces = subdivide_one(p, max_vertices=100)
    assert len(pieces) > 1
    # Cap can be exceeded slightly by clip-added vertices, tolerate 2x
    assert max(vertex_count(x) for x in pieces) <= 200
    assert abs(shapely.union_all(pieces).area - p.area) / p.area < 1e-6


def test_pieces_are_valid_polygons():
    p = blob(20000, seed=3)
    pieces = subdivide_one(p, max_vertices=100)
    assert all(x.is_valid and x.geom_type == "Polygon" for x in pieces)
    assert abs(sum(x.area for x in pieces) - p.area) / p.area < 1e-6


def test_sliver_that_breaks_clip_by_rect():
    """NE Nemaha 31127C_2337: valid, 1,472 vertices, clip_by_rect throws three levels down."""
    from pathlib import Path

    g = shapely.from_wkb(
        Path(__file__).with_name("fixtures").joinpath("ne_nemaha_31127C_2337.wkb").read_bytes()
    )
    assert g.is_valid
    pieces = subdivide_one(g, max_vertices=100)
    assert pieces and all(p.is_valid for p in pieces)
    assert abs(sum(p.area for p in pieces) - g.area) / g.area < 1e-3


def test_batch_keeps_attributes_and_ids():
    p = blob(3000, seed=1)
    tbl = pa.table(
        {
            "source_feature_id": ["A", "B"],
            "risk": ["minimal", "1 percent flood zone"],
            "geometry": [shapely.to_wkb(p), shapely.to_wkb(Point(500, 500).buffer(1))],
        }
    )
    out, stats = subdivide_batch(tbl, max_vertices=100)
    assert stats.input_rows == 2
    assert stats.output_rows == out.num_rows > 2
    ids = out.column("source_feature_id").to_pylist()
    assert ids.count("B") == 1
    assert ids.count("A") == out.num_rows - 1
    piece_ids = [
        k for k, i in zip(out.column("piece_id").to_pylist(), ids, strict=True) if i == "A"
    ]
    assert sorted(piece_ids) == list(range(len(piece_ids)))


def test_collection_with_lines_does_not_recurse():
    """Clipping along a shared edge yields lines and points next to polygons. Drop them."""
    from shapely import GeometryCollection, LineString

    square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    gc = GeometryCollection([square, LineString([(0, 0), (1, 0)]), Point(5, 5)])
    out = subdivide_one(gc, max_vertices=100)
    assert len(out) == 1 and out[0].equals(square)
    assert subdivide_one(LineString([(0, 0), (1, 1)]), max_vertices=100) == []


def test_real_world_shape_with_shared_edges():
    """A polygon whose bbox midpoint coincides with its own edges: the clip
    produces collections, the pieces still cover the area."""
    p = Polygon([(0, 0), (4, 0), (4, 4), (2, 4), (2, 2), (0, 2)])
    p = shapely.segmentize(p, 0.01)  # ~1400 vertices
    pieces = subdivide_one(p, max_vertices=100)
    assert len(pieces) > 1
    assert abs(shapely.union_all(pieces).area - p.area) / p.area < 1e-6

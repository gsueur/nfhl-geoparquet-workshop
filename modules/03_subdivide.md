# 3. Subdivide (35 min, hands-on, the core module)

**Answers**: massive polygons. At the end, every row in silver_subdivided has
at most a hundred vertices, and the point-in-polygon join is measured before
and after.

## Say

This is the module the workshop exists for. One flood zone polygon can carry
a million vertices, and a point test walks all of them. PostGIS solved this
years ago with `ST_Subdivide`: cut every polygon into pieces small enough that
the bounding box does most of the work, and keep a pointer to the original
feature, with a cap of one hundred vertices. DuckDB 1.5.6 ships the same
function, so today it is one SQL statement: `ST_Subdivide`, then `ST_Dump` to
turn the result into rows. The forty lines of Python that did it before are
still in the repo (`--engine python`): run both, compare the pieces and the
clock.

The algorithm is recursive bisection. If the polygon has more vertices than
the cap, split its bounding box along the longer axis at the midpoint,
intersect the polygon with each half, recurse. Stop when a piece fits. Keep
the source feature id and number the pieces. One subtlety, and it took a
real county to find it: clipping along an edge the polygon shares with the
split line returns a collection with lines and points in it. Drop them, never
recurse into them.

Then the trade-offs, all of them, because subdivide is not free. Rows explode
by an order of magnitude. Feature identity is gone unless you carry the id.
Area attributes are wrong on pieces. Rendered as they are, the pieces show
straight artificial edges, which module 5b will dissolve away. The midpoint
split is simple, not balanced. And the cap is soft, because a clip can add
vertices.

## Do

1. The worst polygon in silver:
   ```
   duckdb -c "LOAD spatial; SELECT county, max(ST_NPoints(geometry)) FROM 'data/silver/state=MA/*.parquet' GROUP BY 1"
   ```
2. Open `src/nfhl/subdivide.py`. Read `subdivide_one` and `_polygonal_parts`
   together. Forty lines.
3. `uv run nfhl subdivide --state MA`. Three ok lines, the cap is 100.
   Then `uv run nfhl status`: the `ratio` column is pieces per source row.
4. `uv run nfhl subdivide --state MA --force --max-vertices 256`, then 512.
   Watch the ratio fall. Finish by rerunning at 100: the rest of the workshop
   assumes it.
5. `uv run nfhl bench --stage subdivide`. Two joins of a hundred thousand
   seeded random points, drawn from the extent of the data, against the
   original polygons and against the pieces. Then `uv run nfhl benchmarks`.
   This is the opening slide, reproduced on their laptop.

## Show

- The plan of the join: `EXPLAIN` shows DuckDB's `SPATIAL_JOIN` operator on
  both sides. The bounding-box index is already there. The speedup is the
  per-candidate cost, not the candidate count.
- `SUBDIVIDE_SQL` in `src/nfhl/stages.py`, the whole stage, next to
  `nfhl subdivide --engine python` on the same county: same cap, same valid
  pieces, a few dozen more pieces and a small area drift on the Python side.
- The Louisiana numbers from the checkpoint's benchmarks table, next to
  theirs.

## If it breaks

Middlesex is the slow one; if it has not finished in three minutes, fetch
`silver_subdivided/state=MA` from the checkpoint and set the statuses.

## Exercise (10 min)

Replace the bisection with a fixed grid: intersect each polygon with the H3
res 8 cells that cover it (`h3_polygon_wkt_to_cells_string` on the bbox, then
`h3_cell_to_boundary_wkb`). Compare piece count and join time with the
bisection at the same order of magnitude.

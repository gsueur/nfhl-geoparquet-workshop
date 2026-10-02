# 4. Land, index, validate (15 min, hands-on)

**Answers**: massive polygons, second half. At the end, silver_subdivided is
one `.duckdb` file with an RTree, validated against the control database, and
the two pushdown mechanisms are told apart.

## Say

This is where an API-backed pipeline usually ends: one DuckDB file, the pieces
in Hilbert order, an RTree index, statistics computed, served by an API that
asks one point at a time. Landing the pieces from Parquet into that file is
one CREATE TABLE and one CREATE INDEX. The validation is strict: no invalid
geometry, no null risk, and every county the control
database says is subdivided must be in the table, nothing else.

The point to hammer, because everyone gets it wrong once: the RTree
accelerates queries on the DuckDB table. It does nothing for Parquet files.
When you read Parquet, pruning comes from row-group statistics, and from the
bbox column we will add in the next module. Two storage forms, two mechanisms.

## Do

1. `uv run nfhl load`. It prints rows, invalid, null risk, counties, the two
   consistency lists (both empty), the load and index times, the file size.
2. `uv run nfhl bench --stage rtree`. Two hundred single-point lookups, with
   the index, and with the optimizer extension that injects the index scan
   disabled. Per-lookup time is in the notes column of `nfhl benchmarks`.

## Show

- `EXPLAIN SELECT count(*) FROM flood WHERE ST_Intersects(geometry, ST_Point(-70.3, 41.7))`
  on `data/duckdb/flood.duckdb`: `RTREE_INDEX_SCAN`. Then
  `SET disabled_optimizers = 'extension'` and the same EXPLAIN: a sequential
  scan.
- The three settings people discover through OOMs: `preserve_insertion_order`
  (off to stream, on when an ORDER BY must survive a COPY, see `db.py`),
  `memory_limit` (`NFHL_MEMORY_LIMIT`), `threads`.

## If it breaks

The `.duckdb` file is derived data; fetch `duckdb/flood.duckdb` from the
checkpoint. It covers three states, so the numbers will differ from the room.

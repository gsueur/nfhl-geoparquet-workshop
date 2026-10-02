# 5a. Gold analytic (20 min, demo on the 3-state checkpoint)

**Answers**: two workloads on one asset, first workload. At the end, the
layout that answers "what is the risk here" fastest exists, and the reason it
beats one file per county is measured.

## Say

Module 4 landed the pieces in one file, which answers lookups well, but only
behind a server. Gold analytic is the same data as Parquet files on object
storage, laid out for the lookup: one file per H3 cell at resolution 5, each
piece filed under every cell it overlaps, Hilbert order inside the file, and an
explicit bbox column.
A point becomes a cell id, the cell id becomes a file name, the bbox column
prunes the row groups. No index, no server.

Subdivide is what makes this cheap. Pieces are small, so a piece overlaps few
cells: 1.3 percent of them are filed twice, and a lookup is exact at the cell
edge.
That is the link between module 3 and this one.

We keep the two county layouts as the control, silver and silver_subdivided,
one file per county the way FEMA publishes, and we measure all three. The layout
follows the dominant workload, not taste. Silver stays by state and county
because FEMA publishes by county; gold is free to be anything.

Then the honest three minutes on GeoParquet 2.0. DuckDB writes version 1.0 by
default and 2.0 on request; it never writes 1.1, so there is no `covering`
declaration in the metadata. And 2.0 dropped the covering on purpose: native
geometry statistics per row group replace it. The bbox column here is a plain
struct column that DuckDB prunes on like any other. Same effect, no spec
support. gpio will tell you the column is not declared, and offer to declare it
1.1-style. We do not, and we say why.

## Do

1. `uv run nfhl gold-analytic`. It prints, for both layouts, elapsed, files
   and bytes.
2. `uv run nfhl bench --stage gold`, then `uv run nfhl benchmarks`. Three
   names: `batch_lookup` (a hundred thousand points, the files a lookup
   needs: H3 reads the cells of the points, admin reads everything),
   `single_lookup` (one point at a time, with and without the bbox filter),
   `refresh_one_county` (files and bytes rewritten to replace Barnstable).

## Show

- `parquet_kv_metadata()` on one gold file: version 2.0.0, no covering.
- `EXPLAIN` a single lookup with the bbox predicate, twice on the same file.
  With a point outside the file's extent the plan is `EMPTY_RESULT`: DuckDB
  proved from the column statistics that nothing can match and reads no
  data. With a point inside, the bbox comparisons appear as `Filters` under
  `READ_PARQUET`, ahead of `ST_Intersects`. That is the pruning the bbox
  column buys, with no covering declared anywhere.
- `uv run nfhl verify --path data/gold_analytic/<cell>/data_0.parquet`: the
  gpio warning about the undeclared bbox column, and the suggestion.
- Side by side if time allows: the same table written by DuckDB (V2), by gpio,
  by GeoPandas. Version, encoding, covering, CRS.
- Open `data/gold_analytic` in GeoPQ Workbench: the scorecard reads the same
  footer and reaches the same conclusions.

## If it breaks

Everything in this module reads from the checkpoint; nothing is written that
the next module needs.

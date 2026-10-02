# Make FEMA Flood Maps Cloud-Native

A GeoParquet + DuckDB pipeline. Workshop material, Cloud Native Geospatial Forum 2026.

Public flood-hazard data is critical and exhausting: per-county zipped
shapefiles, inconsistent schemas, oversized polygons that destroy spatial-join
performance, no cloud-native distribution. This repository walks through a
pattern that turns FEMA's National Flood Hazard Layer (NFHL) into
query-optimized GeoParquet 2.0 using only DuckDB, Python and object storage.

```
catalog -> ingest -> normalize -> subdivide -> load -> gold_analytic -> (web map)
```

One silver, one gold: an analytic layout (one file per H3 cell, Hilbert order,
bbox column) for "what is the risk at this point". The web map reads the same
files, zoomed in.

## Setup

```
git clone https://github.com/gsueur/nfhl-geoparquet-workshop.git   # or the ZIP: .../archive/refs/heads/main.zip
cd nfhl-geoparquet-workshop
uv sync
source .venv/bin/activate   # or prefix every command with `uv run`
uv run nfhl check      # DuckDB + spatial + httpfs + h3, gpio, under 10 s
uv run pytest -q
```

`just` recipes wrap the same commands (`just check`, `just test`, `just all`).

## Run the hands-on part (Massachusetts, 3 counties)

```
uv run nfhl catalog --state MA     # portal read once -> import_log, then the county list to pick from
uv run nfhl counties --state MA    # the same list again, from the local database, with status
uv run nfhl ingest --state MA      # ZIP -> memory -> Arrow -> bronze GeoParquet (no ZIP kept)
uv run nfhl normalize --state MA   # YAML-driven schema + domain -> silver
uv run nfhl subdivide --state MA   # cap 100 vertices per row -> silver_subdivided
uv run nfhl load                   # .duckdb file, Hilbert order, RTree, validation
uv run nfhl gold-analytic          # one file per H3 r5 cell, Hilbert order, bbox column
uv run nfhl bench                  # every number of the workshop, into control.benchmarks
uv run nfhl status                 # import_log per state and status
uv run nfhl benchmarks             # the wrap-up table
uv run nfhl verify                 # geoparquet-io (gpio) side check of one file
```

Every stage reads its worklist from `import_log` and only processes counties
sitting at the previous status. Rerun a stage with `--force`, widen it with
`--all` (every county of the state) or narrow it with `--county Barnstable`.
`--all-states` runs every county the catalog registered, whatever the state
(`nfhl catalog` without `--state` registers the whole country).
Ingest, normalize and subdivide take `--jobs N` to run N counties at once in
separate processes; only the parent process writes the control database.

## Production mode

```
uv run nfhl update            # whole country: one page fetch, then only new deliveries
uv run nfhl update --gold     # same, then rebuild the derived layouts
uv run nfhl prune             # drop catalog rows that were never loaded
```

The control database (`data/control/fema_control.duckdb`) is the memory. A
county is downloaded again only when FEMA publishes a newer date in the file
name, or when its files are missing (`reconcile`), and only if it was loaded
before. `update` inserts nothing: counties not in the control database are
ignored, cataloged ones never loaded are reported and left alone, until
someone starts them with `nfhl ingest --state XX --all`.

Ingest keeps no ZIP: bronze is the copy, and `import_log` records FEMA's
file name and date. `nfhl download --state XX --all` is the optional
pre-fetch that stores the ZIPs in `data/downloads/<fileName>`; ingest uses a
stored ZIP when it finds one, so the day-before run makes every later stage
offline. Massachusetts, Louisiana and Utah are cached that way
and their outputs are on R2 (`config/workshop.yaml`). The three states only
differ in how long the stages take: seconds on three MA counties, minutes on
Louisiana. Do not download Louisiana on conference wifi.

## The map

`web/index.html` is MapLibre + DuckDB-WASM + h3-js. It fetches
`gold_analytic/cells.json` and `layouts.json`, and reads the same viewport
from the layout you pick: gold_analytic (the cells the viewport overlaps, bbox
predicate first, one row per piece), silver_subdivided or silver (the county
files whose bbox meets the viewport). A checkbox hides the `minimal` class.
Past 40 files it asks you to zoom in: no layout here has overviews. To run it
on local files:

```
uv run python scripts/serve_local.py
open http://localhost:8000/web/index.html?base=http://localhost:8000/data
```

## Layout

- `src/nfhl/` pipeline, one module per stage, `nfhl` CLI
- `config/` scope, YAML schema mapping
- `modules/` the workshop: `README.md` is the spine, one file per section after it
- `web/` MapLibre + DuckDB-WASM viewer
- `scripts/` precompute, upload checkpoints, local server, catalog sizing
- `data/` (ignored) bronze, silver, silver_subdivided, duckdb, gold_analytic, control

## Prerequisites

SQL and Python fluency. A laptop with `uv`. Optional: an S3 / R2 bucket for
the serving section.

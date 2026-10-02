# 6. Serve and wrap-up (15 min, demo, hands-on optional)

**Answers**: the ambition. The map, from object storage, in a browser, with
files, bytes and milliseconds on screen. Then the thread back to the first
challenge: the pipeline that keeps it current.

## Say

One HTML file, no build. MapLibre draws, h3-js turns the viewport into the
res-5 cells it overlaps, DuckDB-WASM reads those gold_analytic files with
range requests straight from R2. The page fetches `cells.json` (which cells
exist, how big), keeps the cells present, refuses past 40 files and asks to
zoom in, runs one query with the bbox predicate and one row per piece, and
updates a GeoJSON source. A checkbox, on by default, drops `risk = 'minimal'`
from the query: the remainder of every county, most of the pieces, nothing a
basemap does not show (44 percent fewer pieces at Harvard Square, 19 percent
in New Orleans where almost everything is a flood zone; the bytes read do not
change). The counter in the corner is the argument: how many files, how many
bytes, how many milliseconds. The honest part is the refusal:
this layout serves point lookups; a wide map would need overviews, which is
another workshop.

Then the wrap-up. The pattern on one slide, and the benchmarks table the room
produced today. And the last challenge, answered by the first module: the
control database remembers every county, `nfhl update` fetches the catalog
for the country and touches only what FEMA changed. The gold layout is
rebuilt from silver, silver is rebuilt county by county, and the country is
never downloaded twice.

## Do

1. Presenter: open `web/index.html` against the R2 checkpoint. It opens on
   Harvard Square, the point of the lookup query. Zoom out until it refuses,
   then zoom into New Orleans. Read the counter out loud at each step. Tick
   `outline pieces` to show the subdivision.
2. Attendees, optional: `uv run python scripts/serve_local.py`, then
   `http://localhost:8000/web/index.html?base=http://localhost:8000/data`
   shows their own counties.
3. `uv run nfhl benchmarks`. The wrap-up table.

## Show

- The `level` selector forced to r7 at a wide viewport: the file budget falls
  back to r5, and the counter says so.
- The browser's network panel: range requests, one file per cell, nothing
  else.
- `window.map` in the console for anyone who wants to poke.

## Rough edges to name

- CORS and range requests on R2: the upload script prints the policy.
- First load of DuckDB-WASM plus the spatial extension.
- The byte counter is the size of the files touched, an upper bound; range
  requests read less.

## If it breaks

The local server on the presenter's laptop with `?base=` pointed at the
checkpoint copy. The demo does not need the conference wifi once the page and
the WASM bundle are cached.

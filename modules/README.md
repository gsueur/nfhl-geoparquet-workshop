# The workshop, on one page

Make FEMA Flood Maps Cloud-Native: a GeoParquet + DuckDB pipeline.
3 hours, hands-on, Cloud Native Geospatial Forum, October 2026.

## The spine

The whole workshop answers one question: **how do you turn a public dataset
that is critical, huge and badly distributed into files that serve two
workloads at once, and keep them current without redoing the country?**

Module 0 presents the dataset and names four challenges. Every module after it
answers one of them. Say the challenge at the start of each module, say what it
produced at the end, and the thread holds.

| Challenge (module 0) | Answered by | Module |
|---|---|---|
| No API: a portal, one ZIP per county, the date in the file name | catalog + the control database | 1 |
| One schema per vintage, 10-char field names, sentinel values | normalize, driven by one YAML | 2 |
| Massive polygons: one feature can carry a million vertices | subdivide, then load with an RTree | 3, 4 |
| A reactive database for analytics, then a map | gold analytic; the map reads the same files | 5, 6 |
| Per-county updates, forever | the control database again, `nfhl update` | 1 and wrap-up |

The ambition, stated in module 0: first a reactive, fast-responding database
for analytics, then explore ways to map it. DuckDB, Python and object storage
only. No GDAL CLI, no PostGIS, no tile server.

## Timing

| Module | Minutes | Mode |
|---|---|---|
| 0. The dataset and its challenges | 10 | talk |
| 1. Catalog, ingest, control plane | 30 | hands-on |
| 2. Normalize | 20 | hands-on |
| 3. Subdivide | 35 | hands-on, the core |
| 4. Load and index | 15 | hands-on |
| 5. Gold analytic | 30 | demo on the 3-state checkpoint |
| 6. The map and wrap-up | 20 | demo, hands-on optional |

Hands-on runs on 3 Massachusetts counties by default. Attendees may pick a
state of their own instead: `nfhl catalog --state XX`, `nfhl counties --state
XX` to see sizes, then 2 or 3 counties under 50 MB with `--county A --county B`
on every stage. Nothing in the pipeline is Massachusetts-specific; the guard
only refuses to download a whole non-live state by accident. The one real
limit is the venue's wifi, so the ZIPs of Massachusetts, Louisiana and Utah
are pre-downloaded in `data/downloads/` (`nfhl download`) and ingest reads
them from there. Louisiana and Utah outputs are precomputed on R2
(`config/workshop.yaml`). Nobody downloads Louisiana on conference wifi.

## How each module file is written

Every module has the same five parts, in this order, so you always know what
a line is for:

- **Answers**: the challenge from module 0 this module resolves, one line.
- **Say**: the narrative, in prose. What the presenter tells the room, in the
  order it is told. This is the spine.
- **Do**: numbered steps attendees run, the exact command, what they should see.
- **Show**: what the presenter demonstrates on screen between the steps,
  including the rough edges worth 2 minutes.
- **If it breaks**: the fallback. Usually a checkpoint path and a rule for
  skipping ahead.

An **Exercise** closes the hands-on modules when time allows. Numbers are never
written in the prose: `nfhl benchmarks` prints them, and the framing table in
module 0 is refreshed from the portal before the event.

## Before the event

- `bash scripts/precompute.sh` on a good connection, then `nfhl benchmarks`.
- `bash scripts/upload_checkpoints.sh`, CORS on the bucket, URLs in
  `config/workshop.yaml` and `web/index.html`.
- Refresh the catalog facts in module 0 (`scripts/measure_catalog.py`).
- Email attendees: `uv sync && uv run nfhl check` before they travel.

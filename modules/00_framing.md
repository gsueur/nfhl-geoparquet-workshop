# 0. The dataset and its challenges (10 min, talk)

**Answers**: nothing yet. This module names the four challenges the rest of the
workshop answers, and states the scope and the ambition.

## Say

FEMA's National Flood Hazard Layer is the official flood map of the United
States. Its polygon layer, `S_FLD_HAZ_AR`, carries the flood zones (A, AE, VE,
X and their subtypes) that decide insurance, disclosure and building rules. It
is public, it is critical, and it is amongst the worst-distributed dataset I work with.

It is distributed by a web portal, not an API. One ZIP per county, a DFIRM in
FEMA's vocabulary, with a dozen shapefiles inside, and the only release date
you will ever get is in the file name. The portal lists two and a half thousand
county-wide datasets plus a few hundred community-level ones, mixed in the same
table, with the state spelled out in capitals. Downloading the country means
scraping that page and pulling close to ninety gigabytes of ZIPs.

The polygons are enormous. Zone X, "minimal hazard", is drawn as the remainder
of the county: one feature, hundreds of thousands of vertices, a million in
Massachusetts, a whole parish in Louisiana. Every point-in-polygon test pays
for every vertex, and the workload that matters most is exactly that: a point,
a question, an answer, millions of times.

The usual way to tame this is ogr2ogr into PostGIS, subdivide there, then
export. It works. It is also three tools and two copies of the data.

And it never ends. FEMA re-releases counties all year long: hundreds in the
past twelve months, dozens in the past month, the newest a few days before this
count. Any pipeline that redoes the country when one county changes is dead on
arrival. The unit of work is the county, forever.

So, four challenges: no API, one schema per vintage, massive polygons, and two
workloads on one asset that must stay current county by county.

The ambition for the next three hours: one pipeline, DuckDB, Python and object
storage, nothing else. No GDAL command line, no PostGIS, no tile server. The
ambition, in order: first a reactive, fast-responding database for analytics,
"what is the flood risk at this point" at scale; then explore ways to map it.

The scope: three states sit in the local downloads folder, Massachusetts,
Louisiana and Utah, and every stage runs from those ZIPs; the portal is only
asked for the catalog. The hands-on runs on three Massachusetts counties,
coastal, riverine and dense urban, because every stage finishes in seconds on
them. Louisiana (the polygons) and Utah (the conference state, holes in the
coverage) take minutes with the same commands, so their outputs are
precomputed. The three-state checkpoints are on R2 for anyone whose laptop or
wifi gives up.

## The facts behind the talk (portal count of 2026-09-07, refresh before the event)

| Fact | Value |
|---|---|
| Datasets listed on the portal | 2670 |
| County-wide (DFIRM id ends with C) | 2504 |
| Community-level (towns, villages) | 166 |
| States and territories | 56 |
| Total zipped | 88.9 GB |
| Largest ZIP | Wells County, ND, 776 MB |
| Oldest and newest release | 2002-03-04, 2026-09-04 |
| County-wide datasets re-released in the last 30 / 90 / 365 days | 26 / 126 / 431 |
| Whole country as one GeoParquet file of pieces (this pipeline, 2026-09-30) | 35.6 GB |
| Massachusetts: county-wide datasets, zipped | 11, 622 MB |
| Louisiana: parishes, zipped | 51, 2.5 GB |
| Utah: county-wide, community-level, zipped | 16, 3, 257 MB |

Refresh with `uv run python scripts/measure_catalog.py MA LA UT` and the
snippet in `nfhl.catalog` (`list_datasets(county_wide_only=False)`).

The largest polygon and the point-in-polygon number come from the benchmarks
table: `nfhl benchmarks`, stage `subdivide`, names `max_vertices` and
`point_in_polygon_100000`. Louisiana for the slide, Massachusetts for the
reproduction in module 3.

## Slides

1. The portal page, the file name with the date, the table above.
2. One polygon, one number: vertices, and the join before and after subdivide.
3. The target: the map in a browser from R2, and the point lookup, same files.
4. The pattern: catalog, ingest, normalize, subdivide, load, two golds. One
   silver, two golds, because the two workloads share nothing.

## Do

1. `uv run nfhl check`. Everyone. It prints DuckDB, its three extensions,
   pyogrio with its GDAL, shapely and gpio, and finishes in well under ten
   seconds. Anyone who fails gets the checkpoint URLs from
   `config/workshop.yaml` and follows along on precomputed data.

## Show

The portal, live, if the wifi allows: hazards.fema.gov, NFHL search results,
find Barnstable County, point at the file name. Then the HTML source of that
row: the state spelled out, the DFIRM id, the size in a `td`. That is the API.

## If it breaks

No dependency on anything yet. If `nfhl check` fails on DuckDB extensions,
the laptop cannot reach the extension repository: the person pairs with a
neighbour for the hands-on modules.

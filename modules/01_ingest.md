# 1. Catalog, ingest and the control plane (30 min, hands-on)

**Answers**: no API, and per-county updates. At the end, three Massachusetts
counties are in bronze GeoParquet and the control database knows it.

## Say

The portal is the API. One page lists every dataset in the country, so the
catalog stage fetches it once, parses each link, and registers one row per
county in the control database: state, county, FEMA date, URL, ZIP size.
The parser had to learn three things about FEMA: states are spelled out in
capitals, the DFIRM id carries the FIPS code and ends with a C for county-wide
datasets, and community-level rows share the table. We keep county-wide rows
only, because the pipeline's unit of work is the county.

The control database is the idea the whole pipeline rests on.
`import_log` has one row per county, and `status` is the last stage that
completed for it: new, bronze, silver, subdivided. Every stage asks the table
for the counties sitting at the previous status, processes them, and writes
back. Run a stage twice and the second run does nothing. Break a county and
its status does not move, the error is written next to it, and the next run
retries it. That is idempotence with no framework.

The same table is the answer to the last challenge. Run on a schedule, `nfhl
update` fetches the catalog for the whole country, compares FEMA's date with
the one it remembers, and only the counties with a newer date go back to
status new. Everything else is untouched. The country is downloaded once; after
that, only what FEMA changed.

Ingest itself is three lines: the ZIP comes into memory, pyogrio reads
the shapefile out of the bytes as an Arrow table, DuckDB writes the table as
GeoParquet. The ZIP comes from `data/downloads/<fileName>` when `nfhl
download` put it there, from httpx straight into memory otherwise, and ingest
never writes it: bronze is the copy we keep, and `import_log` holds FEMA's
file name and date. Nothing touches the disk before the Parquet file. GDAL is still
there, inside pyogrio, and that is fine: the point is the shape of the
pipeline, not purity.

## Do

1. `uv run nfhl catalog --state MA`. Expect one line, eleven datasets
   registered with their total zipped size, one community-level dataset
   skipped, then the county table read back from the control database:
   county, FEMA date, ZIP size, status new. The portal was read once, the
   local database written once. Anyone who wants their own state runs it
   with that state instead and picks 2 or 3 counties under 50 MB from the
   table. Every later `--state MA` becomes `--state XX --county A --county B`.
   `uv run nfhl counties --state XX` prints the same table again at any
   time, from the local database, with the status each county has reached.
2. `uv run nfhl status`. One row: MA, status new, eleven counties.
3. `uv run nfhl ingest --state MA`. Three counties, the default list from
   `config/workshop.yaml`, each with an ok line and its elapsed time. The
   ZIPs of the three states were pre-fetched with `nfhl download`, so this
   is disk, not network: `bronze_encoding` ends with `zip:cache`.
   Middlesex is the big one. While it runs, `uv run nfhl status` in a second
   terminal: the stage holds the control database only to write each result,
   so the table is readable between counties.
4. Run step 3 again. `bronze: 0 counties to process`. Nothing was downloaded.
5. `uv run nfhl ingest --county Barnstable --force`. One county redone.
6. Look at what you got:
   ```
   duckdb -c "DESCRIBE SELECT * FROM 'data/bronze/state=MA/county=Barnstable/S_FLD_HAZ_AR.parquet'"
   duckdb -c "LOAD spatial; SELECT typeof(geometry) FROM 'data/bronze/state=MA/county=Barnstable/S_FLD_HAZ_AR.parquet' LIMIT 1"
   ```
   Ten-character field names, and a geometry typed `GEOMETRY('EPSG:4269')`.
   pyogrio tagged the Arrow column as GeoArrow WKB with the CRS, DuckDB read
   the tag. The CRS travelled from the shapefile for free.

## Show

- `nfhl status` after step 3: the zip size column, `bronze_encoding` in the
  table itself (`cpg:UTF-8` when the archive has a `.cpg`, `latin1-fallback`
  otherwise).
- Break a county on purpose. In the control database, append a character to
  Hampden's `source_url`, run `nfhl ingest --county Hampden --force`, show the
  red line, then `nfhl status`: status still bronze, the error and its time
  in the second table. Restore the URL, rerun, the error is cleared.
- Two minutes if it works on the pinned version: DuckDB reading the ZIP over
  HTTP itself,
  `SELECT count(*) FROM ST_Read('/vsizip//vsicurl/<url>/S_FLD_HAZ_AR.shp')`.
  Same GDAL, no Python. Say why the pipeline does not do it this way: no
  control over retries, encoding or the CRS tag.
- `nfhl update --help`. Do not run it in the room.

## If it breaks

If a download has not finished ten minutes in, stop it. Fetch
`bronze/state=MA` and `control/` from the checkpoint into `data/` and continue
with module 2: the control database in the checkpoint carries the statuses.

## Exercise (5 min)

Add a `zip_bytes` column to `import_log` and fill it. Two lines: the schema in
`control.py`, the return dict of `ingest_county`. The catalog size and the
downloaded size will not match; say why (the ZIP holds a dozen layers).

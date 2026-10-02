# 2. Normalize with YAML (20 min, hands-on)

**Answers**: one schema per vintage. At the end, silver has one unified schema,
a five-class `risk`, valid geometries and the CRS declared, for every county.

## Say

Bronze is the shapefile's schema, and every county's shapefile is a vintage.
Field names are capped at ten characters, no-data is a sentinel like -9999,
booleans are the letters T and F, and older DFIRM exports spell things
differently. Normalizing this in Python is a rabbit hole. Normalizing it in
SQL, generated from one YAML file, is a page.

The YAML has two sections. `columns` maps target names to source expressions
with a type, a default and the null sentinels. `derived` is the domain logic
written against the target names: the CASE that turns FEMA's zone taxonomy into
five classes of risk, the floodplain flag, the subzone. Both compile to one
SELECT, which you can print and read before running it. Swap the YAML and the
same code handles state wetlands, parcels or zoning.

Two things happen to the geometry on the way. `ST_MakeValid` fixes the
invalid polygons, and there are always some. Then `ST_SetCRS` puts the CRS
back, because every geometry function in DuckDB drops it. The output is
GeoParquet 2.0: the CRS is in the Parquet schema, as a native geometry logical
type, and repeated in the `geo` key.

## Do

1. `uv run nfhl normalize --show-sql`. Read the SELECT: the mapped CTE, the
   derived columns, the final `ST_SetCRS`.
2. `uv run nfhl normalize --state MA`. Three ok lines.
3. `uv run nfhl status`: silver rows equal bronze rows, and the counties are at
   status silver.
4. What GeoParquet 2.0 looks like from inside:
   ```
   duckdb -c "SELECT key, value::VARCHAR FROM parquet_kv_metadata('data/silver/state=MA/county=Barnstable.parquet')"
   duckdb -c "SELECT name, logical_type FROM parquet_schema('data/silver/state=MA/county=Barnstable.parquet') WHERE name = 'geometry'"
   ```
   Version 2.0.0 in `geo`, and `GeometryType(crs=...)` with the PROJJSON of
   NAD83 in the schema.
5. `uv run nfhl verify`: geoparquet-io reads the same file. `gpio inspect meta
   --geo` prints the metadata in plain words, `gpio check all` runs the best
   practice checks.

## Show

- `silver_invalid_fixed` in `nfhl status` or in the table: the count of
  geometries ST_MakeValid changed, per county.
- One divergence between two counties in bronze: query `STATIC_BFE` for the
  sentinel, or `ZONE_SUBTY` spellings, in Barnstable versus Hampden.
- The gpio spec check fails one item on this file: coordinates outside the
  valid range for the CRS. It applies the NAD83 area of use, which crosses
  the antimeridian, to a longitude of -70. Validators have edge cases too:
  geoparquet/geoparquet-io#906, filed while preparing this workshop. Check
  whether the version pinned in `uv.lock` still does it.
- `ST_MakeValid` in DuckDB and `-makevalid` in GDAL do not always agree.
  Neither is wrong.
- The bug the three-state data found: `AREA NOT INCLUDED` starts with `AR`,
  so a `LIKE 'AR%'` meant for the AR zones classified unmapped Vermont as a
  1 percent flood zone. The YAML tests the literal first now. Domain logic in one readable SELECT is how
  you find this.

## If it breaks

Fetch `silver/state=MA` from the checkpoint. Set the three counties to status
silver in the control database, or fetch `control/` as well.

## Exercise (5 min)

Add a `bfe_ft` derived column that is NULL unless the zone is AE. One entry in
`derived`, rerun with `--force`, check with `--show-sql` first.

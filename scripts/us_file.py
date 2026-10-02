"""One national GeoParquet from silver_subdivided: Hilbert order, bbox struct, GeoParquet 2.0.

Usage: NFHL_DATA_ROOT=/Volumes/T9/DATA/nfhl uv run python scripts/us_file.py
"""

import time
from pathlib import Path

from nfhl import config
from nfhl.db import connect, parquet_options, q
from nfhl.stages import _source_crs

root = Path(config.data_root())
# exFAT volumes carry AppleDouble "._*" siblings: list the real files, no glob.
files = sorted(
    str(f) for f in (root / "silver_subdivided").glob("state=*/county=*.parquet") if not f.name.startswith("._")
)
out = root / "nfhl_us.parquet"
tmp = root / "_duckdb_tmp"
tmp.mkdir(exist_ok=True)

src = ", ".join(f"'{q(f)}'" for f in files)
con = connect(preserve_order=True)
con.execute(f"SET temp_directory = '{q(str(tmp))}'")
con.execute("SET memory_limit = '12GB'")
t0 = time.perf_counter()
con.execute(
    f"""
COPY (
    SELECT * EXCLUDE (geometry),
           {{xmin: ST_XMin(geometry), ymin: ST_YMin(geometry),
             xmax: ST_XMax(geometry), ymax: ST_YMax(geometry)}} AS bbox,
           ST_SetCRS(geometry, '{_source_crs()}') AS geometry
    FROM read_parquet([{src}])
    ORDER BY ST_Hilbert(geometry, ST_Extent(ST_MakeEnvelope(-180, -90, 180, 90)))
) TO '{q(str(out))}' ({parquet_options()}, ROW_GROUP_SIZE 20000)
"""
)
n, rg = con.execute(
    f"SELECT sum(row_group_num_rows), count(*) FROM (SELECT DISTINCT row_group_id, row_group_num_rows "
    f"FROM parquet_metadata('{q(str(out))}'))"
).fetchone()
print(
    f"{len(files)} county files -> {out}: {n} rows, {rg} row groups, "
    f"{out.stat().st_size / 1e9:.1f} GB, {time.perf_counter() - t0:.0f} s"
)

"""Size a state before committing to it: county-wide datasets, MB zipped, community-level rows.

The portal prints the archive size next to every link, so this needs one page
fetch and no HEAD requests. Run once, away from the venue.
"""

import sys

from nfhl.catalog import list_datasets

for st in sys.argv[1:] or ["MA", "LA", "UT"]:
    ds = list_datasets(st, county_wide_only=False)
    wide = [d for d in ds if d["county_wide"]]
    mb = sum(d["zip_size_mb"] or 0 for d in wide)
    biggest = max(wide, key=lambda d: d["zip_size_mb"] or 0, default=None)
    print(
        f"{st}: {len(wide)} county-wide datasets, {mb:,.0f} MB zipped, "
        f"{len(ds) - len(wide)} community-level rows skipped"
        + (f", largest {biggest['county']} {biggest['zip_size_mb']:.0f} MB" if biggest else "")
    )

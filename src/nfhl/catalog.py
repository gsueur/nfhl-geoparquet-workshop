"""Discover NFHL county downloads from the FEMA portal.

There is no API. The search result page lists one download link per
dataset, the release date is only available in the file name
(25001C_20260119.zip), and the state is spelled out in uppercase
("MASSACHUSETTS"). This is stage 0 of the workshop and a lesson in itself.

Two kinds of rows are listed:
  - county-wide DFIRMs, id ends with 'C' (25001C, "BARNSTABLE COUNTY")
  - community-level DFIRMs, numeric id (250156, " TOWN OF AMHERST")
The pipeline keys everything on (state, county), so only county-wide rows
are kept. Community-level rows are counted and reported; Vermont has many.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import load

DATE_RE = re.compile(r"_(\d{8})\.zip$")
SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(KB|MB|GB)$", re.IGNORECASE)
# Louisiana has parishes, Alaska has boroughs. Strip the suffix, keep the raw name too.
SUFFIX_RE = re.compile(r"\s+(COUNTY|PARISH|BOROUGH|CENSUS AREA|MUNICIPALITY)$", re.IGNORECASE)

# The portal spells states out. The pipeline, the config and the paths use postal codes.
STATES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR", "CALIFORNIA": "CA",
    "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE", "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID", "ILLINOIS": "IL",
    "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS", "KENTUCKY": "KY", "LOUISIANA": "LA",
    "MAINE": "ME", "MARYLAND": "MD", "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC", "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT", "VERMONT": "VT", "VIRGINIA": "VA",
    "WASHINGTON": "WA", "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "PUERTO RICO": "PR", "VIRGIN ISLANDS": "VI", "GUAM": "GU", "AMERICAN SAMOA": "AS",
    "NORTHERN MARIANA ISLANDS": "MP",
}  # fmt: skip


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
def _fetch(url: str) -> str:
    cfg = load("workshop")["fema"]
    r = httpx.get(url, timeout=cfg["timeout_s"], headers={"User-Agent": cfg["user_agent"]})
    r.raise_for_status()
    return r.text


def clean_county(raw: str) -> str:
    """'BARNSTABLE COUNTY' -> 'Barnstable', 'ST. TAMMANY PARISH' -> 'St. Tammany'."""
    c = raw.strip()
    if "TERRITORY-WIDE" not in c.upper():
        c = SUFFIX_RE.sub("", c).strip()
    return c.title()


def _zip_size_mb(a) -> float | None:
    """The portal prints the archive size in the same table row as the link."""
    tr = a.find_parent("tr")
    if tr is None:
        return None
    for td in tr.find_all("td"):
        m = SIZE_RE.match(td.get_text(strip=True))
        if m:
            value, unit = float(m.group(1)), m.group(2).upper()
            return value * {"KB": 1 / 1024, "MB": 1.0, "GB": 1024.0}[unit]
    return None


def list_datasets(
    state: str | None = None, county: str | None = None, county_wide_only: bool = True
) -> list[dict]:
    """One dict per download link, keyed on (state postal code, county name).

    Filters are case-insensitive on the postal code and the cleaned county name.
    """
    cfg = load("workshop")["fema"]
    soup = BeautifulSoup(_fetch(cfg["catalog_url"]), "html.parser")
    links = soup.find_all("a", href=lambda h: h and "Download/ProductsDownLoadServlet" in h)
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for a in links:
        href = a.get("href")
        url = cfg["download_base"] + href
        params = parse_qs(urlparse(url).query)
        dfirm = params.get("DFIRMID", [None])[0]
        st_name = params.get("state", [None])[0]
        cty = params.get("county", [None])[0]
        fname = params.get("fileName", [None])[0]
        if not (dfirm and st_name and cty and fname):
            continue
        st = STATES.get(st_name.strip().upper())
        m = DATE_RE.search(fname)
        if st is None or m is None:
            continue
        rec = {
            "state": st,
            "county": clean_county(cty),
            "county_raw": cty.strip(),
            "dfirm_id": dfirm.strip(),
            "county_wide": dfirm.strip().upper().endswith("C"),
            "fema_update_date": datetime.strptime(m.group(1), "%Y%m%d").date(),  # noqa: DTZ007
            "zip_size_mb": _zip_size_mb(a),
            "zip_name": os.path.basename(fname),
            "url": url.replace(" ", "%20"),
        }
        if county_wide_only and not rec["county_wide"]:
            continue
        if state and rec["state"].lower() != state.lower():
            continue
        if county and rec["county"].lower() != county.lower():
            continue
        # The same link appears twice per row (icon and text). Keep one.
        key = (rec["state"], rec["county"])
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out

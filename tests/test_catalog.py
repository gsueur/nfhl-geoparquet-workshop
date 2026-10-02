"""Offline test of the FEMA portal parser, on rows copied from the live page (2026-09)."""

from datetime import date

from nfhl import catalog

ROW = """
<tr>
  <td><center class="txt12">{state}</center></td>
  <td><center class="txt12">{county}</center></td>
  <td><center>
    <a href="Download/ProductsDownLoadServlet?DFIRMID={dfirm}&state={state}&county={county}&fileName={dfirm}_{date}.zip" tabindex="1">
      <img src="/femaportal/NFHL/img/ZIPicon.png" alt="Download ZIP File" /></a>
  </center></td>
  <td><center class="txt12">Mon Jan 19 15:25:33 EST 2026</center></td>
  <td><center class="txt12">{size}</center></td>
  <td><center>
    <a href="Download/ProductsDownLoadServlet?DFIRMID={dfirm}&state={state}&county={county}&fileName={dfirm}_{date}.zip" tabindex="1">
      <img src="/femaportal/NFHL/img/ZIPicon.png" alt="Download ZIP File" /></a>
  </center></td>
</tr>
"""

ROWS = [
    dict(
        state="MASSACHUSETTS",
        county="BARNSTABLE COUNTY",
        dfirm="25001C",
        date="20260119",
        size="33MB",
    ),
    dict(
        state="MASSACHUSETTS",
        county=" TOWN OF AMHERST",
        dfirm="250156",
        date="20230228",
        size="1.5MB",
    ),
    dict(
        state="LOUISIANA",
        county="ST. TAMMANY PARISH",
        dfirm="22103C",
        date="20250401",
        size="1.2GB",
    ),
    dict(
        state="PUERTO RICO",
        county="PUERTO RICO TERRITORY-WIDE",
        dfirm="72000C",
        date="20211216",
        size="900KB",
    ),
]
PAGE = "<html><body><table>" + "".join(ROW.format(**r) for r in ROWS) + "</table></body></html>"


def test_clean_county():
    assert catalog.clean_county("BARNSTABLE COUNTY") == "Barnstable"
    assert catalog.clean_county("ST. TAMMANY PARISH") == "St. Tammany"
    assert catalog.clean_county(" TOWN OF AMHERST") == "Town Of Amherst"
    assert catalog.clean_county("PUERTO RICO TERRITORY-WIDE") == "Puerto Rico Territory-Wide"


def test_list_datasets_parses_state_county_date_size(monkeypatch):
    monkeypatch.setattr(catalog, "_fetch", lambda url: PAGE)
    ds = catalog.list_datasets()
    assert [(d["state"], d["county"]) for d in ds] == [
        ("MA", "Barnstable"),
        ("LA", "St. Tammany"),
        ("PR", "Puerto Rico Territory-Wide"),
    ]
    ma = ds[0]
    assert ma["dfirm_id"] == "25001C" and ma["county_wide"]
    assert ma["fema_update_date"] == date(2026, 1, 19)
    assert ma["zip_size_mb"] == 33.0
    assert "%20" in ma["url"] and " " not in ma["url"]
    assert abs(ds[1]["zip_size_mb"] - 1228.8) < 0.1


def test_community_level_rows_and_filters(monkeypatch):
    monkeypatch.setattr(catalog, "_fetch", lambda url: PAGE)
    everything = catalog.list_datasets(county_wide_only=False)
    assert len(everything) == 4
    amherst = next(d for d in everything if d["dfirm_id"] == "250156")
    assert not amherst["county_wide"]
    assert [d["county"] for d in catalog.list_datasets(state="ma")] == ["Barnstable"]
    assert catalog.list_datasets(state="MA", county="hampden") == []

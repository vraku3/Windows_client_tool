"""CSV export of app and Windows Update offers."""
import csv
import io
from types import SimpleNamespace

from modules.updates.export_csv import APP_HEADERS, WU_HEADERS, app_offers_to_csv, wu_offers_to_csv


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


def _app(name="App", winget_id="Acme.App", installed="1.0", available="2.0", source="winget"):
    return SimpleNamespace(name=name, winget_id=winget_id, installed_version=installed,
                           available_version=available, source=source)


def _wu(**overrides):
    base = dict(kb="KB5123456", title="Security Update", classification="Security Updates",
                severity="Critical", reboot="1", release_date="2026-10-14",
                size_mb=123.4, min_size_mb=12.0, downloaded=False, driver=False,
                security_bulletins=["MS17-010"], cve_ids=["CVE-2026-1234"],
                support_url="https://support.microsoft.com/help/KB5123456")
    base.update(overrides)
    return SimpleNamespace(**base)


def test_app_export_header_and_row():
    rows = _rows(app_offers_to_csv([_app()]))
    assert rows[0] == APP_HEADERS
    assert rows[1] == ["App", "Acme.App", "1.0", "2.0", "winget"]


def test_app_export_quotes_a_comma_in_the_name():
    rows = _rows(app_offers_to_csv([_app(name="App, Tools")]))
    assert len(rows[1]) == len(APP_HEADERS)
    assert rows[1][0] == "App, Tools"


def test_wu_export_carries_category_driver_download_and_identifiers():
    rows = _rows(wu_offers_to_csv([_wu(driver=True, downloaded=True)]))
    assert rows[0] == WU_HEADERS
    row = dict(zip(rows[0], rows[1]))
    assert row["Category"] == "driver"
    assert row["Downloaded"] == "True"
    assert row["Driver"] == "True"
    assert row["SecurityBulletins"] == "MS17-010"
    assert row["CveIds"] == "CVE-2026-1234"
    assert row["MaxDownloadMB"] == "123.4"


def test_wu_export_leaves_an_unknown_size_empty():
    rows = _rows(wu_offers_to_csv([_wu(size_mb=0.0, min_size_mb=0.0)]))
    row = dict(zip(rows[0], rows[1]))
    assert row["MaxDownloadMB"] == "" and row["MinDownloadMB"] == ""


def test_empty_export_is_just_the_header():
    assert _rows(app_offers_to_csv([])) == [APP_HEADERS]
    assert _rows(wu_offers_to_csv([])) == [WU_HEADERS]

"""Searchable Windows Update history model."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from modules.updates import history_model as hm


def entry(days_ago, title="Security Update", code=2, hresult=0, operation=1):
    return SimpleNamespace(when=datetime.now(timezone.utc) - timedelta(days=days_ago),
                           title=title, result_code=code, hresult=hresult,
                           operation=operation)


def test_labels_cover_known_codes_and_keep_unknown_ones_visible():
    assert hm.result_label(2) == "Succeeded"
    assert hm.result_label(4) == "Failed"
    assert hm.result_label(9) == "Result 9"
    assert hm.operation_label(1) == "Install"
    assert hm.operation_label(2) == "Uninstall"


def test_detail_is_decoded_only_for_a_failed_entry_with_a_hresult():
    assert hm.detail_text(entry(1, code=2, hresult=0)) == ""
    text = hm.detail_text(entry(1, code=4, hresult=0x80240034))
    assert text.lower().startswith("0x") and "download failed" in text.lower()
    assert hm.detail_text(entry(1, code=4, hresult=0)) == ""


def test_search_matches_title_result_and_detail():
    rows = [entry(1, "2026-10 Security Update (KB5129195)"), entry(2, "Driver update", code=4, hresult=0x80240034)]
    assert [r.title for r in hm.filter_entries(rows, "kb5129195")] == [rows[0].title]
    assert [r.title for r in hm.filter_entries(rows, "failed")] == [rows[1].title]
    assert len(hm.filter_entries(rows, "")) == 2


def test_date_filter_and_newest_first():
    rows = [entry(1), entry(40, "Old"), entry(3, "Newer")]
    recent = hm.filter_entries(rows, "", days=7)
    assert [r.title for r in recent] == ["Security Update", "Newer"]
    assert [r.title for r in hm.filter_entries(rows, "")] == ["Security Update", "Newer", "Old"]


def test_pagination_clamps_out_of_range_pages():
    rows = list(range(250))
    page, current, pages = hm.paginate(rows, 2, 100)
    assert len(page) == 50 and current == 2 and pages == 3
    page, current, pages = hm.paginate(rows, 99, 100)
    assert current == 2 and len(page) == 50
    page, current, pages = hm.paginate([], 5, 100)
    assert page == [] and current == 0 and pages == 1

def test_kb_extraction_and_uninstall_eligibility():
    assert hm.kb_of_title("2026-10 Security Update (KB5129195) (26200.9457)") == "KB5129195"
    assert hm.kb_of_title("No KB here") == ""
    assert hm.uninstallable(entry(1, "Update (KB1234567)", code=2))
    assert not hm.uninstallable(entry(1, "Update (KB1234567)", code=4))
    assert not hm.uninstallable(entry(1, "Update (KB1234567)", code=2, operation=2))
    assert not hm.uninstallable(entry(1, "No KB", code=2))

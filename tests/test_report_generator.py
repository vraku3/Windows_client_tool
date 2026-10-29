import os
import tempfile

import pytest

from modules.updates.report_generator import render_update_report_html, write_report


@pytest.fixture
def app_data_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def test_render_report_with_empty_data_and_history():
    html = render_update_report_html({}, [])
    assert "<html>" in html.lower()
    assert "Maintenance Report" in html
    assert "No Windows Update installs this run." in html
    assert "No winget updates this run." in html
    assert "No prior runs recorded." in html


def test_render_report_includes_wu_and_winget_rows():
    data = {
        "wu_results": [{"title": "Cumulative Update KB123", "kb": "KB123", "success": True, "message": "success"}],
        "winget_results": [{"name": "Foo", "id": "Foo.Bar", "before": "1.0", "after": "updated", "confirmed": True}],
        "store_triggered": 1,
        "cleanup_freed": 5_000_000,
        "cleanup_deleted": 3,
    }
    html = render_update_report_html(data, [])
    assert "Cumulative Update KB123" in html
    assert "Foo.Bar" in html
    assert "CONFIRMED" in html


def test_render_report_escapes_html_in_titles():
    data = {"wu_results": [{"title": "<script>alert(1)</script>", "kb": "KB1", "success": False, "message": "x"}]}
    html = render_update_report_html(data, [])
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_report_includes_dism_output_only_when_present():
    without_dism = render_update_report_html({}, [])
    assert "Component Store Cleanup" not in without_dism

    with_dism = render_update_report_html({"dism_output": "reclaimed 4GB"}, [])
    assert "reclaimed 4GB" in with_dism


def test_render_report_includes_history_rows():
    history = [{"ts": "2026-01-01 10:00", "freed": 1024 * 1024, "updates": 2}]
    html = render_update_report_html({}, history)
    assert "2026-01-01 10:00" in html


def test_write_report_creates_file(app_data_dir):
    html = render_update_report_html({}, [])
    path = write_report(app_data_dir, html)
    assert os.path.exists(path)
    assert path.endswith(".html")
    with open(path, "r", encoding="utf-8") as f:
        assert f.read() == html


def test_report_names_categories_deferred_by_settings():
    html = render_update_report_html(
        {"wu_deferred": {"feature": 1, "driver": 2, "preview": 0}}, [])
    assert "Deferred by settings" in html
    assert "Feature upgrade 1" in html
    assert "Driver 2" in html
    assert "Preview" not in html


def test_report_omits_the_deferred_section_when_nothing_was_skipped():
    html = render_update_report_html({"wu_deferred": {}}, [])
    assert "Deferred by settings" not in html


def test_report_names_store_apps_that_changed_since_the_previous_run():
    html = render_update_report_html({
        "store_triggered": 1,
        "store_changed": ["Contoso.App: 1.0.0.0 -> 2.0.0.0"],
        "store_previous_at": "2026-10-28T10:00:00",
    }, [])
    assert "Contoso.App: 1.0.0.0 -&gt; 2.0.0.0" in html
    assert "1 app(s) changed since 2026-10-28T10:00:00" in html


def test_report_does_not_claim_store_versions_when_they_were_unreadable():
    html = render_update_report_html({
        "store_triggered": 1, "store_read_refused": True,
    }, [])
    assert "installed versions unreadable" in html
    assert "no version change" not in html

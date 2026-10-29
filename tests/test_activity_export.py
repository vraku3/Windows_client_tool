"""Activity ledger CSV export (Qt-free formatting plus the widget button)."""
import csv
import io

from modules.updates.activity_ledger import ACTIVITY_HEADERS, entries_to_csv


def _entry(**overrides):
    base = dict(ts="2026-10-29T10:14:56", name="Node.js", id="OpenJS.NodeJS.LTS",
                source="winget", version_before="24.22.1", version_offered="26.13.0",
                version_after="26.13.0", outcome="UPDATED", context="manual",
                elevated=True, detail="Now 26.13.0")
    base.update(overrides)
    return base


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


def test_activity_header_and_row():
    rows = _rows(entries_to_csv([_entry()]))
    assert rows[0] == ACTIVITY_HEADERS
    row = dict(zip(rows[0], rows[1]))
    assert row["Package"] == "Node.js"
    assert row["Outcome"] == "UPDATED"
    assert row["Elevated"] == "True"


def test_missing_versions_export_empty_not_none():
    rows = _rows(entries_to_csv([_entry(version_before=None, version_offered=None)]))
    row = dict(zip(rows[0], rows[1]))
    assert row["Version before"] == "" and row["Version offered"] == ""


def test_a_comma_in_the_detail_does_not_shift_columns():
    rows = _rows(entries_to_csv([_entry(detail="failed, then retried")]))
    assert len(rows[1]) == len(ACTIVITY_HEADERS)
    assert rows[1][-1] == "failed, then retried"


def test_widget_export_uses_the_filtered_rows(qapp, monkeypatch):
    from modules.updates.activity_widget import ActivityWidget
    widget = ActivityWidget()
    widget._entries = [_entry(), _entry(name="Other", id="Acme.App",
                                        outcome="NEEDS_USER")]
    widget._search.setText("Node")
    captured = []
    monkeypatch.setattr(
        "modules.updates.activity_widget.save_csv",
        lambda parent, title, name, text: captured.append(text) or "activity.csv")
    widget._export()
    assert captured and "Node.js" in captured[0] and "Acme.App" not in captured[0]
    assert "Exported" in widget._status.text()

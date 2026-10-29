"""CSV export of Windows Update history (Qt-free formatting)."""
import csv
import io
from datetime import datetime, timezone
from types import SimpleNamespace

from modules.updates.history_model import CSV_HEADERS, entries_to_csv


def _entry(title, code=2, hresult=0, operation=1, when=None):
    return SimpleNamespace(title=title, result_code=code, hresult=hresult,
                           operation=operation,
                           when=when or datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc))


def _rows(text):
    return list(csv.reader(io.StringIO(text)))


def test_header_and_one_row_per_entry():
    rows = _rows(entries_to_csv([_entry("Security Update KB5123456")]))
    assert rows[0] == CSV_HEADERS
    assert len(rows) == 2
    assert rows[1][3] == "KB5123456"
    assert rows[1][4] == "Succeeded"
    assert rows[1][1] == "Install"


def test_commas_and_quotes_do_not_shift_columns():
    rows = _rows(entries_to_csv([_entry('Fix, "quoted" issue KB5000001')]))
    assert len(rows[1]) == len(CSV_HEADERS)
    assert rows[1][2] == 'Fix, "quoted" issue KB5000001'


def test_failed_hresult_and_decoded_detail_are_exported():
    rows = _rows(entries_to_csv([_entry("KB5123456", code=4, hresult=-2145124322)]))
    assert rows[1][5] == "0x8024001E"
    assert rows[1][6]


def test_no_hresult_leaves_the_column_empty():
    rows = _rows(entries_to_csv([_entry("KB1", code=2, hresult=0)]))
    assert rows[1][5] == ""
    assert rows[1][6] == ""


def test_uninstall_rows_keep_their_operation():
    rows = _rows(entries_to_csv([_entry("KB1", operation=2)]))
    assert rows[1][1] == "Uninstall"

"""History dialog can select several succeeded KBs for one uninstall batch."""
from datetime import datetime, timezone
from types import SimpleNamespace

from PyQt6.QtWidgets import QMessageBox

from modules.updates.history_dialog import UpdateHistoryDialog


def _entry(title, code=2, operation=1):
    return SimpleNamespace(title=title, result_code=code, operation=operation,
                           hresult=0, when=datetime(2026, 10, 29, tzinfo=timezone.utc))


def test_two_succeeded_rows_offer_one_batch(qapp, monkeypatch):
    dialog = UpdateHistoryDialog([_entry("Cumulative KB5000001"),
                                  _entry("Security KB5000002")])
    from PyQt6.QtCore import QItemSelectionModel
    flags = (QItemSelectionModel.SelectionFlag.Select
             | QItemSelectionModel.SelectionFlag.Rows)
    model = dialog._table.model()
    dialog._table.selectionModel().select(model.index(0, 0), flags)
    dialog._table.selectionModel().select(model.index(1, 0), flags)
    assert dialog._uninstall_btn.isEnabled()
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog._ask_uninstall()
    assert dialog.uninstall_kbs == ["KB5000001", "KB5000002"]


def test_a_failed_row_is_not_offered(qapp):
    dialog = UpdateHistoryDialog([_entry("Failed KB5000003", code=4)])
    dialog._table.selectRow(0)
    assert not dialog._uninstall_btn.isEnabled()
    dialog._ask_uninstall()
    assert dialog.uninstall_kbs == []


def test_selection_mode_is_extended(qapp):
    from PyQt6.QtWidgets import QTableWidget
    dialog = UpdateHistoryDialog([])
    assert dialog._table.selectionMode() == QTableWidget.SelectionMode.ExtendedSelection

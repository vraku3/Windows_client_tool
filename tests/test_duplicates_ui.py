from PyQt6.QtCore import Qt
from PyQt6.QtTest import QAbstractItemModelTester

from modules.cleanup.duplicates.models import DuplicateGroup, FileEntry, ScanResult
from modules.cleanup.tabs._duplicates_model import DuplicatesModel
from modules.cleanup.tabs._duplicates_tab import _DuplicatesTab


def test_model_enforces_keeper_and_updates_selection(qapp, tmp_path):
    entries = []
    for name in ("a", "b"):
        path = tmp_path / name
        path.write_bytes(b"abc")
        entries.append(FileEntry.from_path(str(path)))
    model = DuplicatesModel()
    tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
    model.set_result(ScanResult(groups=[DuplicateGroup(0, entries, "exact", "exact")]))
    group = model.index(0, 0)
    first, second = model.index(0, 0, group), model.index(1, 0, group)
    assert model.setData(first, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    assert not model.setData(second, Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    assert len(model.selected) == 1
    assert model.parent(second) == group
    assert tester is not None


def test_tab_is_manual_scan_and_cancels(qapp):
    tab = _DuplicatesTab()
    assert not tab._workers
    tab.auto_scan()
    assert not tab._workers
    tab._cancel_all()
    assert tab._scan_btn.isEnabled()
    tab.deleteLater()


def test_cancelled_worker_restores_controls_and_stale_callbacks_are_safe(qapp):
    import threading
    from core.long_op_pool import get_long_op_pool
    tab = _DuplicatesTab()
    entered = threading.Event()
    release = threading.Event()
    received = []
    def task(worker):
        entered.set()
        release.wait(3)
        return ScanResult()
    tab._launch(task, received.append)
    assert entered.wait(2)
    tab._cancel_all()
    release.set()
    assert get_long_op_pool().waitForDone(3000)
    qapp.processEvents()
    assert not tab._workers
    assert tab._active_worker is None
    assert tab._scan_btn.isEnabled()
    assert not received
    tab.deleteLater()


def test_model_sort_filter_and_large_virtual_group(qapp, tmp_path):
    from modules.cleanup.tabs._duplicates_model import DuplicatesProxy
    entries = [FileEntry(str(tmp_path / str(i)), i + 1, 1, 1, 1, i + 1) for i in range(100_000)]
    model = DuplicatesModel()
    model.set_result(ScanResult(groups=[DuplicateGroup(0, entries, "size", "candidate")]))
    parent = model.index(0, 0)
    assert model.rowCount(parent) == 100_000
    assert model.index(99_999, 1, parent).data() is not None
    proxy = DuplicatesProxy()
    proxy.setSourceModel(model)
    proxy.setFilterFixedString("99999")
    assert proxy.rowCount() == 1
    assert proxy.rowCount(proxy.index(0, 0)) == 1


def test_cleanup_duplicates_is_between_discover_and_history_without_scan(qapp):
    from test_cleanup_module_quick_tab import _module
    module, app = _module(qapp)
    try:
        names = [module._tabs.tabText(i) for i in range(module._tabs.count())]
        assert names[names.index("Discover") + 1:names.index("Discover") + 3] == ["Duplicates", "History"]
        module._tabs.setCurrentWidget(module._duplicates)
        assert not module._duplicates._workers
        module.refresh_data()
        assert not module._duplicates._workers
        module.on_deactivate()
        assert module._duplicates._scan_btn.isEnabled()
    finally:
        app.shutdown()


def test_deleted_tab_ignores_late_worker_signals(qapp):
    import threading
    from PyQt6 import sip
    from core.long_op_pool import get_long_op_pool
    release = threading.Event()
    tab = _DuplicatesTab()
    def task(worker):
        release.wait(3)
        worker.signals.log_line.emit('{"stage":"Late","files":0}')
        return ScanResult()
    tab._launch(task, lambda result: None)
    sip.delete(tab)
    release.set()
    assert get_long_op_pool().waitForDone(3000)
    qapp.processEvents()  # Closure guards must precede every Qt access.


def test_cancelled_actions_still_update_session_totals(qapp):
    import threading
    from core.long_op_pool import get_long_op_pool
    from modules.cleanup.duplicates.actions import ActionResult, ActionOutcome
    entered, release = threading.Event(), threading.Event()
    tab = _DuplicatesTab()
    freed = []
    tab.freed_bytes.connect(freed.append)
    def task(worker):
        entered.set()
        release.wait(3)
        return ActionResult([ActionOutcome("temp-file", "done", "delete", 12)], 12, True)
    tab._launch(task, tab._actions_done, action=True)
    assert entered.wait(2)
    tab._cancel_all()
    release.set()
    assert get_long_op_pool().waitForDone(3000)
    qapp.processEvents()
    assert freed == [12]
    assert tab._scan_btn.isEnabled()
    tab.deleteLater()

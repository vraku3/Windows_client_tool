"""MTP worker results and UI routing must stay separate from local paths."""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt

from modules.treesize.store.node_store import DIR, NodeStore
from modules.treesize.ui.panels import DriveList
from modules.treesize.ui.shell import TreeSizeShell
from treesize.test_mtp_scanner import PATH, Item, shell_for


@pytest.fixture(autouse=True)
def device(monkeypatch):
    from modules.treesize.scan import mtp_scanner as mtp
    shell_for(monkeypatch, mtp, Item("OnePlus 15", [
        Item("Internal", [Item("a.bin", Size=42)], Capacity=1000, FreeSpace=300)],
        path=PATH, kind="Portable device"))
    return mtp.MtpDevice("OnePlus 15", PATH, 1000, 300)


def test_worker_rolls_up_and_carries_capacity_and_com_lifetime(qapp, monkeypatch):
    from modules.treesize.ui import mtp_worker
    events = []
    monkeypatch.setattr(mtp_worker.pythoncom, "CoInitialize", lambda: events.append("init"))
    monkeypatch.setattr(mtp_worker.pythoncom, "CoUninitialize", lambda: events.append("uninit"))
    worker = mtp_worker.MtpScanWorker("OnePlus 15", PATH)
    results = []
    worker.signals.finished.connect(results.append)
    worker.run()
    assert events == ["init", "uninit"]
    result = results[0]
    assert result.engine == "mtp" and result.complete
    assert result.node_count == 3 and result.store.size[result.root] == 42
    assert (result.mtp_capacity, result.mtp_free) == (1000, 300)
    assert result.volume_info is None and result.elapsed > 0


def test_cancelled_worker_emits_no_result(qapp):
    from modules.treesize.ui.mtp_worker import MtpScanWorker
    worker = MtpScanWorker("Phone", PATH)
    events = []
    worker.signals.cancelled.connect(lambda: events.append("cancelled"))
    worker.signals.finished.connect(lambda _result: events.append("finished"))
    worker.cancel()
    worker.run()
    assert events == ["cancelled"]


def test_com_init_failure_reports_failure_without_uninitializing(qapp, monkeypatch):
    from modules.treesize.ui import mtp_worker
    def refused():
        raise OSError("COM refused")
    monkeypatch.setattr(mtp_worker.pythoncom, "CoInitialize", refused)
    monkeypatch.setattr(mtp_worker.pythoncom, "CoUninitialize",
                        lambda: pytest.fail("uninitialize without initialize"))
    worker = mtp_worker.MtpScanWorker("Phone", PATH)
    errors = []
    worker.signals.failed.connect(errors.append)
    worker.run()
    assert len(errors) == 1 and "COM refused" in errors[0]


def test_drive_list_emits_device_path_without_a_drive_suffix(qapp, device):
    panel = DriveList()
    panel.refresh([("C", 2000, 1000)])
    assert panel.topLevelItemCount() == 2
    phone = panel.topLevelItem(1)
    assert phone.text(0) == "OnePlus 15"
    assert phone.data(0, Qt.ItemDataRole.UserRole) == PATH
    emitted = []
    panel.drive_activated.connect(emitted.append)
    panel._on_double_clicked(phone, 0)
    panel._on_double_clicked(panel.topLevelItem(0), 0)
    assert emitted == [PATH, "C:\\"]
    panel.close()


def test_mtp_scan_refresh_and_dropdown_keep_shell_path(qapp, monkeypatch, device):
    from modules.treesize.ui.mtp_worker import MtpScanWorker
    shell = TreeSizeShell()
    workers = []
    shell._pool = SimpleNamespace(start=workers.append)
    shell.start_scan(PATH)
    assert isinstance(workers[0], MtpScanWorker)
    assert shell.path_combo.currentText() == "OnePlus 15"
    assert not shell.ribbon.action("scan.pause").isEnabled()
    workers[0].run()
    assert shell._result.store.size[shell._root] == 42
    shell.refresh_scan()
    assert len(workers) == 2 and workers[-1].shell_path == PATH
    workers[-1].run()
    shell._on_path_chosen(shell.path_combo.findText("OnePlus 15"))
    assert len(workers) == 3 and workers[-1].shell_path == PATH
    workers[-1].run()
    shell.close()


def test_mtp_result_does_not_query_local_drive_and_disables_filesystem_actions(qapp, monkeypatch):
    from modules.treesize.ui import shell as shell_module
    def no_drive(_letter):
        pytest.fail("An MTP name must not be interpreted as a local drive")
    monkeypatch.setattr(shell_module, "drive_space", no_drive)
    store = NodeStore()
    root = store.add(-1, "OnePlus 15", attrs=DIR)
    store.build_child_lists()
    result = SimpleNamespace(store=store, root=root, engine="mtp", node_count=1,
                             volume_info=None, excluded=0, complete=True,
                             mtp_capacity=1000, mtp_free=300)
    shell = TreeSizeShell()
    shell.show_result(result)
    assert "300" in shell.status_bar._free.text()
    assert "MTP" in shell.status_bar._notice.text()
    menu = shell.row_actions.menu_for(root)
    enabled = [a.text() for a in menu.actions() if a.isEnabled() and a.text()]
    assert enabled == ["Copy path", "Copy name", "Properties"]
    assert not shell.ribbon.action("scan.watch").isEnabled()
    shell.close()


def test_device_is_available_in_target_menu(qapp, monkeypatch):
    shell = TreeSizeShell()
    paths = []
    monkeypatch.setattr(shell, "start_scan", paths.append)
    shell._refresh_target_menu()
    action = next(a for a in shell.ribbon.menu("scan.select").actions()
                  if a.text() == "OnePlus 15")
    action.trigger()
    assert paths == [PATH]
    shell.close()

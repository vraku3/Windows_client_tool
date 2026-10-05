"""Connections, System Info, Installed Apps and Disk Space tabs, built without a pool."""
from types import SimpleNamespace

import pytest

from modules.dashboard import connections as cn


def test_connections_tab_filters_sorts_and_keeps_selection(qapp):
    from modules.dashboard.connections_tab import ConnectionsTab, PID
    tab = ConnectionsTab()
    rows = [cn.Connection("TCP4", "1.1.1.1", 1, "8.8.8.8", 443, "ESTABLISHED", 20, "b.exe"),
            cn.Connection("TCP4", "1.1.1.1", 2, "", 0, "LISTEN", 10, "a.exe"),
            cn.Connection("UDP4", "0.0.0.0", 53, "", 0, "", 30, "c.exe")]
    tab._apply(rows)
    assert tab._table.rowCount() == 3
    tab._set_filter("listening")
    assert tab._table.rowCount() == 2
    tab._set_filter("all")
    tab._sort_by(PID)
    pids = [int(tab._table.item(r, PID).text()) for r in range(3)]
    assert pids == sorted(pids, reverse=True)
    tab._table.selectRow(1)
    chosen = tab._selected().key
    tab._repopulate()
    assert tab._selected().key == chosen
    tab._apply(None)
    assert "refused" in tab.status.text()


def test_system_info_tab_renders_sections_and_copies(qapp):
    from modules.dashboard.sysinfo_tab import SystemInfoTab
    tab = SystemInfoTab()
    tab._apply([("System", [("Hostname", "PC")]), ("Memory", [("Total", "8 GB")])])
    assert tab.tree.topLevelItemCount() == 2 and tab.tree.topLevelItem(0).child(0).text(1) == "PC"
    tab._copy_all()
    from PyQt6.QtWidgets import QApplication
    assert "Hostname: PC" in QApplication.clipboard().text()


def test_installed_apps_tab_filters_and_sorts_size_numerically(qapp):
    from modules.dashboard.installed_apps_tab import InstalledAppsTab
    mk = lambda n, size: SimpleNamespace(name=n, version="1", publisher="P", install_date="",
                                         size_mb=size, type_="64-bit", uninstall_string="")
    tab = InstalledAppsTab()
    tab._apply([mk("small", "9 MB"), mk("big", "2 GB"), mk("mid", "100 MB"), mk("none", "")])
    tab._sort_by(4)
    names = [tab._table.item(r, 0).text() for r in range(4)]
    assert names[:3] == ["big", "mid", "small"], names      # 9 MB must not outrank 2 GB
    tab._search.setText("mid")
    assert tab._table.rowCount() == 1
    assert "no size reported" in tab.status.text() or tab._table.rowCount() == 1


def test_disk_space_tab_lists_volumes_and_scans_a_folder(qapp, tmp_path):
    from modules.dashboard import folder_sizes as fs
    from modules.dashboard.disk_space_tab import DiskSpaceTab
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "f").write_bytes(b"1" * 2048)
    tab = DiskSpaceTab()
    tab.refresh()
    assert tab._vol.rowCount() >= 1 and tab._scan_btn.isEnabled()
    tab._scan_done(fs.scan_top_level(str(tmp_path)))
    assert tab._folders.rowCount() == 1 and "1 folders" in tab.status.text()
    cancelled = fs.scan_top_level(str(tmp_path), cancelled=lambda: True)
    tab._scan_done(cancelled)
    assert "partial" in tab.status.text()


def test_disk_space_folder_row_jumps_into_treesize_at_its_own_path(qapp, tmp_path):
    """A row in the folder-scan table must open TreeSize AT THAT FOLDER, not
    on TreeSize's own last-shown state -- the whole point of the jump is to
    skip re-browsing to a path already found here."""
    import os
    from modules.dashboard import folder_sizes as fs
    from core.events import NAV_REQUEST_MODULE
    from modules.dashboard.disk_space_tab import DiskSpaceTab
    (tmp_path / "big").mkdir()
    (tmp_path / "big" / "f").write_bytes(b"1" * 4096)
    (tmp_path / "loose.txt").write_bytes(b"1" * 10)

    tab = DiskSpaceTab()
    tab._scan_done(fs.scan_top_level(str(tmp_path)))
    assert tab._folders.rowCount() == 2  # the "big" folder + the loose-files row

    published = []

    class _FakeBus:
        def publish(self, topic, data):
            published.append((topic, data))

    tab._app = SimpleNamespace(event_bus=_FakeBus())

    # Row 0 is the real folder (it sorts first, being bigger) -- double-click
    # must resolve to an absolute path TreeSize can scan directly.
    real_row = next(r for r in range(tab._folders.rowCount())
                     if tab._folders.item(r, 0).text() == "big")
    tab._open_folder_in_treesize(real_row, 0)
    assert len(published) == 1
    topic, data = published[0]
    assert topic == NAV_REQUEST_MODULE
    assert data.module_name == "TreeSize"
    assert data.path == os.path.join(str(tmp_path), "big")

    # The "(files in the root)" sentinel row names no folder -- it must not
    # publish a bogus path TreeSize would then fail to scan.
    sentinel_row = next(r for r in range(tab._folders.rowCount())
                         if tab._folders.item(r, 0).text() == "(files in the root)")
    published.clear()
    tab._open_folder_in_treesize(sentinel_row, 0)
    assert published == []


def test_disk_space_open_treesize_button_passes_the_selected_drive(qapp):
    from core.events import NAV_REQUEST_MODULE
    from modules.dashboard.disk_space_tab import DiskSpaceTab

    tab = DiskSpaceTab()
    published = []

    class _FakeBus:
        def publish(self, topic, data):
            published.append((topic, data))

    tab._app = SimpleNamespace(event_bus=_FakeBus())
    tab._selected_mount = lambda: "C:\\"
    tab._open_treesize()
    assert published and published[0][1].path == "C:\\"

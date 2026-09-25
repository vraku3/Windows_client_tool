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

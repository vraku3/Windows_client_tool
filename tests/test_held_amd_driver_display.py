"""A held AMD display driver is SHOWN as held, never silently dropped -- and
never installable. Display only: the hold itself stays exactly as strict.

Everything is faked: no real Windows Update search, install or driver query.
"""
from decimal import Decimal

from PyQt6.QtCore import Qt

from modules.updates import windows_updater
from modules.updates.windows_updater import (
    HELD_TEXT, WindowsUpdate, split_scan, wu_status_text,
)


class _Coll:
    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i]


class _Cat:
    def __init__(self, name):
        self.Name = name


class _Behavior:
    RebootBehavior = 0


class _Update:
    def __init__(self, title, type_=1, cls="", maker="", hwid=""):
        self.Title = title
        self.Type = type_
        self.DriverClass = cls
        self.DriverManufacturer = maker
        self.DriverHardwareID = hwid
        self.MaxDownloadSize = Decimal("1048576")
        self.MinDownloadSize = Decimal("0")
        self.MsrcSeverity = ""
        self.InstallationBehavior = _Behavior()
        self.KBArticleIDs = _Coll([])
        self.Categories = _Coll([_Cat("Drivers")])
        self.LastDeploymentChangeTime = "2026-10-01 00:00:00"
        self.IsHidden = False


def _amd():
    return _Update("Advanced Micro Devices, Inc. - Display - 32.0.1", type_=2,
                   cls="Display", maker="Advanced Micro Devices, Inc.",
                   hwid=r"PCI\VEN_1002&DEV_744C")


def _fake_search(monkeypatch, updates):
    import win32com.client

    class _Searcher:
        def Search(self, criteria):
            return type("R", (), {"Updates": _Coll(updates)})()

    class _Session:
        def CreateUpdateSearcher(self):
            return _Searcher()

    monkeypatch.setattr(win32com.client, "Dispatch", lambda progid: _Session())


# --- windows_updater -------------------------------------------------------

def test_held_driver_is_reported_through_held_out_and_never_returned(monkeypatch):
    _fake_search(monkeypatch, [_amd(), _Update("Security update")])
    held = []
    updates = windows_updater.fetch_pending_updates(held_out=held)

    assert [u.title for u in updates] == ["Security update"]
    assert len(held) == 1
    assert held[0].held is True
    assert held[0].identity is None          # nothing to install it with
    assert "Advanced Micro Devices" in held[0].title


def _capturing_search(monkeypatch, updates):
    import win32com.client
    seen = []

    class _Searcher:
        def Search(self, criteria):
            seen.append(criteria)
            return type("R", (), {"Updates": _Coll(updates)})()

    class _Session:
        def CreateUpdateSearcher(self):
            return _Searcher()

    monkeypatch.setattr(win32com.client, "Dispatch", lambda progid: _Session())
    return seen


def test_held_offers_are_found_with_drivers_off_and_no_driver_is_offered(monkeypatch):
    """Drivers are off by default. The criteria used to exclude them, which
    also hid every held AMD offer, so the held rows could never appear."""
    nic = _Update("Realtek - Net - 10.1", type_=2, cls="Net", maker="Realtek")
    seen = _capturing_search(monkeypatch, [_amd(), nic, _Update("Security update")])
    held = []
    updates = windows_updater.fetch_pending_updates(include_drivers=False, held_out=held)
    assert "Driver" not in seen[0]
    assert [u.title for u in updates] == ["Security update"]
    assert [h.title for h in held] == [_amd().Title]


def test_an_unreadable_type_is_not_offered_with_drivers_off(monkeypatch):
    class _NoType(_Update):
        @property
        def Type(self):
            raise OSError("refused")

        @Type.setter
        def Type(self, value):
            pass

    _capturing_search(monkeypatch, [_NoType("Mystery update")])
    assert windows_updater.fetch_pending_updates(include_drivers=False, held_out=[]) == []


def test_drivers_off_without_held_out_keeps_the_fast_criteria(monkeypatch):
    seen = _capturing_search(monkeypatch, [])
    windows_updater.fetch_pending_updates(include_drivers=False)
    assert seen == ["IsInstalled=0 and IsHidden=0 and Type<>'Driver'"]


def test_a_hold_check_that_raises_refuses_that_update_and_keeps_going(monkeypatch):
    """A COM refusal in the guard used to escape the loop and lose every
    result already collected."""
    import win32com.client

    class _Broken:
        pass

    def _refused(_u):
        raise OSError("COM refused")

    monkeypatch.setattr(windows_updater, "is_held_amd_display_driver", _refused)

    class _Session:
        def CreateUpdateDownloader(self):
            return object()

        def CreateUpdateInstaller(self):
            return object()

    monkeypatch.setattr(win32com.client, "Dispatch", lambda progid: _Session())
    broken = WindowsUpdate(kb="KB9", title="broken", classification="x", size_mb=1,
                           release_date="", identity=_Broken())
    stale = WindowsUpdate(kb="KB8", title="stale", classification="x", size_mb=1,
                          release_date="")
    results = windows_updater.install_updates_iter([broken, stale])
    assert [(r.title, r.success) for r in results] == [("broken", False), ("stale", False)]
    assert "hold could not be checked" in results[0].message


def test_hold_is_unchanged_without_held_out(monkeypatch):
    _fake_search(monkeypatch, [_amd()])
    assert windows_updater.fetch_pending_updates() == []


def test_install_refuses_a_held_entry_whatever_list_it_arrives_in(monkeypatch):
    import win32com.client

    calls = []

    class _Session:
        def CreateUpdateDownloader(self):
            calls.append("downloader")
            return object()

        def CreateUpdateInstaller(self):
            return object()

    def _dispatch(progid):
        if progid == "Microsoft.Update.Session":
            return _Session()
        calls.append(progid)                 # an UpdateColl would mean an install attempt
        raise AssertionError(f"held update reached {progid}")

    monkeypatch.setattr(win32com.client, "Dispatch", _dispatch)
    flagged = WindowsUpdate(kb="N/A", title="held one", classification="Drivers",
                            size_mb=1.0, release_date="", held=True)
    live_amd = WindowsUpdate(kb="N/A", title="live AMD", classification="Drivers",
                             size_mb=1.0, release_date="", identity=_amd())
    lines = []
    results = windows_updater.install_updates_iter([flagged, live_amd], output_cb=lines.append)

    assert results == []
    assert calls == ["downloader"]
    assert sum(HELD_TEXT in line for line in lines) == 2


def test_status_text_counts_held_apart_from_pending():
    pending = [WindowsUpdate(kb="KB1", title="a", classification="x", size_mb=1,
                             release_date="", severity="Critical")]
    held = [WindowsUpdate(kb="N/A", title="amd", classification="Drivers", size_mb=1,
                          release_date="", held=True)]
    text = wu_status_text(pending, held)
    assert text.startswith("1 pending update(s)")
    assert "1 held" in text
    assert "held" not in wu_status_text(pending, [])


def test_split_scan_moves_a_stray_held_entry_out_of_the_installable_list():
    stray = WindowsUpdate(kb="", title="amd", classification="", size_mb=0,
                          release_date="", held=True)
    ok = WindowsUpdate(kb="KB2", title="b", classification="", size_mb=0, release_date="")
    updates, held = split_scan({"updates": [ok, stray], "held": []})
    assert updates == [ok] and held == [stray]
    assert split_scan([ok]) == ([ok], [])


# --- Windows Updates tab ----------------------------------------------------

def test_tab_lists_held_row_unselectable_and_counts_it(qapp):
    from modules.updates.updates_module import _WinUpdatesTab

    tab = _WinUpdatesTab()
    ok = WindowsUpdate(kb="KB3", title="Cumulative", classification="Security",
                       size_mb=5, release_date="2026-10-01")
    amd = WindowsUpdate(kb="N/A", title="AMD - Display", classification="Drivers",
                        size_mb=500, release_date="2026-10-01", held=True)
    tab._on_updates({"updates": [ok], "held": [amd]})

    assert tab._table.rowCount() == 2
    assert tab._updates == [ok]
    held_row = 1
    assert tab._table.item(held_row, 7).text() == HELD_TEXT
    for col in range(tab._table.columnCount()):
        flags = tab._table.item(held_row, col).flags()
        assert not (flags & Qt.ItemFlag.ItemIsSelectable)
    assert "1 held" in tab._status_lbl.text()
    tab._table.selectAll()
    assert {i.row() for i in tab._table.selectedIndexes()} == {0}
    tab.deleteLater()


# --- Run All / unattended ---------------------------------------------------

class _Cfg:
    def __init__(self, values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


def test_wu_stage_reports_held_items_and_installs_nothing(monkeypatch):
    from modules.updates import stage_runners

    def _fake_fetch(include_hidden=False, patterns=None, include_drivers=True, held_out=None):
        held_out.append(WindowsUpdate(kb="N/A", title="AMD - Display", classification="Drivers",
                                      size_mb=500, release_date="", held=True))
        return []

    def _no_install(*a, **k):
        raise AssertionError("nothing may be installed")

    monkeypatch.setattr("modules.updates.windows_updater.fetch_pending_updates", _fake_fetch)
    monkeypatch.setattr("modules.updates.windows_updater.install_updates_iter", _no_install)

    class _App:
        config = _Cfg({"updates.wu_include_drivers": True})

    log = []
    result = stage_runners.run_wu_stage(_App(), log.append, lambda: False)

    assert result["held"] == [{"kb": "N/A", "title": "AMD - Display", "state": "held",
                               "message": HELD_TEXT}]
    assert result["installed_count"] == 0
    assert any("held" in line and "AMD - Display" in line for line in log)
    assert not any("up to date" in line for line in log)
    assert stage_runners.stage_failures("wu", result) == []

    flat = stage_runners.normalize_stage_data({"wu": result})
    assert flat["wu_held"] == result["held"]

    from modules.updates.report_generator import render_update_report_html
    html_text = render_update_report_html(flat, [])
    assert "AMD - Display" in html_text
    assert "Held by WinClientTool: AMD display driver" in html_text
    assert "0/0 installed, 1 held" in html_text


def test_wu_stage_keeps_held_items_when_disk_space_blocks_the_install(monkeypatch):
    """The preflight return used to drop `held`, so the report lost the hold."""
    from modules.updates import stage_runners

    def _fake_fetch(include_hidden=False, patterns=None, include_drivers=True, held_out=None):
        held_out.append(WindowsUpdate(kb="N/A", title="AMD - Display", classification="Drivers",
                                      size_mb=500, release_date="", held=True))
        return [WindowsUpdate(kb="KB1", title="Cumulative", classification="Security",
                              size_mb=900, release_date="")]

    monkeypatch.setattr("modules.updates.windows_updater.fetch_pending_updates", _fake_fetch)
    monkeypatch.setattr("core.disk_space.check_wu_preflight", lambda mb: "not enough space")

    class _App:
        config = _Cfg({})

    result = stage_runners.run_wu_stage(_App(), lambda line: None, lambda: False)
    assert [h["title"] for h in result["held"]] == ["AMD - Display"]
    assert stage_runners.normalize_stage_data({"wu": result})["wu_held"] == result["held"]


# --- Driver Manager ----------------------------------------------------------

def _driver(**kw):
    from modules.driver_manager.driver_reader import DriverInfo
    base = dict(device_name="AMD Radeon RX 7900 XTX", driver_class="DISPLAY",
                version="31.0.14000.58004", date="2022-12-01",
                publisher="Advanced Micro Devices, Inc.", signed=True, error_code=0,
                flags="", inf_name="oem12.inf", device_id="PCI\\VEN_1002&DEV_744C\\1",
                hardware_id="PCI\\VEN_1002&DEV_744C&SUBSYS_1")
    base.update(kw)
    return DriverInfo(**base)


def test_driver_manager_update_status_says_held():
    from modules.driver_manager.driver_module import _update_status_text
    from modules.driver_manager.vendor_updates.provider import HELD_STATUS

    assert HELD_STATUS == "Held (AMD display driver kept on purpose)"
    assert _update_status_text(_driver(), {}) == HELD_STATUS


def test_driver_manager_non_held_device_is_unchanged():
    from modules.driver_manager.driver_module import _update_status_text
    from modules.driver_manager.vendor_updates.provider import HELD_STATUS

    nic = _driver(device_name="Realtek NIC", driver_class="NET", publisher="Realtek",
                  hardware_id="PCI\\VEN_10EC&DEV_8125", device_id="PCI\\VEN_10EC\\1")
    assert _update_status_text(nic, {}) != HELD_STATUS


def test_held_ui_text_never_suggests_updating_the_driver():
    from modules.driver_manager.vendor_updates.provider import HELD_REASON, HELD_STATUS

    for text in (HELD_TEXT, HELD_STATUS, HELD_REASON):
        low = text.lower()
        assert "update the amd" not in low and "install the latest" not in low

"""Tests for the Windows Update COM boundary.

Everything here fakes the COM objects, because a real WU search needs the
service, the network and pending updates. What the fakes reproduce faithfully
is the one thing the live API does that the old tests never did: hand back a
`decimal.Decimal` for `MaxDownloadSize`.
"""
from decimal import Decimal

import pytest

from modules.updates import windows_updater


class _Coll:
    """A COM collection: .Count and .Item(i), not a Python sequence."""

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
    def __init__(self, reboot):
        self.RebootBehavior = reboot


class _Update:
    def __init__(self, title="Fake update", size=Decimal("549453824"),
                 kbs=("5001234",), min_size=Decimal("0"),
                 severity="Important", reboot=0):
        self.Title = title
        self.MaxDownloadSize = size
        self.MinDownloadSize = min_size
        self.MsrcSeverity = severity
        self.InstallationBehavior = _Behavior(reboot)
        self.KBArticleIDs = _Coll(kbs)
        self.Categories = _Coll([_Cat("Security Updates")])
        self.LastDeploymentChangeTime = "2026-08-19 00:00:00+00:00"
        self.IsHidden = False


def _install_fake_session(monkeypatch, updates):
    import win32com.client

    class _Searcher:
        def Search(self, criteria):
            return type("R", (), {"Updates": _Coll(updates)})()

    class _Session:
        def CreateUpdateSearcher(self):
            return _Searcher()

    monkeypatch.setattr(win32com.client, "Dispatch", lambda progid: _Session())


def test_size_mb_is_a_float_even_though_com_returns_decimal(monkeypatch):
    """WU declares MaxDownloadSize as a DECIMAL in wuapi.idl, so pywin32
    returns decimal.Decimal. Dividing by an int keeps it a Decimal, which then
    blew up in check_wu_preflight's `/ 1024.0`. Coerce at the boundary."""
    _install_fake_session(monkeypatch, [_Update(size=Decimal("549453824"))])

    updates = windows_updater.fetch_pending_updates()

    assert len(updates) == 1
    assert isinstance(updates[0].size_mb, float), type(updates[0].size_mb)
    assert updates[0].size_mb == pytest.approx(524.0, abs=0.5)


def test_size_text_is_an_upper_bound_not_an_exact_figure():
    """MaxDownloadSize is the whole-package maximum; the Defender security
    intelligence update here reported 1539 MB. Shown as `<= max`."""
    u = windows_updater.WindowsUpdate("KB2267602", "T", "Security Updates",
                                      1539.4, "2026-10-15")
    assert windows_updater.size_text(u) == "≤1539 MB"


def test_size_text_shows_a_range_when_the_minimum_is_known():
    u = windows_updater.WindowsUpdate("KB2267602", "T", "c", 1539.4, "d",
                                      min_size_mb=120.0)
    assert windows_updater.size_text(u) == "120–1539 MB"


def test_size_text_for_an_unreadable_size_is_a_dash():
    u = windows_updater.WindowsUpdate("KB1", "T", "c", 0.0, "d")
    assert windows_updater.size_text(u) == "—"


def test_the_minimum_download_size_is_read_from_com(monkeypatch):
    _install_fake_session(monkeypatch, [
        _Update(size=Decimal("104857600"), min_size=Decimal("52428800"))])
    updates = windows_updater.fetch_pending_updates()
    assert updates[0].min_size_mb == pytest.approx(50.0, abs=0.5)


def test_severity_and_reboot_behavior_are_read_from_com(monkeypatch):
    _install_fake_session(monkeypatch, [
        _Update(title="Critical one", severity="Critical", reboot=1),
        _Update(title="Low one", severity="Low", reboot=0),
    ])
    updates = windows_updater.fetch_pending_updates()
    assert updates[0].severity == "Critical"
    assert windows_updater.reboot_text(updates[0]) == "Yes"
    assert windows_updater.severity_text(updates[0]) == "Critical"
    assert windows_updater.reboot_text(updates[1]) == "No"


def test_reboot_and_severity_text_fall_back_honestly():
    from modules.updates.windows_updater import WindowsUpdate
    blank = WindowsUpdate("KB1", "T", "c", 10.0, "d")
    assert windows_updater.reboot_text(blank) == "—"
    assert windows_updater.severity_text(blank) == "—"
    assert windows_updater.reboot_text(
        WindowsUpdate("KB1", "T", "c", 10.0, "d", reboot="2")) == "May ask"


def test_summed_sizes_survive_the_preflight(monkeypatch):
    """The exact call the Run-All stage makes: sum(u.size_mb) into the
    preflight. This is the line that crashed on a real machine."""
    from core.disk_space import check_wu_preflight

    _install_fake_session(monkeypatch, [
        _Update(size=Decimal("549453824")),
        _Update(size=Decimal("104857600")),
    ])
    updates = windows_updater.fetch_pending_updates()

    assert check_wu_preflight(sum(u.size_mb for u in updates)) is None


def test_unreadable_size_falls_back_to_zero_float(monkeypatch):
    class _Broken(_Update):
        def __init__(self):
            super().__init__()
            del self.MaxDownloadSize

        def __getattr__(self, name):
            if name == "MaxDownloadSize":
                raise OSError("COM said no")
            raise AttributeError(name)

    _install_fake_session(monkeypatch, [_Broken()])
    updates = windows_updater.fetch_pending_updates()
    assert updates[0].size_mb == 0.0
    assert isinstance(updates[0].size_mb, float)


def test_a_failed_search_reports_a_sentence_not_a_tuple(monkeypatch):
    """What the user actually saw when the Run-All cleanup stage restarted
    wuauserv under a search already in flight:

        Failed to query Windows Updates: (-2147352567, 'Exception occurred.',
        (0, None, None, None, 0, -2145124322), None)

    Every number there is either meaningless or in the wrong base. The app has
    known what 0x8024001E means since wu_error_codes was written.
    """
    import pywintypes
    import win32com.client

    class _Searcher:
        def Search(self, criteria):
            raise pywintypes.com_error(
                -2147352567, "Exception occurred.",
                (0, None, None, None, 0, -2145124322), None)

    monkeypatch.setattr(win32com.client, "Dispatch",
                        lambda progid: type("S", (), {"CreateUpdateSearcher": lambda self: _Searcher()})())

    with pytest.raises(RuntimeError) as exc:
        windows_updater.fetch_pending_updates()

    text = str(exc.value)
    assert "0x8024001E" in text, text
    assert "shutting down" in text, text


def test_include_drivers_false_adds_the_type_exclusion_to_search_criteria(monkeypatch):
    """Settings has a checkbox, "Include driver updates from Windows Update",
    that persists to updates.wu_include_drivers and reloads correctly — but
    until this fix, fetch_pending_updates took no such parameter at all, so
    unchecking it changed nothing in the interactive tab, Run All, or the
    unattended runner. Type<>'Driver' is the same search-criteria syntax
    windows_update_driver_check.py already verified live (its Type='Driver'
    counterpart), just negated."""
    import win32com.client

    seen_criteria = []

    class _Searcher:
        def Search(self, criteria):
            seen_criteria.append(criteria)
            return type("R", (), {"Updates": _Coll([])})()

    monkeypatch.setattr(
        win32com.client, "Dispatch",
        lambda progid: type("S", (), {"CreateUpdateSearcher": lambda self: _Searcher()})(),
    )

    windows_updater.fetch_pending_updates(include_drivers=False)
    assert "Type<>'Driver'" in seen_criteria[-1]

    windows_updater.fetch_pending_updates(include_drivers=True)
    assert "Type<>'Driver'" not in seen_criteria[-1]


def test_include_drivers_defaults_to_true_for_backward_compatibility(monkeypatch):
    """Every pre-existing call site (before this fix) called fetch_pending_updates
    with no include_drivers argument at all, and must keep seeing every
    update type by default."""
    _install_fake_session(monkeypatch, [_Update(title="A driver update")])
    updates = windows_updater.fetch_pending_updates()
    assert len(updates) == 1


def test_a_non_com_failure_keeps_its_own_message(monkeypatch):
    import win32com.client

    def _boom(progid):
        raise OSError("COM is not registered")

    monkeypatch.setattr(win32com.client, "Dispatch", _boom)

    with pytest.raises(RuntimeError) as exc:
        windows_updater.fetch_pending_updates()
    assert "COM is not registered" in str(exc.value)

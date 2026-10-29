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


def test_structured_exclusions_keep_offers_separate_without_com_identity(monkeypatch):
    _install_fake_session(monkeypatch, [_Update()])
    excluded = []
    entries = [{"pattern": "KB5001234", "reason": "Pinned", "expires": None}]
    assert windows_updater.fetch_pending_updates(patterns=entries, excluded_out=excluded) == []
    assert len(excluded) == 1
    assert excluded[0].kb == "KB5001234"
    assert excluded[0].identity is None
    entries[0]["expires"] = "2000-01-01"
    assert len(windows_updater.fetch_pending_updates(patterns=entries)) == 1


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


def test_scan_marks_reboot_required_update_as_installed_waiting(monkeypatch):
    u = _Update()
    u.IsInstalled = False
    u.RebootRequired = True
    _install_fake_session(monkeypatch, [u])
    updates = windows_updater.fetch_pending_updates()
    assert updates[0].pending_restart
    assert windows_updater.update_status_text(updates[0]) == "Installed -- restart to finish"


@pytest.mark.parametrize("aggregate,per_update,code", [(True, False, 2), (False, True, 2), (True, True, 2)])
def test_install_reboot_is_pending_not_success(monkeypatch, aggregate, per_update, code):
    import win32com.client
    from types import SimpleNamespace as NS
    identity = _Update()
    identity.IsDownloaded = True
    identity.EulaAccepted = True
    calls = []
    result = NS(ResultCode=code, HResult=0, RebootRequired=aggregate,
                GetUpdateResult=lambda i: NS(RebootRequired=per_update))
    installer = NS(Install=lambda: calls.append("install") or result)
    session = NS(CreateUpdateDownloader=lambda: NS(), CreateUpdateInstaller=lambda: installer)
    monkeypatch.setattr(win32com.client, "Dispatch", lambda name: (
        NS(Add=lambda u: None) if name.endswith("UpdateColl") else session))
    lines = []
    u = windows_updater.WindowsUpdate("KB1", "T", "c", 92638, "d", identity=identity)
    results = windows_updater.install_updates_iter([u], output_cb=lines.append)
    assert len(calls) == 1 and results[0].reboot_required and not results[0].success
    assert results[0].message == "installed -- restart to finish"
    assert "92638" not in " ".join(lines)


def test_install_does_not_reinstall_identity_now_awaiting_restart(monkeypatch):
    import win32com.client
    from types import SimpleNamespace as NS
    identity = _Update()
    identity.RebootRequired = True
    session = NS(CreateUpdateDownloader=lambda: NS(), CreateUpdateInstaller=lambda: NS())
    monkeypatch.setattr(win32com.client, "Dispatch", lambda name: session)
    u = windows_updater.WindowsUpdate("KB1", "T", "c", 1, "d", identity=identity)
    results = windows_updater.install_updates_iter([u])
    assert results[0].reboot_required and not results[0].success


def test_unreadable_restart_properties_keep_existing_scan_behavior(monkeypatch):
    class Unreadable(_Update):
        @property
        def RebootRequired(self):
            raise OSError("refused")
        @property
        def IsInstalled(self):
            raise OSError("refused")
    _install_fake_session(monkeypatch, [Unreadable()])
    updates = windows_updater.fetch_pending_updates()
    assert len(updates) == 1 and not updates[0].pending_restart


def test_pending_installation_identity_prevents_reinstall_without_com_flag(monkeypatch):
    from types import SimpleNamespace as NS
    from datetime import datetime, timezone
    import win32com.client
    from core import pending_reboot
    monkeypatch.setattr(pending_reboot, "check", lambda: NS(needed=True, reasons=["Windows Update"]))
    identity = _Update(kbs=("5127648",))
    identity.Identity = NS(UpdateID="same-offer")
    history = _Coll([NS(Title="Security Update KB5127648", ResultCode=2, Operation=1,
                        UpdateIdentity=NS(UpdateID="same-offer"), Date=datetime.now(timezone.utc))])
    searcher = NS(Search=lambda criteria: NS(Updates=_Coll([identity])),
                  GetTotalHistoryCount=lambda: 1, QueryHistory=lambda start, count: history)
    monkeypatch.setattr(win32com.client, "Dispatch", lambda _: NS(CreateUpdateSearcher=lambda: searcher))
    assert windows_updater.fetch_pending_updates()[0].pending_restart


def test_unreadable_or_preboot_history_does_not_block_current_offer(monkeypatch):
    from types import SimpleNamespace as NS
    from datetime import datetime, timezone
    from core import pending_reboot
    monkeypatch.setattr(pending_reboot, "check", lambda: NS(needed=True, reasons=[]))
    history = _Coll([NS(Title="KB1", ResultCode=2, Date=datetime.now(timezone.utc)),
                     NS(Title="KB1", ResultCode=1, Date=datetime.now(timezone.utc)),
                     NS(Title="KB2", ResultCode=1, Date=datetime(2020, 1, 1, tzinfo=timezone.utc))])
    searcher = NS(GetTotalHistoryCount=lambda: 3, QueryHistory=lambda start, count: history)
    assert windows_updater._pending_restart_ids(searcher) == set()


def test_download_fetches_without_installing_and_marks_the_offer(monkeypatch):
    """#14: Download is a real step of its own -- bytes land in WUA's cache,
    no installer is created, and Install can reuse them later."""
    import win32com.client
    from types import SimpleNamespace as NS
    identity = _Update()
    identity.IsDownloaded = False
    identity.EulaAccepted = True
    calls = []
    downloader = NS(Download=lambda: calls.append("download") or NS(ResultCode=2, HResult=0))
    session = NS(CreateUpdateDownloader=lambda: downloader)
    monkeypatch.setattr(
        win32com.client, "Dispatch",
        lambda name: NS(Add=lambda u: None) if name.endswith("UpdateColl") else session)
    u = windows_updater.WindowsUpdate("KB1", "T", "c", 10.0, "d", identity=identity)
    results = windows_updater.download_updates_iter([u])
    assert calls == ["download"]
    assert results[0].success and results[0].message == "downloaded"
    assert u.downloaded is True


def test_download_skips_an_already_downloaded_offer(monkeypatch):
    import win32com.client
    from types import SimpleNamespace as NS
    identity = _Update()
    identity.IsDownloaded = True
    session = NS(CreateUpdateDownloader=lambda: NS(
        Download=lambda: pytest.fail("must not download again")))
    monkeypatch.setattr(
        win32com.client, "Dispatch",
        lambda name: NS(Add=lambda u: None) if name.endswith("UpdateColl") else session)
    u = windows_updater.WindowsUpdate("KB1", "T", "c", 10.0, "d", identity=identity)
    results = windows_updater.download_updates_iter([u])
    assert results[0].success and results[0].message == "already downloaded"
    assert u.downloaded is True


def test_a_refused_download_is_reported_and_not_marked_downloaded(monkeypatch):
    import win32com.client
    from types import SimpleNamespace as NS
    identity = _Update()
    identity.IsDownloaded = False
    identity.EulaAccepted = True
    downloader = NS(Download=lambda: NS(ResultCode=4, HResult=-2145124322))
    session = NS(CreateUpdateDownloader=lambda: downloader)
    monkeypatch.setattr(
        win32com.client, "Dispatch",
        lambda name: NS(Add=lambda u: None) if name.endswith("UpdateColl") else session)
    u = windows_updater.WindowsUpdate("KB1", "T", "c", 10.0, "d", identity=identity)
    results = windows_updater.download_updates_iter([u])
    assert not results[0].success
    assert "FAILED" in results[0].message
    assert u.downloaded is False


def test_scan_captures_driver_identity_and_security_identifiers(monkeypatch):
    class _Driver(_Update):
        def __init__(self):
            super().__init__(title="Intel Corporation - Display - 31.0")
            self.Type = 2
            self.DriverClass = "Display"
            self.DriverManufacturer = "Intel Corporation"
            self.SecurityBulletinIDs = _Coll(["MS17-010"])
            self.CveIds = _Coll(["CVE-2026-1234"])

    _install_fake_session(monkeypatch, [_Driver()])
    updates = windows_updater.fetch_pending_updates(include_drivers=True)
    update = updates[0]
    assert update.driver is True
    assert update.driver_class == "Display"
    assert update.driver_manufacturer == "Intel Corporation"
    assert update.security_bulletins == ["MS17-010"]
    assert update.cve_ids == ["CVE-2026-1234"]


def test_scan_without_published_security_identifiers_says_nothing(monkeypatch):
    """A build that does not expose CveIds, or publishes no bulletins, must
    leave both lists empty -- never invent an identifier."""
    _install_fake_session(monkeypatch, [_Update()])
    update = windows_updater.fetch_pending_updates()[0]
    assert update.security_bulletins == [] and update.cve_ids == []
    assert update.driver is False


def test_download_stops_between_items(monkeypatch):
    """Stop is checked before each item; the one in flight finishes."""
    import win32com.client
    from types import SimpleNamespace as NS
    calls = []

    def _identity():
        identity = _Update()
        identity.IsDownloaded = False
        identity.EulaAccepted = True
        return identity

    downloader = NS(Download=lambda: calls.append("download") or NS(ResultCode=2, HResult=0))
    session = NS(CreateUpdateDownloader=lambda: downloader)
    monkeypatch.setattr(
        win32com.client, "Dispatch",
        lambda name: NS(Add=lambda u: None) if name.endswith("UpdateColl") else session)
    updates = [windows_updater.WindowsUpdate(f"KB{i}", f"T{i}", "c", 10.0, "d",
                                             identity=_identity()) for i in (1, 2)]
    results = windows_updater.download_updates_iter(
        updates, is_cancelled=lambda: len(calls) >= 1)
    assert len(calls) == 1
    assert len(results) == 1 and results[0].success


def test_install_order_puts_restart_offers_last_and_keeps_scan_order():
    from modules.updates.windows_updater import WindowsUpdate, order_for_install
    a = WindowsUpdate("KB1", "A", "c", 1, "d", reboot="0")
    b = WindowsUpdate("KB2", "B", "c", 1, "d", reboot="1")
    c = WindowsUpdate("KB3", "C", "c", 1, "d", reboot="2")
    d = WindowsUpdate("KB4", "D", "c", 1, "d", reboot="")
    assert [u.kb for u in order_for_install([b, a, c, d])] == ["KB1", "KB2", "KB3", "KB4"]

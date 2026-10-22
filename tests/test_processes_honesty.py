"""Deterministic regressions for the Processes visibility and identity fixes."""
from dataclasses import fields, replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from core.procengine.ntquery import ProcessRaw
from core.procengine.details import ProcessDetails
from core.procengine.rates import Rates
from core.procengine.snapshot import ProcessInfo, Snapshot
from core.procengine.grouping import AppEntry, Group, APPS
from modules.dashboard import process_view as pv, processes_tab as pt

EPOCH = 116444736000000000


def info(pid=10, ppid=0, started=100, **details):
    values = {f.name: 0 for f in fields(ProcessRaw)}
    values.update(pid=pid, ppid=ppid, name=f"proc{pid}.exe", session=1,
                  create_time=EPOCH + started * 10_000_000)
    details.setdefault("path", f"C:/proc{pid}.exe")
    return ProcessInfo(ProcessRaw(**values), Rates(), ProcessDetails(pid, **details))


@pytest.fixture
def tab(qapp):
    t = pt.ProcessesTab()
    a, b, c = info(10, user="alice", elevated=False), info(11, user="bob", elevated=True), info(12)
    t._apply((Snapshot(by_pid={i.pid: i for i in [a, b, c]}),
              [Group(APPS, [AppEntry("Editor", 10, [a, b])]), Group("Background processes", [c])]))
    yield t
    t.cancel_all()
    t.deleteLater()


def test_flat_default_lists_every_process_once_and_sorts(tab):
    assert tab.tree.topLevelItemCount() == 3
    assert {tab.tree.topLevelItem(i).data(0, pt.PID_ROLE) for i in range(3)} == {10, 11, 12}
    assert "3 processes listed" in tab.status.text()
    tab._sort_by(4)
    assert [tab.tree.topLevelItem(i).text(4) for i in range(3)] == ["12", "11", "10"]


def test_grouped_counts_track_expansion_and_filtered_members(tab):
    tab.view_mode.setCurrentIndex(1)
    header = tab.tree.topLevelItem(0)
    app = header.child(0)
    assert header.text(0) == "Apps (1 apps, 2 processes)"
    assert "(2 processes)" in app.text(0)
    assert "2 inside collapsed apps" in tab.status.text()
    assert "1 processes listed" in tab.status.text()
    tab.expand_button.click()
    assert app.isExpanded() and "3 processes listed" in tab.status.text()
    tab.collapse_button.click()
    tab.filter_box.setText("bob")
    app = tab.tree.topLevelItem(0).child(0)
    assert app.childCount() == 1 and app.isExpanded()
    assert app.child(0).data(0, pt.PID_ROLE) == 11
    assert app.data(0, pt.PID_ROLE) == 11  # actions cannot target a filtered-out member
    assert "1 processes listed" in tab.status.text()


def test_search_and_chip_must_match_same_member_and_reset(tab):
    tab.view_mode.setCurrentIndex(1)
    tab.filter_box.setText("alice")
    tab._set_filter("elevated")
    assert tab.tree.topLevelItemCount() == 0
    assert tab._chips["elevated"][0].text() == "Elevated (0)"
    tab.reset_button.click()
    assert tab.filter_box.text() == "" and tab._filter == "all"
    assert tab.tree.topLevelItemCount() == 2


def test_view_choice_persists_and_invalid_config_defaults_flat(tab):
    saved = {}
    config = NS(get=lambda k, d=None: saved.get(k, d), set=lambda k, v: saved.update({k: v}))
    tab.set_app(NS(config=config))
    tab.view_mode.setCurrentIndex(1)
    assert saved["modules.dashboard.process_view_mode"] == "grouped"
    other = pt.ProcessesTab()
    other.set_app(NS(config=config))
    assert other.view_mode.currentData() == "grouped"
    saved["modules.dashboard.process_view_mode"] = "invalid"
    other.set_app(NS(config=config))
    assert other.view_mode.currentData() == "flat"
    other.deleteLater()


@pytest.mark.parametrize("elevated", [True, False])
def test_unreadable_footer_and_chip_agree_and_do_not_claim_hidden_rows(tab, monkeypatch, elevated):
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: elevated)
    rows = [replace(info(pid), details=ProcessDetails(pid)) for pid in (0, 4, 20)]
    tab._apply((Snapshot(by_pid={i.pid: i for i in rows}), [Group("Windows processes", rows)]))
    assert "1 processes' details could not be read" in tab.status.text()
    assert ("run as administrator for more" in tab.status.text()) == (not elevated)
    assert tab._chips["unreadable"][0].text() == "Unreadable (1)"
    tab._set_filter("unreadable")
    assert tab.tree.topLevelItemCount() == 1


def test_partial_aggregate_and_reused_parent_links():
    a, b = info(10, user="alice"), info(11)
    assert pv.aggregate_text(pv.BY_KEY["user"], [a, b]) == "mixed / partly unreadable"
    child, recycled_parent, older_child = info(20, 10, 100), info(10, 0, 200), info(30, 20, 50)
    snap = Snapshot(by_pid={i.pid: i for i in (child, recycled_parent, older_child)})
    text = pv.detail_text(child, snap)
    assert "Parent:     PID 10 (gone)" in text
    assert "Children:" not in text


def test_grouping_never_claims_overlapping_family_twice(monkeypatch):
    from core.procengine import grouping as g
    rows = [info(i) for i in (10, 11, 12)]
    snap = Snapshot(by_pid={i.pid: i for i in rows})
    monkeypatch.setattr(g, "_family", lambda s, pid: {10, 12} if pid == 10 else {11, 12})
    groups = g.group_processes(snap, {10: ["a"], 11: ["b"]})
    pids = [m.pid for group in groups for row in group.rows for m in getattr(row, "members", [row])]
    assert sorted(pids) == [10, 11, 12]


def test_search_ranks_before_cap():
    from modules.dashboard.process_search import ProcessSearchProvider
    from core.search_provider import SearchQuery
    rows = [info(i, cmdline="needle") for i in range(10, 100)]
    exact = replace(info(500), raw=replace(info(500).raw, name="needle"))
    provider = ProcessSearchProvider()
    provider._source = NS(read=lambda: Snapshot(by_pid={i.pid: i for i in rows + [exact]}))
    results = provider.search(SearchQuery(text="needle"))
    assert len(results) == 50 and results[0].detail.pid == 500


def test_cancel_holds_busy_until_finished_and_ignores_old_results(tab):
    pool = NS(start=lambda w: None)
    tab.set_app(NS(thread_pool=pool))
    tab.refresh()
    worker = tab._workers[0]
    snapshot = tab._snapshot
    tab.cancel_all()
    assert tab._busy
    tab.refresh()
    assert not tab._workers
    worker.signals.result.emit((Snapshot(), []))
    worker.signals.error.emit("stale error")
    assert tab._snapshot is snapshot and "stale error" not in tab.status.text()
    worker.signals.finished.emit()
    assert not tab._busy


@pytest.mark.parametrize("action,args", [
    ("end_process", ()), ("set_priority", ("normal",)), ("set_affinity", ([0],)),
    ("suspend_process", ()), ("resume_process", ()), ("restart_process", ()),
    ("create_dump", ("unused.dmp",)), ("end_process_tree", ()),
])
def test_mutations_refuse_reused_pid_before_side_effects(monkeypatch, action, args):
    from core.procengine import actions as a
    process = Mock()
    process.create_time.return_value = 101.0
    monkeypatch.setattr(a.psutil, "Process", lambda pid: process)
    result = getattr(a, action)(10, *args, create_time=100.0)
    assert not result.ok and "a different process now has this PID" in result.message
    process.kill.assert_not_called()
    process.nice.assert_not_called()


def test_vt_failure_distinct_from_404(monkeypatch):
    import requests
    from core import virustotal_client as vt
    monkeypatch.setattr(vt.requests, "get", Mock(side_effect=requests.ConnectionError("offline")))
    result = vt.check_hash("abc", "key")
    assert result.error and "offline" in result.error
    assert not result.found


def test_vt_failure_dialog_has_no_detection_count(tab, monkeypatch):
    from core.virustotal_client import VTResult
    shown = []
    monkeypatch.setattr("modules.dashboard.process_menu.QMessageBox.information",
                        lambda w, title, text: shown.append(text))
    tab.menu._on_vt_result(VTResult(False, "abc", error="rate limit"))
    assert "lookup failed: rate limit" in shown[0].lower()
    assert "detections" not in shown[0] and "unknown" not in shown[0]


@pytest.mark.parametrize("field,value", [
    ("description", "special description"), ("user", "special user"),
    ("path", "C:/special/app.exe"), ("cmdline", "app.exe --special"),
])
def test_every_app_member_uses_full_search_predicate(tab, field, value):
    member = info(11, **{field: value})
    tab._apply((Snapshot(by_pid={11: member}), [Group(APPS, [AppEntry("App", 11, [member])])]))
    tab.view_mode.setCurrentIndex(1)
    tab.filter_box.setText("special")
    assert tab.tree.topLevelItem(0).child(0).child(0).data(0, pt.PID_ROLE) == 11
    tab.filter_box.setText("11")
    assert tab.tree.topLevelItemCount() == 1
    tab.filter_box.setText("1")
    # PID alone must be exact, not a substring (the executable also has 11).
    isolated = replace(member, raw=replace(member.raw, name="app.exe"),
                       details=replace(member.details, path="C:/app.exe"))
    assert pt._matches(isolated, "1") is False


def test_collapsing_category_does_not_count_hidden_processes(tab):
    tab.view_mode.setCurrentIndex(1)
    tab.expand_button.click()
    tab.tree.topLevelItem(0).setExpanded(False)
    assert "1 processes listed" in tab.status.text()


@pytest.mark.parametrize("elevation", [False, True, None])
def test_restart_reads_elevation_before_ending_and_routes_launch(monkeypatch, elevation):
    from core.procengine import actions as a, details
    process = Mock()
    process.create_time.return_value = 100.0
    process.exe.return_value = "C:/app.exe"
    process.cmdline.return_value = ["C:/app.exe", "two words"]
    process.username.return_value = "ADMIN\\user"
    monkeypatch.setattr(a.psutil, "Process", lambda pid: process)
    monkeypatch.setattr(details, "resolve", lambda pid: ProcessDetails(pid, elevated=elevation))
    monkeypatch.setattr("core.shell_launch.desktop_account", lambda: "DESKTOP\\user")
    ended, shell, elevated = Mock(return_value=a.Result(True)), Mock(return_value=None), Mock(return_value=a.Result(True))
    monkeypatch.setattr(a, "end_process", ended)
    monkeypatch.setattr("core.shell_launch.launch", shell)
    monkeypatch.setattr(a, "_launch", elevated)
    result = a.restart_process(10, create_time=100.0)
    if elevation is None:
        assert not result.ok and "elevation" in result.message
        ended.assert_not_called()
        shell.assert_not_called()
        elevated.assert_not_called()
    else:
        assert result.ok
        ended.assert_called_once_with(10, timeout=a.KILL_TIMEOUT, create_time=100.0)
        if elevation:
            elevated.assert_called_once_with("C:/app.exe", '"two words"', runas=True)
            shell.assert_not_called()
        else:
            shell.assert_called_once_with("C:/app.exe", '"two words"')
            elevated.assert_not_called()


def test_end_and_dump_are_tracked_workers_with_original_identity(tab, monkeypatch):
    from modules.dashboard import process_menu as pm
    queued, calls = [], []
    tab.menu.set_app(NS(thread_pool=NS(start=queued.append)))
    monkeypatch.setattr(tab.menu, "_confirm", lambda *args: True)
    monkeypatch.setattr(pm, "end_process", lambda pid, **kw: calls.append(("end", pid, kw)) or pm.Result(True))
    monkeypatch.setattr(pm, "create_dump", lambda pid, path, **kw: calls.append(("dump", pid, kw)) or pm.Result(True))
    monkeypatch.setattr(pm.QFileDialog, "getSaveFileName", lambda *args: ("unused.dmp", ""))
    original = tab._snapshot.by_pid[10]
    tab.menu._end([10])
    tab.menu._dump(10, original)
    assert calls == [] and len(tab.menu._workers) == 2
    tab.menu.set_snapshot(Snapshot(by_pid={10: info(10, started=200)}))
    for worker in queued:
        worker.run()
    assert calls == [("end", 10, {"create_time": 100.0}), ("dump", 10, {"create_time": 100.0})]
    assert tab.menu._workers == []


def test_confirmations_keep_selected_identity_and_tree_member_identities(tab, monkeypatch):
    from modules.dashboard import process_menu as pm
    a, b = info(10), info(11, ppid=10, started=110)
    tab.menu.set_snapshot(Snapshot(by_pid={10: a, 11: b}))
    queued, calls = [], []
    tab.menu.set_app(NS(thread_pool=NS(start=queued.append)))
    def confirm(*args):
        tab.menu.set_snapshot(Snapshot(by_pid={10: info(10, started=200)}))
        return True
    monkeypatch.setattr(tab.menu, "_confirm", confirm)
    monkeypatch.setattr(pm, "end_process_tree", lambda pid, **kw: calls.append(kw) or pm.Result(True))
    tab.menu._end_tree(10)
    queued[0].run()
    assert calls == [{"create_time": 100.0, "members": {10: 100.0, 11: 110.0}}]


def test_restart_confirmation_names_relaunch_account_and_elevation(tab, monkeypatch):
    from modules.dashboard import process_menu as pm
    from core.procengine.actions import RestartContext
    confirmed = []
    monkeypatch.setattr(tab.menu, "_confirm", lambda *args: confirmed.append(args) or False)
    tab.menu._confirm_restart(10, {10: 100},
                             (RestartContext("app", "", False, "DESKTOP\\alice"), None))
    assert "DESKTOP\\alice" in confirmed[0][2] and "unelevated" in confirmed[0][2]


def test_tree_refuses_reused_child_and_never_kills_replacement(monkeypatch):
    from core.procengine import actions as a
    root, child = Mock(), Mock()
    root.create_time.return_value = 100.0
    child.create_time.return_value = 999.0
    monkeypatch.setattr(a.psutil, "Process", lambda pid: root if pid == 10 else child)
    monkeypatch.setattr(a, "is_running", lambda pid: pid == 11)
    result = a.end_process_tree(10, create_time=100.0, members={10: 100.0, 11: 110.0})
    assert not result.ok
    child.kill.assert_not_called()


def test_tree_refuses_root_reused_between_identity_check_and_tree_read(monkeypatch):
    from core.procengine import actions as a, snapshot
    process = Mock()
    process.create_time.return_value = 100.0
    monkeypatch.setattr(a.psutil, "Process", lambda pid: process)
    monkeypatch.setattr(a, "is_running", lambda pid: True)
    monkeypatch.setattr(snapshot, "system_processes", lambda: [info(10, started=200).raw,
                                                              info(11, 10, 210).raw])
    end = Mock(return_value=a.Result(True))
    monkeypatch.setattr(a, "end_process", end)
    result = a.end_process_tree(10, create_time=100.0)
    assert not result.ok
    end.assert_not_called()


def test_tree_keeps_each_members_own_identity_for_the_root_too(monkeypatch):
    from core.procengine import actions as a
    process = Mock()
    process.create_time.return_value = 100.0
    monkeypatch.setattr(a.psutil, "Process", lambda pid: process)
    monkeypatch.setattr(a, "is_running", lambda pid: True)
    end = Mock(return_value=a.Result(True))
    monkeypatch.setattr(a, "end_process", end)
    assert a.end_process_tree(10, create_time=100, members={10: 100, 11: 110}).ok
    assert end.call_args_list[0].kwargs["create_time"] == 110
    assert end.call_args_list[1].kwargs["create_time"] == 100


@pytest.mark.parametrize("status", [401, 403, 429])
def test_vt_http_failure_is_not_unknown(monkeypatch, status):
    from core import virustotal_client as vt
    import requests
    response = Mock(status_code=status)
    response.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    monkeypatch.setattr(vt.requests, "get", lambda *a, **kw: response)
    assert str(status) in vt.check_hash("abc", "bad-key").error


def test_vt_confirmed_404_has_no_count_or_failure(tab, monkeypatch):
    from core import virustotal_client as vt
    shown = []
    monkeypatch.setattr(vt.requests, "get", lambda *a, **kw: NS(status_code=404))
    monkeypatch.setattr("modules.dashboard.process_menu.QMessageBox.information",
                        lambda w, title, text: shown.append(text))
    result = vt.check_hash("abc", "key")
    assert result.error is None
    tab.menu._on_vt_result(result)
    assert "unknown to VirusTotal" in shown[0]
    assert "0 detections" not in shown[0]


@pytest.mark.parametrize("action,args", [("suspend_process", ()), ("resume_process", ()),
                                         ("create_dump", ("unused.dmp",))])
def test_native_actions_check_the_opened_handles_identity(monkeypatch, action, args):
    from core.procengine import actions as a
    import ctypes
    from ctypes import wintypes
    process = Mock()
    process.create_time.return_value = 100.0
    monkeypatch.setattr(a.psutil, "Process", lambda pid: process)
    monkeypatch.setattr(a._kernel32, "OpenProcess", lambda *args: 123)
    closed, mutated = [], []
    monkeypatch.setattr(a._kernel32, "CloseHandle", closed.append)
    def times(handle, created, *rest):
        ticks = EPOCH + 200 * 10_000_000
        value = ctypes.cast(created, ctypes.POINTER(wintypes.FILETIME)).contents
        value.dwLowDateTime, value.dwHighDateTime = ticks & 0xFFFFFFFF, ticks >> 32
        return True
    monkeypatch.setattr(a._kernel32, "GetProcessTimes", times)
    monkeypatch.setattr(a._ntdll, "NtSuspendProcess", lambda h: mutated.append(h) or 0)
    monkeypatch.setattr(a._ntdll, "NtResumeProcess", lambda h: mutated.append(h) or 0)
    monkeypatch.setattr(a, "_write_dump", lambda *args: mutated.append(args) or a.Result(True))
    result = getattr(a, action)(10, *args, create_time=100.0)
    assert not result.ok and "different process" in result.message
    assert not mutated and closed == [123]

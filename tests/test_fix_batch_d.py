"""Whole-app review batch D: WU cache guard, App Buster identity and
deprovision read-back, firewall export/import, hosts conflicts, persistence."""
import os
import types

import pytest

from modules.app_buster.engine import actions as act
from modules.cleanup.cleanup_scanner import scanners_system as ss
from modules.firewall_rules import firewall_manager_module as fw
from modules.hosts_editor import hosts_file as hf


# ---- Windows Update cache: nothing under SoftwareDistribution unless stopped ----

class _Service:
    def __init__(self, confirmed, restart_error=""):
        self.confirmed, self.restart_error = confirmed, restart_error

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _items(tmp_path, monkeypatch):
    root = tmp_path / "Windows"
    wu = root / "SoftwareDistribution" / "Download"
    wu.mkdir(parents=True)
    (wu / "a.cab").write_bytes(b"x" * 10)
    other = tmp_path / "Temp"
    other.mkdir()
    (other / "b.tmp").write_bytes(b"y" * 10)
    monkeypatch.setenv("SystemRoot", str(root))
    return [ss.ScanItem(path=str(wu), size=10, is_dir=True, selected=True, safety="safe"),
            ss.ScanItem(path=str(other), size=10, is_dir=True, selected=True, safety="safe")]


def _quiet(monkeypatch):
    import modules.cleanup.cleanup_scanner.exclusions as ex
    monkeypatch.setattr(ex, "list_exclusions", lambda: [])
    monkeypatch.setattr(ss, "protected_app_dirs", lambda: [])
    monkeypatch.setattr(ss, "_refusal_reason", lambda item: None)
    import modules.cleanup.cleanup_history as ch
    monkeypatch.setattr(ch, "record_report", lambda *a, **k: None)


def test_the_wu_cache_is_left_alone_when_the_service_did_not_stop(tmp_path, monkeypatch):
    items = _items(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    monkeypatch.setattr(ss, "_ServiceStopped", lambda name: _Service(confirmed=False))
    _report, refused, per_item = ss.delete_items_report(items, stop_wuauserv=True)
    assert [p for p, _r in refused] == [items[0].path]
    assert os.path.exists(os.path.join(items[0].path, "a.cab"))
    assert [p for p, _r in per_item] == [items[1].path]


def test_a_failed_restart_is_reported(tmp_path, monkeypatch):
    items = _items(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    monkeypatch.setattr(ss, "_ServiceStopped",
                        lambda name: _Service(confirmed=True, restart_error="1053"))
    _report, refused, _per_item = ss.delete_items_report(items, stop_wuauserv=True)
    assert any("could not be started again" in reason for _p, reason in refused)


# ---- App Buster ----------------------------------------------------------------

def test_a_reused_pid_is_not_ended(monkeypatch):
    ended = []

    class _Proc:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return 2000.0

        def name(self):
            return "other.exe"

        def terminate(self):
            ended.append(self.pid)

    import psutil
    monkeypatch.setattr(psutil, "Process", _Proc)
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout: (procs, []))
    failed = act.close_programs([act.Blocker(42, "app.exe", 1000.0)], lambda line: None)
    assert ended == [] and "not ended" in failed[0]


def test_a_blocker_still_unpacks_as_pid_and_name():
    pid, name = act.Blocker(7, "x.exe", 1.0)
    assert (pid, name) == (7, "x.exe")


class _Runner(act.Runner):
    def __init__(self, provisioned):
        self.calls = 0
        self._prov = provisioned

    def installed_families(self):
        self.calls += 1
        return {"other_x"} if self.calls > 1 else {"acme.app_123"}

    def all_user_families(self):
        return {"other_x"}

    def provisioned_names(self):
        return self._prov

    def powershell(self, script, timeout=180):
        return 0, "", ""


def _rec():
    return types.SimpleNamespace(removable=True, full_name="Acme.App_1.0_x64__123",
                                 family="Acme.App_123", package_name="Acme.App",
                                 install_location="")


def test_still_provisioned_is_not_removed():
    o = act.remove_windows_app(_rec(), act.SCOPE_ALL, _Runner({"acme.app"}), lambda line: None)
    assert o.state == act.SKIPPED and "provisioned" in o.reason


def test_an_unreadable_provisioned_list_is_unconfirmed():
    o = act.remove_windows_app(_rec(), act.SCOPE_ALL, _Runner(None), lambda line: None)
    assert o.state == act.SKIPPED and "unconfirmed" in o.reason


def test_deprovisioned_is_removed():
    o = act.remove_windows_app(_rec(), act.SCOPE_ALL, _Runner(set()), lambda line: None)
    assert o.state == act.REMOVED


# ---- firewall -------------------------------------------------------------------

def test_an_export_needs_a_real_file(tmp_path):
    with pytest.raises(RuntimeError):
        fw._require_policy_file(str(tmp_path / "missing.wfw"))
    empty = tmp_path / "empty.wfw"
    empty.write_bytes(b"")
    with pytest.raises(RuntimeError):
        fw._require_policy_file(str(empty))
    good = tmp_path / "good.wfw"
    good.write_bytes(b"policy")
    fw._require_policy_file(str(good))


def test_the_backup_goes_to_the_app_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = fw._policy_backup_path()
    assert path.startswith(str(tmp_path / "WindowsTweaker" / "firewall-backups"))
    assert path.endswith(".wfw")


# ---- hosts ----------------------------------------------------------------------

def test_a_hosts_file_changed_since_loading_is_not_overwritten(tmp_path):
    path = tmp_path / "hosts"
    path.write_bytes(b"127.0.0.1 a\r\n")
    read = hf.read_hosts_text(str(path))
    path.write_bytes(b"127.0.0.1 a\r\n0.0.0.0 added-by-vpn\r\n")
    out = hf.save_hosts(str(path), "127.0.0.1 b\r\n", read, str(tmp_path / "bk"))
    assert not out.ok and "another program" in out.error
    assert b"added-by-vpn" in path.read_bytes()


def test_an_unchanged_hosts_file_saves(tmp_path):
    path = tmp_path / "hosts"
    path.write_bytes(b"127.0.0.1 a\r\n")
    read = hf.read_hosts_text(str(path))
    out = hf.save_hosts(str(path), "127.0.0.1 b\r\n", read, str(tmp_path / "bk"))
    assert out.ok and path.read_bytes() == b"127.0.0.1 b\r\n"


def test_a_hosts_file_created_since_loading_is_a_conflict(tmp_path):
    path = tmp_path / "hosts"
    read = hf.read_hosts_text(str(path))
    path.write_bytes(b"0.0.0.0 x\r\n")
    assert hf.changed_since_read(str(path), read)

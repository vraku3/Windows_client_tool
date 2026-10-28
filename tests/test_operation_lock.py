import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from types import SimpleNamespace as NS

import pytest

from core import operation_lock as operations


@pytest.fixture(autouse=True)
def isolated_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(operations, "MUTEX_NAME", "Local\\WCT_test_" + uuid.uuid4().hex)
    yield
    assert not operations.lock.held


def sidecar():
    return Path(os.environ["APPDATA"]) / "WindowsTweaker" / "operation_lock.json"


def test_instances_cannot_reenter_and_owner_is_available():
    first, second = operations.OperationLock(), operations.OperationLock()
    assert first.try_acquire("Updates install")
    try:
        assert first.held and first.owner == "Updates install"
        assert not second.try_acquire("Game Mode ON")
        assert operations.current_owner() == "Updates install"
        assert json.loads(sidecar().read_text())["owner"] == "Updates install"
    finally:
        first.release()
    assert not first.held and first.owner is None
    assert not sidecar().exists()
    assert second.try_acquire("Game Mode ON")
    second.release()


def test_cross_thread_release_is_refused_and_contender_waits():
    first = operations.OperationLock()
    assert first.try_acquire("first")
    outcomes = []

    def contender():
        with pytest.raises(RuntimeError, match="thread"):
            first.release()
        other = operations.OperationLock()
        outcomes.append(other.try_acquire("second", timeout_ms=20))

    thread = threading.Thread(target=contender)
    thread.start()
    thread.join(2)
    try:
        assert not thread.is_alive()
        assert outcomes == [False]
        assert first.held
    finally:
        first.release()


def test_context_releases_on_exception_and_reports_busy_owner():
    with pytest.raises(ValueError):
        with operations.operation_lock("Optimise OS apply") as lock:
            assert lock.held and lock.owned_by_current_thread
            with pytest.raises(operations.OperationBusy) as busy:
                with operations.operation_lock("Updates install", timeout_ms=0):
                    pytest.fail("busy lock entered")
            assert busy.value.owner == "Optimise OS apply"
            raise ValueError("failed operation")
    with operations.operation_lock("next"):
        assert operations.lock.held


def test_stale_or_unwritable_sidecar_does_not_control_lock(monkeypatch):
    sidecar().parent.mkdir(parents=True)
    sidecar().write_text('{"owner":"crashed holder"}')
    with operations.operation_lock("new holder"):
        assert json.loads(sidecar().read_text())["owner"] == "new holder"
    monkeypatch.setattr(operations.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("refused")))
    with operations.operation_lock("no metadata"):
        assert operations.lock.held
    assert not list(sidecar().parent.glob("*.tmp"))


def test_native_process_contention_and_crash_recovery(caplog):
    code = """
import os, sys
from core import operation_lock as op
op.MUTEX_NAME = sys.argv[1]
assert op.lock.try_acquire('child maintenance')
print('ready', flush=True)
sys.stdin.readline()
os._exit(0)
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    child = subprocess.Popen([sys.executable, "-u", "-c", code, operations.MUTEX_NAME],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, env=env)
    try:
        assert child.stdout.readline().strip() == "ready"
        # Keep an open handle in this process so the abandoned mutex survives child exit.
        handle = operations._kernel32.CreateMutexW(None, False, operations.MUTEX_NAME)
        assert handle
        try:
            assert not operations.lock.try_acquire("parent")
            assert operations.current_owner() == "child maintenance"
            child.communicate("exit\n", timeout=5)
            assert child.returncode == 0
            with operations.operation_lock("recovered"):
                assert operations.lock.owner == "recovered"
                assert json.loads(sidecar().read_text())["owner"] == "recovered"
            assert "Previous operation holder exited" in caplog.text
        finally:
            operations._kernel32.CloseHandle(handle)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


@pytest.mark.parametrize("name", ["turn_on", "turn_off", "enforce"])
def test_game_mode_operation_is_guarded(monkeypatch, tmp_path, name):
    from modules.game_mode.engine import session
    from modules.game_mode.engine.journal import Journal
    journal = Journal(str(tmp_path / "game"))
    journal.begin()
    if name == "turn_on":
        journal.finish([])
    called = []
    result = {} if name == "turn_on" else [] if name == "turn_off" else 7

    def implementation(*args):
        assert operations.lock.owned_by_current_thread
        assert operations.lock.owner == {"turn_on": "Game Mode ON", "turn_off": "Game Mode OFF",
                                         "enforce": "Game Mode enforce"}[name]
        called.append(True)
        return result

    monkeypatch.setattr(session, "_" + name, implementation)
    args = (None, None, journal, lambda *a: None, 1, 1) if name == "turn_on" else (None, journal, lambda *a: None)
    assert getattr(session, name)(*args) == result
    with operations.operation_lock("Updates install"):
        if name == "enforce":
            assert session.enforce(*args) == 0
        else:
            with pytest.raises(operations.OperationBusy, match="Updates install"):
                getattr(session, name)(*args)
    assert called == [True]


@pytest.mark.parametrize("name", ["apply", "revert"])
def test_optimise_refuses_busy_lock_without_writes(tmp_path, name):
    from modules.game_mode.engine.optimise import Optimiser
    from test_optimise_os import FakeSystem
    system = FakeSystem()
    engine = Optimiser(system, str(tmp_path / "optimise"))
    with operations.operation_lock("Unattended maintenance"):
        with pytest.raises(operations.OperationBusy, match="Unattended maintenance"):
            getattr(engine, name)(*([{"menu_delay"}] if name == "apply" else []))
    assert not system.writes


@pytest.mark.parametrize("corrupt", [False, True])
def test_maintenance_refuses_persistent_game_mode_even_without_mutex(tmp_path, corrupt):
    from modules.game_mode.engine.journal import Journal
    from modules.updates.operation_guard import maintenance_operation
    journal = Journal()
    journal.begin()
    if corrupt:
        Path(journal.path).write_text("corrupt")
    with pytest.raises(operations.OperationBusy, match="Game Mode is active"):
        with maintenance_operation("Updates install"):
            pytest.fail("Game Mode active")
    assert not operations.lock.held


def test_stage_reuses_only_current_thread_lock(monkeypatch):
    from modules.updates import stage_runners as stages
    from core import mdm_store_trigger
    called = []
    def trigger(**kwargs):
        assert operations.lock.owned_by_current_thread
        called.append(True)
        return 2
    monkeypatch.setattr(mdm_store_trigger, "trigger_store_scan", trigger)
    assert stages.run_store_stage(None, lambda _: None, lambda: False) == {"triggered": 2}
    with operations.operation_lock("Unattended maintenance"):
        assert stages.run_store_stage(None, lambda _: None, lambda: False) == {"triggered": 2}
        errors = []
        def other_thread():
            try:
                stages.run_store_stage(None, lambda _: None, lambda: False)
            except operations.OperationBusy as exc:
                errors.append(exc.owner)
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
        assert errors == ["Unattended maintenance"]
    assert len(called) == 2


@pytest.mark.parametrize("game_mode", [False, True])
def test_unattended_skips_without_creating_app_when_busy(monkeypatch, game_mode):
    from app import App
    from modules.updates.unattended_runner import run_unattended
    from modules.game_mode.engine.journal import Journal
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: True)
    monkeypatch.setattr(App, "instance", None)
    monkeypatch.setattr(App, "__init__", lambda *a: pytest.fail("App created on skipped run"))
    if game_mode:
        Journal().begin()
        assert run_unattended(["wu"]) == 0
    else:
        with operations.operation_lock("Optimise OS apply"):
            assert run_unattended(["wu"]) == 0


@pytest.mark.parametrize("kind", ["apps", "windows", "store", "run_all"])
def test_gui_worker_refuses_game_mode_before_mutations(monkeypatch, kind):
    from modules.updates import updates_module as ui, store_updates_tab, run_all_tab
    from modules.game_mode.engine.journal import Journal
    from modules.updates.windows_updater import WindowsUpdate
    from modules.updates import stage_runners
    Journal().begin()
    queued = []
    monkeypatch.setattr(ui.QThreadPool, "globalInstance", lambda: NS(start=queued.append))
    monkeypatch.setattr("core.disk_space.check_wu_preflight", lambda *a: None)
    def forbidden(*args, **kwargs):
        raise AssertionError("Mutation attempted while Game Mode is active")
    monkeypatch.setattr(ui, "install_update", forbidden)
    monkeypatch.setattr(ui, "install_updates_iter", forbidden)
    monkeypatch.setattr("core.system_restore.create_restore_point", forbidden)
    monkeypatch.setattr("core.mdm_store_trigger.trigger_store_scan", forbidden)
    for stage in stage_runners.STAGE_RUNNERS:
        monkeypatch.setitem(stage_runners.STAGE_RUNNERS, stage, forbidden)
    monkeypatch.setattr(stage_runners, "finalize_run", forbidden)
    if kind == "apps":
        tab = ui._AppUpdatesTab()
        start = lambda: tab._run_updates(["Fake.App"])
    elif kind == "windows":
        tab = ui._WinUpdatesTab()
        tab._updates = [WindowsUpdate("KB1", "T", "c", 1, "d")]
        tab._render_table()
        tab._table.selectRow(0)
        start = tab._do_install
    elif kind == "store":
        tab = store_updates_tab._StoreUpdatesTab()
        start = tab._do_trigger
    else:
        tab = run_all_tab._RunAllTab()
        start = tab._do_run
    tab.app = NS(config=NS(get=lambda key, default=None: default))
    try:
        start()
        assert len(queued) == 1
        errors = []
        queued[0].signals.error.connect(errors.append)
        queued[0].run()
        assert errors and "Game Mode is active. Turn it off before installing updates." in errors[0]
        assert not operations.lock.held
    finally:
        tab.deleteLater()


@pytest.mark.parametrize("failure", ["create", "wait", "timeout"])
def test_failed_native_acquisition_closes_handle_and_releases_gate(monkeypatch, failure):
    closed = []
    native = operations._kernel32
    fake = NS(CreateMutexW=lambda *a: None if failure == "create" else 123,
              WaitForSingleObject=lambda *a: operations._WAIT_TIMEOUT if failure == "timeout" else 0xFFFFFFFF,
              CloseHandle=lambda handle: closed.append(handle) or True)
    monkeypatch.setattr(operations, "_kernel32", fake)
    if failure == "timeout":
        assert not operations.lock.try_acquire("refused")
    else:
        with pytest.raises(OSError):
            operations.lock.try_acquire("failed")
    assert closed == ([] if failure == "create" else [123])
    assert not operations.lock.held and not sidecar().exists()
    monkeypatch.setattr(operations, "_kernel32", native)
    with operations.operation_lock("next"):
        assert operations.lock.held


@pytest.mark.parametrize("cancel", [False, True])
def test_maintenance_worker_releases_before_error_or_cancel_signal(cancel):
    from core.worker import Worker
    from modules.updates.operation_guard import maintenance_worker
    signals = []

    @maintenance_worker("Updates install")
    def work(worker):
        assert operations.lock.owned_by_current_thread
        if cancel:
            worker.cancel()
        else:
            raise ValueError("failed install")

    def completed(*args):
        assert not operations.lock.held
        signals.append(True)

    worker = Worker(work)
    worker.signals.cancelled.connect(completed)
    worker.signals.error.connect(completed)
    worker.run()
    assert signals == [True]


@pytest.mark.parametrize("name", ["turn_on", "turn_off", "enforce"])
def test_game_mode_reloads_journal_after_another_process_changes_it(tmp_path, monkeypatch, name):
    from modules.game_mode.engine import session
    from modules.game_mode.engine.journal import Journal
    journal = Journal(str(tmp_path / "game"))
    other_process = Journal(journal.folder)
    if name == "turn_on":
        other_process.begin()
        with pytest.raises(RuntimeError, match="already ON"):
            session.turn_on(None, None, journal, lambda *a: None, 1, 1)
    else:
        journal.begin()
        other_process.load()
        other_process.finish([])
        monkeypatch.setattr(session, "_" + name, lambda *a: pytest.fail("stale journal used"))
        result = getattr(session, name)(None, journal, lambda *a: None)
        assert result == (0 if name == "enforce" else [])


def test_active_owner_believes_only_a_live_holder():
    sidecar().parent.mkdir(parents=True, exist_ok=True)
    sidecar().write_text(json.dumps({"owner": "Game Mode ON", "pid": 424242}))
    assert operations.active_owner() is None       # no such process here
    assert operations.active_owner() != "Game Mode ON"
    import psutil
    sidecar().write_text(json.dumps({"owner": "Game Mode ON", "pid": os.getpid()}))
    assert operations.active_owner() is None       # this process holds nothing: stale
    assert psutil.pid_exists(os.getpid())


def test_active_owner_reports_the_in_process_holder(monkeypatch):
    sidecar().parent.mkdir(parents=True, exist_ok=True)
    with operations.operation_lock("Updates install", timeout_ms=0):
        assert operations.active_owner() == "Updates install"
    assert operations.active_owner() is None

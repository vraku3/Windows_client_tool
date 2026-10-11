"""The wuauserv guard around the Windows Update download cache.

`delete_items(stop_wuauserv=True)` exists because those files are open while
the service runs. pywin32's StopService only ASKS -- it fires
ControlService(SERVICE_CONTROL_STOP) and returns the status it saw, normally
STOP_PENDING -- so without a wait the deletions start against a service that
is still letting go of the files, which is the exact race the guard is for.
"""
import logging

import pytest

from modules.cleanup.cleanup_scanner import scanners_system as ss
from modules.cleanup.cleanup_scanner._common import ScanItem


@pytest.fixture
def fake_service(monkeypatch):
    import win32service
    import win32serviceutil

    calls = []
    state = {"status": win32service.SERVICE_RUNNING}

    def stop(name, machine=None):
        calls.append(("stop", name))
        state["status"] = win32service.SERVICE_STOP_PENDING

    def wait(name, status, secs, machine=None):
        calls.append(("wait", status))
        state["status"] = status

    def start(name, machine=None):
        calls.append(("start", name))
        state["status"] = win32service.SERVICE_RUNNING

    monkeypatch.setattr(win32serviceutil, "StopService", stop)
    monkeypatch.setattr(win32serviceutil, "WaitForServiceStatus", wait)
    monkeypatch.setattr(win32serviceutil, "StartService", start)
    return calls, state


def _one_file(tmp_path, calls):
    f = tmp_path / "wu_cache_file.bin"
    f.write_bytes(b"x" * 16)
    real_remove = ss.os.remove

    def remove(path):
        # The deleter hands Windows the long-path (extended prefix) form; compare the path itself.
        calls.append(("delete", str(path).removeprefix("\\\\?\\")))
        real_remove(path)

    return f, remove


def test_the_stop_is_waited_out_before_anything_is_deleted(tmp_path, monkeypatch, fake_service):
    import win32service
    calls, _state = fake_service
    f, remove = _one_file(tmp_path, calls)
    monkeypatch.setattr(ss.os, "remove", remove)

    deleted, errors = ss.delete_items(
        [ScanItem(path=str(f), size=16, is_dir=False)], stop_wuauserv=True)

    assert (deleted, errors) == (1, 0)
    assert calls == [
        ("stop", "wuauserv"),
        ("wait", win32service.SERVICE_STOPPED),
        ("delete", str(f)),
        ("start", "wuauserv"),
    ], calls


def test_a_stop_that_never_completes_still_cleans_and_warns(tmp_path, monkeypatch, fake_service, caplog):
    """A service that will not stop is a reason to say so, not a reason to
    abandon the clean: most of what is queued has nothing to do with WU."""
    import win32serviceutil
    calls, _state = fake_service
    f, remove = _one_file(tmp_path, calls)
    monkeypatch.setattr(ss.os, "remove", remove)
    monkeypatch.setattr(win32serviceutil, "WaitForServiceStatus",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("timed out")))

    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        deleted, errors = ss.delete_items(
            [ScanItem(path=str(f), size=16, is_dir=False)], stop_wuauserv=True)

    assert (deleted, errors) == (1, 0)
    assert any("wuauserv" in r.getMessage() for r in caplog.records)
    assert ("start", "wuauserv") in calls


def test_already_running_on_restart_is_not_a_warning(tmp_path, monkeypatch, fake_service, caplog):
    """wuauserv is trigger-started: anything touching WU brings it back by
    itself, so StartService answers 1056. That is the outcome we wanted."""
    import pywintypes
    import win32serviceutil
    calls, _state = fake_service
    f, remove = _one_file(tmp_path, calls)
    monkeypatch.setattr(ss.os, "remove", remove)

    def already_running(name, machine=None):
        raise pywintypes.error(1056, "StartService",
                               "An instance of the service is already running.")

    monkeypatch.setattr(win32serviceutil, "StartService", already_running)

    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        ss.delete_items([ScanItem(path=str(f), size=16, is_dir=False)], stop_wuauserv=True)

    assert caplog.records == [], [r.getMessage() for r in caplog.records]

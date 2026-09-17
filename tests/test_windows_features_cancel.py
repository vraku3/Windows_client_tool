"""`Worker.cancel()` only flips a flag -- it never touched the DISM
subprocess `_enable_feature`/`_disable_feature` spawn, so cancelling the
worker (app closing while DISM holds the servicing lock, a real,
independently-measured 25s+ contention elsewhere in this codebase) left
DISM running to completion regardless, with no way to stop it.

`_run_dism_action` now polls `is_cancelled` on a side thread and kills the
process tree if it fires -- these tests pin that it actually happens, and
that the normal (never-cancelled) path is unaffected.
"""
import subprocess
import time

from modules.windows_features import features_module


class _FakeProc:
    def __init__(self, pid, lines, line_delay=0.0):
        self.pid = pid
        self._lines = lines
        self._line_delay = line_delay
        self.returncode = 0
        self._waited = False

    @property
    def stdout(self):
        for line in self._lines:
            if self._line_delay:
                time.sleep(self._line_delay)
            yield line

    def wait(self):
        self._waited = True


def test_a_normal_run_never_calls_taskkill(monkeypatch):
    fake = _FakeProc(pid=4242, lines=["Line 1\n", "Line 2\n"])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
    taskkill_calls = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: taskkill_calls.append(a) or subprocess.CompletedProcess(a, 0),
    )

    output = []
    rc = features_module._run_dism_action(["dism", "/online"], output.append, lambda: False)

    assert rc == 0
    assert output == ["Line 1", "Line 2"]
    assert taskkill_calls == []


def test_cancelling_mid_run_kills_the_process_tree(monkeypatch):
    fake = _FakeProc(pid=9999, lines=["Line 1\n"] * 10, line_delay=0.1)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: fake)
    taskkill_calls = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: taskkill_calls.append(a[0]) or subprocess.CompletedProcess(a, 0),
    )

    cancelled = {"flag": False}

    def _is_cancelled():
        return cancelled["flag"]

    def _cancel_soon():
        time.sleep(0.2)
        cancelled["flag"] = True

    import threading
    canceller = threading.Thread(target=_cancel_soon)
    canceller.start()

    features_module._run_dism_action(["dism", "/online"], lambda _l: None, _is_cancelled)
    canceller.join()

    assert len(taskkill_calls) == 1
    assert "taskkill" in taskkill_calls[0]
    assert "9999" in taskkill_calls[0]

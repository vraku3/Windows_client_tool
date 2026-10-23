"""core.worker.retire: a cancelled worker stays alive until its `finished`
is delivered, so a queued signal never reaches a freed closure."""
import threading
import time

from PyQt6.QtCore import QThreadPool

from core import worker as wk


def _pump(qapp, until, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not until():
        qapp.processEvents()
        time.sleep(0.01)


def test_a_running_worker_is_held_until_finished_is_delivered(qapp, monkeypatch):
    monkeypatch.setattr(wk, "RELEASE_AFTER_MS", 50)
    release = threading.Event()
    w = wk.Worker(lambda _w: release.wait(5))
    seen = []
    w.signals.cancelled.connect(lambda: seen.append("cancelled"))
    w.signals.finished.connect(lambda: seen.append("finished"))
    workers = [w]
    QThreadPool.globalInstance().start(w)
    wk.retire(workers)
    assert workers == [] and w in wk._RETIRING and w.is_cancelled
    release.set()
    _pump(qapp, lambda: "finished" in seen)
    assert seen == ["cancelled", "finished"]
    assert w in wk._RETIRING            # still held for the release delay
    _pump(qapp, lambda: w not in wk._RETIRING)
    assert w not in wk._RETIRING


def test_a_finished_worker_is_released_after_the_delay(qapp, monkeypatch):
    monkeypatch.setattr(wk, "RELEASE_AFTER_MS", 50)
    w = wk.Worker(lambda _w: 1)
    QThreadPool.globalInstance().start(w)
    QThreadPool.globalInstance().waitForDone(5000)
    assert w.finished_emitted
    wk.retire([w])
    _pump(qapp, lambda: w not in wk._RETIRING)
    assert w not in wk._RETIRING


def test_retiring_several_workers_releases_each_its_own(qapp, monkeypatch):
    monkeypatch.setattr(wk, "RELEASE_AFTER_MS", 50)
    gate_a, gate_b = threading.Event(), threading.Event()
    running = threading.Semaphore(0)

    def body(gate):
        running.release()
        gate.wait(5)

    a = wk.Worker(lambda _w: body(gate_a))
    b = wk.Worker(lambda _w: body(gate_b))
    pool = QThreadPool.globalInstance()
    pool.start(a)
    pool.start(b)
    # Both must be RUNNING: a cancelled worker that has not started ends at once.
    assert running.acquire(timeout=5) and running.acquire(timeout=5)
    wk.retire([a, b])
    gate_a.set()
    _pump(qapp, lambda: a not in wk._RETIRING)
    assert a not in wk._RETIRING
    assert b in wk._RETIRING            # still running: must stay held
    gate_b.set()
    _pump(qapp, lambda: b not in wk._RETIRING)
    assert b not in wk._RETIRING


def test_an_unretired_worker_gets_no_extra_connection():
    w = wk.Worker(lambda _w: 1)
    assert w.signals.receivers(w.signals.finished) == 0


def test_cleanup_widgets_retire_rather_than_drop_their_workers():
    import inspect
    from modules.cleanup.components import drive_summary_bar, quick_cleanup_tab, recommendations_panel
    from modules.cleanup.tabs import _scan_tab
    for module in (drive_summary_bar, quick_cleanup_tab, recommendations_panel, _scan_tab):
        source = inspect.getsource(module)
        assert "retire(" in source, module.__name__


def test_track_holds_a_worker_whose_owner_is_gone_until_finished(qapp, monkeypatch):
    monkeypatch.setattr(wk, "RELEASE_AFTER_MS", 50)
    gate = threading.Event()
    w = wk.Worker(lambda _w: gate.wait(5))
    wk.track(w)
    QThreadPool.globalInstance().start(w)
    owner = [w]
    del owner                                  # the owner's list is gone
    assert w in wk._RETIRING                   # but the worker is still held
    gate.set()
    _pump(qapp, lambda: w not in wk._RETIRING)
    assert w not in wk._RETIRING


def test_no_module_connects_destroyed_to_a_python_lambda():
    import pathlib, re
    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    hits = [f"{p.relative_to(src)}:{n}" for p in src.rglob("*.py")
            for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
            if re.search(r"\.destroyed\.connect\(\s*lambda", line)]
    assert hits == [], "use core.worker.track() instead: " + ", ".join(hits)

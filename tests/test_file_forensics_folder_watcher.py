import threading
import time

import pytest

from modules.file_forensics.engine.folder_watcher import FolderWatcher


class _FakeWorker:
    is_cancelled = False


def test_detects_a_real_file_created_in_the_watched_folder(tmp_path):
    detected = []
    watcher = FolderWatcher(str(tmp_path), on_created=detected.append)

    def _create_soon():
        time.sleep(0.3)
        (tmp_path / "new_file.txt").write_text("hello")

    creator_thread = threading.Thread(target=_create_soon)
    run_thread = threading.Thread(target=watcher.run, args=(_FakeWorker(),))

    creator_thread.start()
    run_thread.start()
    creator_thread.join(timeout=5)
    time.sleep(0.5)  # let the watcher's event fire and callback run
    watcher.stop()
    run_thread.join(timeout=5)

    assert not run_thread.is_alive(), "watcher.run() did not exit after stop()"
    assert any("new_file.txt" in path for path in detected)


def test_stop_before_any_event_returns_promptly(tmp_path):
    watcher = FolderWatcher(str(tmp_path), on_created=lambda p: None)
    run_thread = threading.Thread(target=watcher.run, args=(_FakeWorker(),))
    run_thread.start()
    time.sleep(0.2)  # let it reach the wait

    started = time.monotonic()
    watcher.stop()
    run_thread.join(timeout=5)
    elapsed = time.monotonic() - started

    assert not run_thread.is_alive()
    assert elapsed < 2.0, f"stop() took {elapsed:.1f}s to take effect"


def test_a_nonexistent_folder_raises_immediately():
    watcher = FolderWatcher(r"Z:\this\does\not\exist\at\all", on_created=lambda p: None)
    # Specifically FileNotFoundError, not just any OSError: OSError's
    # constructor treats its first positional as a POSIX errno, and a Win32
    # winerror passed there instead lands CPython on the wrong concrete
    # subclass (winerror=3 collides with POSIX ESRCH -> ProcessLookupError)
    # with .winerror silently dropped to None. A bare `pytest.raises(OSError)`
    # would not catch that regression since ProcessLookupError is still an
    # OSError subclass.
    with pytest.raises(FileNotFoundError) as exc_info:
        watcher.run(_FakeWorker())
    assert exc_info.value.winerror is not None

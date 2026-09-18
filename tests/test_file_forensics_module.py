import time

import pytest
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication


class _FakeApp:
    config = None
    thread_pool = None


def _settle(qapp):
    """Drain the real global thread pool and pump Qt events so a Worker's
    queued `result`/`error` signal actually lands on the UI thread before a
    test asserts on its effects -- search is a Worker now (Task 11), not a
    synchronous call. Same pattern as test_power_boot_module.py's `_settle`."""
    QThreadPool.globalInstance().waitForDone(10_000)
    deadline = time.time() + 2
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


@pytest.fixture
def module(qapp):
    from modules.file_forensics.file_forensics_module import FileForensicsModule
    m = FileForensicsModule()
    m.on_start(_FakeApp())
    m.create_widget()
    return m


def test_module_declares_itself_correctly(module):
    assert module.name == "File Forensics"
    assert module.requires_admin is False


def test_create_widget_builds_without_raising(module):
    assert module._widget is not None


def test_search_with_no_folder_shows_the_empty_state(module, monkeypatch, qapp):
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [])
    module._folder_edit.setText(r"C:\some\folder")
    module._on_search_clicked()
    _settle(qapp)
    assert module._results_stack.currentIndex() == 1  # empty page


def test_a_result_populates_the_table_and_shows_content_page(module, monkeypatch, qapp):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    fake_analysis = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\found.txt", size=10,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="TESTUSER", read_only=False,
        ),
        locking_processes=[], locking_summary="ok",
        creator_candidates=[], top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [fake_analysis])
    module._folder_edit.setText(r"C:\some\folder")
    module._on_search_clicked()
    _settle(qapp)

    assert module._table.rowCount() == 1
    assert module._results_stack.currentIndex() == 0  # content page


def test_a_nonexistent_folder_shows_the_error_banner(module, monkeypatch, qapp):
    def _raise(*a, **k):
        raise FileNotFoundError("no such folder")
    monkeypatch.setattr(module, "_analyze_folder", _raise)
    module._folder_edit.setText(r"C:\does\not\exist")
    module._on_search_clicked()
    _settle(qapp)
    assert not module._error_banner.isHidden()


def test_a_directory_listing_refusal_during_walk_is_not_swallowed(
        module, monkeypatch, tmp_path, qapp):
    """os.walk()'s default onerror=None would otherwise let a scandir()
    refusal on the folder itself (traversable but not listable -- a real
    Windows ACL configuration) return an empty result set silently,
    indistinguishable from "genuinely nothing here". The module must pass
    an onerror that re-raises so this shows up as a real refusal."""
    import os as os_module

    def _fake_walk(folder, onerror=None, **kwargs):
        if onerror is not None:
            onerror(PermissionError(13, "Access is denied", folder))
        return iter([])

    monkeypatch.setattr(os_module, "walk", _fake_walk)
    module._folder_edit.setText(str(tmp_path))
    module._recurse_cb.setChecked(True)
    module._on_search_clicked()
    _settle(qapp)

    assert not module._error_banner.isHidden()
    assert module._table.rowCount() == 0


def test_a_subdirectory_listing_refusal_is_skipped_not_fatal(
        module, monkeypatch, tmp_path, qapp):
    """The distinguishing case from the top-level test above: os.walk's
    onerror fires for EVERY directory it can't list anywhere in a
    recursive tree, not just the target folder -- System Volume
    Information, $RECYCLE.BIN, another user's profile subfolder are all
    ordinary occurrences on a real Windows volume. Those must be skipped
    and counted, not treated as a reason to abort a search that already
    found real results elsewhere in the tree."""
    import os as os_module
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    def _fake_walk(folder, onerror=None, **kwargs):
        if onerror is not None:
            # A DIFFERENT directory than the target folder itself failing
            # to list -- the ordinary mid-walk case, not the rare "the
            # user pointed the tool straight at something unlistable"
            # case the previous test covers.
            onerror(PermissionError(
                13, "Access is denied",
                os_module.path.join(folder, "System Volume Information"),
            ))
        yield (folder, [], ["found.txt"])

    def _fake_analyze(path, vt_api_key=""):
        return FileAnalysis(
            metadata=FileMetadata(
                path=path, size=1,
                created=datetime.datetime(2026, 1, 1),
                modified=datetime.datetime(2026, 1, 1),
                accessed=datetime.datetime(2026, 1, 1),
                owner="TESTUSER", read_only=False,
            ),
            locking_processes=[], locking_summary="ok",
            creator_candidates=[], top_creator_signature=None, reputation=None,
        )

    monkeypatch.setattr(os_module, "walk", _fake_walk)
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.analyze", _fake_analyze
    )
    module._folder_edit.setText(str(tmp_path))
    module._recurse_cb.setChecked(True)
    module._on_search_clicked()
    _settle(qapp)

    assert module._table.rowCount() == 1
    assert module._last_skip_count == 1
    assert not module._error_banner.isHidden()
    assert "1" in module._error_banner.text()
    assert "skipped" in module._error_banner.text().lower()


def test_per_file_analysis_refusals_are_disclosed_not_dropped(
        module, monkeypatch, tmp_path, qapp):
    """A file that exists but can't be analyzed (e.g. ACL-denied) must not
    just vanish from the results with no trace -- see CLAUDE.md's
    tweak_engine "(N step(s) could not be checked)" disclosed-uncertainty
    convention. Results and the disclosure can both be true at once: the
    table still shows what WAS analyzed."""
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    (tmp_path / "good.txt").write_text("hello")
    (tmp_path / "bad.txt").write_text("world")

    def _fake_analyze(path, vt_api_key=""):
        import os as os_module
        if os_module.path.basename(path) == "bad.txt":
            raise PermissionError(13, "Access is denied", path)
        return FileAnalysis(
            metadata=FileMetadata(
                path=path, size=5,
                created=datetime.datetime(2026, 1, 1),
                modified=datetime.datetime(2026, 1, 1),
                accessed=datetime.datetime(2026, 1, 1),
                owner="TESTUSER", read_only=False,
            ),
            locking_processes=[], locking_summary="ok",
            creator_candidates=[], top_creator_signature=None, reputation=None,
        )

    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.analyze", _fake_analyze
    )
    module._folder_edit.setText(str(tmp_path))
    module._recurse_cb.setChecked(False)
    module._on_search_clicked()
    _settle(qapp)

    assert module._table.rowCount() == 1
    assert not module._error_banner.isHidden()
    assert "1" in module._error_banner.text()
    assert "skipped" in module._error_banner.text().lower()


def test_a_locked_file_row_is_highlighted(module, monkeypatch, qapp):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    from modules.file_forensics.engine.locking_processes import LockingProcess
    import datetime

    locked = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\locked.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[LockingProcess(pid=1, process="notepad", type_name="File")],
        locking_summary="ok", creator_candidates=[], top_creator_signature=None,
        reputation=None,
    )
    unlocked = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\free.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[], locking_summary="ok", creator_candidates=[],
        top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [locked, unlocked])
    module._folder_edit.setText(r"C:\x")
    module._on_search_clicked()
    _settle(qapp)

    from modules.file_forensics.file_forensics_module import _LOCKED_COLOR
    locked_item = module._table.item(0, 5)
    unlocked_item = module._table.item(1, 5)
    assert locked_item.background().color() == _LOCKED_COLOR
    assert unlocked_item.background().color() != _LOCKED_COLOR


def test_selecting_a_row_shows_its_detail(module, monkeypatch, qapp):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    from modules.file_forensics.engine.locking_processes import LockingProcess
    import datetime

    locked = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\locked.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[LockingProcess(pid=1, process="notepad", type_name="File")],
        locking_summary="1 matches in 250 processes", creator_candidates=[],
        top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [locked])
    module._folder_edit.setText(r"C:\x")
    module._on_search_clicked()
    _settle(qapp)
    module._table.selectRow(0)
    module._on_row_selected()

    assert "notepad" in module._detail_label.text()
    assert "250 processes" in module._detail_label.text()


def test_kill_locking_process_asks_for_confirmation(module, monkeypatch, qapp):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    from modules.file_forensics.engine.locking_processes import LockingProcess
    import datetime

    locked = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\locked.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[LockingProcess(pid=1, process="notepad", type_name="File")],
        locking_summary="ok", creator_candidates=[], top_creator_signature=None,
        reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [locked])
    module._folder_edit.setText(r"C:\x")
    module._on_search_clicked()
    _settle(qapp)
    module._table.selectRow(0)
    module._on_row_selected()

    from PyQt6.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No)
    killed = []
    monkeypatch.setattr("modules.file_forensics.file_forensics_module.end_process",
                        lambda pid: killed.append(pid))

    module._on_kill_locking_clicked()
    _settle(qapp)

    assert asked  # confirmation was shown
    assert killed == []  # user said No, nothing killed


def test_kill_locking_process_reports_a_failed_kill(module, monkeypatch, qapp):
    """A failed end_process() (protected process, access denied, timeout,
    "no longer running") must never be collapsed into silence -- the same
    "a refusal is never dropped" rule this task's other pieces already
    follow. Regression test for the reviewer's Important #1 finding."""
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    from modules.file_forensics.engine.locking_processes import LockingProcess
    from core.procengine.actions import Result
    import datetime

    locked = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\locked.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[LockingProcess(pid=1, process="notepad", type_name="File")],
        locking_summary="ok", creator_candidates=[], top_creator_signature=None,
        reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [locked])
    module._folder_edit.setText(r"C:\x")
    module._on_search_clicked()
    _settle(qapp)
    module._table.selectRow(0)
    module._on_row_selected()

    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.end_process",
        lambda pid: Result(False, "Notepad cannot be ended."),
    )

    module._on_kill_locking_clicked()
    _settle(qapp)

    # The refresh (_on_search_clicked, now a Worker) must not silently
    # overwrite the kill failure once it lands -- _on_search_result applies
    # the module's stashed _pending_banner_message instead of clearing the
    # banner on this otherwise-clean re-search.
    assert not module._error_banner.isHidden()
    assert "Notepad cannot be ended." in module._error_banner.text()


def test_live_watch_toggle_starts_and_stops_a_watcher(module, monkeypatch):
    started = []
    stopped = []

    class _FakeWatcher:
        def __init__(self, path, on_created, recursive=False):
            started.append(path)
            self.on_created = on_created

        def run(self, worker):
            pass

        def stop(self):
            stopped.append(True)

    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.FolderWatcher", _FakeWatcher)
    module._folder_edit.setText(r"C:\watch\me")
    module._watch_cb.setChecked(True)
    module._on_watch_toggled(True)
    assert started == [r"C:\watch\me"]

    module._watch_cb.setChecked(False)
    module._on_watch_toggled(False)
    assert stopped == [True]


def test_ignore_pattern_skips_analysis(module, monkeypatch):
    """The mocked `analyze` here only needs to prove it was (or was not)
    called for a given path -- unlike the history-recording test below, it
    does not need to return a real FileAnalysis, because `_is_ignored`
    short-circuits before `analyze()` (or anything that reads its return
    value) ever runs for the ignored path."""
    module._ignore_edit.setText("*.tmp")
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.history_log.record",
        lambda entry: None)
    # Notification is a separate concern (covered by its own tests below) and
    # the fixture's _FakeApp has no event_bus -- _maybe_notify would reach
    # for one and crash a test that isn't about toasts at all.
    monkeypatch.setattr(module, "_maybe_notify", lambda *a, **k: None)
    analyzed = []

    def _fake_analyze(path, **k):
        analyzed.append(path)
        from modules.file_forensics.engine.file_metadata import FileMetadata
        from modules.file_forensics.engine.analysis import FileAnalysis
        import datetime
        return FileAnalysis(
            metadata=FileMetadata(
                path=path, size=1,
                created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
                accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
            ),
            locking_processes=[], locking_summary="ok",
            creator_candidates=[], top_creator_signature=None, reputation=None,
        )

    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.analyze", _fake_analyze)

    module._on_watched_file_created(r"C:\watch\noise.tmp")
    assert analyzed == []

    module._on_watched_file_created(r"C:\watch\real.docx")
    assert analyzed == [r"C:\watch\real.docx"]


def test_a_watch_detection_is_recorded_to_history(module, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    fake = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\watch\real.docx", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[], locking_summary="ok",
        creator_candidates=[], top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.analyze", lambda path, **k: fake)
    recorded = []
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.history_log.record",
        lambda entry: recorded.append(entry))
    # Not testing notification here -- see the comment on the ignore-pattern
    # test above for why this must be stubbed against the fixture's _FakeApp.
    monkeypatch.setattr(module, "_maybe_notify", lambda *a, **k: None)

    module._on_watched_file_created(r"C:\watch\real.docx")

    assert len(recorded) == 1
    assert recorded[0]["path"] == r"C:\watch\real.docx"


def test_a_watch_detection_updates_the_table(module, monkeypatch):
    """`_on_watched_file_created` hands its result to `_watch_bridge`
    rather than touching the table directly -- see the module's own
    docstring on why (it runs on the watch Worker's background thread in
    real use). A direct call, as here, is a same-thread emit, so the
    connected slot still runs synchronously and the table still updates."""
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    fake = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\watch\real.docx", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[], locking_summary="ok",
        creator_candidates=[], top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.analyze", lambda path, **k: fake)
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.history_log.record",
        lambda entry: None)
    # Not testing notification here -- see the comment on the ignore-pattern
    # test above for why this must be stubbed against the fixture's _FakeApp.
    monkeypatch.setattr(module, "_maybe_notify", lambda *a, **k: None)

    module._on_watched_file_created(r"C:\watch\real.docx")

    assert module._table.rowCount() == 1
    assert module._results_stack.currentIndex() == 0  # content page


def test_watch_detection_toasts_when_app_is_not_foreground(module, monkeypatch):
    """A live-watch hit raises a desktop toast via NOTIFY_BALLOON only when
    the app isn't the foreground window -- when it is, the table update
    already told the user."""
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    class _FakeEventBus:
        def __init__(self):
            self.published = []

        def publish(self, topic, data):
            self.published.append((topic, data))

    class _FakeAppWithBus:
        config = None
        thread_pool = None
        event_bus = _FakeEventBus()

    fake_app = _FakeAppWithBus()
    module.app = fake_app

    fake = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\watch\real.docx", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[], locking_summary="ok",
        creator_candidates=[], top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.analyze", lambda path, **k: fake)
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.history_log.record",
        lambda entry: None)
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: None))

    from core.events import NOTIFY_BALLOON

    module._on_watched_file_created(r"C:\watch\real.docx")

    assert len(fake_app.event_bus.published) == 1
    topic, data = fake_app.event_bus.published[0]
    assert topic == NOTIFY_BALLOON
    assert "real.docx" in data.message


def test_watch_detection_does_not_toast_when_app_is_foreground(module, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    class _FakeEventBus:
        def __init__(self):
            self.published = []

        def publish(self, topic, data):
            self.published.append((topic, data))

    class _FakeAppWithBus:
        config = None
        thread_pool = None
        event_bus = _FakeEventBus()

    fake_app = _FakeAppWithBus()
    module.app = fake_app

    fake = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\watch\real.docx", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[], locking_summary="ok",
        creator_candidates=[], top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.analyze", lambda path, **k: fake)
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.history_log.record",
        lambda entry: None)
    monkeypatch.setattr(QApplication, "activeWindow", staticmethod(lambda: module._widget))

    module._on_watched_file_created(r"C:\watch\real.docx")

    assert fake_app.event_bus.published == []


def test_ignore_patterns_parses_semicolon_separated_list(module):
    module._ignore_edit.setText(" *.tmp ; ~$* ;;  *.log ")
    assert module._ignore_patterns() == ["*.tmp", "~$*", "*.log"]


def test_watch_worker_failure_shows_banner_and_unchecks_box(module, monkeypatch, qapp):
    """Reviewer's Important #1 (fix round 1): FolderWatcher.run() raising
    (CreateFile failing on an empty/invalid/removed folder, a disconnected
    share, a revoked ACL mid-watch) must not vanish into Worker's own
    swallowed signals.error -- the checkbox must reflect that watching
    actually stopped, and the banner must say so."""
    class _FailingWatcher:
        def __init__(self, path, on_created, recursive=False):
            pass

        def run(self, worker):
            raise OSError(None, "The folder could not be opened", r"C:\bad\folder", 3)

        def stop(self):
            pass

    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.FolderWatcher", _FailingWatcher)
    module._folder_edit.setText(r"C:\bad\folder")
    module._watch_cb.setChecked(True)  # fires _on_watch_toggled(True)
    _settle(qapp)

    assert module._watch_cb.isChecked() is False
    assert not module._error_banner.isHidden()
    assert "Live Watch stopped" in module._error_banner.text()
    assert module._watcher is None
    assert module._watch_worker is None

    # A retry must not be blocked by the start-path idempotency guard now
    # that state has been reset.
    started = []

    class _FakeWatcher:
        def __init__(self, path, on_created, recursive=False):
            started.append(path)

        def run(self, worker):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.FolderWatcher", _FakeWatcher)
    module._watch_cb.setChecked(True)
    assert started == [r"C:\bad\folder"]


def test_deactivate_while_watching_unchecks_the_box(module, monkeypatch, qapp):
    """Reviewer's Important #2 (fix round 1): on_deactivate() already tore
    down the actual watch via _stop_watch() -- this pins that the checkbox,
    the module's own primary control, is kept in sync with that, since
    BaseModule's lifecycle calls on_deactivate() on every navigation away,
    not just app shutdown."""
    stopped = []

    class _FakeWatcher:
        def __init__(self, path, on_created, recursive=False):
            pass

        def run(self, worker):
            pass

        def stop(self):
            stopped.append(True)

    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.FolderWatcher", _FakeWatcher)
    module._folder_edit.setText(r"C:\watch\me")
    module._watch_cb.setChecked(True)

    module.on_deactivate()
    _settle(qapp)

    assert module._watch_cb.isChecked() is False
    assert stopped == [True]
    assert module._watcher is None
    assert module._watch_worker is None


def test_history_tab_shows_recorded_entries(module, monkeypatch):
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.history_log.recent",
        lambda limit=50: [{"path": r"C:\a.txt", "creator": "x.exe", "at": "2026-01-01T00:00:00"}])
    module._refresh_history()
    assert module._history_table.rowCount() == 1


def test_history_search_filters(module, monkeypatch):
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.history_log.search",
        lambda query, limit=50: [{"path": r"C:\found.txt", "creator": "y.exe", "at": "2026-01-01T00:00:00"}])
    module._history_search_edit.setText("found")
    module._on_history_search()
    assert module._history_table.rowCount() == 1
    assert module._history_table.item(0, 0).text() == r"C:\found.txt"


def test_export_writes_a_csv(module, tmp_path, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    fake = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\a.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="me", read_only=False,
        ),
        locking_processes=[], locking_summary="ok",
        creator_candidates=[], top_creator_signature=None, reputation=None,
    )
    module._current_results = [fake]
    out_path = str(tmp_path / "export.csv")
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.QFileDialog.getSaveFileName",
        lambda *a, **k: (out_path, "CSV"))

    module._on_export_clicked()

    with open(out_path, encoding="utf-8") as f:
        content = f.read()
    assert r"C:\a.txt" in content


def test_a_live_watch_hit_refreshes_the_history_tab(module, monkeypatch):
    """The brief's illustrative placement (calling _refresh_history() right
    inside _on_watched_file_created) would touch the Qt _history_table from
    the watch worker's background thread -- this module's own "Cross-thread
    widget access" rule forbids that. The refresh is wired into
    _on_watch_detection_ready instead, which _watch_bridge's signal already
    marshals onto the UI thread; a direct call here (as in all the other
    watch tests) is a same-thread emit, so the connected slot still runs
    synchronously and the history table still updates immediately."""
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    fake = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\watch\real.docx", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[], locking_summary="ok",
        creator_candidates=[], top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.analyze", lambda path, **k: fake)
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.history_log.record",
        lambda entry: None)
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.history_log.recent",
        lambda limit=50: [{"path": r"C:\watch\real.docx", "creator": "", "at": "2026-01-01T00:00:00"}])
    monkeypatch.setattr(module, "_maybe_notify", lambda *a, **k: None)

    module._on_watched_file_created(r"C:\watch\real.docx")

    assert module._history_table.rowCount() == 1
    assert module._history_table.item(0, 0).text() == r"C:\watch\real.docx"


def test_kill_failure_survives_a_refresh_that_also_fails(module, monkeypatch, qapp):
    """Reviewer's Important #3 (fix round 1): a pending kill-failure message
    must not be silently discarded if the refresh search it triggers ALSO
    fails -- the same "never collapse a refusal" rule this file already
    applies to skipped files. Both messages must show, not just the last
    one to arrive."""
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    from modules.file_forensics.engine.locking_processes import LockingProcess
    from core.procengine.actions import Result
    import datetime

    locked = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\locked.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[LockingProcess(pid=1, process="notepad", type_name="File")],
        locking_summary="ok", creator_candidates=[], top_creator_signature=None,
        reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [locked])
    module._folder_edit.setText(r"C:\x")
    module._on_search_clicked()
    _settle(qapp)
    module._table.selectRow(0)
    module._on_row_selected()

    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.end_process",
        lambda pid: Result(False, "Notepad cannot be ended."),
    )

    # The re-search _on_kill_locking_clicked triggers must ALSO fail, to
    # exercise the double-refusal path -- not the ordinary "kill fails,
    # refresh succeeds cleanly" case the other kill test already covers.
    def _raise(*a, **k):
        raise PermissionError(13, "Access is denied", r"C:\x")
    monkeypatch.setattr(module, "_analyze_folder", _raise)

    module._on_kill_locking_clicked()
    _settle(qapp)

    text = module._error_banner.text()
    assert not module._error_banner.isHidden()
    assert "Notepad cannot be ended." in text
    assert "Access is denied" in text
    assert module._pending_banner_message is None  # displayed, not left dangling

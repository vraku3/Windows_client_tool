"""Headless regression tests for the October 25 File Forensics upgrades."""
import csv
import io
import logging
import os
import time
from dataclasses import replace
from types import SimpleNamespace

import psutil
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox

from modules.file_forensics.engine.search import SearchFilters, fast_analysis, search_batches
from modules.file_forensics.engine.analysis import LockInspection
from modules.file_forensics.engine.export_csv import export_csv, safe_cell
from modules.file_forensics.engine import process_choices as holders
from modules.file_forensics.process_chooser import ProcessChooser


def test_extensions_are_filtered_before_cap(tmp_path):
    for i in range(12):
        (tmp_path / f'{i}.txt').write_text('noise')
    (tmp_path / 'match.PS1').write_text('payload')
    batches = list(search_batches(str(tmp_path), limit=1, filters=SearchFilters('exe,.ps1')))
    rows = [row for batch in batches for row in batch.rows]
    assert len(rows) == 1 and rows[0].metadata.path.endswith('match.PS1')
    assert batches[-1].matches == 1 and batches[-1].outcome == 'truncated'


def test_metadata_constraints_and_recursive_tree(tmp_path):
    child = tmp_path / 'child'
    child.mkdir()
    small = tmp_path / 'small.dll'
    small.write_text('x')
    old = tmp_path / 'old.dll'
    old.write_text('12345')
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    fresh = child / 'new.DLL'
    fresh.write_text('12345')
    filters = SearchFilters('dll', created_days=1, modified_days=2, min_size=5)
    rows = [r for b in search_batches(str(tmp_path), filters=filters) for r in b.rows]
    assert [r.metadata.path for r in rows] == [str(fresh)]
    assert not [r for b in search_batches(str(tmp_path), recurse=False, filters=filters) for r in b.rows]
    now = time.time()
    fake_stat = SimpleNamespace(st_size=5, st_ctime=now - 2 * 86400, st_mtime=now)
    assert not filters.accepts('a.dll', fake_stat, now)
    fake_stat.st_ctime = now - 86400
    assert filters.accepts('a.dll', fake_stat, now)  # inclusive boundary


@pytest.fixture
def holder_reads(monkeypatch):
    monkeypatch.setattr(holders.psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 123.0))
    monkeypatch.setattr(holders, 'resolve', lambda pid: SimpleNamespace(path=f'C:/app{pid}.exe',
        path_error=None, user='DOMAIN/user', user_error=None, integrity='Medium'))
    monkeypatch.setattr(holders, 'read_mitigations', lambda pid: SimpleNamespace(protection='None', error=None))
    monkeypatch.setattr(holders, '_SIGNATURE_CACHE', {})


def test_all_holders_details_deduplicated_and_cheap_signature(holder_reads, monkeypatch):
    from core.procengine.signatures import SignatureFacts, NOT_SIGNED
    holders._SIGNATURE_CACHE['C:/app11.exe'] = SignatureFacts('C:/app11.exe', NOT_SIGNED)
    choices = holders.resolve_choices([SimpleNamespace(pid=pid, process='app.exe') for pid in (11, 22, 11)])
    assert [c.pid for c in choices] == [11, 22]
    assert choices[0].signature == 'Unsigned' and choices[1].signature == 'Not checked yet'
    assert choices[0].path == 'C:/app11.exe' and choices[0].user == 'DOMAIN/user'
    assert choices[0].created == 123 and choices[0].started != 'Could not check'
    assert not choices[0].reason


@pytest.mark.parametrize('name', ['csrss.exe', 'WININIT', 'services.exe', 'lsass.exe', 'smss.exe', 'System', 'Registry', 'MsMpEng.exe'])
def test_critical_processes_never_offered(holder_reads, name):
    assert 'Critical' in holders.resolve_choices([SimpleNamespace(pid=22, process=name)])[0].reason


def test_protection_and_unreadable_identity_disable_holder(holder_reads, monkeypatch):
    monkeypatch.setattr(holders, 'read_mitigations', lambda pid: SimpleNamespace(protection='Antimalware-Light', error=None))
    assert 'Protected' in holders.resolve_choices([SimpleNamespace(pid=22, process='other.exe')])[0].reason
    monkeypatch.setattr(holders, 'read_mitigations', lambda pid: SimpleNamespace(protection=None, error='Access denied'))
    assert 'Access denied' in holders.resolve_choices([SimpleNamespace(pid=22, process='other.exe')])[0].reason
    monkeypatch.setattr(holders, 'read_mitigations', lambda pid: SimpleNamespace(protection='None', error=None))
    def denied(pid):
        raise psutil.AccessDenied(pid)
    monkeypatch.setattr(holders.psutil, 'Process', denied)
    assert 'could not be identified' in holders.resolve_choices([SimpleNamespace(pid=22, process='other.exe')])[0].reason


def test_pid_changes_while_resolving_are_disabled(holder_reads, monkeypatch):
    times = iter([123.0, 456.0])
    monkeypatch.setattr(holders.psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: next(times)))
    assert 'changed' in holders.resolve_choices([SimpleNamespace(pid=22, process='app.exe')])[0].reason


def test_chooser_defaults_none_and_cancel_and_disables_protected(qapp):
    choices = [holders.ProcessChoice(11, 'one', 123, 'a.exe', 'user', 'today', 'Unsigned'),
               holders.ProcessChoice(22, 'protected', 123, 'b.exe', 'SYSTEM', 'today', 'Signed', 'Protected process')]
    opened = []
    dialog = ProcessChooser(None, 'selected.txt', choices, opened.append)
    assert dialog.selected() is None and not dialog.ok.isEnabled()
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).isDefault()
    assert not dialog.table.item(1, 0).flags() & Qt.ItemFlag.ItemIsEnabled
    assert dialog.table.item(1, 7).text() == 'Protected process'
    dialog.table.selectRow(0)
    assert dialog.selected().pid == 11 and dialog.ok.isEnabled()
    dialog.open_button.click()
    assert opened == [11] and dialog.result() == QDialog.DialogCode.Rejected


@pytest.mark.parametrize('cell', ['=HYPERLINK("bad")', '+cmd', '-1+1', '@SUM(A1)', ' \t=cmd'])
def test_formula_cells_escaped(cell):
    assert safe_cell(cell) == "'" + cell


def test_export_context_and_unknown_states(tmp_path):
    path = tmp_path / 'file.exe'
    path.write_text('data')
    pending = fast_analysis(str(path), path.stat())
    refused = replace(pending, metadata=replace(pending.metadata, path='=evil', owner='@user'),
                      lock_inspection=LockInspection('could_not_check', 'Access denied'),
                      locking_summary='Access denied', creator_ready=True)
    out = tmp_path / 'export.csv'
    context = {'root': str(tmp_path), 'name filter': 'file', 'filters': SearchFilters('exe', 2, 3, 4).summary(),
               'recursion': True, 'start time': 'start', 'end time': 'end', 'outcome': 'stopped',
               'matched': 2, 'skipped links': 3, 'skipped unreadable': 4}
    export_csv(out, [pending, refused], context, {'=evil': {'signatures': ('Could not check: Access denied',)}})
    text = out.read_text(encoding='utf-8')
    for key, value in context.items():
        assert f'# {key}: {value}' in text
    assert '# handle-inspection coverage:' in text
    rows = list(csv.reader(io.StringIO(''.join(line for line in text.splitlines(True) if not line.startswith('# ')))))
    assert rows[1][4] == 'Not checked yet' and rows[1][6] == 'Not checked yet'
    assert rows[2][0] == "'=evil" and rows[2][3] == "'@user"
    assert rows[2][4].startswith('Could not check: Access denied')
    assert rows[2][6].startswith('Could not check')


@pytest.mark.parametrize('overflow', ['zero', 'error', 'submit_error'])
def test_watcher_loss_and_rename_arrival(tmp_path, monkeypatch, caplog, overflow):
    from modules.file_forensics.engine import folder_watcher as fw
    calls, detected, lost, closed = [], [], [], []
    worker = SimpleNamespace(is_cancelled=False)
    monkeypatch.setattr(fw.win32file, 'CreateFile', lambda *a: 'handle')
    def submit(*args):
        calls.append('read')
        if overflow == 'submit_error' and len(calls) == 1:
            raise fw.pywintypes.error(1022, 'ReadDirectoryChangesW', 'overflow')
    monkeypatch.setattr(fw.win32file, 'ReadDirectoryChangesW', submit)
    monkeypatch.setattr(fw.win32event, 'WaitForMultipleObjects', lambda *a: fw.win32event.WAIT_OBJECT_0)
    def result(*args):
        if len(calls) == 1:
            if overflow == 'error':
                raise fw.pywintypes.error(1022, 'GetOverlappedResult', 'overflow')
            return 0
        worker.is_cancelled = True
        return 12
    monkeypatch.setattr(fw.win32file, 'GetOverlappedResult', result)
    monkeypatch.setattr(fw.win32file, 'FILE_NOTIFY_INFORMATION', lambda *a: [(5, 'renamed.txt'), (4, 'old.txt'), (1, 'added.txt')])
    monkeypatch.setattr(fw.win32file, 'CloseHandle', closed.append)
    watcher = fw.FolderWatcher(str(tmp_path), detected.append, on_lost=lost.append)
    with caplog.at_level(logging.WARNING):
        watcher.run(worker)
    assert lost == [fw.EVENTS_LOST] and fw.EVENTS_LOST in caplog.text
    assert detected == [str(tmp_path / 'renamed.txt'), str(tmp_path / 'added.txt')]
    assert closed == ['handle']


@pytest.fixture
def upgrade_pane(qapp, tmp_path, monkeypatch):
    from modules.file_forensics import history_log
    from modules.file_forensics.file_forensics_module import FileForensicsModule
    monkeypatch.setattr(history_log, '_history_path', lambda: str(tmp_path / 'history.json'))
    # Never alter Qt's global pool; these tests execute no background work.
    monkeypatch.setattr(FileForensicsModule, 'thread_pool', property(lambda self: SimpleNamespace(start=lambda w: None)))
    pane = FileForensicsModule()
    pane.on_start(SimpleNamespace(config=None))
    pane.create_widget()
    pane.on_activate()
    yield pane
    pane.on_stop()


def test_navigation_uses_existing_event_and_chosen_pid(upgrade_pane):
    from core.events import NAV_REQUEST_MODULE
    selected, published = [], []
    explorer = SimpleNamespace(name='Process Explorer', _model=SimpleNamespace(_snapshot={22: object()}),
                               _select_pid=selected.append)
    upgrade_pane.app.module_registry = SimpleNamespace(modules=[explorer], disabled_modules=[])
    upgrade_pane.app.event_bus = SimpleNamespace(publish=lambda *args: published.append(args))
    upgrade_pane._open_process(22)
    assert published[0][0] == NAV_REQUEST_MODULE
    assert published[0][1].module_name == 'Process Explorer'
    assert selected == [22]
    upgrade_pane.app.module_registry.disabled_modules = [explorer]
    assert upgrade_pane._process_explorer() is None


def test_end_holder_rechecks_identity_and_keeps_confirmation(upgrade_pane, monkeypatch):
    from modules.file_forensics import file_forensics_module as module
    ended, asked = [], []
    choice = holders.ProcessChoice(22, 'app.exe', 123, 'app.exe', 'user', 'start', 'Unsigned')
    monkeypatch.setattr(psutil, 'Process',
                        lambda pid: SimpleNamespace(create_time=lambda: 456))
    monkeypatch.setattr(module, 'end_process', lambda *a, **k: ended.append((a, k)))
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: asked.append(a) or QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(upgrade_pane, '_on_search_clicked', lambda: None)
    upgrade_pane._confirm_end_holder(choice)
    assert not ended and 'nothing was ended' in upgrade_pane._pending_banner_message
    assert 'Unsaved work may be lost' in asked[0][2] and 'cannot be undone' in asked[0][2]
    assert asked[0][-1] == QMessageBox.StandardButton.No
    monkeypatch.setattr(psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 123))
    monkeypatch.setattr(module, 'end_process', lambda *a, **k: ended.append((a, k)) or SimpleNamespace(ok=True))
    upgrade_pane._confirm_end_holder(choice)
    assert ended == [((22,), {'create_time': 123})]


def test_filter_controls_status_history_and_loss_feed(upgrade_pane, tmp_path, monkeypatch):
    from modules.file_forensics import history_log
    from modules.file_forensics.engine.folder_watcher import EVENTS_LOST
    recorded = []
    monkeypatch.setattr(history_log, 'record', recorded.append)
    pane = upgrade_pane
    pane._folder_edit.setText(str(tmp_path))
    pane._extensions_edit.setText('exe,dll')
    pane._created_days.setValue(2)
    pane._modified_days.setValue(3)
    pane._min_size.setValue(4)
    pane._on_search_clicked()
    assert pane._search_filters == SearchFilters('exe,dll', 2, 3, 4096)
    pane._finish_search('stopped')
    assert pane._search_filters.summary() in pane._progress_label.text()
    assert recorded[-1]['filters'] == pane._search_filters.summary()
    assert recorded[-1]['outcome'] == 'stopped'
    pane._watch_bridge.events_lost.emit(EVENTS_LOST)
    assert pane._watch_feed.item(0).text() == EVENTS_LOST

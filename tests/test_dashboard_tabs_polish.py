from core.procengine.snapshot import SnapshotSource
from core.procengine.usage import app_usage


def test_app_history_never_lists_the_idle_process():
    src = SnapshotSource()
    src.read()
    names = [u.name for u in app_usage(src.read())]
    assert "System Idle Process" not in names


def test_services_table_does_not_wrap_paths(qapp):
    from modules.dashboard.services_tab import ServicesTab
    tab = ServicesTab()
    assert tab._table.wordWrap() is False


def test_a_task_that_never_ran_is_not_dated_1999():
    from modules.startup_manager.startup_reader import last_run_text
    assert last_run_text("1999-11-30 00:00:00") == "Never run"
    assert last_run_text("") == "Never run"
    assert last_run_text("2026-09-23 10:00:00") == "Last: 2026-09-23"


def test_users_tab_never_files_the_idle_process_under_a_user():
    from core.procengine.users import group_by_user
    src = SnapshotSource()
    src.read()
    groups = group_by_user(src.read())
    assert all(i.pid != 0 for g in groups for i in g.rows)

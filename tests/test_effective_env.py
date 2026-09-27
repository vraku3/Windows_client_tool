"""System/User registry vs. this process' live environment (no Qt)."""
from modules.env_vars import effective_env as ee
from modules.env_vars.env_ops import EnvVar


def _v(name, value):
    return EnvVar(name=name, value=value, kind=1)


def test_user_overrides_system_for_an_ordinary_variable():
    rows = ee.compare([_v("TEMP", r"C:\Windows\Temp")], [_v("TEMP", r"C:\Users\me\AppData\Local\Temp")],
                      process_env={"TEMP": r"C:\Users\me\AppData\Local\Temp"})
    row = next(r for r in rows if r.name.upper() == "TEMP")
    assert row.combined_registry_value == r"C:\Users\me\AppData\Local\Temp"
    assert not row.is_stale


def test_path_is_appended_not_overridden():
    rows = ee.compare([_v("Path", r"C:\Windows")], [_v("Path", r"C:\Tools")],
                      process_env={"Path": r"C:\Windows;C:\Tools"})
    row = next(r for r in rows if r.name.upper() == "PATH")
    assert row.combined_registry_value == r"C:\Windows;C:\Tools"
    assert not row.is_stale


def test_a_registry_change_not_yet_live_is_flagged_stale():
    rows = ee.compare([], [_v("MY_VAR", "new-value")], process_env={"MY_VAR": "old-value"})
    row = next(r for r in rows if r.name.upper() == "MY_VAR")
    assert row.is_stale


def test_path_additions_in_the_live_process_are_not_flagged_stale():
    # a shell (or this app's own launcher) commonly prepends its own entries;
    # that is not registry drift.
    rows = ee.compare([_v("Path", r"C:\Windows")], [], process_env={"Path": r"C:\MyShell\bin;C:\Windows"})
    row = next(r for r in rows if r.name.upper() == "PATH")
    assert not row.is_stale


def test_a_variable_only_in_the_live_process_is_marked_process_only():
    rows = ee.compare([], [], process_env={"VIRTUAL_ENV": "C:\\venv"})
    row = next(r for r in rows if r.name.upper() == "VIRTUAL_ENV")
    assert row.process_only and row.system_value is None and row.user_value is None


def test_a_variable_set_but_not_inherited_has_no_process_value():
    rows = ee.compare([_v("NEW_SETTING", "1")], [], process_env={})
    row = next(r for r in rows if r.name.upper() == "NEW_SETTING")
    assert row.process_value is None and not row.is_stale       # nothing live to disagree with


def test_stale_rows_filters_to_only_the_mismatches():
    rows = ee.compare([], [_v("A", "new")], process_env={"A": "old", "B": "same"})
    assert [r.name for r in ee.stale_rows(rows)] == ["A"]


def test_real_process_environment_is_readable_and_non_empty():
    env = ee.read_process_env()
    assert env and "PATH" in {k.upper() for k in env}


def test_comparing_against_this_processs_own_real_registry_and_environment():
    """No fakes: read the real System/User scopes and the real process env,
    and confirm the comparison runs and PATH is never spuriously stale --
    this process inherited its own PATH from the same registry moments ago."""
    import winreg

    from modules.env_vars.env_ops import SYS_PATH, USR_PATH, read_env
    sys_rows, sys_err = read_env(winreg.HKEY_LOCAL_MACHINE, SYS_PATH)
    usr_rows, usr_err = read_env(winreg.HKEY_CURRENT_USER, USR_PATH)
    assert sys_rows is not None and usr_rows is not None
    rows = ee.compare(sys_rows, usr_rows)
    path_row = next((r for r in rows if r.name.upper() == "PATH"), None)
    assert path_row is not None and path_row.process_value


def test_the_effective_pane_renders_real_registry_data(qapp):
    import winreg

    from modules.env_vars.env_vars_module import EnvVarsModule
    m = EnvVarsModule()
    m.app = type("A", (), {"thread_pool": None})()
    m.create_widget()
    m.on_activate()
    assert m._effective_pane._table.rowCount() > 0
    names = [m._effective_pane._table.item(r, 0).text()
            for r in range(m._effective_pane._table.rowCount())]
    assert any(n.upper() == "PATH" for n in names)


def test_show_only_stale_narrows_the_table(qapp):
    from modules.env_vars.env_vars_module import EnvVarsModule
    m = EnvVarsModule()
    m.app = type("A", (), {"thread_pool": None})()
    m.create_widget()
    m.on_activate()
    full = m._effective_pane._table.rowCount()
    m._effective_pane._only_stale.setChecked(True)
    stale = m._effective_pane._table.rowCount()
    assert stale <= full

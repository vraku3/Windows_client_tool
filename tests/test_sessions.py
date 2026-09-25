from modules.dashboard import sessions as se


def test_real_sessions_include_the_current_user():
    found = se.list_sessions()
    assert found is not None and found, "wtsapi32 should always answer on a live desktop"
    import getpass
    assert any(s.user.lower() == getpass.getuser().lower() for s in found)


def test_state_and_type_naming():
    s = se.Session(2, "RDP-Tcp#3", "bob", "ACME", "Disconnected", "PC9")
    assert s.is_remote and s.account == "ACME" + chr(92) + "bob"
    assert not se.Session(1, "Console", "a", "", "Active", "").is_remote


def test_users_tab_shows_sessions_and_states_a_failed_read(qapp):
    from modules.dashboard.users_tab import UsersTab
    tab = UsersTab()
    tab._show_sessions([se.Session(1, "Console", "a", "PC", "Active", "")])
    assert tab.sessions_table.rowCount() == 1 and "1 session" in tab.sessions_note.text()
    tab._show_sessions(None)
    assert tab.sessions_table.rowCount() == 0 and "Could not ask" in tab.sessions_note.text()

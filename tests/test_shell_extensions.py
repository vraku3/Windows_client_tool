"""Explorer context menu handler enumeration (get_shell_extensions). No Qt."""
from modules.startup_manager import startup_reader as sr


class _KeyCtx:
    def __init__(self, values=None, subkeys=None):
        self._values = values or {}
        self._subkeys = subkeys or []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_healthy_handler_named_as_its_own_clsid(monkeypatch):
    import winreg
    clsid = "{11111111-1111-1111-1111-111111111111}"
    root = r"*\shellex\ContextMenuHandlers"

    def fake_open(hive, path, *a, **k):
        return _KeyCtx()

    def fake_enum(key, index):
        if index == 0:
            return clsid
        raise OSError()

    calls = {"n": 0}

    def fake_query2(key, name):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Real Handler", None      # handler subkey's own default value
        return r"C:\Windows\System32\real.dll", None  # InProcServer32 default

    monkeypatch.setattr(winreg, "OpenKey", fake_open)
    monkeypatch.setattr(winreg, "EnumKey", fake_enum)
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query2)
    monkeypatch.setattr("os.path.isfile", lambda p: True)

    entries = [e for e in sr.get_shell_extensions() if e.source == "shell_ext"]
    # Every one of the 6 roots enumerates the same single CLSID, deduplicated to 1.
    assert len(entries) == 1
    e = entries[0]
    assert e.name == "Real Handler" and e.enabled is True and e.extra == "OK"
    assert e.command == r"C:\Windows\System32\real.dll"


def test_friendly_name_default_value_is_not_mistaken_for_a_missing_clsid(monkeypatch):
    """The real bug this reader was built to avoid: a handler subkey whose
    default value is a FRIENDLY NAME (not a CLSID) must still resolve via
    its own subkey name, which real Windows uses as the CLSID."""
    import winreg
    clsid = "{90AA3A4E-1CBA-4233-B8BB-535773D48449}"

    def fake_open(hive, path, *a, **k):
        return _KeyCtx()

    def fake_enum(key, index):
        if index == 0:
            return clsid
        raise OSError()

    calls = {"n": 0}

    def fake_query(key, name):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Taskband Pin", None  # a friendly name, NOT a CLSID
        return r"%SystemRoot%\system32\shell32.dll", None

    monkeypatch.setattr(winreg, "OpenKey", fake_open)
    monkeypatch.setattr(winreg, "EnumKey", fake_enum)
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query)
    monkeypatch.setattr("os.path.isfile", lambda p: True)

    entries = [e for e in sr.get_shell_extensions() if e.source == "shell_ext"]
    assert len(entries) == 1
    assert entries[0].extra == "OK" and entries[0].name == "Taskband Pin"


def test_clsid_with_no_inprocserver32_is_orphaned(monkeypatch):
    import winreg
    clsid = "{22222222-2222-2222-2222-222222222222}"

    def fake_open(hive, path, *a, **k):
        if "CLSID" in path and path.endswith("Server32"):
            raise OSError("not found")
        return _KeyCtx()

    def fake_enum(key, index):
        if index == 0:
            return clsid
        raise OSError()

    def fake_query(key, name):
        return clsid, None  # the handler's own default value repeats the CLSID

    monkeypatch.setattr(winreg, "OpenKey", fake_open)
    monkeypatch.setattr(winreg, "EnumKey", fake_enum)
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query)

    entries = [e for e in sr.get_shell_extensions() if e.source == "shell_ext"]
    assert len(entries) == 1
    assert entries[0].enabled is False and "ORPHANED" in entries[0].extra
    assert "InProcServer32" in entries[0].extra


def test_clsid_pointing_at_a_missing_file_is_orphaned(monkeypatch, tmp_path):
    import winreg
    clsid = "{33333333-3333-3333-3333-333333333333}"
    missing = str(tmp_path / "gone.dll")

    def fake_open(hive, path, *a, **k):
        return _KeyCtx()

    def fake_enum(key, index):
        if index == 0:
            return clsid
        raise OSError()

    calls = {"n": 0}

    def fake_query(key, name):
        calls["n"] += 1
        return (clsid if calls["n"] == 1 else missing), None

    monkeypatch.setattr(winreg, "OpenKey", fake_open)
    monkeypatch.setattr(winreg, "EnumKey", fake_enum)
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query)

    entries = [e for e in sr.get_shell_extensions() if e.source == "shell_ext"]
    assert len(entries) == 1
    assert entries[0].enabled is False and "file does not exist" in entries[0].extra


def test_an_unopenable_root_is_skipped_not_fatal(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))

    assert sr.get_shell_extensions() == []


def test_real_shell_extensions_are_readable_and_mostly_healthy():
    entries = sr.get_shell_extensions()
    assert len(entries) > 5
    orphaned = [e for e in entries if e.extra.startswith("ORPHANED")]
    # Confirmed clean on this real machine (0 of 37) -- a handful would still
    # be plausible on any given machine, hundreds would mean the resolver broke.
    assert len(orphaned) < len(entries) * 0.5


# ---- icon overlay handlers ---------------------------------------------------

def test_healthy_overlay_handler_resolves(monkeypatch):
    import winreg
    clsid = "{44444444-4444-4444-4444-444444444444}"

    def fake_open(hive, path, *a, **k):
        return _KeyCtx()

    def fake_enum(key, index):
        if index == 0:
            return "MyOverlay"
        raise OSError()

    def fake_query(key, name):
        return clsid, None

    calls = {"n": 0}

    def fake_query_dll(key, name):
        calls["n"] += 1
        if calls["n"] == 1:
            return clsid, None
        return r"C:\Windows\System32\overlay.dll", None

    monkeypatch.setattr(winreg, "OpenKey", fake_open)
    monkeypatch.setattr(winreg, "EnumKey", fake_enum)
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query_dll)
    monkeypatch.setattr("os.path.isfile", lambda p: True)

    entries = sr.get_icon_overlay_handlers()
    assert len(entries) == 1
    assert entries[0].name == "MyOverlay" and entries[0].extra == "OK"


def test_overlay_handler_with_no_clsid_is_orphaned(monkeypatch):
    import winreg

    def fake_open(hive, path, *a, **k):
        return _KeyCtx()

    def fake_enum(key, index):
        if index == 0:
            return "Bogus"
        raise OSError()

    def fake_query(key, name):
        return "not a clsid", None

    monkeypatch.setattr(winreg, "OpenKey", fake_open)
    monkeypatch.setattr(winreg, "EnumKey", fake_enum)
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query)

    entries = sr.get_icon_overlay_handlers()
    assert len(entries) == 1
    assert entries[0].enabled is False and "no CLSID registered" in entries[0].extra


def test_over_the_slot_limit_adds_a_note_without_flagging_as_orphaned(monkeypatch):
    import winreg
    names = [f"Overlay{i:02d}" for i in range(sr.ICON_OVERLAY_SLOT_LIMIT + 3)]
    clsid = "{55555555-5555-5555-5555-555555555555}"

    def fake_open(hive, path, *a, **k):
        return _KeyCtx()

    def fake_enum(key, index):
        if index < len(names):
            return names[index]
        raise OSError()

    monkeypatch.setattr(winreg, "OpenKey", fake_open)
    monkeypatch.setattr(winreg, "EnumKey", fake_enum)
    # The per-handler default value and the CLSID's own default value both
    # get queried with name="" -- differentiate by call count instead.
    calls = {"n": 0}

    def fake_query_seq(key, name):
        calls["n"] += 1
        return (clsid if calls["n"] % 2 == 1 else r"C:\Windows\System32\o.dll"), None

    monkeypatch.setattr(winreg, "QueryValueEx", fake_query_seq)
    monkeypatch.setattr("os.path.isfile", lambda p: True)

    entries = sr.get_icon_overlay_handlers()
    assert len(entries) == len(names)
    assert all(e.enabled for e in entries)
    assert all(str(sr.ICON_OVERLAY_SLOT_LIMIT) in e.extra for e in entries)


def test_an_unopenable_overlay_key_returns_no_entries(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))

    assert sr.get_icon_overlay_handlers() == []


def test_real_icon_overlay_handlers_are_readable_and_mostly_healthy():
    entries = sr.get_icon_overlay_handlers()
    assert len(entries) > 0
    orphaned = [e for e in entries if e.extra.startswith("ORPHANED")]
    assert len(orphaned) == 0  # confirmed clean on this real machine (9 of 9)

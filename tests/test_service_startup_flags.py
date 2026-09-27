"""Delayed-autostart and trigger-start reading (registry, no subprocess)."""
from modules.services_manager import service_audit as sa


def test_real_registry_is_readable_and_plausible():
    flags = sa.read_startup_flags()
    assert flags is not None
    assert len(flags) > 100
    assert any(v["trigger"] for v in flags.values())
    assert any(v["delayed"] for v in flags.values())


def test_a_known_trigger_start_service_is_flagged():
    flags = sa.read_startup_flags()
    assert flags is not None
    # AppXSvc is trigger-started on every real Windows 10/11 install.
    assert flags.get("appxsvc", {}).get("trigger") is True


class _KeyCtx:
    """A fake context-manager key. `winreg.OpenKey(root, name, ...)` returns
    one of these; the real module only ever uses it via `with` or `.Close()`,
    both of which this provides."""

    def __init__(self, name=""):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def Close(self):
        pass


def test_absent_delayedautostart_value_means_ordinary_automatic(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _KeyCtx())
    monkeypatch.setattr(winreg, "EnumKey",
                        lambda root, index: "FakeSvc" if index == 0 else (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *a: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(sa, "_has_subkey", lambda key, name: False)
    flags = sa.read_startup_flags()
    assert flags == {"fakesvc": {"delayed": False, "trigger": False}}


def test_an_unopenable_services_key_returns_none(monkeypatch):
    import winreg
    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))
    assert sa.read_startup_flags() is None


def test_one_unreadable_service_key_is_skipped_not_fatal(monkeypatch):
    import winreg

    def fake_open_key(root, name=None, *a, **k):
        if name == "Bad":
            raise OSError("access denied")
        return _KeyCtx(name or "")

    monkeypatch.setattr(winreg, "OpenKey", fake_open_key)
    monkeypatch.setattr(winreg, "EnumKey",
                        lambda root, index: ["Bad", "Good"][index] if index < 2
                        else (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *a: (_ for _ in ()).throw(FileNotFoundError()))
    monkeypatch.setattr(sa, "_has_subkey", lambda key, name: False)
    flags = sa.read_startup_flags()
    assert "good" in flags and "bad" not in flags


def test_services_module_merges_real_startup_flags_into_the_service_list():
    from modules.services_manager.services_module import _with_startup_flags, get_services
    merged = _with_startup_flags(get_services())
    assert merged
    assert any(s.get("TriggerStart") for s in merged)
    assert all("DelayedAutostart" in s and "TriggerStart" in s for s in merged)


def test_a_refused_flags_read_leaves_the_service_list_unharmed(monkeypatch):
    from modules.services_manager import services_module as sm
    monkeypatch.setattr(sm.service_audit, "read_startup_flags", lambda: None)
    services = [{"Name": "Foo"}]
    assert sm._with_startup_flags(services) == [{"Name": "Foo"}]


def test_the_service_view_chips_read_the_merged_flags():
    from modules.services_manager import service_view as sv
    delayed = {"Name": "X", "DelayedAutostart": True, "TriggerStart": False}
    trigger = {"Name": "Y", "DelayedAutostart": False, "TriggerStart": True}
    plain = {"Name": "Z", "DelayedAutostart": False, "TriggerStart": False}
    assert sv.passes("delayed", delayed) and not sv.passes("delayed", plain)
    assert sv.passes("triggerstart", trigger) and not sv.passes("triggerstart", plain)
    counts = sv.filter_counts([delayed, trigger, plain])
    assert counts["delayed"] == 1 and counts["triggerstart"] == 1

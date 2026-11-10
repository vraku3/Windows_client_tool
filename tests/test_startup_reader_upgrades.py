import winreg
import pytest
from modules.startup_manager import startup_reader as sr


def test_collect_disabled_names_supports_hive_parameter():
    # Calling on HKCU should return a set
    res = sr._collect_disabled_names(sr._RUN_APPROVED_KEY, hive=winreg.HKEY_CURRENT_USER)
    assert isinstance(res, set)


def test_get_machine_registry_entries_includes_wow64(monkeypatch):
    calls = []
    def fake_read_run_values(hive, key_path, source, disabled, extra=""):
        calls.append((hive, key_path, source, extra))
        if "WOW64" in key_path:
            return [sr.StartupEntry(name="32BitApp", command="app.exe", enabled=True, source=source, extra=extra)]
        return [sr.StartupEntry(name="64BitApp", command="app64.exe", enabled=True, source=source, extra=extra)]

    monkeypatch.setattr(sr, "_read_run_values", fake_read_run_values)
    monkeypatch.setattr(sr, "_collect_disabled_names", lambda *a, **k: set())

    entries = sr.get_machine_registry_entries()
    assert len(entries) == 2
    names = {e.name for e in entries}
    assert "32BitApp" in names
    assert "64BitApp" in names
    assert any("WOW64" in e.extra for e in entries)


def test_get_startup_folder_entries_includes_both_user_and_common(monkeypatch, tmp_path):
    user_dir = tmp_path / "UserStartup"
    user_dir.mkdir()
    (user_dir / "user_app.lnk").write_text("x")
    (user_dir / "desktop.ini").write_text("ini")

    common_dir = tmp_path / "CommonStartup"
    common_dir.mkdir()
    (common_dir / "common_app.bat").write_text("echo hi")

    monkeypatch.setattr(sr, "get_startup_folder_path", lambda: str(user_dir))
    monkeypatch.setattr(sr, "get_common_startup_folder_path", lambda: str(common_dir))
    monkeypatch.setattr(sr, "_collect_disabled_names", lambda *a, **k: set())

    entries = sr.get_startup_folder_entries()
    names = {e.name for e in entries}
    assert "user_app.lnk" in names
    assert "common_app.bat" in names
    assert "desktop.ini" not in names

    user_entry = next(e for e in entries if e.name == "user_app.lnk")
    assert user_entry.extra == "User"
    common_entry = next(e for e in entries if e.name == "common_app.bat")
    assert common_entry.extra == "All Users"


def test_remove_startup_folder_entry_checks_both_paths(monkeypatch, tmp_path):
    user_dir = tmp_path / "UserStartup"
    user_dir.mkdir()
    common_dir = tmp_path / "CommonStartup"
    common_dir.mkdir()
    
    target_file = common_dir / "target.lnk"
    target_file.write_text("x")

    monkeypatch.setattr(sr, "get_startup_folder_path", lambda: str(user_dir))
    monkeypatch.setattr(sr, "get_common_startup_folder_path", lambda: str(common_dir))
    
    # Run remove
    sr.remove_startup_folder_entry("target.lnk")
    assert not target_file.exists()


"""Editing scripts: a built-in is copied to the user's folder (never edited in
place, never overwritten), a new one starts from a template, and the editor
is ISE when there is one."""
from modules.scripts_hub import library as lib


def _core():
    return next(s for s in lib.BUILT_IN if s.id == "uptime")


def test_the_editable_source_drops_the_wrapper_and_keeps_the_logic():
    src = lib.editable_source(_core())
    assert "Win32_OperatingSystem" in src
    assert "ErrorRecord" not in src and "$ErrorActionPreference" not in src


def test_a_built_in_is_copied_once_and_never_overwritten(tmp_path):
    path, created = lib.export_for_editing(_core(), str(tmp_path))
    assert created and path.endswith("uptime.ps1")
    text = open(path, encoding="utf-8-sig").read()
    assert text.startswith("<#") and ".SYNOPSIS" in text and "Win32_OperatingSystem" in text
    with open(path, "a", encoding="utf-8") as f:
        f.write("# my edit\n")
    again, created2 = lib.export_for_editing(_core(), str(tmp_path))
    assert again == path and not created2
    assert "# my edit" in open(path, encoding="utf-8-sig").read()


def test_the_copy_lists_as_a_user_script_with_its_description(tmp_path):
    lib.export_for_editing(_core(), str(tmp_path))
    (mine,) = lib.load_user_scripts(str(tmp_path))
    assert mine.title == "uptime.ps1" and mine.category == lib.USER_CATEGORY
    assert mine.description.startswith("Uptime, last boot and Fast Startup")


def test_a_copy_of_a_cmd_script_stays_a_cmd(tmp_path):
    s = next(x for x in lib.built_in() if x.kind == lib.CMD)
    path, _ = lib.export_for_editing(s, str(tmp_path))
    assert path.endswith(".cmd") and s.body in open(path, encoding="utf-8-sig").read()


def test_new_scripts_never_overwrite(tmp_path):
    a = lib.new_script("My check", str(tmp_path))
    b = lib.new_script("My check", str(tmp_path))
    assert a != b and a.endswith("My_check.ps1") and b.endswith("My_check_2.ps1")
    (first, _second) = lib.load_user_scripts(str(tmp_path))
    assert first.description.startswith("My check")


def test_ise_opens_ps1_and_notepad_is_the_last_resort(monkeypatch):
    monkeypatch.setattr(lib, "editors", lambda: [("PowerShell ISE", r"C:\ise.exe"),
                                                 ("Visual Studio Code", r"C:\code.cmd"),
                                                 ("Notepad", r"C:\notepad.exe")])
    assert lib.editor_command(r"C:\s\a.ps1") == [r"C:\ise.exe", r"C:\s\a.ps1"]
    assert lib.editor_command(r"C:\s\a.cmd") == ["cmd.exe", "/c", r"C:\code.cmd", r"C:\s\a.cmd"]
    assert lib.editor_command(r"C:\s\a.ps1", "Notepad") == [r"C:\notepad.exe", r"C:\s\a.ps1"]
    monkeypatch.setattr(lib, "editors", lambda: [("Notepad", r"C:\notepad.exe")])
    assert lib.editor_command(r"C:\s\a.ps1") == [r"C:\notepad.exe", r"C:\s\a.ps1"]

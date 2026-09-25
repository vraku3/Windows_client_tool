r"""System Health said the WinClientTool exe "does not exist" -- but it does.

Seen 2026-09-25. `schtasks /query /xml` writes through the console's legacy
codepage, so the comma-below t in "Aplicatii" came back as `?`; the mangled path
does not exist, so a task pointing at a real program was reported as pointing
at a missing one. The same finding also appeared twice (schtasks lists a task
once per trigger) and printed every path with doubled backslashes (repr()).
"""
import subprocess

from modules.system_health import findings as f

ODD = "Aplica\u021bii"          # a character the console codepage cannot hold


def _task_file(root, name, program):
    path = root / "System32" / "Tasks" / name.strip("\\")
    path.parent.mkdir(parents=True, exist_ok=True)
    xml = f"<Task><Actions><Exec><Command>{program}</Command></Exec></Actions></Task>"
    path.write_bytes(xml.encode("utf-16"))          # how Windows stores them


def _schtasks_lists(monkeypatch, names):
    csv_out = "".join(f'"{n}","Ready","N/A"\r\n' for n in names)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
            stderr = ""
            stdout = csv_out if "/xml" not in cmd else ""
        return R()
    monkeypatch.setattr(subprocess, "run", fake_run)


def test_a_path_the_console_codepage_cannot_hold_is_not_falsely_reported(tmp_path, monkeypatch):
    exe_dir = tmp_path / ODD
    exe_dir.mkdir()
    exe = exe_dir / "Tool.exe"
    exe.write_text("x")
    _task_file(tmp_path, "\\WinClientTool_Maintenance", str(exe))
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    _schtasks_lists(monkeypatch, ["\\WinClientTool_Maintenance"])
    assert f.check_orphaned_scheduled_tasks() == []


def test_a_genuinely_missing_program_is_still_reported(tmp_path, monkeypatch):
    _task_file(tmp_path, "\\Gone", str(tmp_path / ODD / "missing.exe"))
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    _schtasks_lists(monkeypatch, ["\\Gone"])
    found = f.check_orphaned_scheduled_tasks()
    assert len(found) == 1 and "missing.exe" in found[0].detail


def test_the_detail_uses_plain_paths_not_doubled_backslashes(tmp_path, monkeypatch):
    _task_file(tmp_path, "\\Sub\\T", r"C:\does\not\exist.exe")
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    _schtasks_lists(monkeypatch, ["\\Sub\\T"])
    detail = f.check_orphaned_scheduled_tasks()[0].detail
    assert r"C:\does\not\exist.exe" in detail
    assert "\\\\" not in detail


def test_a_task_listed_once_per_trigger_is_reported_once(tmp_path, monkeypatch):
    _task_file(tmp_path, "\\Multi", r"C:\nope\x.exe")
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    _schtasks_lists(monkeypatch, ["\\Multi", "\\Multi", "\\Multi", ""])
    assert len(f.check_orphaned_scheduled_tasks()) == 1


def test_an_unreadable_task_file_falls_back_to_schtasks(tmp_path, monkeypatch):
    monkeypatch.setenv("SystemRoot", str(tmp_path))            # no Tasks folder at all
    assert f._task_xml_from_file("\\Nothing") is None
    assert f._task_xml_from_file("\\") is None

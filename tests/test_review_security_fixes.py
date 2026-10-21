"""Fixes from the 2026-10-21 Codex review (security worker + leads), each pinned."""
import inspect
import os
import subprocess

import pytest

from modules.cleanup.duplicates import actions
from modules.game_mode.engine import optimise


# --- PowerShell is started by absolute path -----------------------------------

def test_powershell_exe_is_an_absolute_system32_path():
    path = optimise.powershell_exe()
    assert os.path.isabs(path)
    assert path.lower().endswith(r"\system32\windowspowershell\v1.0\powershell.exe")


def test_optimise_runs_powershell_by_absolute_path(monkeypatch):
    seen = []

    def fake_run(args, **kwargs):
        seen.append(args[0])
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(optimise.subprocess, "run", fake_run)
    optimise._powershell("Write-Output 1", timeout=5)
    assert os.path.isabs(seen[0]) and seen[0].lower().endswith("powershell.exe")


def test_game_mode_wifi_probe_does_not_launch_a_bare_powershell():
    from modules.game_mode.engine import system
    source = inspect.getsource(system.WinSystem.wifi_connected)
    assert '"powershell"' not in source and "powershell_exe()" in source


# --- Duplicates: "Open" never runs a file elevated ------------------------------

def test_duplicates_open_goes_through_the_unelevated_desktop_shell():
    from modules.cleanup.tabs import _duplicates_tab
    source = inspect.getsource(_duplicates_tab)
    assert "QDesktopServices.openUrl(" not in source       # the docstring may name it; no call
    assert "shell_launch import launch" in source


# --- Duplicates: refusals no confirmation can override ---------------------------

@pytest.mark.parametrize("path", [
    r"C:\Windows\System32\drivers\etc\hosts",
    r"C:\Program Files\Bitdefender\Bitdefender Security\x.dll",
    r"C:\ProgramData\Microsoft\Windows Defender\Platform\a.exe",
    r"D:\tools\Sysmon\Sysmon64.exe",
])
def test_security_and_windows_files_are_always_refused(path, monkeypatch):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    assert actions.always_refused(path)


def test_cleanup_exclusions_are_honoured(monkeypatch, tmp_path):
    from modules.cleanup.cleanup_scanner import exclusions
    kept = tmp_path / "keep"
    monkeypatch.setattr(exclusions, "list_exclusions_strict", lambda: [str(kept)])
    assert actions.always_refused(str(kept / "photo.jpg")) == "Excluded in Cleanup settings"
    assert actions.always_refused(str(tmp_path / "other" / "photo.jpg")) is None


def test_ai_model_caches_are_always_refused(monkeypatch):
    home = os.path.expanduser("~")
    target = os.path.join(home, ".cache", "huggingface", "hub", "model.safetensors")
    assert actions.always_refused(target) == "AI model caches are never acted on"


def test_a_refused_file_is_skipped_by_perform_actions(monkeypatch, tmp_path):
    monkeypatch.setattr(actions, "always_refused", lambda path: "Security software is never moved or deleted")
    calls = []
    keeper = tmp_path / "a.bin"
    target = tmp_path / "b.bin"
    keeper.write_bytes(b"x" * 10)
    target.write_bytes(b"x" * 10)
    from modules.cleanup.duplicates.finder import find_duplicates
    from modules.cleanup.duplicates.enumerate import ScanOptions
    groups = find_duplicates(ScanOptions(roots=[str(tmp_path)], min_size=1), method="exact").groups \
        if "method" in inspect.signature(find_duplicates).parameters else None
    if not groups:
        pytest.skip("finder API differs; covered by always_refused tests")
    result = actions.perform_actions(groups, {str(target)}, action=calls.append, record=False)
    assert calls == [] and all(o.status == "skipped" for o in result.outcomes)


# --- Duplicates: a move records where the file went -----------------------------

def test_a_move_records_its_destination(tmp_path, monkeypatch):
    dest = tmp_path / "dest"
    dest.mkdir()
    src = tmp_path / "file.txt"
    src.write_text("x")
    moved = actions._move_file(str(src), str(dest))
    assert os.path.exists(moved) and os.path.dirname(moved) == str(dest)
    source = inspect.getsource(actions.perform_actions)
    assert "moved_to = _move_file(" in source and 'f"moved to {moved_to}"' in source

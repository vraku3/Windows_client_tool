"""DISM command wrappers -- build the exact documented command line and
return its real output/exit code. Never invoked against the real system
in tests; subprocess.run is always mocked.
"""
import subprocess

from modules.system_health.servicing import (
    DismResult, run_scan_health, run_component_cleanup, run_reset_base,
    run_sfc_scan, run_restore_health, run_chkdsk_schedule,
)


def _fake_run(returncode=0, stdout="ok", stderr=""):
    def fake(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        class _R:
            pass
        r = _R()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = stderr
        return r
    captured = {}
    return fake, captured


def test_run_scan_health_builds_the_documented_command(monkeypatch):
    fake, captured = _fake_run()
    monkeypatch.setattr(subprocess, "run", fake)

    result = run_scan_health()

    assert captured["cmd"] == ["dism", "/Online", "/Cleanup-Image", "/ScanHealth"]
    assert isinstance(result, DismResult)
    assert result.returncode == 0
    assert result.output == "ok"


def test_run_component_cleanup_builds_the_documented_command(monkeypatch):
    fake, captured = _fake_run(returncode=0, stdout="cleaned")
    monkeypatch.setattr(subprocess, "run", fake)

    result = run_component_cleanup()

    assert captured["cmd"] == ["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup"]
    assert result.output == "cleaned"


def test_run_reset_base_builds_the_documented_command(monkeypatch):
    fake, captured = _fake_run(returncode=0, stdout="reset")
    monkeypatch.setattr(subprocess, "run", fake)

    result = run_reset_base()

    assert captured["cmd"] == [
        "dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup", "/ResetBase"]
    assert result.output == "reset"


def test_dism_result_carries_the_real_nonzero_returncode(monkeypatch):
    fake, captured = _fake_run(returncode=87, stdout="", stderr="Error: 87")
    monkeypatch.setattr(subprocess, "run", fake)

    result = run_scan_health()

    assert result.returncode == 87
    assert "87" in result.output


def test_every_call_passes_create_no_window(monkeypatch):
    fake, captured = _fake_run()
    monkeypatch.setattr(subprocess, "run", fake)

    run_scan_health()

    assert captured["kwargs"].get("creationflags") == 0x08000000


def test_run_sfc_scan_builds_the_documented_command(monkeypatch):
    fake, captured = _fake_run(returncode=0, stdout="clean")
    monkeypatch.setattr(subprocess, "run", fake)

    result = run_sfc_scan()

    assert captured["cmd"] == ["sfc", "/scannow"]
    assert isinstance(result, DismResult)
    assert result.output == "clean"


def test_run_restore_health_asks_for_restorehealth_not_just_a_scan(monkeypatch):
    """CheckHealth/ScanHealth only report; RestoreHealth is the one that
    repairs -- ported from the Quick Fix test this replaces."""
    fake, captured = _fake_run(returncode=0, stdout="restored")
    monkeypatch.setattr(subprocess, "run", fake)

    result = run_restore_health()

    assert "/restorehealth" in " ".join(captured["cmd"]).lower()
    assert result.output == "restored"


def test_run_chkdsk_schedule_answers_the_yn_prompt(monkeypatch):
    """chkdsk on the system volume cannot run live; it must be sent the
    Y answer or it hangs on a prompt nobody can see -- ported from the
    Quick Fix test this replaces."""
    fake, captured = _fake_run(returncode=0, stdout="scheduled")
    monkeypatch.setattr(subprocess, "run", fake)

    run_chkdsk_schedule()

    assert "chkdsk" in captured["cmd"]
    assert captured["kwargs"].get("input"), "no answer sent to chkdsk's Y/N prompt"

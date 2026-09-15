"""DISM command wrappers -- build the exact documented command line and
return its real output/exit code. Never invoked against the real system
in tests; subprocess.run is always mocked.
"""
import subprocess

from modules.system_health.servicing import (
    DismResult, run_scan_health, run_component_cleanup, run_reset_base,
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

"""Whole-app review batch F: GP export staleness, DNS flush verification,
TreeSize enumeration errors, restart-history read failures."""
import subprocess

from modules.gpresult import rsop_runner as rr
from modules.network_diagnostics import network_fixes as nf
from modules.scripts_hub import library
from modules.treesize.scan import walk_scanner as ws


def test_a_failed_export_does_not_announce_the_old_report(tmp_path, monkeypatch):
    dest = tmp_path / "rsop.html"
    dest.write_text("OLD REPORT")
    monkeypatch.setattr(rr.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, stdout="ERROR: Access denied.", stderr=""))
    ok, why = rr.export_html_report(str(dest))
    assert not ok and "Access denied" in why
    assert dest.read_text() == "OLD REPORT"
    assert [p.name for p in tmp_path.iterdir()] == ["rsop.html"]


def test_a_successful_export_replaces_the_report(tmp_path, monkeypatch):
    dest = tmp_path / "rsop.html"
    dest.write_text("OLD")

    def run(args, **k):
        open(args[2], "w").write("<html>new</html>")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    monkeypatch.setattr(rr.subprocess, "run", run)
    assert rr.export_html_report(str(dest)) == (True, "")
    assert dest.read_text() == "<html>new</html>"


_DISPLAYDNS = """
Windows IP Configuration

    example.com
    ----------------------------------------
    Nume inregistrare . . . . : example.com

    contoso.com
    ----------------------------------------
    Nume inregistrare . . . . : contoso.com
"""


def test_cache_entries_are_counted_in_any_language(monkeypatch):
    monkeypatch.setattr(nf, "_run", lambda cmd, timeout=45: subprocess.CompletedProcess(
        cmd, 0, stdout=_DISPLAYDNS, stderr=""))
    assert nf.dns_cache_count() == 2


def test_a_refused_cache_read_is_unknown(monkeypatch):
    monkeypatch.setattr(nf, "_run", lambda cmd, timeout=45: subprocess.CompletedProcess(
        cmd, 1, stdout="The requested operation requires elevation.", stderr=""))
    assert nf.dns_cache_count() is None


def test_a_failed_flush_is_not_verified():
    run = lambda cmd, timeout=45: subprocess.CompletedProcess(cmd, 1, stdout="Some error", stderr="")
    result = nf.flush_dns(run=run, count=lambda: 0)
    assert result.ok is False


def test_an_interrupted_listing_is_reported(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.txt").write_text("y")
    errors = []
    real_next = ws._kernel32.FindNextFileW

    class _K:
        def __getattr__(self, name):
            return getattr(ws._kernel32_real, name)

        def FindNextFileW(self, h, d):
            return 0                      # fails at once

    monkeypatch.setattr(ws, "_kernel32_real", ws._kernel32, raising=False)
    monkeypatch.setattr(ws, "_kernel32", _K())
    monkeypatch.setattr(ws.ctypes, "get_last_error", lambda: 59)   # ERROR_UNEXP_NET_ERR
    ws.list_directory(str(tmp_path), on_error=lambda p, why: errors.append(why))
    assert errors and "stopped early" in errors[0]
    assert real_next is not None


def test_the_restart_history_tells_a_refusal_from_no_events():
    body = next(s for s in library.BUILT_IN if s.id == "restarts").body
    assert "NoMatchingEventsFound" in body and "ERROR: the System log could not be read" in body
    assert "SilentlyContinue" not in body.split("$events = Get-WinEvent", 1)[1].split("\n", 1)[0]

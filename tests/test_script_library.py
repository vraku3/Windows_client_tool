"""Script Library: read-only checks with live output, the user's own
scripts, and a support bundle whose manifest accounts for every step."""
import base64
import io
import os
import zipfile

from modules.scripts_hub import library as lib
from modules.scripts_hub import support_bundle as sb


def test_every_built_in_is_read_only_unique_and_categorised():
    ids = [s.id for s in lib.BUILT_IN]
    assert len(ids) == len(set(ids))
    for s in lib.BUILT_IN:
        assert s.read_only and s.category in lib.CATEGORIES and s.description.strip()
        assert s.category != "My scripts"


def test_no_built_in_writes_to_the_machine():
    """A read-only check must not contain the verbs that change state."""
    writers = ("Set-", "Remove-", "New-Item", "Stop-", "Start-Service", "Restart-", "Disable-",
               "Enable-", "Add-", "Clear-", "reg add", "reg delete", "sc config", "netsh advfirewall set",
               "Out-File", "Export-Csv", "Invoke-WebRequest", "Invoke-Expression")
    for s in lib.BUILT_IN:
        body = s.body.replace("Set-StrictMode", "")
        hits = [w for w in writers if w.lower() in body.lower()]
        assert not hits, (s.id, hits)


def test_powershell_errors_come_back_as_text_and_an_admin_note_survives_an_apostrophe():
    body = lib._ps("Get-Thing", "other users' processes")
    assert "'other users'' processes'" in body          # PowerShell single-quote escaping
    assert "ErrorRecord" in body and "'ERROR: '" in body
    assert "$ProgressPreference = 'SilentlyContinue'" in body


def test_built_ins_run_encoded_and_user_scripts_run_their_file():
    s = lib.BUILT_IN[0]
    argv = lib.command_for(s)
    assert argv[0] == "powershell.exe" and "-OutputFormat" in argv
    encoded = argv[argv.index("-EncodedCommand") + 1]
    assert base64.b64decode(encoded).decode("utf-16-le") == s.body
    user = lib.Script("user:x.ps1", "x.ps1", "My scripts", "", "", path=r"C:\s\x.ps1")
    assert lib.command_for(user)[-2:] == ["-File", r"C:\s\x.ps1"]
    cmd = lib.Script("user:y.cmd", "y.cmd", "My scripts", "", "", kind=lib.CMD, path=r"C:\s\y.cmd")
    assert lib.command_for(cmd) == ["cmd.exe", "/d", "/c", r"C:\s\y.cmd"]


def test_the_dns_cache_check_refuses_rather_than_report_empty_unelevated():
    s = next(x for x in lib.BUILT_IN if x.id == "dns_cache")
    assert s.admin and "needs administrator rights" in s.body
    assert s.body.index("$isAdmin") < s.body.index("Get-DnsClientCache")


def test_user_scripts_are_listed_with_their_description(tmp_path):
    (tmp_path / "a.ps1").write_text("<#\n.SYNOPSIS\nCleans my temp\n#>\nWrite-Output hi\n",
                                    encoding="utf-8")
    (tmp_path / "b.cmd").write_text("@echo off\nrem Maps my drives\nnet use\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("not a script", encoding="utf-8")
    scripts = lib.load_user_scripts(str(tmp_path))
    assert [s.title for s in scripts] == ["a.ps1", "b.cmd"]
    assert scripts[0].description == "Cleans my temp" and not scripts[0].read_only
    assert scripts[1].description == "Maps my drives" and scripts[1].kind == lib.CMD
    assert lib.load_user_scripts(str(tmp_path / "missing")) == []


class FakeProc:
    def __init__(self, lines, rc=0):
        self.stdout = io.BytesIO(b"".join(line + b"\r\n" for line in lines))
        self.returncode = rc
        self.pid = 99999999

    def wait(self, timeout=None):
        return self.returncode


def test_run_streams_lines_and_decodes_the_console_code_page():
    seen = []
    proc = FakeProc([b"hello", "caf\u00e9".encode("cp850")], rc=0)
    result = lib.run(lib.BUILT_IN[0], seen.append, popen=lambda *a, **k: proc)
    assert seen[0] == "hello" and len(seen) == 2 and result.ok and result.returncode == 0


def test_a_nonzero_exit_is_not_ok():
    result = lib.run(lib.BUILT_IN[0], lambda _l: None, popen=lambda *a, **k: FakeProc([b"x"], 3))
    assert result.returncode == 3 and not result.ok


def test_the_bundle_manifest_accounts_for_every_step(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "is_admin", lambda: False)
    plan = [sb.Step("Fine", "fine.txt"), sb.Step("Broken", "broken.txt"),
            sb.Step("Admin only", "admin.txt", admin=True)]

    def fake_run(step, out_path, elevated, is_cancelled):
        if step.admin and not elevated:
            return sb.StepResult(step, sb.SKIPPED, detail="needs administrator rights")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            f.write("data")
        status = sb.OK if step.name == "Fine" else sb.FAILED
        return sb.StepResult(step, status, detail="" if status == sb.OK else "exit 5: Access is denied")

    bundle = sb.collect(str(tmp_path), plan=plan, run_step=fake_run)
    assert bundle.counts == {sb.OK: 1, sb.FAILED: 1, sb.SKIPPED: 1}
    with zipfile.ZipFile(bundle.path) as z:
        manifest = z.read("MANIFEST.txt").decode()
        assert {"fine.txt", "broken.txt", "MANIFEST.txt"} <= set(z.namelist())
    assert "[FAILED   ] Broken" in manifest and "exit 5: Access is denied" in manifest
    assert "[SKIPPED  ] Admin only" in manifest and "needs administrator rights" in manifest
    assert "Elevated : NO" in manifest and manifest.replace("\r\n", "\n") == bundle.manifest


def test_cancelling_the_bundle_still_zips_what_was_collected(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "is_admin", lambda: True)
    plan = [sb.Step("One", "one.txt"), sb.Step("Two", "two.txt")]
    calls = []

    def fake_run(step, out_path, elevated, is_cancelled):
        calls.append(step.name)
        with open(out_path, "w") as f:
            f.write("x")
        return sb.StepResult(step, sb.OK)

    bundle = sb.collect(str(tmp_path), plan=plan, run_step=fake_run,
                        is_cancelled=lambda: bool(calls))
    assert calls == ["One"] and bundle.counts == {sb.OK: 1, sb.CANCELLED: 1}
    assert os.path.isfile(bundle.path)


def test_the_bundle_plan_skips_library_checks_it_already_exports():
    names = {s.filename for s in sb.steps()}
    assert "checks/ipconfig.txt" not in names and "ipconfig_all.txt" in names
    assert "checks/restarts.txt" in names
    sec = next(s for s in sb.steps() if s.filename == "Security.evtx")
    assert sec.admin

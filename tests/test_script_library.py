"""Script Library: read-only checks with live output, the user's own
scripts, and a support bundle whose manifest accounts for every step."""
import base64
import io
import os
import zipfile

from modules.scripts_hub import library as lib
from modules.scripts_hub import support_bundle as sb


def test_every_built_in_is_read_only_unique_and_categorised():
    ids = [s.id for s in lib.built_in()]
    assert len(ids) == len(set(ids)) and len(ids) >= 600
    for s in lib.built_in():
        assert s.read_only and s.category in lib.CATEGORIES and s.description.strip()
        assert s.category != "My scripts"


def test_no_built_in_writes_to_the_machine():
    """A read-only check must not call a cmdlet or command that changes state.
    Cmdlets are matched as `Verb-Noun` (capitalised noun), so a flag such as
    winget's --disable-interactivity is not mistaken for Disable-Something."""
    import re
    verbs = re.compile(r"(?<![\w-])(Set|Remove|New|Stop|Start|Restart|Disable|Enable|Add|Clear|"
                       r"Install|Uninstall|Register|Unregister|Reset|Suspend|Resume|Rename|Move|"
                       r"Copy|Invoke-Expression|Invoke-WebRequest|Out-File|Export-Csv)-[A-Z]")
    commands = ("reg add", "reg delete", "sc config", "sc stop", "sc delete", "netsh advfirewall set",
                "bcdedit /set", "net stop", "net user ", "format ", "diskpart", "cipher /w")
    for s in lib.built_in():
        # Set-StrictMode and New-Object change nothing outside the script itself.
        body = s.body.replace("Set-StrictMode", "").replace("New-Object", "")
        # Opening and closing a CIM session to read a server changes nothing on it.
        for ok in ("New-CimSessionOption", "New-CimSession", "Remove-CimSession", "New-TimeSpan"):
            body = body.replace(ok, "")
        hits = [m.group(0) for m in verbs.finditer(body)] + [c for c in commands if c in body.lower()]
        assert not hits, (s.id, hits)


def test_powershell_errors_come_back_as_text_and_an_admin_note_survives_an_apostrophe():
    body = lib._ps("Get-Thing", "other users' processes")
    assert "'other users'' processes'" in body          # PowerShell single-quote escaping
    assert "ErrorRecord" in body and "'ERROR: '" in body
    assert "$ProgressPreference = 'SilentlyContinue'" in body


def test_user_scripts_run_their_own_file():
    user = lib.Script("user:x.ps1", "x.ps1", "My scripts", "", "", path=r"C:\s\x.ps1")
    assert lib.command_for(user)[-2:] == ["-File", r"C:\s\x.ps1"]
    assert "-OutputFormat" in lib.command_for(user)
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


def test_a_library_check_that_printed_errors_is_not_ok_even_at_exit_0(tmp_path, monkeypatch):
    """Built-ins print each ErrorRecord as `ERROR:` and exit 0."""
    script = sb.library.BUILT_IN[0]
    monkeypatch.setattr(sb.library, "run", lambda *a, **k: sb.library.RunResult(
        script_id=script.id, returncode=0, lines=["Name : x", "ERROR: Access is denied"]))
    res = sb._run_step(sb.Step("Check", "check.txt", script=script),
                       str(tmp_path / "check.txt"), elevated=True, is_cancelled=lambda: False)
    assert res.status == sb.FAILED and "Access is denied" in res.detail


def test_the_bundle_plan_skips_library_checks_it_already_exports():
    names = {s.filename for s in sb.steps()}
    assert "checks/ipconfig.txt" not in names and "ipconfig_all.txt" in names
    assert "checks/restarts.txt" in names
    sec = next(s for s in sb.steps() if s.filename == "Security.evtx")
    assert sec.admin


# ---- the catalog --------------------------------------------------------------------------

def test_every_server_script_says_what_it_needs_and_guards_for_it():
    server = [s for s in lib.built_in() if s.server]
    assert len(server) >= 250
    for s in server:
        # Either it names what it needs and says NOT AVAILABLE without it, or it
        # needs nothing special and was run here (fleet, log parsing, storage).
        if s.requires:
            assert "NOT AVAILABLE" in s.body, s.id
        assert s.category in lib.SERVER_CATEGORIES, s.id


def test_client_scripts_live_in_client_categories():
    for s in lib.built_in():
        if not s.server:
            assert s.category in lib.CLIENT_CATEGORIES, s.id


def test_reads_that_go_silently_empty_unelevated_refuse_instead():
    """Measured 2026-10-16: Get-DnsClientCache and Get-Partition return nothing,
    with no error, for a standard user."""
    by_id = {s.id: s for s in lib.built_in()}
    for sid in ("dns_cache", "partitions"):
        assert by_id[sid].admin and "needs administrator" in by_id[sid].body, sid


def test_print_checks_state_the_spooler_first():
    by_id = {s.id: s for s in lib.built_in()}
    for sid in ("printers", "print_jobs", "print_drivers", "printer_ports"):
        body = by_id[sid].body
        assert body.index("Get-Service Spooler") < body.index("Get-Print"), sid


def test_a_refused_launch_is_an_error_line_not_an_exception():
    def refuse(*a, **k):
        raise PermissionError(5, "Access is denied")
    seen = []
    r = lib.run(lib.BUILT_IN[0], seen.append, popen=refuse)
    assert r.returncode == -1 and seen and seen[0].startswith("ERROR: Windows refused")


def test_built_ins_run_from_a_temp_file_that_is_removed_afterwards():
    captured = {}

    def fake_popen(argv, **kw):
        path = argv[argv.index("-File") + 1]
        captured["path"] = path
        with open(path, encoding="utf-8-sig") as f:
            captured["body"] = f.read()
        return FakeProc([b"x"])

    lib.run(lib.BUILT_IN[0], lambda _l: None, popen=fake_popen)
    assert captured["body"] == lib.BUILT_IN[0].body
    assert not os.path.exists(captured["path"])


# ---- output colouring ---------------------------------------------------------------------

def test_output_lines_are_classified():
    from modules.scripts_hub import output_style as st
    assert st.spans("ERROR: boom") == [("ERROR: boom", st.BAD)]
    assert st.spans("[BAD]  dead disk") == [("[BAD]  dead disk", st.BAD)]
    assert st.spans("[WARN] x")[0][1] == st.WARN
    assert st.spans("[OK]   fine") == [("[OK]", st.OK), ("   fine", st.PLAIN)]
    assert st.spans("  [INFO] note")[0] == ("  [INFO]", st.INFO)
    assert st.spans("NOT AVAILABLE: no AD")[0][1] == st.NA
    assert st.spans("NOTE: not elevated")[0][1] == st.WARN
    assert st.spans("== Domain Admins (2)")[0][1] == st.HEADER
    assert st.spans("----  -----") == [("----  -----", st.RULE)] and st.is_rule("---- ---")
    assert st.spans("Last boot    : 10/16/2026")[0] == ("Last boot", st.KEY)
    assert st.spans(r"C:\Windows : nope")[0][1] == st.PLAIN
    assert st.spans("plain text")[0] == ("plain text", st.PLAIN)

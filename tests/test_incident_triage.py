"""Incident triage reads what Windows logged and says what it means -- and a
log it could not read is an Unknown finding, never a clean bill of health."""
from modules.security_dashboard import incident_triage as it

_NS = "xmlns='http://schemas.microsoft.com/win/2004/08/events/event'"


def ev(eid, when="2026-10-15T10:00:00.000Z", level=4, **data):
    body = "".join(f"<Data Name='{k}'>{v}</Data>" for k, v in data.items())
    return (f"<Event {_NS}><System><EventID>{eid}</EventID><Level>{level}</Level>"
            f"<TimeCreated SystemTime='{when}'/></System><EventData>{body}</EventData></Event>")


class FakeLogs:
    """wevtutil qe/gl and the Security Center query, answered from a dict."""

    def __init__(self, logs=None, refused=(), task_enabled=False, av="Windows Defender|393472"):
        self.logs = logs or {}
        self.refused = set(refused)
        self.task_enabled = task_enabled
        self.av = av
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "powershell":
            return 0, self.av, ""
        verb, channel = args[1], args[2]
        if channel in self.refused:
            return 5, "", "Failed to read events. Access is denied."
        if verb == "gl":
            return 0, f"name: {channel}\nenabled: {str(self.task_enabled).lower()}\n", ""
        query = next((a for a in args if a.startswith("/q:")), None)
        if query is None:                       # read_log's oldest-event probe
            return 0, "", ""
        wanted = [e for e in self.logs.get(channel, []) if f"EventID={e[0]}" in query]
        if "LogonType']='10'" in query:
            wanted = [e for e in wanted if "'LogonType'>10<" in e[1]]
        return 0, "".join(x for _id, x in wanted), ""


def logs(*pairs):
    out = {}
    for channel, xml in pairs:
        eid = int(xml.split("<EventID>")[1].split("<")[0])
        out.setdefault(channel, []).append((eid, xml))
    return out


def titles(t):
    return {f.key: f for f in t.findings}


def test_parse_skips_a_malformed_event_and_keeps_the_rest():
    events, bad = it.parse_events(ev(7045, ServiceName="A") + "<Event broken>" + "</Event>"
                                  + ev(7045, ServiceName="B"))
    assert [e.data["ServiceName"] for e in events] == ["A", "B"] and bad == 1


def test_a_refused_security_log_is_unknown_not_clean():
    t = it.triage(30, FakeLogs(refused={it.SECURITY}))
    f = titles(t)["coverage.Security"]
    assert f.severity == it.UNKNOWN and "not a clean result" in f.detail
    assert "administrator" in f.fix.lower()
    assert sum(1 for x in t.findings if x.key == "coverage.Security") == 1


def test_a_kernel_driver_from_temp_is_high_and_program_files_is_info_only():
    fake = FakeLogs(logs(
        (it.SYSTEM, ev(7045, ServiceName="HWiNFO", ImagePath=r"C:\Users\x\AppData\Local\Temp\hw.sys",
                       ServiceType="kernel mode driver", AccountName="")),
        (it.SYSTEM, ev(7045, ServiceName="Good", ImagePath=r'"C:\Program Files\Good\svc.exe" /service '
                       r'"C:\WINDOWS\system32\config\systemprofile\AppData\Local\x"',
                       ServiceType="user mode service", AccountName="LocalSystem"))))
    f = titles(it.triage(30, fake))
    assert f["services.suspicious"].severity == it.HIGH
    assert "HWiNFO" in f["services.suspicious"].evidence[0]
    assert all("Good" not in line for line in f["services.suspicious"].evidence)
    assert f["services.installed"].count == 2


def test_a_service_started_through_a_script_host_is_high():
    fake = FakeLogs(logs((it.SYSTEM, ev(7045, ServiceName="x", ServiceType="user mode service",
                                        ImagePath=r"%COMSPEC% /c powershell -enc AAAA"))))
    assert titles(it.triage(30, fake))["services.suspicious"].severity == it.HIGH


def test_relative_and_systemroot_driver_paths_are_not_user_writable():
    for raw in (r"\SystemRoot\System32\drivers\x.sys", r"system32\drivers\wd\KslD.sys",
                r"\??\C:\Windows\System32\drivers\y.sys",
                r"C:\ProgramData\Microsoft\Windows Defender\Platform\4.18\MsMpEng.exe"):
        assert not it.user_writable(it.normalise_image_path(raw)), raw


def test_defender_exclusions_added_are_listed_and_housekeeping_is_not():
    fake = FakeLogs(logs(
        (it.DEFENDER, ev(5007, **{"Old Value": "", "New Value":
            r"HKLM\SOFTWARE\Microsoft\Windows Defender\Exclusions\Paths\C:\Games = 0x0"})),
        (it.DEFENDER, ev(5007, **{"Old Value": r"Default\IsServiceRunning = 0x0", "New Value":
            r"HKLM\SOFTWARE\Microsoft\Windows Defender\IsServiceRunning = 0x1"}))))
    f = titles(it.triage(30, fake))
    assert f["defender.exclusions_added"].count == 1
    assert f["defender.exclusions_added"].evidence[0].endswith(r"+ Paths: C:\Games")


def test_realtime_protection_off_is_info_when_another_antivirus_took_over():
    off = logs((it.DEFENDER, ev(5001)))
    took_over = titles(it.triage(30, FakeLogs(off, av="Windows Defender|393472\nBitdefender Antivirus|266240")))
    assert took_over["defender.rtp_off"].severity == it.INFO
    assert "Bitdefender" in took_over["defender.rtp_off"].title   # "Bitdefender" is not Defender
    alone = titles(it.triage(30, FakeLogs(off, av="Windows Defender|393472")))
    assert alone["defender.rtp_off"].severity == it.HIGH


def test_av_state_decoding():
    assert it.av_products_on("A|266240\nB|393472\nC|397568") == ["A", "C"]
    assert it.av_products_on("") is None


def test_admin_group_addition_is_high_and_system_account_sync_is_ignored():
    fake = FakeLogs(logs(
        (it.SECURITY, ev(4732, TargetSid="S-1-5-32-544", TargetUserName="Administrators",
                         MemberName="-", MemberSid="S-1-5-21-9-1005", SubjectUserName="bob",
                         SubjectDomainName="PC")),
        (it.SECURITY, ev(4738, TargetUserName="me", SubjectUserSid="S-1-5-18")),
        (it.SECURITY, ev(4720, TargetUserName="evil", SubjectUserName="bob", SubjectDomainName="PC"))))
    f = titles(it.triage(30, fake))
    assert f["accounts.admin_added"].severity == it.HIGH and "bob" in f["accounts.admin_added"].evidence[0]
    assert f["accounts.created"].severity == it.HIGH
    assert "accounts.changed" not in f


def test_cleared_logs_and_audit_policy_changes_name_who():
    fake = FakeLogs(logs(
        (it.SECURITY, ev(1102, SubjectUserName="bob")),
        (it.SYSTEM, ev(104, Channel="Application", SubjectUserName="bob")),
        (it.SECURITY, ev(4719, SubjectUserName="bob",
                         SubcategoryGuid="{0cce922b-69ae-11d9-bed3-505054503030}"))))
    f = titles(it.triage(30, fake))
    assert f["trail.cleared"].severity == it.HIGH and f["trail.cleared"].count == 2
    assert "Process Creation  by bob" in f["trail.audit_policy"].evidence[0]


def test_failed_sign_ins_are_grouped_by_account_and_address():
    fails = [(it.SECURITY, ev(4625, TargetUserName="admin", IpAddress="10.0.0.9", LogonType="3"))
             for _ in range(12)]
    rdp = [(it.SECURITY, ev(4624, TargetUserName="me", IpAddress="10.0.0.5", LogonType="10"))]
    f = titles(it.triage(30, FakeLogs(logs(*fails, *rdp))))
    assert f["logons.failed"].severity == it.MEDIUM
    assert f["logons.failed"].evidence == ["admin from 10.0.0.9 (logon type 3)  x12"]
    assert f["logons.rdp"].evidence == ["me from 10.0.0.5  x1"]


def test_powershell_cdxml_wrappers_are_counted_apart():
    cdxml = ev(4104, level=3, MessageNumber="1",
               ScriptBlockText="#requires -version 3.0 [Microsoft.PowerShell.Cmdletization.Cim.X]")
    odd = ev(4104, level=3, MessageNumber="1", ScriptBlockText="IEX (New-Object Net.WebClient)")
    part2 = ev(4104, level=3, MessageNumber="2", ScriptBlockText="rest of a long block")
    f = titles(it.triage(30, FakeLogs(logs((it.POWERSHELL, cdxml), (it.POWERSHELL, odd),
                                           (it.POWERSHELL, part2)))))
    assert f["powershell.cdxml"].count == 1 and f["powershell.cdxml"].severity == it.INFO
    assert f["powershell.flagged"].count == 1 and "Net.WebClient" in f["powershell.flagged"].evidence[0]


def test_task_creation_unrecorded_is_said_out_loud():
    f = titles(it.triage(30, FakeLogs(task_enabled=False)))
    assert f["tasks.not_recorded"].severity == it.UNKNOWN
    assert "Audit & Logging" in f["tasks.not_recorded"].fix


def test_findings_are_ordered_worst_first_and_the_report_has_evidence():
    fake = FakeLogs(logs((it.SECURITY, ev(1102, SubjectUserName="bob")),
                         (it.SYSTEM, ev(7045, ServiceName="ok", ImagePath=r"C:\Program Files\a.exe"))))
    t = it.triage(30, fake)
    assert t.findings[0].severity == it.HIGH
    text = it.as_text(t)
    assert "[HIGH] An event log was cleared" in text and "by bob" in text


def test_the_window_reaches_the_query():
    fake = FakeLogs()
    it.triage(7, fake)
    queries = [a for call in fake.calls for a in call if a.startswith("/q:")]
    assert queries and all(f"<= {7 * 86_400_000}" in q for q in queries)

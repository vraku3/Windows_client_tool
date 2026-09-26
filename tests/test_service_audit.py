"""service_audit: unquoted paths, failure counts, sc qfailure parsing. No Qt."""
from modules.services_manager import service_audit as a


def test_unquoted_path_with_space_is_flagged():
    assert a.is_unquoted_path(r"C:\Program Files\Some App\svc.exe")
    assert a.is_unquoted_path(r"C:\Program Files (x86)\X\svc.exe /run")


def test_quoted_path_is_safe():
    assert not a.is_unquoted_path(r'"C:\Program Files\Some App\svc.exe" -k x')


def test_arguments_with_spaces_do_not_count():
    assert not a.is_unquoted_path(r"C:\WINDOWS\system32\svchost.exe -k LocalService -p")
    assert not a.is_unquoted_path(r"C:\Tools\svc.exe --config C:\Program Files\x.ini")


def test_empty_and_driver_paths_are_not_flagged():
    assert not a.is_unquoted_path("")
    assert not a.is_unquoted_path(r"\SystemRoot\System32\drivers\x y.sys")


def test_hijack_candidates_are_the_prefixes_windows_tries():
    got = a.hijack_candidates(r"C:\Program Files\Blizzard\svc.exe")
    assert got == [r"C:\Program.exe"]
    two = a.hijack_candidates(r"C:\Program Files\My App\svc.exe")
    assert two == [r"C:\Program.exe", r"C:\Program Files\My.exe"]


def test_executable_of_cuts_at_exe():
    assert a.executable_of(r"C:\a b\c.exe -x y") == r"C:\a b\c.exe"
    assert a.executable_of(r'"C:\a b\c.exe" -x') == r"C:\a b\c.exe"


def test_failure_counts_by_display_name():
    rows = [{"Message": "The Print Spooler service terminated unexpectedly.  It has done this 1 time(s)."},
            {"Message": "The Print Spooler service terminated unexpectedly."},
            {"Message": "The Foo service terminated with the following error: x"},
            {"Message": "unrelated"}]
    counts = a.parse_failure_counts(rows)
    assert counts == {"print spooler": 2, "foo": 1}
    assert a.failures_for(counts, "Print Spooler") == 2
    assert a.failures_for(counts, "Other") == 0


def test_unreadable_history_is_none_not_zero():
    assert a.failures_for(None, "Print Spooler") is None


QFAILURE = """[SC] QueryServiceConfig2 SUCCESS

SERVICE_NAME: Spooler
        RESET_PERIOD (in seconds)    : 3600
        REBOOT_MESSAGE               :
        COMMAND_LINE                 :
        FAILURE_ACTIONS              : RESTART -- Delay = 5000 milliseconds.
                                       RESTART -- Delay = 5000 milliseconds.
                                       NONE -- Delay = 0 milliseconds.
"""


def test_qfailure_ignores_reboot_message_line():
    info = a.parse_qfailure(QFAILURE)
    assert info["reset_period"] == "3600"
    assert len(info["actions"]) == 3
    assert all("REBOOT_MESSAGE" not in x for x in info["actions"])
    assert "RESTART" in a.describe_recovery(info)


def test_no_actions_is_stated():
    info = a.parse_qfailure("RESET_PERIOD (in seconds) : 0\n FAILURE_ACTIONS : NONE -- Delay = 0 milliseconds.")
    assert "no recovery" in a.describe_recovery(info)
    assert a.describe_recovery(None) == "could not be read"


def test_audit_lines_report_unquoted_and_crashes():
    lines = a.audit_lines({"Path": r"C:\Program Files\X\x.exe"}, 3)
    assert [sev for sev, _ in lines] == ["warning", "warning"]
    assert a.audit_lines({"Path": r"C:\Windows\x.exe"}, 0) == []


QC = """[SC] QueryServiceConfig SUCCESS

SERVICE_NAME: Spooler
        TYPE               : 110  WIN32_OWN_PROCESS (interactive)
        START_TYPE         : 4   DISABLED
        BINARY_PATH_NAME   : C:\\WINDOWS\\System32\\spoolsv.exe
        DISPLAY_NAME       : Print Spooler
        DEPENDENCIES       : RPCSS
                           : http
        SERVICE_START_NAME : LocalSystem
"""


def test_parse_qc_reads_colon_format_and_continued_dependencies():
    cfg = a.parse_qc(QC)
    assert cfg["binary_path"] == "C:\\WINDOWS\\System32\\spoolsv.exe"
    assert cfg["dependencies"] == ["RPCSS", "http"]
    assert cfg["start_name"] == "LocalSystem" and cfg["display_name"] == "Print Spooler"
    assert a.parse_qc("garbage")["dependencies"] == []


def test_query_service_config_on_a_real_service_does_not_raise():
    from modules.services_manager.services_module import query_service_config
    cfg = query_service_config("Spooler")
    assert cfg["binary_path"].lower().endswith("spoolsv.exe")
    assert "RPCSS" in [d.upper() for d in cfg["dependencies"]]


def test_real_machine_services_are_plausible():
    from modules.services_manager.services_module import get_services
    import pythoncom
    pythoncom.CoInitialize()
    rows = get_services()
    flagged = a.unquoted_services(rows)
    assert len(rows) > 100
    assert len(flagged) < 40   # most machines have a handful, never most services
    assert all("svchost" not in s["Path"].lower() for s in flagged)

"""Parsing `netsh advfirewall firewall show rule`.

Every block below is copied verbatim from a real 544-rule dump on this
machine. That matters: the parser looked fine against invented output for a
long time while silently leaving four columns blank on every real rule,
because netsh's field names are not the obvious ones and Program appears only
under `verbose`.
"""
import pytest

from modules.firewall_rules import firewall_manager_module as fw

# Real `... show rule name=all verbose` output. Note Profiles/LocalPort/
# RemotePort (no spaces), and the Program line that only verbose emits.
VERBOSE_SAMPLE = """
Rule Name:                            Microsoft Edge (mDNS-In)
----------------------------------------------------------------------
Description:                          Inbound rule for Microsoft Edge to allow mDNS traffic.
Enabled:                              Yes
Direction:                            In
Profiles:                             Domain,Private,Public
Grouping:                             Microsoft Edge
LocalIP:                              Any
RemoteIP:                             Any
Protocol:                             UDP
LocalPort:                            5353
RemotePort:                           Any
Edge traversal:                       No
Program:                              C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe
InterfaceTypes:                       Any
Security:                             NotRequired
Rule source:                          Local Setting
Action:                               Allow

Rule Name:                            PowerToys.SparseApp
----------------------------------------------------------------------
Description:                          PowerToys.SparseApp
Enabled:                              Yes
Direction:                            Out
Profiles:                             Domain,Private,Public
Grouping:                             PowerToys.SparseApp
LocalIP:                              Any
RemoteIP:                             Any
Protocol:                             Any
Edge traversal:                       No
InterfaceTypes:                       Any
Security:                             NotRequired
Rule source:                          Local Setting
Action:                               Allow

Rule Name:                            File and Printer Sharing (SMB-Out)
----------------------------------------------------------------------
Description:                          Outbound rule for File and Printer Sharing. [TCP 445]
Enabled:                              Yes
Direction:                            Out
Profiles:                             Private
Grouping:                             File and Printer Sharing
LocalIP:                              Any
RemoteIP:                             LocalSubnet
Protocol:                             TCP
LocalPort:                            Any
RemotePort:                           445
Edge traversal:                       No
Program:                              System
InterfaceTypes:                       Any
Security:                             NotRequired
Rule source:                          Local Setting
Action:                               Allow

Rule Name:                            Block wct_probe_app
----------------------------------------------------------------------
Enabled:                              Yes
Direction:                            Out
Profiles:                             Domain,Private,Public
Grouping:
LocalIP:                              Any
RemoteIP:                             Any
Protocol:                             Any
Edge traversal:                       No
Program:                              C:\\Temp\\probe\\wct_probe_app.exe
InterfaceTypes:                       Any
Security:                             NotRequired
Rule source:                          Local Setting
Action:                               Block
"""


@pytest.fixture
def rules():
    return fw._parse_rules(VERBOSE_SAMPLE)


def test_every_block_becomes_a_rule(rules):
    assert [r.name for r in rules] == [
        "Microsoft Edge (mDNS-In)",
        "PowerToys.SparseApp",
        "File and Printer Sharing (SMB-Out)",
        "Block wct_probe_app",
    ]


def test_the_program_path_is_captured(rules):
    """This is the field the whole unblock feature hangs off; it was blank for
    every rule until fetch started passing `verbose`."""
    assert rules[0].program == (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    )


def test_profiles_is_captured_despite_the_plural(rules):
    assert rules[0].profile == "Domain,Private,Public"
    assert rules[2].profile == "Private"


def test_ports_are_captured_despite_the_missing_space(rules):
    assert (rules[0].local_port, rules[0].remote_port) == ("5353", "Any")
    assert (rules[2].local_port, rules[2].remote_port) == ("Any", "445")


def test_remaining_fields_survive(rules):
    r = rules[3]
    assert (r.enabled, r.direction, r.action, r.protocol) == ("Yes", "Out", "Block", "Any")


def test_a_rule_with_no_program_line_gets_an_empty_program(rules):
    assert rules[1].program == ""


def test_a_program_less_rule_is_never_unblock_material(rules):
    """A blank or non-path Program must not be selectable by a folder sweep —
    it would delete a machine-wide rule the user never pointed at."""
    assert fw._is_program_scoped(rules[1]) is False   # no Program line
    assert fw._is_program_scoped(rules[2]) is False   # Program: System


def test_system_is_treated_as_non_path_not_as_a_relative_path(rules):
    """144 of the 544 real rules here say Program: System (kernel-mode
    traffic). It is a literal, not something living under any folder — so a
    sweep of the whole drive must find the probe rule and nothing else."""
    system_rule = fw._parse_rules(
        VERBOSE_SAMPLE.replace("Action:                               Allow",
                               "Action:                               Block")
    )[2]
    assert system_rule.program == "System"

    found = fw.find_block_rules_in_folder(rules + [system_rule], "C:\\")
    assert [r.name for r in found] == ["Block wct_probe_app"]


def test_a_real_block_rule_is_found_by_its_program(rules):
    found = fw.find_block_rules_for_program(rules, r"C:\Temp\probe\wct_probe_app.exe")
    assert [r.name for r in found] == ["Block wct_probe_app"]


def test_the_fetch_command_asks_for_verbose(monkeypatch):
    """Without `verbose` netsh omits Program entirely and the feature silently
    finds nothing — pin the flag so it cannot be dropped again."""
    captured = {}

    class _Proc:
        returncode, stdout, stderr = 0, VERBOSE_SAMPLE, ""

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _Proc()

    monkeypatch.setattr(fw.subprocess, "run", fake_run)
    assert len(fw.fetch_firewall_rules()) == 4
    assert "verbose" in captured["args"]
    assert "name=all" in captured["args"]


@pytest.mark.parametrize("rc, out", [
    (1, "The requested operation requires elevation.\n"),
    (0, "Nom de la règle:   Foo\nActivé:   Oui\n"),   # localised field names
    (0, ""),
])
def test_a_read_with_no_rules_raises_instead_of_returning_empty(monkeypatch, rc, out):
    """Deletes are verified against this list: an empty one would confirm a
    delete that never happened."""
    class _Proc:
        returncode, stdout, stderr = rc, out, ""

    monkeypatch.setattr(fw.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(RuntimeError, match="could not read the firewall rules"):
        fw.fetch_firewall_rules()


def test_plain_non_verbose_output_yields_no_program(monkeypatch):
    """Documents the failure this fixes: the same rule, without verbose, has
    no Program line at all — so nothing is ever unblockable."""
    plain = "\n".join(
        line for line in VERBOSE_SAMPLE.split("\n") if not line.startswith("Program:")
    )
    parsed = fw._parse_rules(plain)
    assert len(parsed) == 4
    assert all(r.program == "" for r in parsed)
    assert fw.find_block_rules_for_program(
        parsed, r"C:\Temp\probe\wct_probe_app.exe"
    ) == []

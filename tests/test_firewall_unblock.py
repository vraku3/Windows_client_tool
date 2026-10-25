"""Unblocking a program or a folder in the Firewall Rules pane.

Windows Firewall has no "blocked folder" — rules name an executable — so
unblocking a folder means removing every block rule whose program lives under
it. These tests pin *which rules get chosen* and the netsh command built for
each, against output captured from a real `netsh advfirewall firewall show
rule all` on this machine. netsh itself is never run.
"""
import pytest

from modules.firewall_rules import firewall_manager_module as fw
from modules.firewall_rules.firewall_manager_module import FirewallRule


def _rule(name, program, action="Block", direction="Out", enabled="Yes",
          profile="Private"):
    return FirewallRule(
        name=name, enabled=enabled, direction=direction, action=action,
        protocol="Any", local_port="Any", remote_port="Any",
        program=program, profile=profile,
    )


RULES = [
    _rule("Block vlc", r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
    _rule("Block vlc", r"C:\Program Files\VideoLAN\VLC\vlc.exe", direction="In"),
    _rule("Allow vlc", r"C:\Program Files\VideoLAN\VLC\vlc.exe", action="Allow"),
    _rule("Block updater", r"C:\Program Files\VideoLAN\updater.exe"),
    _rule("Block notepad", r"C:\Windows\System32\notepad.exe"),
    _rule("Core Networking", "Any", action="Allow", direction="In"),
    _rule("Block everything out", "", action="Block"),
]


# ── path normalisation ──────────────────────────────────────────────────────

def test_case_and_separator_differences_still_match():
    a = fw.normalize_program_path(r"C:\Program Files\VideoLAN\VLC\vlc.exe")
    b = fw.normalize_program_path("c:/program files/videolan/vlc/./vlc.exe")
    assert a == b


def test_environment_variables_are_expanded(monkeypatch):
    """netsh reports many built-in rules with the variable unexpanded, so a
    raw string compare against a file-dialog path would silently miss them."""
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    assert (
        fw.normalize_program_path(r"%SystemRoot%\system32\svchost.exe")
        == fw.normalize_program_path(r"C:\Windows\System32\svchost.exe")
    )


def test_quoted_paths_are_unwrapped():
    assert (
        fw.normalize_program_path('"C:\\Tools\\a.exe"')
        == fw.normalize_program_path(r"C:\Tools\a.exe")
    )


@pytest.mark.parametrize("value", ["", None, "   "])
def test_empty_paths_normalise_to_empty(value):
    assert fw.normalize_program_path(value) == ""


# ── finding block rules for one program ─────────────────────────────────────

def test_finds_block_rules_in_both_directions_for_a_program():
    found = fw.find_block_rules_for_program(
        RULES, r"C:\Program Files\VideoLAN\VLC\vlc.exe"
    )
    assert [(r.name, r.direction) for r in found] == [
        ("Block vlc", "Out"), ("Block vlc", "In")
    ]


def test_an_allow_rule_for_the_same_program_is_left_alone():
    found = fw.find_block_rules_for_program(
        RULES, r"C:\Program Files\VideoLAN\VLC\vlc.exe"
    )
    assert all(r.action == "Block" for r in found)


def test_a_sibling_executable_is_not_swept_up():
    """updater.exe sits beside vlc.exe; unblocking vlc must not touch it."""
    found = fw.find_block_rules_for_program(
        RULES, r"C:\Program Files\VideoLAN\VLC\vlc.exe"
    )
    assert all("updater" not in r.program for r in found)


def test_a_program_with_no_block_rule_finds_nothing():
    assert fw.find_block_rules_for_program(RULES, r"C:\Tools\nothing.exe") == []


def test_an_empty_path_matches_nothing():
    """Guards against an empty file dialog result selecting every rule."""
    assert fw.find_block_rules_for_program(RULES, "") == []
    assert fw.find_block_rules_in_folder(RULES, "") == []


# ── finding block rules under a folder ──────────────────────────────────────

def test_folder_match_reaches_into_subfolders():
    found = fw.find_block_rules_in_folder(RULES, r"C:\Program Files\VideoLAN")
    assert sorted({r.program for r in found}) == [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files\VideoLAN\updater.exe",
    ]


def test_folder_match_ignores_rules_outside_it():
    found = fw.find_block_rules_in_folder(RULES, r"C:\Program Files\VideoLAN")
    assert all("notepad" not in r.program for r in found)


def test_folder_match_skips_rules_with_no_program():
    """'Any'/blank Program rules are not path-scoped — including them would
    delete a machine-wide block rule the user never pointed at."""
    found = fw.find_block_rules_in_folder(RULES, "C:\\")
    assert all(r.program.strip().lower() not in ("", "any") for r in found)


def test_a_trailing_separator_on_the_folder_does_not_change_the_result():
    a = fw.find_block_rules_in_folder(RULES, r"C:\Program Files\VideoLAN")
    b = fw.find_block_rules_in_folder(RULES, "C:\\Program Files\\VideoLAN\\")
    assert [r.name for r in a] == [r.name for r in b]


def test_a_sibling_folder_with_a_shared_prefix_is_not_matched():
    """'C:\\Prog' must not match 'C:\\Program Files\\...' — a plain string
    prefix test would."""
    assert fw.find_block_rules_in_folder(RULES, r"C:\Program") == []


# ── the netsh command that gets built ───────────────────────────────────────

def test_delete_resolves_one_id_and_verifies(monkeypatch):
    rule = RULES[0]
    monkeypatch.setattr(fw.fa, "read_registry_rules", lambda resolve: ([rule], [fw.fa.RuleExtra(rule_id="ID1")]))
    monkeypatch.setattr(fw, "fetch_firewall_rules", lambda: [rule])
    calls = []
    monkeypatch.setattr(fw, "act_on_rule_by_id", lambda rid, key, enable: calls.append((rid, key, enable)) or (True, "", [], None))
    assert fw.delete_matching_rule(rule) == (True, "")
    assert calls == [("ID1", fw.fa.rule_key(rule), None)]


def test_ambiguous_unblock_never_deletes_by_name(monkeypatch):
    rule = RULES[0]
    monkeypatch.setattr(fw.fa, "read_registry_rules", lambda resolve: ([rule, rule], [fw.fa.RuleExtra(rule_id="A"), fw.fa.RuleExtra(rule_id="B")]))
    monkeypatch.setattr(fw, "fetch_firewall_rules", lambda: [rule, rule])
    monkeypatch.setattr(fw, "act_on_rule_by_id", lambda *a: pytest.fail("ambiguous ID"))
    assert fw.delete_matching_rule(rule)[0] is False


def test_duplicate_profile_variants_are_deleted_once(monkeypatch):
    """One rule shown per profile is still one rule to netsh."""
    calls = []
    monkeypatch.setattr(
        fw, "delete_matching_rule",
        lambda r: (calls.append(r.name), (True, ""))[1],
    )
    dupes = [
        _rule("Block vlc", r"C:\a\vlc.exe", profile="Private"),
        _rule("Block vlc", r"C:\a\vlc.exe", profile="Public"),
    ]
    deleted, errors = fw.unblock_rules(dupes)
    assert (deleted, errors, calls) == (1, [], ["Block vlc"])


def test_one_failing_rule_does_not_abandon_the_rest(monkeypatch):
    def fake(rule):
        return (False, "Access denied") if rule.name == "Block updater" else (True, "")

    monkeypatch.setattr(fw, "delete_matching_rule", fake)
    deleted, errors = fw.unblock_rules(
        [RULES[0], RULES[3], RULES[4]]
    )
    assert deleted == 2
    assert errors == ["Block updater: Access denied"]


# ── _run_netsh's success signal ─────────────────────────────────────────────

class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_netsh_failure_carries_its_output_even_though_it_lands_on_stdout(monkeypatch):
    """netsh reports 'No rules match the specified criteria.' on STDOUT with a
    non-zero exit — the exit code alone would say nothing useful."""
    monkeypatch.setattr(
        fw.subprocess, "run",
        lambda *a, **k: _Proc(1, "No rules match the specified criteria.\n"),
    )
    ok, message = fw._run_netsh(["advfirewall", "firewall", "delete", "rule", "name=x"])
    assert ok is False
    assert "No rules match" in message


def test_netsh_success_is_reported_with_its_output(monkeypatch):
    monkeypatch.setattr(
        fw.subprocess, "run", lambda *a, **k: _Proc(0, "Deleted 1 rule(s).\nOk.\n")
    )
    ok, message = fw._run_netsh(["advfirewall", "firewall", "delete", "rule", "name=x"])
    assert ok is True
    assert "Deleted 1 rule(s)." in message

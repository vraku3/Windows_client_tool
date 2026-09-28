"""Behaviour of the tweak-vs-Group-Policy cross-reference.

Every test here injects both inputs, so nothing depends on whether the machine
running pytest happens to have a local GPO.
"""

import json
import os

import pytest

from modules.gpresult.pol_parser import PolFile, PolicyValue
from modules.gpresult.tweak_conflicts import (
    AGREE_CONFLICT,
    AGREE_DUPLICATE,
    AGREE_UNCOMPARABLE,
    MATCH_BRANCH,
    MATCH_DIRECT,
    MATCH_SAME_KEY,
    ConflictReport,
    default_definitions_dir,
    effective_value_name,
    find_conflicts,
    is_policy_managed_key,
    load_tweak_definitions,
    normalise_key,
    policy_key_path,
    policy_managed_tweak_keys,
    registry_steps,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def tweak(tweak_id, *steps, **extra):
    entry = {"id": tweak_id, "name": tweak_id.replace("_", " ").title(),
             "category": extra.pop("category", "Privacy"), "steps": list(steps)}
    entry.update(extra)
    return entry


def reg(key, value="", data=None, kind="DWORD"):
    return {"type": "registry", "key": key, "value": value, "data": data, "kind": kind}


def reg_delete(key, value):
    return {"type": "registry_delete", "key": key, "value": value}


def machine_pol(*values, **kwargs):
    return PolFile(path=kwargs.get("path", r"X:\Machine\Registry.pol"),
                   scope="Computer", hive="HKLM", exists=True, values=list(values))


def user_pol(*values):
    return PolFile(path=r"X:\User\Registry.pol", scope="User", hive="HKCU",
                   exists=True, values=list(values))


def pv(key, value_name="", data=None, type_id=4, directive=""):
    return PolicyValue(key=key, value_name=value_name, type_id=type_id,
                       data=data, directive=directive)


CLOUD = r"Software\Policies\Microsoft\Windows\CloudContent"


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------

def test_hive_aliases_and_casing_normalise_to_one_comparable_form():
    assert normalise_key(r"HKEY_LOCAL_MACHINE\Software\Policies") == r"HKLM\SOFTWARE\POLICIES"
    assert normalise_key(r"hklm\software\policies") == r"HKLM\SOFTWARE\POLICIES"
    assert normalise_key(r"HKEY_CURRENT_USER\Software") == r"HKCU\SOFTWARE"
    assert normalise_key(r"HKCU\Software\\") == r"HKCU\SOFTWARE"


def test_an_empty_or_missing_key_normalises_to_nothing_rather_than_a_bare_hive():
    assert normalise_key("") == ""
    assert normalise_key("   ") == ""


def test_a_machine_pol_record_is_read_as_hklm_and_a_user_record_as_hkcu():
    # Registry.pol stores keys hive-relative; the hive comes from the file.
    value = pv(CLOUD, "DisableWindowsConsumerFeatures", 1)
    assert policy_key_path(machine_pol(), value).startswith("HKLM\\")
    assert policy_key_path(user_pol(), value).startswith("HKCU\\")


def test_only_keys_inside_a_policies_branch_count_as_policy_managed():
    assert is_policy_managed_key(r"HKLM\SOFTWARE\Policies\Microsoft\Windows")
    assert is_policy_managed_key(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer")
    assert not is_policy_managed_key(r"HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer")


def test_a_delete_directive_names_the_value_it_deletes_not_itself():
    value = pv(CLOUD, "**del.DisableWindowsSpotlightFeatures", directive="delete_value")
    assert effective_value_name(value) == "DisableWindowsSpotlightFeatures"


def test_a_key_level_directive_names_no_value_at_all():
    assert effective_value_name(pv(CLOUD, "**delvals.", directive="delete_all_values")) == ""


# --------------------------------------------------------------------------
# match classification
# --------------------------------------------------------------------------

def test_same_key_and_value_is_a_direct_match_despite_differing_case_and_hive_spelling():
    report = find_conflicts(
        tweaks=[tweak("t", reg(r"HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Microsoft\Windows\CloudContent",
                               "DisableWindowsConsumerFeatures", 1))],
        pol_files=[machine_pol(pv(CLOUD, "disablewindowsconsumerfeatures", 0))])
    assert [c.match for c in report.conflicts] == [MATCH_DIRECT]


def test_a_different_value_under_the_same_key_is_a_same_key_match():
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD, "ConfigureWindowsSpotlight", 0))],
        pol_files=[machine_pol(pv(CLOUD, "DisableWindowsConsumerFeatures", 1))])
    assert [c.match for c in report.conflicts] == [MATCH_SAME_KEY]


def test_a_tweak_key_under_a_managed_key_is_a_branch_match():
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD + r"\Nested", "Anything", 1))],
        pol_files=[machine_pol(pv(CLOUD, "DisableWindowsConsumerFeatures", 1))])
    assert [c.match for c in report.conflicts] == [MATCH_BRANCH]


def test_a_tweak_key_above_a_managed_key_is_also_a_branch_match():
    report = find_conflicts(
        tweaks=[tweak("t", reg(r"HKLM\Software\Policies\Microsoft\Windows", "Foo", 1))],
        pol_files=[machine_pol(pv(CLOUD, "DisableWindowsConsumerFeatures", 1))])
    assert [c.match for c in report.conflicts] == [MATCH_BRANCH]


def test_a_key_that_merely_shares_a_name_prefix_is_not_a_branch_match():
    # `...\CloudContentSomethingElse` is a sibling, not a child.
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD + "Extra", "Foo", 1))],
        pol_files=[machine_pol(pv(CLOUD, "DisableWindowsConsumerFeatures", 1))])
    assert report.conflicts == []


def test_the_same_path_in_a_different_hive_is_not_a_conflict():
    # A User pol record is HKCU; an HKLM tweak writing the same relative path
    # is a different registry location entirely.
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD, "DisableWindowsConsumerFeatures", 1))],
        pol_files=[user_pol(pv(CLOUD, "DisableWindowsConsumerFeatures", 1))])
    assert report.conflicts == []


def test_several_managed_values_under_one_branch_report_one_row_per_branch_key():
    # Five SrpV2 values under one parent must not bury a direct hit under five
    # near-identical branch rows.
    srp = r"Software\Policies\Microsoft\Windows\SrpV2"
    report = find_conflicts(
        tweaks=[tweak("t", reg(r"HKLM\Software\Policies\Microsoft\Windows\SrpV2\Exe", "Other", 1))],
        pol_files=[machine_pol(*[pv(srp + "\\Exe", "AllowWindows", 0)] * 5)])
    assert len(report.conflicts) == 5      # exact-key hits are per value...
    report = find_conflicts(
        tweaks=[tweak("t", reg(r"HKLM\Software\Policies\Microsoft\Windows\SrpV2", "Other", 1))],
        pol_files=[machine_pol(*[pv(srp + "\\Exe", "AllowWindows", 0)] * 5)])
    assert len(report.conflicts) == 1      # ...branch hits are per key


def test_non_registry_steps_are_never_matched():
    report = find_conflicts(
        tweaks=[tweak("t", {"type": "service", "name": "DiagTrack", "start_type": "disabled"},
                      {"type": "command", "cmd": "echo hi"})],
        pol_files=[machine_pol(pv(CLOUD, "DisableWindowsConsumerFeatures", 1))])
    assert report.conflicts == []
    assert report.registry_steps == 0


# --------------------------------------------------------------------------
# agreement: duplicating policy and fighting policy are different findings
# --------------------------------------------------------------------------

def test_writing_the_same_data_as_policy_is_reported_as_duplicating_not_conflicting():
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD, "DisableWindowsConsumerFeatures", 1))],
        pol_files=[machine_pol(pv(CLOUD, "DisableWindowsConsumerFeatures", 1))])
    assert report.conflicts[0].agreement == AGREE_DUPLICATE
    assert "redundant" in report.conflicts[0].summary()


def test_writing_different_data_than_policy_is_reported_as_conflicting():
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD, "DisableWindowsConsumerFeatures", 1))],
        pol_files=[machine_pol(pv(CLOUD, "DisableWindowsConsumerFeatures", 0))])
    assert report.conflicts[0].agreement == AGREE_CONFLICT
    assert report.direct_conflicts == report.conflicts


def test_a_dword_written_as_a_json_string_still_counts_as_agreeing():
    # The definitions use both `"data": 1` and `"data": "1"` for DWORDs.
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD, "DisableWindowsConsumerFeatures", "1"))],
        pol_files=[machine_pol(pv(CLOUD, "DisableWindowsConsumerFeatures", 1))])
    assert report.conflicts[0].agreement == AGREE_DUPLICATE


def test_string_data_agrees_case_insensitively():
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD, "Level", "block", kind="SZ"))],
        pol_files=[machine_pol(pv(CLOUD, "Level", "Block", type_id=1))])
    assert report.conflicts[0].agreement == AGREE_DUPLICATE


def test_a_delete_step_against_a_value_policy_sets_is_a_conflict():
    report = find_conflicts(
        tweaks=[tweak("t", reg_delete("HKLM\\" + CLOUD, "DisableWindowsConsumerFeatures"))],
        pol_files=[machine_pol(pv(CLOUD, "DisableWindowsConsumerFeatures", 1))])
    conflict = report.conflicts[0]
    assert (conflict.match, conflict.agreement) == (MATCH_DIRECT, AGREE_CONFLICT)
    assert "removes" in conflict.summary()


def test_a_delete_step_against_a_value_policy_also_deletes_is_a_duplicate():
    report = find_conflicts(
        tweaks=[tweak("t", reg_delete("HKLM\\" + CLOUD, "Spotlight"))],
        pol_files=[machine_pol(pv(CLOUD, "**del.Spotlight", " ", type_id=1,
                                  directive="delete_value"))])
    conflict = report.conflicts[0]
    assert (conflict.match, conflict.agreement) == (MATCH_DIRECT, AGREE_DUPLICATE)


def test_policy_deleting_the_exact_value_a_tweak_writes_is_a_direct_conflict():
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD, "Spotlight", 1))],
        pol_files=[machine_pol(pv(CLOUD, "**del.Spotlight", " ", type_id=1,
                                  directive="delete_value"))])
    conflict = report.conflicts[0]
    assert (conflict.match, conflict.agreement) == (MATCH_DIRECT, AGREE_CONFLICT)
    assert "delete value" in conflict.summary()


def test_matches_that_cannot_compare_data_say_so_instead_of_guessing():
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD, "Other", 1))],
        pol_files=[machine_pol(pv(CLOUD, "DisableWindowsConsumerFeatures", 1))])
    assert report.conflicts[0].agreement == AGREE_UNCOMPARABLE


# --------------------------------------------------------------------------
# wording: the claim must stay honest
# --------------------------------------------------------------------------

def test_summaries_say_a_tweak_can_be_reverted_never_that_it_is_reverted_on_a_timer():
    # The Registry CSE skips processing while no GPO has changed, so a hand
    # written policy value can survive for weeks. Overstating that is the bug.
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD, "DisableWindowsConsumerFeatures", 1)),
                tweak("u", reg("HKLM\\" + CLOUD, "Other", 1)),
                tweak("v", reg("HKLM\\" + CLOUD + r"\Sub", "Deep", 1))],
        pol_files=[machine_pol(pv(CLOUD, "DisableWindowsConsumerFeatures", 0))])
    text = " ".join(c.summary() for c in report.conflicts).lower()
    assert "without warning" in text
    for overstatement in ("90 minutes", "every", "will be reverted", "always"):
        assert overstatement not in text


def test_a_machine_with_no_policy_files_reports_zero_conflicts_and_says_why():
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD, "DisableWindowsConsumerFeatures", 1))],
        pol_files=[PolFile(path=r"X:\Machine\Registry.pol", scope="Computer", hive="HKLM"),
                   PolFile(path=r"X:\User\Registry.pol", scope="User", hive="HKCU")])
    assert report.conflicts == []
    assert report.policy_values == 0
    assert report.policy_branch_steps == 1
    assert "No local Group Policy" in report.headline()


def test_an_unparsable_policy_file_is_carried_as_a_note_not_swallowed():
    broken = PolFile(path=r"X:\Machine\Registry.pol", scope="Computer", hive="HKLM",
                     exists=True, error="Could not parse X: bad magic")
    report = find_conflicts(tweaks=[tweak("t", reg("HKLM\\" + CLOUD, "Foo", 1))],
                            pol_files=[broken])
    assert any("bad magic" in note for note in report.notes)


def test_an_unreadable_definition_file_is_carried_as_a_note_not_swallowed(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    notes = []
    assert load_tweak_definitions(str(tmp_path), notes) == []
    assert any("broken.json" in note for note in notes)


def test_a_missing_definitions_directory_is_reported_rather_than_raising(tmp_path):
    notes = []
    assert load_tweak_definitions(str(tmp_path / "nope"), notes) == []
    assert any("not found" in note for note in notes)


# --------------------------------------------------------------------------
# report shape
# --------------------------------------------------------------------------

def test_conflicts_are_ordered_tightest_match_and_real_disagreement_first():
    report = find_conflicts(
        tweaks=[tweak("branch", reg("HKLM\\" + CLOUD + r"\Sub", "Deep", 1)),
                tweak("same_key", reg("HKLM\\" + CLOUD, "Other", 1)),
                tweak("duplicate", reg("HKLM\\" + CLOUD, "Flag", 1)),
                tweak("direct", reg("HKLM\\" + CLOUD, "Flag", 0))],
        pol_files=[machine_pol(pv(CLOUD, "Flag", 1))])
    assert [c.tweak_id for c in report.conflicts] == [
        "direct", "duplicate", "same_key", "branch"]


def test_a_tweak_hitting_policy_twice_counts_once_in_the_at_risk_total():
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKLM\\" + CLOUD, "A", 0), reg("HKLM\\" + CLOUD, "B", 0))],
        pol_files=[machine_pol(pv(CLOUD, "A", 1), pv(CLOUD, "B", 1))])
    assert len(report.conflicts) > report.tweaks_at_risk == 1
    assert list(report.by_tweak()) == ["t"]


def test_each_conflict_names_the_scope_and_the_file_it_came_from():
    report = find_conflicts(
        tweaks=[tweak("t", reg("HKCU\\" + CLOUD, "Flag", 0))],
        pol_files=[user_pol(pv(CLOUD, "Flag", 1))])
    conflict = report.conflicts[0]
    assert (conflict.scope, conflict.hive) == ("User", "HKCU")
    assert conflict.policy_source.endswith("Registry.pol")


def test_the_step_index_points_back_at_the_step_that_matched():
    report = find_conflicts(
        tweaks=[tweak("t", reg(r"HKLM\Software\Elsewhere", "X", 1),
                      reg("HKLM\\" + CLOUD, "Flag", 0))],
        pol_files=[machine_pol(pv(CLOUD, "Flag", 1))])
    assert report.conflicts[0].step_index == 1


def test_an_empty_report_is_still_a_report():
    empty = ConflictReport()
    assert empty.tweaks_at_risk == 0
    assert empty.by_tweak() == {}
    assert empty.headline()


# --------------------------------------------------------------------------
# against the app's own definitions
# --------------------------------------------------------------------------

def test_the_real_definitions_load_without_notes_and_carry_registry_steps():
    notes = []
    tweaks = load_tweak_definitions(notes=notes)
    assert notes == []
    assert len(tweaks) > 500
    assert len(registry_steps(tweaks)) > 500


def test_the_real_definitions_are_dominated_by_managed_policy_branches():
    # The hazard this module exists for: hundreds of steps writing into keys
    # the Registry CSE owns. If this ever drops to zero, the module is moot.
    report = find_conflicts(pol_files=[])
    assert report.policy_branch_steps > 200
    assert report.policy_branch_steps < report.registry_steps


def test_the_real_definitions_conflict_with_a_synthetic_policy_over_their_own_keys():
    # Proves the matcher finds real conflicts when they exist, without relying
    # on the test machine having any local GPO at all.
    report = find_conflicts(pol_files=[machine_pol(
        pv(CLOUD, "DisableWindowsConsumerFeatures", 0))])
    assert report.tweaks_at_risk > 0
    assert any(c.match == MATCH_DIRECT and c.agreement == AGREE_CONFLICT
               for c in report.conflicts)


def test_loading_the_definitions_is_not_done_at_import_time():
    # ~800 definitions is not a cost to pay for `import`; a Qt pane imports
    # this module long before anyone opens the tab.
    source = open(os.path.join(default_definitions_dir(), os.pardir, os.pardir,
                               "gpresult", "tweak_conflicts.py"), encoding="utf-8").read()
    calls_at_module_level = [
        line for line in source.splitlines()
        if line and not line[0].isspace() and not line.startswith(("def ", "from ", "import "))
        and ("load_tweak_definitions(" in line or "local_policy_files(" in line)]
    assert calls_at_module_level == []


def test_definitions_that_are_not_tweak_lists_are_skipped_quietly(tmp_path):
    # `app_catalog.json` and the preset files under `builtins/` are neither
    # tweaks nor errors, and must not appear as notes the user cannot act on.
    (tmp_path / "app_catalog.json").write_text(json.dumps([{"id": "firefox"}]), encoding="utf-8")
    (tmp_path / "preset.json").write_text(json.dumps({"name": "Balanced"}), encoding="utf-8")
    (tmp_path / "real.json").write_text(
        json.dumps([tweak("t", reg("HKLM\\" + CLOUD, "Flag", 1))]), encoding="utf-8")
    notes = []
    loaded = load_tweak_definitions(str(tmp_path), notes)
    assert [t["id"] for t in loaded] == ["t"]
    assert notes == []
    assert loaded[0]["_source_file"] == "real.json"


# --------------------------------------------------------------------------
# policy_managed_tweak_keys -- what tweaks_module.py's GPO badge is built on
# --------------------------------------------------------------------------

def test_a_tweak_with_no_policy_branch_steps_is_absent_from_the_map():
    tweaks = [tweak("t1", reg(r"HKLM\Software\Vendor\App", "Setting", 1))]
    assert policy_managed_tweak_keys(tweaks=tweaks) == {}


def test_a_tweak_writing_into_a_policies_branch_is_mapped_by_id():
    tweaks = [tweak("disable_consumer_features", reg(CLOUD, "DisableWindowsConsumerFeatures", 1))]
    result = policy_managed_tweak_keys(tweaks=tweaks)
    assert list(result.keys()) == ["disable_consumer_features"]
    assert result["disable_consumer_features"] == [CLOUD]


def test_several_steps_under_the_same_managed_key_are_not_duplicated():
    tweaks = [tweak("t1", reg(CLOUD, "A", 1), reg(CLOUD, "B", 1))]
    result = policy_managed_tweak_keys(tweaks=tweaks)
    assert result["t1"] == [CLOUD]


def test_a_registry_delete_step_into_a_managed_branch_still_counts():
    tweaks = [tweak("t1", reg_delete(CLOUD, "SomeValue"))]
    result = policy_managed_tweak_keys(tweaks=tweaks)
    assert result["t1"] == [CLOUD]


def test_non_registry_steps_are_ignored_by_the_policy_map():
    tweaks = [tweak("t1", {"type": "service", "name": "Fax", "start_type": "disabled"})]
    assert policy_managed_tweak_keys(tweaks=tweaks) == {}


def test_a_tweak_with_no_id_is_never_mapped():
    tweaks = [{"name": "no id", "steps": [reg(CLOUD, "X", 1)]}]
    assert policy_managed_tweak_keys(tweaks=tweaks) == {}


def test_real_definitions_have_real_policy_managed_tweaks():
    # Confirmed live: this app's own ~700 real tweaks include a real,
    # measurable number of registry steps under a `\Policies\` branch --
    # this is what tweaks_module.py's GPO badge warns about, so the map
    # must not come back empty against the real definition files.
    result = policy_managed_tweak_keys()
    assert len(result) > 0
    assert all(keys for keys in result.values())


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

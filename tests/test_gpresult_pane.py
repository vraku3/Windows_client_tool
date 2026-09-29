"""The Group Policy pane, driven through the real widget.

The complaint that started this: "I see only one, 'Local Group Policy', that
does nothing if I double-click it, it does not expand". That was literally
true -- the rows were built as `QTreeWidgetItem([name, guid])` with no
children ever added, so there was nothing to expand into. These tests assert
the tree now has the depth the report actually contains.
"""
import pytest
from PyQt6.QtWidgets import QTreeWidgetItem

from modules.gpresult.gpresult_module import GPResultModule
from modules.gpresult.rsop_parser import RsopResult, parse_rsop_xml

from tests.test_gpresult_rsop import REAL_USER_ONLY, SETTINGS_XML


@pytest.fixture
def pane(qapp):
    module = GPResultModule()
    module.on_start(None)
    module.create_widget()
    return module


def _roots(pane):
    return {pane._tree.topLevelItem(i).text(0): pane._tree.topLevelItem(i)
            for i in range(pane._tree.topLevelItemCount())}


def _child(item: QTreeWidgetItem, label: str) -> QTreeWidgetItem:
    for i in range(item.childCount()):
        if item.child(i).text(0) == label:
            return item.child(i)
    raise AssertionError("no child %r under %r (has: %s)" % (
        label, item.text(0),
        [item.child(i).text(0) for i in range(item.childCount())]))


def _walk(item):
    yield item
    for i in range(item.childCount()):
        yield from _walk(item.child(i))


def _all_items(pane):
    for i in range(pane._tree.topLevelItemCount()):
        yield from _walk(pane._tree.topLevelItem(i))


# ------------------------------------------------------------------
# Structure
# ------------------------------------------------------------------

def test_computer_and_user_are_separate_roots(pane):
    pane._on_result(parse_rsop_xml(SETTINGS_XML))
    assert set(_roots(pane)) == {"Computer Configuration", "User Configuration"}


def test_a_gpo_row_expands(pane):
    """The original complaint. A GPO carries its GUID, link, order and
    enforcement -- all of which were parsed and then thrown away."""
    pane._on_result(parse_rsop_xml(REAL_USER_ONLY))
    gpos = _child(_roots(pane)["User Configuration"], "Applied GPOs")
    local = _child(gpos, "Local Group Policy")
    assert local.childCount() > 0
    labels = {local.child(i).text(0) for i in range(local.childCount())}
    assert {"GUID", "Link", "Applied order"} <= labels
    assert _child(local, "GUID").text(1) == "LocalGPO"


def test_settings_are_nested_by_their_category_path(pane):
    """gpresult reports "Windows Components/Windows Error Reporting"; the
    tree should nest that the way gpedit does, not print it as one string."""
    pane._on_result(parse_rsop_xml(SETTINGS_XML))
    settings = _child(_roots(pane)["Computer Configuration"], "Settings")
    components = _child(settings, "Windows Components")
    reporting = _child(components, "Windows Error Reporting")
    policy = _child(reporting, "Turn off Windows Error Reporting")
    assert policy.text(1) == "Enabled"
    assert policy.text(2) == "Local Group Policy"


def test_a_setting_expands_into_its_full_detail(pane):
    pane._on_result(parse_rsop_xml(SETTINGS_XML))
    names = {i.text(0) for i in _all_items(pane)}
    assert "Software\\Policies\\Microsoft\\Windows\\SrpV2\\Exe\\AllowWindows" in names
    assert "KeyPath" in names


def test_denied_gpos_get_their_own_section(pane):
    pane._on_result(parse_rsop_xml(SETTINGS_XML))
    denied = _child(_roots(pane)["Computer Configuration"], "Denied GPOs")
    assert denied.text(1) == "2"
    assert _child(denied, "Filtered Out").text(1) == "Denied by security filtering"
    assert _child(denied, "Unreadable").text(1) == "Access denied"


def test_no_denied_section_when_every_gpo_applied(pane):
    pane._on_result(parse_rsop_xml(REAL_USER_ONLY))
    user = _roots(pane)["User Configuration"]
    labels = {user.child(i).text(0) for i in range(user.childCount())}
    assert "Denied GPOs" not in labels


def test_security_groups_and_extension_status_are_shown(pane):
    pane._on_result(parse_rsop_xml(REAL_USER_ONLY))
    user = _roots(pane)["User Configuration"]
    assert _child(user, "Security Groups").text(1) == "1"
    status = _child(user, "Extension Status")
    assert _child(status, "Group Policy Infrastructure").text(1) == "Complete"


def test_a_failed_extension_shows_its_error_code(pane):
    pane._on_result(parse_rsop_xml(SETTINGS_XML))
    status = _child(_roots(pane)["Computer Configuration"], "Extension Status")
    assert "error 2" in _child(status, "Security").text(1)


# ------------------------------------------------------------------
# The refusal has to be visible
# ------------------------------------------------------------------

def test_an_uncollected_scope_says_why_instead_of_looking_empty(pane):
    """This is the whole defect: unelevated, the pane used to render the user
    half and nothing at all for the computer half, with no explanation."""
    result = parse_rsop_xml(REAL_USER_ONLY)
    result.computer.unavailable_reason = (
        "Computer policy was not collected: reading it requires an elevated "
        "run.")
    pane._on_result(result)

    computer = _roots(pane)["Computer Configuration"]
    assert computer.text(1) == "not collected"
    assert computer.childCount() == 1
    assert "elevated" in computer.child(0).text(0)
    assert pane._banner.isHidden() is False
    assert "elevated" in pane._banner.text()


def test_the_banner_stays_hidden_when_everything_was_collected(pane):
    pane._on_result(parse_rsop_xml(SETTINGS_XML))
    assert pane._banner.isHidden() is True


def test_a_gpresult_failure_is_shown_in_the_banner(pane):
    result = RsopResult()
    result.error = "ERROR: Access Denied."
    pane._on_result(result)
    assert pane._banner.isHidden() is False
    assert "Access Denied" in pane._banner.text()


def test_status_line_counts_scopes_and_settings(pane):
    pane._on_result(parse_rsop_xml(SETTINGS_XML))
    text = pane._status_lbl.text()
    assert "Computer, User" in text
    assert "5 setting(s)" in text


# ------------------------------------------------------------------
# Filtering
# ------------------------------------------------------------------

def test_filtering_keeps_the_path_to_a_match(pane):
    """Hiding non-matching rows without keeping their parents hides the match
    itself -- the tree would filter to nothing visible."""
    pane._on_result(parse_rsop_xml(SETTINGS_XML))
    pane._apply_filter("SrpV2")

    visible = [i.text(0) for i in _all_items(pane) if not i.isHidden()]
    assert "Computer Configuration" in visible
    assert "Settings" in visible
    assert any("SrpV2" in name for name in visible)
    assert "Security Groups" not in visible


def test_filtering_matches_the_value_and_gpo_columns_too(pane):
    pane._on_result(parse_rsop_xml(SETTINGS_XML))
    pane._apply_filter("filtered out")
    visible = [i.text(0) for i in _all_items(pane) if not i.isHidden()]
    assert "Filtered Out" in visible


def test_clearing_the_filter_shows_everything_again(pane):
    pane._on_result(parse_rsop_xml(SETTINGS_XML))
    total = sum(1 for _ in _all_items(pane))
    pane._apply_filter("SrpV2")
    assert sum(1 for i in _all_items(pane) if i.isHidden()) > 0
    pane._apply_filter("")
    assert sum(1 for i in _all_items(pane) if not i.isHidden()) == total


# ------------------------------------------------------------------
# External consoles
# ------------------------------------------------------------------

def test_the_gpedit_button_is_disabled_where_gpedit_is_absent(qapp, monkeypatch):
    """gpedit.msc does not ship with Windows Home. A button that opens
    nothing is worse than one that explains itself."""
    import modules.gpresult.gpresult_module as mod
    monkeypatch.setattr(mod, "mmc_console_path", lambda name: None)
    module = GPResultModule()
    module.on_start(None)
    module.create_widget()
    assert module._gpedit_btn.isEnabled() is False
    assert "Pro" in module._gpedit_btn.toolTip()


def test_the_gpedit_button_launches_mmc(pane, monkeypatch):
    import modules.gpresult.gpresult_module as mod
    launched = []
    monkeypatch.setattr(mod, "mmc_console_path",
                        lambda name: r"C:\Windows\System32\%s" % name)
    monkeypatch.setattr(mod.subprocess, "Popen",
                        lambda cmd, *a, **k: launched.append(cmd))
    pane._open_console("gpedit.msc")
    assert launched == [["mmc.exe", r"C:\Windows\System32\gpedit.msc"]]


# ------------------------------------------------------------------
# Refresh behaviour
# ------------------------------------------------------------------

def test_there_is_no_auto_refresh_timer(pane):
    """RSOP is a snapshot; Windows refreshes policy every 90 minutes. The old
    120-second timer re-ran a multi-second subprocess forever."""
    assert pane.get_refresh_interval() is None


def test_a_second_refresh_is_refused_while_one_is_running(pane, monkeypatch):
    pane._busy = True
    pane._do_refresh()
    assert "wait" in pane._status_lbl.text().lower()


# ------------------------------------------------------------------
# Readability in both themes
# ------------------------------------------------------------------

def _relative_luminance(hex_colour):
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def channel(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = channel(r), channel(g), channel(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(one, two):
    a, b = sorted((_relative_luminance(one), _relative_luminance(two)))
    return (b + 0.05) / (a + 0.05)


def test_the_banner_fixes_both_halves_of_its_colour_pair():
    """It paints its own background, so leaving the foreground to the theme
    would put dark.qss's #d4d4d4 on pale yellow at about 1.3:1 -- the exact
    bug already fixed once on the Sysinternals banner."""
    import re
    from modules.gpresult.gpresult_module import BANNER_STYLE

    def declared(prop):
        match = re.search(r"(?:^|;)\s*%s\s*:\s*([^;]+)" % prop, BANNER_STYLE)
        return match.group(1).strip() if match else None

    background, colour = declared("background"), declared("color")
    assert background and colour
    assert _contrast(colour, background) >= 4.5


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_tree_status_colours_clear_aa_in_both_themes(pane, theme):
    """The pane paints denied GPOs and failed extensions from Python, which a
    stylesheet cannot revisit when the theme changes."""
    from core import semantic_colors

    previous = semantic_colors.current_theme()
    try:
        semantic_colors.set_theme(theme)
        background = semantic_colors.PANE_BACKGROUND[theme]
        for meaning in ("warning", "error"):
            assert _contrast(semantic_colors.semantic(meaning), background) >= 4.5
    finally:
        semantic_colors.set_theme(previous)


# ------------------------------------------------------------------
# Local policy read straight from Registry.pol
# ------------------------------------------------------------------

def _pol_file(scope="Computer", hive="HKLM", values=(), exists=True, error=""):
    from modules.gpresult.pol_parser import PolFile
    return PolFile(path=r"C:\Windows\System32\GroupPolicy\Machine\Registry.pol",
                   scope=scope, hive=hive, exists=exists,
                   values=list(values), error=error)


def _pol_value(key, value_name, data, type_id=4, directive=""):
    from modules.gpresult.pol_parser import PolicyValue
    return PolicyValue(key=key, value_name=value_name, type_id=type_id,
                       data=data, raw=b"", directive=directive)


SRPV2 = _pol_value(
    r"Software\Policies\Microsoft\Windows\SrpV2\Exe", "AllowWindows", 0)


def test_a_refused_scope_still_shows_local_policy(pane):
    """The whole point of reading Registry.pol: gpresult refuses the computer
    half unless elevated, but the .pol file is readable by anyone, and on a
    machine that is not domain-joined it is the only policy there is."""
    result = parse_rsop_xml(REAL_USER_ONLY)
    result.computer.unavailable_reason = "requires an elevated run."
    pane._on_result(result, [_pol_file(values=[SRPV2])])

    computer = _roots(pane)["Computer Configuration"]
    local = _child(computer, "Local Policy (Registry.pol)")
    assert local.text(1) == "1"
    setting = _child(
        local, r"HKLM\Software\Policies\Microsoft\Windows\SrpV2\Exe\AllowWindows")
    assert setting.text(1) == "0"
    assert _child(setting, "Type").text(1) == "REG_DWORD"


def test_the_banner_says_local_policy_is_standing_in(pane):
    result = parse_rsop_xml(REAL_USER_ONLY)
    result.computer.unavailable_reason = "requires an elevated run."
    pane._on_result(result, [_pol_file(values=[SRPV2])])
    assert "Registry.pol" in pane._banner.text()


def test_the_banner_does_not_promise_local_policy_it_does_not_have(pane):
    """An empty .pol must not produce "showing local policy instead" -- there
    would be nothing behind the claim."""
    result = parse_rsop_xml(REAL_USER_ONLY)
    result.computer.unavailable_reason = "requires an elevated run."
    pane._on_result(result, [_pol_file(values=[])])
    assert "Registry.pol" not in pane._banner.text()


def test_local_policy_is_shown_even_when_the_scope_was_collected(pane):
    """RSOP and the .pol answer different questions -- what Windows computed
    as the winner, versus what is configured locally. Where they disagree,
    the disagreement is the interesting part, so both are shown."""
    pane._on_result(parse_rsop_xml(SETTINGS_XML), [_pol_file(values=[SRPV2])])
    computer = _roots(pane)["Computer Configuration"]
    assert _child(computer, "Local Policy (Registry.pol)").text(1) == "1"


def test_a_missing_pol_file_adds_no_section(pane):
    pane._on_result(parse_rsop_xml(SETTINGS_XML),
                    [_pol_file(exists=False)])
    computer = _roots(pane)["Computer Configuration"]
    labels = {computer.child(i).text(0) for i in range(computer.childCount())}
    assert "Local Policy (Registry.pol)" not in labels


def test_a_corrupt_pol_file_shows_its_error(pane):
    pane._on_result(parse_rsop_xml(SETTINGS_XML),
                    [_pol_file(values=[], error="Could not parse: bad magic")])
    local = _child(_roots(pane)["Computer Configuration"],
                   "Local Policy (Registry.pol)")
    assert "bad magic" in local.child(0).text(0)


def test_a_key_only_record_renders_without_a_value_name_row(pane):
    r"""Real local policy carries records naming a key and no value at all --
    this machine has one for ...\Windows\Safer."""
    safer = _pol_value(r"Software\Policies\Microsoft\Windows\Safer", "",
                       b"", type_id=0)
    pane._on_result(parse_rsop_xml(SETTINGS_XML), [_pol_file(values=[safer])])
    local = _child(_roots(pane)["Computer Configuration"],
                   "Local Policy (Registry.pol)")
    item = local.child(0)
    labels = {item.child(i).text(0) for i in range(item.childCount())}
    assert "Value name" not in labels
    assert _child(item, "Type").text(1) == "REG_NONE"


def test_the_user_pol_goes_under_the_user_scope(pane):
    """Machine pol is HKLM, user pol is HKCU. Crossing them would attribute
    every user policy to the machine."""
    user_value = _pol_value(r"Software\Policies\Test", "Flag", 1)
    pane._on_result(parse_rsop_xml(SETTINGS_XML), [
        _pol_file("Computer", "HKLM", [SRPV2]),
        _pol_file("User", "HKCU", [user_value]),
    ])
    user = _child(_roots(pane)["User Configuration"],
                  "Local Policy (Registry.pol)")
    assert _child(user, r"HKCU\Software\Policies\Test\Flag").text(1) == "1"


def test_a_delete_directive_is_not_painted_as_a_setting(pane):
    """`**del.Foo` removes a value rather than setting one."""
    directive = _pol_value(r"Software\Policies\Test", "**del.Foo", None,
                           type_id=1, directive="delete_value")
    pane._on_result(parse_rsop_xml(SETTINGS_XML), [_pol_file(values=[directive])])
    local = _child(_roots(pane)["Computer Configuration"],
                   "Local Policy (Registry.pol)")
    assert local.child(0).text(1) == "(delete value)"


# ------------------------------------------------------------------
# Multiple Local GPO (System32\GroupPolicyUsers) -- a separate mechanism
# from the single machine-wide Local Policy section above.
# ------------------------------------------------------------------

def _per_user_entry(sid="S-1-5-32-544", account_name="Administrators",
                    resolved=True, pol=None):
    from modules.gpresult.pol_parser import PerUserLocalPolicy
    return PerUserLocalPolicy(sid=sid, account_name=account_name,
                              resolved=resolved, pol=pol)


def test_the_section_is_absent_when_the_check_never_ran(pane):
    """`None` means the caller never fetched this, the same signal
    `_build_audit` reads on `tattoo`/`conflicts` -- distinct from a real,
    empty answer."""
    pane._on_result(parse_rsop_xml(SETTINGS_XML))
    assert "Per-User Local Policies" not in _roots(pane)


def test_a_real_empty_answer_still_gets_its_own_row(pane):
    """Confirmed on the real machine this ships on: the folder exists but
    holds no SID subfolders. That is worth stating, not hiding."""
    pane._on_result(parse_rsop_xml(SETTINGS_XML), per_user_policy=[])
    section = _roots(pane)["Per-User Local Policies"]
    assert section.text(1) == "0"
    assert "System32" in section.child(0).text(0)


def test_a_configured_per_user_policy_is_shown_under_its_account_name(pane):
    pol = _pol_file(scope="User", hive="HKU\\S-1-5-32-544", values=[SRPV2])
    entry = _per_user_entry(pol=pol)
    pane._on_result(parse_rsop_xml(SETTINGS_XML), per_user_policy=[entry])
    section = _roots(pane)["Per-User Local Policies"]
    assert section.text(1) == "1 (1 with settings configured)"
    account = _child(section, "Administrators")
    setting = _child(
        account, r"Software\Policies\Microsoft\Windows\SrpV2\Exe\AllowWindows")
    assert setting.text(1) == "0"


def test_an_unresolvable_sid_is_shown_under_its_raw_sid_and_flagged(pane):
    """A deleted account's per-user GPO is still real, still-effective
    policy -- it must be shown, not dropped for want of a friendly name."""
    entry = _per_user_entry(sid="S-1-5-21-1-2-3-9999", account_name="",
                            resolved=False, pol=_pol_file(values=[SRPV2]))
    pane._on_result(parse_rsop_xml(SETTINGS_XML), per_user_policy=[entry])
    section = _roots(pane)["Per-User Local Policies"]
    row = _child(section, "S-1-5-21-1-2-3-9999")
    assert "Could not resolve" in _child(row, "Account").text(1)


# ------------------------------------------------------------------
# Is the configured policy actually in effect?
# ------------------------------------------------------------------

def _drift(full_path, state, reason="because", expected="", live=""):
    from modules.gpresult import policy_drift as pd

    class _Fake:
        def __init__(self):
            self.full_path = full_path
            self.state = state
            self.reason = reason
            self.expected_display = expected
            self.live_display = live

        @property
        def is_drift(self):
            return self.state in (pd.DIFFERENT, pd.MISSING)

    return _Fake()


def _report(*results):
    class _Fake:
        def __init__(self, items):
            self.results = list(items)

    return _Fake(results)


SRPV2_PATH = (r"HKLM\Software\Policies\Microsoft\Windows"
              r"\SrpV2\Exe\AllowWindows")


def test_a_policy_that_took_effect_says_so_on_its_own_row(pane):
    """Configured and in-effect are different questions. The .pol file can
    only answer the first; the second needs the live registry read back."""
    from modules.gpresult import policy_drift as pd

    pane._on_result(parse_rsop_xml(SETTINGS_XML), [_pol_file(values=[SRPV2])],
                    _report(_drift(SRPV2_PATH, pd.APPLIED, "matches")))
    local = _child(_roots(pane)["Computer Configuration"],
                   "Local Policy (Registry.pol)")
    assert local.text(1) == "1 (all in effect)"
    effect = _child(_child(local, SRPV2_PATH), "In effect?")
    assert effect.text(1) == "applied"
    assert _child(effect, "Why").text(1) == "matches"


def test_a_policy_the_registry_contradicts_shows_both_values(pane):
    """"It is set to something else" is useless without saying to what."""
    from modules.gpresult import policy_drift as pd

    pane._on_result(
        parse_rsop_xml(SETTINGS_XML), [_pol_file(values=[SRPV2])],
        _report(_drift(SRPV2_PATH, pd.DIFFERENT, "differs",
                       expected="0", live="1")))
    local = _child(_roots(pane)["Computer Configuration"],
                   "Local Policy (Registry.pol)")
    assert local.text(1) == "1 (1 not in effect)"
    effect = _child(_child(local, SRPV2_PATH), "In effect?")
    assert _child(effect, "Policy expects").text(1) == "0"
    assert _child(effect, "Registry holds").text(1) == "1"


def test_a_refused_read_is_not_counted_as_drift(pane):
    """Being unable to look is not the same as finding something wrong.
    Folding refused reads into the drift count turns a permissions problem
    into a phantom policy problem."""
    from modules.gpresult import policy_drift as pd

    pane._on_result(parse_rsop_xml(SETTINGS_XML), [_pol_file(values=[SRPV2])],
                    _report(_drift(SRPV2_PATH, pd.UNREADABLE, "denied")))
    local = _child(_roots(pane)["Computer Configuration"],
                   "Local Policy (Registry.pol)")
    assert local.text(1) == "1 (all in effect)"
    assert _child(_child(local, SRPV2_PATH), "In effect?").text(1) == "unreadable"


def test_without_a_drift_pass_the_count_makes_no_claim(pane):
    """No drift data must not render as "all in effect" -- that would be a
    claim nothing checked."""
    pane._on_result(parse_rsop_xml(SETTINGS_XML), [_pol_file(values=[SRPV2])])
    local = _child(_roots(pane)["Computer Configuration"],
                   "Local Policy (Registry.pol)")
    assert local.text(1) == "1"
    item = _child(local, SRPV2_PATH)
    labels = {item.child(i).text(0) for i in range(item.childCount())}
    assert "In effect?" not in labels


def test_every_drift_state_has_a_readable_colour_in_both_themes():
    """These are painted from Python, which a stylesheet cannot revisit when
    the theme changes."""
    from core import semantic_colors
    from modules.gpresult.gpresult_module import _DRIFT_COLOURS

    previous = semantic_colors.current_theme()
    try:
        for theme in ("dark", "light"):
            semantic_colors.set_theme(theme)
            background = semantic_colors.PANE_BACKGROUND[theme]
            for meaning in _DRIFT_COLOURS.values():
                ratio = _contrast(semantic_colors.semantic(meaning), background)
                assert ratio >= 4.5, (theme, meaning, ratio)
    finally:
        semantic_colors.set_theme(previous)

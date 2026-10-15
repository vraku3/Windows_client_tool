"""Attack Surface Reduction rules: which are on, and which technique each stops.

The Security Dashboard already had `check_defender_asr_rules`, a single count of
how many ASR rules are configured. That is the wrong shape: ASR is a set of
independent rules, each one a GUID with its own Block / Audit / Warn / Disabled
state, and "12 configured" tells an admin nothing about WHICH twelve or whether
the ones that matter are in Block rather than Audit. This module is the
per-rule model the catalog needs.

Everything here is Qt-free and takes a `Get-MpPreference` dict, so the parsing
is tested without Defender and against the machine's real preference blob.

The action codes are not guessed: the installed Defender module declares them
in `MSFT_MpPreference.cdxml` (`MpPreference.ASRRuleActionType`), read on this
machine 2026-10-15:

    Disabled = 0   Enabled (Block) = 1   AuditMode = 2
    NotConfigured = 5   Warn = 6

The GUIDs and names are Microsoft's documented ASR rule set
(learn.microsoft.com/defender-endpoint/attack-surface-reduction-rules-reference),
read 2026-10-15. The `standard` flag marks the three rules Microsoft's own
"Standard protection" preset enables in Block.

A rule that is ABSENT from `Get-MpPreference` is `NotConfigured` -- the definite
Windows default, not "unknown". `read_actions` returns None only when the
preference blob itself was refused, which is a different thing.
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ACTION_DISABLED = 0
ACTION_BLOCK = 1
ACTION_AUDIT = 2
ACTION_NOT_CONFIGURED = 5
ACTION_WARN = 6

ACTION_LABELS = {
    ACTION_DISABLED: "Disabled",
    ACTION_BLOCK: "Block",
    ACTION_AUDIT: "Audit",
    ACTION_NOT_CONFIGURED: "Not configured",
    ACTION_WARN: "Warn",
}

#: Defender's own wording when it will not answer an ordinary user. It arrives
#: as a VALUE, so a parser that trusts it reports a rule named that string.
_REFUSAL_MARKERS = ("must be an administrator", "access is denied",
                    "access denied", "not authorized")


@dataclass(frozen=True)
class Rule:
    guid: str
    name: str
    slug: str
    why: str
    #: Microsoft's "Standard protection" preset enables these three in Block.
    standard: bool = False
    #: What this rule can break or requires, stated for the admin about to
    #: turn it on. Empty when there is nothing worth warning about.
    caution: str = ""


RULES: Tuple[Rule, ...] = (
    Rule("56a863a9-875e-4185-98a7-b882c64b5ce5",
         "Block abuse of exploited vulnerable signed drivers",
         "vulnerable_signed_drivers",
         "A vulnerable signed driver is a kernel back door: an app with "
         "enough privilege uses it to read or write kernel memory, past every "
         "protections in user mode. This stops the driver being written to "
         "disk (it does not unload one already loaded).",
         standard=True),
    Rule("9e6c4e1f-7d60-472f-ba1a-a39ef669e4b2",
         "Block credential stealing from the Windows local security authority "
         "subsystem (LSASS)",
         "lsass_credential_stealing",
         "Stops a process opening LSASS to read the logon credentials in it -- "
         "the exact step every credential-dumping tool takes. Not needed when "
         "LSA protection or Credential Guard is already on.",
         standard=True,
         caution="Produces a large volume of audit events, almost all benign "
                 "(Chrome and other apps touch LSASS unnecessarily). Start in "
                 "Block; do not be alarmed by the noise."),
    Rule("e6db77e5-3df2-4cf1-b95a-636979351e5b",
         "Block persistence through WMI event subscription",
         "wmi_persistence",
         "The WMI event model can run code when a chosen event fires, with no "
         "file and no registry entry to find -- the same mechanism this app's "
         "Startup & Boot tab now lists. This blocks creating such a "
         "subscription.",
         standard=True,
         caution="Configuration Manager and WMI-heavy management tools use "
                 "WMI subscriptions. Test in Audit mode on a managed fleet "
                 "before Block."),
    Rule("7674ba52-37eb-4a4f-a9a1-f0f9a1619a2c",
         "Block Adobe Reader from creating child processes",
         "adobe_reader_child_process",
         "Reader is a classic first-stage target: a malicious PDF escapes the "
         "sandbox by launching a child process. This blocks that step."),
    Rule("d4f940ab-401b-4efc-aadc-ad5f3c50688a",
         "Block all Office applications from creating child processes",
         "office_child_process",
         "A macro that spawns cmd.exe or PowerShell is the most common "
         "Office-borne attack. This blocks every Office app from spawning a "
         "child process.",
         caution="Some legitimate line-of-business macros spawn a helper "
                 "process; check those before enforcing."),
    Rule("be9ba2d9-53ea-4cdc-84e5-9b1eeee46550",
         "Block executable content from email client and webmail",
         "email_executable_content",
         "An attachment you can run is an attachment that runs itself the "
         "moment a user double-clicks. This blocks executables, scripts and "
         "archives opened from mail, with no exceptions."),
    Rule("01443614-cd74-433a-b99e-2ecdc07bfc25",
         "Block executable files unless they meet a prevalence, age or trusted "
         "list criterion",
         "untrusted_executables",
         "An unknown executable -- no prevalence in the cloud, freshly built "
         "-- is what a dropper looks like. This blocks it until it has a "
         "reputation.",
         caution="The noisiest ASR rule: it blocks brand-new legitimate "
                 "software too. Needs cloud-delivered protection and usually "
                 "exclusions for internal tools. Test thoroughly."),
    Rule("5beb7efe-fd9a-4556-801d-275e5ffc04cc",
         "Block execution of potentially obfuscated scripts",
         "obfuscated_scripts",
         "Obfuscation hides what a script does from a human and from static "
         "analysis; malware uses it constantly to evade both.",
         caution="Requires cloud-delivered protection. Some signed installers "
                 "use packed scripts and will be flagged."),
    Rule("d3e037e1-3eb8-44c8-a917-57927947596d",
         "Block JavaScript or VBScript from launching downloaded executable "
         "content",
         "script_downloaded_content",
         "A .js or .vbs dropped from the internet is almost always a "
         "downloader. This stops the script running what it fetched."),
    Rule("3b576869-a4ec-4529-8536-b80a7769e899",
         "Block Office applications from creating executable content",
         "office_executable_content",
         "Stops a macro writing an executable to disk and running it -- the "
         "persistence step that survives a reboot."),
    Rule("75668c1f-73b5-4cf0-bb93-3ecf5cb7cc84",
         "Block Office applications from injecting code into other processes",
         "office_code_injection",
         "Code injection lets malicious macro code masquerade inside a clean "
         "process. There is no legitimate business reason for Office to inject.",
         caution="Requires restarting the Office apps after it is set, and is "
                 "incompatible with BeyondTrust Privilege Guard and Heimdal."),
    Rule("26190899-1602-49e8-8b27-eb1d0a1ce869",
         "Block Office communication application from creating child processes",
         "office_comm_child_process",
         "Outlook is the other Office attack surface: a malicious mail rule or "
         "form runs a program. This blocks Outlook from spawning children."),
    Rule("d1e49aac-8f56-4280-b9ba-993a6d77406c",
         "Block process creations originating from PSExec and WMI commands",
         "psexec_wmi_process",
         "PsExec and WMI are how an attacker moves laterally without dropping "
         "an agent. This blocks processes they create.",
         caution="On a Configuration Manager-managed machine the client "
                 "depends on WMI; test in Audit first."),
    Rule("33ddedf1-c6e0-47cb-833e-de6133960387",
         "Block rebooting machine in Safe Mode",
         "safe_mode_reboot",
         "Safe Mode disables most security software, which makes it a "
         "preferred step for disabling defences or encrypting a disk. This "
         "blocks bcdedit/bootcfg-style reboots into it."),
    Rule("b2b3f03d-6a65-4f7b-a9c7-1c7ef74a9ba4",
         "Block untrusted and unsigned processes that run from USB",
         "usb_untrusted_process",
         "Removable media is still one of the cheapest ways in. This stops "
         "unsigned or untrusted executables running from USB drives and SD "
         "cards."),
    Rule("c0033c00-d16d-4114-a5a0-dc9b3a7d2ceb",
         "Block use of copied or impersonated system tools",
         "impersonated_system_tools",
         "A copy of a Windows tool (or a file named like one) running from "
         "outside System32 is how an attacker blends in. This blocks it even "
         "when it is signed.",
         caution="Heuristic: it can block legitimate third-party tools that "
                 "resemble a system binary. Expect some exclusions."),
    Rule("92e97fa1-2edf-4476-bdd6-9dd0b4dddc7b",
         "Block Win32 API calls from Office macros",
         "office_macro_win32",
         "Lets a macro compile nothing to disk and still call the operating "
         "system -- fileless execution. Most organisations never need it."),
    Rule("c1db55ab-c21a-4637-bb3f-a12568109d35",
         "Use advanced protection against ransomware",
         "ransomware_advanced_protection",
         "Client and cloud heuristics block files that look like ransomware "
         "even without a signature, including files with no positive "
         "reputation yet.",
         caution="Blocks unknown-but-benign files more than the other rules. "
                 "Requires cloud-delivered protection."),
)


def _as_list(value: Any) -> List[str]:
    """A Get-MpPreference array field as a list of strings.

    `ConvertTo-Json` in Windows PowerShell 5.1 serializes a single-element
    array as a bare string, not a one-item array, so both shapes are real.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(value)]


def _looks_refused(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)


def read_actions(prefs: Any) -> Optional[Dict[str, int]]:
    """`Get-MpPreference` -> {guid (lowercase, braces kept): action code}.

    None when the blob is not a dict or a field carries Defender's refusal
    text instead of data. A rule that is simply absent is left out of the map
    and reads as NotConfigured.
    """
    if not isinstance(prefs, dict):
        return None
    id_list = _as_list(prefs.get("AttackSurfaceReductionRules_Ids"))
    action_list = _as_list(prefs.get("AttackSurfaceReductionRules_Actions"))
    if any(_looks_refused(v) for v in id_list + action_list):
        return None
    actions: Dict[str, int] = {}
    for guid, action in zip(id_list, action_list):
        try:
            actions[guid.strip().lower()] = int(action)
        except (TypeError, ValueError):
            logger.warning("ASR rule %r has a non-numeric action %r; ignored",
                           guid, action)
    return actions


def state_for(actions: Optional[Dict[str, int]], rule: Rule) -> Optional[int]:
    """One rule's action code, NotConfigured when absent, None when refused."""
    if actions is None:
        return None
    return actions.get(rule.guid.lower(), ACTION_NOT_CONFIGURED)


def state_text(action: int) -> str:
    return ACTION_LABELS.get(action, f"Unknown ({action})")


def blocking_count(actions: Optional[Dict[str, int]]) -> Optional[int]:
    if actions is None:
        return None
    return sum(1 for value in actions.values() if value == ACTION_BLOCK)


def configured_count(actions: Optional[Dict[str, int]]) -> Optional[int]:
    if actions is None:
        return None
    return sum(1 for value in actions.values()
               if value != ACTION_NOT_CONFIGURED)


def standard_rules() -> Tuple[Rule, ...]:
    return tuple(rule for rule in RULES if rule.standard)

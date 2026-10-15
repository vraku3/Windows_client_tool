"""Defender controls.

Most of these are `Set-MpPreference` only -- there is no registry equivalent
Defender honours -- so they are `script` steps whose revert command is computed
at stage time from the reader's current value (spec 2.2). Where a registry
equivalent DOES exist and Defender honours it, the registry step wins, because
BackupService can restore a registry value exactly and cannot revert a command.

Two conventions hold throughout this file:

* A multi-valued control (`read_value` pulls a number, not a bool) sets the
  recommended value in `on_steps` and **Windows' own default** in `off_steps`.
  "Off" for a level is the default, not zero-as-a-guess.
* `desired=None` means the catalog deliberately has no opinion. It is not an
  oversight and it is not False -- see `defender_cpu_usage`.

**Tamper Protection blocks every script step in this file.** When
`check_tamper_protection` reads On, Defender refuses programmatic changes to
its own settings, so these writes fail; the apply path's verify-after-write
pass reports them as REFUSED rather than as applied (Task 11). The machine this
was written on has Tamper Protection ON and Defender in Passive Mode, so no
Defender write here has been verified end-to-end against a live Defender.
"""
from typing import Any, Dict, Tuple

from .. import asr_rules, snapshots
from ..security_reader import (
    check_applocker, check_cloud_protection, check_controlled_folder_access,
    check_defender, check_defender_archive_scanning, check_defender_asr_rules,
    check_defender_behavior_monitoring, check_defender_catchup_scan,
    check_defender_check_signatures, check_defender_cloud_block_level,
    check_defender_cloud_timeout, check_defender_cpu_usage,
    check_defender_email_scanning, check_defender_engine_version,
    check_defender_exclusions, check_defender_ioav,
    check_defender_nis, check_defender_oobe, check_defender_removable_drive,
    check_defender_script_scanning, check_defender_threat_high,
    check_defender_threat_low, check_defender_threat_moderate,
    check_defender_threat_severe, check_elam,
    check_network_protection_defender, check_pua_protection,
    check_tamper_protection,
)
from .model import Category, Risk, SecurityControl

_PS = "powershell -NoProfile -Command "


#: from_value -> the token that puts a `Disable<X>` preference back. The
#: feature being ON means the preference was $false.
_REVERT_DISABLE_FLAG = {"True": "$false", "False": "$true"}
#: ...and for a preference that is not inverted.
_REVERT_BOOL = {"True": "$true", "False": "$false"}
#: MAPSReporting: Disabled=0, Basic=1, Advanced=2.
_REVERT_MAPS = {"0": "Disabled", "1": "Basic", "2": "Advanced"}
#: CloudBlockLevel, off the live cmdlet's enum.
_REVERT_CLOUD_BLOCK_LEVEL = {"0": "Default", "1": "Moderate", "2": "High",
                             "4": "HighPlus", "6": "ZeroTolerance"}
#: PUAProtection and EnableNetworkProtection share this shape.
_REVERT_ENABLED_AUDIT = {"0": "Disabled", "1": "Enabled", "2": "AuditMode"}
#: EnableControlledFolderAccess reads as a bool but is written by name.
_REVERT_ENABLED_BOOL = {"True": "Enabled", "False": "Disabled"}
#: ThreatAction. **0 is deliberately absent**: Get-MpPreference reports 0 for
#: an unconfigured severity, and 0 is not a member of the enum, so there is no
#: command that reverts to it. A missing key yields no revert command, which
#: is the honest answer rather than a guess (Ruling 20).
_REVERT_THREAT_ACTION = {"1": "Clean", "2": "Quarantine", "3": "Remove",
                         "6": "Allow", "8": "UserDefined", "9": "NoAction",
                         "10": "Block", "11": "None"}


def _set_pref(assignment: str, revert_values: dict = None) -> dict:
    """One `Set-MpPreference` script step, carrying its own way back.

    BackupService records a before-value for a registry write and nothing at
    all for a command, so a script step that cannot say how to undo itself is
    a one-way door. `${old}` is filled in at stage time from what the machine
    actually had (staging.PendingChange.resolved_steps).

    `revert_values` is omitted only for a numeric parameter, which reverts to
    its own number.
    """
    parameter = assignment.split()[0]
    step = {"type": "script",
            "command": f"{_PS}Set-MpPreference {assignment}",
            "revert_template": f"{_PS}Set-MpPreference {parameter} ${{old}}"}
    if revert_values is not None:
        step["revert_values"] = revert_values
    return step


def _disable_flag(field: str) -> dict:
    """Steps for a `Disable<X>` preference, which is inverted: the feature is
    ON when the preference is $false."""
    return {"on": (_set_pref(f"-{field} $false", _REVERT_DISABLE_FLAG),),
            "off": (_set_pref(f"-{field} $true", _REVERT_DISABLE_FLAG),)}


_THREAT_ACTIONS = (
    # (id suffix, severity, Set-MpPreference parameter)
    ("low", "Low", "LowThreatDefaultAction"),
    ("moderate", "Moderate", "ModerateThreatDefaultAction"),
    ("high", "High", "HighThreatDefaultAction"),
    ("severe", "Severe", "SevereThreatDefaultAction"),
)

_THREAT_READERS = {
    "low": check_defender_threat_low,
    "moderate": check_defender_threat_moderate,
    "high": check_defender_threat_high,
    "severe": check_defender_threat_severe,
}


def _threat_control(suffix: str, severity: str, param: str) -> SecurityControl:
    """Defender's default action for one threat severity.

    `desired=2` is Quarantine, chosen over Remove (3) because quarantine is
    reversible -- the file can be restored from the Defender UI -- and over
    Block (10), Allow (6) and NoAction (9), which leave the file where it is.
    0 (Default) lets Defender decide per detection, which is what every one of
    these reads on this machine.

    `off_steps` sets None (11), not 0. Get-MpPreference reports 0 for a
    severity nobody configured, but 0 is not a member of the ThreatAction
    enum -- `Set-MpPreference -LowThreatDefaultAction 0` fails parameter
    binding -- so None, the enum's own "unspecified", is the nearest reachable
    revert. It does not land back on exactly 0; see the ledger.
    """
    return SecurityControl(
        id=f"defender_threat_{suffix}",
        title=f"Default action for {severity.lower()}-severity threats",
        category=Category.DEFENDER,
        description=f"What Defender does when it detects a {severity.lower()}-"
                    "severity threat: 0 Default, 1 Clean, 2 Quarantine, "
                    "3 Remove, 6 Allow, 9 NoAction, 10 Block.",
        why_it_matters="Left at Default, the action is Defender's call. Set "
                       "explicitly, a detection at this severity is always "
                       "quarantined rather than left on disk pending a "
                       "judgement you never see.",
        reader=_THREAT_READERS[suffix],
        read_value=lambda d: d.get("value"),
        on_steps=(_set_pref(f"-{param} Quarantine", _REVERT_THREAT_ACTION),),
        off_steps=(_set_pref(f"-{param} None", _REVERT_THREAT_ACTION),),
        desired=2,
        risk=Risk.LOW,
    )


#: A control's comparable value is a bool (blocking or not), so a revert can
#: only put the rule back to Enabled or Disabled -- it cannot know whether the
#: machine had the rule in Audit, Warn or NotConfigured before.
_ASR_REVERT = {"True": "Enabled", "False": "Disabled"}


def _asr_step(rule: asr_rules.Rule, enable: bool) -> Dict[str, Any]:
    """One ASR rule, set or cleared by GUID.

    `Add-MpPreference`/`Remove-MpPreference` address a SINGLE rule, which is
    exactly what a per-rule control needs -- `Set-MpPreference` on the whole
    Ids/Actions array would rewrite every other rule. The GUID is single-quoted
    so the braces are not read as a PowerShell scriptblock.
    """
    ids = f"-AttackSurfaceReductionRules_Ids '{rule.guid}'"
    actions = "-AttackSurfaceReductionRules_Actions"
    revert = f"{_PS}Add-MpPreference {ids} {actions} ${{old}}"
    if enable:
        command = f"{_PS}Add-MpPreference {ids} {actions} Enabled"
    else:
        command = f"{_PS}Remove-MpPreference {ids}"
    return {"type": "script", "command": command,
            "revert_template": revert, "revert_values": _ASR_REVERT}


def _asr_reader(rule: asr_rules.Rule):
    def read() -> Dict[str, Any]:
        prefs = snapshots.mp_preference()
        reason = snapshots.unavailable("mp_preference")
        if reason is not None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [(rule.name, f"Could not read: {reason}")]}
        action = asr_rules.state_for(asr_rules.read_actions(prefs), rule)
        if action is None:
            return {"status": "Unknown", "color": "amber", "available": False,
                    "details": [(rule.name,
                                 "Defender did not report its ASR rule state")]}
        blocking = action == asr_rules.ACTION_BLOCK
        details = [(rule.name, asr_rules.state_text(action))]
        if rule.caution:
            details.append(("Before you enforce it", rule.caution))
        return {"status": asr_rules.state_text(action),
                "color": "green" if blocking else "amber",
                "available": True, "enabled": blocking, "action": action,
                "details": details}
    return read


def _asr_control(rule: asr_rules.Rule) -> SecurityControl:
    """One Attack Surface Reduction rule as its own control.

    Microsoft's three "Standard protection" rules carry `desired=True` (that is
    Microsoft's own recommended preset, so they are in the recommended
    baseline). The other fifteen are `desired=None`: the catalog has no opinion,
    because which of them a machine should enforce depends on what it runs --
    the card shows the real state and offers the action without calling an
    unconfigured rule a problem.
    """
    why = ("Windows leaves every ASR rule off by default, so this technique is "
           "unblocked until the rule is set.")
    if rule.caution:
        why = f"{why} {rule.caution}"
    return SecurityControl(
        id=f"asr_{rule.slug}",
        title=rule.name,
        category=Category.DEFENDER,
        description=rule.why,
        why_it_matters=why,
        reader=_asr_reader(rule),
        on_steps=(_asr_step(rule, True),),
        off_steps=(_asr_step(rule, False),),
        desired=True if rule.standard else None,
        risk=Risk.LOW if rule.standard else Risk.MEDIUM,
    )


CONTROLS: Tuple[SecurityControl, ...] = (

    # -- the core engine ---------------------------------------------------
    SecurityControl(
        id="defender_realtime",
        title="Real-time protection",
        category=Category.DEFENDER,
        description="Defender scanning files as they are opened, written and "
                    "executed, rather than only during a scheduled scan.",
        why_it_matters="With real-time protection off, malware is detected "
                       "the next time a scan happens to reach it -- which for "
                       "a file that has already run is too late.",
        reader=check_defender,
        read_value=lambda d: d.get("real_time"),
        on_steps=(_set_pref("-DisableRealtimeMonitoring $false", _REVERT_DISABLE_FLAG),),
        off_steps=(_set_pref("-DisableRealtimeMonitoring $true", _REVERT_DISABLE_FLAG),),
        desired=True,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="defender_behavior_monitoring",
        title="Behaviour monitoring",
        category=Category.DEFENDER,
        description="Watches running processes for malicious behaviour rather "
                    "than matching known file signatures.",
        why_it_matters="Signature scanning cannot see fileless malware or a "
                       "living-off-the-land attack driven entirely through "
                       "PowerShell and WMI. Behaviour monitoring is what does.",
        reader=check_defender_behavior_monitoring,
        on_steps=_disable_flag("DisableBehaviorMonitoring")["on"],
        off_steps=_disable_flag("DisableBehaviorMonitoring")["off"],
        desired=True,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="defender_script_scanning",
        title="Script scanning",
        category=Category.DEFENDER,
        description="Scans scripts before they run, through the AMSI "
                    "interface PowerShell, VBScript and JScript expose.",
        why_it_matters="Script droppers are the standard first stage: nothing "
                       "malicious is ever written to disk as an executable.",
        reader=check_defender_script_scanning,
        on_steps=_disable_flag("DisableScriptScanning")["on"],
        off_steps=_disable_flag("DisableScriptScanning")["off"],
        desired=True,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="defender_ioav",
        title="Scan downloaded files and attachments",
        category=Category.DEFENDER,
        description="IOAV protection: scans files as they arrive from a "
                    "browser or a mail client.",
        why_it_matters="Catches a download at the moment it lands, rather "
                       "than at the moment someone runs it.",
        reader=check_defender_ioav,
        on_steps=_disable_flag("DisableIOAVProtection")["on"],
        off_steps=_disable_flag("DisableIOAVProtection")["off"],
        desired=True,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="defender_archive_scanning",
        title="Scan inside archives",
        category=Category.DEFENDER,
        description="Looks inside .zip, .7z, .iso and similar containers "
                    "during a scan.",
        why_it_matters="Mail-borne malware travels in an archive precisely "
                       "because an archive is what unscanned looks like.",
        reader=check_defender_archive_scanning,
        on_steps=_disable_flag("DisableArchiveScanning")["on"],
        off_steps=_disable_flag("DisableArchiveScanning")["off"],
        desired=True,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="defender_email_scanning",
        title="Scan email files",
        category=Category.DEFENDER,
        description="Parses mailbox files (.pst, .dbx, mbox) during a scan.",
        why_it_matters="An attachment sitting unopened in a local mailbox "
                       "file is invisible to a scan that does not parse it.",
        reader=check_defender_email_scanning,
        on_steps=_disable_flag("DisableEmailScanning")["on"],
        off_steps=_disable_flag("DisableEmailScanning")["off"],
        desired=True,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="defender_removable_drive",
        title="Scan removable drives",
        category=Category.DEFENDER,
        description="Includes USB sticks and external disks in a full scan.",
        why_it_matters="A USB stick is still one of the few ways into a "
                       "machine that never touches the network.",
        reader=check_defender_removable_drive,
        on_steps=_disable_flag("DisableRemovableDriveScanning")["on"],
        off_steps=_disable_flag("DisableRemovableDriveScanning")["off"],
        desired=True,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="defender_nis",
        title="Network Inspection System",
        category=Category.DEFENDER,
        description="Inspects network traffic for known exploit signatures "
                    "against Windows network stacks.",
        why_it_matters="Blocks an exploit aimed at an unpatched service "
                       "before the service ever sees the packet.",
        reader=check_defender_nis,
        on_steps=(_set_pref("-DisableIntrusionPreventionSystem $false", _REVERT_DISABLE_FLAG),),
        off_steps=(_set_pref("-DisableIntrusionPreventionSystem $true", _REVERT_DISABLE_FLAG),),
        desired=True,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="defender_catchup_scan",
        title="Catch-up scans",
        category=Category.DEFENDER,
        description="Runs a scheduled scan that was missed -- because the "
                    "machine was off or asleep -- at the next opportunity.",
        why_it_matters="A laptop that is asleep at 02:00 every night never "
                       "runs its scheduled scan at all without this.",
        reader=check_defender_catchup_scan,
        on_steps=(_set_pref("-DisableCatchupQuickScan $false", _REVERT_DISABLE_FLAG),
                  _set_pref("-DisableCatchupFullScan $false", _REVERT_DISABLE_FLAG)),
        off_steps=(_set_pref("-DisableCatchupQuickScan $true", _REVERT_DISABLE_FLAG),
                   _set_pref("-DisableCatchupFullScan $true", _REVERT_DISABLE_FLAG)),
        desired=True,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="defender_check_signatures",
        title="Update signatures before scanning",
        category=Category.DEFENDER,
        description="Fetches definition updates immediately before a "
                    "scheduled scan starts.",
        why_it_matters="A scan with week-old definitions is a scan for last "
                       "week's malware.",
        reader=check_defender_check_signatures,
        on_steps=(_set_pref("-CheckForSignaturesBeforeRunningScan $true", _REVERT_BOOL),),
        off_steps=(_set_pref("-CheckForSignaturesBeforeRunningScan $false", _REVERT_BOOL),),
        desired=True,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="defender_oobe",
        title="Protection during first sign-in",
        category=Category.DEFENDER,
        description="Turns real-time protection and signature updates on "
                    "during the out-of-box experience, before a user account "
                    "exists.",
        why_it_matters="Closes the window on a freshly imaged machine that is "
                       "on the network but not yet protected.",
        reader=check_defender_oobe,
        on_steps=(_set_pref("-OobeEnableRtpAndSigUpdate $true", _REVERT_BOOL),),
        off_steps=(_set_pref("-OobeEnableRtpAndSigUpdate $false", _REVERT_BOOL),),
        desired=True,
        risk=Risk.LOW,
    ),

    # -- cloud ------------------------------------------------------------
    SecurityControl(
        id="defender_cloud_protection",
        title="Cloud-delivered protection (MAPS)",
        category=Category.DEFENDER,
        description="Sends telemetry about suspicious files to Microsoft's "
                    "cloud service and acts on the verdict. 0 off, 1 basic, "
                    "2 advanced.",
        why_it_matters="Cloud protection is how a sample first seen ten "
                       "minutes ago anywhere in the world is blocked here.",
        reader=check_cloud_protection,
        read_value=lambda d: d.get("maps_level"),
        on_steps=(_set_pref("-MAPSReporting Advanced", _REVERT_MAPS),),
        off_steps=(_set_pref("-MAPSReporting Disabled", _REVERT_MAPS),),
        desired=2,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="defender_cloud_block_level",
        title="Cloud-delivered protection level",
        category=Category.DEFENDER,
        description="How aggressively Defender blocks files its cloud service "
                    "is unsure about. 0 Default, 1 Moderate, 2 High, "
                    "4 High+, 6 Zero Tolerance.",
        why_it_matters="At the default level a brand-new sample is allowed to "
                       "run while the cloud makes up its mind.",
        reader=check_defender_cloud_block_level,
        read_value=lambda d: d.get("level"),
        on_steps=(_set_pref("-CloudBlockLevel High", _REVERT_CLOUD_BLOCK_LEVEL),),
        off_steps=(_set_pref("-CloudBlockLevel Default", _REVERT_CLOUD_BLOCK_LEVEL),),
        desired=2,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="defender_cloud_timeout",
        title="Cloud check extended timeout",
        category=Category.DEFENDER,
        description="Extra seconds, on top of the 10-second default, that "
                    "Defender will block a file while waiting for a cloud "
                    "verdict. 0 to 50.",
        why_it_matters="When the cloud does not answer in time the file is "
                       "allowed to run. On a slow link the default 10 seconds "
                       "is how a known-bad sample gets through.",
        reader=check_defender_cloud_timeout,
        read_value=lambda d: d.get("seconds"),
        on_steps=(_set_pref("-CloudExtendedTimeout 50"),),
        off_steps=(_set_pref("-CloudExtendedTimeout 0"),),
        desired=50,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="defender_pua",
        title="Potentially unwanted application blocking",
        category=Category.DEFENDER,
        description="Blocks adware, bundled toolbars, crypto-miners and "
                    "similar software that is not malware but is not wanted. "
                    "0 off, 1 block, 2 audit only.",
        why_it_matters="PUA is the single most common thing actually found on "
                       "a home machine, and off is the Windows default.",
        reader=check_pua_protection,
        read_value=lambda d: d.get("level"),
        on_steps=(_set_pref("-PUAProtection Enabled", _REVERT_ENABLED_AUDIT),),
        off_steps=(_set_pref("-PUAProtection Disabled", _REVERT_ENABLED_AUDIT),),
        # Enabled=1 blocks; AuditMode=2 only logs. This machine reads 2.
        desired=1,
        risk=Risk.LOW,
    ),

    SecurityControl(
        id="defender_network_protection",
        title="Network protection",
        category=Category.DEFENDER,
        description="Blocks outbound connections to domains and IPs with a "
                    "bad reputation, from any process. 0 off, 1 block, "
                    "2 audit.",
        why_it_matters="Cuts the callback: a payload that runs still cannot "
                       "reach the address it was told to phone home to.",
        reader=check_network_protection_defender,
        read_value=lambda d: d.get("level"),
        on_steps=(_set_pref("-EnableNetworkProtection Enabled", _REVERT_ENABLED_AUDIT),),
        off_steps=(_set_pref("-EnableNetworkProtection Disabled", _REVERT_ENABLED_AUDIT),),
        desired=1,
        risk=Risk.MEDIUM,
    ),

    SecurityControl(
        id="defender_controlled_folder_access",
        title="Controlled folder access",
        category=Category.DEFENDER,
        description="Only allow-listed applications may write to Documents, "
                    "Pictures and the other protected folders.",
        why_it_matters="This is the anti-ransomware control: an encryptor "
                       "that runs cannot write over the files it encrypted.",
        reader=check_controlled_folder_access,
        on_steps=(_set_pref("-EnableControlledFolderAccess Enabled", _REVERT_ENABLED_BOOL),),
        off_steps=(_set_pref("-EnableControlledFolderAccess Disabled", _REVERT_ENABLED_BOOL),),
        desired=True,
        risk=Risk.MEDIUM,
    ),

    # -- scan cost: a real setting the catalog has no security opinion on --
    SecurityControl(
        id="defender_cpu_usage",
        title="Scan CPU load limit",
        category=Category.DEFENDER,
        description="The share of one CPU a scheduled scan may use, as a "
                    "percentage. Windows' default is 50.",
        why_it_matters="A limit set very low makes a full scan take so long "
                       "it never finishes on a machine that gets shut down. "
                       "Very high makes the machine unusable while it runs. "
                       "Neither is a security verdict, so the catalog has no "
                       "opinion here (`desired=None`) and never counts this "
                       "as a problem.",
        reader=check_defender_cpu_usage,
        read_value=lambda d: d.get("percent"),
        on_steps=(_set_pref("-ScanAvgCPULoadFactor 50"),),
        off_steps=(_set_pref("-ScanAvgCPULoadFactor 25"),),
        desired=None,
        risk=Risk.LOW,
    ),

    # -- threat actions, one per severity ---------------------------------
    *(_threat_control(suffix, severity, param)
      for suffix, severity, param in _THREAT_ACTIONS),

    # -- read-only, and each says why -------------------------------------
    SecurityControl(
        id="defender_tamper_protection",
        title="Tamper Protection",
        category=Category.DEFENDER,
        description="Stops any program -- including this one -- from changing "
                    "Defender's settings.",
        why_it_matters="It is what keeps malware from turning Defender off "
                       "before it starts. It is also why the other Defender "
                       "controls on this tab will be refused while it is on.",
        reader=check_tamper_protection,
        read_only_reason="Tamper Protection exists specifically to refuse "
                         "programmatic changes to Defender. A button here "
                         "would silently do nothing. Change it in Windows "
                         "Security > Virus & threat protection > Manage "
                         "settings.",
    ),

    SecurityControl(
        id="defender_engine_version",
        read_value=lambda d: d.get("status"),
        title="Defender engine version",
        category=Category.DEFENDER,
        description="The version of the Defender scanning engine currently "
                    "loaded.",
        why_it_matters="An engine well behind the current one is a sign that "
                       "updates are not reaching this machine.",
        reader=check_defender_engine_version,
        read_only_reason="The engine version is set by Windows Update and "
                         "Defender platform updates. Update definitions from "
                         "the Defender tab instead of setting this.",
    ),

    SecurityControl(
        id="defender_exclusions",
        title="Scan exclusions",
        category=Category.DEFENDER,
        description="Paths, processes and file extensions Defender is told "
                    "to skip entirely.",
        why_it_matters="An exclusion is a hole with no expiry date, and "
                       "malware families specifically look for excluded "
                       "folders to live in. Defender will not even list them "
                       "to a non-administrator.",
        reader=check_defender_exclusions,
        read_only_reason="Removing an exclusion is a per-item decision -- "
                         "which one, and does something still depend on it -- "
                         "not a switch. Windows Security > Virus & threat "
                         "protection > Manage settings > Exclusions.",
    ),

    SecurityControl(
        id="defender_asr_rules",
        title="Attack surface reduction rules",
        category=Category.DEFENDER,
        description="How many of Defender's ASR rules are configured. Each "
                    "rule blocks one specific technique -- Office spawning "
                    "child processes, credential theft from LSASS, "
                    "executables running from mail.",
        why_it_matters="ASR is the highest-value hardening Defender offers "
                       "and none of it is on by default.",
        reader=check_defender_asr_rules,
        read_only_reason="Each rule is a separate GUID with its own Block / "
                         "Audit / Warn setting, and turning a set of them on "
                         "as one switch would hide which rule broke what. "
                         "This needs a per-rule editor, which the catalog "
                         "cannot express as one on/off pair.",
    ),

    SecurityControl(
        id="defender_applocker",
        title="AppLocker",
        category=Category.DEFENDER,
        description="Application allow-listing: only executables, scripts, "
                    "installers and DLLs matching a rule may run.",
        why_it_matters="Allow-listing is the strongest control on this page, "
                       "and it is what stops software nobody has ever seen "
                       "before from running at all.",
        reader=check_applocker,
        read_only_reason="AppLocker enforces a rule set. Turning enforcement "
                         "on without rules does nothing; turning it on with "
                         "the wrong rules locks you out of your own machine. "
                         "Author the rules in secpol.msc first.",
    ),

    SecurityControl(
        id="defender_elam",
        title="Early Launch Antimalware",
        category=Category.DEFENDER,
        description="The antimalware service is loaded before other boot "
                    "drivers, so it can vet them as they load.",
        why_it_matters="Without ELAM a rootkit that loads as a boot driver is "
                       "already resident by the time anything scans for it.",
        reader=check_elam,
        read_only_reason="This reads whether the antimalware service is "
                         "running, which follows from Defender being enabled "
                         "and is not separately settable. The ELAM driver "
                         "load policy itself is a boot setting under Group "
                         "Policy > System > Early Launch Antimalware.",
    ),
) + tuple(_asr_control(rule) for rule in asr_rules.RULES)

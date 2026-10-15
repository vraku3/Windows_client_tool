"""The logs and audit settings an investigation actually needs.

Two measurements drive this category, both taken on this machine on
2026-10-15:

* A log's `maxSize` is not the metric that matters -- COVERAGE is.
  `Microsoft-Windows-PowerShell/Operational` was at Windows' 15 MB default and
  held ONE day of history, because Script Block Logging fills it; `System` was
  at 20 MB and held six weeks. The readers here therefore report the oldest
  retained event and how many days that is, not just the size.
* `Microsoft-Windows-TaskScheduler/Operational` ships DISABLED, so "something
  runs at night" has no task history to read until it is turned on.

Reads are honest about refusals. `wevtutil gl` answers unelevated for every
channel here -- including `Security` -- so the SIZE is always known; only the
oldest event of `Security` needs administrator rights. When that one read is
refused the size is still shown and only the coverage line says it could not
be read. A read that could not be taken at all is `available: False`, never
"0 days".

Writes are `wevtutil sl` with app-owned constants (log name and byte size)
and a registry delete for the command-line value, so every step is
revertible and no string read off the machine reaches a command line.
"""
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Tuple

from .. import audit_policy, log_readiness
from ..security_reader import NEEDS_ADMIN, _reg_read
from .model import Category, Risk, SecurityControl

_MB = 1024 * 1024

#: CIS Windows 11 L1 baselines, in bytes.
_SECURITY_MIN = 196608 * 1024          # 192 MB
_SYSTEM_MIN = 32768 * 1024             # 32 MB
_POWERSHELL_MIN = 256 * _MB           # 256 MB -- 15 MB covered one day here

_SECURITY_DEFAULT = 20971520
_SYSTEM_DEFAULT = 20971520
_POWERSHELL_DEFAULT = 15728640

_CMD_LINE_KEY = (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion"
                 r"\Policies\System\Audit")
_CMD_LINE_VALUE = "ProcessCreationIncludeCmdLine_Enabled"

_SYSMON_CHANNEL = "Microsoft-Windows-Sysmon/Operational"


def _command(cmd: str) -> Dict[str, Any]:
    return {"type": "command", "cmd": cmd}


def _dword(key: str, value: str, data: int) -> Dict[str, Any]:
    return {"type": "registry", "key": key, "value": value, "data": data,
            "kind": "DWORD"}


def _coverage_text(status: log_readiness.LogStatus) -> str:
    if status.oldest is not None:
        days = status.coverage_days(datetime.now(timezone.utc))
        stamp = status.oldest.astimezone(timezone.utc)
        return (f"oldest event {stamp:%Y-%m-%d %H:%M} -- "
                f"{days:.1f} day(s) of history")
    if status.refused:
        return f"coverage could not be read ({status.refused})"
    return "log holds no events yet"


def _refusal(status: log_readiness.LogStatus, label: str) -> Dict[str, Any]:
    reason = status.refused or NEEDS_ADMIN
    return {"status": f"Unknown — {NEEDS_ADMIN}", "color": "amber",
            "available": False,
            "details": [(label, f"Could not read: {reason}")]}


def describe_log(status: log_readiness.LogStatus, threshold: int,
                 label: str) -> Dict[str, Any]:
    """A log's size, mode and coverage as a dashboard reader dict."""
    if status.present is False:
        return {"status": "Not present", "color": "amber", "available": True,
                "enabled": False,
                "details": [(label, "Channel is not present on this machine")]}
    if status.max_bytes is None:
        return _refusal(status, label)
    enabled = status.max_bytes >= threshold
    size_mb = status.max_bytes / _MB
    details = [(f"{label} size", f"{size_mb:.0f} MB "
                                f"(target {threshold / _MB:.0f} MB)"),
               (f"{label} mode", status.mode or "unknown"),
               ("Coverage", _coverage_text(status))]
    needed = threshold / _MB
    status_text = (f"{size_mb:.0f} MB, sized for an investigation"
                   if enabled else f"{size_mb:.0f} MB (below {needed:.0f} MB)")
    return {"status": status_text, "color": "green" if enabled else "amber",
            "available": True, "enabled": enabled, "details": details}


def _log_reader(name: str, threshold: int, label: str
                ) -> Callable[[], Dict[str, Any]]:
    def read() -> Dict[str, Any]:
        return describe_log(log_readiness.read_log(name), threshold, label)
    return read


def read_taskscheduler_channel() -> Dict[str, Any]:
    """Whether Task Scheduler's own per-run history channel is recording."""
    status = log_readiness.read_log(
        "Microsoft-Windows-TaskScheduler/Operational")
    label = "Task history"
    if status.present is False:
        return {"status": "Not present", "color": "amber", "available": True,
                "enabled": False,
                "details": [(label, "Channel is not present on this machine")]}
    if status.enabled is None:
        return _refusal(status, label)
    on = status.enabled
    details = [(label, "On" if on else "Off — no per-run history is recorded"),
               ("Coverage", _coverage_text(status))]
    return {"status": "On" if on else "Off", "color": "green" if on else "amber",
            "available": True, "enabled": on, "details": details}


def read_process_cmdline_auditing() -> Dict[str, Any]:
    """4688 includes the command line only when this value is set."""
    value = _reg_read(_CMD_LINE_KEY, _CMD_LINE_VALUE)
    on = value == 1
    if value is None:
        note = "Key not present — command lines are not recorded in 4688"
    else:
        note = ("Command lines included in 4688" if on
                else "Value is not 1 — command lines are not recorded")
    return {"status": "On" if on else "Off", "color": "green" if on else "amber",
            "available": True, "enabled": on,
            "details": [("Process creation command line",
                         note)]}


def read_sysmon() -> Dict[str, Any]:
    """Whether Sysmon's channel exists — read-only; this app deploys no drivers."""
    status = log_readiness.sysmon_status()
    if status.present is False:
        return {"status": "Not installed", "color": "amber", "available": True,
                "enabled": False,
                "details": [("Sysmon", "Channel is not present — Sysmon is not "
                                       "installed on this machine")]}
    if status.present is None:
        return _refusal(status, "Sysmon")
    details = [("Sysmon", "Installed and logging"),
               ("Coverage", _coverage_text(status))]
    return {"status": "Installed", "color": "green", "available": True,
            "enabled": True, "details": details}


def _audit_text(success: bool, failure: bool) -> str:
    if success and failure:
        return "Success and Failure"
    if success:
        return "Success"
    if failure:
        return "Failure"
    return "No Auditing"


def _audit_includes(sub: audit_policy.Subcategory,
                    setting: Tuple[bool, bool]) -> bool:
    success, failure = setting
    if sub.want_success and not success:
        return False
    if sub.want_failure and not failure:
        return False
    return True


def _audit_reader(sub: audit_policy.Subcategory) -> Callable[[], Dict[str, Any]]:
    def read() -> Dict[str, Any]:
        setting, reason = audit_policy.read(sub)
        if setting is None:
            return {"status": f"Unknown — {NEEDS_ADMIN}", "color": "amber",
                    "available": False,
                    "details": [(sub.name, reason or NEEDS_ADMIN)]}
        includes = _audit_includes(sub, setting)
        text = _audit_text(*setting)
        return {"status": text, "color": "green" if includes else "amber",
                "available": True, "enabled": includes,
                "details": [(sub.name, text),
                            ("Windows default", sub.default)]}
    return read


def _audit_step(sub: audit_policy.Subcategory, success: bool, failure: bool
                ) -> Dict[str, Any]:
    return _command(audit_policy.set_command(sub, success, failure))


#: What each subcategory is for, in the words an admin would use.
_AUDIT_WHY = {
    "Process Creation": "A process-creation event is the first line of almost "
                        "every intrusion timeline: without it there is no "
                        "record of what ran.",
    "Logon": "Logon events show who signed in, from where and how -- "
             "interactive, network, RDP or a service account.",
    "Account Lockout": "A lockout records a failed-password spray against an "
                       "account, often the first visible sign of an attack.",
    "Security Group Management": "Changes to groups are privilege escalation; "
                                 "an added administrator is the end of an "
                                 "attack, not the start.",
    "User Account Management": "Account creation and changes are how an "
                               "attacker keeps access after the first break-in.",
    "Audit Policy Change": "If someone can turn auditing off, every other "
                           "control here is worthless; this records the "
                           "attempt.",
}


def _audit_control(sub: audit_policy.Subcategory) -> SecurityControl:
    def_success, def_failure = audit_policy.default_flags(sub.default)
    control_id = "audit_" + sub.name.lower().replace(" ", "_")
    return SecurityControl(
        id=control_id,
        title=f"Audit: {sub.name}",
        category=Category.AUDIT_LOGGING,
        description=f"Whether Windows records {sub.name} events.",
        why_it_matters=_AUDIT_WHY[sub.name],
        reader=_audit_reader(sub),
        on_steps=(_audit_step(sub, sub.want_success or def_success,
                              sub.want_failure or def_failure),),
        off_steps=(_audit_step(sub, def_success, def_failure),),
        desired=True,
        risk=Risk.LOW,
    )


_SYSMON_REASON = (
    "Sysmon is a Sysinternals kernel driver. It is installed and configured "
    "deliberately, with a reviewed configuration file, and this app does not "
    "deploy drivers.")


CONTROLS: Tuple[SecurityControl, ...] = (
    SecurityControl(
        id="security_log_size",
        title="Security log size",
        category=Category.AUDIT_LOGGING,
        description="How much the Security log holds before it overwrites "
                    "the oldest events.",
        why_it_matters="Windows' default 20 MB can hold less than a day of "
                       "logons and process events under load. An investigation "
                       "is usually opened after the evidence is already gone.",
        reader=_log_reader("Security", _SECURITY_MIN, "Security log"),
        read_value=lambda d: d.get("enabled"),
        on_steps=(_command("wevtutil sl Security /ms:201326592"),),
        off_steps=(_command("wevtutil sl Security /ms:20971520"),),
        desired=True,
        risk=Risk.LOW,
    ),
    SecurityControl(
        id="system_log_size",
        title="System log size",
        category=Category.AUDIT_LOGGING,
        description="How much the System log holds before it overwrites the "
                    "oldest events.",
        why_it_matters="Service crashes, driver failures and unexpected "
                       "shutdowns land here; a small circular log loses the "
                       "first occurrence of a problem that recurs.",
        reader=_log_reader("System", _SYSTEM_MIN, "System log"),
        read_value=lambda d: d.get("enabled"),
        on_steps=(_command("wevtutil sl System /ms:33554432"),),
        off_steps=(_command("wevtutil sl System /ms:20971520"),),
        desired=True,
        risk=Risk.LOW,
    ),
    SecurityControl(
        id="application_log_size",
        title="Application log size",
        category=Category.AUDIT_LOGGING,
        description="How much the Application log holds before it overwrites "
                    "the oldest events.",
        why_it_matters="Application failures and updater errors are the "
                       "commonest first clue, and the default log is small.",
        reader=_log_reader("Application", _SYSTEM_MIN, "Application log"),
        read_value=lambda d: d.get("enabled"),
        on_steps=(_command("wevtutil sl Application /ms:33554432"),),
        off_steps=(_command("wevtutil sl Application /ms:20971520"),),
        desired=True,
        risk=Risk.LOW,
    ),
    SecurityControl(
        id="powershell_log_size",
        title="PowerShell operational log size",
        category=Category.AUDIT_LOGGING,
        description="How much the PowerShell operational log holds before it "
                    "overwrites the oldest events.",
        why_it_matters="With script block logging on, this channel fills fast: "
                       "at this machine's 15 MB default it held ONE day of "
                       "history. Offensive PowerShell is exactly what an "
                       "investigation reads it for.",
        reader=_log_reader("Microsoft-Windows-PowerShell/Operational",
                           _POWERSHELL_MIN, "PowerShell log"),
        read_value=lambda d: d.get("enabled"),
        on_steps=(_command("wevtutil sl Microsoft-Windows-PowerShell"
                           "/Operational /ms:268435456"),),
        off_steps=(_command("wevtutil sl Microsoft-Windows-PowerShell"
                            "/Operational /ms:15728640"),),
        desired=True,
        risk=Risk.LOW,
    ),
    SecurityControl(
        id="taskscheduler_log_enabled",
        title="Task Scheduler history",
        category=Category.AUDIT_LOGGING,
        description="Whether Task Scheduler records when each task ran and "
                    "how it ended, in its Operational channel.",
        why_it_matters="Windows ships this channel OFF, so a task that runs at "
                       "night leaves no history of what it did. It is the "
                       "first thing checked after 'something happened "
                       "overnight'.",
        reader=read_taskscheduler_channel,
        on_steps=(_command("wevtutil sl Microsoft-Windows-TaskScheduler"
                           "/Operational /e:true"),),
        off_steps=(_command("wevtutil sl Microsoft-Windows-TaskScheduler"
                            "/Operational /e:false"),),
        desired=True,
        risk=Risk.LOW,
    ),
    SecurityControl(
        id="process_cmdline_auditing",
        title="Command lines in process-creation events",
        category=Category.AUDIT_LOGGING,
        description="Whether event 4688 includes the full command line of "
                    "each new process.",
        why_it_matters="Without it, 4688 records the process name and not the "
                       "arguments -- and the arguments are where the malicious "
                       "command lives.",
        reader=read_process_cmdline_auditing,
        on_steps=(_dword(_CMD_LINE_KEY, _CMD_LINE_VALUE, 1),),
        off_steps=({"type": "registry_delete", "key": _CMD_LINE_KEY,
                    "value": _CMD_LINE_VALUE},),
        desired=True,
        risk=Risk.LOW,
    ),
    SecurityControl(
        id="sysmon_installed",
        title="Sysmon",
        category=Category.AUDIT_LOGGING,
        description="Sysmon's kernel driver, which records process, network "
                    "and file events far beyond what Windows logs natively.",
        why_it_matters="On a machine with Sysmon, process ancestry, hashes "
                       "and network connections are recorded; without it, "
                       "that detail does not exist to find.",
        reader=read_sysmon,
        read_only_reason=_SYSMON_REASON,
    ),
) + tuple(_audit_control(sub) for sub in audit_policy.SUBCATEGORIES)

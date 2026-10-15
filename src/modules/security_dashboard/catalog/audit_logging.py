"""Audit & Logging: will this machine still hold the evidence when it is needed?

Sizes come from `wevtutil gl`, which answers unelevated even for Security; only
the coverage line (the oldest event) needs elevation there. Every write is a
`wevtutil sl` with constant arguments, takes effect immediately, and has an
off step that puts Windows' default back. A refused read is `available: False`.
"""
import logging
import winreg
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Tuple

from .. import log_readiness
from .accounts import _dword
from .model import Category, Risk, SecurityControl

logger = logging.getLogger(__name__)

_MB = 1024 * 1024
_SECURITY = "Security"
_PS_LOG = "Microsoft-Windows-PowerShell/Operational"
_TASK_LOG = "Microsoft-Windows-TaskScheduler/Operational"
_AUDIT_KEY = (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion"
              r"\Policies\System\Audit")
_CMDLINE_VALUE = "ProcessCreationIncludeCmdLine_Enabled"

_UNKNOWN = "Unknown — needs administrator rights"
_SYSMON_WHY = ("Sysmon is a Sysinternals kernel driver; install and configure "
               "it deliberately, with a reviewed configuration -- this app "
               "does not deploy drivers.")


def _unavailable(st: log_readiness.LogStatus) -> Dict[str, Any]:
    reason = st.refused or "could not be read"
    return {"status": _UNKNOWN, "color": "amber", "available": False,
            "details": [(st.name, f"Could not read: {reason}")]}


def _coverage_line(st: log_readiness.LogStatus) -> Tuple[str, str]:
    if st.oldest is not None:
        days = st.coverage_days(datetime.now(timezone.utc)) or 0.0
        local = st.oldest.astimezone().strftime("%Y-%m-%d %H:%M")
        unit = "day" if round(days, 1) == 1.0 else "days"
        return ("Coverage",
                f"oldest event {local} -- {days:.1f} {unit} of history")
    if st.refused:
        return ("Coverage", f"could not be read: {st.refused}")
    return ("Coverage", "no events recorded yet")


def _size_reader(name: str, minimum: int) -> Callable[[], Dict[str, Any]]:
    def read() -> Dict[str, Any]:
        st = log_readiness.read_log(name)
        if st.present is None or (st.present and st.max_bytes is None):
            return _unavailable(st)
        if not st.present:
            return {"status": "Log not present", "color": "amber",
                    "available": True, "enabled": False,
                    "details": [(name, "This log does not exist on this PC")]}
        enough = st.max_bytes >= minimum
        return {
            "status": f"{st.max_bytes // _MB} MB",
            "color": "green" if enough else "amber",
            "available": True, "enabled": enough,
            "details": [("Size", f"{st.max_bytes // _MB} MB "
                                 f"(wanted: {minimum // _MB} MB or more)"),
                        ("Mode", st.mode or "unknown"),
                        _coverage_line(st)],
        }
    return read


def _read_taskscheduler() -> Dict[str, Any]:
    st = log_readiness.read_log(_TASK_LOG)
    if st.present is None or (st.present and st.enabled is None):
        return _unavailable(st)
    if not st.present:
        return {"status": "Log not present", "color": "amber",
                "available": True, "enabled": False,
                "details": [(_TASK_LOG, "This log does not exist on this PC")]}
    return {"status": "Recording" if st.enabled else "Not recording",
            "color": "green" if st.enabled else "amber",
            "available": True, "enabled": bool(st.enabled),
            "details": [("Task history", "on" if st.enabled else "off"),
                        ("Size", f"{(st.max_bytes or 0) // _MB} MB"),
                        _coverage_line(st)]}


def _read_sysmon() -> Dict[str, Any]:
    st = log_readiness.sysmon_status()
    if st.present is None:
        return _unavailable(st)
    return {"status": "Installed" if st.present else "Not installed",
            "color": "green" if st.present else "amber",
            "available": True, "enabled": st.present,
            "details": [("Sysmon channel",
                         "present" if st.present else "not present")]}


def _read_cmdline() -> Dict[str, Any]:
    path = _AUDIT_KEY.split("\\", 1)[1]
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            data, _ = winreg.QueryValueEx(key, _CMDLINE_VALUE)
        on = data == 1
    except FileNotFoundError:
        on = False
    except OSError as e:
        logger.warning("process_cmdline_auditing: registry read refused: %s", e)
        return {"status": _UNKNOWN, "color": "amber", "available": False,
                "details": [(_CMDLINE_VALUE, f"Could not read: {e}")]}
    return {"status": "Command lines recorded" if on else "Not recorded",
            "color": "green" if on else "amber",
            "available": True, "enabled": on,
            "details": [(_CMDLINE_VALUE, "1" if on else "not set")]}


def _sl(log: str, arg: str) -> Dict[str, Any]:
    return {"type": "command", "cmd": f"wevtutil sl {log} {arg}"}


def _size_control(cid: str, title: str, log: str, want: int, default: int,
                  reader, why: str) -> SecurityControl:
    return SecurityControl(
        id=cid, title=title, category=Category.AUDIT_LOGGING,
        description=f"How much of the {log} log Windows keeps before "
                    "overwriting the oldest events.",
        why_it_matters=why, reader=reader,
        on_steps=(_sl(log, f"/ms:{want}"),),
        off_steps=(_sl(log, f"/ms:{default}"),),
        desired=True, risk=Risk.LOW)


_SEC_MIN = 196_608 * 1024
_SYS_MIN = 32_768 * 1024
_PS_MIN = 256 * _MB
_DEFAULT = 20_971_520

CONTROLS: Tuple[SecurityControl, ...] = (
    _size_control(
        "security_log_size", "Security log size", _SECURITY, _SEC_MIN,
        _DEFAULT, _size_reader(_SECURITY, _SEC_MIN),
        "At the 20 MB default the Security log can roll over in hours on a "
        "busy machine, taking the logon and process evidence with it. CIS "
        "recommends 192 MB or more."),
    _size_control(
        "system_log_size", "System log size", "System", _SYS_MIN, _DEFAULT,
        _size_reader("System", _SYS_MIN),
        "Service installs and driver loads are recorded here; a small log "
        "forgets them first. CIS recommends 32 MB or more."),
    _size_control(
        "application_log_size", "Application log size", "Application",
        _SYS_MIN, _DEFAULT, _size_reader("Application", _SYS_MIN),
        "Application crashes and installer events live here. CIS recommends "
        "32 MB or more."),
    _size_control(
        "powershell_log_size", "PowerShell log size", _PS_LOG, _PS_MIN,
        15_728_640, _size_reader(_PS_LOG, _PS_MIN),
        "With script block logging on, the 15 MB default is full within "
        "about a day, so the evidence of what ran last week is already gone."),
    SecurityControl(
        id="taskscheduler_log_enabled", title="Task Scheduler history",
        category=Category.AUDIT_LOGGING,
        description="Whether Windows records when scheduled tasks start, "
                    "finish and fail.",
        why_it_matters="Disabled by default. When something runs at night, "
                       "task history is the first place to look, and with it "
                       "off there is nothing to read.",
        reader=_read_taskscheduler,
        on_steps=(_sl(_TASK_LOG, "/e:true"),),
        off_steps=(_sl(_TASK_LOG, "/e:false"),),
        desired=True, risk=Risk.LOW),
    SecurityControl(
        id="process_cmdline_auditing", title="Command line in process events",
        category=Category.AUDIT_LOGGING,
        description="Adds the full command line to Security event 4688 "
                    "(process creation).",
        why_it_matters="Without it a process-creation event says that "
                       "powershell.exe started, not what it was told to do.",
        reader=_read_cmdline,
        on_steps=(_dword(_AUDIT_KEY, _CMDLINE_VALUE, 1),),
        off_steps=({"type": "registry_delete", "key": _AUDIT_KEY,
                    "value": _CMDLINE_VALUE},),
        desired=True, risk=Risk.LOW),
    SecurityControl(
        id="sysmon_installed", title="Sysmon",
        category=Category.AUDIT_LOGGING,
        description="Whether the Sysmon event channel exists on this PC.",
        why_it_matters="Sysmon records process, network and file-creation "
                       "detail that the built-in logs do not.",
        reader=_read_sysmon, read_only_reason=_SYSMON_WHY,
        desired=True, risk=Risk.LOW),
)

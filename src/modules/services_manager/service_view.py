"""What the Services tab can say about a service beyond Task Manager's columns.

No Qt in here. Every judgement works on the plain dicts `get_services()` returns
(Name, Display Name, Status, Start Type, PID, Description, Impact, Path,
StartName), and a field WMI left empty is treated as unknown, never as a value.
"""
from typing import Callable, Dict, Iterable, List, Tuple

#: Accounts every service may run under without anyone having chosen them.
_BUILTIN_ACCOUNTS = {"localsystem", "nt authority\\system", "nt authority\\localservice",
                     "nt authority\\networkservice", "localservice", "networkservice",
                     "nt authority\\local service", "nt authority\\network service"}


def _text(svc: Dict, key: str) -> str:
    return str(svc.get(key) or "").strip()


def is_running(svc: Dict) -> bool:
    return _text(svc, "Status").lower() == "running"


def is_stopped(svc: Dict) -> bool:
    return _text(svc, "Status").lower() == "stopped"


def start_mode(svc: Dict) -> str:
    return _text(svc, "Start Type").lower()


def auto_but_stopped(svc: Dict) -> bool:
    """Set to start automatically, and not running.

    Not always a fault: Windows has many trigger-start services that are
    Automatic on paper and idle by design, and a service that ran and exited
    is normal too. It is the list to READ, which is why it is a chip and not
    a finding.
    """
    return start_mode(svc).startswith("auto") and is_stopped(svc)


def is_disabled(svc: Dict) -> bool:
    return start_mode(svc) == "disabled"


def logon_account(svc: Dict) -> str:
    return _text(svc, "StartName")


def custom_account(svc: Dict) -> bool:
    """Runs as a real account rather than one of the built-in service ones.

    A service under a person's or a service account is where password expiry,
    lockouts and "it stopped when they left the company" come from.
    """
    account = logon_account(svc).lower()
    return bool(account) and account not in _BUILTIN_ACCOUNTS


def is_third_party(svc: Dict) -> bool:
    """Its binary lives outside the Windows folder. svchost-hosted services
    all resolve to the Windows folder, so the ones Microsoft ships stay out."""
    path = _text(svc, "Path").lower().strip('"')
    if not path:
        return False
    return "\\windows\\" not in path


FILTERS: Tuple[Tuple[str, str, Callable[[Dict], bool]], ...] = (
    ("all", "All", lambda s: True),
    ("running", "Running", is_running),
    ("stopped", "Stopped", is_stopped),
    ("autostopped", "Auto, not running", auto_but_stopped),
    ("thirdparty", "Third-party", is_third_party),
    ("account", "Custom account", custom_account),
    ("disabled", "Disabled", is_disabled),
)
_FILTER_BY_KEY = {key: fn for key, _, fn in FILTERS}


def passes(filter_key: str, svc: Dict) -> bool:
    return _FILTER_BY_KEY.get(filter_key, _FILTER_BY_KEY["all"])(svc)


def filter_counts(services: Iterable[Dict]) -> Dict[str, int]:
    services = list(services)
    return {key: sum(1 for s in services if fn(s)) for key, _, fn in FILTERS}


def matches(svc: Dict, needle: str) -> bool:
    """Name, display name, description, path or account contains `needle`."""
    needle = needle.strip().lower()
    if not needle:
        return True
    return any(needle in _text(svc, key).lower()
               for key in ("Name", "Display Name", "Description", "Path", "StartName"))


def visible(services: Iterable[Dict], filter_key: str, needle: str) -> List[Dict]:
    return [s for s in services if passes(filter_key, s) and matches(s, needle)]


def detail_text(svc: Dict, required_by: List[str] = None) -> str:
    """Everything worth reading about one service, as plain text."""
    lines = [
        f"{_text(svc, 'Display Name') or _text(svc, 'Name')}   ({_text(svc, 'Name')})",
        f"Status:     {_text(svc, 'Status') or 'unknown'}"
        + (f"   PID {_text(svc, 'PID')}" if _text(svc, "PID") else ""),
        f"Start type: {_text(svc, 'Start Type') or 'unknown'}"
        + ("   (Automatic but not running)" if auto_but_stopped(svc) else ""),
        f"Log on as:  {logon_account(svc) or 'unknown'}"
        + ("   (a real account: check its password and lockout)" if custom_account(svc) else ""),
        f"Path:       {_text(svc, 'Path') or 'unknown'}",
        f"Impact:     {_text(svc, 'Impact') or 'unknown'}",
    ]
    description = _text(svc, "Description")
    if description:
        lines.append(f"About:      {description}")
    if required_by:
        lines.append(f"Needed by:  {', '.join(required_by[:8])}"
                     + (" …" if len(required_by) > 8 else ""))
    elif required_by is not None:
        lines.append("Needed by:  nothing else")
    return "\n".join(lines)


# ---- startup type (writes) -----------------------------------------------------

START_MODES: Tuple[Tuple[str, str], ...] = (
    ("Automatic", "auto"),
    ("Automatic (delayed)", "delayed-auto"),
    ("Manual", "demand"),
    ("Disabled", "disabled"),
)
_MODE_ARG = dict(START_MODES)


def set_start_type(name: str, label: str):
    """Change a service's startup type with `sc config`. Returns (ok, message).

    Success is the tool's own "SUCCESS" line AND a zero exit: `sc` writes its
    refusal ("OpenService FAILED 5: Access is denied") to stdout, so the exit
    code alone is not trusted either way. The caller re-reads the service
    afterwards, which is what actually proves the change.
    """
    import logging
    import re
    import subprocess
    mode = _MODE_ARG.get(label)
    if mode is None:
        return False, f"unknown startup type {label!r}"
    if not re.fullmatch(r"[\w .\-$@]+", name or ""):
        return False, f"refusing an odd service name {name!r}"
    try:
        done = subprocess.run(
            ["sc.exe", "config", name, "start=", mode],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        logging.getLogger(__name__).warning("sc config failed: %s", e)
        return False, str(e)
    out = (done.stdout or done.stderr or "").strip()
    if done.returncode == 0 and "SUCCESS" in out.upper():
        return True, out
    return False, out or f"sc exited {done.returncode}"

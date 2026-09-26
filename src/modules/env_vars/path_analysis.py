"""PATH analysis. Qt-free.

Findings a sysadmin cares about: entries that do not exist, duplicates,
two folders holding the same executable name (the earlier one wins, so the
later one is shadowed), and folders early in the SYSTEM path that ordinary
users can write to (a planting risk).

A folder we could not inspect is reported as such, never as "fine".
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

EXEC_EXTS = (".exe", ".com", ".bat", ".cmd")
# how many leading system entries count as "early" for the writable finding
EARLY_ENTRIES = 8

SEV_WARN = "warning"
SEV_INFO = "info"
SEV_UNKNOWN = "unknown"

_USERS_PRINCIPALS = ("BUILTIN\\USERS", "NT AUTHORITY\\AUTHENTICATED USERS", "EVERYONE")
_WRITE_RIGHTS = {"F", "M", "W", "WD", "AD", "GW", "GA"}


@dataclass
class PathEntry:
    index: int
    raw: str
    expanded: str
    exists: Optional[bool]      # None: could not tell
    duplicate_of: Optional[int] = None


@dataclass
class Finding:
    severity: str
    kind: str
    entry_index: int
    message: str


@dataclass
class PathReport:
    entries: List[PathEntry] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)


def split_path(value: str) -> List[str]:
    """Split on ';' honouring double-quoted segments; drops empty entries."""
    parts: List[str] = []
    cur: List[str] = []
    quoted = False
    for ch in value:
        if ch == '"':
            quoted = not quoted
            continue
        if ch == ";" and not quoted:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def expand(raw: str, environ: Optional[Dict[str, str]] = None) -> str:
    """Expand %VAR% references; unknown variables are left as written."""
    env = {k.upper(): v for k, v in (environ if environ is not None else os.environ).items()}

    def sub(m: "re.Match[str]") -> str:
        return env.get(m.group(1).upper(), m.group(0))

    return re.sub(r"%([^%]+)%", sub, raw)


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p)).rstrip("\\")


def parse_icacls(output: str) -> Optional[bool]:
    """True if a broad group (Users/Authenticated Users/Everyone) can write.

    None when the output carries no ACE lines at all (a refusal).
    """
    saw = False
    for line in output.splitlines():
        m = re.search(r":((?:\([^)]*\))+)\s*$", line)
        if not m:
            continue
        saw = True
        principal = line[:m.start()].strip().upper()
        if not any(principal.endswith(p) for p in _USERS_PRINCIPALS):
            continue
        groups = [g.strip().upper() for g in re.findall(r"\(([^)]*)\)", m.group(1))]
        if "DENY" in groups:
            continue
        rights = set()
        for g in groups:
            rights.update(x.strip() for x in g.split(","))
        if rights & _WRITE_RIGHTS:
            return True
    return False if saw else None


def icacls_writable(path: str) -> Optional[bool]:
    try:
        proc = subprocess.run(["icacls", path], capture_output=True, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("icacls %s failed: %s", path, e)
        return None
    if proc.returncode != 0:
        return None
    return parse_icacls(proc.stdout)


def list_executables(folder: str) -> Optional[set]:
    try:
        return {n.lower() for n in os.listdir(folder) if n.lower().endswith(EXEC_EXTS)}
    except OSError as e:
        logger.debug("cannot list %s: %s", folder, e)
        return None


def analyse(value: str, is_system: bool = False,
            environ: Optional[Dict[str, str]] = None,
            exists_fn: Callable[[str], Optional[bool]] = None,
            list_fn: Callable[[str], Optional[set]] = list_executables,
            writable_fn: Callable[[str], Optional[bool]] = icacls_writable) -> PathReport:
    rep = PathReport()
    exists_fn = exists_fn or (lambda p: os.path.isdir(p))
    seen: Dict[str, int] = {}
    for i, raw in enumerate(split_path(value)):
        exp = expand(raw, environ)
        try:
            ex: Optional[bool] = bool(exists_fn(exp))
        except OSError as e:
            logger.debug("exists check failed for %s: %s", exp, e)
            ex = None
        ent = PathEntry(i, raw, exp, ex)
        key = _norm(exp)
        if key in seen:
            ent.duplicate_of = seen[key]
            rep.findings.append(Finding(SEV_INFO, "duplicate", i,
                                        f"Duplicate of entry {seen[key] + 1}: {exp}"))
        else:
            seen[key] = i
        if "%" in exp:
            rep.findings.append(Finding(SEV_WARN, "unexpanded", i,
                                        f"Contains a variable that is not defined: {raw}"))
        elif ex is False:
            rep.findings.append(Finding(SEV_WARN, "missing", i, f"Folder does not exist: {exp}"))
        elif ex is None:
            rep.findings.append(Finding(SEV_UNKNOWN, "unreadable", i, f"Could not check: {exp}"))
        rep.entries.append(ent)
    _shadowing(rep, list_fn)
    if is_system:
        _writable(rep, writable_fn)
    return rep


def _shadowing(rep: PathReport, list_fn) -> None:
    owner: Dict[str, int] = {}
    for ent in rep.entries:
        if ent.exists is not True or ent.duplicate_of is not None:
            continue
        names = list_fn(ent.expanded)
        if names is None:
            rep.findings.append(Finding(SEV_UNKNOWN, "unreadable", ent.index,
                                        f"Could not list folder: {ent.expanded}"))
            continue
        for n in sorted(names):
            first = owner.get(n)
            if first is None:
                owner[n] = ent.index
            else:
                rep.findings.append(Finding(
                    SEV_INFO, "shadowed", ent.index,
                    f"{n} here is shadowed by entry {first + 1}"))


def _writable(rep: PathReport, writable_fn) -> None:
    for ent in rep.entries[:EARLY_ENTRIES]:
        if ent.exists is not True or ent.duplicate_of is not None:
            continue
        w = writable_fn(ent.expanded)
        if w is True:
            rep.findings.append(Finding(
                SEV_WARN, "writable", ent.index,
                f"Ordinary users can write to {ent.expanded} (entry {ent.index + 1} of the system PATH): "
                "a program planted there is found before later entries."))
        elif w is None:
            rep.findings.append(Finding(SEV_UNKNOWN, "writable_unknown", ent.index,
                                        f"Could not read permissions of {ent.expanded}"))


def summarize(rep: PathReport) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for f in rep.findings:
        out[f.kind] = out.get(f.kind, 0) + 1
    return out

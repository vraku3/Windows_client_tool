r"""Does Windows actually need a restart? One answer for the whole app.

Qt-free. The trap this exists for, measured 2026-10-10: the Overview said
"A restart is pending" after EVERY reboot. The only marker present was
`PendingFileRenameOperations`, and every one of its 9 entries was a
DELETION of an updater's leftovers -- OneDrive (which updated itself at
14:34, three hours after the 11:09 boot, and queued its previous version's
folder) and Edge (`Edge\Temp\...\old_msedge.exe`), plus a Gaming Services
proxy DLL. Those updaters run after every boot, so the list is never empty
for long, and a restart only empties it until the next update. Windows'
own Settings does not count them as a pending restart either.

So a restart is NEEDED only for:

* `Component Based Servicing\RebootPending` -- Windows servicing;
* `WindowsUpdate\Auto Update\RebootRequired` -- Windows Update;
* a queued file REPLACEMENT (a rename with a destination) -- an installer
  swapping a file that was in use, which really completes only at boot.

Queued deletions are housekeeping: they are reported as such (by program,
so nobody wonders what they are), never as "restart pending".

Entries may carry a `*N` prefix before `\??\` (seen here: `*1\??\C:\...`);
it is stripped before the path is read.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

SESSION_MANAGER = r"SYSTEM\CurrentControlSet\Control\Session Manager"
REBOOT_KEYS = (
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
     "servicing (CBS)"),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired",
     "Windows Update"),
)

DELETE, REPLACE = "delete", "replace"

#: (fragment of a lower-cased path, the program to name)
_OWNERS = (
    ("\\microsoft onedrive\\", "OneDrive"),
    ("\\microsoft\\edgewebview\\", "Edge WebView2"),
    ("\\microsoft\\edge", "Microsoft Edge"),
    ("gamingservices", "Gaming Services"),
    ("\\nvidia", "NVIDIA"),
    ("\\amd\\", "AMD"),
    ("\\google\\", "Google"),
    ("\\windows\\system32\\", "Windows"),
    ("\\windows\\syswow64\\", "Windows"),
    ("\\windows\\winsxs\\", "Windows"),
)


@dataclass
class PendingOp:
    kind: str           # DELETE | REPLACE
    path: str
    target: str = ""

    @property
    def owner(self) -> str:
        return owner_of(self.path)


@dataclass
class PendingRestart:
    reasons: List[str] = field(default_factory=list)          # what NEEDS the restart
    housekeeping: List[PendingOp] = field(default_factory=list)  # deletions only
    unreadable: List[str] = field(default_factory=list)

    @property
    def needed(self) -> bool:
        return bool(self.reasons)


def owner_of(path: str) -> str:
    low = path.lower()
    for fragment, name in _OWNERS:
        if fragment in low:
            return name
    parts = [p for p in re.split(r"[\\/]", path) if p]
    for marker in ("program files", "program files (x86)", "programdata"):
        if marker in [p.lower() for p in parts]:
            i = [p.lower() for p in parts].index(marker)
            if i + 1 < len(parts):
                return parts[i + 1]
    return parts[1] if len(parts) > 1 else path


def _clean(raw: str) -> str:
    text = re.sub(r"^\*\d+", "", raw or "")
    return text[4:] if text.startswith("\\??\\") else text


def parse_operations(values) -> List[PendingOp]:
    """REG_MULTI_SZ pairs (source, destination); an empty destination is a delete."""
    if isinstance(values, str):
        values = [values]
    values = list(values or [])
    ops: List[PendingOp] = []
    for i in range(0, len(values), 2):
        src = _clean(values[i])
        dst = _clean(values[i + 1]) if i + 1 < len(values) else ""
        if src:
            ops.append(PendingOp(REPLACE if dst else DELETE, src, dst))
    return ops


def read_operations() -> Optional[List[PendingOp]]:
    """The queued boot-time file operations; [] for none, None if unreadable."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, SESSION_MANAGER) as k:
            values, _kind = winreg.QueryValueEx(k, "PendingFileRenameOperations")
    except FileNotFoundError:
        return []
    except OSError as e:
        logger.warning("PendingFileRenameOperations unreadable: %s", e)
        return None
    return parse_operations(values)


def key_present(path: str) -> Optional[bool]:
    import winreg
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path))
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.warning("pending-restart key %s unreadable: %s", path, e)
        return None


def check(key_exists: Callable[[str], Optional[bool]] = key_present,
          operations: Callable[[], Optional[List[PendingOp]]] = read_operations) -> PendingRestart:
    out = PendingRestart()
    for path, label in REBOOT_KEYS:
        found = key_exists(path)
        if found:
            out.reasons.append(label)
        elif found is None:
            out.unreadable.append(path.rsplit("\\", 1)[-1])
    ops = operations()
    if ops is None:
        out.unreadable.append("PendingFileRenameOperations")
        return out
    replacements = [op for op in ops if op.kind == REPLACE]
    if replacements:
        owners = sorted({op.owner for op in replacements})
        out.reasons.append(f"{len(replacements)} file replacement(s) queued by {', '.join(owners)}")
    out.housekeeping = [op for op in ops if op.kind == DELETE]
    return out


def housekeeping_text(ops: List[PendingOp]) -> str:
    """'OneDrive, Microsoft Edge: 9 leftover files' -- for a tooltip or a note."""
    if not ops:
        return ""
    owners = sorted({op.owner for op in ops})
    return f"{', '.join(owners)}: {len(ops)} leftover file(s) queued for deletion at the next restart"


def restart_now(delay_seconds: int = 10) -> tuple:
    """Ask Windows to restart in `delay_seconds` (cancellable with `shutdown /a`).
    Returns (ok, message). Only ever called after the person confirmed."""
    try:
        proc = subprocess.run(
            ["shutdown", "/r", "/t", str(int(delay_seconds)), "/d", "p:4:1",
             "/c", "Restarting to finish pending Windows changes (Windows Client Tool)."],
            capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or f"shutdown exited {proc.returncode}").strip()
    return True, f"Windows will restart in {delay_seconds} seconds."

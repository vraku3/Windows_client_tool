"""Non-destructive conflict checks before Game Mode ON (Qt-free)."""
from __future__ import annotations

from typing import Iterable, List, Optional


def conflicts(*, pending_reboot: bool = False, reboot_reasons: Optional[Iterable[str]] = None,
              wu_installing: bool = False, owner: Optional[str] = None,
              maintenance_active: bool = False) -> List[str]:
    rows = []
    if pending_reboot:
        reasons = [str(r) for r in (reboot_reasons or []) if str(r).strip()]
        rows.append("A restart is pending"
                    + (f" ({', '.join(reasons[:3])})" if reasons else "")
                    + "; Game Mode changes may be undone by the restart.")
    if wu_installing:
        rows.append("Windows Update is installing; wait for it to finish.")
    if owner:
        rows.append(f"Another operation is running: {owner}.")
    if maintenance_active:
        rows.append("Windows Automatic Maintenance is running; pausing it now may "
                    "interrupt a task already in progress.")
    return rows

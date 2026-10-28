"""Evidence shown before a maintenance run starts (Qt-free).

Combines two facts the operator should see before anything is installed:
whether Windows already wants a restart, and whether a previous run was cut
off. A missing answer is reported as unreadable, never as "no".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from modules.updates.run_checkpoint import item_summary, unfinished


@dataclass
class RebootPreflight:
    reasons: Optional[List[str]]
    unreadable: bool = False
    unfinished_run: Optional[dict] = None

    @property
    def should_ask(self) -> bool:
        return bool(self.reasons) or self.unfinished_run is not None

    def evidence_lines(self) -> List[str]:
        lines: List[str] = []
        if self.reasons:
            lines.append("Windows already wants a restart because of: "
                         + ", ".join(self.reasons) + ".")
            lines.append("Installing now may mix pre-restart and post-restart state; "
                         "the second half of an update can fail until Windows restarts.")
        elif self.unreadable:
            lines.append("Could not read the pending-restart state; continuing without that evidence.")
        run = self.unfinished_run
        if run:
            lines.append("A previous maintenance run did not finish"
                         + (f" ({run.get('context')} from {run.get('started')})" if run.get("context") else "")
                         + ".")
            recorded = item_summary(run)
            if recorded:
                lines.append("Last recorded transitions:")
                lines.extend(f"  {row}" for row in recorded[-10:])
        return lines


def assess(app_data_dir, reboot_reader=None) -> RebootPreflight:
    """Read the restart evidence without touching Windows."""
    reader = reboot_reader or _read_reboot
    reasons, unreadable = reader()
    return RebootPreflight(reasons=reasons, unreadable=unreadable,
                           unfinished_run=unfinished(app_data_dir) if app_data_dir else None)


def _read_reboot():
    try:
        from core.pending_reboot import check
        state = check()
    except Exception:                    # the preflight must never block a run by crashing
        import logging
        logging.getLogger(__name__).warning("Pending-restart evidence unreadable", exc_info=True)
        return None, True
    if state.unreadable and not state.reasons:
        return None, True
    return list(state.reasons), False

"""Optional System Restore point before an app/Store update batch."""
from __future__ import annotations

from typing import Callable, Optional


def ensure_restore_point(
    app,
    purpose: str,
    log: Callable[[str], None],
    create: Optional[Callable[[str], tuple]] = None,
) -> bool:
    """Create a restore point when the setting is on.

    Missing/off is a no-op. A requested restore point that cannot be created
    raises instead of letting the batch proceed with no way back. `create`
    exists for tests; the real call is `core.system_restore.create_restore_point`.
    """
    if not app.config.get("updates.restore_point_before_app_updates", False):
        return False
    if create is None:
        from core.system_restore import create_restore_point as create
    log(f"Creating a System Restore point before {purpose}...")
    ok, _output = create(f"Windows Client Tool - {purpose}")
    if not ok:
        raise RuntimeError(
            f"Nothing was installed: the System Restore point for {purpose} could not "
            "be created (System Restore off or blocked by policy?). Turn off 'Create a "
            "System Restore point before app and Store update batches' in Settings to "
            "continue without one.")
    log("Restore point created.")
    return True

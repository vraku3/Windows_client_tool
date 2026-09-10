"""Undo an update THIS FEATURE applied -- not general driver history,
which stays Device Manager's job (see CLAUDE.md's Driver Manager section
and this phase's spec). Trusts the driver store to still hold the
pre-update package as a superseded one, per the spec's resolved Open
Question -- Windows measurably keeps these (this codebase's own Cleanup
module data), so this does not copy packages separately at snapshot time.
"""
import logging
import os
from typing import List, Optional

from core.system_restore import create_restore_point
from modules.cleanup.cleanup_scanner.driver_store import store_folder_for
from modules.driver_manager.driver_reader import DriverInfo, published_name_for
from modules.driver_manager.vendor_updates.pipeline import InstallResult, _run_pnputil_install

logger = logging.getLogger(__name__)


def snapshot_before_install(driver: DriverInfo) -> Optional[str]:
    """The OEM published name (e.g. 'oem12.inf') of driver's CURRENT
    package, to roll back to later -- or None if there's nothing to roll
    back to (a driverless device, or an inbox driver pnputil can't
    address by an oem number)."""
    return published_name_for(driver.inf_name)


def rollback(token: str) -> InstallResult:
    """Reverses one snapshot_before_install token through the identical
    restore-point -> write -> verify discipline install_light uses -- a
    rollback is a write too, no shortcuts."""
    if not token:
        logger.warning("rollback: called with no token (nothing to roll back to)")
        return InstallResult(ok=False,
                             reason="no rollback token recorded for this device",
                             restore_point_taken=False)
    ok, reason = create_restore_point(f"Before rolling back driver update: {token}")
    if not ok:
        return InstallResult(ok=False,
                             reason=f"could not take a restore point, "
                                    f"refusing to proceed: {reason}",
                             restore_point_taken=False)
    folder = store_folder_for(token)
    if folder is None:
        return InstallResult(ok=False,
                             reason=f"the driver store no longer has {token} "
                                    f"-- it may have been pruned, so this "
                                    f"update cannot be automatically rolled "
                                    f"back",
                             restore_point_taken=True)
    inf_path = os.path.join(folder, token)
    ok, reason = _run_pnputil_install(inf_path)
    if not ok:
        return InstallResult(ok=False, reason=reason, restore_point_taken=True)
    return InstallResult(ok=True, reason="", restore_point_taken=True)


def bulk_rollback(tokens: List[str]) -> List[InstallResult]:
    """Every token from one update session, in one action -- a partial
    failure (some rolled back, some not) is reported as exactly that, per
    result, never collapsed into one pass/fail."""
    return [rollback(token) for token in tokens]

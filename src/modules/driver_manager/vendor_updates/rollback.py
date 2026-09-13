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
from modules.cleanup.cleanup_scanner.driver_store import file_repository, store_folder_for
from modules.driver_manager.driver_reader import DriverInfo, published_name_for
from modules.driver_manager.vendor_updates.pipeline import InstallResult, _run_pnputil_install

logger = logging.getLogger(__name__)


def snapshot_before_install(driver: DriverInfo) -> Optional[str]:
    """The OEM published name (e.g. 'oem12.inf') of driver's CURRENT
    package, to roll back to later -- or None if there's nothing to roll
    back to (a driverless device, or an inbox driver pnputil can't
    address by an oem number)."""
    return published_name_for(driver.inf_name)


def rollback(token: str, take_restore_point: bool = True) -> InstallResult:
    """Reverses one snapshot_before_install token through the identical
    restore-point -> write -> verify discipline install_light uses -- a
    rollback is a write too, no shortcuts.

    take_restore_point=False lets bulk_rollback take exactly ONE restore
    point for a whole batch rather than one per token (Finding I6) --
    the caller has already taken it and is vouching for that with the
    returned InstallResult still reporting restore_point_taken=True.
    """
    if not token:
        logger.warning("rollback: called with no token (nothing to roll back to)")
        return InstallResult(ok=False,
                             reason="no rollback token recorded for this device",
                             restore_point_taken=False)
    if take_restore_point:
        ok, reason = create_restore_point(f"Before rolling back driver update: {token}")
        if not ok:
            return InstallResult(ok=False,
                                 reason=f"could not take a restore point, "
                                        f"refusing to proceed: {reason}",
                                 restore_point_taken=False)
        restore_point_taken = True
    else:
        # The caller (bulk_rollback) already took one restore point for
        # this whole batch.
        restore_point_taken = True

    folder = store_folder_for(token)
    if folder is None:
        return InstallResult(ok=False,
                             reason=f"the driver store no longer has {token} "
                                    f"-- it may have been pruned, so this "
                                    f"update cannot be automatically rolled "
                                    f"back",
                             restore_point_taken=restore_point_taken)
    # store_folder_for() returns a bare FileRepository folder NAME (e.g.
    # "amdgpio2.inf_amd64_e3ba65168b9dbb29"), never a full path -- and the
    # real .inf file inside that folder is the vendor's ORIGINAL filename,
    # never the OEM published name (`token`) that addresses the package as
    # a whole. See CLAUDE.md's Driver Manager section.
    package_dir = os.path.join(file_repository(), folder)
    try:
        infs = [f for f in os.listdir(package_dir) if f.lower().endswith(".inf")]
    except OSError as exc:
        logger.warning("rollback: cannot read package folder %s: %s", package_dir, exc)
        return InstallResult(ok=False,
                             reason=f"the driver store folder for {token} "
                                    f"could not be read: {exc}",
                             restore_point_taken=restore_point_taken)
    if not infs:
        return InstallResult(ok=False,
                             reason=f"the driver store folder for {token} "
                                    f"contains no INF file",
                             restore_point_taken=restore_point_taken)
    inf_path = os.path.join(package_dir, infs[0])
    ok, reason = _run_pnputil_install(inf_path)
    if not ok:
        return InstallResult(ok=False, reason=reason, restore_point_taken=restore_point_taken)
    return InstallResult(ok=True, reason="", restore_point_taken=restore_point_taken)


def bulk_rollback(tokens: List[str]) -> List[InstallResult]:
    """Every token from one update session, in one action -- a partial
    failure (some rolled back, some not) is reported as exactly that, per
    result, never collapsed into one pass/fail.

    Takes exactly ONE restore point for the whole batch (Finding I6) --
    not one Checkpoint-Computer call per token, which is what the naive
    "just call rollback() N times" version did. If the batch restore
    point itself can't be taken, every token is refused up front, cleanly,
    with no pnputil calls attempted at all.
    """
    if not tokens:
        return []
    ok, reason = create_restore_point(f"Before rolling back {len(tokens)} driver update(s)")
    if not ok:
        return [InstallResult(ok=False,
                              reason=f"could not take a restore point for "
                                     f"the batch, refusing to proceed: {reason}",
                              restore_point_taken=False)
               for _ in tokens]
    return [rollback(token, take_restore_point=False) for token in tokens]

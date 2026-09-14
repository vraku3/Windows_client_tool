"""Windows Update's OWN driver channel -- genuinely vendor-agnostic,
unlike every other module in this package (each built against one
company's own site). This is what makes "regardless of the PC" real:
Intel in particular distributes the vast majority of its consumer
graphics/WiFi/chipset driver updates through Windows Update rather than
a scrapable web page -- intel.com's own download-center site is
broadly Akamai-blocked, even its homepage 403s to a non-browser
request (confirmed live 2026-09-14, a stronger block than AMD's,
NVIDIA's, or Realtek's sites all needed). This is Intel's OWN preferred
distribution channel for most consumer systems, not a workaround
reached for because the real site was unreachable -- and it applies
equally to any other vendor enrolled in Windows Update's driver
program, not just Intel.

Verified against Microsoft's own documented WUA API
(IWindowsDriverUpdate, wuapi.h -- DriverHardwareID, DriverManufacturer,
DriverClass; present since Windows XP/2000) -- NOT empirically tested
end to end against a real pending driver install. This development
machine has zero pending updates of any kind right now (confirmed live:
both a plain search and a Type='Driver' one return 0 results), so there
is nothing real to install-test against yet. The search mechanism and
property access are real and documented; the actual install call reuses
modules.updates.windows_updater.install_updates_iter verbatim (already
tested, shipped code -- not duplicated here).
"""
import logging
from dataclasses import dataclass
from typing import Optional

from modules.driver_manager.driver_reader import DriverInfo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WindowsUpdateDriverMatch:
    title: str
    manufacturer: str
    driver_class: str
    identity: object  # the raw IUpdate COM object -- install_updates_iter's input


def find_windows_update_driver(driver: DriverInfo) -> Optional[WindowsUpdateDriverMatch]:
    """Searches Windows Update's own driver-classified queue for one
    matching driver's hardware_id. Must run in a COMWorker
    (CoInitialize already done) -- this is a real network+service round
    trip, the same reason every other check in this package backgrounds
    itself off the UI thread.

    Never raises: a missing hardware_id, a failed search, or a
    malformed property on one candidate update are all a clean None
    (or, for one bad candidate, skipped rather than aborting the whole
    scan) -- matching this package's "a refusal is a clean answer, never
    an uncaught exception" rule throughout.
    """
    if not driver.hardware_id:
        return None
    import win32com.client
    try:
        session = win32com.client.Dispatch("Microsoft.Update.Session")
        searcher = session.CreateUpdateSearcher()
        result = searcher.Search("IsInstalled=0 and IsHidden=0 and Type='Driver'")
    except Exception as exc:  # noqa: BLE001 -- a COM search failure is a
        # real, expected refusal mode (no WU service, no network, policy
        # blocking it), not a bug -- must come back as None, never raise
        # out of a check that every call site treats as backgroundable.
        logger.warning("windows_update_driver_check: search failed: %s", exc)
        return None

    target = driver.hardware_id.strip().upper()
    try:
        count = result.Updates.Count
    except Exception as exc:
        logger.warning("windows_update_driver_check: could not read search "
                       "results: %s", exc)
        return None

    for i in range(count):
        try:
            u = result.Updates.Item(i)
            hwid = (u.DriverHardwareID or "").strip().upper()
        except Exception as exc:
            logger.warning("windows_update_driver_check: could not read "
                           "update %d: %s", i, exc)
            continue
        if not hwid:
            continue
        # A driver update's own hardware_id can be a more general
        # PCI\VEN_x&DEV_y prefix than the device's own, more specific
        # one (which may carry &SUBSYS_.../&REV_...) -- match by prefix
        # either direction, never exact-equality only.
        if not (target.startswith(hwid) or hwid.startswith(target)):
            continue
        try:
            manufacturer = u.DriverManufacturer or "Unknown"
        except Exception:
            manufacturer = "Unknown"
        try:
            driver_class = u.DriverClass or "Unknown"
        except Exception:
            driver_class = "Unknown"
        return WindowsUpdateDriverMatch(
            title=u.Title, manufacturer=manufacturer,
            driver_class=driver_class, identity=u)
    return None

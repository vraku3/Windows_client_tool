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


def _search_pending_driver_updates() -> Optional[list]:
    """The raw list of pending IUpdate COM objects Windows Update
    currently has queued as driver-type updates -- ONE real search, no
    matter how many devices end up matched against it (a bulk sweep
    calling find_windows_update_driver once per device would mean one
    full WU service round trip per device, easily 100+ redundant
    searches; see find_windows_update_drivers_for_many). None (never
    raises) on any search failure -- a real, expected refusal mode (no
    WU service, no network, policy blocking it), not a bug."""
    import win32com.client
    try:
        session = win32com.client.Dispatch("Microsoft.Update.Session")
        searcher = session.CreateUpdateSearcher()
        result = searcher.Search("IsInstalled=0 and IsHidden=0 and Type='Driver'")
        count = result.Updates.Count
    except Exception as exc:  # noqa: BLE001 -- see docstring
        logger.warning("windows_update_driver_check: search failed: %s", exc)
        return None
    return [result.Updates.Item(i) for i in range(count)]


def _match_hardware_id(candidates: list, hardware_id: str) -> Optional[WindowsUpdateDriverMatch]:
    """The first candidate IUpdate whose own DriverHardwareID matches
    hardware_id by prefix either direction -- a driver update's own
    hardware_id can be a more general PCI\\VEN_x&DEV_y prefix than a
    device's own, more specific one (which may carry
    &SUBSYS_.../&REV_...), so never exact-equality only. One malformed
    candidate is skipped (logged), never aborts matching the rest."""
    target = hardware_id.strip().upper()
    for i, u in enumerate(candidates):
        try:
            hwid = (u.DriverHardwareID or "").strip().upper()
        except Exception as exc:
            logger.warning("windows_update_driver_check: could not read "
                           "update %d: %s", i, exc)
            continue
        if not hwid:
            continue
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


def find_windows_update_driver(driver: DriverInfo) -> Optional[WindowsUpdateDriverMatch]:
    """Searches Windows Update's own driver-classified queue for one
    matching driver's hardware_id. Must run in a COMWorker
    (CoInitialize already done) -- this is a real network+service round
    trip, the same reason every other check in this package backgrounds
    itself off the UI thread. Never raises -- see _search_pending_driver_updates
    and _match_hardware_id, which this composes. For checking many
    devices at once, use find_windows_update_drivers_for_many instead --
    calling this once per device means one full WU search per device."""
    if not driver.hardware_id:
        return None
    candidates = _search_pending_driver_updates()
    if candidates is None:
        return None
    return _match_hardware_id(candidates, driver.hardware_id)


def find_windows_update_drivers_for_many(drivers) -> dict:
    """The bulk-sweep equivalent of find_windows_update_driver: ONE real
    WU search, matched in memory against every driver in drivers that
    has a hardware_id. Returns a dict keyed by device_id -- only devices
    with a real match are present. Must run in a COMWorker, same as the
    single-device version. Empty dict (never raises) if the search
    itself fails or nothing has a hardware_id to match on. Never even
    calls into WU at all when drivers is empty -- every vendor-specific
    check already matched, nothing left for this pass to add."""
    if not drivers:
        return {}
    candidates = _search_pending_driver_updates()
    if not candidates:
        return {}
    matches = {}
    for driver in drivers:
        if not driver.hardware_id or not driver.device_id:
            continue
        match = _match_hardware_id(candidates, driver.hardware_id)
        if match is not None:
            matches[driver.device_id] = match
    return matches

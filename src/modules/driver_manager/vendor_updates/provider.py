"""The vendor-provider registry. A provider answers one question --
'does this device have a newer driver, and where do I get it' -- and
never guesses: no confident answer is None, with a stated reason (see
NoProviderReason) so the UI can say WHY, not just that there's nothing to
show.
"""
import enum
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol

from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates.vendor_id import vendor_for_hardware_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpdateInfo:
    vendor: str
    current_version: str
    latest_version: str
    download_url: str
    installer_signer: str


class VendorProvider(Protocol):
    vendor_name: str
    allowed_download_domains: List[str]
    expected_signer: str

    def check_for_update(self, driver: DriverInfo) -> Optional[UpdateInfo]:
        ...


class NoProviderReason(enum.Enum):
    UNRECOGNIZED_VENDOR = "unrecognized_vendor"       # vendor_id has no entry
    NO_ADAPTER_FOR_VENDOR = "no_adapter_for_vendor"    # recognized, no provider yet


_PROVIDERS: Dict[str, VendorProvider] = {}


def register_provider(provider: VendorProvider) -> None:
    """Called at import time by each provider module (e.g.
    nvidia_provider.py) to add itself. Overwrites any existing
    registration for the same vendor_name -- last import wins, which only
    matters if two modules ever claim the same vendor, which would be a
    packaging bug worth seeing loudly rather than silently."""
    if provider.vendor_name in _PROVIDERS:
        logger.warning("Overwriting existing provider for vendor %r",
                       provider.vendor_name)
    _PROVIDERS[provider.vendor_name] = provider


def provider_for(driver: DriverInfo) -> Optional[VendorProvider]:
    """The registered provider for driver's vendor, or None -- call
    no_provider_reason(driver) to learn why when this is None."""
    vendor = vendor_for_hardware_id(driver.hardware_id, driver.device_name)
    if vendor is None:
        return None
    return _PROVIDERS.get(vendor)


def no_provider_reason(driver: DriverInfo) -> Optional[NoProviderReason]:
    """None only when provider_for(driver) would actually find one --
    otherwise the specific reason it didn't."""
    vendor = vendor_for_hardware_id(driver.hardware_id, driver.device_name)
    if vendor is None:
        return NoProviderReason.UNRECOGNIZED_VENDOR
    if vendor not in _PROVIDERS:
        return NoProviderReason.NO_ADAPTER_FOR_VENDOR
    return None

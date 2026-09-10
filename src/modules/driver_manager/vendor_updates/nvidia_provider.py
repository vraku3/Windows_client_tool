"""NVIDIA's own driver-download page API, verified live (not scraped
HTML) -- see docs/superpowers/specs/2026-09-10-driver-manager-phase1-design.md
for the full verification record. Two real calls: a GPU-name-to-pfid
lookup table (cached locally, NVIDIA's own page uses the same table), and
a driver-lookup call keyed by that pfid.
"""
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import unquote
from urllib.request import urlopen

from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates.provider import UpdateInfo, register_provider

logger = logging.getLogger(__name__)

_PFID_LOOKUP_URL = "https://www.nvidia.com/Download/API/lookupValueSearch.aspx?TypeID=3"
_DRIVER_LOOKUP_URL = (
    "https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php"
    "?func=DriverManualLookup&psid=101&pfid={pfid}&osID=135&languageCode=1033"
    "&beta=null&isWHQL=1&dch=1&sort1=0&numberOfResults=10"
)
_PFID_CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_REQUEST_TIMEOUT_SECONDS = 15


def _pfid_cache_path() -> str:
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    directory = os.path.join(base, "WindowsTweaker", "driver_updates")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "nvidia_pfid_cache.xml")


def _fetch_pfid_table() -> Optional[bytes]:
    """The pfid lookup table, from cache or a fresh fetch -- or None if
    neither the cache nor the network could produce it. Never raises: a
    network failure or a local disk error is a "we don't know", the same
    as a parse failure, not an uncaught exception out of check_for_update.
    """
    cache_path = _pfid_cache_path()
    try:
        if os.path.exists(cache_path):
            age = time.time() - os.path.getmtime(cache_path)
            if age < _PFID_CACHE_MAX_AGE_SECONDS:
                with open(cache_path, "rb") as f:
                    return f.read()
        with urlopen(_PFID_LOOKUP_URL, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            data = resp.read()
        with open(cache_path, "wb") as f:
            f.write(data)
        return data
    except OSError as exc:
        logger.warning("nvidia_provider: could not fetch/cache pfid table: %s", exc)
        return None


def _pfid_for_gpu_name(gpu_name: str) -> Optional[str]:
    table = _fetch_pfid_table()
    if table is None:
        return None
    try:
        root = ET.fromstring(table)
    except ET.ParseError as exc:
        logger.warning("nvidia_provider: could not parse pfid table: %s", exc)
        return None
    for lookup_value in root.iter("LookupValue"):
        name_el = lookup_value.find("Name")
        value_el = lookup_value.find("Value")
        if name_el is not None and value_el is not None and name_el.text == gpu_name:
            return value_el.text
    return None


class NvidiaProvider:
    vendor_name = "NVIDIA"
    allowed_download_domains = ["download.nvidia.com"]
    expected_signer = "NVIDIA Corporation"

    def check_for_update(self, driver: DriverInfo) -> Optional[UpdateInfo]:
        pfid = _pfid_for_gpu_name(driver.device_name)
        if pfid is None:
            logger.info("nvidia_provider: no pfid match for GPU name %r",
                       driver.device_name)
            return None
        url = _DRIVER_LOOKUP_URL.format(pfid=pfid)
        try:
            with urlopen(url, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
                raw = resp.read()
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("nvidia_provider: driver lookup failed for pfid %s: %s",
                           pfid, exc)
            return None
        ids = data.get("IDS") or []
        if not ids:
            return None
        info = ids[0].get("downloadInfo") or {}
        if info.get("Success") != "1":
            return None
        version = info.get("Version")
        download_url = info.get("DownloadURL")
        if not version or not download_url:
            logger.warning("nvidia_provider: driver lookup response missing "
                          "Version or DownloadURL for pfid %s", pfid)
            return None
        return UpdateInfo(
            vendor="NVIDIA",
            current_version=driver.version,
            latest_version=unquote(version),
            download_url=unquote(download_url),
            installer_signer=self.expected_signer,
        )


register_provider(NvidiaProvider())

"""Realtek's own download site -- scraped, like AMD's, because Realtek
publishes no API either. Verified live 2026-09-14 against this machine's
real hardware (3 Realtek NICs: onboard PCIe 5GbE, two USB dongles):

- Realtek's site (realtek.com, a Joomla-descended ASP.NET app judging by
  its own menu JSON) organizes drivers into per-CONTROLLER-FAMILY category
  pages, not per-exact-model ones like AMD's -- one page covers every
  PCIe Fast/Gigabit/2.5G/5G/10G Ethernet chip Realtek makes
  (cate_id=584), a separate one covers the USB equivalents (cate_id=585).
  Real machine data confirms both are relevant here: the onboard "Realtek
  PCIe 5GbE Family Controller" and two "Realtek ... USB ... Family
  Controller" dongles. hardware_id's bus prefix (PCI\\ vs USB\\) picks
  the right one -- there is no per-exact-chip page to construct a URL
  for the way AMD's per-GPU-model pages work.
- Those category pages are plain server-rendered HTML (no client-side
  API call needed to read them -- confirmed by direct curl, no JS
  execution), with a real "Windows" section listing several driver
  variants at once: an NDIS-model installer (broadly Win10+Win11), a
  newer NetAdapterCx-model one (Win11 only in the rows seen), and
  "- Not Support Power Saving" cut-down variants of each, plus unrelated
  DASH/diagnostic utilities in the same table. _pick_windows_driver_row
  prefers the plain NDIS "Auto Installation Program" entry -- broadest
  compatibility, full feature set -- falling back to NetAdapterCx only
  if no NDIS entry is found.
- The actual FILE download (/Download/ToDownload?type=direct&downloadid=N)
  requires solving a CAPTCHA -- confirmed live, unconditionally, with a
  realistic browser UA, a Referer from the category page, AND a real
  cookie jar from that same request. This is Realtek's own deliberate
  anti-bot gate on the download step specifically (the listing pages
  carry no such gate at all), and this app does not automate around it.
  UpdateInfo.manual_download_only=True reflects that -- download_url
  points at the category page itself, for a person to open and click
  through by hand, never at the gated endpoint.
- Realtek's PCI/USB vendor ids identify the COMPANY, not a product line
  -- 0x0BDA (USB) also turned up on several real "Generic USB Hub"
  entries on this exact machine (Realtek makes hub controller chips
  too), which check_for_update was routing to the NIC download pages
  before this was caught by running the real-machine harness after
  wiring the USB vendor id in. Gated on driver.driver_class == "NET"
  now -- vendor_id.py answering "who made this" is a different question
  from this provider answering "does this apply to me".
"""
import logging
import re
from typing import Optional
from urllib.request import Request, urlopen

from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates.provider import UpdateInfo, register_provider

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# Real category ids, confirmed live: 584 covers every PCIe FE/GbE/2.5GbE/
# 5G/10G Realtek NIC, 585 the USB equivalents. Both are named
# generically by controller FAMILY, not by exact chip -- there is no
# finer-grained page to pick between real machine's own three NICs.
_PCI_CATE_ID = "584"
_USB_CATE_ID = "585"

_ROW_RE = re.compile(
    r'downloadid=(\d+)"[^>]*>.*?</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>',
    re.DOTALL)


def _category_url(cate_id: str) -> str:
    return f"https://www.realtek.com/Download/List?cate_id={cate_id}"


def _fetch(url: str) -> Optional[str]:
    try:
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        logger.warning("realtek_provider: could not fetch %s: %s", url, exc)
        return None


def _pick_windows_driver_row(html: str):
    """The (downloadid, description, version, date) tuple for the real
    driver install package -- never a DASH/diagnostic utility (same
    category page, same table shape) and never a "Not Support Power
    Saving" cut-down variant when a full one exists. Prefers the
    NDIS-model installer (broadly Win10+Win11 compatible) over the
    newer NetAdapterCx one only because the real rows seen so far offer
    NetAdapterCx as Win11-only -- falls back to it if no NDIS row
    qualifies. None if the "Windows" section isn't found or nothing in
    it looks like a real driver installer."""
    marker = html.find(">Windows<")
    if marker == -1:
        return None
    next_section = html.find('<h2 class="title">', marker + 1)
    window = html[marker:next_section] if next_section != -1 else html[marker:marker + 8000]
    fallback = None
    for match in _ROW_RE.finditer(window):
        download_id, desc, version, date = match.groups()
        desc = desc.strip()
        if "Auto Installation Program" not in desc:
            continue
        if "Win10" not in desc and "Win11" not in desc:
            continue
        if "Not Support Power Saving" in desc or "DASH" in desc:
            continue
        if "NetAdapterCx" in desc:
            if fallback is None:
                fallback = (download_id, desc, version.strip(), date.strip())
            continue
        return (download_id, desc, version.strip(), date.strip())
    return fallback


class RealtekProvider:
    vendor_name = "Realtek"
    # Unused in practice -- manual_download_only=True means
    # download_and_verify is never called for this provider at all (see
    # this module's docstring). Kept populated anyway so the Protocol's
    # shape stays uniform across providers.
    allowed_download_domains = ["realtek.com"]
    expected_signer = "Realtek Semiconductor Corp."

    def check_for_update(self, driver: DriverInfo) -> Optional[UpdateInfo]:
        # Realtek makes far more than NICs on the SAME vendor id (real
        # machine data: several "Generic USB Hub" entries carry a
        # Realtek USB vendor id too -- 0x0BDA is assigned to Realtek as
        # a COMPANY, not to any one product line). Recognizing the
        # vendor (vendor_id.py) is a different question from this
        # provider actually applying to the device -- only the network
        # class does. Routing a USB hub or audio codec to a NIC
        # download page would be a real, live-observed false positive,
        # not a hypothetical one.
        if (driver.driver_class or "").upper() != "NET":
            return None
        hardware_id = (driver.hardware_id or "").upper()
        if hardware_id.startswith("USB\\"):
            cate_id = _USB_CATE_ID
        elif hardware_id.startswith("PCI\\"):
            cate_id = _PCI_CATE_ID
        else:
            logger.info("realtek_provider: %r has neither a PCI nor USB "
                       "hardware id -- no category page to check",
                       driver.device_name)
            return None
        url = _category_url(cate_id)
        html = _fetch(url)
        if html is None:
            return None
        row = _pick_windows_driver_row(html)
        if row is None:
            logger.info("realtek_provider: no usable Windows driver row "
                       "found on %s", url)
            return None
        _download_id, _desc, version, _date = row
        return UpdateInfo(
            vendor="Realtek",
            current_version=driver.version,
            latest_version=version,
            download_url=url,
            installer_signer=self.expected_signer,
            manual_download_only=True,
        )


register_provider(RealtekProvider())

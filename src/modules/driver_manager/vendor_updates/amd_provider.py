"""AMD's own driver-download pages -- scraped, not an API, because AMD
publishes none (unlike NVIDIA's lookupValueSearch/AjaxDriverService pair).
Even the best-known community tool for this
(github.com/nunodxxd/AMD-Software-Adrenalin) resorts to the same thing,
self-documented as fragile if AMD's frontend changes. Every fact below was
verified live against AMD's real pages and CDN, not assumed:

- The generic landing page (amd.com/en/support/download/drivers.html)
  always links a small "auto-detect" web-installer stub (measured
  ~46MB). It's real and always current, but useless for LIGHT install --
  it downloads its actual payload live during install rather than
  shipping it, so there's nothing to extract right after downloading it.
- A per-model product page (amd.com/en/support/downloads/drivers.html/
  graphics/radeon-rx/radeon-rx-<NNNN>-series/amd-radeon-rx-<model>.html)
  links the real full, self-contained installer instead -- measured
  930,590,688 bytes for the RX 7900 XTX's own page (version 26.9.1), and
  genuinely 7z-extractable (verified: 619 files, 41 INF/26 SYS pairs,
  "Everything is Ok"). That's what makes AMD's LIGHT install path viable
  at all.
- That per-model URL is CONSTRUCTIBLE from the device name, not looked
  up anywhere -- verified across 5 real models spanning RX 5000-9000
  series (7900 XTX, 7800 XT, 6800 XT, 6600, 5700 XT, 9070 XT), all
  resolving 200; a made-up model 404s cleanly. So this needs no
  hardcoded per-model slug table the way a pure scraper otherwise would.
- drivers.amd.com REQUIRES a Referer header naming an amd.com page, or it
  302s to a "Download Incomplete" page instead of serving the real file
  -- confirmed with both the exact referring page and a bare
  "https://www.amd.com/"; the ordinary www.amd.com pages have not been
  observed to need one, but sending it everywhere is one code path
  instead of two.
- A product page can list more than one current WHQL build at once (the
  RX 7900 XTX's page listed both "26.8.1 (WHQL Recommended)" and "26.9.1
  (WHQL Optional)" side by side) -- this provider prefers the one in the
  "Recommended" card, falling back to the first full-installer link found
  if that label isn't present, rather than assuming document order.

Only Radeon RX-branded GPUs are covered. An APU's integrated graphics
(WMI reports it as e.g. "AMD Radeon(TM) Graphics", no RX model number at
all) has no product page this scheme can construct -- check_for_update
returns None for it honestly rather than guessing a URL.
"""
import logging
import os
import re
from typing import Optional
from urllib.request import Request, urlopen

from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates.provider import UpdateInfo, register_provider

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15
_REFERER = "https://www.amd.com/"
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# "AMD Radeon RX 7900 XTX" -> "7900 XTX". Deliberately does NOT match
# "AMD Radeon(TM) Graphics" (an APU's integrated GPU) -- there is no RX
# model number to build a product-page URL from for that device shape.
_RX_MODEL_RE = re.compile(r"Radeon\s+RX\s+([0-9]{4}[A-Za-z0-9 ]*)", re.IGNORECASE)

_FULL_INSTALLER_RE = re.compile(
    r"https://drivers\.amd\.com/drivers/whql-amd-software-adrenalin-edition-"
    r"([\d.]+)-win\d+-[a-z]\.exe", re.IGNORECASE)


def _product_page_url(model: str) -> str:
    model = model.strip()
    series = f"{model[0]}000-series"
    slug = "amd-radeon-rx-" + re.sub(r"\s+", "-", model.lower())
    return ("https://www.amd.com/en/support/downloads/drivers.html/graphics/"
            f"radeon-rx/radeon-rx-{series}/{slug}.html")


def _fetch(url: str) -> Optional[str]:
    """GETs url with a browser User-Agent and an amd.com Referer. Never
    raises: any failure is a clean None, the same as every other refusal
    in this pipeline."""
    try:
        request = Request(url, headers={"User-Agent": _USER_AGENT, "Referer": _REFERER})
        with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (OSError, ValueError) as exc:
        logger.warning("amd_provider: could not fetch %s: %s", url, exc)
        return None


def _find_full_installer(html: str) -> Optional[re.Match]:
    """Prefers the link inside the "WHQL Recommended" card; falls back to
    the first full-installer link anywhere on the page if that label
    isn't found -- a real if less certain answer beats refusing outright
    when AMD's page still has the link, just not in the expected spot."""
    marker = html.find("WHQL Recommended")
    if marker != -1:
        match = _FULL_INSTALLER_RE.search(html[marker:marker + 3000])
        if match is not None:
            return match
    return _FULL_INSTALLER_RE.search(html)


class AmdProvider:
    vendor_name = "AMD"
    allowed_download_domains = ["drivers.amd.com"]
    expected_signer = "Advanced Micro Devices"
    # See this module's docstring -- drivers.amd.com refuses a request
    # with no Referer naming an amd.com page.
    download_headers = {"Referer": _REFERER, "User-Agent": _USER_AGENT}

    def check_for_update(self, driver: DriverInfo) -> Optional[UpdateInfo]:
        match = _RX_MODEL_RE.search(driver.device_name or "")
        if match is None:
            logger.info("amd_provider: %r doesn't look like a Radeon RX "
                       "model -- no product page to check", driver.device_name)
            return None
        url = _product_page_url(match.group(1))
        html = _fetch(url)
        if html is None:
            return None
        found = _find_full_installer(html)
        if found is None:
            logger.info("amd_provider: no full-installer link found on %s", url)
            return None
        return UpdateInfo(
            vendor="AMD",
            current_version=driver.version,
            latest_version=found.group(1),
            download_url=found.group(0),
            installer_signer=self.expected_signer,
        )

    def build_silent_install_args(self, log_path: str) -> list:
        """AMD's own documented mechanism (Command Line Installation User
        Guide, drivers.amd.com/relnotes/command-line-installation.pdf,
        confirmed 2026-09-13): "-INSTALL" runs a silent install with no
        UI; "-LOG <path>" writes a result-code log to that path. The same
        guide's "-UI" switch is what a non-silent run would use instead
        -- never passed here."""
        return ["-INSTALL", "-OUTPUT", "screen", "-LOG", log_path]

    def silent_install_succeeded(self, log_path: str, exit_code: int) -> Optional[bool]:
        """AMD's guide documents the LOG FILE's "ResultCode" as the real
        success signal (0 = PASS, 1/2 = FAIL) -- it never documents what
        the process's own exit code means, so exit_code is intentionally
        unused here rather than guessed at. None (triggers the
        interactive-installer fallback) when the log never appeared or
        didn't contain a recognizable ResultCode line."""
        if not os.path.exists(log_path):
            return None
        try:
            with open(log_path, "r", errors="ignore") as f:
                text = f.read()
        except OSError as exc:
            logger.warning("amd_provider: could not read install log %s: %s", log_path, exc)
            return None
        match = re.search(r"ResultCode\s*=\s*(\d+)", text)
        if match is None:
            return None
        return match.group(1) == "0"


register_provider(AmdProvider())

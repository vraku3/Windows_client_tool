# Driver Manager Phase 1 — Update Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first real write capability to Driver Manager — look up
whether a device has a newer driver from its vendor (proven on NVIDIA),
download it, verify it, and install just the driver files (LIGHT: INF/SYS/CAT
via `pnputil`, no vendor bloatware) — plus rollback of an update this feature
applied, bulk rollback across a session, and a per-device power-management
toggle.

**Architecture:** A new Qt-free `vendor_updates/` package under
`driver_manager/`: a pluggable vendor-provider registry keyed by PCI vendor
ID, one proven provider (NVIDIA, using its own real download-page API,
verified live during design), a shared safety pipeline (download → verify
signature → restore point → install), and rollback/power-management as
siblings sharing that pipeline. `driver_module.py` gets the Qt wiring
(confirm dialogs, context-menu actions, `Worker`-based orchestration) and an
elevation gate for the new write actions only.

**Tech Stack:** Python 3.12, PyQt6, `urllib.request`/`requests` (check which
this codebase already uses elsewhere before picking — see Task 3),
`core.procengine.signatures` (existing Authenticode verifier),
`core.system_restore` (existing restore-point creator), 7-Zip (already
shelled out to elsewhere in this codebase), `pnputil`.

**Spec:** `docs/superpowers/specs/2026-09-10-driver-manager-phase1-design.md`

## Global Constraints

- Every refused, failed, or not-applicable read is `None`, distinctly, never
  collapsed into a value that looks like a real answer (this codebase's
  project-wide rule).
- Four distinct "nothing to update" answers must stay visibly distinct in UI
  text, never collapsed into one message: (a) vendor byte unrecognized, (b)
  vendor recognized, no provider yet, (c) provider ran, no confident device
  match, (d) provider ran, confirmed no update available.
- A signature-verification failure (wrong signer, invalid, unsigned,
  could-not-verify) always refuses the install and states which of those
  four it was.
- A restore point that fails to create refuses the whole write — no
  install proceeds without one.
- LIGHT extraction finding no usable INF/SYS is a distinct "LIGHT not
  available for this package" outcome, never a partial or faked success.
- `allowed_download_domains` is checked against the download URL's actual
  host (suffix match, e.g. `host.endswith("download.nvidia.com")`), never a
  substring check anywhere in the URL string.
- Nothing under `vendor_updates/` imports PyQt6 — same Qt-free split this
  codebase already keeps in TreeSize's `scan/`/`store/`, Monitor Control, and
  GPResult. Only `driver_module.py` itself touches Qt.
- Every write action (install, rollback, power-management write) runs on a
  `Worker`, never the UI thread, and confirms with the user first — Phase
  0's own final review already found and fixed this exact mistake once in
  this same module; it does not get repeated here.
- Driver Manager's `requires_admin` stays `False` with
  `read_only_unelevated = True` added (Monitor Control's own pattern) — all
  of Phase 0's existing reads keep working unelevated; only this phase's new
  write actions are gated.
- No silent exception swallowing — every `except` logs via
  `logger.warning`/`logger.error`.

---

### Task 1: `vendor_id.py` — PCI vendor ID → company name

**Files:**
- Create: `src/modules/driver_manager/vendor_updates/__init__.py` (empty)
- Create: `src/modules/driver_manager/vendor_updates/vendor_id.py`
- Test: `tests/test_vendor_id.py`

**Interfaces:**
- Produces: `vendor_for_hardware_id(hardware_id: str) -> Optional[str]`,
  consumed by `provider.py` (Task 2).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_vendor_id.py
from modules.driver_manager.vendor_updates.vendor_id import vendor_for_hardware_id


def test_recognizes_nvidia_from_a_pci_hardware_id():
    assert vendor_for_hardware_id("PCI\\VEN_10DE&DEV_2684&SUBSYS_88761458") == "NVIDIA"


def test_recognizes_amd_from_a_pci_hardware_id():
    assert vendor_for_hardware_id("PCI\\VEN_1002&DEV_744C&SUBSYS_0B361002") == "AMD"


def test_recognizes_intel_from_a_pci_hardware_id():
    assert vendor_for_hardware_id("PCI\\VEN_8086&DEV_A780&SUBSYS_00000000") == "Intel"


def test_unrecognized_vendor_id_returns_none():
    assert vendor_for_hardware_id("PCI\\VEN_FFFF&DEV_0000") is None


def test_empty_hardware_id_returns_none():
    assert vendor_for_hardware_id("") is None


def test_malformed_hardware_id_returns_none():
    assert vendor_for_hardware_id("not a hardware id at all") is None


def test_is_case_insensitive_on_the_vendor_hex():
    assert vendor_for_hardware_id("PCI\\ven_10de&dev_2684") == "NVIDIA"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_vendor_id.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/modules/driver_manager/vendor_updates/__init__.py
```
(empty file)

```python
# src/modules/driver_manager/vendor_updates/vendor_id.py
"""PCI vendor ID -> company name, for identifying which vendor made a
device from its hardware_id -- never from DriverInfo.publisher, which WMI
reports inconsistently (often just "Microsoft" for the driver class, not
the silicon vendor).

Static table: PCI-SIG vendor IDs are permanent and don't change, so this
is not a live lookup. Recognizing a vendor here does NOT mean a provider
exists for it (see provider.py) -- the two are deliberately separate, so
the UI can say "AMD detected, no update source configured yet" rather
than "unknown vendor" for a vendor this table knows about but Phase 1/2
hasn't built an adapter for.
"""
import re
from typing import Optional

# PCI-SIG vendor IDs, hex, as they appear in a Win32_PnPSignedDriver
# HardWareID string (e.g. "PCI\VEN_10DE&DEV_2684&SUBSYS_88761458").
_PCI_VENDOR_IDS = {
    "10DE": "NVIDIA",
    "1002": "AMD",
    "8086": "Intel",
}

_VEN_RE = re.compile(r"PCI\\VEN_([0-9A-Fa-f]{4})", re.IGNORECASE)


def vendor_for_hardware_id(hardware_id: str) -> Optional[str]:
    """The company name for hardware_id's PCI vendor prefix, or None if
    hardware_id is empty, isn't a recognizable PCI hardware ID shape, or
    names a vendor not in the table yet."""
    if not hardware_id:
        return None
    match = _VEN_RE.match(hardware_id)
    if not match:
        return None
    return _PCI_VENDOR_IDS.get(match.group(1).upper())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_vendor_id.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/modules/driver_manager/vendor_updates/ tests/test_vendor_id.py
git commit -m "feat(driver manager): PCI vendor ID recognition for update lookups

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `provider.py` — the vendor-provider registry

**Files:**
- Create: `src/modules/driver_manager/vendor_updates/provider.py`
- Test: `tests/test_vendor_provider.py`

**Interfaces:**
- Consumes: `vendor_id.vendor_for_hardware_id` (Task 1).
- Produces: `UpdateInfo` dataclass, `VendorProvider` protocol, `register_provider(provider)`,
  `provider_for(driver) -> Optional[VendorProvider]`, `NoProviderReason` enum
  — all consumed by `nvidia_provider.py` (Task 3), `driver_module.py` (Task 8).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_vendor_provider.py
import pytest

from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates import provider as pv


def _driver(hardware_id="PCI\\VEN_10DE&DEV_2684", version="1.0"):
    return DriverInfo(device_name="Test GPU", driver_class="Display",
                      version=version, date="", publisher="V", signed=True,
                      error_code=0, flags="", hardware_id=hardware_id)


class _FakeProvider:
    vendor_name = "NVIDIA"
    allowed_download_domains = ["download.nvidia.com"]
    expected_signer = "NVIDIA Corporation"

    def check_for_update(self, driver):
        return pv.UpdateInfo(
            vendor="NVIDIA", current_version=driver.version,
            latest_version="999.99", download_url="https://us.download.nvidia.com/x.exe",
            installer_signer="NVIDIA Corporation")


@pytest.fixture(autouse=True)
def _clean_registry():
    pv._PROVIDERS.clear()
    yield
    pv._PROVIDERS.clear()


def test_provider_for_finds_a_registered_provider():
    pv.register_provider(_FakeProvider())
    result = pv.provider_for(_driver())
    assert result is not None
    assert result.vendor_name == "NVIDIA"


def test_provider_for_returns_none_reason_unrecognized_vendor_when_no_provider_registered():
    driver = _driver(hardware_id="PCI\\VEN_FFFF&DEV_0000")
    assert pv.provider_for(driver) is None
    assert pv.no_provider_reason(driver) == pv.NoProviderReason.UNRECOGNIZED_VENDOR


def test_provider_for_returns_none_reason_no_adapter_when_vendor_recognized_but_unregistered():
    driver = _driver(hardware_id="PCI\\VEN_1002&DEV_744C")  # AMD, recognized, no provider
    assert pv.provider_for(driver) is None
    assert pv.no_provider_reason(driver) == pv.NoProviderReason.NO_ADAPTER_FOR_VENDOR


def test_update_info_is_frozen():
    info = pv.UpdateInfo(vendor="NVIDIA", current_version="1.0",
                         latest_version="2.0", download_url="https://x",
                         installer_signer="X")
    with pytest.raises(Exception):
        info.vendor = "changed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_vendor_provider.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/modules/driver_manager/vendor_updates/provider.py
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
    vendor = vendor_for_hardware_id(driver.hardware_id)
    if vendor is None:
        return None
    return _PROVIDERS.get(vendor)


def no_provider_reason(driver: DriverInfo) -> Optional[NoProviderReason]:
    """None only when provider_for(driver) would actually find one --
    otherwise the specific reason it didn't."""
    vendor = vendor_for_hardware_id(driver.hardware_id)
    if vendor is None:
        return NoProviderReason.UNRECOGNIZED_VENDOR
    if vendor not in _PROVIDERS:
        return NoProviderReason.NO_ADAPTER_FOR_VENDOR
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_vendor_provider.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/modules/driver_manager/vendor_updates/provider.py tests/test_vendor_provider.py
git commit -m "feat(driver manager): vendor-provider registry with distinct no-provider reasons

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `nvidia_provider.py` — the first real provider

**Files:**
- Create: `src/modules/driver_manager/vendor_updates/nvidia_provider.py`
- Test: `tests/test_nvidia_provider.py`

**Interfaces:**
- Consumes: `provider.UpdateInfo`, `provider.register_provider` (Task 2).
- Produces: registers itself as the `"NVIDIA"` provider; `NvidiaProvider`
  class, importable for direct testing.

**Before writing code:** check which HTTP library this codebase already
uses elsewhere for a plain GET (`grep -rn "^import requests\|^import urllib.request" src/`)
and use that one — don't introduce a new HTTP dependency if one is already
established. If neither is used anywhere yet, use `urllib.request` (stdlib,
zero new dependency) rather than adding `requests` for one feature.

Real, verified request/response shapes (captured live during the design
session — see the spec's Open Questions section for the full record):

- **GPU name → `pfid`**: `GET https://www.nvidia.com/Download/API/lookupValueSearch.aspx?TypeID=3`
  returns XML: `<LookupValueSearch><LookupValues><LookupValue ParentID="...">
  <Name>GeForce RTX 4090</Name><Value>995</Value></LookupValue>...`. Cache
  this response to `%APPDATA%/WindowsTweaker/driver_updates/nvidia_pfid_cache.xml`;
  refetch if the cache file is missing or older than 7 days.
- **Latest driver for a `pfid`**:
  `GET https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php`
  with query params `func=DriverManualLookup`, `psid=101` (GeForce's fixed
  product-series id — this provider targets GeForce only), `pfid=<resolved>`,
  `osID=135` (Windows 11 — verified live), `languageCode=1033`, `beta=null`,
  `isWHQL=1`, `dch=1`, `sort1=0`, `numberOfResults=10`. Real JSON shape:
  ```json
  {"Success": "10", "IDS": [{"downloadInfo": {
      "Success": "1", "Version": "616.92",
      "DownloadURL": "https://us.download.nvidia.com/Windows/616.92/616.92-...exe",
      "DownloadURLFileSize": "990.85 MB", "IsWHQL": "1", "IsBeta": "0",
      "ReleaseDateTime": "Wed Sep 09, 2026", "DetailsURL": "https://..."
  }}]}
  ```
  **The real success signal is `IDS[0]["downloadInfo"]["Success"] == "1"`
  — the top-level `Success` is NOT a boolean** (observed `"10"` in a real
  response) and must never be used as the check. An empty `IDS` list, or a
  `downloadInfo.Success != "1"`, both mean "no update found" — return
  `None`, don't raise.
- Every string value in the response is URL-encoded (`%20` for spaces) —
  decode with `urllib.parse.unquote()`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_nvidia_provider.py
import json
from unittest.mock import patch, MagicMock

import pytest

from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates import nvidia_provider as nvp

_PFID_XML = b"""<?xml version="1.0"?>
<LookupValueSearch><LookupValues>
<LookupValue ParentID="3"><Name>GeForce RTX 4090</Name><Value>995</Value></LookupValue>
<LookupValue ParentID="3"><Name>GeForce RTX 3080</Name><Value>877</Value></LookupValue>
</LookupValues></LookupValueSearch>"""

_DRIVER_FOUND_JSON = json.dumps({
    "Success": "10",
    "IDS": [{"downloadInfo": {
        "Success": "1", "Version": "616.92",
        "DownloadURL": "https://us.download.nvidia.com/Windows/616.92/616.92-desktop-win10-win11-64bit-international-dch-whql.exe",
        "DownloadURLFileSize": "990.85 MB", "IsWHQL": "1", "IsBeta": "0",
        "ReleaseDateTime": "Wed Sep 09, 2026",
        "DetailsURL": "https://www.nvidia.com/en-us/drivers/details/278453/",
    }}],
}).encode("utf-8")

_DRIVER_NOT_FOUND_JSON = json.dumps({
    "Success": "0", "MessageCode": "DriverDownloadIDNotFound",
    "MessageValue": "The Driver Download not found for Manual Lookup requested",
}).encode("utf-8")


def _driver(name="GeForce RTX 4090", version="612.10"):
    return DriverInfo(device_name=name, driver_class="Display", version=version,
                      date="", publisher="NVIDIA", signed=True, error_code=0,
                      flags="", hardware_id="PCI\\VEN_10DE&DEV_2684")


def test_check_for_update_returns_update_info_when_a_newer_driver_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(nvp, "_pfid_cache_path", lambda: str(tmp_path / "pfid.xml"))
    responses = [_PFID_XML, _DRIVER_FOUND_JSON]

    def fake_urlopen(url, timeout=None):
        m = MagicMock()
        m.read.return_value = responses.pop(0)
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: False
        return m

    monkeypatch.setattr(nvp, "urlopen", fake_urlopen)
    provider = nvp.NvidiaProvider()
    result = provider.check_for_update(_driver())
    assert result is not None
    assert result.vendor == "NVIDIA"
    assert result.latest_version == "616.92"
    assert result.download_url == "https://us.download.nvidia.com/Windows/616.92/616.92-desktop-win10-win11-64bit-international-dch-whql.exe"
    assert result.installer_signer == "NVIDIA Corporation"


def test_check_for_update_returns_none_when_nvidia_reports_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(nvp, "_pfid_cache_path", lambda: str(tmp_path / "pfid.xml"))
    responses = [_PFID_XML, _DRIVER_NOT_FOUND_JSON]

    def fake_urlopen(url, timeout=None):
        m = MagicMock()
        m.read.return_value = responses.pop(0)
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: False
        return m

    monkeypatch.setattr(nvp, "urlopen", fake_urlopen)
    provider = nvp.NvidiaProvider()
    assert provider.check_for_update(_driver()) is None


def test_check_for_update_returns_none_when_gpu_name_has_no_pfid_match(tmp_path, monkeypatch):
    monkeypatch.setattr(nvp, "_pfid_cache_path", lambda: str(tmp_path / "pfid.xml"))

    def fake_urlopen(url, timeout=None):
        m = MagicMock()
        m.read.return_value = _PFID_XML
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: False
        return m

    monkeypatch.setattr(nvp, "urlopen", fake_urlopen)
    provider = nvp.NvidiaProvider()
    result = provider.check_for_update(_driver(name="Some Unlisted GPU Model"))
    assert result is None


def test_check_for_update_returns_none_and_logs_on_malformed_json(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(nvp, "_pfid_cache_path", lambda: str(tmp_path / "pfid.xml"))
    responses = [_PFID_XML, b"not json at all"]

    def fake_urlopen(url, timeout=None):
        m = MagicMock()
        m.read.return_value = responses.pop(0)
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: False
        return m

    monkeypatch.setattr(nvp, "urlopen", fake_urlopen)
    provider = nvp.NvidiaProvider()
    with caplog.at_level("WARNING"):
        result = provider.check_for_update(_driver())
    assert result is None
    assert any("nvidia" in r.message.lower() for r in caplog.records)


def test_pfid_cache_is_reused_when_fresh(tmp_path, monkeypatch):
    cache_path = tmp_path / "pfid.xml"
    cache_path.write_bytes(_PFID_XML)
    monkeypatch.setattr(nvp, "_pfid_cache_path", lambda: str(cache_path))
    calls = []

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        m = MagicMock()
        m.read.return_value = _DRIVER_NOT_FOUND_JSON
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: False
        return m

    monkeypatch.setattr(nvp, "urlopen", fake_urlopen)
    provider = nvp.NvidiaProvider()
    provider.check_for_update(_driver())
    # only the driver-lookup call should have happened -- pfid cache hit,
    # no lookupValueSearch.aspx call
    assert len(calls) == 1
    assert "AjaxDriverService" in calls[0]


def test_provider_registers_itself_on_import():
    from modules.driver_manager.vendor_updates import provider as pv
    assert "NVIDIA" in pv._PROVIDERS
    assert isinstance(pv._PROVIDERS["NVIDIA"], nvp.NvidiaProvider)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_nvidia_provider.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/modules/driver_manager/vendor_updates/nvidia_provider.py
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


def _fetch_pfid_table() -> bytes:
    cache_path = _pfid_cache_path()
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


def _pfid_for_gpu_name(gpu_name: str) -> Optional[str]:
    try:
        root = ET.fromstring(_fetch_pfid_table())
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_nvidia_provider.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Run the vendor_updates suite so far, then commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_vendor_id.py tests/test_vendor_provider.py tests/test_nvidia_provider.py -v`

```bash
git add src/modules/driver_manager/vendor_updates/nvidia_provider.py tests/test_nvidia_provider.py
git commit -m "feat(driver manager): NVIDIA vendor provider, using NVIDIA's own real driver-lookup API

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `pipeline.py` — download and signature verification

**Files:**
- Create: `src/modules/driver_manager/vendor_updates/pipeline.py`
- Test: `tests/test_vendor_update_pipeline.py`

**Interfaces:**
- Consumes: `provider.UpdateInfo` (Task 2), `core.procengine.signatures.verify_signature`
  (existing — returns `SignatureFacts(path, status, signer, reason)`, `status`
  one of `VALID`/`NOT_SIGNED`/`INVALID`/`COULD_NOT_VERIFY`, `.signed` property
  true only when `status == VALID`).
- Produces: `download_and_verify(update, cache_dir=None) -> DownloadResult`,
  consumed by `driver_module.py` (Task 8).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_vendor_update_pipeline.py
from unittest.mock import patch

import pytest

from core.procengine.signatures import SignatureFacts
from modules.driver_manager.vendor_updates import pipeline as pl
from modules.driver_manager.vendor_updates.provider import UpdateInfo


def _update(url="https://us.download.nvidia.com/x.exe", signer="NVIDIA Corporation"):
    return UpdateInfo(vendor="NVIDIA", current_version="1.0", latest_version="2.0",
                      download_url=url, installer_signer=signer)


def test_download_and_verify_refuses_a_url_outside_the_allowed_domain(tmp_path):
    update = _update(url="https://evil.example.com/x.exe")
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is None
    assert "domain" in result.reason.lower()


def test_download_and_verify_succeeds_for_a_validly_signed_file(tmp_path, monkeypatch):
    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"fake installer bytes")
        return True

    monkeypatch.setattr(pl, "_download_file", fake_download)
    monkeypatch.setattr(pl, "verify_signature",
                        lambda path: SignatureFacts(path=path, status="VALID",
                                                    signer="NVIDIA Corporation"))
    update = _update()
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is not None
    assert result.reason == ""


def test_download_and_verify_refuses_a_wrong_signer(tmp_path, monkeypatch):
    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"fake installer bytes")
        return True

    monkeypatch.setattr(pl, "_download_file", fake_download)
    monkeypatch.setattr(pl, "verify_signature",
                        lambda path: SignatureFacts(path=path, status="VALID",
                                                    signer="Some Other Company"))
    update = _update()
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is None
    assert "signer" in result.reason.lower()


def test_download_and_verify_refuses_an_unsigned_file(tmp_path, monkeypatch):
    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"fake installer bytes")
        return True

    monkeypatch.setattr(pl, "_download_file", fake_download)
    monkeypatch.setattr(pl, "verify_signature",
                        lambda path: SignatureFacts(path=path, status="NOT_SIGNED"))
    update = _update()
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is None
    assert "not_signed" in result.reason.lower() or "unsigned" in result.reason.lower()


def test_download_and_verify_reports_a_download_failure_distinctly(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "_download_file", lambda url, dest: False)
    update = _update()
    result = pl.download_and_verify(
        update, allowed_domains=["download.nvidia.com"], cache_dir=str(tmp_path))
    assert result.path is None
    assert "download" in result.reason.lower()


def test_download_and_verify_refuses_cleanly_when_the_cache_directory_cannot_be_created(monkeypatch):
    # No cache_dir passed -- forces the default-cache-dir path, which is
    # made to fail here rather than actually touching a real directory.
    monkeypatch.setattr(pl, "_default_cache_dir", lambda: None)
    update = _update()
    result = pl.download_and_verify(update, allowed_domains=["download.nvidia.com"])
    assert result.path is None
    assert "cache directory" in result.reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_vendor_update_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/modules/driver_manager/vendor_updates/pipeline.py
"""The one safety pipeline every vendor provider and every install mode
(LIGHT now, FULL in Phase 2) goes through -- so adding either never means
re-deriving download/verification safety logic. Qt-free: driver_module.py
calls into this from a Worker, never the UI thread.
"""
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse
from urllib.request import urlopen

from core.procengine.signatures import verify_signature

logger = logging.getLogger(__name__)


def _default_cache_dir() -> Optional[str]:
    """None (never raises) if the directory can't be created -- a
    permission error here must reach download_and_verify as a normal
    refusal, the same as every other failure mode in this pipeline, not
    an uncaught exception."""
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    directory = os.path.join(base, "WindowsTweaker", "driver_updates", "downloads")
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        logger.warning("pipeline: could not create cache directory %s: %s",
                       directory, exc)
        return None
    return directory


@dataclass(frozen=True)
class DownloadResult:
    path: Optional[str]
    reason: str = ""  # always populated on failure, empty on success


def _download_file(url: str, dest_path: str) -> bool:
    """Real network download -- mocked in every test above it. Returns
    False on any failure rather than raising, so the caller's reason
    text stays uniform with every other refusal in this pipeline."""
    try:
        with urlopen(url, timeout=120) as resp, open(dest_path, "wb") as out:
            out.write(resp.read())
        return True
    except OSError as exc:
        logger.warning("pipeline: download failed for %s: %s", url, exc)
        return False


def download_and_verify(update, allowed_domains: List[str],
                        cache_dir: Optional[str] = None) -> DownloadResult:
    """update: a provider.UpdateInfo. Refuses (path=None, a stated reason)
    for: a download URL outside allowed_domains (checked by host suffix,
    never substring), a download that fails outright, or a downloaded
    file whose Authenticode signature is not VALID and signed by
    update.installer_signer exactly. Never runs anything it downloads --
    that's the caller's job, only after this returns a real path."""
    host = urlparse(update.download_url).hostname or ""
    if not any(host.endswith(domain) for domain in allowed_domains):
        return DownloadResult(
            path=None,
            reason=f"download URL's domain ({host!r}) is not in the "
                   f"allowed list for {update.vendor}")

    directory = cache_dir or _default_cache_dir()
    if directory is None:
        return DownloadResult(path=None, reason="could not create a cache "
                             "directory for the download")
    dest_path = os.path.join(directory, f"{uuid.uuid4().hex}.exe")
    if not _download_file(update.download_url, dest_path):
        return DownloadResult(path=None, reason="the download itself failed")

    facts = verify_signature(dest_path)
    if not facts.signed:
        return DownloadResult(
            path=None,
            reason=f"downloaded file's signature is {facts.status} "
                   f"(expected a VALID signature from {update.installer_signer!r})")
    if facts.signer != update.installer_signer:
        return DownloadResult(
            path=None,
            reason=f"downloaded file is signed by {facts.signer!r}, "
                   f"expected {update.installer_signer!r} -- refusing to "
                   f"run something not from the expected vendor")
    return DownloadResult(path=dest_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_vendor_update_pipeline.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/modules/driver_manager/vendor_updates/pipeline.py tests/test_vendor_update_pipeline.py
git commit -m "feat(driver manager): download+signature-verification pipeline for vendor updates

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `pipeline.py` — LIGHT install

**Files:**
- Modify: `src/modules/driver_manager/vendor_updates/pipeline.py`
- Test: `tests/test_vendor_update_pipeline.py`

**Interfaces:**
- Consumes: `core.system_restore.create_restore_point(description, timeout=60) -> Tuple[bool, str]`
  (existing).
- Produces: `InstallResult` dataclass, `install_light(installer_path, driver) -> InstallResult`,
  consumed by `rollback.py` (Task 6) and `driver_module.py` (Task 8).

Extraction approach: shell out to 7-Zip the same way this codebase already
does for CBS log `.cab` extraction (grep `CbsPersist` handling in the
Diagnose module for the exact subprocess pattern to match — same tool,
same `CREATE_NO_WINDOW` discipline as every other subprocess call in
`driver_manager/`). Extract to a temp directory, then walk it for the
first `.inf` file whose sibling directory also contains a matching `.sys`
(a package with an INF but no driver binary at all isn't a usable LIGHT
install — treat that the same as "no INF found").

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_vendor_update_pipeline.py -- add these
from modules.driver_manager.vendor_updates.pipeline import InstallResult


def _driver_for_install():
    from modules.driver_manager.driver_reader import DriverInfo
    return DriverInfo(device_name="Test GPU", driver_class="Display",
                      version="1.0", date="", publisher="V", signed=True,
                      error_code=0, flags="", hardware_id="PCI\\VEN_10DE&DEV_2684")


def test_install_light_refuses_when_no_restore_point_can_be_created(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "create_restore_point", lambda desc, timeout=60: (False, "policy disabled"))
    result = pl.install_light(str(tmp_path / "installer.exe"), _driver_for_install())
    assert result.ok is False
    assert result.restore_point_taken is False
    assert "restore point" in result.reason.lower()


def test_install_light_refuses_when_extraction_finds_no_inf(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(pl, "_extract_with_7zip", lambda installer, dest: True)
    # extraction "succeeds" but leaves no usable inf/sys pair
    result = pl.install_light(str(tmp_path / "installer.exe"), _driver_for_install())
    assert result.ok is False
    assert result.restore_point_taken is True
    assert "not available" in result.reason.lower() or "no usable" in result.reason.lower()


def test_install_light_runs_pnputil_when_an_inf_is_found(tmp_path, monkeypatch):
    extract_dir_holder = {}

    def fake_extract(installer, dest):
        extract_dir_holder["dir"] = dest
        os.makedirs(os.path.join(dest, "display.driver"), exist_ok=True)
        with open(os.path.join(dest, "display.driver", "nv_disp.inf"), "w") as f:
            f.write("; fake inf")
        with open(os.path.join(dest, "display.driver", "nv_disp.sys"), "wb") as f:
            f.write(b"fake sys")
        return True

    monkeypatch.setattr(pl, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(pl, "_extract_with_7zip", fake_extract)
    ran = {}

    def fake_run_pnputil(inf_path):
        ran["inf_path"] = inf_path
        return True, ""

    monkeypatch.setattr(pl, "_run_pnputil_install", fake_run_pnputil)
    result = pl.install_light(str(tmp_path / "installer.exe"), _driver_for_install())
    assert result.ok is True
    assert result.restore_point_taken is True
    assert ran["inf_path"].endswith("nv_disp.inf")


def test_install_light_reports_pnputil_failure_distinctly(tmp_path, monkeypatch):
    def fake_extract(installer, dest):
        os.makedirs(os.path.join(dest, "d"), exist_ok=True)
        open(os.path.join(dest, "d", "x.inf"), "w").close()
        open(os.path.join(dest, "d", "x.sys"), "wb").close()
        return True

    monkeypatch.setattr(pl, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(pl, "_extract_with_7zip", fake_extract)
    monkeypatch.setattr(pl, "_run_pnputil_install", lambda inf: (False, "pnputil exited 3"))
    result = pl.install_light(str(tmp_path / "installer.exe"), _driver_for_install())
    assert result.ok is False
    assert "pnputil" in result.reason.lower()
```

Add `import os` at the top of the test file if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_vendor_update_pipeline.py -v`
Expected: FAIL — `AttributeError`/`ImportError` on `install_light`.

- [ ] **Step 3: Implement**

Add to `pipeline.py`:

```python
import subprocess
import tempfile

from core.system_restore import create_restore_point

CREATE_NO_WINDOW = 0x08000000


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    reason: str
    restore_point_taken: bool
    previous_package_hint: Optional[str] = None  # for rollback.py, Task 6


def _extract_with_7zip(installer_path: str, dest_dir: str) -> bool:
    """Shells out to 7z the same way this codebase already does for CBS
    log cab extraction -- same CREATE_NO_WINDOW discipline as every other
    subprocess call in this module. Not every installer format extracts
    cleanly; a non-zero exit here is a real, expected outcome (NSIS/
    InstallShield/custom wrappers vary), not a bug -- the caller treats a
    False return as 'LIGHT not available for this package', never a crash."""
    try:
        proc = subprocess.run(
            ["7z", "x", installer_path, f"-o{dest_dir}", "-y"],
            capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("pipeline: 7z extraction failed for %s: %s", installer_path, exc)
        return False


def _find_inf_with_sys(dest_dir: str) -> Optional[str]:
    """The first .inf file in dest_dir's tree whose own directory also has
    a .sys -- an inf with no driver binary alongside it isn't installable,
    and picking the wrong inf out of a package with several (a control-
    panel's own inf vs. the actual device driver's) is a real risk this
    check reduces, though not eliminates -- Phase 2 may need to be pickier
    once a real, messy vendor package is tested against this."""
    for root, _dirs, files in os.walk(dest_dir):
        infs = [f for f in files if f.lower().endswith(".inf")]
        syss = {f.lower()[:-4] for f in files if f.lower().endswith(".sys")}
        for inf in infs:
            if inf.lower()[:-4] in syss or syss:
                return os.path.join(root, inf)
    return None


def _run_pnputil_install(inf_path: str) -> tuple:
    try:
        proc = subprocess.run(
            ["pnputil", "/add-driver", inf_path, "/install"],
            capture_output=True, text=True, timeout=120,
            creationflags=CREATE_NO_WINDOW)
        if proc.returncode == 0:
            return True, ""
        return False, f"pnputil exited {proc.returncode}: {proc.stdout or proc.stderr}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"pnputil could not be run: {exc}"


def install_light(installer_path: str, driver) -> InstallResult:
    """The LIGHT install path: extract just INF/SYS from installer_path
    and pnputil-install it -- never runs the vendor's own installer.
    Always takes a restore point first; refuses outright if one can't be
    created. driver: a driver_reader.DriverInfo, used only for the
    restore-point description text."""
    ok, reason = create_restore_point(
        f"Before LIGHT driver update: {driver.device_name}")
    if not ok:
        return InstallResult(ok=False, reason=f"could not take a restore "
                             f"point, refusing to proceed: {reason}",
                             restore_point_taken=False)

    with tempfile.TemporaryDirectory(prefix="wct_driver_update_") as dest_dir:
        if not _extract_with_7zip(installer_path, dest_dir):
            return InstallResult(ok=False,
                                 reason="LIGHT install is not available for "
                                        "this package -- 7-Zip could not "
                                        "extract it",
                                 restore_point_taken=True)
        inf_path = _find_inf_with_sys(dest_dir)
        if inf_path is None:
            return InstallResult(ok=False,
                                 reason="LIGHT install is not available for "
                                        "this package -- no usable INF/SYS "
                                        "pair was found inside it",
                                 restore_point_taken=True)
        ok, reason = _run_pnputil_install(inf_path)
        if not ok:
            return InstallResult(ok=False, reason=reason, restore_point_taken=True)
        return InstallResult(ok=True, reason="", restore_point_taken=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_vendor_update_pipeline.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add src/modules/driver_manager/vendor_updates/pipeline.py tests/test_vendor_update_pipeline.py
git commit -m "feat(driver manager): LIGHT install -- extract INF/SYS, pnputil, restore point first

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: `rollback.py`

**Files:**
- Create: `src/modules/driver_manager/vendor_updates/rollback.py`
- Test: `tests/test_vendor_update_rollback.py`

**Interfaces:**
- Consumes: `published_name_for(inf_name) -> Optional[str]`,
  `cleanup.cleanup_scanner.driver_store.store_folder_for(published) -> Optional[str]`
  (both existing), `core.system_restore.create_restore_point` (existing).
- Produces: `snapshot_before_install(driver) -> Optional[str]`,
  `rollback(token) -> InstallResult`, `bulk_rollback(tokens) -> List[InstallResult]`,
  consumed by `driver_module.py` (Task 8).

Per the spec's resolved Open Question: this trusts the driver store to
still hold the pre-update package (Windows retains superseded OEM
packages — measured on this exact machine's Cleanup module data) rather
than copying it separately. The snapshot token is simply the OEM published
name (`oemNN.inf`) of the package in place BEFORE the install — recording
that is enough to `pnputil /add-driver <store path>\<inf> /install` it
again later, since it's still in the store as a superseded (not deleted)
package.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_vendor_update_rollback.py
from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates import rollback as rb


def _driver(inf_name="oem12.inf"):
    return DriverInfo(device_name="Test GPU", driver_class="Display",
                      version="1.0", date="", publisher="V", signed=True,
                      error_code=0, flags="", inf_name=inf_name,
                      hardware_id="PCI\\VEN_10DE&DEV_2684")


def test_snapshot_before_install_records_the_oem_published_name():
    token = rb.snapshot_before_install(_driver(inf_name="oem12.inf"))
    assert token == "oem12.inf"


def test_snapshot_before_install_returns_none_for_a_driverless_device():
    token = rb.snapshot_before_install(_driver(inf_name=""))
    assert token is None


def test_snapshot_before_install_returns_none_for_an_inbox_driver():
    # published_name_for() itself returns None for non-oem infs like usb.inf
    token = rb.snapshot_before_install(_driver(inf_name="usb.inf"))
    assert token is None


def test_rollback_reinstalls_the_snapshotted_package(monkeypatch):
    monkeypatch.setattr(rb, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(rb, "store_folder_for", lambda published: r"C:\Windows\System32\DriverStore\FileRepository\display.inf_amd64_abc")
    ran = {}

    def fake_pnputil(inf_path):
        ran["inf_path"] = inf_path
        return True, ""

    monkeypatch.setattr(rb, "_run_pnputil_install", fake_pnputil)
    result = rb.rollback("oem12.inf")
    assert result.ok is True
    assert "oem12.inf" in ran["inf_path"]


def test_rollback_refuses_when_no_restore_point_can_be_created(monkeypatch):
    monkeypatch.setattr(rb, "create_restore_point", lambda desc, timeout=60: (False, "disabled"))
    result = rb.rollback("oem12.inf")
    assert result.ok is False
    assert "restore point" in result.reason.lower()


def test_rollback_reports_when_the_store_no_longer_has_the_package(monkeypatch):
    monkeypatch.setattr(rb, "create_restore_point", lambda desc, timeout=60: (True, ""))
    monkeypatch.setattr(rb, "store_folder_for", lambda published: None)
    result = rb.rollback("oem12.inf")
    assert result.ok is False
    assert "no longer" in result.reason.lower() or "not found" in result.reason.lower()


def test_bulk_rollback_reports_every_result_even_with_a_partial_failure(monkeypatch):
    monkeypatch.setattr(rb, "create_restore_point", lambda desc, timeout=60: (True, ""))

    def fake_store_folder(published):
        return r"C:\...\ok" if published == "oem1.inf" else None

    monkeypatch.setattr(rb, "store_folder_for", fake_store_folder)
    monkeypatch.setattr(rb, "_run_pnputil_install", lambda inf: (True, ""))
    results = rb.bulk_rollback(["oem1.inf", "oem2.inf"])
    assert len(results) == 2
    assert results[0].ok is True
    assert results[1].ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_vendor_update_rollback.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/modules/driver_manager/vendor_updates/rollback.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_vendor_update_rollback.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/modules/driver_manager/vendor_updates/rollback.py tests/test_vendor_update_rollback.py
git commit -m "feat(driver manager): rollback and bulk rollback for updates this feature applied

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: `power_management.py`

**Files:**
- Create: `src/modules/driver_manager/vendor_updates/power_management.py`
- Test: `tests/test_power_management.py`

**Interfaces:**
- Produces: `get_power_management(device_id) -> Optional[bool]`,
  `set_power_management(device_id, allow_off) -> Tuple[bool, str]`, consumed
  by `driver_module.py` (Task 8).

**This task starts with a real-machine experiment, not a guess.** The spec's
Open Questions record what's already known: community sources point at a
`PnPCapabilities` DWORD under a device's Class registry key, and this exact
machine has a real device with that value entirely absent (consistent with
"default/allowed", but not proof of the bit-level meaning for an explicitly
disabled state). Do this before writing any implementation code:

- [ ] **Step 1: Run the real-machine experiment**

1. Pick a real device on the test machine that has a Power Management tab
   with the "Allow the computer to turn off this device to save power"
   checkbox available (most USB devices and many network adapters have
   one; a GPU typically does not — check a few via Device Manager's
   Properties → Power Management tab until one with the checkbox found).
2. Note its device instance ID (Device Manager → Details tab → "Device
   instance path").
3. Read its current registry state:
   ```powershell
   $instanceId = "<the device instance id from step 2>"
   $driverKey = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Enum\$instanceId").Driver
   Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Class\$driverKey" | Format-List *
   ```
   Save this output.
4. In Device Manager, UNCHECK the "Allow the computer to turn off..."
   checkbox and click OK.
5. Re-run the same PowerShell from step 3 and diff the two outputs — note
   exactly which value(s) appeared, changed, or disappeared.
6. Re-CHECK the checkbox (restore the original state) and confirm the
   registry reverts.
7. Compare against the implementation below: it already codes
   `PnPCapabilities` (0 = allowed, 24 = disabled) as its working answer,
   based on real community documentation of this exact mechanism — but
   that is secondhand, not verified live on this machine the way every
   other resolved item in this plan was. **If your experiment's diff shows
   a different value name or different numbers, update `_VALUE_NAME`,
   `_ALLOWED_VALUE`, and `_DISABLED_VALUE` in Step 4 below to match what
   you actually observed before treating this task as done** — the code
   below is a documented-but-unverified starting point, not a final
   answer to build on blindly.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_power_management.py
from modules.driver_manager.vendor_updates import power_management as pm


def test_get_power_management_returns_none_when_the_device_has_no_setting(monkeypatch):
    monkeypatch.setattr(pm, "_read_registry_value", lambda device_id: None)
    assert pm.get_power_management("PCI\\VEN_1234&DEV_5678\\0") is None


def test_get_power_management_returns_true_when_allowed(monkeypatch):
    monkeypatch.setattr(pm, "_read_registry_value", lambda device_id: pm._ALLOWED_VALUE)
    assert pm.get_power_management("PCI\\VEN_1234&DEV_5678\\0") is True


def test_get_power_management_returns_false_when_disabled(monkeypatch):
    monkeypatch.setattr(pm, "_read_registry_value", lambda device_id: pm._DISABLED_VALUE)
    assert pm.get_power_management("PCI\\VEN_1234&DEV_5678\\0") is False


def test_set_power_management_writes_the_disabled_value(monkeypatch):
    written = {}
    monkeypatch.setattr(pm, "_write_registry_value",
                        lambda device_id, value: written.update(device_id=device_id, value=value) or (True, ""))
    ok, reason = pm.set_power_management("PCI\\VEN_1234&DEV_5678\\0", allow_off=False)
    assert ok is True
    assert written["value"] == pm._DISABLED_VALUE


def test_set_power_management_reports_a_write_failure(monkeypatch):
    monkeypatch.setattr(pm, "_write_registry_value", lambda device_id, value: (False, "access denied"))
    ok, reason = pm.set_power_management("PCI\\VEN_1234&DEV_5678\\0", allow_off=True)
    assert ok is False
    assert "access denied" in reason.lower()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_power_management.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Implement, using Step 1's real observed values**

```python
# src/modules/driver_manager/vendor_updates/power_management.py
"""Per-device 'allow the computer to turn off this device to save power',
the same setting Device Manager's own Power Management tab shows.

The value name and numbers below (PnPCapabilities, 0=allowed, 24=disabled)
are this codebase's best answer from real community documentation of this
exact mechanism, checked against this machine's own registry showing the
value absent on a device at its default (consistent with, though not
independent proof of, this reading). Task 7's Step 1 is a real-machine
registry-diff experiment that either confirms these values or corrects
them -- run it and update the three constants below to match what it
actually shows before this file is done.
"""
import logging
import winreg
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_VALUE_NAME = "PnPCapabilities"
_ALLOWED_VALUE = 0
_DISABLED_VALUE = 24


def _driver_key_for(device_id: str) -> Optional[str]:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            f"SYSTEM\\CurrentControlSet\\Enum\\{device_id}") as key:
            return winreg.QueryValueEx(key, "Driver")[0]
    except OSError as exc:
        logger.warning("power_management: could not read Driver value for %s: %s",
                       device_id, exc)
        return None


def _read_registry_value(device_id: str) -> Optional[int]:
    driver_key = _driver_key_for(device_id)
    if driver_key is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            f"SYSTEM\\CurrentControlSet\\Control\\Class\\{driver_key}") as key:
            return winreg.QueryValueEx(key, _VALUE_NAME)[0]
    except FileNotFoundError:
        return None  # value absent -- device doesn't expose this setting, or is at default
    except OSError as exc:
        logger.warning("power_management: could not read %s for %s: %s",
                       _VALUE_NAME, device_id, exc)
        return None


def _write_registry_value(device_id: str, value: int) -> Tuple[bool, str]:
    driver_key = _driver_key_for(device_id)
    if driver_key is None:
        return False, "could not resolve this device's driver registry key"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            f"SYSTEM\\CurrentControlSet\\Control\\Class\\{driver_key}",
                            0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_DWORD, value)
        return True, ""
    except OSError as exc:
        logger.warning("power_management: could not write %s for %s: %s",
                       _VALUE_NAME, device_id, exc)
        return False, str(exc)


def get_power_management(device_id: str) -> Optional[bool]:
    """True if the device is allowed to be powered off, False if disabled,
    None if this device doesn't expose the setting at all (most devices --
    that's a real 'not applicable', not a refusal)."""
    value = _read_registry_value(device_id)
    if value is None:
        return None
    return value == _ALLOWED_VALUE


def set_power_management(device_id: str, allow_off: bool) -> Tuple[bool, str]:
    """Writes the setting; (False, reason) on any failure. Never silently
    no-ops -- a caller that gets (True, "") knows the write actually
    happened."""
    value = _ALLOWED_VALUE if allow_off else _DISABLED_VALUE
    return _write_registry_value(device_id, value)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_power_management.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add src/modules/driver_manager/vendor_updates/power_management.py tests/test_power_management.py
git commit -m "feat(driver manager): per-device power-management toggle, registry mechanism confirmed live

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: `driver_module.py` — UI wiring, elevation, orchestration

**Files:**
- Modify: `src/modules/driver_manager/driver_module.py`
- Test: `tests/test_driver_module.py`

**Interfaces:**
- Consumes: everything from Tasks 1-7 (`provider.provider_for`,
  `provider.no_provider_reason`, `pipeline.download_and_verify`,
  `pipeline.install_light`, `rollback.snapshot_before_install`,
  `rollback.rollback`, `rollback.bulk_rollback`,
  `power_management.get_power_management`,
  `power_management.set_power_management`).

Read `driver_module.py`'s current `class DriverModule(BaseModule)` header
and `_build_toolbar`/`_on_context_menu`/`_resolve_driver_for_row` bodies in
full before editing — this task adds to an already-large, already-tested
file; match its exact existing idioms (Worker tracking in `self._workers`,
`widget_is_valid` guards, `_resolve_driver_for_row` for device identity).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_driver_module.py -- add these
def test_driver_module_requires_admin_is_still_false_but_read_only_unelevated_is_true():
    mod = _module()
    assert mod.requires_admin is False
    assert mod.read_only_unelevated is True


def test_check_for_vendor_update_shows_no_provider_reason_when_vendor_unrecognized(monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="Weird Card", driver_class="Display", version="1.0",
                  date="", publisher="V", signed=True, error_code=0, flags="",
                  hardware_id="PCI\\VEN_FFFF&DEV_0000"),
    ]
    mod._populate(mod._drivers_ref[0], "")
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._check_for_vendor_update(mod._drivers_ref[0][0])
    assert shown
    assert "vendor" in shown[0].lower()


def test_check_for_vendor_update_shows_no_adapter_reason_for_a_recognized_unsupported_vendor(monkeypatch):
    mod = _module()
    mod._drivers_ref[0] = [
        DriverInfo(device_name="AMD Card", driver_class="Display", version="1.0",
                  date="", publisher="V", signed=True, error_code=0, flags="",
                  hardware_id="PCI\\VEN_1002&DEV_744C"),
    ]
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._check_for_vendor_update(mod._drivers_ref[0][0])
    assert shown
    assert "no update source" in shown[0].lower() or "not configured" in shown[0].lower()


def _fake_nvidia_update_provider():
    class _FakeProvider:
        vendor_name = "NVIDIA"
        allowed_download_domains = ["download.nvidia.com"]
        expected_signer = "NVIDIA Corporation"

        def check_for_update(self, d):
            from modules.driver_manager.vendor_updates.provider import UpdateInfo
            return UpdateInfo(vendor="NVIDIA", current_version="1.0",
                              latest_version="2.0",
                              download_url="https://us.download.nvidia.com/x.exe",
                              installer_signer="NVIDIA Corporation")
    return _FakeProvider()


def test_check_for_vendor_update_refuses_unelevated_before_any_confirm(monkeypatch):
    mod = _module()
    driver = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display",
                        version="1.0", date="", publisher="V", signed=True,
                        error_code=0, flags="", hardware_id="PCI\\VEN_10DE&DEV_2684")
    monkeypatch.setattr(dmod, "provider_for", lambda d: _fake_nvidia_update_provider())
    monkeypatch.setattr(dmod, "is_admin", lambda: False)
    asked_to_confirm = []
    monkeypatch.setattr(dmod.QMessageBox, "question",
                        lambda *a, **k: asked_to_confirm.append(a))
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._check_for_vendor_update(driver)
    assert not asked_to_confirm  # never reached the confirm dialog
    assert shown
    assert "administrator" in shown[0].lower()


def test_check_for_vendor_update_confirms_before_downloading_when_elevated(monkeypatch):
    mod = _module()
    driver = DriverInfo(device_name="GeForce RTX 4090", driver_class="Display",
                        version="1.0", date="", publisher="V", signed=True,
                        error_code=0, flags="", hardware_id="PCI\\VEN_10DE&DEV_2684")
    monkeypatch.setattr(dmod, "provider_for", lambda d: _fake_nvidia_update_provider())
    monkeypatch.setattr(dmod, "is_admin", lambda: True)
    confirmed = []
    monkeypatch.setattr(dmod.QMessageBox, "question",
                        lambda *a, **k: confirmed.append(a) or dmod.QMessageBox.StandardButton.No)
    mod._check_for_vendor_update(driver)
    assert confirmed
    # the confirm text names the vendor and both versions
    confirm_text = confirmed[0][2]
    assert "NVIDIA" in confirm_text and "1.0" in confirm_text and "2.0" in confirm_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k "vendor_update or read_only_unelevated" -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

Add imports near the top of `driver_module.py`:
```python
from core.admin_utils import is_admin
from modules.driver_manager.vendor_updates.provider import provider_for, no_provider_reason, NoProviderReason
from modules.driver_manager.vendor_updates import pipeline as vendor_pipeline
from modules.driver_manager.vendor_updates.nvidia_provider import NvidiaProvider  # noqa: F401 -- import registers the provider
```

On the `DriverModule` class, alongside the existing `requires_admin`:
```python
    requires_admin = False
    read_only_unelevated = True  # Phase 1: reads (unchanged from Phase 0)
                                   # need no elevation; only the new write
                                   # actions below check is_admin() themselves.
```

Add to `_on_context_menu`, after the existing `act_rollback`/Device-Manager
action (read the current body to place this correctly relative to
`_resolve_driver_for_row`'s result, called `driver`):
```python
        act_check_update = menu.addAction("Check for Vendor Update...")
        act_check_update.triggered.connect(
            lambda: self._check_for_vendor_update(driver) if driver else None)
```

New methods on `DriverModule`:
```python
    def _check_for_vendor_update(self, driver: DriverInfo) -> None:
        reason = no_provider_reason(driver)
        if reason == NoProviderReason.UNRECOGNIZED_VENDOR:
            QMessageBox.information(
                self._widget, "Check for Vendor Update",
                f"Could not identify {driver.device_name}'s vendor from "
                f"its hardware ID -- no update check is possible.")
            return
        if reason == NoProviderReason.NO_ADAPTER_FOR_VENDOR:
            QMessageBox.information(
                self._widget, "Check for Vendor Update",
                f"{driver.device_name}'s vendor is recognized, but no "
                f"update source is configured for it yet.")
            return
        provider = provider_for(driver)
        update = provider.check_for_update(driver)
        if update is None:
            QMessageBox.information(
                self._widget, "Check for Vendor Update",
                f"No update available for {driver.device_name} (currently "
                f"{driver.version}).")
            return
        if not is_admin():
            QMessageBox.information(
                self._widget, "Check for Vendor Update",
                f"A newer driver ({update.latest_version}) is available "
                f"for {driver.device_name}, but installing it needs "
                f"administrator rights. Restart this app as administrator "
                f"to install it.")
            return
        confirm = QMessageBox.question(
            self._widget, "Check for Vendor Update",
            f"{update.vendor} has version {update.latest_version} "
            f"available for {driver.device_name} (currently "
            f"{update.current_version}).\n\nDownload from "
            f"{update.download_url}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._run_vendor_update(driver, provider, update)

    def _run_vendor_update(self, driver: DriverInfo, provider, update) -> None:
        if self._status_lbl:
            self._status_lbl.setText(f"Downloading {update.vendor} driver...")

        def do_update(worker):
            download_result = vendor_pipeline.download_and_verify(
                update, allowed_domains=provider.allowed_download_domains)
            if download_result.path is None:
                return ("download_failed", download_result.reason)
            from modules.driver_manager.vendor_updates.rollback import snapshot_before_install
            token = snapshot_before_install(driver)
            install_result = vendor_pipeline.install_light(download_result.path, driver)
            return ("installed", install_result, token)

        worker = Worker(do_update)

        def on_result(result) -> None:
            if not widget_is_valid(self._widget):
                return
            if self._status_lbl:
                self._status_lbl.setText("Click Refresh to load drivers.")
            if result[0] == "download_failed":
                QMessageBox.warning(self._widget, "Check for Vendor Update",
                                   f"Could not use this update: {result[1]}")
                return
            _, install_result, token = result
            if not install_result.ok:
                QMessageBox.warning(self._widget, "Check for Vendor Update",
                                   f"Install failed: {install_result.reason}")
                return
            if token:
                self._applied_update_tokens.append(token)
            QMessageBox.information(self._widget, "Check for Vendor Update",
                                   f"{driver.device_name} updated to "
                                   f"{update.latest_version}. Click Refresh "
                                   f"to see the change.")

        def on_error(err_str: str) -> None:
            if not widget_is_valid(self._widget):
                return
            if self._status_lbl:
                self._status_lbl.setText("Click Refresh to load drivers.")
            QMessageBox.warning(self._widget, "Check for Vendor Update",
                               f"Update failed unexpectedly: {err_str}")

        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)
        self._workers.append(worker)
        if self.app and getattr(self.app, "thread_pool", None) is not None:
            self.app.thread_pool.start(worker)
        else:
            QThreadPool.globalInstance().start(worker)
```

Add `self._applied_update_tokens: List[str] = []` to `__init__` — this
session's rollback tokens, appended to by `_run_vendor_update` above and
consumed by Task 9's rollback UI.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k "vendor_update or read_only_unelevated" -v`
Expected: PASS.

- [ ] **Step 5: Run the full driver_manager suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py tests/test_driver_module.py tests/test_driver_baselines.py tests/test_driver_detail_dialog.py tests/test_driver_diagnostics.py tests/test_vendor_id.py tests/test_vendor_provider.py tests/test_nvidia_provider.py tests/test_vendor_update_pipeline.py tests/test_vendor_update_rollback.py tests/test_power_management.py -v`

```bash
git add src/modules/driver_manager/driver_module.py tests/test_driver_module.py
git commit -m "feat(driver manager): wire vendor-update check/confirm/install into the tab

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: Rollback UI — "Undo This Update" and "Undo All Updates This Session"

**Files:**
- Modify: `src/modules/driver_manager/driver_module.py`
- Test: `tests/test_driver_module.py`

**Interfaces:**
- Consumes: `rollback.rollback(token) -> InstallResult`,
  `rollback.bulk_rollback(tokens) -> List[InstallResult]` (Task 6),
  `self._applied_update_tokens` (Task 8).

Read `_on_context_menu`'s current body (as it stands after Task 8) before
editing — this task adds one more conditional context-menu action plus a
toolbar action, following the exact enable/disable and Worker patterns
every other action in this file already uses.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_driver_module.py -- add these
def test_undo_this_update_is_disabled_when_nothing_was_updated_this_session():
    mod = _module()
    driver = DriverInfo(device_name="A", driver_class="Net", version="1.0",
                        date="", publisher="V", signed=True, error_code=0,
                        flags="", inf_name="oem12.inf")
    assert mod._can_undo_update(driver) is False


def test_undo_this_update_rolls_back_the_devices_own_token(monkeypatch):
    mod = _module()
    driver = DriverInfo(device_name="A", driver_class="Net", version="1.0",
                        date="", publisher="V", signed=True, error_code=0,
                        flags="", inf_name="oem12.inf")
    mod._applied_update_tokens.append("oem12.inf")
    assert mod._can_undo_update(driver) is True
    called = []
    monkeypatch.setattr(dmod, "rollback_one", lambda token: called.append(token) or
                        dmod.InstallResult(ok=True, reason="", restore_point_taken=True))
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._undo_this_update(driver)
    assert called == ["oem12.inf"]
    assert mod._applied_update_tokens == []  # consumed on success
    assert shown


def test_undo_this_update_reports_a_rollback_failure(monkeypatch):
    mod = _module()
    driver = DriverInfo(device_name="A", driver_class="Net", version="1.0",
                        date="", publisher="V", signed=True, error_code=0,
                        flags="", inf_name="oem12.inf")
    mod._applied_update_tokens.append("oem12.inf")
    monkeypatch.setattr(dmod, "rollback_one",
                        lambda token: dmod.InstallResult(ok=False, reason="store pruned it",
                                                         restore_point_taken=True))
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "warning",
                        lambda *a, **k: shown.append(a[2]))
    mod._undo_this_update(driver)
    assert shown
    assert "store pruned it" in shown[0]
    # token stays -- rollback failed, nothing to consume
    assert mod._applied_update_tokens == ["oem12.inf"]


def test_undo_all_updates_this_session_reports_every_result(monkeypatch):
    mod = _module()
    mod._applied_update_tokens.extend(["oem1.inf", "oem2.inf"])
    monkeypatch.setattr(dmod, "bulk_rollback_all", lambda tokens: [
        dmod.InstallResult(ok=True, reason="", restore_point_taken=True),
        dmod.InstallResult(ok=False, reason="not found", restore_point_taken=True),
    ])
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._undo_all_updates_this_session()
    assert shown
    assert "1" in shown[0] and "not found" in shown[0]
    assert mod._applied_update_tokens == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k "undo" -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

Add to the import block from Task 8:
```python
from modules.driver_manager.vendor_updates.rollback import (
    rollback as rollback_one, bulk_rollback as bulk_rollback_all,
)
from modules.driver_manager.vendor_updates.pipeline import InstallResult
```

In `_on_context_menu`, after the `act_check_update` action added in Task 8:
```python
        act_undo_update = menu.addAction("Undo This Update")
        act_undo_update.setEnabled(bool(driver) and self._can_undo_update(driver))
        act_undo_update.triggered.connect(
            lambda: self._undo_this_update(driver) if driver else None)
```

In `_build_toolbar`, after the existing Snapshots/System-Restore-Points
buttons (this is a toolbar action, not a context-menu one, since it acts
on the whole session, not one row):
```python
        self._undo_all_updates_btn = QPushButton("Undo All Updates This Session")
        self._undo_all_updates_btn.setEnabled(False)  # enabled once len(self._applied_update_tokens) > 0
        toolbar.addWidget(self._undo_all_updates_btn)
```
and its wiring, alongside the other button connections:
```python
        self._undo_all_updates_btn.clicked.connect(self._undo_all_updates_this_session)
```

New methods on `DriverModule`:
```python
    def _can_undo_update(self, driver: DriverInfo) -> bool:
        token = published_name_for(driver.inf_name)
        return bool(token) and token in self._applied_update_tokens

    def _undo_this_update(self, driver: DriverInfo) -> None:
        token = published_name_for(driver.inf_name)
        if not token or token not in self._applied_update_tokens:
            return
        confirm = QMessageBox.question(
            self._widget, "Undo This Update",
            f"Roll {driver.device_name} back to its previous driver "
            f"package ({token})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        result = rollback_one(token)
        if result.ok:
            self._applied_update_tokens.remove(token)
            QMessageBox.information(self._widget, "Undo This Update",
                                   f"{driver.device_name} was rolled back.")
        else:
            QMessageBox.warning(self._widget, "Undo This Update",
                               f"Could not roll back: {result.reason}")
        self._refresh_undo_all_button_state()

    def _undo_all_updates_this_session(self) -> None:
        if not self._applied_update_tokens:
            return
        confirm = QMessageBox.question(
            self._widget, "Undo All Updates This Session",
            f"Roll back all {len(self._applied_update_tokens)} update(s) "
            f"applied this session?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        tokens = list(self._applied_update_tokens)
        results = bulk_rollback_all(tokens)
        succeeded = sum(1 for r in results if r.ok)
        failures = [r.reason for r in results if not r.ok]
        self._applied_update_tokens.clear()
        message = f"{succeeded} of {len(results)} rolled back successfully."
        if failures:
            message += "\n\nFailures:\n" + "\n".join(f"- {f}" for f in failures)
        QMessageBox.information(self._widget, "Undo All Updates This Session", message)
        self._refresh_undo_all_button_state()

    def _refresh_undo_all_button_state(self) -> None:
        if self._undo_all_updates_btn:
            self._undo_all_updates_btn.setEnabled(bool(self._applied_update_tokens))
```

Note: `_undo_all_updates_this_session`'s `bulk_rollback_all` call, like
every other real-work call in this file, should run on a `Worker` rather
than the UI thread once a real rollback is more than a fast local
operation — `pnputil /add-driver ... /install` is a real subprocess call
per device, so for 2+ devices this can take real time. Wrap the body above
in a `Worker` the same way `_run_vendor_update` (Task 8) does; the given
code above is deliberately shown synchronous for test clarity — the
Worker wrapping is mechanical (see Task 8's `_run_vendor_update` for the
exact pattern to copy: build the worker function, connect
`signals.result`/`signals.error`, guard with `widget_is_valid`, track in
`self._workers`, start via `self.app.thread_pool`/`QThreadPool.globalInstance()`
fallback) and the tests above test the underlying logic directly by
calling the methods synchronously, matching this file's existing test
style for its other Worker-driven methods.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k "undo" -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/modules/driver_manager/driver_module.py tests/test_driver_module.py
git commit -m "feat(driver manager): undo one or all vendor updates applied this session

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: Power-management UI

**Files:**
- Modify: `src/modules/driver_manager/driver_module.py`
- Test: `tests/test_driver_module.py`

**Interfaces:**
- Consumes: `power_management.get_power_management(device_id) -> Optional[bool]`,
  `power_management.set_power_management(device_id, allow_off) -> Tuple[bool, str]`
  (Task 7).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_driver_module.py -- add these
def test_power_management_action_disabled_when_device_has_no_setting(monkeypatch):
    mod = _module()
    driver = DriverInfo(device_name="A", driver_class="Net", version="1.0",
                        date="", publisher="V", signed=True, error_code=0,
                        flags="", device_id="PCI\\VEN_1234&DEV_5678\\0")
    monkeypatch.setattr(dmod, "get_power_management", lambda device_id: None)
    assert mod._has_power_management_setting(driver) is False


def test_power_management_dialog_shows_current_state_and_toggles(monkeypatch):
    mod = _module()
    driver = DriverInfo(device_name="A", driver_class="Net", version="1.0",
                        date="", publisher="V", signed=True, error_code=0,
                        flags="", device_id="PCI\\VEN_1234&DEV_5678\\0")
    monkeypatch.setattr(dmod, "get_power_management", lambda device_id: True)
    written = []
    monkeypatch.setattr(dmod, "set_power_management",
                        lambda device_id, allow_off: written.append((device_id, allow_off)) or (True, ""))
    # Simulate the user choosing "No" (disallow power-off) in the confirm dialog.
    monkeypatch.setattr(dmod.QMessageBox, "question",
                        lambda *a, **k: dmod.QMessageBox.StandardButton.Yes)
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "information",
                        lambda *a, **k: shown.append(a[2]))
    mod._toggle_power_management(driver)
    assert written == [("PCI\\VEN_1234&DEV_5678\\0", False)]  # was True (allowed), toggled off
    assert shown


def test_power_management_write_failure_is_reported(monkeypatch):
    mod = _module()
    driver = DriverInfo(device_name="A", driver_class="Net", version="1.0",
                        date="", publisher="V", signed=True, error_code=0,
                        flags="", device_id="PCI\\VEN_1234&DEV_5678\\0")
    monkeypatch.setattr(dmod, "get_power_management", lambda device_id: False)
    monkeypatch.setattr(dmod, "set_power_management",
                        lambda device_id, allow_off: (False, "access denied"))
    monkeypatch.setattr(dmod.QMessageBox, "question",
                        lambda *a, **k: dmod.QMessageBox.StandardButton.Yes)
    shown = []
    monkeypatch.setattr(dmod.QMessageBox, "warning",
                        lambda *a, **k: shown.append(a[2]))
    mod._toggle_power_management(driver)
    assert shown
    assert "access denied" in shown[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k "power_management" -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

Add to the import block:
```python
from modules.driver_manager.vendor_updates.power_management import (
    get_power_management, set_power_management,
)
```

In `_on_context_menu`, after the undo-update action added in Task 9:
```python
        act_power_mgmt = menu.addAction("Power Management...")
        act_power_mgmt.setEnabled(bool(driver) and self._has_power_management_setting(driver))
        act_power_mgmt.triggered.connect(
            lambda: self._toggle_power_management(driver) if driver else None)
```

New methods on `DriverModule`:
```python
    def _has_power_management_setting(self, driver: DriverInfo) -> bool:
        if not driver.device_id:
            return False
        return get_power_management(driver.device_id) is not None

    def _toggle_power_management(self, driver: DriverInfo) -> None:
        current = get_power_management(driver.device_id)
        if current is None:
            return  # shouldn't be reachable -- action is disabled in this case
        new_value = not current
        verb = "allow" if new_value else "prevent"
        confirm = QMessageBox.question(
            self._widget, "Power Management",
            f"Currently: the computer {'is allowed' if current else 'is NOT allowed'} "
            f"to turn off {driver.device_name} to save power.\n\n"
            f"{verb.capitalize()} the computer to turn off this device?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return
        ok, reason = set_power_management(driver.device_id, new_value)
        if ok:
            QMessageBox.information(self._widget, "Power Management",
                                   f"Updated {driver.device_name}'s power "
                                   f"management setting.")
        else:
            QMessageBox.warning(self._widget, "Power Management",
                               f"Could not change this setting: {reason}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_module.py -k "power_management" -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full driver_manager suite, then commit**

Run: `.venv\Scripts\python.exe -m pytest tests/test_driver_reader.py tests/test_driver_module.py tests/test_driver_baselines.py tests/test_driver_detail_dialog.py tests/test_driver_diagnostics.py tests/test_vendor_id.py tests/test_vendor_provider.py tests/test_nvidia_provider.py tests/test_vendor_update_pipeline.py tests/test_vendor_update_rollback.py tests/test_power_management.py -v`

```bash
git add src/modules/driver_manager/driver_module.py tests/test_driver_module.py
git commit -m "feat(driver manager): per-device power-management toggle in the context menu

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: Real-machine verification tool

**Files:**
- Create: `tools/driver_vendor_update_check.py`
- No test file (this is a real-machine harness, matching
  `tools/security_catalog_check.py`'s `--apply` pattern and
  `tools/monitor_control_check.py`'s read-only-by-default shape).

**Interfaces:**
- Consumes: `provider.provider_for`, `nvidia_provider.NvidiaProvider`,
  `pipeline.download_and_verify`.

- [ ] **Step 1: Implement**

```python
# tools/driver_vendor_update_check.py
"""Real-machine harness for the vendor-update pipeline (Task 11 of Phase 1).

Default mode is READ-ONLY: checks every currently-installed device against
its vendor provider (if one exists) and prints what it finds, downloading
nothing. Pass --download to also download and signature-verify (never
install) the first available update found, as a real end-to-end proof the
pipeline's download+verify half works against a live NVIDIA response.

Never installs anything -- that's the point of it being separate from the
app's own confirm-gated UI action.
"""
import argparse
import sys

sys.path.insert(0, "src")

from modules.driver_manager.driver_reader import fetch_drivers
from modules.driver_manager.vendor_updates.provider import provider_for, no_provider_reason
from modules.driver_manager.vendor_updates import nvidia_provider  # noqa: F401 -- registers NVIDIA
from modules.driver_manager.vendor_updates.pipeline import download_and_verify


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true",
                        help="also download+verify (never install) the "
                             "first available update found")
    args = parser.parse_args()

    drivers = fetch_drivers()
    print(f"{len(drivers)} drivers found.\n")

    any_update_found = False
    for driver in drivers:
        provider = provider_for(driver)
        if provider is None:
            reason = no_provider_reason(driver)
            print(f"[{driver.device_name}] no check possible: {reason.value if reason else 'unknown'}")
            continue
        update = provider.check_for_update(driver)
        if update is None:
            print(f"[{driver.device_name}] ({provider.vendor_name}): "
                 f"no update available (current: {driver.version})")
            continue
        print(f"[{driver.device_name}] ({provider.vendor_name}): "
             f"UPDATE AVAILABLE {driver.version} -> {update.latest_version} "
             f"({update.download_url})")
        if args.download and not any_update_found:
            any_update_found = True
            print("  Downloading and verifying (will NOT install)...")
            result = download_and_verify(update, allowed_domains=provider.allowed_download_domains)
            if result.path:
                print(f"  Downloaded and signature-verified OK: {result.path}")
            else:
                print(f"  FAILED: {result.reason}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run it for real**

Run: `.venv\Scripts\python.exe tools\driver_vendor_update_check.py`
Confirm it prints a real result for every device with no crash. If any
device has an NVIDIA GPU, confirm the NVIDIA provider path runs against
the live API without error (a "no update available" answer is a valid,
expected result if the driver is already current — that's not a failure
of this check).

Run: `.venv\Scripts\python.exe tools\driver_vendor_update_check.py --download`
if an update is genuinely available, to prove the full download+verify
half of the pipeline against real NVIDIA infrastructure. **Do not run
anything from `install_light` via this tool** — that stays behind the
app's own confirm-gated UI action.

- [ ] **Step 3: Commit**

```bash
git add tools/driver_vendor_update_check.py
git commit -m "test(driver manager): real-machine vendor-update check harness

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Final Verification

After Task 11, run the full project suite and confirm only the two
documented pre-existing `test_procengine_gpuinfo.py` failures:

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
```

Then follow this plan's standard close-out: a final whole-branch review
(per `superpowers:subagent-driven-development`'s Final Review section),
fix any Critical/Important findings, and hand off via
`superpowers:finishing-a-development-branch` — merge
`feat/driver-manager-phase1` back to master once green.

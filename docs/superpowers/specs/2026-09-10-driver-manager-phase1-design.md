# Driver Manager Phase 1 — Update Foundation (LIGHT Install, Rollback, Power Toggle)

**Status:** Implemented and merged, MINUS the power-management toggle
(deferred — see note below and the implementation plan's Task 7/10
entries for the full reason).
**Author:** Claude (design session with user, 2026-09-10)
**Scope:** Phase 1 of the 6-phase Driver Manager improvement program named in
`docs/superpowers/specs/2026-09-09-driver-manager-phase0-design.md`'s Context
section. This is the first phase with real writes.

**Post-implementation note (2026-09-13):** Goal 7 and its `power_management.py`
component (below) were never built. The design assumed a `PnPCapabilities`
registry DWORD maps cleanly to the Power Management tab's checkbox (0 =
allowed, 24 = disabled), sourced secondhand and flagged in this spec's own
Open Questions as needing live verification. That verification — an
interactive Device Manager experiment — never happened (no agent working
this plan has interactive GUI access, and the user was unable to complete
it after being asked). Real evidence surfaced in the meantime that
contradicts the assumption: real inbox driver INFs on the dev machine set
`PnPCapabilities` to `1` and `0x120`, not a clean binary pair — suggesting
it's a general capability bitfield, not a single toggle. Shipping either
direction (read or write) on an unverified, now actively-contradicted
mapping risked confidently telling the user the wrong thing about a real
device's state, which is worse than not shipping it. Everything else in
this spec (Goals 1-6, 8) was built as designed.

## Context

Phase 0 (shipped, merged) made Driver Manager's existing read/export/uninstall
surface richer, entirely read-only. This phase adds the first real write
capability: looking up whether a device has a newer driver available from its
vendor, downloading it, and installing **just the driver files** (INF/SYS/CAT
— no vendor control-panel software, no telemetry services, no bundled
bloatware) rather than the vendor's full installer. That "LIGHT" install is
this phase's centerpiece; the "FULL" (run the vendor's own installer) path is
explicitly Phase 2's job, once this phase's safety pipeline is proven.

Two independent-but-related features ride along in this same phase because
they share the same "restore point → write → verify" infrastructure this
phase builds: **rollback** of an update this feature applied, and a
**per-device power-management toggle**. Neither depends on the other; both
depend on this phase's write-safety machinery.

Later phases (not this spec, sketched in Phase 0's design doc):
- **2** — full-installer update path, additional vendor adapters, bulk
  "Update All", update pinning, Updates-module Run-All stage integration.
- **3** — generic vendor-agnostic search chain (paid API → scrape →
  driver-DB fallback, no specific service picked yet — research when we get
  there), background scanning, Dashboard "N updates available".

## Goals

1. A vendor-provider architecture: given a `DriverInfo`, identify which
   vendor made the hardware (from the PCI/USB vendor ID in `hardware_id`,
   never from the free-text `publisher` string) and, if a provider exists for
   that vendor, ask it whether a newer driver exists and where to get it.
   A vendor with no provider yet answers honestly — "no update source
   configured for this vendor" — never a guess.
2. One proven vendor provider: **NVIDIA**, using the real, stable endpoints
   NVIDIA's own driver-download page itself uses (verified during this design
   session, not assumed — see NVIDIA Provider below).
3. A shared safety pipeline every provider and every install path (LIGHT now,
   FULL in Phase 2) goes through: confirm → download → verify signature →
   restore point → install → verify the result.
4. **LIGHT install**: extract just INF/SYS/CAT from the downloaded package
   and install via `pnputil /add-driver ... /install` — never the vendor's
   bundled software. If a given package can't be reliably extracted this
   way, LIGHT is unavailable for that specific update, stated honestly.
5. **Rollback**: undo an update *this feature* applied to one device, back to
   the driver package that was in place immediately before. This is not
   general "roll back any driver to any past version" — that remains Device
   Manager's job (existing, deliberate decision — see CLAUDE.md's Driver
   Manager section: "Roll-back-to-previous-version is routed to Device
   Manager... this app does not track" arbitrary driver history). It becomes
   trackable here only because this feature is the one doing the write, and
   snapshots what it's about to replace before it does.
6. **Bulk driver restore**: do the above for every device this feature
   updated in one session, in one action.
7. **Per-device power-management toggle**: expose and let the user change
   "Allow the computer to turn off this device to save power" (the same
   setting Device Manager's own Power Management tab shows), read/write via
   registry (`HKLM\SYSTEM\CurrentControlSet\Enum\<device_id>\Device
   Parameters\...` — confirm the exact value name during implementation; do
   not assume a value that turns out wrong).
8. Elevation gate: everything Phase 0 built stays unelevated (reads).
   Everything this phase adds that writes (install, rollback, power-toggle
   write) requires admin — Driver Manager's `requires_admin` currently
   `False`; add `read_only_unelevated = True` (Monitor Control's own pattern)
   so the tab still opens and reads normally unelevated, with only the write
   actions gated.

## Non-goals (explicitly out of scope for Phase 1)

- FULL (vendor-installer) updates — Phase 2.
- Any vendor besides NVIDIA — Phase 2 adds more adapters one at a time; this
  phase proves the pipeline on one.
- Bulk "Check All" / "Update All" across every device — Phase 2. This phase
  is per-device, on-demand only (matches how the user's own earlier answer
  scoped this: "both" per-device and bulk, with per-device coming first).
- Rolling back a driver this feature did NOT install (general driver
  history) — stays Device Manager's job, unchanged.
- The generic vendor-agnostic fallback chain — Phase 3.
- A paid driver-lookup API — no specific service chosen; Phase 3 research.

## Architecture

New package `src/modules/driver_manager/vendor_updates/`, kept separate from
the existing flat `driver_manager/*.py` files since this is a distinct,
growing subsystem (more providers arrive in Phase 2+) rather than one more
sibling file:

```
src/modules/driver_manager/vendor_updates/
  __init__.py
  vendor_id.py       (new — PCI/USB vendor-ID -> company name; static table)
  provider.py        (new — the VendorProvider protocol/ABC + registry)
  nvidia_provider.py (new — the first real provider)
  pipeline.py         (new — shared confirm/download/verify/restore-point/
                        install pipeline, Qt-free)
  power_management.py (new — per-device power-toggle read/write, Qt-free)
  rollback.py         (new — snapshot-before-write + restore, Qt-free)
src/modules/driver_manager/
  driver_module.py    (existing — new context-menu actions: "Check for
                        Vendor Update...", "Power Management...", plus a
                        rollback affordance for a just-applied update)
```

Everything under `vendor_updates/` is Qt-free, same split `scan/`+`store/`
already keep in TreeSize and `driver_reader.py`/`driver_baselines.py`/
`driver_diagnostics.py` already keep in this module — testable with no
display, no elevation (except the actual install/write calls, which are
mocked in tests the same way `driver_reader.py`'s subprocess calls already
are).

## Components

### `vendor_id.py` (new)

```python
def vendor_for_hardware_id(hardware_id: str) -> Optional[str]:
    """'NVIDIA' for a hardware_id starting 'PCI\\VEN_10DE', None for an
    unrecognized or empty vendor prefix. Static table, not a live lookup --
    PCI-SIG vendor IDs are permanent and don't change. Starts with just the
    vendors this phase and the next actually build providers for; growing
    the table is how Phase 2+ adds recognition for more vendors even before
    their provider exists (so the UI can say 'AMD detected, no update source
    configured yet' rather than 'unknown vendor')."""
```

Static dict, e.g. `{"10DE": "NVIDIA", "1002": "AMD", "8086": "Intel"}` (PCI
vendor IDs; USB `VID_` prefixes get their own table if/when a USB-attached
device provider is ever built — out of scope here, every provider Phase 1/2
targets is a PCI device, i.e. GPUs). Only `"10DE"` has a working provider
behind it this phase; the others are recognized-but-unsupported, which is
the whole point of Goal 1's honesty requirement.

### `provider.py` (new)

```python
@dataclass(frozen=True)
class UpdateInfo:
    vendor: str
    current_version: str
    latest_version: str
    download_url: str          # the vendor's own domain, verified below
    installer_signer: str      # expected Authenticode signer, e.g.
                                # "NVIDIA Corporation" -- checked after download


class VendorProvider(Protocol):
    vendor_name: str            # must match vendor_id.py's output exactly
    allowed_download_domains: List[str]  # hardcoded allowlist, never derived
                                          # from the lookup response itself
    expected_signer: str

    def check_for_update(self, driver: DriverInfo) -> Optional[UpdateInfo]:
        """None if no update is available, the vendor's lookup refused, the
        response couldn't be parsed, or this driver isn't confidently
        identifiable in the vendor's own catalog -- never a guess. A real
        UpdateInfo only when the provider is confident."""


_PROVIDERS: Dict[str, VendorProvider] = {}  # populated at import time by
                                             # each provider module registering
                                             # itself, e.g. nvidia_provider.py


def provider_for(driver: DriverInfo) -> Optional[VendorProvider]:
    """vendor_id.vendor_for_hardware_id(driver.hardware_id), then a
    dict lookup. None either way is the same answer to the caller: no
    check possible right now, with the caller responsible for saying WHY
    (unrecognized vendor vs. recognized-but-no-provider) -- see below."""
```

### `nvidia_provider.py` (new)

Verified during this design session against the real, live endpoints
NVIDIA's own driver-download page itself calls (not scraping HTML, not
guessing):

1. **GPU name → `pfid`**: `GET https://www.nvidia.com/Download/API/lookupValueSearch.aspx?TypeID=3`
   returns an XML table of every current GPU model name to its `pfid` (GPU
   family id) — e.g. `GeForce RTX 4090` → `995`. Fetch and cache this table
   locally (e.g. `%APPDATA%/WindowsTweaker/driver_updates/nvidia_pfid_cache.xml`,
   refreshed if older than N days — confirm a sensible N during
   implementation, a week is a reasonable starting point since new GPU
   models don't appear often). Match `driver.device_name` against the
   table's `Name` field. **No match → `None`, not a guess at the closest
   name.**
2. **Latest driver for that `pfid`**: `GET https://gfwsl.geforce.com/services_toolkit/services/com/nvidia/services/AjaxDriverService.php?func=DriverManualLookup&pfid=<pfid>&osID=<osID>&dch=1`
   — `osID` needs its own confirmed value for the actual Windows version
   running (community sources found `57` for "Windows 10 64-bit"; **confirm
   the correct `osID` for Windows 11 64-bit during implementation with a
   real call and manual verification against nvidia.com's own UI for the
   same GPU** — do not ship a guessed osID, a wrong one silently returns the
   wrong OS's driver). `dch=1` requests the current DCH driver type (the
   only type NVIDIA now ships for Windows 10/11).
3. Parse the response for the latest version string and its direct
   `.exe` download URL — **confirm the exact response shape (JSON vs XML,
   field names) with a real call during implementation**; this design
   session verified the *request* pattern is real and stable (used by the
   community `nvidia-update` tool for years) but did not capture a live
   response body to pin the exact parse code against.
4. `allowed_download_domains = ["download.nvidia.com", "international.download.nvidia.com", "us.download.nvidia.com"]`
   (or whatever NVIDIA's actual CDN response resolves to — **confirm real
   domain(s) from an actual response during implementation**, don't
   hardcode from assumption).
5. `expected_signer = "NVIDIA Corporation"`.

Every "confirm during implementation" above is a real, bounded spike (a
handful of live HTTP calls plus reading the actual response), not an
architectural unknown — the mechanism itself is proven; only exact field
names/domains need pinning down with real traffic.

### `pipeline.py` (new)

The one shared path every provider and every install mode goes through, so
adding a provider or adding FULL in Phase 2 never means re-deriving safety
logic:

```python
def check_and_confirm(driver: DriverInfo, parent) -> Optional[UpdateInfo]:
    """provider_for(driver) -> check_for_update -> if found, a confirm
    dialog naming vendor/current version/new version/URL. Returns None if
    no provider, no update, or the user declines -- all three are 'nothing
    to do', but the CALLER (driver_module.py) is responsible for showing
    which of the three it was, honestly, not collapsing them into silence."""

def download_and_verify(update: UpdateInfo) -> Tuple[Optional[str], str]:
    """Downloads update.download_url (refusing anything outside
    update-provider-specific allowed_download_domains -- checked against
    the URL's actual host, not string-contains) to a temp/cache dir, then
    core.procengine.signatures.verify_signature() on the result. Returns
    (path, "") on a VALID signature matching update.installer_signer,
    (None, reason) otherwise -- wrong signer, invalid, unsigned, or
    could-not-verify are all real, distinct refusals, never silently
    treated as good enough."""

def install_light(installer_path: str, driver: DriverInfo) -> InstallResult:
    """7-Zip-extracts installer_path looking for .inf/.sys/.cat files
    (matching this codebase's existing CBS-log precedent for shelling out
    to 7z), then `pnputil /add-driver <extracted.inf> /install`. If
    extraction finds no usable INF, returns a result saying LIGHT isn't
    available for this specific package -- never partially applies
    something and calls it done."""


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    reason: str                 # always populated, success or failure
    restore_point_taken: bool
    previous_package_snapshot: Optional[str]  # for rollback.py
```

`create_restore_point(...)` (from the existing `core/system_restore.py`,
already used by `windows_updater.py` for exactly this purpose) runs before
`install_light` ever touches the system — and per this codebase's
established rule (`core/system_restore.py`'s own design, referenced in
Phase 0's spec and CLAUDE.md's Tweak System section): if the restore point
itself can't be created, refuse the whole operation rather than proceed
without one.

### `rollback.py` (new)

Before `install_light` runs, snapshot enough about the device's CURRENT
driver package to undo the install later: `driver.inf_name`,
`published_name_for(driver.inf_name)` (already exists), and the driver
store's OEM package folder (`store_folder_for`, already exists in
`cleanup/cleanup_scanner/driver_store.py`) — copy or record what
`pnputil /export-driver` would need, or simply record the OEM package name
and rely on `pnputil` still having the prior package in the store (Windows
keeps superseded packages until something prunes them — Cleanup's own
"Superseded Drivers" panel is proof they're normally still there right
after an update). Confirm during implementation which of "copy the package
ourselves" vs. "trust the store still has it" is more reliable — a
real-machine spike, not a guess.

```python
def snapshot_before_install(driver: DriverInfo) -> Optional[str]:
    """Returns an opaque token (implementation TBD per the spike above)
    identifying how to get back to the current state, or None if nothing
    to snapshot (e.g. driverless device -- nothing to roll back to)."""

def rollback(token: str) -> InstallResult:
    """Reverses one snapshot_before_install, through the same restore-point
    -> write -> verify pipeline as install_light -- a rollback is a write
    too and gets the identical safety treatment, not a shortcut."""

def bulk_rollback(tokens: List[str]) -> List[InstallResult]:
    """Every token from one update session, applied in one action. Reports
    per-device results -- a partial failure (3 of 4 rolled back) is stated
    as exactly that, never collapsed into one pass/fail."""
```

### `power_management.py` (new)

```python
def get_power_management(device_id: str) -> Optional[bool]:
    """True/False for the current 'allow the computer to turn off this
    device' setting, None if not determinable for this device (many
    devices -- most storage/GPU/system devices -- don't expose this
    setting at all; that's a real 'not applicable', not a refusal)."""

def set_power_management(device_id: str, allow_off: bool) -> Tuple[bool, str]:
    """Writes the setting; (False, reason) on any failure (access denied,
    the device doesn't support it, the registry value doesn't exist to
    write). Never silently no-ops."""
```

Confirm the exact registry path/value name against a real device with a
Power Management tab during implementation (Device Manager's own UI is the
ground truth to match, not a guess from documentation that may be stale).

### `driver_module.py` additions

- Context menu: "Check for Vendor Update..." (enabled always — the honest
  "no provider for this vendor" answer IS the check, not a reason to hide
  the action), "Power Management..." (enabled only when
  `get_power_management` returns non-`None` for the device), and, only on a
  device this session actually updated, "Undo This Update" (rollback) plus
  a toolbar "Undo All Updates This Session" (bulk rollback) once 2+ updates
  have been applied in the current session.
- Every write action confirms first, runs on a `Worker` (never the UI
  thread — this phase's own install/download calls are exactly the kind of
  real work Phase 0's final review already flagged this codebase for
  getting wrong once), and reports success/failure/partial honestly in
  `self._status_lbl` and/or a `QMessageBox`.

## Data Flow

`DriverInfo.hardware_id` (already populated, Phase 0) is the only new input
this phase reads from the existing driver list — nothing about `fetch_drivers`
changes. Everything downloaded goes to a cache directory under
`%APPDATA%/WindowsTweaker/driver_updates/` (matching the established
`%APPDATA%/WindowsTweaker/...` convention `driver_baselines.py` already
uses), cleaned up after a successful install (a failed/declined download is
left for one retry without re-fetching, then cleaned on the next app start
if stale — confirm a reasonable staleness window during implementation).

## Error Handling

Every new function follows this codebase's established rule across the
board: a refused, failed, or not-applicable read is `None`, distinctly,
never collapsed into a value that looks like a real answer. Specifically:
- "No provider for this vendor" (recognized vendor, no adapter yet),
  "vendor recognized but hardware_id vendor byte unrecognized", "provider
  ran but found no confident match", and "provider ran and confirmed no
  update available" are FOUR different answers, and the UI text for each
  must say which one it is — never collapsed into one "nothing to check"
  message.
- A signature verification failure (wrong signer, invalid, unsigned,
  could-not-verify) always refuses the install and states which of those
  four it was — this is the single highest-stakes refusal in this whole
  phase and gets no shortcuts.
- A restore point that can't be created refuses the whole write, full stop
  — matching `core/system_restore.py`'s own existing contract.
- LIGHT extraction finding no usable INF/SYS is a real, distinct outcome
  ("LIGHT not available for this package"), never a partial/fake success.

## Testing

- `vendor_id.py`, `provider.py`, `pipeline.py`, `rollback.py`,
  `power_management.py`: Qt-free, direct unit tests with fixture data and
  mocked subprocess/HTTP calls — no real network access, no real WMI/
  registry writes in tests, matching every other Qt-free file in this
  module.
- `nvidia_provider.py`: unit tests mock the two HTTP calls (pfid lookup,
  driver lookup) with fixture response bodies captured during the
  implementation-time spike above — never a live network call in the test
  suite itself.
- `driver_module.py` additions: the established `_module()`/`_FakeApp`
  harness, `Worker`-based per Phase 0's own already-fixed "run real work off
  the UI thread" lesson.
- A real-machine, read-only-by-default harness (matching
  `tools/driver_manager_check.py`-style tools elsewhere in this codebase,
  e.g. `tools/security_catalog_check.py`'s `--apply` flag pattern) for a
  human to run the actual pipeline once, deliberately, outside the test
  suite — this phase installs real driver files, which is not something
  any automated test suite should ever do unattended.

## Open Questions

Four of the five raised during the design session were resolved with real,
live verification before the plan was written (kept here for the record,
not as open items anymore):

1. **NVIDIA driver-lookup response shape — RESOLVED.** A real call
   (`func=DriverManualLookup&psid=101&pfid=995&osID=135&languageCode=1033&
   beta=null&isWHQL=1&dch=1&sort1=0&numberOfResults=10` — `psid=101` is
   GeForce's product-series id, required alongside `pfid` or the endpoint
   answers `DriverDownloadIDNotFound`) returned real JSON. The true success
   signal is `IDS[0].downloadInfo.Success == "1"` — **not** the top-level
   `Success` field, which echoes back something else entirely (observed
   `"10"` in a real response, not a boolean). Real field names on the
   result object: `Version`, `DownloadURL`, `DownloadURLFileSize`,
   `ReleaseDateTime`, `IsWHQL`, `IsBeta`, `DetailsURL`, `OSList` (array of
   `{OSName, OsCode}`). Every string value is URL-encoded (`%20` for
   spaces) — decode with `urllib.parse.unquote()` before display.
2. **Correct `osID` for Windows 11 — RESOLVED.** `135` (a single unified
   "Windows 11" entry — unlike Windows 10, there's no separate 32/64-bit
   split since Windows 11 only ships 64-bit). Confirmed via
   `lookupValueSearch.aspx?TypeID=4`'s real OS table, not the Windows-10-era
   community value.
3. **Real CDN domain — RESOLVED.** `us.download.nvidia.com`, observed in a
   real response. Allowlist by suffix match (`host.endswith
   ("download.nvidia.com")`) to also cover regional mirrors
   (`international.download.nvidia.com` etc.) without a loose substring
   check.
4. **Rollback mechanism — RESOLVED (evidence-based, not experimental).**
   This codebase's own Cleanup module already measures that Windows
   retains superseded OEM driver packages after a real-world update on
   this exact machine (CLAUDE.md: "the 7.13 GB store yields 2.2 MB of
   superseded packages"). `rollback.py` therefore trusts the driver store
   to still hold the pre-update package rather than copying it separately
   — BUT the implementation task must verify this holds true immediately
   after THIS feature's own install (not just "eventually", which is what
   the existing evidence actually shows), and fall back to copying the
   package proactively before install if a real test shows otherwise.
5. **Power-management registry mechanism — STILL OPEN, needs a real-machine
   experiment, not web research.** Community sources point at a
   `PnPCapabilities` DWORD under the device's Class registry key, but what
   was actually verified live: this codebase's own machine has a real
   device with the value entirely ABSENT (consistent with "default/allowed,
   matching this codebase's established 'absent means applied' pattern" —
   but not proof of the bit-level meaning for the checked/unchecked state,
   only for whether the feature is available at all). The implementation
   task for `power_management.py` starts with a concrete experiment (toggle
   the checkbox via Device Manager's own UI on a real device that has one,
   diff the registry before/after) rather than a guessed bit value — see
   Task 8 in the plan.
6. **Download cache staleness window** — how long an undecided/declined
   download sits before cleanup; a reasonable default (e.g. 7 days) ships
   in the plan, adjustable later, not blocking.

## Self-Review

- **Placeholder scan**: five Open Questions above are all bounded,
  implementation-time spikes with a stated fallback or resolution path, not
  unresolved gaps in the design itself.
- **Internal consistency**: every write path (LIGHT install, rollback,
  power-management write) goes through the same restore-point discipline;
  no goal item lacks a component; the NVIDIA provider is explicitly scoped
  as the ONE proven adapter, consistent with the Non-goals section.
- **Scope check**: this spec covers Phase 1 only, as the user explicitly
  chose to keep it bundled (foundation + LIGHT + rollback + bulk restore +
  power toggle) rather than split it further. FULL install, more vendors,
  bulk "Update All", and the generic fallback chain are named for context
  but designed in Phase 2/3's own specs, not here.
- **Ambiguity check**: the two genuinely ambiguous readings surfaced during
  this design session — whether Phase 1 includes real vendor lookup at all,
  and what "rollback"/"bulk driver restore" mean — were both resolved
  explicitly with the user rather than assumed, and are recorded as such
  above.

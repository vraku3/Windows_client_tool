"""Failed Store app installs/updates, read from Windows' own deployment log.

The question an admin asks first about a Store app that "will not update" is
not answered by `Get-AppxPackage` at all -- that only lists what IS installed.
The AppX deployment service records every attempt that failed, readable
unelevated, in `Microsoft-Windows-AppXDeploymentServer/Operational`:

* **Event 404 is one row per failed operation**, with structured
  `EventData`: `PackageFullName`, `ErrorCode` (`0x80073d02`), `SummaryError`
  (the human text) and `CallingProcess` (`svchost.exe,wuauserv` for Store /
  Windows Update, `svchost.exe,AppReadiness`, `ms-teamsupdate.exe`,
  `setup.exe`...). 401 and 419 repeat the same failure in other words, so
  only 404 is read -- reading all three would triple every count. Measured
  here: 24 of them, read with
  `wevtutil qe ... /q:*[System[(EventID=404)]] /rd:true /f:xml` in ~30 ms.
* **"Is this still broken?" is answered by the log's own success record, not
  by comparing versions.** Event 400 is a SUCCEEDED operation, 401 a failed
  one; a 400 with `DeploymentOperation` 1 (Add) or 6 (Register) for the same
  `PackageFullName`, later than the failure, means a retry got through.
  Version comparison alone is wrong for bundles (`_neutral_~_`): the BUNDLE
  version is not the package version. Measured 2026-10-09: ScreenSketch's
  bundle 2022.2608.41.0 failed against an installed 11.2608.41.0, and
  OfficeHub's 2026.1005.1447.0 against 19.2610.35011.0 -- both read as
  "pending" by version, and both have a successful Register 400 hours later.
  CrossDevice 1.26082.74.0 (0x80073D02 that morning) has no later 400: that
  update genuinely is still waiting. Versions are only the fallback when no
  success record is found. Operation codes seen here: 1 Add, 2 Remove,
  4 Stage, 5 DeStage, 6 Register, 11 StageUserData, 27 OnDemandRegister,
  29 Provision, 33 Deprovision. A successful Stage precedes nearly every
  failed Register, so Stage does not count as completion.
* Some 404s name **no package** (0x80073CF8 "Failure to get staging session
  for: x-windowsupdate://..."; seven in one Windows Update scan) and some name
  only a **family** (`Microsoft.Edge.GameAssist_8wekyb3d8bbwe`, no version).
  Both are grouped and shown with the state "unknown" -- never claimed as
  fixed or still broken.
* The log is **5 MB circular**: here it reached back only to 2026-10-02 with
  6,740 records. The window it covers is reported, because "no failures"
  means "none since <date>". A success is always newer than the failure it
  resolves, so rotation can drop a failure but never orphan its fix.
* `wevtutil` **refuses with a non-zero exit** (5 = access denied, 15007 =
  channel not found). That is a `read_error`, never an empty list.
* An unelevated `Get-AppxPackage` lists the current user's packages only, so
  "not installed" means "not installed for this user" -- 404s logged under
  S-1-5-18 can be for another account's registration.
"""
import locale
import logging
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from core.appx_service import _version_key as version_key
from modules.store_apps.appx_errors import describe, format_code, parse_hresult

logger = logging.getLogger(__name__)

LOG_NAME = "Microsoft-Windows-AppXDeploymentServer/Operational"
FAILURE_EVENT_ID = 404
SUCCESS_EVENT_ID = 400
#: DeploymentOperation values whose success means the package is in place.
_COMPLETING_OPERATIONS = {"1", "6"}   # Add, Register
_NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"

STATE_PENDING = "pending"          # no later success, older version installed
STATE_RESOLVED = "resolved"        # a later Add/Register succeeded
STATE_NOT_INSTALLED = "not_installed"
STATE_UNKNOWN = "unknown"          # no version or no package in the event

_STATE_ORDER = {STATE_PENDING: 0, STATE_NOT_INSTALLED: 1,
                STATE_UNKNOWN: 2, STATE_RESOLVED: 3}

STATE_LABELS = {
    STATE_PENDING: "Still pending",
    STATE_RESOLVED: "Resolved by a later retry",
    STATE_NOT_INSTALLED: "Not installed for this user",
    STATE_UNKNOWN: "Cannot tell",
}


@dataclass(frozen=True)
class DeploymentFailure:
    when: Optional[datetime]
    package_full_name: str
    package_name: str
    attempted_version: str
    code: Optional[int]
    summary: str
    caller: str
    user_sid: str


@dataclass
class FailureGroup:
    package_name: str
    code: Optional[int]
    count: int = 0
    first: Optional[datetime] = None
    last: Optional[datetime] = None
    attempted_version: str = ""
    attempted_full_name: str = ""
    installed_version: str = ""
    resolved_at: Optional[datetime] = None
    state: str = STATE_UNKNOWN
    summary: str = ""
    caller: str = ""

    @property
    def code_text(self) -> str:
        return format_code(self.code)

    @property
    def meaning(self) -> str:
        return describe(self.code)[1]

    @property
    def symbol(self) -> str:
        return describe(self.code)[0]

    @property
    def display_name(self) -> str:
        return self.package_name or "(no package named in the event)"


@dataclass
class FailureReport:
    groups: List[FailureGroup] = field(default_factory=list)
    events: int = 0
    log_since: Optional[datetime] = None
    read_error: str = ""

    @property
    def pending(self) -> List[FailureGroup]:
        return [g for g in self.groups if g.state == STATE_PENDING]


# ----------------------------------------------------------------------
# Parsing (pure)
# ----------------------------------------------------------------------

def split_full_name(full_name: str) -> Tuple[str, str]:
    """(name, version) from a package full or family name.

    Full: `Name_Version_Arch_ResourceId_PublisherId` (`~` for a bundle's
    resource id). Family: `Name_PublisherId` -- no version. Package names
    cannot contain `_`, so the split is unambiguous.
    """
    if not full_name:
        return "", ""
    parts = full_name.split("_")
    if len(parts) >= 5:
        return parts[0], parts[1]
    return parts[0], ""


def is_bundle(full_name: str) -> bool:
    parts = (full_name or "").split("_")
    return len(parts) >= 5 and parts[3] == "~"


def _parse_time(text: str) -> Optional[datetime]:
    if not text:
        return None
    text = text.rstrip("Z")
    if "." in text:
        head, frac = text.split(".", 1)
        text = f"{head}.{frac[:6]}"
    try:
        return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    except ValueError:
        logger.debug("unparseable event time %r", text)
        return None


def _events(text: str):
    if not text or not text.strip():
        return []
    return ET.fromstring(f"<Events>{text.strip()}</Events>").findall(f"{_NS}Event")


def _event_data(event) -> Dict[str, str]:
    return {item.get("Name", ""): (item.text or "").strip()
            for item in event.iter(f"{_NS}Data")}


def _event_time(event) -> Optional[datetime]:
    created = event.find(f"{_NS}System/{_NS}TimeCreated")
    return _parse_time(created.get("SystemTime", "")) if created is not None else None


def parse_events_xml(text: str) -> List[DeploymentFailure]:
    """`wevtutil qe /f:xml` output (bare <Event> elements) -> failures."""
    failures = []
    for event in _events(text):
        security = event.find(f"{_NS}System/{_NS}Security")
        data = _event_data(event)
        full = data.get("PackageFullName", "")
        name, version = split_full_name(full)
        failures.append(DeploymentFailure(
            when=_event_time(event), package_full_name=full,
            package_name=name, attempted_version=version,
            code=parse_hresult(data.get("ErrorCode", "")),
            summary=data.get("SummaryError", ""),
            caller=data.get("CallingProcess", ""),
            user_sid=security.get("UserID", "") if security is not None else "",
        ))
    return failures


def parse_success_xml(text: str) -> Dict[str, datetime]:
    """Event 400s -> {PackageFullName: latest completing (Add/Register) success}."""
    latest: Dict[str, datetime] = {}
    for event in _events(text):
        data = _event_data(event)
        if data.get("DeploymentOperation", "") not in _COMPLETING_OPERATIONS:
            continue
        full, when = data.get("PackageFullName", ""), _event_time(event)
        if full and when is not None and (full not in latest or when > latest[full]):
            latest[full] = when
    return latest


def _judge(g: FailureGroup, installed: Optional[str],
           successes: Dict[str, datetime]) -> str:
    done = successes.get(g.attempted_full_name) if g.attempted_full_name else None
    if done is not None and (g.last is None or done > g.last):
        g.resolved_at = done
        return STATE_RESOLVED
    if not g.attempted_version:
        return STATE_UNKNOWN
    if installed is None:
        return STATE_NOT_INSTALLED
    if installed == g.attempted_version:
        return STATE_RESOLVED
    if (not is_bundle(g.attempted_full_name)
            and version_key(installed) > version_key(g.attempted_version)):
        return STATE_RESOLVED
    return STATE_PENDING


def _absorb(g: FailureGroup, f: DeploymentFailure) -> None:
    g.count += 1
    if f.when is not None:
        if g.first is None or f.when < g.first:
            g.first = f.when
        if g.last is None or f.when >= g.last:
            g.last = f.when
            g.summary, g.caller = f.summary, f.caller
    elif not g.summary:
        g.summary, g.caller = f.summary, f.caller
    if f.attempted_version and (
            not g.attempted_version
            or version_key(f.attempted_version) > version_key(g.attempted_version)):
        g.attempted_version = f.attempted_version
        g.attempted_full_name = f.package_full_name


def group_failures(failures: Iterable[DeploymentFailure],
                   installed: Dict[str, str],
                   successes: Optional[Dict[str, datetime]] = None,
                   ) -> List[FailureGroup]:
    """One row per (package, error code), judged against the log and the
    installed list.

    `installed` maps package Name -> installed Version; `successes` is
    `parse_success_xml`'s map. Repeats collapse into a count with first/last
    seen; the newest attempted version is the one judged, so an older failed
    version superseded by a newer failed one does not read as resolved.
    """
    successes = successes or {}
    groups: Dict[Tuple[str, Optional[int]], FailureGroup] = {}
    for f in failures:
        key = (f.package_name, f.code)
        if key not in groups:
            groups[key] = FailureGroup(package_name=f.package_name, code=f.code)
        _absorb(groups[key], f)
    for g in groups.values():
        inst = installed.get(g.package_name) if g.package_name else None
        g.installed_version = inst or ""
        g.state = _judge(g, inst, successes)
    floor = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(groups.values(),
                  key=lambda g: (_STATE_ORDER[g.state], -(g.last or floor).timestamp()))


# ----------------------------------------------------------------------
# Reading the real log
# ----------------------------------------------------------------------

def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # Piped wevtutil output is the ANSI code page on some builds.
        return raw.decode(locale.getpreferredencoding(False), errors="replace")


def _wevtutil(args: List[str], timeout: int = 20) -> Tuple[int, str, str]:
    proc = subprocess.run(
        ["wevtutil", "qe", LOG_NAME, *args, "/f:xml"],
        capture_output=True, timeout=timeout, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return proc.returncode, _decode(proc.stdout), _decode(proc.stderr)


def _refusal_text(rc: int, err: str) -> str:
    lines = [ln.strip() for ln in (err or "").splitlines() if ln.strip()]
    reason = lines[-1] if lines else f"wevtutil exited {rc}"
    return f"Could not read {LOG_NAME}: {reason}"


def read_failures(installed: Dict[str, str], max_events: int = 1000) -> FailureReport:
    """Read event 404s and judge each group against the log and `installed`.

    A refused or failed read sets `read_error` and leaves `groups` empty --
    the caller must show that, not "no failures".
    """
    report = FailureReport()
    try:
        rc, out, err = _wevtutil([f"/q:*[System[(EventID={FAILURE_EVENT_ID})]]",
                                  "/rd:true", f"/c:{max_events}"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.read_error = f"Could not run wevtutil: {exc}"
        logger.warning("deployment log read failed: %s", exc)
        return report
    if rc != 0:
        report.read_error = _refusal_text(rc, err)
        logger.warning("%s", report.read_error)
        return report
    try:
        failures = parse_events_xml(out)
    except ET.ParseError as exc:
        report.read_error = f"The deployment log returned unreadable XML: {exc}"
        logger.warning("%s", report.read_error)
        return report
    report.events = len(failures)
    report.groups = group_failures(failures, installed, _read_successes())
    report.log_since = _oldest_record_time()
    return report


def _read_successes() -> Dict[str, datetime]:
    """Completing successes. A failed read only costs the "resolved" verdicts
    that depend on it (they fall back to versions), so it is logged, not
    raised."""
    try:
        rc, out, err = _wevtutil([f"/q:*[System[(EventID={SUCCESS_EVENT_ID})]]",
                                  "/rd:true", "/c:5000"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("could not read deployment successes: %s", exc)
        return {}
    if rc != 0:
        logger.warning("could not read deployment successes: %s",
                       _refusal_text(rc, err))
        return {}
    try:
        return parse_success_xml(out)
    except ET.ParseError as exc:
        logger.warning("deployment success XML unreadable: %s", exc)
        return {}


def _oldest_record_time() -> Optional[datetime]:
    """When the circular log's oldest surviving record was written."""
    try:
        rc, out, err = _wevtutil(["/c:1"], timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("could not read the deployment log's oldest record: %s", exc)
        return None
    if rc != 0:
        logger.warning("could not read the deployment log's oldest record: %s",
                       _refusal_text(rc, err))
        return None
    try:
        events = _events(out)
    except ET.ParseError as exc:
        logger.warning("oldest record XML unreadable: %s", exc)
        return None
    return _event_time(events[0]) if events else None

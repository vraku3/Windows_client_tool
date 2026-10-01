"""Software inventory analysis (Qt-free): runtimes, duplicate versions,
end-of-life flags, filter chips, findings and exports.

The end-of-life table is a STATIC, dated snapshot of vendor lifecycle pages
(``EOL_TABLE_AS_OF``).  It says "past its published end-of-support date", not
"vulnerable"; it is a prompt to look, and it can go stale.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from modules.software_inventory.software_reader import SoftwareEntry
from modules.startup_manager.persistence import resolve_command

logger = logging.getLogger(__name__)

EOL_TABLE_AS_OF = date(2026, 9, 26)
OLD_YEARS = 5

# --------------------------------------------------------------------------
# Dates and versions
# --------------------------------------------------------------------------


def parse_install_date(text: str) -> Optional[date]:
    """Uninstall keys store YYYYMMDD; anything else is unknown, not 'today'."""
    t = (text or "").strip()
    for fmt in ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            logger.debug("install date %r does not match %s", t, fmt)
            continue
    return None


def version_key(version: str) -> Tuple[int, ...]:
    """Numeric sort key; '' when nothing numeric ('Unknown', '')."""
    return tuple(int(p) for p in re.findall(r"\d+", version or "")[:6])


# --------------------------------------------------------------------------
# Broken uninstallers
# --------------------------------------------------------------------------


def uninstaller_target_status(entry: SoftwareEntry) -> Optional[bool]:
    """``True`` when the program ``UninstallString`` would launch is
    CONFIRMED MISSING from disk; ``False`` when it was confirmed present.
    ``None`` means the question does not apply or could not be answered --
    never collapsed into either verdict:

    - no ``UninstallString`` at all (a different, already-flagged problem);
    - an MSI entry (``WindowsInstaller``), which resolves through msiexec and
      a product code, not a file path, and through winget, which resolves
      through its own database -- neither depends on anything checked here;
    - the path itself could not be stat'd for a reason other than "not
      found" (e.g. access denied partway down the tree) -- a refusal, not a
      missing file.

    A confirmed ``False`` is a real, specific admin headache: the installer's
    own cached copy (common for WiX/Burn/NSIS/InstallShield bootstrappers,
    e.g. a ``Package Cache`` folder a disk-cleanup tool removed) is gone, so
    clicking Uninstall -- here or in Programs and Features -- launches
    nothing and the entry never goes away on its own.
    """
    text = (entry.uninstall_string or "").strip()
    if entry.windows_installer or not text:
        return None
    low = text.lower()
    if low.startswith("winget ") or low.startswith("msiexec"):
        return None
    exe, _args = resolve_command(text)
    if not exe:
        return None
    try:
        os.stat(exe)
        return False       # confirmed present
    except FileNotFoundError:
        return True        # confirmed missing -- the real finding
    except OSError as e:
        logger.debug("could not determine whether uninstaller %r exists: %s", exe, e)
        return None


# --------------------------------------------------------------------------
# Runtime families
# --------------------------------------------------------------------------

_VCPP = re.compile(r"Visual C\+\+\s+(?:v)?(\d{4}(?:\s*-\s*\d{4})?|14)\b", re.I)
_DOTNET = re.compile(
    r"(?:\.NET (?:Runtime|SDK|Core|Host|AppHost|Desktop Runtime|Standard)|Windows Desktop Runtime|"
    r"ASP\.NET Core)[^\d]*(\d+)\.(\d+)", re.I)
_JAVA = re.compile(r"\b(?:Java(?:\(TM\))?(?: SE)?|JRE|JDK|OpenJDK|Temurin|Corretto|Zulu)\b", re.I)
_WEBVIEW = re.compile(r"WebView2", re.I)


def runtime_family(name: str) -> str:
    """'' when the entry is not a runtime; else a short family label."""
    if _VCPP.search(name) and re.search(r"redistributable|runtime", name, re.I):
        m = _VCPP.search(name)
        year = re.sub(r"\s+", "", m.group(1))
        return "Visual C++ 2015-2022" if year in ("14", "2015-2022", "2015", "2017", "2019", "2022") else f"Visual C++ {year}"
    m = _DOTNET.search(name)
    if m:
        return f".NET {m.group(1)}"
    if _JAVA.search(name) and not re.search(r"javascript|script", name, re.I):
        return "Java"
    if _WEBVIEW.search(name):
        return "WebView2"
    return ""


# --------------------------------------------------------------------------
# Duplicate detection
# --------------------------------------------------------------------------

_ARCH_X64 = re.compile(r"x64|64-bit|amd64|win64", re.I)
_ARCH_X86 = re.compile(r"x86|32-bit|win32", re.I)


def _arch(name: str) -> str:
    if _ARCH_X64.search(name):
        return "x64"
    if _ARCH_X86.search(name):
        return "x86"
    return ""


def product_key(name: str) -> str:
    """Name with version numbers stripped and the architecture kept as a token,
    so 'Java 8 Update 381 (64-bit)' and '... Update 401 (64-bit)' collide but
    x86 and x64 builds do not.  Single numbers (2010, 17) are kept: VC++ 2010
    and 2013 are different products."""
    n = name.lower()
    arch = _arch(n)
    n = re.sub(r"\((?:[^)]*\b(?:x64|x86|64-bit|32-bit|edition)\b[^)]*)\)", " ", n)
    n = re.sub(r"\b(?:x64|x86|amd64|win64|win32|64-bit|32-bit)\b", " ", n)
    n = re.sub(r"\bupdate\s+\d+\b", " ", n)
    n = re.sub(r"\bv?\d+(?:\.\d+){1,}\b", " ", n)          # 1.2, 14.51.36247
    n = re.sub(r"[-_,(){}\[\]]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return f"{n}|{arch}"


@dataclass
class DuplicateGroup:
    key: str
    display: str
    entries: List[SoftwareEntry]        # newest version first

    @property
    def newest(self) -> SoftwareEntry:
        return self.entries[0]


def find_duplicates(entries: Iterable[SoftwareEntry]) -> List[DuplicateGroup]:
    """Products installed at more than one DISTINCT version."""
    groups: Dict[str, List[SoftwareEntry]] = {}
    for e in entries:
        if e.is_update or not e.version:
            continue
        groups.setdefault(product_key(e.name), []).append(e)
    out: List[DuplicateGroup] = []
    for key, members in groups.items():
        if len({version_key(m.version) for m in members}) < 2:
            continue
        members.sort(key=lambda m: version_key(m.version), reverse=True)
        out.append(DuplicateGroup(key, key.split("|")[0], members))
    return sorted(out, key=lambda g: g.display)


# --------------------------------------------------------------------------
# End of life
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class EolInfo:
    product: str
    end_of_support: date

    def days_left(self, today: date) -> int:
        return (self.end_of_support - today).days


_DOTNET_EOL = {
    (1, 0): date(2019, 6, 27), (2, 0): date(2018, 10, 1), (2, 1): date(2021, 8, 21),
    (2, 2): date(2019, 12, 23), (3, 0): date(2020, 3, 3), (3, 1): date(2022, 12, 13),
    (5, 0): date(2022, 5, 10), (6, 0): date(2024, 11, 12), (7, 0): date(2024, 5, 14),
    (8, 0): date(2026, 11, 10), (9, 0): date(2026, 11, 10), (10, 0): date(2028, 11, 14),
}
_VCPP_EOL = {
    "2005": date(2016, 4, 12), "2008": date(2018, 4, 10), "2010": date(2020, 7, 14),
    "2012": date(2023, 1, 10), "2013": date(2024, 4, 9),
}
_OFFICE_EOL = {
    "2007": date(2017, 10, 10), "2010": date(2020, 10, 13), "2013": date(2023, 4, 11),
    "2016": date(2025, 10, 14), "2019": date(2025, 10, 14),
}
_PYTHON_EOL = {
    "2.7": date(2020, 1, 1), "3.6": date(2021, 12, 23), "3.7": date(2023, 6, 27),
    "3.8": date(2024, 10, 7), "3.9": date(2025, 10, 31),
}
_JAVA_EOL = {"6": date(2013, 2, 1), "7": date(2015, 4, 1)}


def eol_for(entry: SoftwareEntry) -> Optional[EolInfo]:
    name = entry.name
    m = _DOTNET.search(name)
    if m:
        major, minor = int(m.group(1)), int(m.group(2))
        eol = _DOTNET_EOL.get((major, minor)) or (_DOTNET_EOL.get((major, 0)) if major >= 5 else None)
        return EolInfo(f".NET {major}.{minor}", eol) if eol else None
    m = re.search(r"Visual C\+\+\s+(2005|2008|2010|2012|2013)\b", name, re.I)
    if m and _VCPP_EOL.get(m.group(1)):
        return EolInfo(f"Visual C++ {m.group(1)} runtime", _VCPP_EOL[m.group(1)])
    m = re.search(r"(?:Office|Microsoft 365).*?\b(2007|2010|2013|2016|2019)\b", name, re.I)
    if m and "microsoft" in (entry.publisher + name).lower():
        return EolInfo(f"Microsoft Office {m.group(1)}", _OFFICE_EOL[m.group(1)])
    m = re.match(r"\s*Python\s+(\d+\.\d+)", name, re.I)
    if m and m.group(1) in _PYTHON_EOL:
        return EolInfo(f"Python {m.group(1)}", _PYTHON_EOL[m.group(1)])
    if _JAVA.search(name) and not re.search(r"javascript", name, re.I):
        jm = re.search(r"(?:Java(?:\(TM\))?(?: SE)?|JRE|JDK)\s*(?:Runtime Environment\s*)?(?:1\.)?(6|7)\b", name, re.I)
        if jm:
            return EolInfo(f"Java {jm.group(1)}", _JAVA_EOL[jm.group(1)])
    if re.search(r"flash player", name, re.I):
        return EolInfo("Adobe Flash Player", date(2020, 12, 31))
    if re.search(r"silverlight", name, re.I):
        return EolInfo("Microsoft Silverlight", date(2021, 10, 12))
    return None


# --------------------------------------------------------------------------
# Rows, chips, filtering
# --------------------------------------------------------------------------

@dataclass
class Row:
    entry: SoftwareEntry
    family: str = ""
    installed: Optional[date] = None
    age_years: Optional[float] = None
    eol: Optional[EolInfo] = None
    eol_days_left: Optional[int] = None
    superseded_by: str = ""          # newest version string when an older duplicate
    winget_available: str = ""
    uninstaller_missing: Optional[bool] = None   # True = confirmed gone; None = N/A or unknown
    tags: Set[str] = field(default_factory=set)

    @property
    def name(self) -> str:
        return self.entry.name


CHIPS = ["All", "Apps", "Runtimes", "System components", "Duplicates", "End of life",
         f"Old ({OLD_YEARS}+ yr)", "32-bit", "User install", "Updates available", "No publisher",
         "Broken uninstaller"]


def analyze(entries: Sequence[SoftwareEntry], today: Optional[date] = None,
            winget: Optional[Dict[str, str]] = None) -> List[Row]:
    """``winget`` maps lower-cased display name -> available version (None when
    no check was run, so the chip stays empty rather than claiming 'up to date')."""
    today = today or date.today()
    dup_newest: Dict[int, str] = {}
    for g in find_duplicates(entries):
        for older in g.entries[1:]:
            dup_newest[id(older)] = g.newest.version
    rows: List[Row] = []
    for e in entries:
        installed = parse_install_date(e.install_date)
        row = Row(entry=e, family=runtime_family(e.name), installed=installed)
        if installed and installed <= today:
            row.age_years = (today - installed).days / 365.25
        row.eol = eol_for(e)
        if row.eol:
            row.eol_days_left = row.eol.days_left(today)
        row.superseded_by = dup_newest.get(id(e), "")
        row.uninstaller_missing = uninstaller_target_status(e)
        if winget:
            row.winget_available = winget.get(e.name.lower().strip(), "")
        row.tags = _tags(row, today)
        rows.append(row)
    return rows


def _tags(row: Row, today: date) -> Set[str]:
    e, tags = row.entry, set()
    hidden = e.system_component or e.is_update
    if e.system_component:
        tags.add("System components")
    if not hidden:
        tags.add("Apps")
    if row.family:
        tags.add("Runtimes")
    if row.superseded_by:
        tags.add("Duplicates")
    if row.eol is not None and row.eol_days_left is not None and row.eol_days_left < 0:
        tags.add("End of life")
    if row.age_years is not None and row.age_years >= OLD_YEARS and not hidden:
        tags.add(f"Old ({OLD_YEARS}+ yr)")
    if e.type_ == "32-bit":
        tags.add("32-bit")
    if e.type_ == "User":
        tags.add("User install")
    if row.winget_available:
        tags.add("Updates available")
    if not e.publisher.strip() and not hidden:
        tags.add("No publisher")
    if row.uninstaller_missing is True:
        tags.add("Broken uninstaller")
    tags.add("All")
    return tags


def chip_counts(rows: Sequence[Row]) -> Dict[str, int]:
    counts = {c: 0 for c in CHIPS}
    for r in rows:
        for t in r.tags:
            if t in counts:
                counts[t] += 1
    return counts


def matches_query(row: Row, query: str) -> bool:
    """Every whitespace-separated term must appear in some searchable field."""
    terms = query.lower().split()
    if not terms:
        return True
    e = row.entry
    hay = " ".join((e.name, e.version, e.publisher, e.install_date, e.type_, e.product_code,
                    e.install_location, e.uninstall_string, row.family)).lower()
    return all(t in hay for t in terms)


def filter_rows(rows: Sequence[Row], chip: str = "All", query: str = "") -> List[Row]:
    return [r for r in rows if chip in r.tags and matches_query(r, query)]


# --------------------------------------------------------------------------
# Findings
# --------------------------------------------------------------------------

@dataclass
class Finding:
    severity: str
    title: str
    detail: str


def software_findings(rows: Sequence[Row], winget_error: str = "") -> List[Finding]:
    out: List[Finding] = []
    broken = [r for r in rows if r.uninstaller_missing is True]
    if broken:
        names = ", ".join(r.name for r in broken[:5]) + (" ..." if len(broken) > 5 else "")
        out.append(Finding(
            "warning",
            f"{len(broken)} uninstall entr{'y points' if len(broken) == 1 else 'ies point'} at a program that no longer exists",
            f"{names}. Clicking Uninstall will launch nothing and the entry stays. "
            "Its own installer cache is gone (common after a disk-cleanup tool removed it) -- "
            "remove the program's own folder by hand, or try msiexec/Programs and Features if those still work."))
    seen_eol: Set[str] = set()
    for r in rows:
        if r.eol is None or r.eol_days_left is None or r.entry.is_update:
            continue
        if r.eol.product in seen_eol:
            continue
        if r.eol_days_left < 0:
            seen_eol.add(r.eol.product)
            out.append(Finding("warning", f"{r.eol.product} is past end of support",
                               f"Ended {r.eol.end_of_support:%Y-%m-%d} ({-r.eol_days_left // 365} yr ago), e.g. \"{r.name}\". "
                               "Static vendor-lifecycle table; check the vendor before acting."))
        elif r.eol_days_left <= 180:
            seen_eol.add(r.eol.product)
            out.append(Finding("info", f"{r.eol.product} support ends in {r.eol_days_left} days",
                               f"{r.eol.end_of_support:%Y-%m-%d}."))
    dupes = [r for r in rows if r.superseded_by and r.family]
    java = [r for r in rows if r.superseded_by and r.family == "Java"]
    if java:
        out.append(Finding("warning", f"{len(java)} older Java version(s) installed alongside a newer one",
                           "Old JREs stay on disk and can be picked up by apps; remove the superseded ones."))
    other = [r for r in rows if r.superseded_by and r.family != "Java" and not r.entry.system_component]
    if other:
        out.append(Finding("info", f"{len(other)} product(s) installed at more than one version",
                           "See the Duplicates chip."))
    if dupes and not java:
        out.append(Finding("info", f"{len(dupes)} superseded runtime version(s)",
                           "Usually harmless; runtimes are shared and older ones can be needed by old apps."))
    upd = [r for r in rows if r.winget_available]
    if upd:
        out.append(Finding("info", f"{len(upd)} package(s) have a winget update",
                           ", ".join(r.name for r in upd[:5]) + (" ..." if len(upd) > 5 else "")))
    if winget_error:
        out.append(Finding("info", "winget update check did not complete", winget_error))
    return out


# --------------------------------------------------------------------------
# winget upgrade output
# --------------------------------------------------------------------------

def parse_winget_upgrades(output: str) -> Optional[Dict[str, str]]:
    """{lower-case name: available version}.  None when the output is not a
    winget table at all (winget missing, error text), {} when it ran and
    found nothing to upgrade."""
    lines = output.splitlines()
    head = next((i for i, ln in enumerate(lines) if "Name" in ln and "Id" in ln and "Available" in ln), -1)
    if head < 0:
        if re.search(r"no installed package|no available upgrade|no applicable upgrade", output, re.I):
            return {}
        return None
    header = lines[head]
    c_name, c_id = header.index("Name"), header.index("Id")
    c_ver, c_av = header.index("Version"), header.index("Available")
    c_src = header.index("Source") if "Source" in header else len(header)
    result: Dict[str, str] = {}
    for ln in lines[head + 2:]:
        if not ln.strip() or ln.lstrip().startswith("-") or "upgrades available" in ln.lower():
            continue
        name = ln[c_name:c_id].strip()
        avail = ln[c_av:c_src].strip()
        if name and avail and avail.lower() != "unknown":
            result[name.lower()] = avail
    return result


# --------------------------------------------------------------------------
# Export / copy
# --------------------------------------------------------------------------

EXPORT_COLUMNS = ["Name", "Version", "Publisher", "Install date", "Size", "Architecture",
                  "Category", "Product code", "Uninstall command", "Quiet uninstall",
                  "Install location", "Registry key", "End of support", "Note"]


def _note(r: Row) -> str:
    notes = []
    if r.uninstaller_missing is True:
        notes.append("uninstaller program missing -- Uninstall will not work")
    if r.superseded_by:
        notes.append(f"older than installed {r.superseded_by}")
    if r.eol is not None and r.eol_days_left is not None and r.eol_days_left < 0:
        notes.append(f"{r.eol.product} past end of support")
    if r.winget_available:
        notes.append(f"winget update {r.winget_available}")
    return "; ".join(notes)


def row_values(r: Row) -> List[str]:
    e = r.entry
    category = ("System component" if e.system_component else "Update" if e.is_update
                else r.family or "Application")
    return [e.name, e.version, e.publisher, e.install_date, e.size_mb, e.type_, category,
            e.product_code, e.uninstall_string, e.quiet_uninstall or (
                f"{e.msi_uninstall} /qn" if e.msi_uninstall and e.windows_installer else ""),
            e.install_location, e.registry_key,
            f"{r.eol.end_of_support:%Y-%m-%d}" if r.eol else "", _note(r)]


def rows_to_csv(rows: Sequence[Row]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(EXPORT_COLUMNS)
    for r in rows:
        w.writerow(row_values(r))
    return buf.getvalue()


def rows_to_markdown(rows: Sequence[Row], host: str = "") -> str:
    lines = [f"### Installed software{': ' + host if host else ''} ({len(rows)})", "",
             "| Name | Version | Publisher | Installed | Arch | Note |", "|---|---|---|---|---|---|"]
    for r in rows:
        e = r.entry
        cells = [e.name, e.version, e.publisher, e.install_date, e.type_, _note(r)]
        lines.append("| " + " | ".join((c or "-").replace("|", "/") for c in cells) + " |")
    return "\n".join(lines) + "\n"


def detail_text(r: Row) -> str:
    e = r.entry
    lines = [e.name, f"Version: {e.version or 'not recorded'}    Publisher: {e.publisher or 'not recorded'}"]
    when = f"{r.installed:%Y-%m-%d} ({r.age_years:.1f} years ago)" if r.installed and r.age_years is not None \
        else (e.install_date or "not recorded")
    lines.append(f"Installed: {when}    Size: {e.size_mb or 'not recorded'}    Kind: {e.type_}")
    if r.family:
        lines.append(f"Runtime family: {r.family}")
    if r.superseded_by:
        lines.append(f"Superseded: version {r.superseded_by} of this product is also installed.")
    if r.eol:
        state = ("PAST END OF SUPPORT" if (r.eol_days_left or 0) < 0 else f"support ends in {r.eol_days_left} days")
        lines.append(f"Lifecycle: {r.eol.product} {state} ({r.eol.end_of_support:%Y-%m-%d}; "
                     f"table as of {EOL_TABLE_AS_OF:%Y-%m-%d})")
    if r.winget_available:
        lines.append(f"winget: update to {r.winget_available} is available")
    if r.uninstaller_missing is True:
        lines.append("Uninstall: BROKEN -- the uninstaller's own program no longer exists on disk. "
                     "This entry cannot remove itself; delete its install folder by hand.")
    lines.append(f"Location: {e.install_location or 'not recorded'}")
    lines.append(f"Registry: {e.registry_key}")
    if e.product_code:
        lines.append(f"MSI product code: {e.product_code}")
        lines.append(f"MSI uninstall: {e.msi_uninstall}")
    lines.append(f"Uninstall: {e.uninstall_string or 'none recorded'}")
    if e.quiet_uninstall:
        lines.append(f"Quiet uninstall: {e.quiet_uninstall}")
    return "\n".join(lines)

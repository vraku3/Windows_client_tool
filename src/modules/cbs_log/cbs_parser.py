import html
import logging
import re
from datetime import datetime
from typing import Optional, Tuple

from core.log_parser_base import LogParserBase
from core.types import LogEntry
from modules.log_viewer.error_codes import advice

logger = logging.getLogger(__name__)

_CBS_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}), (\w+)\s+(\S+)\s+(.+)$"
)

# Hex HRESULT patterns
_HRESULT = re.compile(r"\b0[xX]([0-9a-fA-F]{8})\b")
_INLINE_NAME = re.compile(r"0[xX]([0-9a-fA-F]{8})\s*-\s*([A-Za-z][A-Za-z0-9_]{3,})")

# Package stage transitions
_PLAN_EXEC = re.compile(
    r"(?:Exec:\s*Staging package|Plan:\s*Package|Appl:\s*Package):\s*(\S+?),\s*"
    r"current:\s*(\w+),\s*pending:\s*\w+,\s*target:\s*(\w+)",
    re.IGNORECASE,
)
_STATE_CHANGE = re.compile(
    r"Package:\s*(\S+?)\s+changed state(?: from (\w+))? to (\w+)",
    re.IGNORECASE,
)
_SET_STATE = re.compile(
    r"(?:Setting|Updating)\s+state for package:\s*(\S+?)\s+to\s+(\w+)",
    re.IGNORECASE,
)
_PKG_FAILED = re.compile(
    r"(?:Package:\s*(\S+?)\s+failed|Package failed:\s*(\S+)|Package failed\b)",
    re.IGNORECASE,
)

# Package names
_PACKAGE_NAME = re.compile(
    r"\b((?:Package_for_|Microsoft-Windows-|Mapping_Package_for_)[A-Za-z0-9_.~-]+)\b",
    re.IGNORECASE,
)
_PACKAGE_TOKEN = re.compile(
    r"package:\s*(\S+?)(?:[, \]]|$)",
    re.IGNORECASE,
)

# Target file paths
_TARGET_FILE_CONTEXT = re.compile(
    r"(?:Failed to find file:\s*|File not found:\s*|Manifest not found:\s*|"
    r"Failed to open file:\s*|Failed to load manifest for component:\s*\S+\s+file:\s*|"
    r"Cannot repair member file [^\"\r\n]*\"|Repairing corrupted file\s+|"
    r"Could not reproject corrupted file\s+|source file in store:\s*)"
    r"([a-zA-Z]:\\[^,\r\n\t\"<>|*?\[;]+|\\\\\?\\(?:[a-zA-Z]:\\[^,\r\n\t\"<>|*?\[;]+)|\\\?\?\\(?:[a-zA-Z]:\\[^,\r\n\t\"<>|*?\[;]+))",
    re.IGNORECASE,
)
_SERVICING_EXT_PATH = re.compile(
    r"(?:\\\\\?\\|\\\?\?\\)?([a-zA-Z]:\\(?:[^,\r\n\t\"<>|*?]+?\\)?(?:servicing\\packages|winsxs)[^,\r\n\t\"<>|*?]+?\.(?:mum|manifest|cat|dll|sys|exe))",
    re.IGNORECASE,
)
_GENERIC_WIN_PATH = re.compile(
    r"(?:\\\\\?\\|\\\?\?\\)?([a-zA-Z]:\\[^,\r\n\t\"<>|*?]+?\.(?:mum|manifest|cat|dll|sys|exe|cab|xml|edb))",
    re.IGNORECASE,
)

# Benign package lookup probe
_PROBE_LINE = re.compile(
    r"InternalOpenPackage failed for|Failed to internally open package|"
    r"Failed to create open package|Failed to OpenPackage using worker session",
    re.IGNORECASE,
)

# Failure category patterns
_CORRUPTION_PAT = re.compile(
    r"STATUS_SXS_COMPONENT_STORE_CORRUPT|cannot repair member file|"
    r"store corruption|Corrupt component store|do not match|STATUS_SXS_",
    re.IGNORECASE,
)
_MANIFEST_PAT = re.compile(
    r"Manifest not found|Failed to load manifest|Failed to open manifest|"
    r"manifest missing|manifest file not found",
    re.IGNORECASE,
)
_PAYLOAD_PAT = re.compile(
    r"missing payload|source file in store is also corrupted|source file was not found|"
    r"Failed to find file|cannot find payload",
    re.IGNORECASE,
)


class CBSParser(LogParserBase):
    """Deep parser for C:\\Windows\\Logs\\CBS\\CBS.log.

    Extracts servicing failures, affected packages, target files, stage
    transitions (Staged, Installed, Superseded, Failed), and actionable
    remedy guidance.
    """

    def __init__(self, file_path: str) -> None:
        super().__init__(file_path)
        # Carried so a continuation line can inherit the record it belongs to.
        self._last_timestamp = datetime.min
        self._last_source = ""

    def parse_line(self, line: str) -> Optional[LogEntry]:
        m = _CBS_PATTERN.match(line)
        if not m:
            return self._continuation(line)
        timestamp_str, level, component, message = m.groups()
        try:
            timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            logger.debug("Unparseable timestamp: %s", timestamp_str)
            return None
        self._last_timestamp = timestamp
        self._last_source = component

        clean_msg = message.strip()
        raw = {"component": component, "raw_level": level}

        _extract_hresults(clean_msg, raw)
        _extract_stage_transitions(clean_msg, raw)
        _extract_file_path(clean_msg, level, raw)
        _extract_servicing_failure(clean_msg, level, raw)

        return LogEntry(
            timestamp=timestamp,
            source=component,
            level=level.capitalize() if level.lower() in ("info", "warning", "error", "debug") else level,
            message=clean_msg,
            raw=raw,
        )

    def _continuation(self, line: str) -> Optional[LogEntry]:
        """A line that is not a new record, but belongs to the one above.

        Inherits timestamp and source, but keeps level as Info.
        """
        text = line.strip()
        if not text:
            return None
        return LogEntry(
            timestamp=self._last_timestamp,
            source=self._last_source,
            level="Info",
            message=line.rstrip(),
            raw={"continuation": True},
        )


def _extract_hresults(clean_msg: str, raw: dict) -> None:
    hresults = []
    for hm in _HRESULT.finditer(clean_msg):
        code = int(hm.group(1), 16)
        hresults.append(code)
    if hresults:
        raw["hresults"] = hresults
        raw["hresult"] = hresults[0]
        raw["hresult_hex"] = f"0x{hresults[0]:08X}"

    for inm in _INLINE_NAME.finditer(clean_msg):
        code = int(inm.group(1), 16)
        raw["hresult_name"] = inm.group(2)
        if "hresult" not in raw:
            raw["hresult"] = code
            raw["hresult_hex"] = f"0x{code:08X}"


def _extract_stage_transitions(clean_msg: str, raw: dict) -> None:
    transition_found = False
    target_state = None
    current_state = None
    package_name = None

    m_plan = _PLAN_EXEC.search(clean_msg)
    if m_plan:
        package_name, current_state, target_state = m_plan.groups()
        transition_found = True

    if not transition_found:
        m_state = _STATE_CHANGE.search(clean_msg)
        if m_state:
            package_name, current_state, target_state = m_state.groups()
            transition_found = True

    if not transition_found:
        m_set = _SET_STATE.search(clean_msg)
        if m_set:
            package_name, target_state = m_set.groups()
            transition_found = True

    if not transition_found:
        m_fail = _PKG_FAILED.search(clean_msg)
        if m_fail:
            pkg1, pkg2 = m_fail.groups()
            package_name = pkg1 or pkg2 or None
            target_state = "Failed"
            transition_found = True

    if transition_found and target_state:
        target_norm = target_state.capitalize()
        raw["target_state"] = target_norm
        if current_state:
            current_norm = current_state.capitalize()
            raw["current_state"] = current_norm
            raw["transition"] = f"{current_norm} -> {target_norm}"
        else:
            raw["transition"] = target_norm
        if package_name:
            raw["package"] = package_name.strip("~,[] ")

    if "package" not in raw:
        m_pkg = _PACKAGE_NAME.search(clean_msg)
        if m_pkg:
            raw["package"] = m_pkg.group(1).strip("~,[] ")
        else:
            m_tok = _PACKAGE_TOKEN.search(clean_msg)
            if m_tok:
                raw["package"] = m_tok.group(1).strip("~,[] ")


def _extract_file_path(clean_msg: str, level: str, raw: dict) -> None:
    file_path = None
    m_ctx = _TARGET_FILE_CONTEXT.search(clean_msg)
    if m_ctx:
        file_path = m_ctx.group(1).strip()
    elif _SERVICING_EXT_PATH.search(clean_msg):
        file_path = _SERVICING_EXT_PATH.search(clean_msg).group(1).strip()
    elif level.lower() in ("error", "warning") and _GENERIC_WIN_PATH.search(clean_msg):
        file_path = _GENERIC_WIN_PATH.search(clean_msg).group(1).strip()

    if file_path:
        if " [" in file_path:
            file_path = file_path.split(" [", 1)[0]
        if ";" in file_path:
            file_path = file_path.split(";", 1)[0]
        file_path = file_path.strip()

        while file_path.startswith("\\??\\") or file_path.startswith("\\\\?\\"):
            file_path = file_path[4:]
        if file_path.startswith("\\??"):
            file_path = file_path[3:]
        elif file_path.startswith("\\\\?"):
            file_path = file_path[3:]
        raw["file_path"] = file_path.strip()


def _extract_servicing_failure(clean_msg: str, level: str, raw: dict) -> None:
    is_probe = False
    primary_code = raw.get("hresult", 0)
    if primary_code == 0x800F0805 and _PROBE_LINE.search(clean_msg):
        is_probe = True
        raw["is_probe"] = True

    failure_type = None
    if not is_probe:
        if primary_code in (0x80073712, 0x800F0831, 0x8007371B) or _CORRUPTION_PAT.search(clean_msg):
            failure_type = "store_corruption"
        elif _MANIFEST_PAT.search(clean_msg) or (
            primary_code in (0x80070003, 0x80070490, 0x80073701, 0x80073715)
            and ("manifest" in clean_msg.lower() or ".mum" in clean_msg.lower())
        ):
            failure_type = "manifest_not_found"
        elif primary_code in (0x800F081F, 0x800F0906) or _PAYLOAD_PAT.search(clean_msg) or (
            primary_code == 0x80070002 and ("payload" in clean_msg.lower() or "source" in clean_msg.lower())
        ):
            failure_type = "missing_payload"
        elif raw.get("target_state") == "Failed" or (level.lower() == "error" and "package failed" in clean_msg.lower()):
            failure_type = "stage_failure"

    if failure_type:
        raw["failure_type"] = failure_type

    cause, remedy = _get_servicing_remedy(failure_type, primary_code, is_probe)
    if cause:
        raw["cause"] = cause
    if remedy:
        raw["remedy"] = remedy


def _get_servicing_remedy(failure_type: Optional[str], primary_code: int, is_probe: bool) -> Tuple[Optional[str], Optional[str]]:
    if failure_type == "missing_payload":
        return (
            "Component store lacks required update payload files (source missing or pruned).",
            "Run 'DISM /Online /Cleanup-Image /RestoreHealth'. If offline or Windows Update is unreachable, supply installation media.",
        )
    if failure_type == "manifest_not_found":
        return (
            "Servicing package manifest (.mum) or component manifest (.manifest) is missing from WinSxS.",
            "Run 'sfc /scannow' to restore manifests, then 'DISM /Online /Cleanup-Image /RestoreHealth'.",
        )
    if failure_type == "store_corruption":
        return (
            "Component store (WinSxS) repository corruption detected in package manifests, assemblies, or catalogs.",
            "Run 'DISM /Online /Cleanup-Image /RestoreHealth' followed by 'sfc /scannow'.",
        )
    if failure_type == "stage_failure":
        return (
            "Package installation or stage transition failed during servicing execution.",
            "Run 'DISM /Online /Cleanup-Image /RestoreHealth', clear '%windir%\\SoftwareDistribution\\Download', and retry.",
        )
    if primary_code and (primary_code & 0x80000000) and not is_probe:
        adv = advice(primary_code)
        if adv:
            return adv.cause, adv.remedy
    return None, None

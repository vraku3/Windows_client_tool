import html
import logging
import re
from datetime import datetime
from typing import Optional

from core.log_parser_base import LogParserBase
from core.types import LogEntry
from modules.log_viewer.error_codes import advice

logger = logging.getLogger(__name__)

_DISM_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}), (\w+)\s+(\S+)\s+(.+)$"
)

# Hex HRESULT patterns
_HRESULT = re.compile(r"\b0[xX]([0-9a-fA-F]{8})\b")
_INLINE_NAME = re.compile(r"0[xX]([0-9a-fA-F]{8})\s*-\s*([A-Za-z][A-Za-z0-9_]{3,})")

# Command line execution
_DISM_CMD_LINE = re.compile(r"Executing command line:\s*(.+)$", re.IGNORECASE)

# Scratch directory patterns
_SCRATCH_SET = re.compile(
    r"Scratch directory set to ['\"]?([^'\"\r\n]+?)['\"]?\.",
    re.IGNORECASE,
)
_SCRATCH_INPUT = re.compile(
    r"ScratchDirectory:\s*['\"]?([^,'\"\r\n\(\)]+)['\"]?",
    re.IGNORECASE,
)
_SCRATCH_LOW_SPACE = re.compile(
    r"Scratch directory at (.+?) has less than (\d+) MB of free space",
    re.IGNORECASE,
)
_SCRATCH_WARN_LINE = re.compile(
    r"Failed to (?:get|set) the scratch directory|Scratch directory (?:is not valid|full|out of space)",
    re.IGNORECASE,
)

# Package and Feature operation patterns
_DISM_ADD_PKG = re.compile(
    r"(?:Adding package|Processing the package:\s*(\S+)\.\s*State:\s*(?:Staged|Resolved|Add)|AddPackage(?:Internal)?)\s*(\S+)?",
    re.IGNORECASE,
)
_DISM_REM_PKG = re.compile(
    r"(?:Removing package|Processing the package:\s*(\S+)\.\s*State:\s*(?:Uninstall|Remove)|RemovePackage(?:Internal)?)\s*(\S+)?",
    re.IGNORECASE,
)
_DISM_FEATURE_OP = re.compile(
    r"(?:Enabling feature|Disabling feature)\s+([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)

# Operation token in DISM provider
_TOP_TOKEN = re.compile(
    r"Processing the top level command token\(([^)]+)\)",
    re.IGNORECASE,
)


class DISMParser(LogParserBase):
    """Deep parser for C:\\Windows\\Logs\\DISM\\dism.log.

    Extracts DISM operations (CheckHealth, ScanHealth, RestoreHealth, package
    additions/removals, component cleanup), scratch directory settings and
    warnings, HRESULT error codes, causes, and actionable remedies.
    """

    def __init__(self, file_path: str) -> None:
        super().__init__(file_path)
        # Carried so a continuation line can inherit the record it belongs to.
        self._last_timestamp = datetime.min
        self._last_source = ""

    def parse_line(self, line: str) -> Optional[LogEntry]:
        m = _DISM_PATTERN.match(line)
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

        _extract_dism_hresults(clean_msg, raw)
        _extract_dism_operations(clean_msg, raw)
        scratch_warning = _extract_dism_scratch(clean_msg, level, raw)
        _extract_dism_remedy(raw, scratch_warning)

        return LogEntry(
            timestamp=timestamp,
            source=component,
            level=level.capitalize() if level.lower() in ("info", "warning", "error", "debug") else level,
            message=clean_msg,
            raw=raw,
        )


def _extract_dism_hresults(clean_msg: str, raw: dict) -> None:
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


def _extract_dism_operations(clean_msg: str, raw: dict) -> None:
    operation = None
    target_package = None
    target_feature = None

    m_cmd = _DISM_CMD_LINE.search(clean_msg)
    if m_cmd:
        cmd = m_cmd.group(1).strip()
        raw["command"] = cmd
        cmd_lower = cmd.lower()
        op_map = [
            ("/checkhealth", "CheckHealth"), ("/scanhealth", "ScanHealth"),
            ("/restorehealth", "RestoreHealth"), ("/startcomponentcleanup", "StartComponentCleanup"),
            ("/analyzecomponentstore", "AnalyzeComponentStore"), ("/resetbase", "ResetBase"),
            ("/add-package", "Add-Package"), ("/remove-package", "Remove-Package"),
            ("/enable-feature", "Enable-Feature"), ("/disable-feature", "Disable-Feature"),
            ("/add-driver", "Add-Driver"), ("/remove-driver", "Remove-Driver"),
            ("/export-driver", "Export-Driver"), ("/get-packages", "Get-Packages"),
            ("/get-features", "Get-Features"), ("/get-drivers", "Get-Drivers"),
            ("/get-mountedwiminfo", "Get-MountedImageInfo"), ("/get-mountedimageinfo", "Get-MountedImageInfo"),
            ("/mount-image", "Mount-Image"), ("/mount-wim", "Mount-Image"),
            ("/unmount-image", "Unmount-Image"), ("/unmount-wim", "Unmount-Image"),
        ]
        for flag, op_name in op_map:
            if flag in cmd_lower:
                operation = op_name
                break

    if not operation:
        m_tok = _TOP_TOKEN.search(clean_msg)
        if m_tok:
            token = m_tok.group(1).strip().lower()
            token_map = {
                "cleanup-image": "Cleanup-Image", "checkhealth": "CheckHealth",
                "scanhealth": "ScanHealth", "restorehealth": "RestoreHealth",
                "startcomponentcleanup": "StartComponentCleanup", "add-package": "Add-Package",
                "remove-package": "Remove-Package", "enable-feature": "Enable-Feature",
                "disable-feature": "Disable-Feature", "add-driver": "Add-Driver",
                "remove-driver": "Remove-Driver",
            }
            operation = token_map.get(token, token.capitalize())

    if not operation:
        if "failed to restore image health" in clean_msg.lower() or "restorehealth completed" in clean_msg.lower():
            operation = "RestoreHealth"
        elif (
            "component store is repairable" in clean_msg.lower()
            or "no component store corruption detected" in clean_msg.lower()
            or "component store cannot be repaired" in clean_msg.lower()
        ):
            operation = "ScanHealth"
        elif _DISM_ADD_PKG.search(clean_msg):
            m_add = _DISM_ADD_PKG.search(clean_msg)
            operation = "Add-Package"
            target_package = m_add.group(1) or m_add.group(2)
        elif _DISM_REM_PKG.search(clean_msg):
            m_rem = _DISM_REM_PKG.search(clean_msg)
            operation = "Remove-Package"
            target_package = m_rem.group(1) or m_rem.group(2)
        elif _DISM_FEATURE_OP.search(clean_msg):
            m_feat = _DISM_FEATURE_OP.search(clean_msg)
            operation = "Enable-Feature" if "enabling" in clean_msg.lower() else "Disable-Feature"
            target_feature = m_feat.group(1)

    if operation:
        raw["operation"] = operation
    if target_package:
        raw["target_package"] = target_package.strip("~,[] ")
    if target_feature:
        raw["target_feature"] = target_feature.strip("~,[] ")


def _extract_dism_scratch(clean_msg: str, level: str, raw: dict) -> Optional[str]:
    scratch_dir = None
    scratch_warning = None
    scratch_free_mb = None

    m_low = _SCRATCH_LOW_SPACE.search(clean_msg)
    if m_low:
        scratch_dir = m_low.group(1).strip()
        scratch_free_mb = int(m_low.group(2))
        scratch_warning = f"Scratch directory has low free space: {scratch_free_mb} MB (< 1024 MB required)"
    else:
        m_set = _SCRATCH_SET.search(clean_msg)
        if m_set:
            scratch_dir = m_set.group(1).strip()
        else:
            m_inp = _SCRATCH_INPUT.search(clean_msg)
            if m_inp and m_inp.group(1).strip().lower() != "(null)":
                scratch_dir = m_inp.group(1).strip()

        if level.lower() in ("warning", "error") and _SCRATCH_WARN_LINE.search(clean_msg):
            scratch_warning = clean_msg

    if scratch_dir:
        raw["scratch_dir"] = scratch_dir
    if scratch_free_mb is not None:
        raw["scratch_free_mb"] = scratch_free_mb
    if scratch_warning:
        raw["scratch_warning"] = scratch_warning
    return scratch_warning


def _extract_dism_remedy(raw: dict, scratch_warning: Optional[str]) -> None:
    cause = None
    remedy = None

    if scratch_warning:
        cause = "The scratch directory has insufficient free space (< 1024 MB) or invalid path. Operations may fail."
        remedy = "Specify a scratch folder on a volume with at least 1 GB free space using '/ScratchDir:<path>'."
    else:
        primary_code = raw.get("hresult", 0)
        if primary_code and (primary_code & 0x80000000):
            if primary_code == 0x800F081F:
                cause = "The component store source files could not be found or downloaded."
                remedy = "Run 'DISM /Online /Cleanup-Image /RestoreHealth /Source:WIM:<path>\\install.wim:1 /LimitAccess'."
            elif primary_code == 0x80073712:
                cause = "Component store corruption was detected by DISM."
                remedy = "Run 'DISM /Online /Cleanup-Image /RestoreHealth' followed by 'sfc /scannow'."
            elif primary_code == 0x800F0906:
                cause = "The source files could not be downloaded from Windows Update or WSUS."
                remedy = "Verify network connection, or supply offline source using /Source:<path> /LimitAccess."
            else:
                adv = advice(primary_code)
                if adv:
                    cause = adv.cause
                    remedy = adv.remedy

    if cause:
        raw["cause"] = cause
    if remedy:
        raw["remedy"] = remedy

    def _continuation(self, line: str):
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

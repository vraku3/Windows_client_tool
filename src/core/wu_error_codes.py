"""Windows Update HRESULT decoder — ported from Update Center's Convert-UcWuError.

Known WU_E_* codes get a human explanation. For the 0x8007xxxx (Win32) facility
only, unknown codes fall back to ctypes.WinError()'s system message — that
fallback is NOT valid for other facilities (e.g. 0x8024xxxx WU-specific codes),
so it is intentionally restricted to 0x8007xxxx.
"""
from typing import Optional

WU_ERROR_MAP = {
    0x80240017: "the update is not applicable to this system (WU_E_NOT_APPLICABLE)",
    0x80240037: "unsupported operation (WU_E_NOT_SUPPORTED)",
    0x8024000B: "the operation was cancelled (WU_E_CALL_CANCELLED)",
    0x8024000E: "invalid XML received from server (WU_E_XML_INVALID)",
    0x80240022: "all updates failed to install (WU_E_ALL_UPDATES_FAILED)",
    0x80240034: "download failed (WU_E_DOWNLOAD_FAILED)",
    0x8024001E: "operation stopped — service or system is shutting down",
    0x8024002E: "Windows Update access is restricted by policy (WU_E_WU_DISABLED)",
    0x80240438: "the WU agent could not contact the service — check network/proxy",
    0x8024402C: "server name could not be resolved — DNS or proxy issue",
    0x80244022: "server returned 503 (temporarily unavailable) — try again later",
    0x8024200D: "an additional download is required (WU_E_UH_NEEDANOTHERDOWNLOAD)",
    0x80248007: "local WU datastore is missing data — try resetting WU components",
    0x8024500C: "the server rejected the request (WU_E_REDIRECTOR_LOAD_XML)",
    0x80D02002: "Delivery Optimization download timed out",
    0x800F0922: "installation failed in CBS — often a full System Reserved partition",
    0x800F081F: "source files could not be found (CBS_E_SOURCE_MISSING)",
    0x800F0820: "a servicing operation is pending; restart needed (CBS_E_PENDING)",
    0x800F0988: "delta compression mismatch (PSFX_E_INVALID_DELTA_COMBINATION)",
    0x800F0990: "reverse/forward delta application failed (PSFX_E_APPLY_REVERSE_FORWARD_DELTA_FAILED)",
    0x800F0991: "forward delta application failed — component store needs repair (PSFX_E_APPLY_FORWARD_DELTA_FAILED)",
    0x800F0992: "missing forward delta required for installation (PSFX_E_MISSING_FORWARD_DELTA)",
    0x800F0993: "reverse delta application failed during servicing rollback (PSFX_E_APPLY_REVERSE_DELTA_FAILED)",
    0x80073D02: "the package could not be installed because resources it modifies are in use (ERROR_PACKAGES_IN_USE)",
    0x80070005: "access denied — run as administrator",
    0x80070020: "file in use by another process (sharing violation)",
    0x80070570: "corrupt file — try DISM /RestoreHealth then sfc /scannow",
    0x800705B4: "timeout",
    0x80070643: "MSI/MSU installation error",
    0x8007000E: "not enough memory",
}


def decode_wu_error(hresult) -> str:
    """Return a human-readable explanation for a Windows Update HRESULT.

    Accepts either a signed or unsigned int. Returns 'success' for 0, a mapped
    explanation for known codes, a Win32Exception-derived message only for the
    0x8007xxxx facility, and the raw hex code otherwise.
    """
    try:
        hr = int(hresult)
    except (TypeError, ValueError):
        return ""
    if hr == 0:
        return "success"

    unsigned = hr & 0xFFFFFFFF
    hex_str = f"0x{unsigned:08X}"

    if unsigned in WU_ERROR_MAP:
        return f"{hex_str} - {WU_ERROR_MAP[unsigned]}"

    if (unsigned & 0xFFFF0000) == 0x80070000:
        msg = _win32_message(unsigned & 0xFFFF)
        if msg:
            return f"{hex_str} - {msg}"

    if (unsigned & 0xFFFF0000) == 0x80240000:
        return f"{hex_str} - unknown Windows Update code, look it up on learn.microsoft.com"

    return hex_str


#: DISPATCH_E_EXCEPTION. Means only "the callee raised" — the code worth
#: reading is the scode inside the EXCEPINFO the callee filled in.
DISPATCH_E_EXCEPTION = -2147352567


def hresult_from_com_error(exc) -> Optional[int]:
    """Dig the HRESULT that actually says something out of a com_error.

    A failed WU search arrives as

        (-2147352567, 'Exception occurred.',
         (0, None, None, None, 0, -2145124322), None)

    where the leading number is DISPATCH_E_EXCEPTION and the one that names
    the problem is the scode at the end of the EXCEPINFO tuple (here
    0x8024001E, "service is shutting down"). Reporting the first one tells
    nobody anything.

    Returns None for anything that is not a COM error, so callers can fall
    back to the exception's own text.
    """
    excepinfo = getattr(exc, "excepinfo", None)
    hr = getattr(exc, "hresult", None)
    if excepinfo is None and hr is None:
        return None
    if isinstance(excepinfo, (tuple, list)) and len(excepinfo) >= 6:
        scode = excepinfo[5]
        if isinstance(scode, int) and scode:
            return scode
    return hr if isinstance(hr, int) else None


def _win32_message(code: int) -> Optional[str]:
    try:
        import ctypes

        return ctypes.WinError(code).strerror
    except Exception:
        return None

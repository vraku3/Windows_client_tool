"""Error-code lookup for log lines — CMTrace's Error Lookup.

The reason people reach for CMTrace is rarely the colours: it is that a log
line says `0x80070005` and they need to know that means "access denied".

Sources, in order of specificity:

1. **Kernel NTSTATUS codes** (`0xC000xxxx`), such as 0xC0000005 (Access Violation)
   or 0xC0000142 (DLL Initialization Failed).
2. **Windows Installer (MSI) codes** (1603, 1618, 1601, 1619, 1620, etc.) both
   as decimal values and Win32 HRESULTs (`0x80070643`).
3. **Servicing & Windows Update tables**, covering CBS and WU HRESULTs.
4. **Windows itself**, for the Win32 facility (`0x8007xxxx`) and raw Win32 codes.
   The low sixteen bits are a Win32 error code and `FormatMessage` has a real
   sentence for every one of them.
5. **A ConfigMgr table** for the `0x87Dxxxxx` range, which Windows does not
   know and which is most of what an SCCM log actually contains.
6. **The app's existing `decode_wu_error`**, for additional WU codes.

Anything still unrecognised returns "" rather than a guess. A wrong
explanation of an error code is worse than none: it sends someone off after
the wrong problem entirely.

No Qt here, so the lookup is testable without a display.
"""
import ctypes
from dataclasses import dataclass
import logging
import re

logger = logging.getLogger(__name__)

#: Hex code pattern (0x followed by 1 to 8 hex digits)
_CODE = re.compile(r"\b0[xX]([0-9a-fA-F]{1,8})\b")

#: Matches either a hex code or a known MSI decimal error code.
#: Bare generic numbers like "id 12345" do not match.
_CODE_OR_MSI = re.compile(
    r"\b0[xX]([0-9a-fA-F]{1,8})\b"
    r"|\b(160[1-9]|161[0-9]|162[0-9]|163[0-9]|164[0-9]|1650)\b"
)

#: The Win32 facility. The low word is a plain Win32 error code.
_WIN32_FACILITY = 0x80070000

#: ConfigMgr's own range. Windows has no idea about these, and they are the
#: ones an SCCM log is full of.
CONFIGMGR_CODES = {
    0x87D00201: "content location request failed — no distribution point",
    0x87D00213: "the client is not assigned to a site",
    0x87D00215: "no matching software update was found",
    0x87D00231: "no distribution point found for the content",
    0x87D00244: "the deployment is not applicable to this machine",
    0x87D00269: "content download failed",
    0x87D00280: "a required policy has not arrived yet",
    0x87D00314: "the application is already installed",
    0x87D01106: "the program failed because a dependency failed",
    0x87D0128F: "waiting for a maintenance window",
    0x87D00668: "the software update failed to install",
    0x87D20B0C: "the task sequence step failed",
}

#: Common Kernel NTSTATUS codes (0xC000xxxx / 0xD000xxxx).
NTSTATUS_CODES = {
    0xC0000005: "access violation (STATUS_ACCESS_VIOLATION)",
    0xC0000006: "in-page I/O error reading executable or mapped file (STATUS_IN_PAGE_ERROR)",
    0xC0000008: "an invalid handle was specified (STATUS_INVALID_HANDLE)",
    0xC000000D: "an invalid parameter was passed to a service or function (STATUS_INVALID_PARAMETER)",
    0xC0000017: "not enough virtual memory available (STATUS_NO_MEMORY)",
    0xC000001D: "illegal instruction executed by CPU (STATUS_ILLEGAL_INSTRUCTION)",
    0xC0000022: "access denied by NT security manager (STATUS_ACCESS_DENIED)",
    0xC0000034: "object name not found (STATUS_OBJECT_NAME_NOT_FOUND)",
    0xC000009A: "insufficient system resources to complete API (STATUS_INSUFFICIENT_RESOURCES)",
    0xC0000135: "required dependent DLL was not found (STATUS_DLL_NOT_FOUND)",
    0xC0000139: "specified DLL entrypoint was not found (STATUS_ENTRYPOINT_NOT_FOUND)",
    0xC0000142: "DLL initialization failed (STATUS_DLL_INIT_FAILED)",
    0xC000021A: "subsystem process terminated unexpectedly (STATUS_SYSTEM_PROCESS_TERMINATED)",
    0xC0000225: "boot configuration or file not found (STATUS_NOT_FOUND / BCD)",
    0xC0000409: "stack buffer overrun detected by /GS check (STATUS_STACK_BUFFER_OVERRUN)",
    0xC0000428: "Windows cannot verify the digital signature for this file (STATUS_INVALID_IMAGE_HASH)",
}

#: Common Windows Installer (MSI) error codes.
MSI_CODES = {
    1601: "the Windows Installer service could not be accessed (ERROR_INSTALL_SERVICE_FAILURE)",
    1602: "installation cancelled by user (ERROR_INSTALL_USEREXIT)",
    1603: "fatal error during installation (ERROR_INSTALL_FAILURE)",
    1605: "this action is only valid for products currently installed (ERROR_UNKNOWN_PRODUCT)",
    1612: "the installation source for this product is not available (ERROR_INSTALL_SOURCE_ABSENT)",
    1618: "another installation is already in progress (ERROR_INSTALL_ALREADY_RUNNING)",
    1619: "this installation package could not be opened (ERROR_INSTALL_PACKAGE_OPEN_FAILED)",
    1620: "this installation package could not be opened — invalid package (ERROR_INSTALL_PACKAGE_INVALID)",
    1622: "error opening installation log file (ERROR_INSTALL_LOG_FAILURE)",
    1625: "installation forbidden by system policy (ERROR_INSTALL_PACKAGE_REJECTED)",
    1632: "the Temp folder is full or inaccessible (ERROR_INSTALL_TEMP_UNWRITABLE)",
    1633: "this installation package is not supported on this platform (ERROR_INSTALL_PLATFORM_UNSUPPORTED)",
    1638: "another version of this product is already installed (ERROR_PRODUCT_VERSION)",
    1642: "the installer cannot install the upgrade patch (ERROR_PATCH_PACKAGE_UNSUPPORTED)",
}

#: Curated CBS & Servicing HRESULTs.
CBS_CODES = {
    0x800F081F: "source files could not be found (CBS_E_SOURCE_MISSING)",
    0x800F0906: "the source files could not be downloaded (CBS_E_DOWNLOAD_FAILURE)",
    0x800F0805: "the specified package is not a valid Windows package (CBS_E_INVALID_PACKAGE)",
    0x800F0825: "package cannot be uninstalled (CBS_E_CANNOT_UNINSTALL)",
    0x800F0831: "component store corruption detected (CBS_E_STORE_CORRUPTION)",
    0x80240020: "automatic updates cannot proceed without user interaction (WU_E_NO_INTERACTIVE_USER)",
    0x80244019: "the update server returned HTTP 404 (Not Found) (WU_E_PT_HTTP_STATUS_NOT_FOUND)",
    0x80248007: "local WU datastore is missing data — try resetting WU components",
}


def find_codes(text: str) -> list:
    """Every distinct error code in `text`, in the order it appears."""
    seen = []
    for _start, _end, code in code_spans(text):
        if code not in seen:
            seen.append(code)
    return seen


def code_spans(text: str) -> list:
    """`(start, end, code)` for every code occurrence, in order.

    `find_codes` answers "which codes are here" and de-duplicates. The
    delegate asks "where are they" and must colour both occurrences of a
    code that appears twice, so this one does not.
    """
    spans = []
    for match in _CODE_OR_MSI.finditer(text or ""):
        if match.group(1) is not None:
            code = int(match.group(1), 16)
        else:
            code = int(match.group(2), 10)
        spans.append((match.start(), match.end(), code))
    return spans


@dataclass(frozen=True)
class Advice:
    """What a code usually means, and what to do about it."""
    cause: str
    remedy: str
    reference: str


#: Codes whose cause is documented and whose remedy is something a person can
#: actually run and then check.
CODE_ADVICE = {
    # ── Windows Update & CBS ──────────────────────────────────────────
    0x80240020: Advice(
        cause="Automatic Updates could not proceed because user interaction or a required policy "
              "was missing. Common during feature updates when a background task attempts an interactive "
              "operation without an active desktop session.",
        remedy="Restart the system and run Windows Update interactively from Settings > Windows Update, "
               "or run 'usoclient StartInteractiveScan'.",
        reference="WU_E_NO_INTERACTIVE_USER / WU_E_INSTALL_NOT_ALLOWED (0x80240020)."),

    0x80244019: Advice(
        cause="The update server (WSUS or Windows Update) returned HTTP 404 (Not Found) for a requested "
              "metadata or update payload file.",
        remedy="Verify WSUS synchronization, run 'wsusutil reset' on the WSUS server, or check proxy/firewall "
               "whitelisting for Microsoft Update endpoints.",
        reference="WU_E_PT_HTTP_STATUS_NOT_FOUND (0x80244019 / HTTP 404)."),

    0x800F081F: Advice(
        cause="The component store has no source for the files the update needs -- usually the payload "
              "was cleaned up or the machine cannot reach Windows Update.",
        remedy="DISM /Online /Cleanup-Image /RestoreHealth, adding /Source:<path> and /LimitAccess if "
               "the machine is offline.",
        reference="CBS_E_SOURCE_MISSING; Microsoft's servicing repair guidance."),

    0x800F0906: Advice(
        cause="The source files could not be downloaded because the component store could not contact "
              "Windows Update, WSUS does not host Feature-on-Demand files, or network connection failed.",
        remedy="DISM /Online /Enable-Feature /FeatureName:... /Source:<path> /LimitAccess, or configure the "
               "'Specify settings for optional component installation' Group Policy.",
        reference="CBS_E_DOWNLOAD_FAILURE (0x800F0906)."),

    0x80070002: Advice(
        cause="The system cannot find the file specified. In servicing or update operations, a required "
              "manifest, staging file, or SoftwareDistribution database entry is missing or deleted.",
        remedy="Stop Windows Update services ('net stop wuauserv', 'net stop bits'), clear "
               "'%windir%\\SoftwareDistribution\\Download', restart services, and run 'sfc /scannow'.",
        reference="ERROR_FILE_NOT_FOUND (Win32 2 / 0x80070002)."),

    0x80070005: Advice(
        cause="Access is denied. Servicing or installation was blocked by file/registry permissions, "
              "security software interference, or insufficient elevation.",
        remedy="Run the command from an elevated Administrator prompt; temporarily disable third-party antivirus, "
               "and check permissions on '%windir%\\System32\\catroot2' and '%windir%\\SoftwareDistribution'.",
        reference="ERROR_ACCESS_DENIED (Win32 5 / 0x80070005)."),

    0x80070422: Advice(
        cause="The service cannot be started, either because it is disabled or because it has no enabled devices "
              "associated with it. The Windows Update (wuauserv), BITS, or TrustedInstaller service is disabled.",
        remedy="Enable and start required services: run 'sc config wuauserv start= auto' and 'net start wuauserv'; "
               "verify BITS and CryptSvc are running.",
        reference="ERROR_SERVICE_DISABLED (Win32 1058 / 0x80070422)."),

    0x80073712: Advice(
        cause="The component store has been corrupted. Component-Based Servicing (CBS) manifests or payload "
              "catalogs are corrupt or missing in WinSxS.",
        remedy="Run 'DISM /Online /Cleanup-Image /RestoreHealth' followed by 'sfc /scannow'. If repair fails, "
               "supply a clean install.wim via /Source:WIM:<path>\\install.wim:1 /LimitAccess.",
        reference="ERROR_SXS_COMPONENT_STORE_CORRUPT (Win32 14098 / 0x80073712)."),

    0x80248007: Advice(
        cause="The local Windows Update datastore is missing required update records or contains corrupted cache "
              "entries in DataStore.edb.",
        remedy="Stop the Windows Update service ('net stop wuauserv'), rename or delete "
               "'%windir%\\SoftwareDistribution\\DataStore', restart the service ('net start wuauserv'), and check for updates.",
        reference="WU_E_DS_DATANOTFOUND (0x80248007)."),

    0x80070020: Advice(
        cause="The process cannot access the file because it is being used by another process. An active process "
              "or antivirus scanner has an exclusive lock on an update binary, log, or database file.",
        remedy="Identify locking processes with WinClientTool File Forensics or handle.exe, exclude update folders "
               "from real-time AV scanning, or reboot to release file locks.",
        reference="ERROR_SHARING_VIOLATION (Win32 32 / 0x80070020)."),

    0x80073701: Advice(
        cause="A referenced assembly is missing from the component store, so servicing cannot complete "
              "the operation.",
        remedy="DISM /Online /Cleanup-Image /RestoreHealth, then sfc /scannow to confirm the store is consistent.",
        reference="ERROR_SXS_ASSEMBLY_MISSING."),

    0x800F0805: Advice(
        cause="The package handed to servicing is not a valid package -- commonly a damaged or partly "
              "downloaded update.",
        remedy="Remove the cached update and let Windows Update download it again; DISM /Online /Cleanup-Image "
               "/RestoreHealth if the store itself is suspect.",
        reference="CBS_E_INVALID_PACKAGE."),

    0x80070490: Advice(
        cause="An element servicing looked for is not present in the store. It accompanies a store that is "
              "missing or has damaged manifests rather than being a fault in the update itself.",
        remedy="DISM /Online /Cleanup-Image /RestoreHealth, then sfc /scannow.",
        reference="ERROR_NOT_FOUND."),

    0x80004005: Advice(
        cause="An unspecified failure. It is a GENERIC code and carries no detail of its own, so the useful "
              "information is in the lines around it, not in the number.",
        remedy="Read the records immediately before this one -- the Summary panel's first-error row is usually "
               "the specific failure this is reporting.",
        reference="E_FAIL. Deliberately offers no repair command: doing so would be inventing a fix for an "
                  "error that does not say what went wrong."),

    # ── Kernel NTSTATUS ───────────────────────────────────────────────
    0xC0000005: Advice(
        cause="Memory access violation — the thread attempted to read or write to an invalid virtual address, "
              "null pointer dereference, DEP violation, or corrupted memory.",
        remedy="Inspect crash dump in WinClientTool or WinDbg to find the faulting module/DLL. Run 'sfc /scannow' "
               "and Windows Memory Diagnostic (mdsched.exe) to rule out memory or file corruption.",
        reference="STATUS_ACCESS_VIOLATION (0xC0000005)."),

    0xC0000034: Advice(
        cause="Object name not found. A required system file, device, BCD boot entry, or registry hive could "
              "not be located during boot or execution.",
        remedy="For boot failures, rebuild the BCD store using 'bcdboot %SystemRoot% /s S: /f UEFI' or "
               "'bootrec /rebuildbcd'. For application crashes, check missing dependencies or file paths.",
        reference="STATUS_OBJECT_NAME_NOT_FOUND (0xC0000034)."),

    0xC0000142: Advice(
        cause="DLL initialization failed. Typically caused by missing Visual C++ Redistributable runtime, "
              "corrupted system DLLs, desktop heap exhaustion, or AppInit_DLLs injection.",
        remedy="Install or repair Microsoft Visual C++ Redistributables (both x86 and x64), run 'sfc /scannow', "
               "and check for conflicting third-party DLL hooks in 'AppInit_DLLs' registry keys.",
        reference="STATUS_DLL_INIT_FAILED (0xC0000142)."),

    0xC000021A: Advice(
        cause="A critical user-mode subsystem process (such as winlogon.exe or csrss.exe) terminated unexpectedly, "
              "compromising system security and triggering a bugcheck.",
        remedy="Boot into Windows Recovery Environment (WinRE); run 'DISM /Image:%SystemDrive%\\ /Cleanup-Image /RestoreHealth', "
               "'sfc /scannow /offbootdir=%SystemDrive%\\ /offwindir=%SystemRoot%', and roll back recently installed drivers or updates.",
        reference="STATUS_SYSTEM_PROCESS_TERMINATED / BugCheck 0xC000021A."),

    0xC0000022: Advice(
        cause="An NT security manager access check failed. The process lacks the required security token privileges, "
              "DACL rights, or integrity level to access the object.",
        remedy="Verify the application is running with administrative privileges. Check file and registry ACLs, "
               "and use Process Monitor (ProcMon) to identify the specific object returning ACCESS DENIED.",
        reference="STATUS_ACCESS_DENIED (0xC0000022)."),

    0xC000001D: Advice(
        cause="The CPU encountered an invalid or unsupported instruction opcode. Typically occurs when software "
              "requires CPU instruction sets (AVX, AVX2, SSE4.2, POPCNT) not supported by the processor or hypervisor.",
        remedy="Verify the CPU meets the application's hardware requirements (e.g. Windows 11 24H2 requires "
               "SSE4.2 and POPCNT). If running in a VM, enable instruction set passthrough in VM CPU settings.",
        reference="STATUS_ILLEGAL_INSTRUCTION (0xC000001D)."),

    # ── Windows Installer (MSI) ───────────────────────────────────────
    1603: Advice(
        cause="Fatal error during installation (ERROR_INSTALL_FAILURE). A custom action returned failure, "
              "the destination folder was locked/encrypted, SYSTEM lacks permissions, or a pending reboot blocked installation.",
        remedy="Run the installer with verbose logging: 'msiexec /i package.msi /l*v install.log' and search for "
               "'Return value 3'. Grant SYSTEM Full Control on target folders, reboot, and retry.",
        reference="ERROR_INSTALL_FAILURE (MSI 1603 / 0x80070643)."),

    0x80070643: Advice(
        cause="Fatal error during installation (ERROR_INSTALL_FAILURE). A custom action returned failure, "
              "the destination folder was locked/encrypted, SYSTEM lacks permissions, or a pending reboot blocked installation.",
        remedy="Run the installer with verbose logging: 'msiexec /i package.msi /l*v install.log' and search for "
               "'Return value 3'. Grant SYSTEM Full Control on target folders, reboot, and retry.",
        reference="ERROR_INSTALL_FAILURE (MSI 1603 / 0x80070643)."),

    1618: Advice(
        cause="Another installation is already in progress (ERROR_INSTALL_ALREADY_RUNNING). Another msiexec.exe "
              "or Windows Update transaction holds the global _MSIExecute mutex.",
        remedy="Wait for background updates/installations to finish, or terminate stalled installer instances "
               "via 'taskkill /F /IM msiexec.exe' and retry.",
        reference="ERROR_INSTALL_ALREADY_RUNNING (MSI 1618 / 0x80070652)."),

    0x80070652: Advice(
        cause="Another installation is already in progress (ERROR_INSTALL_ALREADY_RUNNING). Another msiexec.exe "
              "or Windows Update transaction holds the global _MSIExecute mutex.",
        remedy="Wait for background updates/installations to finish, or terminate stalled installer instances "
               "via 'taskkill /F /IM msiexec.exe' and retry.",
        reference="ERROR_INSTALL_ALREADY_RUNNING (MSI 1618 / 0x80070652)."),

    1601: Advice(
        cause="The Windows Installer service could not be accessed (ERROR_INSTALL_SERVICE_FAILURE). "
              "The msiserver service is disabled, unregistered, or running in Safe Mode without MSI support.",
        remedy="Re-register the Windows Installer service: run 'msiexec /unregister' followed by 'msiexec /regserver', "
               "and configure startup: 'sc config msiserver start= demand'.",
        reference="ERROR_INSTALL_SERVICE_FAILURE (MSI 1601 / 0x80070641)."),

    0x80070641: Advice(
        cause="The Windows Installer service could not be accessed (ERROR_INSTALL_SERVICE_FAILURE). "
              "The msiserver service is disabled, unregistered, or running in Safe Mode without MSI support.",
        remedy="Re-register the Windows Installer service: run 'msiexec /unregister' followed by 'msiexec /regserver', "
               "and configure startup: 'sc config msiserver start= demand'.",
        reference="ERROR_INSTALL_SERVICE_FAILURE (MSI 1601 / 0x80070641)."),

    1619: Advice(
        cause="This installation package could not be opened (ERROR_INSTALL_PACKAGE_OPEN_FAILED). "
              "The MSI/MSP file does not exist at the specified path, is locked, or lacks NTFS read permissions.",
        remedy="Verify the MSI path exists and is locally accessible (copy from network share to local disk). "
               "Check file permissions and right-click Properties to Unblock if downloaded from internet.",
        reference="ERROR_INSTALL_PACKAGE_OPEN_FAILED (MSI 1619 / 0x80070653)."),

    0x80070653: Advice(
        cause="This installation package could not be opened (ERROR_INSTALL_PACKAGE_OPEN_FAILED). "
              "The MSI/MSP file does not exist at the specified path, is locked, or lacks NTFS read permissions.",
        remedy="Verify the MSI path exists and is locally accessible (copy from network share to local disk). "
               "Check file permissions and right-click Properties to Unblock if downloaded from internet.",
        reference="ERROR_INSTALL_PACKAGE_OPEN_FAILED (MSI 1619 / 0x80070653)."),

    1620: Advice(
        cause="This installation package is invalid or corrupt (ERROR_INSTALL_PACKAGE_INVALID). "
              "The file is not a valid Windows Installer package (partial download, zero bytes, HTML error page saved as .msi).",
        remedy="Re-download the installer package from the vendor; check file size and SHA256 checksum; "
               "test package integrity with 'msiexec /a package.msi /qn'.",
        reference="ERROR_INSTALL_PACKAGE_INVALID (MSI 1620 / 0x80070654)."),

    0x80070654: Advice(
        cause="This installation package is invalid or corrupt (ERROR_INSTALL_PACKAGE_INVALID). "
              "The file is not a valid Windows Installer package (partial download, zero bytes, HTML error page saved as .msi).",
        remedy="Re-download the installer package from the vendor; check file size and SHA256 checksum; "
               "test package integrity with 'msiexec /a package.msi /qn'.",
        reference="ERROR_INSTALL_PACKAGE_INVALID (MSI 1620 / 0x80070654)."),
}


def advice(code: int):
    """What is documented about `code`, or None.

    None is the common answer and the right one: an explanation of the wrong
    error sends someone after a problem they do not have.
    """
    if code is None:
        return None
    try:
        val = int(code)
    except (ValueError, TypeError):
        return None

    unsigned = val & 0xFFFFFFFF
    if unsigned in CODE_ADVICE:
        return CODE_ADVICE[unsigned]

    # Map Win32 facility 0x8007xxxx <-> low 16-bit code
    if (unsigned & 0xFFFF0000) == _WIN32_FACILITY:
        low = unsigned & 0xFFFF
        if low in CODE_ADVICE:
            return CODE_ADVICE[low]
    elif (_WIN32_FACILITY | unsigned) in CODE_ADVICE:
        return CODE_ADVICE[_WIN32_FACILITY | unsigned]

    return None


def describe(code: int) -> str:
    """A sentence for `code`, or "" when nothing reliable is known.

    Silence beats a guess: an explanation of the wrong error sends someone
    after a problem they do not have.
    """
    if code is None:
        return ""
    try:
        val = int(code)
    except (ValueError, TypeError):
        return ""

    if val == 0:
        return "success"

    unsigned = val & 0xFFFFFFFF

    # 1. ConfigMgr table (0x87Dxxxxx)
    if unsigned in CONFIGMGR_CODES:
        return CONFIGMGR_CODES[unsigned]

    # 2. Kernel NTSTATUS table (0xC000xxxx / 0xD000xxxx)
    if unsigned in NTSTATUS_CODES:
        return NTSTATUS_CODES[unsigned]

    # 3. MSI table (both decimal and HRESULT / low-word hex)
    if unsigned in MSI_CODES:
        return MSI_CODES[unsigned]
    if (unsigned & 0xFFFF0000) == _WIN32_FACILITY:
        low = unsigned & 0xFFFF
        if low in MSI_CODES:
            return MSI_CODES[low]

    # 4. Explicit CBS / Windows Update overrides
    if unsigned in CBS_CODES:
        return CBS_CODES[unsigned]

    # 5. Win32 facility (0x8007xxxx) — low 16-bits via FormatError
    if (unsigned & 0xFFFF0000) == _WIN32_FACILITY:
        try:
            message = ctypes.FormatError(unsigned & 0xFFFF).strip()
        except Exception:                           # noqa: BLE001
            message = ""
        if message and not message.lower().startswith("unknown error"):
            return message

    # Bare Win32 error code (1 .. 65535)
    if 0 < unsigned <= 0xFFFF:
        try:
            message = ctypes.FormatError(unsigned).strip()
        except Exception:                           # noqa: BLE001
            message = ""
        if message and not message.lower().startswith("unknown error"):
            return message

    # 6. Windows Update decoder
    try:
        from core.wu_error_codes import decode_wu_error

        decoded = decode_wu_error(unsigned)
    except Exception:                               # noqa: BLE001
        return ""

    if decoded and " - " in decoded:
        part = decoded.split(" - ", 1)[1]
        if not part.lower().startswith("unknown"):
            return part
    if decoded and decoded.lower() == "success":
        return "success"

    return ""


def explain(text: str) -> list:
    """(code_text, meaning) for every FAILING code in `text`.

    Success codes are deliberately left out. Measured on a real 85,850-line
    CBS.log: 4,553 lines carry a hex code and 4,427 of them -- 97% -- carry
    nothing but 0x00000000. Reporting those would put "success" on screen
    four thousand times to surface the nine lines that actually failed.
    """
    out = []
    raw = text or ""
    for code in find_codes(raw):
        if not _is_failure(code):
            continue
        meaning = describe(code)
        if meaning:
            is_hex = bool(re.search(rf"\b0[xX]0*{code:x}\b", raw, re.IGNORECASE))
            if code in MSI_CODES and str(code) in raw and not is_hex:
                out.append((str(code), meaning))
            else:
                out.append((f"0x{code:08X}", meaning))
    return out


#: Phrases that mean the component store is damaged, as Windows actually
#: writes them.
_CORRUPTION = re.compile(
    r"STATUS_SXS_\w+"
    r"|cannot repair"
    r"|store corruption"
    r"|do not match",
    re.IGNORECASE)


def corruption_spans(text: str) -> list:
    """`(start, end, label)` for every damage marker in `text`.

    Ordered and non-overlapping, so the delegate can lay them alongside the
    error-code spans without nesting tags. The label is the matched phrase
    lowercased, which is what names the finding.
    """
    return [(match.start(), match.end(), match.group(0).lower())
            for match in _CORRUPTION.finditer(text or "")]


def _is_failure(code: int) -> bool:
    """An HRESULT or NTSTATUS fails when its sign bit is set; 0 is S_OK.
    Known installer (MSI) error codes are non-zero failures."""
    if code is None:
        return False
    try:
        val = int(code)
    except (ValueError, TypeError):
        return False
    if val == 0:
        return False
    if val in MSI_CODES:
        return True
    return bool(val & 0xFFFFFFFF & 0x80000000)


def annotate(text: str) -> str:
    """`text` with an explanation appended, or unchanged if nothing is known.

    Used for the tooltip: the line as written, then what its codes mean.
    """
    found = explain(text)
    if not found:
        return text
    lines = [text, ""]
    lines.extend(f"{code}  —  {meaning}" for code, meaning in found)
    return "\n".join(lines)

"""What an AppX deployment HRESULT actually means, in words an admin can act on.

Qt-free so the uninstall path and the deployment-failure reader share it.

Measured on this machine (2026-10-09), from
`Microsoft-Windows-AppXDeploymentServer/Operational` event 404 over a week:
0x80073D02 x8 (CrossDevice, WhatsApp, Codex, OfficeHub, ScreenSketch, Teams --
every one "Unable to install because the following apps need to be closed"),
0x80073CF9 x7 (one package, "currently paused", once a day), 0x80073CF8 x7
(Windows Update staging sessions, no package named), 0x80070005 x2.

The hint this tab used to give for an uninstall failure keyed on 0x80073CFB as
the "app is running" code. It is not: 0x80073CFB is ERROR_PACKAGE_ALREADY_EXISTS.
The in-use code is 0x80073D02, and its message ("need to be closed") matched
none of the old text markers either -- so the one failure this machine actually
produces never got the hint.

Names and meanings are the documented `winerror.h` ERROR_INSTALL_* /
ERROR_PACKAGE_* values (the 0x80073CF0..0x80073D0A block).
"""
import re
from typing import Optional, Tuple

#: HRESULT -> (symbolic name, what it means / what to do).
_HRESULTS = {
    0x80073CF0: ("ERROR_INSTALL_OPEN_PACKAGE_FAILED",
                 "The package file could not be opened."),
    0x80073CF1: ("ERROR_INSTALL_PACKAGE_NOT_FOUND",
                 "The package was not found."),
    0x80073CF2: ("ERROR_INSTALL_INVALID_PACKAGE",
                 "The package data is invalid."),
    0x80073CF3: ("ERROR_INSTALL_RESOLVE_DEPENDENCY_FAILED",
                 "Update, dependency or conflict validation failed -- a "
                 "framework it needs is missing or a newer version is installed."),
    0x80073CF4: ("ERROR_INSTALL_OUT_OF_DISK_SPACE",
                 "Not enough disk space on the target volume."),
    0x80073CF5: ("ERROR_INSTALL_NETWORK_FAILURE",
                 "The package could not be downloaded."),
    0x80073CF6: ("ERROR_INSTALL_REGISTRATION_FAILURE",
                 "The package could not be registered."),
    0x80073CF7: ("ERROR_INSTALL_DEREGISTRATION_FAILURE",
                 "The package could not be unregistered."),
    0x80073CF8: ("ERROR_INSTALL_CANCEL",
                 "The operation was cancelled before it finished."),
    0x80073CF9: ("ERROR_INSTALL_FAILED",
                 "Install failed -- the event's own text says why (on this "
                 "machine: the package is paused and must be staged first)."),
    0x80073CFA: ("ERROR_REMOVE_FAILED",
                 "Removal failed. Windows marks some packages NonRemovable; "
                 "those always refuse."),
    0x80073CFB: ("ERROR_PACKAGE_ALREADY_EXISTS",
                 "A package with this identity is already installed with "
                 "different contents -- remove it before reinstalling."),
    0x80073CFC: ("ERROR_NEEDS_REMEDIATION",
                 "The app cannot be started; try repairing it."),
    0x80073CFD: ("ERROR_INSTALL_PREREQUISITE_FAILED",
                 "A specific install prerequisite could not be satisfied."),
    0x80073CFE: ("ERROR_PACKAGE_REPOSITORY_CORRUPTED",
                 "The package repository is corrupted."),
    0x80073CFF: ("ERROR_INSTALL_POLICY_FAILURE",
                 "Blocked by policy -- sideloading/developer mode or an "
                 "AppLocker/WDAC rule."),
    0x80073D00: ("ERROR_PACKAGE_UPDATING",
                 "The app is being updated right now."),
    0x80073D01: ("ERROR_DEPLOYMENT_BLOCKED_BY_POLICY",
                 "Deployment is blocked by policy."),
    0x80073D02: ("ERROR_PACKAGES_IN_USE",
                 "The app was running, so Windows could not replace or remove "
                 "it. Close the app (check the tray too); a pending update "
                 "retries on its own."),
    0x80073D03: ("ERROR_RECOVERY_FILE_CORRUPT",
                 "The recovery file is corrupt."),
    0x80073D04: ("ERROR_INVALID_STAGED_SIGNATURE",
                 "The staged package's signature is no longer valid."),
    0x80073D05: ("ERROR_DELETING_EXISTING_APPLICATIONDATA_STORE_FAILED",
                 "The app's existing data store could not be deleted -- "
                 "usually because the app is still running."),
    0x80073D06: ("ERROR_INSTALL_PACKAGE_DOWNGRADE",
                 "A newer version is already installed (downgrade refused)."),
    0x80073D07: ("ERROR_SYSTEM_NEEDS_REMEDIATION",
                 "A system component needs repair."),
    0x80073D08: ("ERROR_APPX_INTEGRITY_FAILURE_CLR_NGEN",
                 "A .NET native image failed integrity validation."),
    0x80073D09: ("ERROR_RESILIENCY_FILE_CORRUPT",
                 "The resiliency file is corrupt."),
    0x80073D0A: ("ERROR_INSTALL_FIREWALL_SERVICE_NOT_RUNNING",
                 "The Windows Firewall service is not running, and the "
                 "package declares firewall rules."),
    0x80070005: ("E_ACCESSDENIED",
                 "Access denied -- the operation needed rights this caller "
                 "did not have."),
}

_HRESULT_RE = re.compile(r"0x[0-9a-fA-F]{8}")

#: The in-use HRESULT. Exposed so callers can test for it by value.
PACKAGES_IN_USE = 0x80073D02


def parse_hresult(value: str) -> Optional[int]:
    """`'0x80073d02'` -> 0x80073D02; None when it is not a hex HRESULT."""
    if not value:
        return None
    match = _HRESULT_RE.search(value)
    if not match:
        return None
    return int(match.group(0), 16)


def describe(code: Optional[int]) -> Tuple[str, str]:
    """(symbolic name, meaning). Unknown codes are named, never guessed."""
    if code is None:
        return ("", "No error code was recorded.")
    if code in _HRESULTS:
        return _HRESULTS[code]
    return ("", f"0x{code:08X} is not an AppX deployment code this tool "
                "knows; the event text is the authority.")


def format_code(code: Optional[int]) -> str:
    return f"0x{code:08X}" if code is not None else ""


def first_hresult(output: str) -> Optional[int]:
    """The first known deployment HRESULT in command output, else the first
    HRESULT-shaped token, else None."""
    codes = [int(m, 16) for m in _HRESULT_RE.findall(output or "")]
    for code in codes:
        if code in _HRESULTS:
            return code
    return codes[0] if codes else None

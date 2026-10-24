"""Fresh-process shell verification and account boundary checks."""
import ctypes
import json
import os
import subprocess
import uuid

from core.shell_launch import desktop_account
from core.system_paths import powershell_exe
from .model import Verification, VerifyResult
from .onedrive import normal


def write_refusal():
    import win32api
    import win32con
    import win32security
    try:
        # PyHANDLE is not a context manager (pywin32): close it explicitly.
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        user, domain, _ = win32security.LookupAccountSid(None, sid)
        current = domain + "\\" + user
        desktop = desktop_account()
        if not desktop:
            return "Could not identify the signed-in desktop account. Changes are refused."
        if current.casefold() != desktop.casefold():
            return f"Changes refused: process account {current} differs from desktop account {desktop}."
    except Exception as error:
        return f"Could not verify the process and desktop accounts: {error}"
    return ""


def refresh_shell():
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)


def verify(entry):
    guid = "{" + str(uuid.UUID(entry.guid.strip("{}"))).upper() + "}"
    # User input travels only through the child environment, never executable text.
    script = """$Guid = $env:WCT_NP_GUID
$ExpectedName = $env:WCT_NP_NAME
$ExpectedPath = $env:WCT_NP_PATH
$ResolvedPath = $env:WCT_NP_RESOLVED
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
 $shell = New-Object -ComObject Shell.Application
 $own = $shell.Namespace('::' + $Guid)
 if ($null -eq $own) { @{registered=$false} | ConvertTo-Json -Compress; exit 0 }
 $name = $own.Self.Name
 $path = $own.Self.Path
 $desks = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('CommonDesktopDirectory')) | Where-Object { $_ }
 $listed = @($shell.Namespace(0).Items() | Where-Object { $_.Name -eq $name -and $_.Path -eq $path -and ($desks -notcontains [System.IO.Path]::GetDirectoryName($_.Path)) }).Count -gt 0
 @{registered=$true; listed=$listed; name=$name; path=$path} | ConvertTo-Json -Compress
} catch { Write-Error $_; exit 1 }

"""
    environment = dict(os.environ, WCT_NP_GUID=guid, WCT_NP_NAME=entry.name,
                       WCT_NP_PATH=entry.target, WCT_NP_RESOLVED=entry.resolved_target)
    try:
        result = subprocess.run([powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", "-"],
                                input=script, env=environment, capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            return VerifyResult(Verification.COULD_NOT_CHECK, result.stderr.strip() or "Shell query failed.")
        return verdict(entry, json.loads(result.stdout.lstrip("\ufeff")))
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return VerifyResult(Verification.COULD_NOT_CHECK, str(error))


def verdict(entry, data):
    # Identity comes from opening the object by its CLSID (Shell.Namespace('::{GUID}')).
    # A Desktop item's Path is the TARGET folder, never the GUID (measured 2026-10-23 on
    # "Aplicații"), so matching items by GUID can never succeed and matching by name/path
    # alone can be fooled by an ordinary folder or another entry with the same name.
    if not data.get("registered"):
        return VerifyResult(Verification.NOT_LISTED,
                            f"A fresh shell process has no object {entry.guid}. Reopen Explorer; sign-out may be needed.")
    if data.get("name") != entry.name:
        return VerifyResult(Verification.WRONG_NAME, f"Shell returned name: {data.get('name')!r}")
    path = data.get("path", "")
    if not path or normal(path) != normal(entry.resolved_target or entry.target):
        return VerifyResult(Verification.COULD_NOT_CHECK, f"Name matched, but shell target could not be confirmed: {path}")
    if not data.get("listed"):
        return VerifyResult(Verification.NOT_LISTED,
                            "The shell object is registered with the right name and folder, but the Desktop tree does not "
                            "show it yet. Reopen Explorer; sign-out may be needed.")
    return VerifyResult(Verification.VERIFIED,
                        "Fresh shell process opened the object by its GUID (right name and folder) and the Desktop tree lists it.")


def restart_explorer():
    reason = write_refusal()
    if reason:
        raise PermissionError(reason)
    from modules.game_mode.engine.system import WinSystem
    session = ctypes.c_uint32()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise ctypes.WinError()
    error = WinSystem().restart_explorer(session.value)
    if error:
        raise OSError(error)
    return "Explorer restarted as the signed-in user."

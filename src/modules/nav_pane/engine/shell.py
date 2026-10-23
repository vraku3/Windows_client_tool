"""Fresh-process shell verification and account boundary checks."""
import ctypes
import json
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

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
    # A script file avoids command-line quoting and console-codepage corruption.
    script = """param([string]$Guid, [string]$ExpectedName, [string]$ExpectedPath, [string]$ResolvedPath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
 $shell = New-Object -ComObject Shell.Application
 $all = @($shell.Namespace(0).Items())
 $byId = @($all | Where-Object { $_.Path -like ('*' + $Guid + '*') })
 $candidates = @(if ($byId.Count) { $byId } else {
   $all | Where-Object { $_.Name -eq $ExpectedName -or $_.Path -eq $ExpectedPath -or $_.Path -eq $ResolvedPath }
 })
 if (-not $candidates.Count) { @{listed=$false} | ConvertTo-Json -Compress; exit 0 }
 $item = $candidates | Where-Object { $_.Path -eq $ExpectedPath -or $_.Path -eq $ResolvedPath } | Select-Object -First 1
 if ($null -eq $item) { $item = $candidates | Select-Object -First 1 }
 $folder = $item.GetFolder
 $paths = @($item.Path, $folder.Self.Path, $item.ExtendedProperty('System.Link.TargetParsingPath'))
 $path = $paths | Where-Object { $_ -and [System.IO.Directory]::Exists($_) } | Select-Object -First 1
 @{listed=$true; name=$item.Name; path=$path} | ConvertTo-Json -Compress
} catch { Write-Error $_; exit 1 }
"""
    filename = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ps1", mode="w", encoding="utf-8-sig", delete=False) as stream:
            stream.write(script)
            filename = stream.name
        result = subprocess.run([powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", filename, guid,
                                 entry.name, entry.target, entry.resolved_target],
                                capture_output=True, encoding="utf-8", errors="replace", timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            return VerifyResult(Verification.COULD_NOT_CHECK, result.stderr.strip() or "Shell query failed.")
        return verdict(entry, json.loads(result.stdout.lstrip("\ufeff")))
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return VerifyResult(Verification.COULD_NOT_CHECK, str(error))
    finally:
        if filename:
            Path(filename).unlink(missing_ok=True)


def verdict(entry, data):
    if not data.get("listed"):
        return VerifyResult(Verification.NOT_LISTED, "Not listed in a fresh shell process. Reopen Explorer; sign-out may be needed.")
    if data.get("name") != entry.name:
        return VerifyResult(Verification.WRONG_NAME, f"Shell returned name: {data.get('name')!r}")
    path = data.get("path", "")
    if not path or normal(path) != normal(entry.resolved_target or entry.target):
        return VerifyResult(Verification.COULD_NOT_CHECK, f"Name matched, but shell target could not be confirmed: {path}")
    return VerifyResult(Verification.VERIFIED, "Fresh shell process confirmed the name and folder path.")


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

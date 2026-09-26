"""Is this file signed -- counting Windows' catalog signatures too.

`core.procengine.signatures.verify_signature` asks WinVerifyTrust about the
file's EMBEDDED signature only. Most of Windows itself (cmd.exe, notepad.exe,
rundll32.exe, SecurityHealthSystray.exe...) has no embedded signature: it is
signed through a security catalog (.cat) that lists the file's hash. Measured
on this machine, the embedded-only check calls all of those "not signed",
which for a persistence review is a false claim about the most trusted files
on the box. So this module asks the catalog question when the embedded one
says "no signature".

The publisher shown is the version resource's CompanyName, not the signer
certificate subject: for Steam the signer extractor returns the timestamp
countersigner ("DigiCert ... Timestamp Responder"), which is not who
published the program.

Qt-free. A refusal is `could_not_verify` with a reason, never "not signed".
"""
import ctypes
import logging
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import Dict, Optional

from core.procengine import signatures as sig

logger = logging.getLogger(__name__)

SIGNED = "signed"
SIGNED_CATALOG = "signed_catalog"
UNSIGNED = "unsigned"
INVALID = "invalid"
UNKNOWN = "unknown"

WTD_CHOICE_CATALOG = 2
_GENERIC_ACTION = sig.WINTRUST_ACTION_GENERIC_VERIFY_V2

_wintrust = sig._wintrust
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_INVALID_HANDLE = ctypes.c_void_p(-1).value


@dataclass(frozen=True)
class Trust:
    status: str                 # one of the constants above
    publisher: Optional[str]    # CompanyName from the version resource
    reason: Optional[str] = None

    @property
    def is_signed(self) -> bool:
        return self.status in (SIGNED, SIGNED_CATALOG)


class _CATALOG_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD), ("wszCatalogFile", wintypes.WCHAR * 260)]


class _WINTRUST_CATALOG_INFO(ctypes.Structure):
    _fields_ = [("cbStruct", wintypes.DWORD), ("dwCatalogVersion", wintypes.DWORD),
                ("pcwszCatalogFilePath", wintypes.LPCWSTR),
                ("pcwszMemberTag", wintypes.LPCWSTR),
                ("pcwszMemberFilePath", wintypes.LPCWSTR),
                ("hMemberFile", wintypes.HANDLE),
                ("pbCalculatedFileHash", ctypes.c_void_p),
                ("cbCalculatedFileHash", wintypes.DWORD),
                ("pcCatalogContext", ctypes.c_void_p),
                ("hCatAdmin", ctypes.c_void_p)]


_wintrust.CryptCATAdminAcquireContext.argtypes = [
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD]
_wintrust.CryptCATAdminAcquireContext.restype = wintypes.BOOL
_wintrust.CryptCATAdminCalcHashFromFileHandle.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD]
_wintrust.CryptCATAdminCalcHashFromFileHandle.restype = wintypes.BOOL
_wintrust.CryptCATAdminEnumCatalogFromHash.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p)]
_wintrust.CryptCATAdminEnumCatalogFromHash.restype = ctypes.c_void_p
_wintrust.CryptCATCatalogInfoFromContext.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(_CATALOG_INFO), wintypes.DWORD]
_wintrust.CryptCATCatalogInfoFromContext.restype = wintypes.BOOL
_wintrust.CryptCATAdminReleaseCatalogContext.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
_wintrust.CryptCATAdminReleaseContext.argtypes = [ctypes.c_void_p, wintypes.DWORD]
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def catalog_signed(path: str) -> Optional[bool]:
    """True when a security catalog lists this file's hash and the catalog
    verifies; False when no catalog lists it; None when we could not look."""
    handle = _kernel32.CreateFileW(path, 0x80000000, 1 | 2, None, 3, 0x80, None)
    if handle in (None, _INVALID_HANDLE):
        return None
    admin = ctypes.c_void_p()
    try:
        if not _wintrust.CryptCATAdminAcquireContext(ctypes.byref(admin), None, 0):
            return None
        size = wintypes.DWORD(0)
        _wintrust.CryptCATAdminCalcHashFromFileHandle(handle, ctypes.byref(size), None, 0)
        if not size.value:
            return None
        digest = (ctypes.c_ubyte * size.value)()
        if not _wintrust.CryptCATAdminCalcHashFromFileHandle(
                handle, ctypes.byref(size), digest, 0):
            return None
        return _verify_in_catalog(admin, path, digest, size.value)
    finally:
        _kernel32.CloseHandle(handle)
        if admin.value:
            _wintrust.CryptCATAdminReleaseContext(admin, 0)


def _verify_in_catalog(admin, path, digest, size) -> bool:
    context = _wintrust.CryptCATAdminEnumCatalogFromHash(admin, digest, size, 0, None)
    if not context:
        return False
    try:
        info = _CATALOG_INFO()
        info.cbStruct = ctypes.sizeof(info)
        if not _wintrust.CryptCATCatalogInfoFromContext(context, ctypes.byref(info), 0):
            return False
        tag = "".join(f"{b:02X}" for b in bytes(digest))
        cat = _WINTRUST_CATALOG_INFO()
        cat.cbStruct = ctypes.sizeof(cat)
        cat.pcwszCatalogFilePath = info.wszCatalogFile
        cat.pcwszMemberTag = tag
        cat.pcwszMemberFilePath = path
        cat.pbCalculatedFileHash = ctypes.cast(digest, ctypes.c_void_p)
        cat.cbCalculatedFileHash = size
        cat.hCatAdmin = admin
        data = sig._WINTRUST_DATA()
        data.cbStruct = ctypes.sizeof(data)
        data.dwUIChoice = sig.WTD_UI_NONE
        data.dwUnionChoice = WTD_CHOICE_CATALOG
        data.pFile = ctypes.cast(ctypes.byref(cat), ctypes.c_void_p)
        data.dwProvFlags = sig.WTD_REVOCATION_CHECK_NONE
        action = sig._GUID(*_GENERIC_ACTION)
        code = _wintrust.WinVerifyTrust(None, ctypes.byref(action), ctypes.byref(data))
        return (code & 0xFFFFFFFF) == 0
    finally:
        _wintrust.CryptCATAdminReleaseCatalogContext(admin, context, 0)


_CACHE: Dict[str, Trust] = {}


def clear_cache() -> None:
    _CACHE.clear()


_version = ctypes.WinDLL("version", use_last_error=True)
_version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
_version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
_version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]


def _publisher(path: str) -> Optional[str]:
    """CompanyName from the version resource, read up to its NUL.

    `core.procengine.modinfo.version_info` reads by the returned length and
    on this machine gave "Bitdefender L$FileDescr" for Bitdefender's own
    binaries, so this reads the string as NUL-terminated instead.
    """
    size = _version.GetFileVersionInfoSizeW(path, None)
    if not size:
        return None
    block = ctypes.create_string_buffer(size)
    if not _version.GetFileVersionInfoW(path, 0, size, block):
        return None
    value, length = ctypes.c_void_p(), wintypes.UINT(0)
    if not _version.VerQueryValueW(block, "\\VarFileInfo\\Translation", ctypes.byref(value),
                                   ctypes.byref(length)) or not length.value:
        return None
    lang, codepage = ctypes.cast(value, ctypes.POINTER(wintypes.WORD * 2)).contents
    query = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\CompanyName"
    if not _version.VerQueryValueW(block, query, ctypes.byref(value), ctypes.byref(length)) \
            or not length.value:
        return None
    return ctypes.wstring_at(value.value).strip() or None


def check(path: str) -> Trust:
    """Trust facts for one file. Never raises."""
    key = path.lower()
    if key in _CACHE:
        return _CACHE[key]
    trust = _check(path)
    _CACHE[key] = trust
    return trust


def _check(path: str) -> Trust:
    if not path or not os.path.isfile(path):
        return Trust(UNKNOWN, None, "the file does not exist")
    try:
        facts = sig.verify_signature(path)
        publisher = _publisher(path)
        if facts.status == sig.VALID:
            return Trust(SIGNED, publisher)
        if facts.status == sig.INVALID:
            return Trust(INVALID, publisher, facts.reason)
        if facts.status == sig.COULD_NOT_VERIFY:
            return Trust(UNKNOWN, publisher, facts.reason)
        in_catalog = catalog_signed(path)
        if in_catalog:
            return Trust(SIGNED_CATALOG, publisher)
        if in_catalog is None:
            return Trust(UNKNOWN, publisher, "could not read the file to look for a catalog signature")
        return Trust(UNSIGNED, publisher)
    except (OSError, ValueError, ctypes.ArgumentError) as error:
        logger.warning("Signature check failed for %s: %s", path, error)
        return Trust(UNKNOWN, None, f"signature check failed: {error}")

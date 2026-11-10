"""The Winsock LSP (Layered Service Provider) catalog. Qt-free.

`netsh winsock show catalog` prints one "Field: Value" block per registered
provider. A provider whose DLL no longer exists on disk is a corrupted
catalog entry -- the classic cause of networking breaking for every
application at once (WSAAF / "corrupted" errors), and exactly what
`network_fixes.reset_winsock` exists to repair. This reads the catalog
first so a repair is informed by an actual diagnosis rather than reached
for blind.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_FIELD = re.compile(r"^\s*(.+?)\s*:\s*(.*?)\s*$")


@dataclass
class WinsockProvider:
    description: str
    provider_path: str
    catalog_id: str
    entry_type: str


def parse_catalog(text: str) -> List[WinsockProvider]:
    """Every provider block `netsh winsock show catalog` printed, in order."""
    providers: List[WinsockProvider] = []
    fields = {}

    def flush() -> None:
        if fields.get("description") is not None:
            providers.append(WinsockProvider(
                description=fields.get("description", ""),
                provider_path=fields.get("provider path", ""),
                catalog_id=fields.get("catalog entry id", ""),
                entry_type=fields.get("entry type", "")))

    for raw in text.splitlines():
        if raw.strip().startswith("---"):
            continue
        if "Winsock Catalog Provider Entry" in raw:
            flush()
            fields = {}
            continue
        match = _FIELD.match(raw)
        if match is None:
            continue
        fields[match.group(1).strip().lower()] = match.group(2).strip()
    flush()
    return providers


def _resolve_indirect_string(s: str) -> str:
    if not s.startswith("@"):
        return s
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        if ctypes.windll.shlwapi.SHLoadIndirectString(s, buf, 512, None) == 0 and buf.value:
            return buf.value
    except Exception as exc:
        logger.debug("SHLoadIndirectString failed for %r: %s", s, exc)
    return s


def read_catalog_from_registry() -> Optional[List[WinsockProvider]]:
    """Read Winsock catalog entries directly from HKLM registry.

    Provides reliable, unelevated access even on modern Windows 11 builds
    where `netsh winsock show catalog` has been deprecated.
    """
    import winreg
    subkeys = [
        r"SYSTEM\CurrentControlSet\Services\Winsock2\Parameters\Protocol_Catalog9\Catalog_Entries",
        r"SYSTEM\CurrentControlSet\Services\Winsock2\Parameters\Protocol_Catalog9\Catalog_Entries64",
    ]
    providers: List[WinsockProvider] = []
    found_any = False
    for path in subkeys:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
                num_subkeys = winreg.QueryInfoKey(k)[0]
                found_any = True
                for idx in range(num_subkeys):
                    sub_name = winreg.EnumKey(k, idx)
                    with winreg.OpenKey(k, sub_name) as sk:
                        try:
                            pname = str(winreg.QueryValueEx(sk, "ProtocolName")[0])
                        except OSError:
                            pname = sub_name
                        desc = _resolve_indirect_string(pname)
                        try:
                            raw = winreg.QueryValueEx(sk, "PackedCatalogItem")[0]
                            prov_path = raw.split(b"\x00")[0].decode("latin1", errors="replace").strip()
                        except (OSError, IndexError, AttributeError):
                            prov_path = ""
                        providers.append(WinsockProvider(
                            description=desc,
                            provider_path=prov_path,
                            catalog_id=sub_name,
                            entry_type="Base Service Provider",
                        ))
        except OSError as exc:
            logger.debug("Could not open Winsock catalog key %s: %s", path, exc)
            continue
    return providers if found_any else None


def read_catalog(timeout: int = 30) -> Optional[List[WinsockProvider]]:
    """None means the read failed or was refused."""
    try:
        result = subprocess.run(
            ["netsh", "winsock", "show", "catalog"], capture_output=True, text=True,
            errors="replace", timeout=timeout, creationflags=_CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("netsh winsock show catalog failed: %s", exc)
        return None
    if result.returncode != 0:
        err_msg = (result.stderr or result.stdout or "").strip()
        if "command was not found" in err_msg.lower():
            return read_catalog_from_registry()
        logger.warning("netsh winsock show catalog refused: %s", err_msg[:200])
        return None
    return parse_catalog(result.stdout)


def broken_providers(providers: List[WinsockProvider]) -> List[WinsockProvider]:
    """Providers whose DLL does not exist -- the corrupted-catalog symptom."""
    out = []
    for p in providers:
        path = os.path.expandvars(p.provider_path.strip('"'))
        if path and not os.path.isfile(path):
            out.append(p)
    return out

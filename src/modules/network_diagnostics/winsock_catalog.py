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
        logger.warning("netsh winsock show catalog refused: %s",
                       (result.stderr or result.stdout or "").strip()[:200])
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

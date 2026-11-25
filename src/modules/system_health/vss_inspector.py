"""Volume Shadow Copy (VSS) Writers & Providers Health Inspector.

Parses VSS writers and providers, flags stalled or failed writers, and provides
safe service restart remediation for failed backups and restore points.
Pure Python (Qt-free), robust and SAC-safe.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class VssWriter:
    name: str
    id_guid: str
    instance_id: str
    state_code: int
    state_desc: str
    last_error: str
    is_failed: bool = False

    @property
    def is_stable(self) -> bool:
        return self.state_code == 1 and ("no error" in self.last_error.lower() or not self.last_error)


def parse_vssadmin_writers(output: str) -> List[VssWriter]:
    """Parse output of `vssadmin list writers`."""
    writers: List[VssWriter] = []
    current: Dict[str, str] = {}

    def flush():
        name = current.get("writer name", "")
        if name:
            # State line format: [1] Stable, [5] Waiting for completion, [8] Failed
            state_raw = current.get("state", "")
            code_m = re.search(r"\[(\d+)\]", state_raw)
            state_code = int(code_m.group(1)) if code_m else 0
            state_desc = re.sub(r"\[\d+\]\s*", "", state_raw).strip()
            last_err = current.get("last error", "").strip()

            is_failed = state_code not in (1, 5) or ("no error" not in last_err.lower() and bool(last_err))

            writers.append(VssWriter(
                name=name.strip("'\""),
                id_guid=current.get("writer id", "").strip("{}"),
                instance_id=current.get("writer instance id", "").strip("{}"),
                state_code=state_code,
                state_desc=state_desc,
                last_error=last_err,
                is_failed=is_failed,
            ))

    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k_clean = k.strip().lower()
            v_clean = v.strip()
            if k_clean == "writer name":
                if current:
                    flush()
                    current = {}
            current[k_clean] = v_clean

    if current:
        flush()

    return writers


def get_vss_writers_status() -> Tuple[List[VssWriter], str]:
    """Execute vssadmin list writers and parse output."""
    try:
        proc = subprocess.run(
            ["vssadmin.exe", "list", "writers"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).strip()
            return [], f"vssadmin refused ({proc.returncode}): {err}"
        return parse_vssadmin_writers(proc.stdout), ""
    except Exception as exc:
        return [], f"Failed to run vssadmin: {exc}"


def restart_vss_services() -> Tuple[bool, str]:
    """Restart VSS, swprv, and CryptSvc to reset stalled VSS writers."""
    services = ["vss", "swprv", "cryptsvc"]
    log = []

    # 1. Stop services
    for svc in services:
        try:
            subprocess.run(["net.exe", "stop", svc, "/y"], capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            log.append(f"Stopped {svc}")
        except Exception as e:
            log.append(f"Failed to stop {svc}: {e}")

    # 2. Start services
    success = True
    for svc in reversed(services):
        try:
            res = subprocess.run(["net.exe", "start", svc], capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if res.returncode == 0:
                log.append(f"Started {svc}")
            else:
                success = False
                log.append(f"Failed to start {svc}: {res.stderr.strip()}")
        except Exception as e:
            success = False
            log.append(f"Error starting {svc}: {e}")

    return success, "\n".join(log)

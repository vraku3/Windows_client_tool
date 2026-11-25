"""DriverStore OEM driver packages parser, superseded detector, and cleanup engine.

Provides pure Python (Qt-free) scanning and removal:
1. Enumerates OEM driver packages installed in the DriverStore via `pnputil /enum-drivers`.
2. Measures on-disk size of corresponding folders in `C:\\Windows\\System32\\DriverStore\\FileRepository`.
3. Detects superseded (older duplicate) packages by matching (original_name, class_name).
4. Provides safe driver package deletion via `pnputil /delete-driver <oemX.inf>`.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from core.windows_utils import system32

logger = logging.getLogger(__name__)

DRIVER_STORE_REPO = os.path.join(system32(), "DriverStore", "FileRepository")


@dataclass
class DriverPackage:
    published_name: str       # e.g. "oem7.inf"
    original_name: str        # e.g. "amdafd.inf"
    provider_name: str        # e.g. "Advanced Micro Devices, Inc."
    class_name: str           # e.g. "System"
    class_guid: str           # e.g. "{4d36e97d-e325-11ce-bfc1-08002be10318}"
    driver_version: str       # e.g. "24.10.0.1"
    driver_date: str          # e.g. "06/07/2024"
    signer_name: str          # e.g. "Microsoft Windows Hardware Compatibility Publisher"
    is_superseded: bool = False
    superseded_by: str = ""   # e.g. "26.20.0.1 (oem75.inf)"
    size_bytes: int = 0       # Disk size in FileRepository

    @property
    def version_tuple(self) -> Tuple[int, ...]:
        parts = re.findall(r"\d+", self.driver_version)
        return tuple(int(p) for p in parts) if parts else (0,)

    @property
    def parsed_date(self) -> datetime:
        # Standard format from pnputil is MM/DD/YYYY
        try:
            return datetime.strptime(self.driver_date.strip(), "%m/%d/%Y")
        except Exception:
            return datetime.min


def _parse_field(line: str) -> Tuple[str, str]:
    if ":" not in line:
        return "", ""
    k, v = line.split(":", 1)
    return k.strip().lower(), v.strip()


def parse_pnputil_output(text: str) -> List[DriverPackage]:
    """Parse output from `pnputil /enum-drivers` into DriverPackage objects."""
    packages: List[DriverPackage] = []
    current: Dict[str, str] = {}

    def flush():
        pub = current.get("published name") or ""
        if pub.lower().endswith(".inf"):
            # Driver version line often has: "MM/DD/YYYY X.Y.Z.W"
            v_line = current.get("driver version", "")
            date_part = ""
            ver_part = ""
            tokens = v_line.split()
            if len(tokens) >= 2:
                date_part = tokens[0]
                ver_part = tokens[1]
            elif len(tokens) == 1:
                ver_part = tokens[0]

            packages.append(DriverPackage(
                published_name=pub,
                original_name=current.get("original name", ""),
                provider_name=current.get("provider name", ""),
                class_name=current.get("class name", ""),
                class_guid=current.get("class guid", ""),
                driver_version=ver_part,
                driver_date=date_part,
                signer_name=current.get("signer name", ""),
            ))

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        k, v = _parse_field(line)
        if k == "published name":
            if current:
                flush()
                current = {}
            current["published name"] = v
        elif k:
            current[k] = v

    if current:
        flush()

    return packages


def calculate_folder_size(folder_path: str) -> int:
    """Calculate total size of files inside a directory."""
    total = 0
    try:
        for root, _, files in os.walk(folder_path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total += os.path.getsize(fp)
                except OSError as e:
                    logger.debug("Failed getting size for %s: %s", fp, e)
    except OSError as e:
        logger.debug("Failed walking folder %s: %s", folder_path, e)
    return total


def mark_superseded_packages(packages: List[DriverPackage]) -> None:
    """Identify older packages for the same (original_name, class_name) and mark them as superseded."""
    groups: Dict[Tuple[str, str], List[DriverPackage]] = {}
    for p in packages:
        key = (p.original_name.lower().strip(), p.class_name.lower().strip())
        groups.setdefault(key, []).append(p)

    for (orig_name, cls_name), pkg_list in groups.items():
        if not orig_name or len(pkg_list) <= 1:
            continue

        # Sort by version tuple descending, then parsed date descending
        pkg_list.sort(key=lambda x: (x.version_tuple, x.parsed_date), reverse=True)
        latest = pkg_list[0]

        for older in pkg_list[1:]:
            older.is_superseded = True
            older.superseded_by = f"{latest.driver_version} ({latest.published_name})"


def scan_driver_store() -> List[DriverPackage]:
    """Execute `pnputil /enum-drivers`, parse output, measure size, and mark superseded packages."""
    cmd = ["pnputil.exe", "/enum-drivers"]
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if res.returncode != 0 and not res.stdout:
            logger.warning("pnputil /enum-drivers failed with returncode %d", res.returncode)
            return []
    except Exception as exc:
        logger.warning("Error running pnputil: %s", exc)
        return []

    packages = parse_pnputil_output(res.stdout)

    # Cache FileRepository folder sizes
    repo_dirs: Dict[str, int] = {}
    if os.path.isdir(DRIVER_STORE_REPO):
        try:
            for entry in os.scandir(DRIVER_STORE_REPO):
                if entry.is_dir():
                    # Directory names look like: "amdafd.inf_amd64_18fb8f78cf4a95c3"
                    inf_prefix = entry.name.split("_")[0].lower()
                    size = calculate_folder_size(entry.path)
                    repo_dirs[entry.name.lower()] = size
                    # Also map prefix
                    if inf_prefix not in repo_dirs:
                        repo_dirs[inf_prefix] = size
        except OSError as e:
            logger.debug("Could not scan FileRepository: %s", e)

    for p in packages:
        orig = p.original_name.lower().strip()
        if orig in repo_dirs:
            p.size_bytes = repo_dirs[orig]

    mark_superseded_packages(packages)
    return packages


def delete_driver_package(published_name: str, force: bool = False) -> Tuple[bool, str]:
    """Safely uninstall and delete an OEM driver package from the DriverStore."""
    if not published_name.lower().startswith("oem") or not published_name.lower().endswith(".inf"):
        return False, f"Invalid package name '{published_name}'. Only oem*.inf packages can be deleted."

    cmd = ["pnputil.exe", "/delete-driver", published_name]
    if force:
        cmd.append("/force")

    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (res.stdout or "") + (res.stderr or "")
        out_clean = out.strip()

        if "driver package deleted successfully" in out.lower():
            return True, f"Successfully deleted package {published_name} from DriverStore."
        elif "failed to delete" in out.lower() or "in use" in out.lower():
            return False, f"Failed to delete {published_name}: {out_clean}"
        elif res.returncode == 0:
            return True, f"Package {published_name} deleted ({out_clean})."
        else:
            return False, f"pnputil failed ({res.returncode}): {out_clean}"
    except Exception as exc:
        return False, f"Error deleting driver package: {exc}"

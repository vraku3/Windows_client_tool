"""The slower facts, filled in after the list is on screen: storage, winget
updates, and what is new since the last session.

Qt-free.

* **Storage** -- App files (the package or install folder) plus App data
  (``%LocalAppData%\\Packages\\<family>`` for a Windows app). Measured here:
  all 154 packages' folders in ~2.6 s, every one readable unelevated. A
  folder the walk could not finish is reported as "at least", never as its
  partial sum passed off as the size.
* **Updates** -- ``winget upgrade``, parsed by COLUMN from the header offsets
  (names and ids carry spaces and dots; see CLAUDE.md, Apps tab). Matched to
  rows by winget's own name first, then by package family for msstore ids.
* **Newly discovered** -- the keys seen last session are kept in a small JSON
  file; anything not in it is new. The first run ever has no baseline, and
  says so by reporting nothing as new rather than everything.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import model as m

logger = logging.getLogger(__name__)

#: A Windows install folder past this many files is not walked to the end.
MAX_FILES = 60_000


def folder_size(path: str, max_files: int = MAX_FILES) -> Tuple[Optional[int], bool]:
    """(bytes, complete). None when the folder is not there at all."""
    if not path or not os.path.isdir(path):
        return None, True
    total, count, complete = 0, 0, True
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        complete = False
                    if count > max_files:
                        return total, False
        except OSError as e:
            logger.debug("size walk refused at %s: %s", current, e)
            complete = False
    return total, complete


def measure(rec: m.AppRecord) -> None:
    """Fill files/data bytes in place. Desktop apps keep the installer's own
    EstimatedSize when it gave one; a walk replaces it only when it did not."""
    rec.extra["measured"] = "yes"       # tried: None from here on means "nothing to measure"
    if rec.type in (m.WINDOWS, m.SYSTEM, m.FRAMEWORK) and rec.install_location:
        size, complete = folder_size(rec.install_location)
        rec.files_bytes = size
        if not complete:
            rec.extra["storage_note"] = "at least (the folder could not be read to the end)"
    elif rec.type in (m.DESKTOP, m.DEFECT) and rec.files_bytes is None and rec.install_location:
        size, complete = folder_size(rec.install_location)
        rec.files_bytes = size
        if not complete:
            rec.extra["storage_note"] = "at least (the folder could not be read to the end)"
    elif rec.type == m.ORPHANED and rec.leftover == m.LEFTOVER_DATA_FOLDER:
        rec.files_bytes = 0
        rec.data_bytes, _complete = folder_size(rec.leftover_paths[0])
        return
    data = rec.extra.get("data_folder", "")
    if data:
        rec.data_bytes, _complete = folder_size(data)
    elif rec.type == m.WINDOWS and rec.status == m.INSTALLED:
        rec.data_bytes = 0


# ---- winget ---------------------------------------------------------------------------------

class WingetRow:
    def __init__(self, name: str, wid: str, version: str, available: str, source: str) -> None:
        self.name, self.id, self.version, self.available, self.source = name, wid, version, available, source


def parse_upgrade_table(output: str) -> Optional[List[WingetRow]]:
    """Rows of `winget upgrade`. None when the output is not a winget table at
    all (winget missing, an error), [] when it ran and found nothing."""
    lines = output.replace("\r", "\n").splitlines()
    head = next((i for i, ln in enumerate(lines)
                 if re.search(r"\bName\b", ln) and re.search(r"\bId\b", ln) and "Available" in ln), -1)
    if head < 0:
        if re.search(r"no installed package|no available upgrade|no applicable upgrade", output, re.I):
            return []
        return None
    header = lines[head]
    header = header[header.index("Name"):]
    offset = lines[head].index("Name")
    cols = [header.index(c) for c in ("Name", "Id", "Version", "Available")]
    c_src = header.index("Source") if "Source" in header else None
    rows: List[WingetRow] = []
    for raw in lines[head + 1:]:
        ln = raw[offset:]
        if not ln.strip() or set(ln.strip()) <= {"-"} or re.search(r"upgrades? available", ln, re.I):
            continue
        if re.search(r"require explicit targeting|package\(s\) have version numbers", ln, re.I):
            break
        name = ln[cols[0]:cols[1]].strip()
        wid = ln[cols[1]:cols[2]].strip().split(" ")[0]
        version = ln[cols[2]:cols[3]].strip()
        available = (ln[cols[3]:c_src] if c_src else ln[cols[3]:]).strip().split(" ")[0]
        source = ln[c_src:].strip() if c_src else ""
        if name and wid and available and available.lower() != "unknown":
            rows.append(WingetRow(name, wid, version, available, source))
    return rows


def query_upgrades(timeout: float = 120) -> Tuple[Optional[List[WingetRow]], str]:
    try:
        proc = subprocess.run(
            ["winget", "upgrade", "--include-unknown", "--accept-source-agreements",
             "--disable-interactivity"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"winget did not answer: {e}"
    rows = parse_upgrade_table(proc.stdout or "")
    if rows is None:
        return None, (proc.stderr or proc.stdout or "winget gave no table").strip()[-300:]
    return rows, ""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def attach_updates(rows: Sequence[m.AppRecord], upgrades: Iterable[WingetRow]) -> int:
    """Mark rows winget can update. Returns how many were matched."""
    by_name: Dict[str, m.AppRecord] = {}
    for r in rows:
        if r.type in (m.DESKTOP, m.WINDOWS, m.DEFECT) and r.status == m.INSTALLED:
            by_name.setdefault(_norm(r.name), r)
    by_family = {r.family.lower(): r for r in rows if r.family}
    matched = 0
    for up in upgrades:
        rec = by_name.get(_norm(up.name)) or by_family.get(up.id.lower())
        if rec is None and up.source == "msstore":
            rec = next((r for r in rows if r.family and r.family.lower().startswith(up.id.lower())), None)
        if rec is not None and not rec.update:
            rec.update, rec.winget_id = up.available, up.id
            matched += 1
    return matched


# ---- change tracking -------------------------------------------------------------------------

class SeenStore:
    """The set of app keys seen at the end of the previous session."""

    def __init__(self, path: str) -> None:
        self.path = path

    def load(self) -> Optional[Set[str]]:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            logger.warning("App Buster's last-seen list is unreadable (%s); treating as first run", e)
            return None
        return set(data.get("keys", [])) if isinstance(data, dict) else None

    def save(self, keys: Iterable[str]) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"keys": sorted(keys)}, f)
            os.replace(tmp, self.path)
        except OSError as e:
            logger.warning("could not save App Buster's last-seen list: %s", e)


def newly_discovered(rows: Sequence[m.AppRecord], previous: Optional[Set[str]]) -> Set[str]:
    if previous is None:
        return set()
    return {r.key for r in rows if r.key not in previous and r.status != m.INSTALLABLE}

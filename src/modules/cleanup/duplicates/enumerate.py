"""Parallel scandir traversal. No data opens, Qt or image imports here."""
from __future__ import annotations

import logging
import os
import stat
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from modules.cleanup.cleanup_scanner import known_folders
from modules.cleanup.cleanup_scanner.drives import expand_fixed_drives
from .models import FileEntry, under

logger = logging.getLogger(__name__)
CLOUD_MASK = 0x400000 | 0x40000 | 0x1000


@dataclass
class ScanOptions:
    roots: list[str] = field(default_factory=list)
    min_size: int = 1024 * 1024
    max_size: int = 0
    method: str = "exact"
    include_extensions: tuple[str, ...] = ()
    exclude_extensions: tuple[str, ...] = ()
    exclude_folders: tuple[str, ...] = ()
    skip_system_folders: bool = True
    skip_hidden_system: bool = True
    use_cache: bool = True
    cache_path: str | None = None
    threads: int = 4
    image_threshold: int = 5


def system_folders() -> list[str]:
    return list(dict.fromkeys(os.path.abspath(os.environ[name]) for name in
                ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)",
                 "ProgramData", "APPDATA", "LOCALAPPDATA") if os.environ.get(name)))


def unsafe_ancestors(path: str) -> bool:
    """Reject a root reached through a junction too, not just links met in the walk."""
    current = os.path.abspath(path)
    while True:
        st = os.lstat(current)
        attrs = getattr(st, "st_file_attributes", 0)
        if stat.S_ISLNK(st.st_mode) or attrs & 0x400 or attrs & CLOUD_MASK:
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def enumerate_files(options: ScanOptions, cancelled=lambda: False, progress=None):
    roots = []
    errors = []
    skipped = 0
    excludes = [os.path.abspath(p) for p in options.exclude_folders]
    if options.skip_system_folders:
        excludes += system_folders()
    for raw in options.roots:
        for root in expand_fixed_drives(raw):
            root = os.path.abspath(root)
            if not any(under(root, prev) for prev in roots):
                roots = [prev for prev in roots if not under(prev, root)]
                roots.append(root)
    include = {e.lower().lstrip("*.") for e in options.include_extensions}
    exclude = {e.lower().lstrip("*.") for e in options.exclude_extensions}

    def walk_directory(path):
        files, dirs, problems, dropped = [], [], [], 0
        if cancelled():
            return files, dirs, problems, dropped
        try:
            if any(under(path, ex) for ex in excludes) or unsafe_ancestors(path):
                return files, dirs, problems, 1
            with os.scandir(path) as entries:
                for entry in entries:
                    if cancelled():
                        break
                    try:
                        st = entry.stat(follow_symlinks=False)
                        attrs = getattr(st, "st_file_attributes", 0)
                        if (stat.S_ISLNK(st.st_mode) or attrs & CLOUD_MASK
                                or (options.skip_hidden_system and attrs & 0x6)):
                            dropped += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if not attrs & 0x400:
                                dirs.append(entry.path)
                            else:
                                dropped += 1
                            continue
                        if not stat.S_ISREG(st.st_mode) or st.st_size == 0:
                            continue
                        ext = os.path.splitext(entry.name)[1].lower().lstrip(".")
                        if (st.st_size < options.min_size
                                or (options.max_size and st.st_size > options.max_size)
                                or (include and ext not in include) or ext in exclude):
                            continue
                        # Metadata only: obtain real Windows identity; see models.py.
                        files.append(FileEntry.from_path(entry.path))
                    except OSError as exc:
                        problems.append((entry.path, str(exc)))
        except OSError as exc:
            problems.append((path, str(exc)))
        return files, dirs, problems, dropped

    found, pending_dirs = [], list(roots)
    with ThreadPoolExecutor(max_workers=max(1, options.threads), thread_name_prefix="dupes-list") as pool:
        futures = set()
        while (pending_dirs or futures) and not cancelled():
            while pending_dirs and len(futures) < max(1, options.threads) * 2:
                futures.add(pool.submit(walk_directory, pending_dirs.pop()))
            done, futures = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                files, dirs, problems, dropped = future.result()
                found.extend(files)
                pending_dirs.extend(dirs)
                errors.extend(problems)
                skipped += dropped
            if progress:
                progress({"stage": "Listing", "files": len(found), "errors": len(errors)})
        for future in futures:
            future.cancel()
    for path, reason in errors[:20]:
        logger.warning("Duplicate scan could not list %s: %s", path, reason)
    # Alias paths and overlapping roots cannot create duplicate physical files.
    unique = {}
    hardlinks = 0
    for entry in found:
        if entry.identity in unique:
            hardlinks += 1
        else:
            unique[entry.identity] = entry
    return list(unique.values()), hardlinks, skipped, errors

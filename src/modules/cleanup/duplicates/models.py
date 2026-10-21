"""Immutable metadata and result types shared by the engine and UI."""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field

from modules.cleanup.cleanup_scanner.disk_size import on_disk_size


@dataclass(frozen=True, slots=True)
class FileEntry:
    path: str
    size: int
    mtime_ns: int
    ctime_ns: int
    dev: int
    ino: int
    nlink: int = 1
    attrs: int = 0
    #: Bytes the file occupies on disk (sparse/compressed differ from `size`);
    #: -1 = not measured, use `size`. `size` stays the LOGICAL length, which is
    #: what content comparison needs.
    disk: int = -1

    @classmethod
    def from_path(cls, path: str) -> FileEntry:
        # On Windows DirEntry.stat() caches zero for st_ino/st_dev/st_nlink.
        # os.stat is required for an actual file identity, without opening data.
        st = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
            raise OSError("Not a regular, unlinked file")
        return cls(os.path.abspath(path), st.st_size, st.st_mtime_ns,
                   getattr(st, "st_birthtime_ns", st.st_ctime_ns),
                   st.st_dev, st.st_ino, st.st_nlink,
                   getattr(st, "st_file_attributes", 0),
                   on_disk_size(path, st))

    @property
    def on_disk(self) -> int:
        return self.size if self.disk < 0 else self.disk

    @property
    def identity(self):
        return (self.dev, self.ino) if self.ino else (self.path,)

    @property
    def cache_key(self):
        return (os.path.normcase(self.path), self.size, self.mtime_ns, self.dev, self.ino)

    def unchanged(self, other: FileEntry) -> bool:
        return self.cache_key == other.cache_key


@dataclass(slots=True)
class DuplicateGroup:
    id: int
    files: list[FileEntry]
    method: str
    confidence: str
    digest: str = ""

    @property
    def reclaimable(self) -> int:
        # A surviving name of a multiply-linked file cannot free that data.
        # Counted on disk: deleting a sparse copy frees what it allocates.
        eligible = sorted((f.on_disk for f in self.files if f.nlink <= 1), reverse=True)
        if not any(f.nlink > 1 for f in self.files) and eligible:
            eligible.pop(0)
        return sum(eligible)


@dataclass(slots=True)
class ScanResult:
    groups: list[DuplicateGroup] = field(default_factory=list)
    listed: int = 0
    hardlinks: int = 0
    skipped: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    stage_bytes: dict[str, int] = field(default_factory=dict)
    bytes_hashed: int = 0
    cache_hits: int = 0
    candidate_groups: int = 0


def validate_selection(groups: list[DuplicateGroup], selected: set[str]) -> None:
    known = {f.path for g in groups for f in g.files}
    if selected - known:
        raise ValueError("Selection contains files outside the scan")
    for group in groups:
        if group.files and all(f.path in selected for f in group.files):
            raise ValueError("Keep at least one file in every group")


def select_files(groups: list[DuplicateGroup], strategy: str,
                 folder: str = "") -> set[str]:
    selected: set[str] = set()
    preferred = os.path.normcase(os.path.abspath(folder)) if folder else ""
    for group in groups:
        if group.method in ("size", "images"):
            continue
        files = group.files
        if not files:
            continue
        if strategy == "newest":
            keeper = max(files, key=lambda f: (f.mtime_ns, f.path))
        elif strategy == "oldest":
            keeper = min(files, key=lambda f: (f.mtime_ns, f.path))
        elif strategy == "shortest":
            keeper = min(files, key=lambda f: (len(f.path), f.path))
        elif strategy == "longest":
            keeper = max(files, key=lambda f: (len(f.path), f.path))
        elif strategy in ("preferred", "keep_folder"):
            matches = [f for f in files if under(f.path, preferred)] if preferred else []
            if not matches:
                continue
            keeper = min(matches, key=lambda f: f.path)
        else:
            continue
        selected.update(f.path for f in files if f.path != keeper.path
                        and not (strategy == "keep_folder" and under(f.path, preferred)))
    validate_selection(groups, selected)
    return selected


def under(path: str, root: str) -> bool:
    try:
        p, r = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(root))
        return os.path.commonpath((p, r)) == r
    except ValueError:
        return False  # Different volumes have no common path.

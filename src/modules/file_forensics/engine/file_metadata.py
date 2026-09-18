"""File metadata for File Forensics -- size, times, owner, read-only.

Owner lookup reuses `treesize.scan.owners.OwnerResolver` rather than a
second win32security wrapper: it already handles pywin32 being absent,
caches the SID->name round trip, and never raises on an unreadable ACL.
"""
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from modules.treesize.scan.owners import OwnerResolver

_owner_resolver = OwnerResolver()


@dataclass(frozen=True)
class FileMetadata:
    path: str
    size: int
    created: datetime
    modified: datetime
    accessed: datetime
    owner: str
    read_only: bool


def read_metadata(path: str) -> FileMetadata:
    """Everything about one file. Raises FileNotFoundError if it's gone --
    a real possibility for a temp file between being listed and being
    inspected, and the caller (analysis.py) is the one that decides
    whether that's worth reporting or silently skipping."""
    st = os.stat(path)
    try:
        owner = _owner_resolver.for_path(path)
    except Exception:  # noqa: BLE001 -- an unreadable ACL is ordinary, per owners.py's own docstring
        owner = ""
    return FileMetadata(
        path=path,
        size=st.st_size,
        created=datetime.fromtimestamp(st.st_ctime),
        modified=datetime.fromtimestamp(st.st_mtime),
        accessed=datetime.fromtimestamp(st.st_atime),
        owner=owner,
        read_only=not os.access(path, os.W_OK),
    )

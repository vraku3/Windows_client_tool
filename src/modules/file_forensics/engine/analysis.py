"""One found file -> everything File Forensics knows about it.

The single seam the UI calls, whether the file came from a manual search
or a live-watch detection -- both paths must produce identically-shaped
results, and this is what guarantees that.
"""
from dataclasses import dataclass
from typing import List, Optional

from .creator_heuristic import CreatorCandidate, find_creator_candidates
from .file_metadata import FileMetadata, read_metadata
from .locking_processes import LockingProcess, find_locking_processes
from .reputation import check_reputation, check_signature


@dataclass(frozen=True)
class FileAnalysis:
    metadata: FileMetadata
    locking_processes: List[LockingProcess]
    locking_summary: str
    creator_candidates: List[CreatorCandidate]
    #: None when there was no creator candidate to check at all -- a
    #: different fact than "checked and it's unsigned".
    top_creator_signed: Optional[bool]
    reputation: object  # VTResult | None


def analyze(path: str, tolerance_seconds: float = 5.0,
            vt_api_key: str = "") -> FileAnalysis:
    metadata = read_metadata(path)
    locking, locking_summary = find_locking_processes(path)
    candidates = find_creator_candidates(metadata.created, tolerance_seconds)

    top_creator_signed = None
    reputation = None
    if candidates and candidates[0].path:
        top_creator_signed = check_signature(candidates[0].path).signed
        reputation = check_reputation(candidates[0].path, vt_api_key)

    return FileAnalysis(
        metadata=metadata,
        locking_processes=locking,
        locking_summary=locking_summary,
        creator_candidates=candidates,
        top_creator_signed=top_creator_signed,
        reputation=reputation,
    )

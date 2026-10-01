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
from .reputation import (
    SignatureFacts, check_own_signature, check_reputation, check_signature,
)


@dataclass(frozen=True)
class FileAnalysis:
    metadata: FileMetadata
    locking_processes: List[LockingProcess]
    locking_summary: str
    creator_candidates: List[CreatorCandidate]
    #: None when there was no creator candidate to check at all, OR when
    #: the top candidate's own path could not be resolved -- both are
    #: "nothing to check", a different fact than a SignatureFacts that
    #: says genuinely unsigned, invalid, or could-not-verify (a refusal
    #: must never collapse into one of those either -- carry the whole
    #: object, not a pre-collapsed bool).
    top_creator_signature: Optional[SignatureFacts]
    reputation: object  # VTResult | None
    #: The FOUND file's own Authenticode verdict -- answers "is this exe/dll
    #: /msi itself signed, and validly", a different question from whether
    #: the process that dropped it was signed. `None` means the file is not
    #: a signable type at all (see `reputation.check_own_signature`), never
    #: collapsed with a real refusal.
    own_signature: Optional[SignatureFacts] = None


def analyze(path: str, tolerance_seconds: float = 5.0,
            vt_api_key: str = "") -> FileAnalysis:
    metadata = read_metadata(path)
    locking, locking_summary = find_locking_processes(path)
    candidates = find_creator_candidates(metadata.created, tolerance_seconds)
    own_signature = check_own_signature(path)

    top_creator_signature = None
    reputation = None
    if candidates and candidates[0].path:
        top_creator_signature = check_signature(candidates[0].path)
        reputation = check_reputation(candidates[0].path, vt_api_key)

    return FileAnalysis(
        metadata=metadata,
        locking_processes=locking,
        locking_summary=locking_summary,
        creator_candidates=candidates,
        top_creator_signature=top_creator_signature,
        reputation=reputation,
        own_signature=own_signature,
    )

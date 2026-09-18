"""Reputation and signature checks for a file -- both already built
elsewhere in this app (VirusTotal for Process Explorer, Authenticode
verification for the process engine generally); this only wires them in
for a file path found by File Forensics rather than a running process.
"""
from typing import Optional

from core.procengine.signatures import SignatureFacts, verify_signature
from core.virustotal_client import VTClient, VTResult, compute_sha256


def check_signature(path: str) -> SignatureFacts:
    return verify_signature(path)


def check_reputation(path: str, api_key: str) -> Optional[VTResult]:
    """`None` means "we didn't ask" (no key, or couldn't hash) -- distinct
    from `VTResult(found=False)`, which means VT itself said unknown."""
    if not api_key:
        return None
    sha256 = compute_sha256(path)
    if not sha256:
        return None
    return VTClient(api_key=api_key).check(sha256)

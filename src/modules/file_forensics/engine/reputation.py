"""Reputation and signature checks for a file -- both already built
elsewhere in this app (VirusTotal for Process Explorer, Authenticode
verification for the process engine generally); this only wires them in
for a file path found by File Forensics rather than a running process.
"""
import os
from typing import Optional

from core.procengine.signatures import SignatureFacts, verify_signature
from core.virustotal_client import VTClient, VTResult, compute_sha256

#: Extensions `WinVerifyTrust` actually renders a meaningful verdict for.
#: Measured on this machine (2026-10-01): a real .txt and a real .md both
#: come back `could_not_verify` with "The system cannot find the path
#: specified" (TRUST_E_SUBJECT_FORM_UNKNOWN, 0x800B0003) -- a provider that
#: does not recognise the file's FORM at all, not a refused read of a real
#: signature. Running every found file through WinVerifyTrust anyway would
#: paint that same non-answer on every ordinary document and photo File
#: Forensics turns up, drowning the real signal: an .exe/.dll/.msi that
#: genuinely has no signature, or one whose signature is INVALID. Gating on
#: extension keeps `check_own_signature` returning `None` ("not a file type
#: this can answer for") rather than a wall of identical, non-actionable
#: refusals -- distinct from a real `COULD_NOT_VERIFY` with a reason, which
#: a signable file can still get (access denied, a torn download). Real
#: DLLs (`C:\Windows\System32\*.dll`) and a real installer cache MSI
#: (`C:\Windows\Installer\108feac.msi`) both verified `valid` on this
#: machine against this list.
_SIGNABLE_EXTENSIONS = {
    ".exe", ".dll", ".sys", ".ocx", ".scr", ".cpl", ".com",
    ".msi", ".msp", ".cab",
}


def check_signature(path: str) -> SignatureFacts:
    return verify_signature(path)


def check_own_signature(path: str) -> Optional[SignatureFacts]:
    """The Authenticode verdict for the FOUND file itself -- distinct from
    `check_signature`, which is only ever called on a creator candidate's
    own exe. A dropped file that is itself an executable/installer can
    carry a tampered or missing signature regardless of who (if anyone) was
    caught creating it, and that is a different, equally real question.

    `None` means "not a file type this answers for" (see
    `_SIGNABLE_EXTENSIONS`'s docstring) -- never collapsed with a real
    `SignatureFacts(status=COULD_NOT_VERIFY, ...)`, which means the file
    WAS a signable type and the check itself was refused.
    """
    _, ext = os.path.splitext(path)
    if ext.lower() not in _SIGNABLE_EXTENSIONS:
        return None
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

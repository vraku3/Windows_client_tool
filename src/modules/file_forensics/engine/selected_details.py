"""Checks requested for one selected file. Cloud placeholders are never read."""
import os
from dataclasses import replace
from .reputation import check_own_signature, check_signature
from .file_metadata import read_metadata
from modules.cleanup.cleanup_scanner.known_folders import is_cloud_placeholder
from core.virustotal_client import compute_sha256, VTClient
from core.procengine.details import resolve
from core.procengine.signatures import SignatureFacts, COULD_NOT_VERIFY


def inspect_selected(analysis, mode, api_key=''):
    path = analysis.metadata.path
    notes = []
    placeholder = is_cloud_placeholder(path)
    attributes = getattr(os.stat(path, follow_symlinks=False), 'st_file_attributes', 0)
    notes.append(f'Attributes: 0x{attributes:X}; cloud placeholder: {"yes — contents never opened" if placeholder else "no"}')
    analysis = replace(analysis, metadata=read_metadata(path))
    for process in analysis.locking_processes:
        details = resolve(process.pid)
        notes.append(f'Open: {process.process} (PID {process.pid}), user: {details.user or details.user_error or "could not check"}')
    if mode == 'metadata':
        return analysis, mode, tuple(notes)
    if mode == 'signatures':
        if placeholder:
            notes.append('File signature not checked: cloud placeholder.')
        else:
            analysis = replace(analysis, own_signature=check_own_signature(path))
            notes.append('File signature: ' + (str(analysis.own_signature) if analysis.own_signature else 'not a signable file type'))
        top = None
        for i, candidate in enumerate(analysis.creator_candidates):
            if not candidate.path or is_cloud_placeholder(candidate.path):
                notes.append(f'{candidate.name}: signature could not be checked (path unavailable or cloud placeholder).')
                if i == 0:
                    top = SignatureFacts(path=candidate.path or '', status=COULD_NOT_VERIFY, reason='Path unavailable or cloud placeholder')
                continue
            sig = check_signature(candidate.path)
            notes.append(f'{candidate.name}: {sig.status}; signer: {sig.signer or "none"}; {sig.reason or ""}')
            if i == 0:
                top = sig
        analysis = replace(analysis, top_creator_signature=top)
    else:
        if placeholder:
            notes.append('SHA-256 / VirusTotal not checked: cloud placeholder.')
        elif not api_key:
            notes.append('VirusTotal not checked: set virustotal.api_key in Settings.')
        else:
            digest = compute_sha256(path)
            if digest:
                result = VTClient(api_key=api_key).check(digest)
                analysis = replace(analysis, reputation=result)
                notes.append(f'SHA-256: {digest}')
                notes.append('VirusTotal: ' + (result.error if result.error else f'{result.malicious}/{result.total} engines flagged it' if result.found else 'hash not known'))
            else:
                notes.append('SHA-256 could not be read; VirusTotal could not be checked.')
    return analysis, mode, tuple(notes)

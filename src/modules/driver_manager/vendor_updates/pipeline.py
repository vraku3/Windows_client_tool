"""The one safety pipeline every vendor provider and every install mode
(LIGHT now, FULL in Phase 2) goes through -- so adding either never means
re-deriving download/verification safety logic. Qt-free: driver_module.py
calls into this from a Worker, never the UI thread.
"""
import logging
import os
import uuid
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse
from urllib.request import urlopen

from core.procengine.signatures import verify_signature

logger = logging.getLogger(__name__)


def _default_cache_dir() -> Optional[str]:
    """None (never raises) if the directory can't be created -- a
    permission error here must reach download_and_verify as a normal
    refusal, the same as every other failure mode in this pipeline, not
    an uncaught exception."""
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    directory = os.path.join(base, "WindowsTweaker", "driver_updates", "downloads")
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        logger.warning("pipeline: could not create cache directory %s: %s",
                       directory, exc)
        return None
    return directory


@dataclass(frozen=True)
class DownloadResult:
    path: Optional[str]
    reason: str = ""  # always populated on failure, empty on success


def _download_file(url: str, dest_path: str) -> bool:
    """Real network download -- mocked in every test above it. Returns
    False on any failure rather than raising, so the caller's reason
    text stays uniform with every other refusal in this pipeline."""
    try:
        with urlopen(url, timeout=120) as resp, open(dest_path, "wb") as out:
            out.write(resp.read())
        return True
    except OSError as exc:
        logger.warning("pipeline: download failed for %s: %s", url, exc)
        return False


def download_and_verify(update, allowed_domains: List[str],
                        cache_dir: Optional[str] = None) -> DownloadResult:
    """update: a provider.UpdateInfo. Refuses (path=None, a stated reason)
    for: a malformed download URL, a download URL outside allowed_domains
    (checked by host suffix, never substring), a download that fails
    outright, or a downloaded file whose Authenticode signature is not
    VALID and signed by update.installer_signer exactly. Never runs
    anything it downloads -- that's the caller's job, only after this
    returns a real path."""
    try:
        host = urlparse(update.download_url).hostname or ""
    except ValueError as exc:
        # A malformed download_url (e.g. a bad vendor API response) must
        # come back as a clean refusal, not an uncaught exception -- the
        # entire point of this pipeline.
        logger.warning("pipeline: malformed download URL %s: %s",
                       update.download_url, exc)
        return DownloadResult(
            path=None,
            reason=f"download URL is malformed: {exc}")

    if not any(host.endswith(domain) for domain in allowed_domains):
        return DownloadResult(
            path=None,
            reason=f"download URL's domain ({host!r}) is not in the "
                   f"allowed list for {update.vendor}")

    directory = cache_dir or _default_cache_dir()
    if directory is None:
        return DownloadResult(path=None, reason="could not create a cache "
                             "directory for the download")
    dest_path = os.path.join(directory, f"{uuid.uuid4().hex}.exe")
    if not _download_file(update.download_url, dest_path):
        return DownloadResult(path=None, reason="the download itself failed")

    facts = verify_signature(dest_path)
    if not facts.signed:
        return DownloadResult(
            path=None,
            reason=f"downloaded file's signature is {facts.status} "
                   f"(expected a VALID signature from {update.installer_signer!r})")
    if facts.signer != update.installer_signer:
        return DownloadResult(
            path=None,
            reason=f"downloaded file's signer is {facts.signer!r}, "
                   f"expected {update.installer_signer!r} -- refusing to "
                   f"run something not from the expected vendor")
    return DownloadResult(path=dest_path)

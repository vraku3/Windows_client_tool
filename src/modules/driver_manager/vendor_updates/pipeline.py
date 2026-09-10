"""The one safety pipeline every vendor provider and every install mode
(LIGHT now, FULL in Phase 2) goes through -- so adding either never means
re-deriving download/verification safety logic. Qt-free: driver_module.py
calls into this from a Worker, never the UI thread.
"""
import logging
import os
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse
from urllib.request import urlopen

from core.procengine.signatures import verify_signature
from core.system_restore import create_restore_point
from core.windows_utils import program_files

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000


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


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    reason: str
    restore_point_taken: bool
    previous_package_hint: Optional[str] = None  # for rollback.py, Task 6


def _find_7zip() -> Optional[str]:
    """Resolves 7z.exe's real install path the same way cbs_module.py does
    for CBS log cab extraction -- 7-Zip's own installer does not add itself
    to PATH, so a bare '7z' command name never resolves on an ordinary
    machine. None (never raises) if it isn't installed in either the 64-bit
    or 32-bit Program Files."""
    seven_zip = os.path.join(program_files(), "7-Zip", "7z.exe")
    if not os.path.exists(seven_zip):
        seven_zip = os.path.join(program_files(x86=True), "7-Zip", "7z.exe")
    if not os.path.exists(seven_zip):
        return None
    return seven_zip


def _extract_with_7zip(installer_path: str, dest_dir: str) -> bool:
    """Shells out to 7z the same way this codebase already does for CBS
    log cab extraction -- same CREATE_NO_WINDOW discipline as every other
    subprocess call in this module. Not every installer format extracts
    cleanly; a non-zero exit here is a real, expected outcome (NSIS/
    InstallShield/custom wrappers vary), not a bug -- the caller treats a
    False return as 'LIGHT not available for this package', never a crash."""
    seven_zip = _find_7zip()
    if seven_zip is None:
        logger.warning("pipeline: 7-Zip is not installed -- cannot extract %s",
                       installer_path)
        return False
    try:
        proc = subprocess.run(
            [seven_zip, "x", installer_path, f"-o{dest_dir}", "-y"],
            capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("pipeline: 7z extraction failed for %s: %s", installer_path, exc)
        return False


def _find_inf_with_sys(dest_dir: str) -> Optional[str]:
    """Prefers an .inf whose OWN directory also has a same-stem .sys (the
    real driver binary, not some other tool's .inf) -- only falls back to
    the first .inf found in a directory with ANY .sys present if no exact
    stem match exists anywhere, since some real driver packages split the
    two into differently-named files. An inf with no driver binary
    alongside it at all isn't installable and is skipped entirely."""
    fallback = None
    for root, _dirs, files in os.walk(dest_dir):
        infs = [f for f in files if f.lower().endswith(".inf")]
        syss = {f.lower()[:-4] for f in files if f.lower().endswith(".sys")}
        for inf in infs:
            if inf.lower()[:-4] in syss:
                return os.path.join(root, inf)
            if syss and fallback is None:
                fallback = os.path.join(root, inf)
    return fallback


def _run_pnputil_install(inf_path: str) -> tuple:
    try:
        proc = subprocess.run(
            ["pnputil", "/add-driver", inf_path, "/install"],
            capture_output=True, text=True, timeout=120,
            creationflags=CREATE_NO_WINDOW)
        if proc.returncode == 0:
            return True, ""
        return False, f"pnputil exited {proc.returncode}: {proc.stdout or proc.stderr}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("pipeline: pnputil could not be run: %s", exc)
        return False, f"pnputil could not be run: {exc}"


def install_light(installer_path: str, driver) -> InstallResult:
    """The LIGHT install path: extract just INF/SYS from installer_path
    and pnputil-install it -- never runs the vendor's own installer.
    Always takes a restore point first; refuses outright if one can't be
    created. driver: a driver_reader.DriverInfo, used only for the
    restore-point description text."""
    ok, reason = create_restore_point(
        f"Before LIGHT driver update: {driver.device_name}")
    if not ok:
        return InstallResult(ok=False, reason=f"could not take a restore "
                             f"point, refusing to proceed: {reason}",
                             restore_point_taken=False)

    try:
        with tempfile.TemporaryDirectory(prefix="wct_driver_update_") as dest_dir:
            if not _extract_with_7zip(installer_path, dest_dir):
                return InstallResult(ok=False,
                                     reason="LIGHT install is not available for "
                                            "this package -- 7-Zip could not "
                                            "extract it",
                                     restore_point_taken=True)
            inf_path = _find_inf_with_sys(dest_dir)
            if inf_path is None:
                return InstallResult(ok=False,
                                     reason="LIGHT install is not available for "
                                            "this package -- no usable INF/SYS "
                                            "pair was found inside it",
                                     restore_point_taken=True)
            ok, reason = _run_pnputil_install(inf_path)
            if not ok:
                return InstallResult(ok=False, reason=reason, restore_point_taken=True)
    except OSError as exc:
        # A restore point was already taken above -- this only covers the
        # temp directory itself failing (disk full, no permission to create
        # one), which must still come back as a normal refusal, not an
        # exception escaping a function whose whole contract is "always
        # returns an InstallResult."
        logger.warning("pipeline: could not create a temp directory for "
                       "LIGHT extraction: %s", exc)
        return InstallResult(ok=False,
                             reason=f"could not create a temporary directory "
                                    f"for extraction: {exc}",
                             restore_point_taken=True)
    return InstallResult(ok=True, reason="", restore_point_taken=True)

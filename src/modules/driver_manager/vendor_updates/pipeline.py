"""The one safety pipeline every vendor provider and every install mode
(LIGHT, FULL) goes through -- so adding either never means re-deriving
download/verification safety logic. Qt-free: driver_module.py calls into
this from a Worker, never the UI thread.
"""
import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

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


def _download_file(url: str, dest_path: str, headers: Optional[Dict[str, str]] = None) -> bool:
    """Real network download -- mocked in every test above it. Returns
    False on any failure rather than raising, so the caller's reason
    text stays uniform with every other refusal in this pipeline.
    headers: extra request headers (e.g. AMD's CDN requires a Referer
    naming an amd.com page, or it 302s to a "Download Incomplete" page
    instead of the real file -- verified live; NVIDIA's CDN has not been
    observed to need this, so it stays optional)."""
    try:
        request = Request(url, headers=headers or {})
        with urlopen(request, timeout=120) as resp, open(dest_path, "wb") as out:
            shutil.copyfileobj(resp, out)
        return True
    except OSError as exc:
        logger.warning("pipeline: download failed for %s: %s", url, exc)
        return False


def download_and_verify(update, allowed_domains: List[str],
                        cache_dir: Optional[str] = None,
                        extra_headers: Optional[Dict[str, str]] = None) -> DownloadResult:
    """update: a provider.UpdateInfo. Refuses (path=None, a stated reason)
    for: a malformed download URL, a download URL outside allowed_domains
    (checked by host suffix, never substring), a download that fails
    outright, or a downloaded file whose Authenticode signature is not
    VALID and signed by update.installer_signer exactly. Never runs
    anything it downloads -- that's the caller's job, only after this
    returns a real path.

    extra_headers: passed straight to the download request when given
    (see provider.VendorProvider.download_headers) -- omitted entirely
    (rather than passed as {}) when None, so a test that replaces
    _download_file with a plain 2-arg fake keeps working unmodified."""
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
    if extra_headers:
        downloaded = _download_file(update.download_url, dest_path, headers=extra_headers)
    else:
        downloaded = _download_file(update.download_url, dest_path)
    if not downloaded:
        return DownloadResult(path=None, reason="the download itself failed")

    facts = verify_signature(dest_path)
    if not facts.signed:
        _delete_rejected_download(dest_path)
        return DownloadResult(
            path=None,
            reason=f"downloaded file's signature is {facts.status} "
                   f"(expected a VALID signature from {update.installer_signer!r})")
    if facts.signer != update.installer_signer:
        _delete_rejected_download(dest_path)
        return DownloadResult(
            path=None,
            reason=f"downloaded file's signer is {facts.signer!r}, "
                   f"expected {update.installer_signer!r} -- refusing to "
                   f"run something not from the expected vendor")
    return DownloadResult(path=dest_path)


def _delete_rejected_download(dest_path: str) -> None:
    """A file whose signature this pipeline just refused must not linger
    on disk forever -- NVIDIA packages alone run 600-900MB. Best-effort:
    a failure to delete must never mask the real refusal reason the
    caller is about to return."""
    try:
        os.remove(dest_path)
    except OSError as exc:
        logger.warning("pipeline: could not delete rejected download %s: %s",
                       dest_path, exc)


@dataclass(frozen=True)
class InstallResult:
    ok: bool
    reason: str
    restore_point_taken: bool
    previous_package_hint: Optional[str] = None  # for rollback.py, Task 6
    # FULL install only: True when a silent install couldn't be confirmed
    # one way or the other and this instead launched the vendor's own
    # installer window for the user to finish by hand. ok is False in
    # that case too (nothing is confirmed installed yet) -- this field is
    # what lets the caller show "opened the installer for you" rather
    # than a plain failure message for the exact same ok=False.
    handed_off_to_ui: bool = False


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


_VEN_DEV_RE = re.compile(r"PCI\\VEN_[0-9A-Fa-f]{4}&DEV_[0-9A-Fa-f]{4}", re.IGNORECASE)


def _find_inf_for_device(dest_dir: str, driver) -> Optional[str]:
    """The .inf inside dest_dir whose own CONTENT names driver's PCI
    vendor+device id -- the only correct way to pick the right package
    out of a real multi-component vendor installer. Real AMD data: a
    single Adrenalin download bundles 41 .inf files (GPU, audio, camera,
    NPU, chipset...), and the right one for a real RX 7900 XTX
    (u0203731.inf) shares neither a directory NOR a filename stem with
    its own driver binary -- amdkmdag.sys sits one level below it, in
    .\\B026470\\, referenced only inside the INF's own SourceDisksNames
    section (a normal, spec-legal layout pnputil resolves itself; it is
    _find_inf_with_sys's directory-proximity guess that cannot see it).
    Matching on the device id actually being serviced, the same fact
    Windows itself matches on, works regardless of how a package lays
    its files out. None (never raises) if hardware_id has no PCI
    VEN&DEV token to extract, or nothing in the package mentions it --
    callers fall back to the layout-based heuristic in that case, never
    treating this as a hard failure by itself."""
    hardware_id = getattr(driver, "hardware_id", "") or ""
    match = _VEN_DEV_RE.match(hardware_id)
    if match is None:
        return None
    needle = match.group(0).lower().encode("ascii", errors="ignore")
    for root, _dirs, files in os.walk(dest_dir):
        for f in files:
            if not f.lower().endswith(".inf"):
                continue
            inf_path = os.path.join(root, f)
            try:
                with open(inf_path, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                logger.warning("pipeline: could not read %s while matching "
                               "device id: %s", inf_path, exc)
                continue
            if needle in data.lower():
                return inf_path
    return None


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
            # Device-id content match first (correct for a real
            # multi-component package, see _find_inf_for_device); the
            # older directory-proximity heuristic only as a fallback,
            # for a device with no PCI vendor id or a package that
            # never names it explicitly.
            inf_path = _find_inf_for_device(dest_dir, driver)
            if inf_path is None:
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


_SILENT_INSTALL_TIMEOUT_SECONDS = 900  # AMD/NVIDIA packages run 600-900MB;
                                        # a silent install of one is not fast


def _launch_interactive_installer(installer_path: str, reason_prefix: str) -> InstallResult:
    """Starts installer_path's own normal (visible) install wizard and
    returns immediately -- never waits for it, since there is no way to
    know how long a person takes to click through a wizard. ok=False
    because nothing is confirmed installed by the time this returns;
    handed_off_to_ui=True is what tells the caller this is an intentional
    handoff, not a plain failure."""
    try:
        subprocess.Popen([installer_path])
    except OSError as exc:
        logger.warning("pipeline: could not launch %s interactively: %s", installer_path, exc)
        return InstallResult(
            ok=False,
            reason=f"{reason_prefix} -- and could not launch the installer's "
                   f"own window either: {exc}",
            restore_point_taken=True)
    return InstallResult(
        ok=False,
        reason=f"{reason_prefix} -- opened the installer's own window for "
               f"you to finish",
        restore_point_taken=True,
        handed_off_to_ui=True)


def install_full(installer_path: str, driver, provider,
                 allow_interactive_fallback: bool = True) -> InstallResult:
    """The FULL install path: runs the vendor's OWN installer rather than
    extracting just INF/SYS. Always takes a restore point first, same as
    install_light, and refuses outright if one can't be taken.

    provider supplies the vendor-specific mechanism, via two OPTIONAL
    methods (see provider.VendorProvider) -- shared pipeline code stays
    generic, the same way allowed_download_domains/expected_signer keep
    per-vendor facts on the provider rather than here:

    - build_silent_install_args(log_path) -> List[str]: the documented
      silent-install command-line switches for this vendor's installer
      (AMD: "-INSTALL" + a -LOG path, per AMD's own Command Line
      Installation User Guide; NVIDIA: "-s -n", per NVIDIA's own support
      KB). log_path is passed even when a vendor's switches don't use it
      (NVIDIA) -- providers that don't need it simply ignore the arg.
    - silent_install_succeeded(log_path, exit_code) -> Optional[bool]:
      True/False is a confident answer; None means "could not tell" (the
      log never appeared, didn't parse, or the vendor's exit code isn't
      documented as meaningful) and triggers the interactive fallback
      rather than guessing.

    A provider missing either method (no documented silent mechanism
    verified for it yet) goes straight to the interactive installer --
    never guesses at generic-sounding flags that were never confirmed
    against that vendor's own documentation.

    allow_interactive_fallback=False (bulk installs): an inconclusive
    silent result is reported as a skipped device instead of popping up
    a visible installer window mid-batch -- retry that one device
    individually to get the interactive fallback.
    """
    ok, reason = create_restore_point(
        f"Before FULL driver update: {driver.device_name}")
    if not ok:
        return InstallResult(ok=False, reason=f"could not take a restore "
                             f"point, refusing to proceed: {reason}",
                             restore_point_taken=False)

    build_args = getattr(provider, "build_silent_install_args", None)
    check_result = getattr(provider, "silent_install_succeeded", None)
    if build_args is None or check_result is None:
        if not allow_interactive_fallback:
            return InstallResult(
                ok=False,
                reason=f"{provider.vendor_name} has no documented silent-install "
                       f"mechanism -- skipped (retry individually to use the "
                       f"installer's own window)",
                restore_point_taken=True)
        return _launch_interactive_installer(
            installer_path,
            reason_prefix=f"{provider.vendor_name} has no documented "
                          f"silent-install mechanism")

    try:
        with tempfile.TemporaryDirectory(prefix="wct_driver_full_install_") as tmp_dir:
            log_path = os.path.join(tmp_dir, "install.log")
            args = build_args(log_path)
            try:
                proc = subprocess.run(
                    [installer_path] + list(args), timeout=_SILENT_INSTALL_TIMEOUT_SECONDS,
                    capture_output=True, creationflags=CREATE_NO_WINDOW)
                exit_code = proc.returncode
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.warning("pipeline: silent FULL install failed to run for "
                               "%s: %s", installer_path, exc)
                if not allow_interactive_fallback:
                    return InstallResult(
                        ok=False,
                        reason=f"silent install could not be run: {exc} -- skipped",
                        restore_point_taken=True)
                return _launch_interactive_installer(
                    installer_path, reason_prefix=f"silent install could not be run: {exc}")
            succeeded = check_result(log_path, exit_code)
    except OSError as exc:
        logger.warning("pipeline: could not create a temp directory for "
                       "FULL install: %s", exc)
        return InstallResult(ok=False,
                             reason=f"could not create a temporary directory "
                                    f"for the silent install log: {exc}",
                             restore_point_taken=True)

    if succeeded is True:
        return InstallResult(ok=True, reason="", restore_point_taken=True)
    if succeeded is False:
        return InstallResult(ok=False,
                             reason=f"{provider.vendor_name}'s installer reported "
                                    f"failure",
                             restore_point_taken=True)
    # None: inconclusive, never guessed at
    if not allow_interactive_fallback:
        return InstallResult(
            ok=False,
            reason="could not confirm the silent install's result -- skipped "
                   "(retry individually to use the installer's own window)",
            restore_point_taken=True)
    return _launch_interactive_installer(
        installer_path, reason_prefix="could not confirm the silent install's result")
    return InstallResult(ok=True, reason="", restore_point_taken=True)

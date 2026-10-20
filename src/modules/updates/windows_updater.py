import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from core.blocklist import is_blocked

logger = logging.getLogger(__name__)

_RESULT_TEXT = {
    0: "not started", 1: "in progress", 2: "success",
    3: "partial success", 4: "FAILED", 5: "cancelled",
}


@dataclass
class WindowsUpdate:
    kb: str
    title: str
    classification: str
    #: `IUpdate.MaxDownloadSize`, in MB -- an UPPER BOUND, not the delta that
    #: actually downloads. The disk-space preflight uses this worst case.
    size_mb: float
    release_date: str
    identity: object = field(default=None, repr=False)  # IUpdate COM object, stored for install
    is_hidden: bool = False
    #: `IUpdate.MinDownloadSize` -- the floor WUA knows, often 0. Shown as a
    #: range with `size_mb` when it is present and smaller.
    min_size_mb: float = 0.0
    #: `IUpdate.MsrcSeverity` ("Critical"/"Important"/"Moderate"/"Low").
    severity: str = ""
    #: `IUpdate.InstallationBehavior.RebootBehavior` as a string, from the
    #: tagInstallationRebootBehavior enum: 0 never reboots, 1 always requires a
    #: reboot, 2 may request one.
    reboot: str = ""
    #: An AMD display driver this app never installs (`is_held_amd_display_driver`).
    #: A held entry is only ever SHOWN: it carries no COM identity, and
    #: `install_updates_iter` refuses it whatever list it arrives in.
    held: bool = False


#: What every surface says for a held update instead of hiding it.
#: "by WinClientTool": the hold is this app's, not Windows'. Windows Update
#: itself (Settings > Windows Update) is not stopped from offering it.
HELD_TEXT = ("Held by WinClientTool: AMD display driver -- kept on the installed "
             "version on purpose")

#: tagInstallationRebootBehavior, read from wuapi.dll's generated type module.
_REBOOT_TEXT = {"0": "No", "1": "Yes", "2": "May ask"}


def severity_text(u: "WindowsUpdate") -> str:
    return u.severity or "—"


def reboot_text(u: "WindowsUpdate") -> str:
    return _REBOOT_TEXT.get(u.reboot, "—")


def wu_status_text(updates: List["WindowsUpdate"], held: List["WindowsUpdate"]) -> str:
    """The Windows Updates tab's status line. Held AMD display drivers are
    counted ("N held") rather than left to vanish, and never in the pending
    count -- nothing on this tab can install them."""
    severe = sum(1 for u in updates if (u.severity or "").lower() in ("critical", "important"))
    reboot = sum(1 for u in updates if u.reboot in ("1", "2"))
    extra = []
    if severe:
        extra.append(f"{severe} critical/important")
    if reboot:
        extra.append(f"{reboot} need a restart")
    if held:
        extra.append(f"{len(held)} held (AMD display driver, kept on purpose)")
    suffix = (" — " + ", ".join(extra)) if extra else ""
    return f"{len(updates)} pending update(s){suffix}."


def split_scan(payload) -> "tuple[List[WindowsUpdate], List[WindowsUpdate]]":
    """(installable, held) from a tab scan's result: a {"updates", "held"}
    dict, or a bare list (no held information). A held entry that somehow
    reached the installable list is moved out of it."""
    if isinstance(payload, dict):
        updates = list(payload.get("updates") or [])
        held = list(payload.get("held") or [])
    else:
        updates, held = list(payload or []), []
    held += [u for u in updates if u.held]
    return [u for u in updates if not u.held], held


def _com_str(obj, attr: str) -> str:
    try:
        return str(getattr(obj, attr) or "").strip()
    except Exception:
        logger.debug("update attribute %s is unreadable", attr, exc_info=True)
        return ""


def _com_reboot(obj) -> str:
    try:
        return str(int(obj.InstallationBehavior.RebootBehavior))
    except Exception:
        logger.debug("RebootBehavior is unreadable", exc_info=True)
        return ""


def _com_mb(obj, attr: str) -> float:
    """One WUA size attribute in MB, coerced off the COM boundary.

    MaxDownloadSize/MinDownloadSize are DECIMAL in wuapi.idl, so pywin32 hands
    back a decimal.Decimal -- and Decimal/int stays Decimal, which then dies on
    the first `/ 1024.0` downstream. Coerce here, once. An unreadable attribute
    is 0.0, logged, never a crash.
    """
    try:
        return float(getattr(obj, attr)) / (1024 * 1024)
    except Exception:
        logger.debug("update size %s is unreadable", attr, exc_info=True)
        return 0.0


def size_text(u: "WindowsUpdate") -> str:
    """Download size for a table row or a log line.

    `MaxDownloadSize` is the maximum, not the amount that will be downloaded:
    the Defender security-intelligence update here reported 1539 MB, the size
    of its whole bundle, while the delta served is far smaller. Printing that
    bare reads as a broken figure, so it is shown as `<= max`, or as a
    `min-max` range when WUA also reports a smaller minimum.
    """
    if u.size_mb <= 0:
        return "—"
    if u.min_size_mb and u.min_size_mb < u.size_mb:
        return f"{u.min_size_mb:.0f}–{u.size_mb:.0f} MB"
    return f"≤{u.size_mb:.0f} MB"


@dataclass
class InstallResult:
    kb: str
    title: str
    success: bool
    hresult: Optional[int]
    message: str


def fetch_pending_updates(
    include_hidden: bool = False,
    patterns: Optional[List[str]] = None,
    include_drivers: bool = True,
    held_out: Optional[List[WindowsUpdate]] = None,
) -> List[WindowsUpdate]:
    """
    Uses Microsoft.Update.Session COM object to get pending updates.
    Must be called from a COMWorker thread (CoInitialize already done).

    include_hidden: when False (default), excludes updates the user hid.
    patterns: optional blocklist patterns — matching updates are excluded.
    include_drivers: when False, excludes Type='Driver' updates from the
    search criteria itself (the same criteria syntax
    windows_update_driver_check.py already verified live on this machine).
    Windows Update's default search mixes driver-classified updates in with
    everything else, so this is the only place that can honor the Settings
    tab's "Include driver updates from Windows Update" checkbox — that
    checkbox persisted to updates.wu_include_drivers and loaded back into
    the UI, but nothing downstream ever read it: fetch_pending_updates took
    no such parameter, and both the interactive Windows Updates tab and the
    shared run_wu_stage (used by both Run All and the headless --unattended
    runner) called it without one, so unchecking the box changed nothing.

    held_out: optional list that receives every AMD display driver offer the
    hold kept out of the result, as a `held=True` entry WITHOUT a COM identity
    -- so a caller can say "held" instead of letting the update vanish. The
    returned list never contains one, whether or not held_out is passed.
    """
    import win32com.client
    updates = []
    criteria = "IsInstalled=0" if include_hidden else "IsInstalled=0 and IsHidden=0"
    # With drivers excluded, the criteria alone would also hide the held AMD
    # offers -- and drivers are excluded by DEFAULT, so the held rows would
    # never appear. When a caller asks for them, search everything and drop
    # the drivers here instead (measured: 7.6 s for the full search against
    # 1.3 s without drivers). A driver is still never offered for install.
    drop_drivers = not include_drivers and held_out is not None
    if not include_drivers and not drop_drivers:
        criteria += " and Type<>'Driver'"
    try:
        session = win32com.client.Dispatch("Microsoft.Update.Session")
        searcher = session.CreateUpdateSearcher()
        result = searcher.Search(criteria)
        for i in range(result.Updates.Count):
            u = result.Updates.Item(i)
            # KB numbers
            kb_list = [u.KBArticleIDs.Item(j) for j in range(u.KBArticleIDs.Count)]
            kb = ", ".join(f"KB{k}" for k in kb_list) if kb_list else "N/A"
            # Classification
            cats = [u.Categories.Item(j).Name for j in range(u.Categories.Count)]
            classification = cats[0] if cats else "Unknown"
            # Size: both bounds, coerced off the DECIMAL COM boundary.
            size_mb = _com_mb(u, "MaxDownloadSize")
            min_size_mb = _com_mb(u, "MinDownloadSize")
            severity = _com_str(u, "MsrcSeverity")
            reboot = _com_reboot(u)
            # Date
            try:
                release_date = str(u.LastDeploymentChangeTime)[:10]
            except Exception:
                release_date = "Unknown"
            try:
                hidden = bool(u.IsHidden)
            except Exception:
                hidden = False

            if patterns and is_blocked(u.Title, kb, patterns):
                continue
            if drop_drivers and not is_held_amd_display_driver(u):
                kind = _update_type(u)
                if kind != 1:     # utSoftware; a driver, or a type we could not read
                    if kind is None:
                        logger.warning("Windows Update: %r left out -- its update type "
                                       "could not be read and driver updates are off",
                                       u.Title)
                    continue
            if is_held_amd_display_driver(u):
                logger.warning("Windows Update: held back %r -- AMD display drivers are never "
                               "installed by this app (the RX 7900 XTX stays on its 2022 driver)",
                               u.Title)
                if held_out is not None:
                    held_out.append(WindowsUpdate(
                        kb=kb, title=u.Title, classification=classification,
                        size_mb=size_mb, min_size_mb=min_size_mb,
                        severity=severity, reboot=reboot,
                        release_date=release_date, identity=None,
                        is_hidden=hidden, held=True,
                    ))
                continue

            updates.append(WindowsUpdate(
                kb=kb, title=u.Title, classification=classification,
                size_mb=size_mb, min_size_mb=min_size_mb,
                severity=severity, reboot=reboot,
                release_date=release_date, identity=u, is_hidden=hidden,
            ))
    except Exception as e:
        raise RuntimeError(f"Failed to query Windows Updates: {_explain(e)}") from e
    return updates


_AMD_NAMES = ("advanced micro devices", "amd", "ati technologies")


def _update_type(u) -> Optional[int]:
    """IUpdate.Type (1 software, 2 driver), or None when it cannot be read."""
    try:
        return int(u.Type)
    except Exception:                                    # late-bound COM refused the field
        return None


def is_held_amd_display_driver(u) -> bool:
    """A Windows Update DRIVER offer for an AMD display adapter. These are
    never installed: the owner keeps the RX 7900 XTX on a 2022 driver on
    purpose (newer ones crash games here), and one AMD display package can
    replace another's components. Judged from the IWindowsDriverUpdate
    fields when the object exposes them, else from the title Windows Update
    gives drivers ("Advanced Micro Devices, Inc. - Display - 31.0...")."""
    # An unreadable Type is not "not a driver": the title and hardware-id
    # checks below still decide, so a refused field never lets one through.
    kind = _update_type(u)
    if kind is not None and kind != 2:                   # utDriver
        return False
    try:
        cls = str(getattr(u, "DriverClass", "") or "").lower()
        maker = str(getattr(u, "DriverManufacturer", "") or "").lower()
    except Exception:                                    # late-bound COM refused the field
        cls = maker = ""
    try:
        title = str(getattr(u, "Title", "") or "").lower()
    except Exception:                                    # late-bound COM refused the field
        title = ""
    # Either source is enough: metadata can be partial (a class with no
    # maker), and a hold missed here installs the driver the user refuses.
    by_metadata = cls == "display" and any(n in maker.split() or maker.startswith(n)
                                           for n in _AMD_NAMES)
    by_title = "display" in title and any(title.startswith(n) or f"{n} " in title
                                          for n in _AMD_NAMES)
    # PCI vendor 1002 is AMD/ATI graphics (CPUs and chipsets are 1022): any
    # package for the GPU -- display, extension, HDMI audio -- in any language.
    try:
        hwid = str(getattr(u, "DriverHardwareID", "") or "").upper()
    except Exception:                                    # late-bound COM refused the field
        hwid = ""
    return by_metadata or by_title or "VEN_1002" in hwid


def _explain(exc: Exception) -> str:
    """Turn a COM failure into something a person can act on.

    A com_error's str() is a tuple of two HRESULTs in signed decimal, the
    first of which (DISPATCH_E_EXCEPTION) says nothing at all. Anything that
    is not a COM error keeps its own message.
    """
    from core.wu_error_codes import decode_wu_error, hresult_from_com_error

    hr = hresult_from_com_error(exc)
    if hr is None:
        return str(exc)
    return decode_wu_error(hr)


def hide_update(update: WindowsUpdate) -> None:
    """Hide a Windows Update so it stops being offered (WUA IsHidden=True).
    Must be called from a COMWorker thread, with a `.identity` from a scan
    made in the *same* runspace (COM objects aren't valid across threads)."""
    if update.identity is None:
        raise RuntimeError("Update has no live COM identity — re-scan first.")
    update.identity.IsHidden = True


def install_updates_iter(
    updates: List[WindowsUpdate],
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    output_cb: Optional[Callable[[str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> List[InstallResult]:
    """Download and install updates one at a time (own downloader/installer call
    per item) so callers get real per-item progress and isolated pass/fail
    instead of one aggregate all-or-nothing result. Must run in a COMWorker.

    progress_cb(done_count, total_count, current_title) — called before each item.
    output_cb(line) — streamed status lines.
    is_cancelled() — checked between items; installation of an in-flight item
    is not interrupted mid-way (matches Worker.cancel() semantics elsewhere).
    """
    import win32com.client
    from core.wu_error_codes import decode_wu_error, hresult_from_com_error

    def _log(line: str) -> None:
        if output_cb:
            output_cb(line)

    session = win32com.client.Dispatch("Microsoft.Update.Session")
    downloader = session.CreateUpdateDownloader()
    installer = session.CreateUpdateInstaller()
    total = len(updates)
    results: List[InstallResult] = []

    for i, u in enumerate(updates):
        if is_cancelled and is_cancelled():
            _log("Cancelled.")
            break
        if progress_cb:
            progress_cb(i, total, u.title)
        _log(f"[{i + 1}/{total}] {u.title}  ({size_text(u)})")

        # Defence in depth: the scan already keeps these out, but a held driver
        # is never installed whatever list it arrives in.
        try:
            held = u.held or (u.identity is not None and is_held_amd_display_driver(u.identity))
        except Exception as e:
            # A COM refusal here used to escape the loop and lose every result
            # already collected. Unsure means not installed.
            detail = _explain(e)
            _log(f"  not installed: could not confirm it is not a held AMD driver ({detail})")
            results.append(InstallResult(u.kb, u.title, False, hresult_from_com_error(e),
                                         "not installed: the AMD driver hold could not be "
                                         f"checked ({detail})"))
            continue
        if held:
            _log(f"  {HELD_TEXT}")
            continue

        if u.identity is None:
            results.append(InstallResult(u.kb, u.title, False, None, "stale scan — no COM identity, re-scan first"))
            continue

        coll = win32com.client.Dispatch("Microsoft.Update.UpdateColl")
        coll.Add(u.identity)
        try:
            if not u.identity.EulaAccepted:
                u.identity.AcceptEula()
        except Exception:
            logger.warning("Could not check/accept EULA for %s", u.title, exc_info=True)

        try:
            if not u.identity.IsDownloaded:
                downloader.Updates = coll
                dl_result = downloader.Download()
                dl_code = int(dl_result.ResultCode)
                _log(f"  download: {_RESULT_TEXT.get(dl_code, dl_code)}")
                if dl_code != 2:
                    msg = f"download {_RESULT_TEXT.get(dl_code, dl_code)} — {decode_wu_error(dl_result.HResult)}"
                    results.append(InstallResult(u.kb, u.title, False, dl_result.HResult, msg))
                    continue
            else:
                _log("  already downloaded")

            installer.Updates = coll
            install_result = installer.Install()
            code = int(install_result.ResultCode)
            success = code == 2
            msg = _RESULT_TEXT.get(code, str(code))
            if not success:
                msg += " — " + decode_wu_error(install_result.HResult)
            _log(f"  install: {msg}")
            results.append(InstallResult(u.kb, u.title, success, install_result.HResult, msg))
        except Exception as e:
            detail = _explain(e)
            _log(f"  error: {detail}")
            results.append(InstallResult(u.kb, u.title, False,
                                         hresult_from_com_error(e), detail))

    if progress_cb:
        progress_cb(total, total, "")
    return results

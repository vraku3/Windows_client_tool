import logging
import math
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
    #: actually downloads. See download_size_mb for the disk-space estimate.
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
    pending_restart: bool = False
    downloaded: bool = False
    #: `IUpdate.KBArticleIDs`, without the "KB" prefix.
    kb_ids: List[str] = field(default_factory=list)
    support_url: str = ""
    more_info_urls: List[str] = field(default_factory=list)
    description: str = ""
    #: `IUpdate.Type` is `utDriver` (2). A driver is never installed by an
    #: automatic run; it is reviewed and approved explicitly (Driver Manager
    #: routing), even when optional updates are otherwise allowed.
    driver: bool = False
    driver_class: str = ""
    driver_manufacturer: str = ""
    #: `IUpdate.SecurityBulletinIDs` (e.g. "MS17-010"). WUA may publish none.
    security_bulletins: List[str] = field(default_factory=list)
    #: `IUpdate.CveIds` when the WUA build exposes it. Usually absent: WUA
    #: publishes bulletin IDs, not CVEs, for most updates.
    cve_ids: List[str] = field(default_factory=list)


#: What every surface says for a held update instead of hiding it.
#: "by WinClientTool": the hold is this app's, not Windows'. Windows Update
#: itself (Settings > Windows Update) is not stopped from offering it.
HELD_TEXT = ("Held by WinClientTool: AMD display driver -- kept on the installed "
             "version on purpose")
RESTART_TEXT = "Installed -- restart to finish"


def update_status_text(u: WindowsUpdate) -> str:
    return RESTART_TEXT if u.pending_restart else (HELD_TEXT if u.held else "")


def _com_bool(obj, attr: str) -> Optional[bool]:
    try:
        value = getattr(obj, attr)
        return bool(value) if value is not None else None
    except Exception:
        logger.debug("update attribute %s is unreadable", attr, exc_info=True)
        return None


def _pending_restart_ids(searcher) -> set:
    """Current-boot installation identities while Windows needs a restart.

    A KB can cover unrelated offers or many Defender revisions; only the
    exact UpdateID and installation operation associate history with an offer.
    """
    identities = set()
    try:
        from core.pending_reboot import check
        from psutil import boot_time
        state = check()
        if not state.needed:
            return set()
        history = searcher.QueryHistory(0, min(100, searcher.GetTotalHistoryCount()))
        boot = boot_time()
        for i in range(history.Count):
            try:
                entry = history.Item(i)
                if (int(entry.Operation) == 1 and int(entry.ResultCode) == 2
                        and entry.Date.timestamp() >= boot):
                    update_id = _com_str(entry.UpdateIdentity, "UpdateID")
                    if update_id:
                        identities.add(update_id.casefold())
            except Exception:
                logger.debug("Pending installation history entry unreadable", exc_info=True)
    except Exception:
        logger.debug("Pending installation history unreadable; using per-update flags", exc_info=True)
    return identities


def _awaiting_restart(identity, pending_ids: set) -> bool:
    if _com_bool(identity, "RebootRequired") is True:
        return True
    try:
        update_id = _com_str(identity.Identity, "UpdateID")
        return bool(update_id) and update_id.casefold() in pending_ids
    except Exception:
        logger.debug("Offer identity unreadable; treating it as installable", exc_info=True)
        return False

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
    waiting = sum(u.pending_restart for u in updates)
    if waiting:
        extra.append(f"{waiting} installed -- restart to finish")
    suffix = (" — " + ", ".join(extra)) if extra else ""
    return f"{len(updates) - waiting} pending update(s){suffix}."


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


def _com_urls(obj) -> List[str]:
    """`IUpdate.MoreInfoUrls` as a plain list; empty when refused."""
    try:
        collection = obj.MoreInfoUrls
        return [str(collection.Item(i)).strip() for i in range(collection.Count)
                if str(collection.Item(i)).strip()]
    except Exception:
        logger.debug("MoreInfoUrls is unreadable", exc_info=True)
        return []


def _com_strings(obj, attr: str) -> List[str]:
    """One COM string collection as a plain list; empty when refused/absent.

    Used for `SecurityBulletinIDs` / `CveIds`: a build that does not expose
    the property, or refuses it, must read as "not published", never as a
    fabricated identifier.
    """
    try:
        collection = getattr(obj, attr)
        return [str(collection.Item(i)).strip() for i in range(collection.Count)
                if str(collection.Item(i)).strip()]
    except Exception:
        logger.debug("update attribute %s is unreadable", attr, exc_info=True)
        return []


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
    if u.downloaded:
        return "already downloaded"
    if u.size_mb > 10 * 1024:
        return ("size not known in advance (Windows Update reports up to "
                f"{u.size_mb / 1024:.0f} GB for the whole package set)")
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
    reboot_required: bool = False
    result_code: Optional[int] = None

    @property
    def pending_restart(self) -> bool:
        return self.reboot_required and self.result_code in (None, 2)


def download_size_mb(updates: List[WindowsUpdate], warn: Callable[[str], None]) -> float:
    """Estimate download space, keeping WUA's enormous bundle bounds out.

    The normal install-space floor still applies in check_wu_preflight.
    """
    total = 0.0
    for update in updates:
        if update.downloaded or _com_bool(update.identity, "IsDownloaded") is True:
            continue
        size = update.size_mb
        if size > 10 * 1024:
            try:
                size = float(update.identity.MinDownloadSize) / (1024 * 1024)
            except Exception:
                size = update.min_size_mb
            size = max(size if math.isfinite(size) else 0, 10 * 1024)
            warn(f"Windows Update: {update.title}: size uncertain; budgeting {size:.0f} MB")
        if size is None or not math.isfinite(size) or size < 0 or (size == 0 and update.size_mb == 0):
            warn(f"Windows Update: {update.title}: download size unknown; checking minimum free space")
            continue
        total += size
    return total


def fetch_pending_updates(
    include_hidden: bool = False,
    patterns: Optional[List[str]] = None,
    include_drivers: bool = True,
    held_out: Optional[List[WindowsUpdate]] = None,
    excluded_out: Optional[List[WindowsUpdate]] = None,
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

    excluded_out: optional display-only offers suppressed by patterns, without
    COM identities. These offers are never returned for installation.
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
        pending_ids = _pending_restart_ids(searcher)
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
            kb_ids = [str(k) for k in kb_list]
            support_url = _com_str(u, "SupportUrl")
            more_info = _com_urls(u)
            description = _com_str(u, "Description")
            kind = _update_type(u)
            driver = kind == 2                              # utDriver
            driver_class = _com_str(u, "DriverClass") if driver else ""
            driver_manufacturer = _com_str(u, "DriverManufacturer") if driver else ""
            security_bulletins = _com_strings(u, "SecurityBulletinIDs")
            cve_ids = _com_strings(u, "CveIds")

            if is_blocked(u.Title, kb, patterns):
                if excluded_out is not None:
                    excluded_out.append(WindowsUpdate(
                        kb=kb, title=u.Title, classification=classification,
                        size_mb=size_mb, min_size_mb=min_size_mb,
                        severity=severity, reboot=reboot,
                        release_date=release_date, identity=None, is_hidden=hidden,
                        kb_ids=kb_ids, support_url=support_url,
                        more_info_urls=more_info, description=description,
                        driver=driver, driver_class=driver_class,
                        driver_manufacturer=driver_manufacturer,
                        security_bulletins=security_bulletins, cve_ids=cve_ids,
                    ))
                continue
            if drop_drivers and not is_held_amd_display_driver(u):
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
                        kb_ids=kb_ids, support_url=support_url,
                        more_info_urls=more_info, description=description,
                        driver=driver, driver_class=driver_class,
                        driver_manufacturer=driver_manufacturer,
                        security_bulletins=security_bulletins, cve_ids=cve_ids,
                    ))
                continue

            updates.append(WindowsUpdate(
                kb=kb, title=u.Title, classification=classification,
                size_mb=size_mb, min_size_mb=min_size_mb,
                severity=severity, reboot=reboot,
                release_date=release_date, identity=u, is_hidden=hidden,
                pending_restart=_awaiting_restart(u, pending_ids),
                downloaded=_com_bool(u, "IsDownloaded") is True,
                kb_ids=kb_ids, support_url=support_url,
                more_info_urls=more_info, description=description,
                driver=driver, driver_class=driver_class,
                driver_manufacturer=driver_manufacturer,
                security_bulletins=security_bulletins, cve_ids=cve_ids,
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


def uninstall_update(kb: str, output_cb: Optional[Callable[[str], None]] = None,
                     is_cancelled: Optional[Callable[[], bool]] = None) -> InstallResult:
    """Remove one installed KB through WUA, after asking Windows if it can.

    Must run on a COM-initialised thread. Windows may refuse (`CanUninstall`
    False, or the uninstaller itself reports failure); nothing here retries or
    forces it. The caller is responsible for the restore point and confirmation.
    """
    import win32com.client

    emit = output_cb or (lambda line: None)
    cancel = is_cancelled or (lambda: False)
    kb = (kb or "").strip().upper()
    if not kb.startswith("KB"):
        kb = f"KB{kb}"
    number = kb[2:]
    emit(f"Searching installed software updates for {kb}...")
    session = win32com.client.Dispatch("Microsoft.Update.Session")
    searcher = session.CreateUpdateSearcher()
    try:
        found = searcher.Search(
            f"IsInstalled=1 AND Type='Software' AND KBArticleIDs='{number}'")
        candidates = [found.Updates.Item(i) for i in range(found.Updates.Count)]
    except Exception as e:
        return InstallResult(kb, kb, False, None,
                             f"Could not list installed updates: {e}")
    if not candidates:
        return InstallResult(kb, kb, False, None,
                             "Not found among installed software updates "
                             "(superseded, removed, or installed by another channel).")
    target = candidates[0]
    title = str(getattr(target, "Title", "") or kb)
    can_uninstall = None
    try:
        can_uninstall = bool(target.UninstallationBehavior.CanUninstall)
    except Exception:
        logger.debug("UninstallationBehavior is unreadable", exc_info=True)
    if can_uninstall is False:
        return InstallResult(kb, title, False, None,
                             "Windows reports this update cannot be uninstalled.")
    if cancel():
        return InstallResult(kb, title, False, None, "Cancelled before starting.")
    emit(f"Uninstalling {title}...")
    try:
        collection = win32com.client.Dispatch("Microsoft.Update.UpdateColl")
        collection.Add(target)
        installer = session.CreateUpdateInstaller()
        installer.Updates = collection
        outcome = installer.Uninstall()
    except Exception as e:
        return InstallResult(kb, title, False, None, f"Uninstall failed: {e}")
    code = int(outcome.ResultCode)
    message = {2: "Uninstalled", 3: "Uninstalled (with warnings)",
               4: "Uninstall failed", 5: "Uninstall cancelled"}.get(code, f"Result code {code}")
    return InstallResult(kb, title, code in (2, 3), int(outcome.HResult or 0),
                         message, bool(outcome.RebootRequired), code)


def install_updates_iter(
    updates: List[WindowsUpdate],
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    output_cb: Optional[Callable[[str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    result_cb: Optional[Callable[[InstallResult], None]] = None,
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

    def _result(result):
        results.append(result)
        if result_cb:
            result_cb(result)

    try:
        pending_ids = _pending_restart_ids(session.CreateUpdateSearcher())
    except Exception:
        logger.debug("Pending installation searcher unreadable; using per-update flags", exc_info=True)
        pending_ids = set()

    for i, u in enumerate(updates):
        if is_cancelled and is_cancelled():
            _log("Cancelled.")
            break
        if progress_cb:
            progress_cb(i, total, u.title)
        u.downloaded = u.downloaded or _com_bool(u.identity, "IsDownloaded") is True
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
            _result(InstallResult(u.kb, u.title, False, hresult_from_com_error(e),
                                         "not installed: the AMD driver hold could not be "
                                         f"checked ({detail})"))
            continue
        if held:
            _log(f"  {HELD_TEXT}")
            continue

        if u.pending_restart or _awaiting_restart(u.identity, pending_ids):
            _log(f"  {RESTART_TEXT}")
            _result(InstallResult(u.kb, u.title, False, None, RESTART_TEXT, True))
            continue

        if u.identity is None:
            _result(InstallResult(u.kb, u.title, False, None, "stale scan — no COM identity, re-scan first"))
            continue

        coll = win32com.client.Dispatch("Microsoft.Update.UpdateColl")
        coll.Add(u.identity)
        try:
            if not u.identity.EulaAccepted:
                u.identity.AcceptEula()
        except Exception:
            logger.warning("Could not check/accept EULA for %s", u.title, exc_info=True)

        try:
            if _com_bool(u.identity, "IsDownloaded") is not True:
                downloader.Updates = coll
                dl_result = downloader.Download()
                dl_code = int(dl_result.ResultCode)
                _log(f"  download: {_RESULT_TEXT.get(dl_code, dl_code)}")
                if dl_code != 2:
                    msg = f"download {_RESULT_TEXT.get(dl_code, dl_code)} — {decode_wu_error(dl_result.HResult)}"
                    _result(InstallResult(u.kb, u.title, False, dl_result.HResult, msg))
                    continue
            else:
                _log("  already downloaded")

            installer.Updates = coll
            install_result = installer.Install()
            code = int(install_result.ResultCode)
            reboot_required = _com_bool(install_result, "RebootRequired") is True
            try:
                reboot_required = (_com_bool(install_result.GetUpdateResult(0), "RebootRequired") is True
                                   or reboot_required)
            except Exception:
                logger.debug("Per-update install result unreadable", exc_info=True)
            success = code == 2 and not reboot_required
            msg = {2: "installed -- restart to finish" if reboot_required else "success",
                   3: "installed with errors" + (" -- restart to finish" if reboot_required else ""),
                   4: "failed", 5: "aborted"}.get(code, _RESULT_TEXT.get(code, str(code)))
            if code != 2:
                msg += " — " + decode_wu_error(install_result.HResult)
                if reboot_required and code != 3:
                    msg += "; a restart is also required"
            _log(f"  install: {msg}")
            _result(InstallResult(u.kb, u.title, success, install_result.HResult, msg,
                                         reboot_required, code))
        except Exception as e:
            detail = _explain(e)
            _log(f"  error: {detail}")
            _result(InstallResult(u.kb, u.title, False,
                                         hresult_from_com_error(e), detail))

    if progress_cb:
        progress_cb(total, total, "")
    return results


@dataclass
class DownloadResult:
    kb: str
    title: str
    success: bool
    message: str


def download_updates_iter(
    updates: List[WindowsUpdate],
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    output_cb: Optional[Callable[[str], None]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
    result_cb: Optional[Callable[[DownloadResult], None]] = None,
) -> List[DownloadResult]:
    """Download updates now and leave installation for a later, explicit step.

    This is the split the WU tab exposes as "Download": bytes are fetched into
    WUA's own cache (nothing is installed, no restart is requested), then
    `install_updates_iter` reuses them because `IsDownloaded` is true. Must run
    in a COMWorker. Cancellation is checked between items; a download already
    in flight is allowed to finish, matching install semantics.
    """
    import win32com.client
    from core.wu_error_codes import decode_wu_error

    def _log(line: str) -> None:
        if output_cb:
            output_cb(line)

    session = win32com.client.Dispatch("Microsoft.Update.Session")
    downloader = session.CreateUpdateDownloader()
    total = len(updates)
    results: List[DownloadResult] = []

    def _result(result: DownloadResult) -> None:
        results.append(result)
        if result_cb:
            result_cb(result)

    for i, u in enumerate(updates):
        if is_cancelled and is_cancelled():
            _log("Cancelled.")
            break
        if progress_cb:
            progress_cb(i, total, u.title)
        _log(f"[{i + 1}/{total}] {u.title}  ({size_text(u)})")

        if u.held:
            _log(f"  {HELD_TEXT}")
            _result(DownloadResult(u.kb, u.title, False, HELD_TEXT))
            continue
        if u.identity is None:
            message = "stale scan - no COM identity, re-scan first"
            _result(DownloadResult(u.kb, u.title, False, message))
            continue
        try:
            if _com_bool(u.identity, "IsDownloaded") is True:
                u.downloaded = True
                _log("  already downloaded")
                _result(DownloadResult(u.kb, u.title, True, "already downloaded"))
                continue
        except Exception:
            logger.debug("IsDownloaded is unreadable for %s", u.title, exc_info=True)

        coll = win32com.client.Dispatch("Microsoft.Update.UpdateColl")
        coll.Add(u.identity)
        try:
            if not u.identity.EulaAccepted:
                u.identity.AcceptEula()
        except Exception:
            logger.warning("Could not check/accept EULA for %s", u.title, exc_info=True)

        try:
            downloader.Updates = coll
            dl_result = downloader.Download()
            code = int(dl_result.ResultCode)
            if code == 2:
                u.downloaded = True
                _log("  download: success")
                _result(DownloadResult(u.kb, u.title, True, "downloaded"))
            else:
                message = (f"download {_RESULT_TEXT.get(code, code)} - "
                           f"{decode_wu_error(dl_result.HResult)}")
                _log(f"  {message}")
                _result(DownloadResult(u.kb, u.title, False, message))
        except Exception as e:
            detail = _explain(e)
            _log(f"  error: {detail}")
            _result(DownloadResult(u.kb, u.title, False, detail))

    if progress_cb:
        progress_cb(total, total, "")
    return results

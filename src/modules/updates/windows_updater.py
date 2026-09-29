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
    size_mb: float
    release_date: str
    identity: object = field(default=None, repr=False)  # IUpdate COM object, stored for install
    is_hidden: bool = False


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
    """
    import win32com.client
    updates = []
    criteria = "IsInstalled=0" if include_hidden else "IsInstalled=0 and IsHidden=0"
    if not include_drivers:
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
            # Size. MaxDownloadSize is a DECIMAL in wuapi.idl, so pywin32
            # hands back a decimal.Decimal — and Decimal/int stays Decimal,
            # which then dies on the first `/ 1024.0` downstream. Coerce here,
            # at the boundary, so nothing past this point ever sees one.
            try:
                size_mb = float(u.MaxDownloadSize) / (1024 * 1024)
            except Exception:
                size_mb = 0.0
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

            updates.append(WindowsUpdate(
                kb=kb, title=u.Title, classification=classification,
                size_mb=size_mb, release_date=release_date, identity=u,
                is_hidden=hidden,
            ))
    except Exception as e:
        raise RuntimeError(f"Failed to query Windows Updates: {_explain(e)}") from e
    return updates


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
        _log(f"[{i + 1}/{total}] {u.title}  ({u.size_mb:.1f} MB)")

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

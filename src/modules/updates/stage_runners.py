"""Shared maintenance-stage implementations used by both the threaded "Run
All" tab (run_all_tab.py) and the headless --unattended runner
(unattended_runner.py), so the two entry points can't drift apart. Each stage
function takes an `app` (for config), a `log(line)` callback, and an
`is_cancelled()` callable — the threaded caller passes `worker.is_cancelled`,
the headless caller passes `lambda: False`.

WU/Store stages use win32com and must be invoked from a COM-initialized
thread (COMWorker on the GUI side, explicit pythoncom.CoInitialize() in the
unattended runner).
"""
import logging
from core.formatting import format_size as _fmt_size
from typing import Callable

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]


def run_wu_stage(app, log: LogFn, is_cancelled: CancelFn) -> dict:
    """Scan + install pending Windows Updates. Returns results + reboot flag."""
    from modules.updates.windows_updater import fetch_pending_updates, install_updates_iter

    patterns = app.config.get("updates.blocklist_patterns", [])
    include_hidden = app.config.get("updates.wu_include_hidden", False)
    log("Windows Update: scanning...")
    updates = fetch_pending_updates(include_hidden=include_hidden, patterns=patterns)
    if not updates:
        log("Windows Update: system is up to date.")
        return {"results": [], "reboot_required": False, "installed_count": 0}
    log(f"Windows Update: {len(updates)} update(s) found.")

    from core.disk_space import check_wu_preflight
    reason = check_wu_preflight(sum(u.size_mb for u in updates))
    if reason:
        log(f"Windows Update: skipped — {reason}")
        return {"results": [], "reboot_required": False, "installed_count": 0}

    if app.config.get("updates.restore_point_before_install", True):
        from core.system_restore import create_restore_point
        log("Creating a System Restore point before installing updates...")
        ok, _out = create_restore_point("Windows Client Tool — before WU install")
        log("Restore point created." if ok else "Restore point creation failed or was skipped.")

    def _progress(done, total, title):
        if title:
            log(f"Installing ({done + 1}/{total}): {title}")

    results = install_updates_iter(
        updates, progress_cb=_progress, output_cb=log, is_cancelled=is_cancelled
    )
    results_dicts = [
        {"kb": r.kb, "title": r.title, "success": r.success, "message": r.message}
        for r in results
    ]
    installed = sum(1 for r in results if r.success)
    log(f"Windows Update: {installed}/{len(results)} installed.")

    reboot_required = False
    try:
        from core.windows_utils import is_reboot_pending
        reboot_required = is_reboot_pending()
    except Exception:
        logger.warning("Could not check reboot-pending state", exc_info=True)

    return {"results": results_dicts, "reboot_required": reboot_required, "installed_count": installed}


def run_winget_stage(app, log: LogFn, is_cancelled: CancelFn) -> dict:
    """Scan + update all winget packages one at a time, then verify."""
    from modules.updates.winget_updater import fetch_updates, install_update

    patterns = app.config.get("updates.blocklist_patterns", [])
    log("winget: scanning for updates...")
    before = fetch_updates(patterns=patterns)
    if not before:
        log("winget: all packages up to date.")
        return {"results": []}
    log(f"winget: {len(before)} package(s) have updates.")

    before_map = {u.winget_id: u.installed_version for u in before}
    results = []
    for i, u in enumerate(before):
        if is_cancelled():
            log("Cancelled.")
            break
        log(f"[{i + 1}/{len(before)}] Updating {u.name} ({u.winget_id})...")
        install_update(u.winget_id, output_cb=log)
        results.append({"name": u.name, "id": u.winget_id, "before": before_map.get(u.winget_id, "")})

    if results and app.config.get("updates.verify_after_update", True):
        log("winget: verifying installed versions...")
        after_ids = {a.winget_id for a in fetch_updates(patterns=patterns)}
        for r in results:
            confirmed = r["id"] not in after_ids
            r["confirmed"] = confirmed
            r["after"] = "updated" if confirmed else "unchanged"
        changed = sum(1 for r in results if r["confirmed"])
        log(f"winget: {changed}/{len(results)} confirmed updated.")

    return {"results": results}


def run_store_stage(app, log: LogFn, is_cancelled: CancelFn) -> dict:
    """Trigger a Microsoft Store update scan (fire-and-forget — Store downloads
    updates itself once triggered, there's nothing further to wait for)."""
    from core.mdm_store_trigger import trigger_store_scan
    count = trigger_store_scan(output_cb=log)
    return {"triggered": count}


def run_cleanup_safe_stage(app, log: LogFn, is_cancelled: CancelFn) -> dict:
    """Scan and delete every 'safe'-tagged item across the cleanup module's
    category groups — reuses the exact same logic as Cleanup's Overview tab
    'Clean All Safe' rather than re-implementing it."""
    from modules.cleanup.cleanup_scanner import _OV_GROUPS
    from modules.cleanup import cleanup_scanner as cs

    log("Cleanup: scanning safe categories...")
    all_items = []
    needs_wu = False
    total = 0
    for group_name, scanners in _OV_GROUPS:
        if scanners is None or is_cancelled():
            continue
        for fn in scanners:
            try:
                r = fn(min_age_days=0)
                for item in r.items:
                    if item.safety == "safe":
                        item.selected = True
                        all_items.append(item)
                        total += item.size
                if fn == cs.scan_wu_cache:
                    needs_wu = True
            except Exception as e:
                logger.warning("Cleanup scan %s failed: %s", getattr(fn, "__name__", fn), e)

    if not all_items:
        log("Cleanup: nothing to clean.")
        return {"freed": 0, "deleted": 0}

    log(f"Cleanup: deleting {len(all_items)} item(s), {_fmt_size(total)}...")
    deleted, errors = cs.delete_items(all_items, stop_wuauserv=needs_wu)
    log(
        f"Cleanup: {deleted} item(s) deleted"
        + (f", {errors} error(s)" if errors else "")
        + f" — {_fmt_size(total)} freed."
    )
    return {"freed": total, "deleted": deleted}


def run_dism_stage(app, log: LogFn, is_cancelled: CancelFn) -> dict:
    """DISM /StartComponentCleanup — the slow WinSxS cleanup, not the fast
    /AnalyzeComponentStore preview (that one lives in the Cleanup module)."""
    import subprocess
    log("DISM: running StartComponentCleanup (this can take several minutes)...")
    proc = subprocess.run(
        ["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup"],
        capture_output=True, text=True, timeout=1800,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    log(output or "(no output)")
    return {"output": output}


STAGE_RUNNERS = {
    "wu": run_wu_stage,
    "winget": run_winget_stage,
    "store": run_store_stage,
    "cleanup": run_cleanup_safe_stage,
    "dism": run_dism_stage,
}

STAGE_LABELS = {
    "wu": "Windows Update",
    "winget": "winget",
    "store": "Microsoft Store",
    "cleanup": "Cleanup (safe)",
    "dism": "DISM WinSxS cleanup",
}


def normalize_stage_data(data: dict) -> dict:
    """Flatten the per-stage result dicts (keyed by stage name) into the flat
    shape report_generator.render_update_report_html and history_writer.append_run
    expect. Shared by run_all_tab.py (threaded) and unattended_runner.py
    (headless) so both entry points build reports/history identically."""
    wu = data.get("wu", {}) or {}
    winget = data.get("winget", {}) or {}
    store = data.get("store", {}) or {}
    cleanup = data.get("cleanup", {}) or {}
    dism = data.get("dism", {}) or {}
    return {
        "wu_results": wu.get("results", []),
        "wu_installed": wu.get("installed_count", 0),
        "winget_results": winget.get("results", []),
        "store_triggered": store.get("triggered", 0),
        "cleanup_freed": cleanup.get("freed", 0),
        "cleanup_deleted": cleanup.get("deleted", 0),
        "dism_output": dism.get("output"),
    }

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
import os
from core.formatting import format_size as _fmt_size
from typing import Callable

logger = logging.getLogger(__name__)

LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]


def run_wu_stage(app, log: LogFn, is_cancelled: CancelFn) -> dict:
    """Scan + install pending Windows Updates. Returns results + reboot flag."""
    from modules.updates.windows_updater import (
        fetch_pending_updates, install_updates_iter, download_size_mb, HELD_TEXT,
    )

    patterns = app.config.get("updates.blocklist_patterns", [])
    include_hidden = app.config.get("updates.wu_include_hidden", False)
    include_drivers = app.config.get("updates.wu_include_drivers", False)
    log("Windows Update: scanning...")
    held_found: list = []
    updates = fetch_pending_updates(
        include_hidden=include_hidden, patterns=patterns, include_drivers=include_drivers,
        held_out=held_found,
    )
    # Held updates are reported, never installed: they are not in `updates`.
    held = [{"kb": u.kb, "title": u.title, "state": "held", "message": HELD_TEXT}
            for u in held_found]
    for h in held:
        log(f"Windows Update: held -- {h['title']} ({HELD_TEXT})")
    waiting = [{"kb": u.kb, "title": u.title, "success": False, "reboot_required": True,
                "message": "Installed -- restart to finish"} for u in updates if u.pending_restart]
    updates = [u for u in updates if not u.pending_restart]
    for entry in waiting:
        log(f"Windows Update: {entry['title']}: {entry['message']}")
    if not updates:
        log("Windows Update: nothing to install." if held or waiting
            else "Windows Update: system is up to date.")
        if waiting:
            log(f"Restart required to finish {len(waiting)} update(s)")
        return {"results": waiting, "reboot_required": bool(waiting), "installed_count": 0,
                "pending_restart_count": len(waiting), "held": held}
    log(f"Windows Update: {len(updates)} update(s) found"
        + (f", {len(held)} held." if held else "."))

    from core.disk_space import check_wu_preflight
    reason = check_wu_preflight(download_size_mb(updates, log))
    if reason:
        log(f"Windows Update: skipped — {reason}")
        return {"results": waiting, "reboot_required": bool(waiting), "installed_count": 0,
                "pending_restart_count": len(waiting), "held": held}

    if app.config.get("updates.restore_point_before_install", True):
        from core.system_restore import create_restore_point
        log("Creating a System Restore point before installing updates...")
        ok, _out = create_restore_point("Windows Client Tool — before WU install")
        if not ok:
            # The safeguard was asked for and is missing: installing anyway
            # would leave no way back. Turn the setting off to proceed without.
            log("Windows Update: NOT installed -- the System Restore point that Settings asks "
                "for could not be created (System Restore off or blocked by policy?).")
            return {"results": waiting, "reboot_required": bool(waiting), "installed_count": 0,
                    "pending_restart_count": len(waiting), "held": held,
                    "error": "restore point could not be created"}
        log("Restore point created.")

    def _progress(done, total, title):
        if title:
            log(f"Installing ({done + 1}/{total}): {title}")

    results = install_updates_iter(
        updates, progress_cb=_progress, output_cb=log, is_cancelled=is_cancelled
    )
    results_dicts = waiting + [
        {"kb": r.kb, "title": r.title, "success": r.success, "message": r.message,
         "reboot_required": r.reboot_required, "result_code": r.result_code}
        for r in results
    ]
    installed = sum(1 for r in results if r.success)
    log(f"Windows Update: {installed}/{len(results)} installed.")
    pending_count = sum(bool(r.get("reboot_required")) for r in results_dicts)
    if pending_count:
        log(f"Restart required to finish {pending_count} update(s)")

    reboot_required = False
    try:
        from core.windows_utils import is_reboot_pending
        reboot_required = is_reboot_pending()
    except Exception:
        logger.warning("Could not check reboot-pending state", exc_info=True)

    return {"results": results_dicts, "reboot_required": reboot_required or bool(pending_count),
            "installed_count": installed, "pending_restart_count": pending_count, "held": held}


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

    results = []
    stopped_reason = ""
    for i, u in enumerate(before):
        if stopped_reason:
            log(f"SKIPPED {u.winget_id}: {stopped_reason}")
            results.append({"name": u.name, "id": u.winget_id, "before": u.installed_version,
                            "confirmed": False, "state": "SKIPPED", "after": stopped_reason})
            continue
        if is_cancelled():
            log("Cancelled.")
            break
        log(f"[{i + 1}/{len(before)}] Updating {u.name} ({u.winget_id})...")
        # Each package is judged by reading ITS installed version back -- not by
        # its absence from a re-scan, which a failed re-scan grants to every package.
        outcome = install_update(u.winget_id, output_cb=log, is_cancelled=is_cancelled,
                                 target_version=u.available_version,
                                 before_version=u.installed_version,
                                 verify=app.config.get("updates.verify_after_update", True))
        log(f"  {outcome.state}: {outcome.detail}")
        results.append({"name": u.name, "id": u.winget_id, "before": u.installed_version,
                        "confirmed": bool(outcome), "state": outcome.state,
                        "after": outcome.detail})
        if outcome.state == "STILL RUNNING":
            stopped_reason = outcome.detail

    if results:
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
    """Cached catalog + overview scans, selected with Quick Cleanup's Light preset."""
    from modules.cleanup import cleanup_scanner as cs
    from modules.cleanup.cleanup_scanner import catalog, scan_cache
    from modules.cleanup.cleanup_scanner.knowledge import entry_for, effective_safety, id_for_scanner
    from modules.cleanup.cleanup_scanner.scanners_system import _is_inside
    from modules.cleanup import cleanup_presets

    log("Cleanup: scanning safe categories...")
    sources = [(area, fn) for area, scanners in cs._OV_GROUPS for fn in (scanners or [])]
    for category, area in catalog.CATEGORY_TABS.items():
        sources.extend((area, fn) for fn, (_label, safety) in
                       catalog.scanners_for(category, present_only=True).items() if safety == "safe")
    results, names, areas, seen = {}, {}, {}, set()
    for area, fn in sources:
        if is_cancelled():
            break
        name = fn.__name__
        spec = getattr(fn, "spec", None)
        if (name in seen or name in cleanup_presets.NEVER_INCLUDED or
                (spec and spec.disabled_reason) or
                effective_safety(spec.safety if spec else "safe",
                                 entry_for(id_for_scanner(fn))) != "safe"):
            continue
        seen.add(name)
        try:
            results[name] = scan_cache.cached_scan(fn, 0)
            names[name] = name
            for item in results[name].items:
                areas.setdefault(item.path, area)
        except Exception as e:
            logger.warning("Cleanup scan %s failed: %s", name, e)
            log(f"Cleanup: {area}: scan failed -- {e}")

    all_items = cleanup_presets.items_for_preset("light", results, names)
    for item in all_items:
        item.selected = True
    total = sum(item.size for item in all_items)
    wu_root = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "SoftwareDistribution")
    needs_wu = any(_is_inside(item.path, wu_root) for item in all_items)

    if not all_items:
        log("Cleanup: nothing to clean.")
        return {"freed": 0, "deleted": 0}

    log(f"Cleanup: deleting {len(all_items)} item(s), {_fmt_size(total)}...")
    report, refused, per_item = cs.delete_items_report(
        all_items, stop_wuauserv=needs_wu, cancelled=is_cancelled)
    deleted = report.files
    log(
        f"Cleanup: {deleted} file(s) deleted"
        + f", {cleanup_left_text(report, refused)}"
        + f" — {_fmt_size(report.freed)} freed."
        + (" Cancelled before it finished." if report.cancelled else "")
    )
    from modules.cleanup.cleanup_scanner.deleter import DeleteReport
    by_area = {}
    for path, one in per_item:
        by_area.setdefault(areas[path], [DeleteReport(), []])[0].merge(one)
    for path, reason in refused:
        by_area.setdefault(areas.get(path, "Windows Update"), [DeleteReport(), []])[1].append((path, reason))
    summaries = []
    for area, (one, rejections) in by_area.items():
        summary = {"area": area, "freed": one.freed, "left": cleanup_left_text(one, rejections)}
        summaries.append(summary)
        log(f"Cleanup: {area}: {_fmt_size(one.freed)} cleaned; {summary['left']}.")
    return {"freed": report.freed, "deleted": deleted, "cancelled": report.cancelled,
            "left": cleanup_left_text(report, refused), "areas": summaries}


def cleanup_left_text(report, refused) -> str:
    """Explain measured outcomes, separating protection from access failures."""
    protected = getattr(report, "protected", [])
    protected_paths = {path for path, _reason in protected}
    denied = sum(path not in protected_paths for path in report.denied)
    reasons = protected + list(refused)
    return (f"{len(report.locked)} in use (locked), {report.kept_recent} kept (newer than 24 h), "
            f"{denied} access denied, {report.cloud_only} cloud-only, "
            f"{len(reasons)} protected/refused"
            + (f" (first reason: {reasons[0][1]})" if reasons else ""))


def run_dism_stage(app, log: LogFn, is_cancelled: CancelFn) -> dict:
    """DISM /StartComponentCleanup — the slow WinSxS cleanup, not the fast
    /AnalyzeComponentStore preview (that one lives in the Cleanup module)."""
    import subprocess
    from core import pending_reboot
    restart_message = ("SKIPPED: a restart is pending (Windows Update/servicing) -- "
                       "run component cleanup after restarting")
    if pending_reboot.check().needed:
        log(f"DISM: {restart_message}")
        return {"status": "SKIPPED", "reboot_required": True, "output": restart_message}
    log("DISM: running StartComponentCleanup (this can take several minutes)...")
    proc = subprocess.run(
        ["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup"],
        capture_output=True, text=True, timeout=1800,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    log(output or "(no output)")
    if proc.returncode & 0xFFFFFFFF == 0x800F0806:
        log(f"DISM: {restart_message}")
        return {"status": "SKIPPED", "reboot_required": True, "returncode": proc.returncode,
                "output": restart_message + "\n" + output}
    if proc.returncode != 0:
        log(f"DISM: FAILED (exit {proc.returncode}).")
    return {"output": output, "returncode": proc.returncode,
            **({"error": f"DISM exited {proc.returncode}"} if proc.returncode != 0 else {})}


def stage_failures(stage: str, result: dict) -> list:
    """What went wrong in one stage's result, for the run's exit code and log.
    A stage that ran and reported failures is a failed run, not exit 0."""
    if not isinstance(result, dict):
        return [f"{stage}: no result"]
    problems = [f"{stage}: {result['error']}"] if result.get("error") else []
    for r in result.get("results", []) or []:
        pending = r.get("reboot_required") and r.get("result_code") in (None, 2)
        if stage == "wu" and (r.get("result_code") in (0, 1, 3, 4, 5) or
                               (not r.get("success", True) and not pending)):
            problems.append(f"wu: {r.get('kb') or r.get('title')}: {r.get('message', 'failed')}")
        elif stage in ("winget", "store") and r.get("state") in ("FAILED", "STILL RUNNING"):
            problems.append(f"{stage}: {r.get('id') or r.get('name')}: {r.get('after') or r['state']}")
    return problems


def run_health_stage(app, log: LogFn, is_cancelled: CancelFn) -> dict:
    """Read-only System Health findings only -- never ScanHealth,
    Component Cleanup, or Reset Base. Writes its own result to
    System Health's own history file (a separate stream from Update
    Center's, per that module's own design), not into this function's
    return dict, which stays a plain {findings_count} for STAGE_RUNNERS'
    homogeneous -> dict contract."""
    from modules.system_health import findings, history
    log("System Health: checking for pending servicing, orphaned tasks, and upgrade headroom...")
    results = findings.all_findings()
    for finding in results:
        log(f"  [{finding.severity}] {finding.title}")
    history.append_run(app.app_data_dir, action="unattended_findings",
                       command="", returncode=0, findings_count=len(results))
    return {"findings_count": len(results)}


STAGE_RUNNERS = {
    "wu": run_wu_stage,
    "winget": run_winget_stage,
    "store": run_store_stage,
    "cleanup": run_cleanup_safe_stage,
    "dism": run_dism_stage,
    "health": run_health_stage,
}

STAGE_LABELS = {
    "wu": "Windows Update",
    "winget": "winget",
    "store": "Microsoft Store",
    "cleanup": "Cleanup (safe)",
    "dism": "DISM WinSxS cleanup",
    "health": "System Health findings",
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
        "wu_pending_restart": wu.get("pending_restart_count", 0),
        "reboot_required": bool(wu.get("reboot_required") or dism.get("reboot_required")),
        "wu_held": wu.get("held", []),
        "winget_results": winget.get("results", []),
        "store_triggered": store.get("triggered", 0),
        "cleanup_freed": cleanup.get("freed", 0),
        "cleanup_deleted": cleanup.get("deleted", 0),
        "cleanup_left": cleanup.get("left", ""),
        "cleanup_areas": cleanup.get("areas", []),
        "dism_output": dism.get("output"),
    }

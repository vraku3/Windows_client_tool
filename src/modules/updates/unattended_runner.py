"""Headless maintenance entry point for `python main.py --unattended --stages ...`.

Constructs the App singleton (for config/logger/thread_pool access) but never
starts module_registry or a MainWindow — this is plain synchronous script
execution on the main thread, not a Qt event loop, so it must explicitly
CoInitialize/CoUninitialize around any WU/Store COM work rather than relying
on COMWorker. Intended to be launched by a Task Scheduler entry created from
the Updates module's Settings tab (see updates_module.py's _ScheduleTab).
"""
import logging
import sys
from typing import List
from core.operation_lock import OperationBusy
from modules.updates import run_checkpoint
from modules.updates.operation_guard import maintenance_operation

logger = logging.getLogger(__name__)

VALID_STAGES = ("wu", "winget", "store", "cleanup", "dism", "health")


def completion_message(data: dict, report_path, reboot_required: bool, failures=None) -> str:
    packages = data.get("winget_results", [])
    def count(key):
        value = data.get(key)
        return "-" if value is None else str(value)
    confirmed = data.get("apps_confirmed", sum(bool(r.get("confirmed")) for r in packages)
                         if "winget_results" in data else None)
    need_you = data.get("apps_need_you", sum(not r.get("confirmed", False) for r in packages)
                        if "winget_results" in data else None)
    prefix = (f"Maintenance INCOMPLETE: {len(failures)} problem(s) (first: {failures[0]})"
              if failures else "Maintenance finished")
    return (f"{prefix}; Windows installed: {count('wu_installed')}"
            + f"; Windows awaiting restart: {count('wu_pending_restart')}"
            + f"; apps confirmed: {confirmed if confirmed is not None else '-'}"
            + f"; apps needing you / broken: {need_you if need_you is not None else '-'}"
            + "; machine restart pending: " + ("could not check" if reboot_required is None else "yes" if reboot_required else "no")
            + (" -- restart to finish" if reboot_required else "")
            + f" -- report: {report_path or 'could not be written'}")


def run_unattended(stages: List[str]) -> int:
    from core.admin_utils import is_admin

    if not is_admin():
        msg = "--unattended requires administrator privileges (task must run with highest privileges)."
        print(msg, file=sys.stderr)
        logger.error(msg)
        return 1

    valid_stages = [s for s in stages if s in VALID_STAGES]
    if not valid_stages:
        msg = f"No valid --stages given (got {stages!r}); valid values: {VALID_STAGES}"
        print(msg, file=sys.stderr)
        logger.error(msg)
        return 1

    try:
        with maintenance_operation("Unattended maintenance"):
            return _run_unattended_locked(valid_stages)
    except OperationBusy as exc:
        logger.info("Unattended maintenance skipped: %s", exc)
        return 0
    except Exception:
        logger.exception("Unattended maintenance failed")
        return 1


def _run_unattended_locked(valid_stages: List[str]) -> int:
    from app import App
    app = App.instance or App()
    from core.timeline import detect_reboot, record
    detect_reboot(app.app_data_dir)
    record(app.app_data_dir, "system", "app_start", "Unattended maintenance started")

    import pythoncom
    pythoncom.CoInitialize()
    exit_code = 0
    try:
        from modules.updates.history_writer import append_run, load_history
        from modules.updates.report_generator import render_update_report_html, write_report
        from modules.updates.stage_runners import (STAGE_LABELS, STAGE_RUNNERS,
                                                   normalize_stage_data, stage_failures,
                                                   finalize_run, write_session_log, outstanding_text)

        data = {}
        failures = []
        lines = []
        run_checkpoint.begin(app.app_data_dir, valid_stages, "unattended")
        def log(line):
            lines.append(line)
            logger.info("%s", line)
        for stage in valid_stages:
            logger.info("--- unattended stage: %s ---", STAGE_LABELS.get(stage, stage))
            fn = STAGE_RUNNERS[stage]
            try:
                kwargs = {"context": "unattended"} if stage in ("wu", "winget") else {}
                data[stage] = fn(app, log, lambda: False, **kwargs)
            except Exception as exc:
                logger.exception("Unattended stage %s failed", stage)
                data[stage] = {"error": str(exc) or type(exc).__name__}
            problems = stage_failures(stage, data[stage])
            failures.extend(problems)
            for problem in problems:
                logger.error("Unattended stage failure -- %s", problem)
                exit_code = 1
            if any(r.get("state") == "STILL RUNNING" for r in data[stage].get("results", [])):
                log("Remaining stages stopped: installer completion unknown.")
                break

        finalize_run(data)
        logger.info("%s", outstanding_text(data["outstanding"]))
        write_session_log(app.app_data_dir, data, lines)
        run_checkpoint.finish(app.app_data_dir, "completed" if exit_code == 0 else "completed_with_errors")
        normalized = normalize_stage_data(data)
        append_run(
            app.app_data_dir,
            freed_bytes=normalized.get("cleanup_freed", 0),
            updates_installed=normalized.get("wu_installed", 0),
            winget_count=normalized.get("apps_confirmed"),
            apps_need_you=normalized.get("apps_need_you"),
            machine_restart_pending=normalized.get("machine_restart_pending"),
            pending_restart=normalized.get("wu_pending_restart", 0),
        )
        history = load_history(app.app_data_dir)
        html_text = render_update_report_html(normalized, history)
        path = write_report(app.app_data_dir, html_text)
        if path is None:
            logger.warning("Unattended run complete. Report not written — low disk space.")
        else:
            logger.info("Unattended run complete. Report: %s", path)
        from modules.updates.maintenance_notify import notify_finished
        restart = normalized.get("machine_restart_pending")
        notify_finished(completion_message(normalized, path, restart, failures), app.app_data_dir)
    except Exception:
        logger.exception("Unattended run failed")
        exit_code = 1
    finally:
        pythoncom.CoUninitialize()

    try:
        app.shutdown()
    except Exception:
        logger.warning("Error during unattended shutdown", exc_info=True)

    return exit_code

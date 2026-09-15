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

logger = logging.getLogger(__name__)

VALID_STAGES = ("wu", "winget", "store", "cleanup", "dism", "health")


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

    from app import App
    app = App.instance or App()

    import pythoncom
    pythoncom.CoInitialize()
    exit_code = 0
    try:
        from modules.updates.history_writer import append_run, load_history
        from modules.updates.report_generator import render_update_report_html, write_report
        from modules.updates.stage_runners import STAGE_LABELS, STAGE_RUNNERS, normalize_stage_data

        data = {}
        for stage in valid_stages:
            logger.info("--- unattended stage: %s ---", STAGE_LABELS.get(stage, stage))
            fn = STAGE_RUNNERS[stage]
            try:
                data[stage] = fn(app, logger.info, lambda: False)
            except Exception:
                logger.exception("Unattended stage %s failed", stage)
                data[stage] = {}
                exit_code = 1

        normalized = normalize_stage_data(data)
        append_run(
            app.app_data_dir,
            freed_bytes=normalized.get("cleanup_freed", 0),
            updates_installed=normalized.get("wu_installed", 0),
            winget_count=len(normalized.get("winget_results", [])),
        )
        history = load_history(app.app_data_dir)
        html_text = render_update_report_html(normalized, history)
        path = write_report(app.app_data_dir, html_text)
        if path is None:
            logger.warning("Unattended run complete. Report not written — low disk space.")
        else:
            logger.info("Unattended run complete. Report: %s", path)
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

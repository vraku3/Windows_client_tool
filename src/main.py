import argparse
import logging
import os
import sys
import traceback

# Add src/ to Python path so imports like `core.event_bus` work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Startup trace helper ──────────────────────────────────────────────────────
_log = logging.getLogger("startup")


def _s(entry: str) -> None:
    """Log a startup entry point. Writes to stderr so it's visible even if
    the app crashes before the log file is set up."""
    msg = f"[STARTUP] {entry}"
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        # Windows console may not support Unicode — fall back to ASCII
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)
    _log.debug(msg)


_s("=== main.py entered ===")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app import App  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
logger = logging.getLogger(__name__)

_s("imports done")


def _global_exception_handler(exc_type, exc_value, exc_tb):
    """Global exception handler — logs traceback and shows error dialog."""
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _log.critical("Unhandled exception:\n%s", tb_text)

    # Show non-fatal error dialog with Copy to Clipboard
    try:
        app = QApplication.instance()
        if app:
            msg = QMessageBox()
            msg.setWindowTitle("Unexpected Error")
            msg.setIcon(QMessageBox.Icon.Critical)
            msg.setText("An unexpected error occurred. The application may continue running.")
            msg.setDetailedText(tb_text)
            # Find the active modal window to use as parent
            for w in app.topLevelWidgets():
                if w.isWindow() and w.isVisible() and w.windowType() == w.WindowType.Dialog:
                    msg.setParent(w)
                    break
            copy_btn = msg.addButton("Copy to Clipboard", QMessageBox.ButtonRole.ActionRole)
            msg.addButton(QMessageBox.StandardButton.Ok)
            msg.exec()
            if msg.clickedButton() == copy_btn:
                app.clipboard().setText(tb_text)
    except Exception:
        logger.warning("If dialog fails, at least the log was written", exc_info=True)


def _parse_args():
    parser = argparse.ArgumentParser(description="Windows Client Tool")
    parser.add_argument(
        "--unattended", action="store_true",
        help="Run maintenance stages headlessly (no GUI) and exit — used by the "
             "scheduled task created from the Updates module's Settings tab.",
    )
    parser.add_argument(
        "--stages", default="wu,winget,cleanup",
        help="Comma-separated stages for --unattended: wu,winget,store,cleanup,dism",
    )
    parser.add_argument(
        "--apply-security-batch", metavar="BATCH_JSON",
        help="Apply one staged Security Dashboard batch and exit — this is "
             "the elevated child the pane launches for its single UAC prompt. "
             "No GUI: the parent cannot read this process's output, so the "
             "outcome goes to the --result file.",
    )
    parser.add_argument(
        "--result", metavar="RESULT_JSON",
        help="Where --apply-security-batch writes its report.",
    )
    # Ignore unknown args instead of erroring — PyInstaller/Qt may pass extras.
    args, _unknown = parser.parse_known_args()
    return args


def register_all_modules(app) -> None:
    """Import and register every module with `app.module_registry`.

    Split out from main() so tests can build a module registry without also
    standing up a QApplication/MainWindow — see tests/test_module_smoke.py.
    """
    # Register all data modules
    from modules.dashboard.dashboard_module import DashboardModule
    from modules.tweaks.tweaks_module import TweaksModule
    from modules.cleanup.cleanup_module import CleanupModule
    from modules.monitor_control.monitor_module import MonitorControlModule
    from modules.cleanup.quick_cleanup_module import QuickCleanupModule
    from modules.debloat.debloat_module import DebloatModule
    from modules.treesize.treesize_module import TreeSizeModule
    from modules.quick_fix.quick_fix_module import QuickFixModule
    from modules.updates.updates_module import UpdatesModule
    # Batch B — System group
    from modules.hardware_inventory.hardware_module import HardwareModule
    from modules.network_diagnostics.network_module import NetworkDiagnosticsModule
    from modules.security_dashboard.security_module import SecurityDashboardModule
    from modules.driver_manager.driver_module import DriverModule
    # Batch B — Manage group
    from modules.startup_manager.startup_module import StartupBootModule
    from modules.scheduled_tasks.tasks_module import TasksModule
    from modules.windows_features.features_module import WindowsFeaturesModule
    from modules.certificate_viewer.cert_module import CertModule
    from modules.gpresult.gpresult_module import GPResultModule
    # Batch C — Tools group
    from modules.performance_tuner.perf_tuner_module import PerfTunerModule
    from modules.shared_resources.shares_module import SharesModule
    from modules.env_vars.env_vars_module import EnvVarsModule
    from modules.registry_explorer.registry_module import RegistryExplorerModule
    from modules.software_inventory.software_module import SoftwareModule
    from modules.remote_tools.remote_module import RemoteToolsModule
    from modules.disk_health.disk_health_module import DiskHealthModule
    from modules.restore_manager.restore_module import RestoreManagerModule
    # Track 2 — New Tool Modules
    from modules.services_manager.services_module import ServicesModule
    from modules.firewall_rules.firewall_manager_module import FirewallManagerModule
    from modules.local_users.users_module import LocalUsersModule
    from modules.system_report.report_module import SystemReportModule
    from modules.about.about_module import AboutModule
    from modules.diagnose.diagnose_module import DiagnoseModule
    from modules.log_viewer.log_viewer_module import LogViewerModule

    app.module_registry.register(DashboardModule())
    app.module_registry.register(DiagnoseModule())
    app.module_registry.register(LogViewerModule())
    # EventViewer, CBS, DISM, WU, Reliability, CrashDumps — embedded in DiagnoseModule
    # PerfMon is a Dashboard tab, not its own sidebar entry: one place for the
    # live performance picture. See modules/dashboard/dashboard_module.py.
    app.module_registry.register(TweaksModule())
    app.module_registry.register(CleanupModule())
    app.module_registry.register(QuickCleanupModule())
    app.module_registry.register(DebloatModule())
    app.module_registry.register(TreeSizeModule())
    app.module_registry.register(QuickFixModule())
    app.module_registry.register(UpdatesModule())
    app.module_registry.register(HardwareModule())
    app.module_registry.register(MonitorControlModule())
    app.module_registry.register(NetworkDiagnosticsModule())
    app.module_registry.register(SecurityDashboardModule())
    app.module_registry.register(DriverModule())
    app.module_registry.register(StartupBootModule())
    app.module_registry.register(TasksModule())
    app.module_registry.register(WindowsFeaturesModule())
    app.module_registry.register(CertModule())
    app.module_registry.register(GPResultModule())
    # Batch C
    app.module_registry.register(PerfTunerModule())
    app.module_registry.register(SharesModule())
    app.module_registry.register(EnvVarsModule())
    app.module_registry.register(RegistryExplorerModule())
    app.module_registry.register(SoftwareModule())
    app.module_registry.register(RemoteToolsModule())
    app.module_registry.register(DiskHealthModule())
    app.module_registry.register(RestoreManagerModule())
    # Track 2
    app.module_registry.register(ServicesModule())
    app.module_registry.register(FirewallManagerModule())
    app.module_registry.register(LocalUsersModule())
    app.module_registry.register(SystemReportModule())
    app.module_registry.register(AboutModule())


def main():
    args = _parse_args()

    if args.unattended:
        _s("=== unattended mode ===")
        from modules.updates.unattended_runner import run_unattended
        sys.exit(run_unattended(args.stages.split(",")))

    if args.apply_security_batch:
        # Before any Qt object exists: this is the elevated helper, it has no
        # window, and it reports through --result rather than stdout.
        _s("=== security batch (elevated helper) ===")
        from modules.security_dashboard.elevated_helper import run_from_file
        if not args.result:
            print("--apply-security-batch requires --result", file=sys.stderr)
            sys.exit(2)
        sys.exit(run_from_file(args.apply_security_batch, args.result))

    qt_app = QApplication(sys.argv)

    # Initialize App singleton (wires all core services)
    app = App()

    # Install global exception handler (after logging is set up)
    sys.excepthook = _global_exception_handler

    register_all_modules(app)

    # Start modules
    _s("app.start() → module_registry.start_all()")
    app.start()
    _s("app.start() done — window creation next")

    # Create window and register all modules (uses new register_module API)
    window = MainWindow(app)
    for module in app.module_registry.modules:
        window.register_module(module)

    # Also on aboutToQuit: shutdown saves config, closes the backup
    # database and drains the thread pool, and MainWindow.closeEvent is not
    # the only way out — a Windows session logoff and any qApp.quit() skip
    # it entirely, losing whatever config changes were pending.
    # App.shutdown is idempotent, so both paths firing is fine.
    qt_app.aboutToQuit.connect(app.shutdown)

    window.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()

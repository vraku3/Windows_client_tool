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
            from PyQt6.QtCore import Qt
            for w in app.topLevelWidgets():
                # Qt.WindowType, not w.WindowType: the latter does not exist on a
                # widget, and raised here for every visible window (2026-09-18 log),
                # so the error dialog never appeared at all.
                if w.isWindow() and w.isVisible() and w.windowType() == Qt.WindowType.Dialog:
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
    parser.add_argument(
        "--game-mode", choices=("on", "off"),
        help="Enter or leave the journaled Game Mode session and exit (no GUI).",
    )
    parser.add_argument(
        "--game-profile", default="",
        help="Saved Game Mode profile to use with --game-mode on.",
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
    from modules.debloat.debloat_module import DebloatModule
    from modules.treesize.treesize_module import TreeSizeModule
    from modules.quick_fix.quick_fix_module import QuickFixModule
    from modules.game_mode.game_mode_module import GameModeModule
    from modules.updates.updates_module import UpdatesModule
    # Batch B — System group
    from modules.hardware_hub.hardware_hub_module import HardwareHubModule
    from modules.network_diagnostics.network_module import NetworkDiagnosticsModule
    from modules.driver_manager.driver_module import DriverModule
    from modules.system_health.system_health_module import SystemHealthModule
    # Batch B — Manage group
    from modules.startup_manager.startup_module import StartupBootModule
    from modules.security_hub.security_hub_module import SecurityHubModule
    # Batch C — Tools group
    from modules.disk_health.disk_health_module import DiskHealthModule
    from modules.restore_manager.restore_module import RestoreManagerModule
    from modules.scripts_hub.scripts_hub_module import ScriptsModule
    from modules.utilities_hub.utilities_hub_module import UtilitiesHubModule
    from modules.about.about_module import AboutModule
    from modules.diagnose.diagnose_module import DiagnoseModule

    app.module_registry.register(DashboardModule())
    app.module_registry.register(DiagnoseModule())
    # EventViewer, CBS, DISM, WU, Reliability, CrashDumps, Timeline, LogViewer — embedded in DiagnoseModule
    # PerfMon is a Dashboard tab, not its own sidebar entry: one place for the
    # live performance picture. See modules/dashboard/dashboard_module.py.
    app.module_registry.register(TweaksModule())
    app.module_registry.register(CleanupModule())
    app.module_registry.register(DebloatModule())
    app.module_registry.register(TreeSizeModule())
    app.module_registry.register(QuickFixModule())
    app.module_registry.register(GameModeModule())
    app.module_registry.register(UpdatesModule())
    # Hardware Info, Thermal Control, Monitor Control, Stress Test — folded into HardwareHubModule
    app.module_registry.register(HardwareHubModule())
    app.module_registry.register(SystemHealthModule())
    app.module_registry.register(NetworkDiagnosticsModule())
    # Security Dashboard, Firewall, Certificates, GPResult, Local Users — folded into SecurityHubModule
    app.module_registry.register(SecurityHubModule())
    app.module_registry.register(DriverModule())
    app.module_registry.register(StartupBootModule())
    # SharesModule, RemoteToolsModule — folded into NetworkDiagnosticsModule
    app.module_registry.register(DiskHealthModule())
    app.module_registry.register(RestoreManagerModule())
    app.module_registry.register(ScriptsModule())
    # SoftwareInventory, SystemManagement, ControlPanel, EnvVars, RegistryExplorer, SystemReport — folded into UtilitiesHubModule
    app.module_registry.register(UtilitiesHubModule())
    app.module_registry.register(AboutModule())


def _run_game_mode(command: str, profile: str) -> int:
    """Headless Game Mode ON/OFF for launchers; same journaled session path."""
    import ctypes
    from app import App
    from modules.game_mode.engine import game_config, session
    from modules.game_mode.engine.journal import Journal
    from modules.game_mode.engine.system import WinSystem

    app = App.instance or App()
    config = app.config
    choices = session.choices_from_config(
        config.get("modules.game_mode.choices", None))
    if command == "on" and profile:
        saved = game_config.load_profiles(config).get(profile)
        if saved is None:
            print(f"Game Mode profile not found: {profile}", file=sys.stderr)
            return 2
        choices = session.choices_from_config(saved)
    settings = game_config.load_settings(config)
    choices.protected = set(game_config.load_protected(config))
    choices.protected_publishers = set(config.get(
        "modules.game_mode.protected_publishers", []) or [])
    choices.bound_game = settings.bound_game
    choices.priority_boost = settings.priority_boost
    choices.force_close = settings.force_close
    choices.close_grace_s = settings.close_grace_s
    sid = ctypes.c_uint(0)
    ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
    journal = Journal()
    system = WinSystem()

    def log(level, text):
        print(f"[{level}] {text}", file=sys.stderr)

    try:
        if command == "on":
            data = session.turn_on(system, choices, journal, log, os.getpid(), int(sid.value))
            print(f"Game Mode ON: {data.get('handles_before')} -> "
                  f"{data.get('handles_after')} handles")
        else:
            report = session.turn_off(system, journal, log)
            print(f"Game Mode OFF: {len(report)} restoration message(s)")
    except Exception as exc:
        print(f"Game Mode {command} failed: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            app.shutdown()
        except Exception as exc:
            logging.getLogger("main").debug("app.shutdown error: %s", exc)
    return 0


def main():
    args = _parse_args()

    if args.unattended:
        _s("=== unattended mode ===")
        from modules.updates.unattended_runner import run_unattended
        sys.exit(run_unattended(args.stages.split(",")))

    if args.game_mode:
        sys.exit(_run_game_mode(args.game_mode, args.game_profile))

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
    from ui.dialog_enhancer import install_dialog_enhancer
    from ui.table_resizer import install_table_resizer
    install_dialog_enhancer(qt_app)
    install_table_resizer(qt_app)

    # Initialize App singleton (wires all core services)
    app = App()
    from core.timeline import detect_reboot, record
    detect_reboot(app.app_data_dir)
    record(app.app_data_dir, "system", "app_start", "Windows Client Tool started")

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
    exit_code = qt_app.exec()

    # Ensure orderly shutdown before interpreter finalization
    try:
        app.shutdown()
    except Exception as e:
        logger.warning("Error during final app.shutdown: %s", e)

    try:
        from ui.dialog_enhancer import uninstall_dialog_enhancer
        from ui.table_resizer import uninstall_table_resizer
        uninstall_table_resizer(qt_app)
        uninstall_dialog_enhancer(qt_app)
    except Exception as e:
        logger.debug("Error uninstalling dialog/table enhancers: %s", e)

    try:
        from core.worker import _RETIRING
        _RETIRING.clear()
    except Exception as e:
        logger.debug("Error clearing retiring workers: %s", e)

    try:
        window.deleteLater()
        qt_app.processEvents()
        del window
        del app
    except Exception as e:
        logger.debug("Error during window/app deletion: %s", e)

    try:
        if sys.stdout:
            sys.stdout.flush()
        if sys.stderr:
            sys.stderr.flush()
    except Exception as e:
        logger.debug("Error flushing stdout/stderr: %s", e)

    # os._exit terminates immediately and cleanly without allowing Python's
    # post-mortem cyclic GC to touch freed Qt C++ object wrappers in sip.pyd.
    os._exit(exit_code)


if __name__ == "__main__":
    main()

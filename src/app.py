"""Application services for the Windows Client Tool."""

import os
import sys
from typing import ClassVar, Optional

from PyQt6.QtCore import QThreadPool

from core.backup_service import BackupService
from core.config_manager import ConfigManager
from core.event_bus import EventBus
from core.logging_service import LoggingService
from core.module_registry import ModuleRegistry
from core.search_engine import SearchEngine
from core.theme_manager import ThemeManager


def _get_app_data_dir() -> str:
    """Return %APPDATA%/WindowsTweaker, creating it if needed."""
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    app_dir = os.path.join(base, "WindowsTweaker")
    os.makedirs(app_dir, exist_ok=True)
    return app_dir


def _get_resource_dir() -> str:
    """Return the base directory for bundled resources (PyInstaller or source tree)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    # Running from source: resources live one level above src/
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _get_default_config() -> dict:
    """Load default config from config/default_config.json."""
    import json

    config_path = os.path.join(_get_resource_dir(), "config", "default_config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


class App:
    """Singleton that owns all core services. Created once in main.py."""

    instance: ClassVar[Optional["App"]] = None

    def __init__(self, app_data_dir: Optional[str] = None):
        if App.instance is not None:
            raise RuntimeError("App is a singleton — use App.get()")
        App.instance = self

        self._shut_down = False
        self._app_data_dir = app_data_dir or _get_app_data_dir()
        self.app_data_dir = self._app_data_dir  # public alias for modules

        # Core services
        self.event_bus = EventBus()
        self.config = ConfigManager(
            config_dir=self._app_data_dir,
            defaults=_get_default_config,  # callable — lazily loaded only when needed
            event_bus=self.event_bus,
        )
        self.config.load()

        log_dir = os.path.join(self._app_data_dir, "logs")
        log_level = self.config.get("app.log_level", "INFO")
        self.logger = LoggingService(log_dir=log_dir, log_level=log_level)
        self.logger.setup()
        self.logger.setup_session_log()

        # Rotate old dated log/report files — runs once per launch, cheap no-op
        # once nothing is past the retention window. retention_days=0 keeps forever.
        try:
            from core.log_rotation import keep_newest, rotate_old_files

            retention_days = self.config.get("app.log_retention_days", 30)
            session_glob = self.logger.session_log_glob
            if session_glob:
                rotate_old_files(self.logger.session_log_dir, session_glob, retention_days)
            rotate_old_files(
                os.path.join(self._app_data_dir, "updates"), "report-*.html", retention_days
            )
            # The session log gets one file per launch, so the age rule alone
            # never catches a busy day — 101 files piled up here in two.
            if session_glob:
                keep_newest(
                    self.logger.session_log_dir,
                    session_glob,
                    self.config.get("app.log_retention_count", 20),
                )
        except Exception:
            import logging as _logging

            _logging.getLogger(__name__).warning("Log rotation failed", exc_info=True)

        self.backup = BackupService(data_dir=self._app_data_dir)

        styles_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "styles")
        self.theme = ThemeManager(styles_dir=styles_dir)

        self.search = SearchEngine(config_manager=self.config)
        self.module_registry = ModuleRegistry()
        self.thread_pool = QThreadPool.globalInstance()

    @classmethod
    def get(cls) -> "App":
        assert cls.instance is not None, "App not initialized"
        return cls.instance

    def start(self) -> None:
        """Initialize theme and start all registered modules."""
        theme = self.config.get("app.theme", "dark")
        self.theme.apply_theme(theme)
        self.module_registry.start_all(self)

    def shutdown(self) -> None:
        """Stop modules, save config, shut down logging.

        Idempotent: it is reachable from MainWindow.closeEvent AND from
        QApplication.aboutToQuit, and a Windows session logoff or any
        qApp.quit() takes only the second of those. Running it twice would
        stop already-stopped modules and close an already-closed backup
        database.
        """
        if getattr(self, "_shut_down", False):
            return
        self._shut_down = True

        self.module_registry.stop_all()
        # Workers first, THEN the backup database: a tweak still applying
        # records each step's before-value as it goes, and a closed database
        # would leave the change made with no record to revert it from.
        self.thread_pool.waitForDone(5000)
        from core.long_op_pool import get_long_op_pool
        get_long_op_pool().waitForDone(5000)  # same best-effort grace period as thread_pool above
        self.backup.close()
        self.config.save()
        self.logger.shutdown()
        App.instance = None

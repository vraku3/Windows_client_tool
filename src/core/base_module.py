"""Base class for all application modules.

Provides common functionality for module lifecycle management, worker tracking,
and metadata for the application shell. All module-specific functionality should
extend this abstract base class.
"""

from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, Callable, List, Optional
from PyQt6.QtWidgets import QWidget

if TYPE_CHECKING:
    from PyQt6.QtCore import QThreadPool
    from app import App
    from core.search_provider import SearchProvider


class BaseModule(ABC):
    """Abstract base class for all Windows Tweaker modules.

    Attributes:
        name: Human-readable module identifier (e.g., "Dashboard", "Event Viewer")
        icon: Emoji icon for sidebar representation
        description: Short description shown in help/context menus
        requires_admin: Whether module needs elevated privileges to function
        group: Logical grouping for sidebar organization (OVERVIEW, DIAGNOSE, TOOLS, etc.)

    Lifecycle:
        1. Instance created during module registration
        2. on_start(app) called when app initializes
        3. create_widget() produces the main UI widget
        4. on_activate() called when widget becomes visible
        5. on_deactivate() called when widget is hidden
        6. on_stop() called before app shutdown for cleanup
    """

    name: str
    icon: str
    description: str
    requires_admin: bool = False
    #: A module that is safe to *open and read* unelevated even though its
    #: writes need elevation. The registry and composite hosts keep it live
    #: when unelevated; the module must gate its destructive actions through
    #: `require_admin()`. Modules without this flag are fully disabled when
    #: unelevated, as before.
    read_only_unelevated: bool = False
    group: str

    def __init__(self) -> None:
        """Initialize module state.

        Sets up internal tracking for worker threads and app reference.
        """
        self._workers: List[object] = []
        self.app: Optional[object] = None

    def require_admin(self, parent=None) -> bool:
        """Block a destructive action unless the app is running elevated.

        Returns True when elevated, so callers can proceed. When unelevated
        it explains why the action is blocked and offers to relaunch as
        Administrator, then returns False.
        """
        from core.admin_utils import is_admin
        if is_admin():
            return True

        from PyQt6.QtWidgets import QMessageBox
        parent = parent if parent is not None else getattr(self, "_widget", None)
        reply = QMessageBox.warning(
            parent, "Administrator Required",
            "This action needs Administrator rights.\n\n"
            "Relaunch the application as Administrator and try again.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply == QMessageBox.StandardButton.Yes:
            from core.admin_utils import restart_as_admin
            restart_as_admin()
        return False

    @property
    def thread_pool(self) -> "QThreadPool":
        """Return the shared QThreadPool instance for this app."""
        from PyQt6.QtCore import QThreadPool
        return QThreadPool.globalInstance()

    def create_widget(self) -> QWidget:
        """Create and return the module's main widget.

        Called once during app initialization. Must return a QWidget instance
        or raise an appropriate exception if creation fails.

        Returns:
            QWidget: The module's primary UI component.

        Raises:
            RuntimeError: If widget cannot be created
        """
        raise NotImplementedError(
            f"Module '{self.name}' must implement create_widget()"
        )

    def on_activate(self) -> None:
        """Called when module widget becomes visible.

        Performs any necessary initialization: starting background timers,
        loading initial data, or setting up event handlers. Override in
        subclasses for module-specific behavior.
        """
        pass

    def on_deactivate(self) -> None:
        """Called when module widget is hidden.

        Performs cleanup that should be reversed when the module is reactivated:
        stop timers, disconnect event handlers, clear cached data. Override
        in subclasses if needed.
        """
        pass

    def on_start(self, app: App) -> None:
        """Called when application starts.

        Store reference to app instance for accessing shared services like
        config manager, theme manager, worker pool, etc. Override for
        app-dependent initialization.

        Args:
            app: The App singleton instance
        """
        self.app = app

    def on_stop(self) -> None:
        """Called when application stops/shuts down.

        Perform shutdown cleanup: explicitly stop workers, release resources,
        save any final state, and prevent background operations. Override
        to ensure thorough cleanup (currently relies on cancel_all_workers).
        """
        self.cancel_all_workers()

    def get_config_schema(self) -> dict[str, object]:
        """Return module-specific configuration schema.

        Returns:
            dict: Configuration keys defined by this module (empty by default)

        Example:
            return {
                "dashboard.auto_refresh_interval": int,
                "updates.check_on_start": bool,
            }
        """
        return {}

    def get_toolbar_actions(self) -> List[Callable[[], object]]:
        """Return toolbar actions for this module.

        Returns:
            list: Actions to add to main toolbar (empty by default)

        Example:
            actions = [
                QAction("Refresh", self),
                QAction("Export CSV", self),
            ]
        """
        return []

    def get_menu_actions(self) -> List[Callable[[], object]]:
        """Return context menu actions for this module.

        Returns:
            list: Actions to add to module's context menu (empty by default)

        Example:
            actions = [
                QAction("Clear View", self),
                QAction("Export...", self),
            ]
        """
        return []

    def get_status_info(self) -> str:
        """Return brief status message for status bar.

        Returns:
            str: Short status text shown in app status bar when module active
                 (empty string by default)

        Example:
            return f"Event Viewer — {count} events loaded"
        """
        return ""

    def get_search_provider(self) -> Optional[SearchProvider]:
        """Return search provider for this module's content.

        Returns:
            SearchProvider or None: Provider for indexing/searching module data
        """
        return None

    def get_search_providers(self) -> List[SearchProvider]:
        """Return every search provider this module contributes.

        A plain module contributes at most one, so the default wraps
        `get_search_provider()`. A module that hosts others (see
        `core.composite_module.CompositeModule`) overrides this to return its
        children's providers.

        This is a list rather than a single provider because
        `SearchEngine.execute` filters per provider on `provider.module_name`
        — one provider standing in for several would have to re-implement that
        filtering and could drift from it.
        """
        provider = self.get_search_provider()
        return [provider] if provider is not None else []

    def cancel_all_workers(self) -> None:
        """Cancel and clean up all running background workers.

        Calls cancel() on each tracked worker to prevent blocking or
        unauthorized background operations after shutdown.

        Through core.worker.retire(), never `cancel(); clear()`: dropping the
        last reference to a worker that is still running frees its
        WorkerSignals while its queued cancelled/finished are still in the
        event loop, and delivering them crashes the process natively (see
        CLAUDE.md, "Never drop the last reference to a worker that is still
        running"). Every module inherited the unsafe form until 2026-10-25.
        """
        from core.worker import retire
        retire(self._workers)

    def get_refresh_interval(self) -> Optional[int]:
        """Return auto-refresh interval in milliseconds, or None to disable.

        Modules that support auto-refresh should override this.
        The timer calls refresh_data() if available, otherwise on_activate().
        """
        return None

    def refresh_data(self) -> None:
        """Refresh module data.

        Called by the global auto-refresh timer. Override in modules that
        need a dedicated refresh (rather than the full on_activate behavior).
        By default, calls on_activate().
        """
        self.on_activate()

    @property
    def workers(self) -> List[object]:
        """Convenience property: return module's worker list."""
        return list(self._workers)

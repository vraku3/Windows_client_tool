"""TreeSize module (spec 9).

`requires_admin = False` deliberately: the walk fallback keeps the module fully
functional unelevated, and the Home tab's *Start as administrator* button plus
a status-bar notice offer the fast path when it would help.
"""
import ctypes
import logging
from typing import Optional

from PyQt6.QtWidgets import QWidget

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.search_provider import SearchProvider

from modules.treesize.search_provider import TreeSizeSearchProvider
from modules.treesize.ui.shell import TreeSizeShell
from modules.treesize.ui.theme import apply_theme

logger = logging.getLogger(__name__)


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class TreeSizeModule(BaseModule):
    name = "TreeSize"
    icon = "📊"
    description = "Disk space analysis: what is using the drive, duplicate files, and where"
    requires_admin = False
    group = ModuleGroup.TOOLS

    def __init__(self) -> None:
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._shell: Optional[TreeSizeShell] = None
        self._last_summary = "No scan yet"
        # Built here rather than in create_widget: the registry may ask for
        # the provider before the pane has ever been shown.
        self._search_provider = TreeSizeSearchProvider()

    def create_widget(self) -> QWidget:
        self._shell = TreeSizeShell()
        # The sheet is applied to this module's root widget, so it scopes to
        # the pane by descent and leaves every other module's styling alone.
        apply_theme(self._shell)
        self._shell.scan_finished.connect(self._on_scan_finished)
        config = getattr(self, "_config", None)
        if config is not None:
            from modules.treesize.ui.options_dialog import load_settings
            self._shell.config = config
            self._shell.apply_settings(load_settings(config))
        # Elevation only changes which engine is chosen, never whether the
        # module works, so it is a note rather than a gate.
        self._shell.ribbon.set_enabled("tools.admin", not _is_admin())
        self._shell.elevation_banner.set_elevated(_is_admin())
        self._widget = self._shell
        return self._widget

    def on_start(self, app) -> None:
        self._app = app
        # on_start runs BEFORE create_widget, so only the reference is stored
        # here -- touching the shell would be touching a widget that does not
        # exist yet.
        self._config = getattr(app, "config", None)

    def on_activate(self) -> None:
        if self._shell is not None:
            self._shell.drive_list.refresh()

    def on_deactivate(self) -> None:
        self.cancel_all_workers()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def cancel_all_workers(self) -> None:
        if self._shell is not None:
            self._shell.stop_scan()
            # The watcher holds a handle on the scanned root, so leaving it
            # running after the pane is deactivated would keep a volume busy
            # for a view nobody is looking at.
            self._shell._stop_watching()

    def open_path(self, path: str) -> None:
        """Jump straight to scanning `path`, for a caller (Disk Space's
        folder-scan rows) that already knows the exact folder — rather than
        landing on the Home tab and making the user re-browse to it.
        `_navigate_to_module` calls this after `on_activate`, so the shell is
        already built by the time this runs.

        `start_scan` ignores a request while another scan runs, so say so
        rather than letting the double-click look like it did nothing.
        """
        if self._shell is None:
            return
        if self._shell._worker is not None:
            self._shell.status_bar.show_notice(
                f"A scan is still running. Stop it to open {path}")
            return
        self._shell.start_scan(path)

    def get_search_provider(self) -> Optional[SearchProvider]:
        """Spec 9. TreeSize was the one module without one, so the global bar
        could not reach the paths it had already indexed."""
        return self._search_provider

    def get_status_info(self) -> str:
        return self._last_summary

    def _on_scan_finished(self, result) -> None:
        from modules.treesize.ui.formatting import format_bytes, format_count
        # The provider searches the store that is already in memory, so it
        # only needs pointing at the newest one.
        target = self._shell.path_combo.currentText() if self._shell else ""
        self._search_provider.set_scan(result.store, result.root, target)
        state = "" if result.complete else " (incomplete)"
        self._last_summary = (
            f"{format_bytes(result.store.size[result.root])} across "
            f"{format_count(result.node_count)} nodes via {result.engine}{state}")

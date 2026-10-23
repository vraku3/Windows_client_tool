"""The App history tab, as a child module of the Dashboard.

Thin: the widget is `AppHistoryTab` and the rollup lives in
`procengine/usage.py`. Exists so the tab can be hosted by `CompositeModule`
and tested on its own.

Not `requires_admin`: cumulative CPU time is part of the bulk syscall and
needs no privilege. The network columns Task Manager shows for Store apps
are deliberately absent (per-process network counters need a driver) -- the
tab says so in its own header line.
"""
from typing import Optional

from PyQt6.QtWidgets import QWidget

from core.base_module import BaseModule
from core.module_groups import ModuleGroup

from .app_history_tab import AppHistoryTab
from ui.help.dashboard_ui import apply_help


class AppHistoryModule(BaseModule):
    name = "App history"
    icon = "📈"
    description = "Cumulative CPU time per program since it started"
    requires_admin = False
    group = ModuleGroup.OVERVIEW

    def __init__(self) -> None:
        super().__init__()
        self._widget: Optional[AppHistoryTab] = None

    def on_start(self, app) -> None:
        self.app = app

    def create_widget(self) -> QWidget:
        self._widget = AppHistoryTab()
        self._widget.set_app(self.app)
        apply_help(self._widget, self.name)
        return self._widget

    def on_activate(self) -> None:
        if self._widget is not None:
            self._widget.start()

    def on_deactivate(self) -> None:
        if self._widget is not None:
            self._widget.stop()

    def on_stop(self) -> None:
        if self._widget is not None:
            self._widget.stop()

    def get_refresh_interval(self) -> Optional[int]:
        """None: this tab drives its own timer."""
        return None

    def get_status_info(self) -> str:
        if self._widget is None:
            return "App history"
        return self._widget.status.text() or "App history"

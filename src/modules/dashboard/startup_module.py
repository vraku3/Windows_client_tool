"""The Startup apps tab, as a child module of the Dashboard.

Thin: the widget is `StartupTab` and the reading comes from the existing
`startup_manager.startup_reader` module. This exists so the tab can be hosted
by `CompositeModule` like any other child, and so it can be tested on its own.

Not `requires_admin`: every source this table reads (Run keys, the Startup
folder, scheduled tasks, services, browser extensions) is readable unelevated,
and rows it cannot read are named in the status line rather than hidden.
"""
from typing import Optional

from PyQt6.QtWidgets import QWidget

from core.base_module import BaseModule
from core.module_groups import ModuleGroup

from .startup_tab import StartupTab
from ui.help.dashboard_ui import apply_help


class StartupModule(BaseModule):
    name = "Startup apps"
    icon = "🚀"
    description = "Everything that runs at startup, with its source"
    requires_admin = False
    group = ModuleGroup.OVERVIEW

    def __init__(self) -> None:
        super().__init__()
        self._widget: Optional[StartupTab] = None

    def on_start(self, app) -> None:
        # Only the app reference here: `create_widget` has not run, so there
        # is no widget to touch yet.
        self.app = app

    def create_widget(self) -> QWidget:
        self._widget = StartupTab()
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
        """None: startup items change only when software does, and each scan
        walks the task scheduler and every service. The tab reads on
        activate, not on a timer.
        """
        return None

    def get_status_info(self) -> str:
        if self._widget is None:
            return "Startup apps"
        return self._widget.status.text() or "Startup apps"

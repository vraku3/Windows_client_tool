"""The Performance tab, as a child module of the Dashboard.

Thin: the widget is `PerformanceTab` and everything real lives there or in
`procengine/`. This exists so the tab can be hosted by `CompositeModule` like
any other child, and so it can be tested on its own.

Not `requires_admin`. Unelevated it still shows every process and every
number the kernel gives up for free -- about half the machine's paths and
users are refused, and the pane says so rather than pretending. Gating the
whole tab behind elevation would hide a table that is most of the way useful
without it.
"""
from typing import Optional

from PyQt6.QtWidgets import QWidget

from core.base_module import BaseModule
from core.module_groups import ModuleGroup

from .performance_tab import PerformanceTab
from ui.help.dashboard_ui import apply_help


class PerformanceModule(BaseModule):
    name = "Performance"
    icon = "📈"
    description = "CPU, memory and the graphs behind them"
    requires_admin = False
    group = ModuleGroup.OVERVIEW

    def __init__(self) -> None:
        super().__init__()
        self._widget: Optional[PerformanceTab] = None

    def on_start(self, app) -> None:
        # Only the app reference here: `create_widget` has not run, so there
        # is no widget to touch yet (CLAUDE.md).
        self.app = app

    def create_widget(self) -> QWidget:
        self._widget = PerformanceTab()
        self._widget.set_app(self.app)
        apply_help(self._widget, self.name)
        return self._widget

    def on_activate(self) -> None:
        if self._widget is not None:
            self._widget.start()

    def on_deactivate(self) -> None:
        """Stop the timer and the workers.

        A process table nobody is looking at must not keep reading the
        machine once a second for the life of the app.
        """
        if self._widget is not None:
            self._widget.stop()

    def on_stop(self) -> None:
        if self._widget is not None:
            self._widget.stop()

    def get_refresh_interval(self) -> Optional[int]:
        """None: this tab drives its own one-second timer.

        Letting the host refresh it as well would read the machine twice per
        tick for one table.
        """
        return None

    def get_status_info(self) -> str:
        if self._widget is None:
            return "Performance"
        return self._widget.status.text() or "Details"

"""The Services tab, as a child module of the Dashboard.

Thin: the widget is `ServicesTab` and the reading comes from the existing
`services_manager.services_module` data layer (WMI). This exists so the tab
can be hosted by `CompositeModule` like any other child, and so it can be
tested on its own.

Not `requires_admin`: the table is a WMI read anyone can do, and the
Dashboard's other tabs all run unelevated. The start/stop/restart actions
need elevation and are refused by the OS when the app is unelevated -- the
tab reports that refusal rather than pretending. Gating the whole tab behind
elevation would hide a list that is fully readable without it.
"""
from typing import Optional

from PyQt6.QtWidgets import QWidget

from core.base_module import BaseModule
from core.module_groups import ModuleGroup

from .services_tab import ServicesTab
from ui.help.dashboard_ui import apply_help


class ServicesModule(BaseModule):
    name = "Services"
    icon = "⚙️"
    description = "Every service, with start/stop/restart and go to process"
    requires_admin = False
    group = ModuleGroup.OVERVIEW

    def __init__(self) -> None:
        super().__init__()
        self._widget: Optional[ServicesTab] = None

    def on_start(self, app) -> None:
        # Only the app reference here: `create_widget` has not run, so there
        # is no widget to touch yet.
        self.app = app

    def create_widget(self) -> QWidget:
        self._widget = ServicesTab()
        self._widget.set_app(self.app)
        apply_help(self._widget, self.name)
        return self._widget

    def on_activate(self) -> None:
        if self._widget is not None:
            self._widget.start()

    def on_deactivate(self) -> None:
        """Stop the timer and the workers.

        A service table nobody is looking at must not re-read WMI every five
        seconds for the life of the app.
        """
        if self._widget is not None:
            self._widget.stop()

    def on_stop(self) -> None:
        if self._widget is not None:
            self._widget.stop()

    def get_refresh_interval(self) -> Optional[int]:
        """None: this tab drives its own five-second timer.

        Letting the host refresh it as well would read the machine twice per
        tick for one table.
        """
        return None

    def get_status_info(self) -> str:
        if self._widget is None:
            return "Services"
        return self._widget.status.text() or "Services"

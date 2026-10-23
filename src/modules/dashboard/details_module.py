"""The Details tab, as a child module of the Dashboard.

Thin: the widget is `DetailsTab` and everything real lives there or in
`procengine/`. This exists so the tab can be hosted by `CompositeModule` like
any other child, and so it can be tested on its own.

Not `requires_admin`. Unelevated it still shows every process and every
number the kernel gives up for free -- about half the machine's paths and
users are refused, and the pane says so rather than pretending. Gating the
whole tab behind elevation would hide a table that is most of the way useful
without it.

This module also owns the Dashboard's global-search provider (W5-04): one
place for the process data, so Details and Processes do not each register a
second provider that would double every hit.
"""
from typing import Optional

from PyQt6.QtWidgets import QWidget

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.search_provider import SearchProvider

from .details_tab import DetailsTab
from .process_search import ProcessSearchProvider
from ui.help.dashboard_ui import apply_help


class DetailsModule(BaseModule):
    name = "Details"
    icon = "📋"
    description = "Every process, with Task Manager's full column set"
    requires_admin = False
    group = ModuleGroup.OVERVIEW

    def __init__(self) -> None:
        super().__init__()
        self._widget: Optional[DetailsTab] = None
        # Built in __init__, not on_start: the registry may ask for the
        # provider before the widget has ever been shown (CLAUDE.md, and the
        # same choice TreeSize makes).
        self._search_provider = ProcessSearchProvider()

    def on_start(self, app) -> None:
        # Only the app reference here: `create_widget` has not run, so there
        # is no widget to touch yet (CLAUDE.md).
        self.app = app

    def create_widget(self) -> QWidget:
        self._widget = DetailsTab()
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

    def get_search_provider(self) -> Optional[SearchProvider]:
        return self._search_provider

    def get_status_info(self) -> str:
        if self._widget is None:
            return "Details"
        return self._widget.status.text() or "Details"

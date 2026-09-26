"""Base for a module whose whole UI is one `LogPane`.

The six diagnostic readers — Event Viewer, CBS, DISM, Windows Update,
Reliability, Crash Dumps — differ in exactly three ways: what they read, which
search provider answers for them, and whether they add a control to the
toolbar. Everything else was copied six times.

A subclass supplies `provider_class` and `load_entries`, and optionally
overrides `build_controls`. It does not touch Qt.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from PyQt6.QtWidgets import QWidget

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.search_provider import SearchProvider
from ui.log_pane import LogPane

logger = logging.getLogger(__name__)


class LogReaderModule(BaseModule):
    """One `LogPane` over one log source."""

    group = ModuleGroup.DIAGNOSE
    requires_admin = False

    #: The SearchProvider class that answers for this source.
    provider_class: Optional[type] = None

    def __init__(self) -> None:
        super().__init__()
        self._pane: Optional[LogPane] = None
        self._activated_slot: Optional[Callable] = None
        self._search_provider: Optional[SearchProvider] = (
            self.provider_class() if self.provider_class else None
        )

    # -- what a subclass provides ----------------------------------------
    def load_entries(self, worker):
        """Read the log. Runs on a worker; return a list of `LogEntry`."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement load_entries()"
        )

    def pane_options(self) -> dict:
        """Extra `LogPane` keyword arguments (columns, detail enricher, summariser)."""
        return {}

    def build_controls(self, toolbar, extra) -> None:
        """Add source-specific widgets to the pane's toolbar. Optional."""
        return None

    # -- BaseModule ------------------------------------------------------
    def create_widget(self) -> QWidget:
        self._pane = LogPane(loader=self.load_entries,
                             extra_controls=self.build_controls,
                             **self.pane_options())
        self._pane.entries_loaded.connect(self._feed_provider)
        if self._activated_slot is not None:
            self._pane.entry_activated.connect(self._activated_slot)
        return self._pane

    def connect_activated(self, slot: Callable) -> None:
        """Hook a double-clicked row.

        The host wires this before the tab has ever been shown, so the pane
        usually does not exist yet; the slot is remembered until it does.
        """
        self._activated_slot = slot
        if self._pane is not None:
            self._pane.entry_activated.connect(slot)

    def _feed_provider(self, entries) -> None:
        # The provider answers global search out of the entries the pane
        # loaded, so it stays empty until this tab has been opened once.
        # That is how Diagnose already behaved; it is not new.
        if self._search_provider is None:
            return
        try:
            self._search_provider.set_entries(entries)
        except Exception:
            logger.debug("Could not hand entries to the %s provider",
                         self.name, exc_info=True)

    def on_activate(self) -> None:
        if self._pane is not None:
            self._pane.load()

    def on_deactivate(self) -> None:
        if self._pane is not None:
            self._pane.cancel()

    def on_stop(self) -> None:
        if self._pane is not None:
            self._pane.cancel()
        self.cancel_all_workers()

    def get_search_provider(self) -> Optional[SearchProvider]:
        return self._search_provider

    def get_status_info(self) -> str:
        if self._pane is None:
            return ""
        return f"{self.name} — {self._pane.row_count()} entries"

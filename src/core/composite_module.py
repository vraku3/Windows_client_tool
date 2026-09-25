"""A module that hosts other modules as tabs.

Several features in this app are three or four sibling views of one subject —
the network tools, the boot tools, the six diagnostic log readers. Each used to
be its own sidebar entry, which made the sidebar long and made TOOLS a drawer.

A `CompositeModule` presents its children as tabs and forwards the module
lifecycle to them, so a child stays an ordinary `BaseModule` that knows nothing
about being hosted. That matters for two reasons: a child can be tested on its
own, and a child can be moved between hosts without being rewritten.

The forwarding rule that earns its keep is on tab change: the outgoing child is
deactivated before the incoming one is activated. Children stop their refresh
timers in `on_deactivate`, so without it a host with three children polls WMI
three times over for as long as the app is open — invisible in tests, obvious
in Task Manager.
"""
from __future__ import annotations

import logging
from time import monotonic
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from core.admin_utils import is_admin
from core.base_module import BaseModule

if TYPE_CHECKING:
    from core.search_provider import SearchProvider

logger = logging.getLogger(__name__)


class CompositeModule(BaseModule):
    """Hosts `children` as tabs. Subclasses set `children` in `__init__`."""

    #: Always False. The host itself is never gated, so it keeps its sidebar
    #: entry when only *some* children need elevation; each gated child becomes
    #: a disabled tab explaining itself.
    requires_admin = False

    def __init__(self) -> None:
        super().__init__()
        self.children: List[BaseModule] = []
        self._tabs: Optional[QTabWidget] = None
        #: index -> child, for tabs that are live (started, not gated)
        self._live: Dict[int, BaseModule] = {}
        #: index -> why this tab is disabled. Admin gating and a crash in
        #: on_start both disable a tab, and they are not the same thing to
        #: whoever is reading the tab.
        self._disabled: Dict[int, str] = {}
        #: index -> the permanent page a child's widget gets added into
        self._pages: Dict[int, QWidget] = {}
        #: indices whose real widget has been built
        self._built: set = set()
        #: index -> when that child was last auto-refreshed (ms)
        self._last_refresh: Dict[int, float] = {}
        self._current: int = -1

    # -- seam so tests can pretend to be elevated ------------------------
    def _is_admin(self) -> bool:
        return is_admin()

    # -- lifecycle -------------------------------------------------------
    def on_start(self, app) -> None:
        self.app = app
        elevated = self._is_admin()
        for index, child in enumerate(self.children):
            if child.requires_admin and not elevated and not child.read_only_unelevated:
                logger.info(
                    "%s: child '%s' requires admin — tab disabled",
                    self.name, child.name,
                )
                self._disabled[index] = (
                    f"⚠️ {child.name} requires administrator privileges.\n\n"
                    "Restart the application as Administrator to enable this tab."
                )
                continue
            try:
                child.on_start(app)
            except Exception:
                logger.exception(
                    "%s: child '%s' failed to start — tab disabled",
                    self.name, child.name,
                )
                self._disabled[index] = (
                    f"⚠️ {child.name} could not start.\n\n"
                    "The details are in the application log."
                )
                continue
            self._live[index] = child

    def on_stop(self) -> None:
        for child in self._live.values():
            try:
                child.on_stop()
            except Exception:
                logger.exception("%s: child '%s' failed to stop", self.name, child.name)
        self.cancel_all_workers()

    def on_activate(self) -> None:
        child = self._live.get(self._current)
        if child is not None:
            child.on_activate()
            self._mark_refreshed(self._current)

    def _mark_refreshed(self, index: int) -> None:
        """Start this child's auto-refresh clock now.

        Activating a child loads it, so the next auto-refresh is due a full
        interval later — not on the host's very next tick.
        """
        self._last_refresh[index] = monotonic() * 1000.0

    def on_deactivate(self) -> None:
        child = self._live.get(self._current)
        if child is not None:
            child.on_deactivate()

    def get_refresh_interval(self) -> Optional[int]:
        """The fastest rate any child wants, or None if none want one.

        `MainWindow._start_module_refresh_timer` reads this off the *selected*
        module, so a composite that did not answer would leave every child it
        hosts with no auto-refresh at all — which is exactly what happened to
        six modules when they were first merged.

        One timer at the fastest rate drives them all; `refresh_data` below
        keeps a slower child from being polled at a faster child's rate.
        """
        intervals = [
            interval for child in self.children
            if (interval := child.get_refresh_interval()) is not None
        ]
        return min(intervals) if intervals else None

    def refresh_data(self) -> None:
        """Tick the visible child, but only as often as that child asked for.

        The host's timer runs at the fastest rate any child wants, so a child
        that asked for 120s would otherwise be refreshed every 15s. Store Apps
        shelling out to `Get-AppxPackage` eight times over is the cost of
        getting this wrong.
        """
        child = self._live.get(self._current)
        if child is None:
            return
        wanted = child.get_refresh_interval()
        if wanted is None:
            return  # this child does not want auto-refresh at all

        host_tick = self.get_refresh_interval() or wanted
        now = monotonic() * 1000.0
        last = self._last_refresh.get(self._current)
        # Half a host tick of slack, so jitter never costs a whole period.
        if last is not None and (now - last) < (wanted - host_tick / 2):
            return
        self._last_refresh[self._current] = now
        child.refresh_data()

    # -- widget ----------------------------------------------------------
    def create_widget(self) -> QWidget:
        self._tabs = QTabWidget()
        for index, child in enumerate(self.children):
            # "&" makes the next letter a keyboard mnemonic and is not drawn, so
            # "Power & Freq" read "Power Freq". Doubled, it is shown as written.
            label = f"{getattr(child, 'icon', '')} {child.name}".strip().replace("&", "&&")
            # Every tab gets a permanent page with a layout. A lazily built
            # child widget is ADDED to that layout later; the page itself is
            # never swapped out. Swapping (removeTab/insertTab) on the current
            # index fires currentChanged again and re-enters the handler that
            # asked for the build.
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            self._pages[index] = page
            self._tabs.addTab(page, label)
            if index not in self._live:
                layout.addWidget(self._notice(self._disabled.get(
                    index, f"{child.name} is unavailable.")))
                self._tabs.setTabEnabled(index, False)

        # Connect only after every tab is in place: addTab() fires
        # currentChanged(0) synchronously for the first tab, and connecting
        # first would build a child during create_widget(). DiagnoseModule
        # learned this the hard way — see its comment at create_widget.
        self._tabs.currentChanged.connect(self._on_tab_changed)

        first = self._first_live_index()
        if first is not None:
            self._current = first
            self._tabs.setCurrentIndex(first)
            self._ensure_built(first)
        return self.wrap(self._tabs)

    def wrap(self, tabs: QTabWidget) -> QWidget:
        """Return the widget the shell should show. Override to add chrome.

        Default is the bare tab widget. `DiagnoseModule` overrides this to keep
        its unified search bar and results tree above the tabs.
        """
        return tabs

    def disabled_reason(self, child_name: str) -> str:
        """Why that child's tab is disabled, or "" if it is live."""
        for index, child in enumerate(self.children):
            if child.name == child_name:
                return self._disabled.get(index, "")
        return ""

    def _first_live_index(self) -> Optional[int]:
        for index in range(len(self.children)):
            if index in self._live:
                return index
        return None

    def _notice(self, text: str) -> QWidget:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        return label

    def _ensure_built(self, index: int) -> None:
        if index in self._built:
            return
        child = self._live.get(index)
        page = self._pages.get(index)
        if child is None or page is None or self._tabs is None:
            return
        self._built.add(index)  # set first: a failed build must not retry
        try:
            widget = child.create_widget()
        except Exception:
            logger.exception(
                "%s: child '%s' failed to build its widget", self.name, child.name
            )
            self._disabled[index] = (
                f"⚠️ {child.name} could not be displayed.\n\n"
                "The details are in the application log."
            )
            widget = self._notice(self._disabled[index])
            self._tabs.setTabEnabled(index, False)
            self._live.pop(index, None)
        page.layout().addWidget(widget)

    def _on_tab_changed(self, index: int) -> None:
        if index == self._current or index < 0:
            return
        outgoing = self._live.get(self._current)
        self._current = index
        if outgoing is not None:
            try:
                outgoing.on_deactivate()
            except Exception:
                logger.exception("%s: on_deactivate failed", self.name)
        self._ensure_built(index)
        incoming = self._live.get(index)
        if incoming is not None:
            try:
                incoming.on_activate()
                self._mark_refreshed(index)
            except Exception:
                logger.exception("%s: on_activate failed", self.name)

    # -- search and navigation -------------------------------------------
    def get_search_providers(self) -> List["SearchProvider"]:
        providers: List["SearchProvider"] = []
        for child in self.children:
            providers.extend(child.get_search_providers())
        return providers

    def route_map(self) -> Dict[str, Tuple[str, int]]:
        """`{child name: (this host's name, tab index)}` for name-based nav."""
        return {child.name: (self.name, i) for i, child in enumerate(self.children)}

    def select_child(self, name: str) -> bool:
        for index, child in enumerate(self.children):
            if child.name == name:
                if self._tabs is not None:
                    self._tabs.setCurrentIndex(index)
                return True
        return False

# src/ui/main_window.py
import datetime
import logging
from typing import Dict, Optional

from PyQt6.QtCore import QByteArray, Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QPushButton, QScrollArea, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from core.admin_utils import is_admin, restart_as_admin
from core.base_module import BaseModule
from core.events import NOTIFY_BALLOON, NAV_REQUEST_MODULE
from ui.sidebar_nav import SidebarNav
from ui.status_bar import AppStatusBar
from ui.toolbar import DynamicToolbar
from ui.search_bar import SearchBar
from ui.filter_panel import FilterPanel
from ui.search_results import SearchResultsTable
from ui.notification_tray import SystemTrayManager

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Application shell: SidebarNav + QStackedWidget, toolbar, menu, status bar."""

    def __init__(self, app_instance):
        super().__init__()
        self._app = app_instance
        self.setWindowTitle("Windows 11 Tweaker & Optimizer")
        self._restore_window_geometry()

        self._module_map: Dict[str, BaseModule] = {}
        self._module_widgets: Dict[str, QWidget] = {}
        #: One permanent page per module, added to the stack at registration.
        #: The module's real widget goes inside it on first selection.
        self._module_pages: Dict[str, QWidget] = {}
        #: Modules whose widget has been built (or deliberately never will
        #: be, for a disabled one). Membership is set BEFORE create_widget
        #: runs, so a pane that raises is not retried on every visit.
        self._built: set[str] = set()
        self._active_module: Optional[BaseModule] = None
        self._module_refresh_timers: Dict[str, QTimer] = {}
        self._auto_refresh_paused: bool = self._app.config.get(
            "app.auto_refresh_paused", False
        )
        self._always_on_top: bool = self._app.config.get(
            "app.always_on_top", False
        )
        self._minimize_paused: bool = False  # True if WE paused due to window hide
        self._first_show: bool = True  # Defer first module activation until after show()

        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        if not is_admin():
            root_layout.addWidget(self._create_admin_banner())

        self._sidebar = SidebarNav()
        self._sidebar.set_admin(is_admin())
        self._stack = QStackedWidget()

        # Named for assistive technology. Without these a screen reader
        # announces "list" and "frame" and gives the user no way to tell
        # the navigation from the pane it controls.
        self._sidebar.setAccessibleName("Module navigation")
        self._sidebar.setAccessibleDescription(
            "Choose which tool to show. Grouped by Overview, Diagnose, "
            "System, Manage, Optimize, Tools and Process.")
        self._stack.setAccessibleName("Current module")

        # Restore sidebar collapsed state
        saved_collapsed = self._app.config.get("app.sidebar_collapsed", False)
        if saved_collapsed:
            self._sidebar.toggle_collapse()

        # Persist sidebar collapsed state when it changes
        self._sidebar.collapsed_changed.connect(
            lambda collapsed: self._app.config.set("app.sidebar_collapsed", collapsed)
        )

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 1200])
        # Stretch 1: the splitter owns every spare pixel of height. A
        # *horizontal* QSplitter is Expanding across and only Preferred down,
        # and the admin banner above it is a plain QWidget — Preferred too. With
        # nothing in this column claiming the surplus, Qt split it EQUALLY
        # between the two, and a 28px banner was drawn 450px tall with the app
        # crushed into the bottom half. Unelevated only, and it vanished
        # whenever the search results table (Expanding) appeared, which is how
        # it went unnoticed. The same trap waits for anything else added here.
        root_layout.addWidget(splitter, 1)

        self._search_results = SearchResultsTable(self)
        self._search_results.setAccessibleName("Search results")
        self._search_results.setVisible(False)
        self._search_results.result_activated.connect(self._on_result_activated)
        root_layout.addWidget(self._search_results)

        self.setCentralWidget(central)

        self._toolbar = DynamicToolbar(self)
        self.addToolBar(self._toolbar)
        self._search_bar = SearchBar(self)
        self._search_bar.setAccessibleName("Search all modules")
        self._search_bar.search_requested.connect(self._on_search)
        self._search_bar.filter_toggled.connect(self._on_filter_toggled)
        self._toolbar.addWidget(self._search_bar)

        pause_text = "\u23f8 Pause Refresh" if not self._auto_refresh_paused else "\u25b6 Resume Refresh"
        self._pause_refresh_action = QAction(pause_text, self)
        self._pause_refresh_action.triggered.connect(self._toggle_auto_refresh_pause)
        self._toolbar.addAction(self._pause_refresh_action)

        self._filter_panel = FilterPanel(self)
        self._filter_panel.setAccessibleName("Search filters")
        self._filter_panel.setVisible(False)
        self._setup_filter_search_debounce()
        self._setup_status_info_refresh()
        root_layout.insertWidget(root_layout.indexOf(splitter), self._filter_panel)

        self._status_bar = AppStatusBar(self)
        self.setStatusBar(self._status_bar)
        self._status_bar.set_admin_status(is_admin())

        self._setup_menus()
        self._setup_shortcuts()
        self._setup_tray()
        # Apply before the window is shown: on a fresh window setWindowFlag
        # takes effect directly, no re-show required.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,
                           self._always_on_top)
        self._sidebar.module_selected.connect(self._on_module_selected)
        self._app.event_bus.subscribe(NOTIFY_BALLOON, self._on_notify_balloon)
        self._app.event_bus.subscribe(NAV_REQUEST_MODULE, self._on_nav_request)
        self._schedule_update_check()
        self._show_module_warnings()

    def _on_notify_balloon(self, data) -> None:
        """Modules publish NOTIFY_BALLOON instead of reaching into MainWindow
        directly, so this is the single place that maps to the tray icon."""
        from PyQt6.QtWidgets import QSystemTrayIcon
        icon_map = {
            "warning": QSystemTrayIcon.MessageIcon.Warning,
            "error": QSystemTrayIcon.MessageIcon.Critical,
        }
        icon = icon_map.get(getattr(data, "icon_type", None), QSystemTrayIcon.MessageIcon.Information)
        self._tray_manager.show_balloon(data.title, data.message, icon)

    def _on_nav_request(self, data) -> None:
        self._navigate_to_module(data.module_name)

    def _restore_window_geometry(self) -> None:
        """Put the window back where it was, on the screen it was on.

        Only [width, height] used to be saved, so maximise-quit-reopen
        came back windowed and centred on the primary display — the app you
        keep on the second screen opened on the first one every launch.
        saveGeometry/restoreGeometry encode position, size, maximised state
        and screen identity together.
        """
        blob = self._app.config.get("app.window_geometry", None)
        if blob:
            try:
                if self.restoreGeometry(QByteArray.fromBase64(blob.encode("ascii"))):
                    self._ensure_on_screen()
                    return
            except (ValueError, TypeError, UnicodeEncodeError):
                # It is base64 inside a JSON file a person can edit.
                logger.warning("stored window geometry could not be read; "
                               "falling back to the saved size", exc_info=True)

        # Pre-geometry configs, and the first run after an upgrade.
        size = self._app.config.get("app.window_size", [1400, 900])
        self.resize(size[0], size[1])

    def _save_window_geometry(self) -> None:
        self._app.config.set(
            "app.window_geometry",
            bytes(self.saveGeometry().toBase64()).decode("ascii"))
        # Kept in step so a downgrade, or a config read by something older,
        # still finds a usable size.
        size = self.size()
        self._app.config.set("app.window_size", [size.width(), size.height()])

    def _ensure_on_screen(self) -> None:
        """Drag the window back if the screen it was on is gone.

        Restoring geometry from an unplugged monitor leaves the window
        somewhere unreachable, and nothing on screen says so — the app
        simply appears not to have started.
        """
        frame = self.frameGeometry()
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        for candidate in screen.virtualSiblings():
            if candidate.availableGeometry().intersects(frame):
                return

        available = screen.availableGeometry()
        logger.info("saved window geometry is off-screen; recentering")
        self.resize(min(self.width(), available.width()),
                    min(self.height(), available.height()))
        self.move(available.center() - self.rect().center())

    def _create_admin_banner(self) -> QWidget:
        banner = QWidget()
        banner.setStyleSheet("background-color: #805500; padding: 4px;")
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(8, 4, 8, 4)
        label = QLabel("Some features require administrator privileges.")
        label.setStyleSheet("color: white;")
        layout.addWidget(label)
        layout.addStretch()
        btn = QPushButton("Restart as Admin")
        btn.clicked.connect(self._on_restart_as_admin)
        layout.addWidget(btn)
        return banner

    def _show_module_warnings(self) -> None:
        failed = self._app.module_registry.failed_modules
        if not failed:
            return
        names = ", ".join(m.name for m in failed)
        QMessageBox.warning(
            self,
            "Some Modules Failed to Load",
            f"The following modules failed to start and are disabled:\n\n"
            f"{names}\n\nCheck the application log for details.",
        )

    def _on_restart_as_admin(self) -> None:
        reply = QMessageBox.question(
            self, "Restart as Administrator",
            "The application will restart with elevated privileges. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            restart_as_admin()

    def register_module(self, module: BaseModule) -> None:
        """Register a module: reserve its page, add it to sidebar and stack.

        The widget is NOT built here. Building all 33 up front cost 1.78s of
        a 2.10s startup — against 0.17s to import every module package and
        0.05s to stand up App — and 32 of them were panes the user was not
        looking at. `_ensure_built` does it on first selection instead.

        The page is permanent and the real widget is added into its layout.
        Never removeWidget/insertWidget to swap one in: on the current page
        that re-enters the handler which asked for the build, the same trap
        CompositeModule documents for its tabs.
        """
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)

        enabled = module not in self._app.module_registry.disabled_modules
        if not enabled:
            placeholder = QLabel(
                f"⚠️ {module.name} requires administrator privileges.\n\n"
                "Restart the application as Administrator to enable this module."
            )
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setWordWrap(True)
            page_layout.addWidget(placeholder)
            self._module_widgets[module.name] = placeholder
            # Nothing left to build, so first selection must not try.
            self._built.add(module.name)

        self._stack.addWidget(page)
        self._module_map[module.name] = module
        self._module_pages[module.name] = page

        self._sidebar.add_module(
            group=module.group,
            name=module.name,
            icon=getattr(module, "icon", ""),
            display=module.name,
            requires_admin=module.requires_admin,
        )

        # Auto-select first enabled module. Both the build and the
        # activation are deferred to showEvent, so nothing here blocks the
        # window appearing.
        if self._active_module is None and enabled:
            self._sidebar.select(module.name)
            self._active_module = module
            self._stack.setCurrentWidget(page)

    def _ensure_built(self, module: BaseModule) -> None:
        """Build `module`'s widget into its page, once.

        create_widget() runs pane code that reaches WMI, the registry and
        subprocesses. It used to run inside register_module, where nothing
        caught it — one failing pane took the whole window with it before
        anything was on screen. Here a failure costs that one pane.
        """
        if module.name in self._built:
            return
        self._built.add(module.name)

        try:
            widget = module.create_widget()
        except Exception:
            logger.exception("Module '%s' failed to build its widget", module.name)
            widget = QLabel(
                f"{module.name} failed to load.\n\n"
                "See the application log for details."
            )
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            widget.setWordWrap(True)

        page = self._module_pages.get(module.name)
        if page is not None:
            # In a scroll area so a pane's MINIMUM size cannot become the
            # window's. Measured 2026-09-24: Driver Manager's rows of buttons
            # add up to a 3476px minimum, and selecting it made the main window
            # 3660px wide -- wider than a 2560px monitor -- and it stayed that
            # way for every tab after. Log Viewer (2172px), TreeSize (1581px)
            # and Monitor Control (1280px) did the same. A pane wider than the
            # window now scrolls instead of stretching the window off-screen.
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(widget)
            page.layout().addWidget(scroll)
        self._module_widgets[module.name] = widget

    def _on_module_selected(self, name: str) -> None:
        # Stop previous module's refresh timer
        if self._active_module is not None:
            prev_name = self._active_module.name
            if prev_name in self._module_refresh_timers:
                self._module_refresh_timers[prev_name].stop()
                del self._module_refresh_timers[prev_name]
            try:
                self._active_module.on_deactivate()
            except Exception:
                logger.exception("Error deactivating %s", self._active_module.name)

        module = self._module_map.get(name)
        if module is None:
            return
        self._active_module = module
        self._ensure_built(module)
        page = self._module_pages.get(name)
        if page is not None:
            self._stack.setCurrentWidget(page)
        # A module disabled for want of elevation never built its widget --
        # the page shows a placeholder. Activating it, or ticking a refresh
        # timer for it, calls into a pane that does not exist (Firewall Rules:
        # `AttributeError: _refresh_btn`, logged on every visit and again on
        # every timer tick for as long as the app ran unelevated).
        disabled = module in self._app.module_registry.disabled_modules
        if not disabled:
            try:
                module.on_activate()
            except Exception:
                logger.exception("Error activating %s", name)

            # Start auto-refresh timer if configured and not paused
            interval = module.get_refresh_interval()
            if interval is not None and not self._auto_refresh_paused:
                self._start_module_refresh_timer(module, interval)

        self._toolbar.set_module_actions(
            [] if disabled else module.get_toolbar_actions())
        self._status_bar.set_module_info(
            "Requires administrator" if disabled else module.get_status_info())
        self._status_bar.set_last_updated(
            datetime.datetime.now().strftime("%H:%M:%S"))

    def _start_module_refresh_timer(self, module: BaseModule, interval: int) -> None:
        """Start an auto-refresh QTimer for a module."""
        timer = QTimer()
        timer.setInterval(interval)
        refresh_method = getattr(module, "refresh_data", None) or module.on_activate

        def _tick() -> None:
            try:
                refresh_method()
            except Exception:
                logger.exception("Auto-refresh failed for %s", module.name)
            # The status bar's "Updated HH:MM:SS" — one clock, every tab.
            self._status_bar.set_last_updated(
                datetime.datetime.now().strftime("%H:%M:%S"))

        timer.timeout.connect(_tick)
        timer.start()
        self._module_refresh_timers[module.name] = timer

    def hideEvent(self, event):
        """Auto-pause all refresh timers when window is hidden/minimized."""
        if not self._auto_refresh_paused:
            self._minimize_paused = True
            for timer in self._module_refresh_timers.values():
                timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        """Resume refresh timers when window becomes visible again."""
        if self._first_show:
            self._first_show = False
            if self._active_module is not None:
                # The auto-selected module is built here rather than at
                # registration, so the window is on screen before any pane
                # code runs.
                self._ensure_built(self._active_module)
                try:
                    self._active_module.on_activate()
                except Exception:
                    logger.exception("Error activating first module %s", self._active_module.name)
        if self._minimize_paused:
            self._minimize_paused = False
            for timer in self._module_refresh_timers.values():
                timer.start()
        super().showEvent(event)

    def _toggle_auto_refresh_pause(self) -> None:
        """Globally pause or resume all module auto-refresh timers."""
        self._auto_refresh_paused = not self._auto_refresh_paused
        self._app.config.set("app.auto_refresh_paused", self._auto_refresh_paused)

        if self._auto_refresh_paused:
            # Stop all active timers
            for timer in self._module_refresh_timers.values():
                timer.stop()
            self._pause_refresh_action.setText("\u25b6 Resume Refresh")
        else:
            # Restart timer for current module
            module = self._active_module
            if module is not None:
                interval = module.get_refresh_interval()
                if interval is not None:
                    # Stop any existing timer before starting a new one
                    if module.name in self._module_refresh_timers:
                        self._module_refresh_timers[module.name].stop()
                        del self._module_refresh_timers[module.name]
                    self._start_module_refresh_timer(module, interval)
            self._pause_refresh_action.setText("\u23f8 Pause Refresh")

    def _setup_menus(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        settings_action = QAction("&Settings", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self._open_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence("Alt+F4"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        tools_menu = menu_bar.addMenu("&Tools")
        restore_action = QAction("&Restore Manager...", self)
        restore_action.triggered.connect(self._open_restore_manager)
        tools_menu.addAction(restore_action)

        view_menu = menu_bar.addMenu("&View")
        theme_action = QAction("Toggle &Theme", self)
        theme_action.triggered.connect(self._toggle_theme)
        view_menu.addAction(theme_action)
        self._always_on_top_action = QAction("Always on &top", self)
        self._always_on_top_action.setCheckable(True)
        self._always_on_top_action.setChecked(self._always_on_top)
        self._always_on_top_action.triggered.connect(
            self._toggle_always_on_top)
        view_menu.addAction(self._always_on_top_action)

        # Force shutdown / restart / restart-to-Safe-Mode: three pictogram
        # buttons on the menu bar's own corner, visually right next to
        # File / Tools / View — the fastest place to reach them.
        from ui.power_actions import PowerActionsWidget

        menu_bar.setCornerWidget(PowerActionsWidget(self))

    def _setup_shortcuts(self) -> None:
        QShortcut(QKeySequence("F5"), self).activated.connect(self._refresh_current)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(
            self._search_bar.focus_search)
        QShortcut(QKeySequence("Ctrl+Shift+F"), self).activated.connect(
            self._search_bar.focus_search_with_filters)
        QShortcut(QKeySequence("Escape"), self).activated.connect(self._clear_search)
        QShortcut(QKeySequence("Ctrl+P"), self).activated.connect(self._open_command_palette)

    def _setup_tray(self) -> None:
        from PyQt6.QtWidgets import QStyle
        icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self._tray_manager = SystemTrayManager(self, icon)
        self._tray_manager.show()
        self._tray_manager.connect_activated(self._on_tray_activated)

    def _on_tray_activated(self, reason) -> None:
        from PyQt6.QtWidgets import QSystemTrayIcon
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.raise_()
                self.activateWindow()

    def _open_command_palette(self) -> None:
        from ui.command_palette import CommandPalette
        modules = list(self._module_map.values())
        palette = CommandPalette(modules, self._navigate_to_module, self)
        # Centre near top of main window
        geo = self.geometry()
        pw = palette.sizeHint().width() or 480
        x = geo.x() + (geo.width() - pw) // 2
        y = geo.y() + 80
        palette.move(x, y)
        palette.exec()

    def _navigate_to_module(self, name: str) -> None:
        """Select the module called `name` — or whatever now contains it.

        A module that became a tab of a composite has no sidebar entry of its
        own any more, so a plain sidebar lookup would silently do nothing for
        every caller that still asks for it by name.
        """
        from ui.navigation import resolve_target

        target, tab = resolve_target(
            name,
            set(self._module_map),
            self._app.module_registry.route_map(),
        )
        if target is None:
            logger.warning("Navigation: nothing named %r", name)
            return
        self._sidebar.select(target)
        self._on_module_selected(target)
        if tab is not None:
            module = self._module_map.get(target)
            select_child = getattr(module, "select_child", None)
            if callable(select_child):
                select_child(name)

    def _schedule_update_check(self) -> None:
        """Run the update check once in the background 3 seconds after startup."""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(3000, self._run_update_check)

    def _run_update_check(self) -> None:
        # Set app.update_repo (in config.json, "owner/repo") to enable this —
        # left blank until the project has a real public release repo, so we
        # don't hit the GitHub API with a placeholder value on every launch.
        repo = self._app.config.get("app.update_repo", "").strip()
        if not repo:
            return

        from core.worker import Worker
        from core.update_checker import UpdateChecker
        from modules.about.about_module import _APP_VERSION

        def _check(_w):
            return UpdateChecker(repo, _APP_VERSION).check()

        worker = Worker(_check)
        worker.signals.result.connect(self._on_update_result)
        self._update_worker = worker
        from PyQt6.QtCore import QThreadPool
        QThreadPool.globalInstance().start(worker)

    def _on_update_result(self, result) -> None:
        update_worker = getattr(self, "_update_worker", None)
        if update_worker is not None and update_worker.is_cancelled:
            return
        if result.error or not result.update_available:
            return
        self._tray_manager.show_balloon(
            "Update Available",
            f"Version {result.latest_version} is available. "
            f"Current: {result.current_version}",
        )
        logger.info(
            "Update available: %s → %s (%s)",
            result.current_version, result.latest_version, result.release_url,
        )

    def show_notification(self, title: str, message: str) -> None:
        """Public helper for modules to show a tray balloon notification."""
        self._tray_manager.show_balloon(title, message)

    def _refresh_current(self) -> None:
        if self._active_module:
            try:
                self._active_module.on_activate()
            except Exception:
                logger.exception("Error refreshing %s", self._active_module.name)

    def _open_settings(self) -> None:
        from ui.settings_dialog import SettingsDialog
        SettingsDialog(self._app, self).exec()

    def _open_restore_manager(self) -> None:
        try:
            from ui.restore_manager import RestoreManagerDialog
            RestoreManagerDialog(self._app, self).exec()
        except ImportError:
            QMessageBox.information(self, "Coming Soon",
                                    "Restore Manager will be available in a future update.")

    def _toggle_theme(self) -> None:
        new_theme = self._app.theme.toggle()
        self._app.config.set("app.theme", new_theme)

    def _toggle_always_on_top(self, checked: bool) -> None:
        """Keep the window above other windows, Task Manager style.

        Changing the WindowStaysOnTopHint flag after the window is shown
        needs the window re-shown for the flag to take effect; the same
        call that works on a fresh window silently does nothing on a shown
        one.
        """
        self._always_on_top = bool(checked)
        self._app.config.set("app.always_on_top", self._always_on_top)
        self._apply_always_on_top()

    def _apply_always_on_top(self) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint,
                           self._always_on_top)
        self.show()

    def _on_search(self, text: str, regex: bool) -> None:
        if not text.strip():
            self._search_results.setVisible(False)
            return
        query = self._filter_panel.build_query(text, regex)
        results = self._app.search.execute(query)
        self._search_results.set_results(results)
        self._search_results.setVisible(bool(results))
        self._status_bar.showMessage(
            f"Search: {len(results)} result(s) for '{text}'"
        )

    def _on_filter_toggled(self, expanded: bool) -> None:
        self._filter_panel.setVisible(expanded)

    def _setup_status_info_refresh(self) -> None:
        """Keep the module's status-bar text current.

        `get_status_info()` was read once, when a tab was selected -- before an
        asynchronous load had finished -- so a tab that fills in a moment later
        kept saying "Restore Manager -- 0 points" above a list of three. It is
        a string per call, so re-reading it every couple of seconds is cheap."""
        self._status_info_timer = QTimer(self)
        self._status_info_timer.setInterval(2000)
        self._status_info_timer.timeout.connect(self._refresh_status_info)
        self._status_info_timer.start()

    def _refresh_status_info(self) -> None:
        module = self._active_module
        if module is None or module in self._app.module_registry.disabled_modules:
            return
        try:
            text = module.get_status_info()
        except Exception:
            logger.exception("get_status_info failed for %s", module.name)
            return
        self._status_bar.set_module_info(text)

    def _setup_filter_search_debounce(self) -> None:
        """One search per burst of filter changes, not one per checkbox: a
        Reset that flips several of them used to run that many synchronous
        searches on the UI thread."""
        self._filter_search_timer = QTimer(self)
        self._filter_search_timer.setSingleShot(True)
        self._filter_search_timer.setInterval(250)
        self._filter_search_timer.timeout.connect(self._rerun_search_with_filters)
        self._filter_panel.filters_changed.connect(self._on_filters_changed)

    def _on_filters_changed(self, _query) -> None:
        """A filter widget changed -- re-run the active search with it.

        `FilterPanel.filters_changed` is real, but nothing was ever
        connected to it: adjusting the date range or a source checkbox
        silently did nothing until the next keystroke re-triggered
        `_on_search` on its own. `_query` itself carries an empty search
        text (`FilterPanel` has no way to know it), so the real text comes
        from the search bar -- the same source `_on_search` always uses.
        `_on_search` already no-ops when there is nothing to search for.
        Debounced: the search itself runs `_rerun_search_with_filters` once the
        changes stop arriving.
        """
        self._filter_search_timer.start()

    def _rerun_search_with_filters(self) -> None:
        self._on_search(self._search_bar.text(), self._search_bar.is_regex())

    def _on_result_activated(self, result) -> None:
        from ui.search_result_detail import SearchResultDetail
        SearchResultDetail(result, self).exec()

    def _clear_search(self) -> None:
        self._search_bar.clear()
        self._search_results.setVisible(False)
        self._filter_panel.setVisible(False)

    def closeEvent(self, event) -> None:
        # The minimize-to-tray branch comes FIRST, and returns without
        # touching the timers. Tearing them down above it and then calling
        # event.ignore() left showEvent's resume loop with an empty dict, so
        # hiding to the tray silently ended auto-refresh for the rest of the
        # session — the Dashboard just stopped updating and looked like a
        # very idle machine. hideEvent stops the timers on its own when the
        # window hides, so nothing is left ticking behind a hidden window.
        if self._app.config.get("app.minimize_to_tray", False):
            event.ignore()
            self.hide()
            self._tray_manager.show_balloon(
                "Still Running",
                "Windows Tweaker is minimized to the tray. Double-click to restore.",
            )
            return

        for timer in self._module_refresh_timers.values():
            timer.stop()
        self._module_refresh_timers.clear()

        update_worker = getattr(self, "_update_worker", None)
        if update_worker is not None:
            update_worker.cancel()

        self._save_window_geometry()
        self._app.shutdown()
        event.accept()
        super().closeEvent(event)

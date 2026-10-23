"""The ribbon (spec 5.2).

Qt ships no ribbon widget, so this builds one: a QTabBar over a QStackedWidget,
each page a horizontal row of bordered *groups*. A group carries a caption at
the bottom and a mix of one large button (32 px icon, label beneath) and stacked
small buttons (16 px icon, label beside), with separators between groups.

Every tab, group and button name here is the product's own, via spec 5.2, which
took them from the installed help. They are not invented approximations -- the
point of the module is to be recognisable to someone who uses the real thing.

Actions are exposed as QAction objects on `actions`, keyed by a stable id, so
the shell wires behaviour without the ribbon knowing what anything does.
"""

from modules.treesize.help_widgets import (
    describe_control, explain_widget,
)
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMenu, QSizePolicy, QStackedWidget, QTabBar,
    QToolButton, QVBoxLayout, QWidget,
)

LARGE_ICON = QSize(32, 32)
SMALL_ICON = QSize(16, 16)

# (tab, group, [(action_id, label, is_large, has_dropdown)])
# "Create portable installation" is deliberately absent from Tools: this module
# ships inside a host application and has no independent installation to make
# portable. Spec 5.2 says omit it rather than show it disabled.
RIBBON: tuple = (
    ("Home", (
        ("Scan", (("scan.select", "Select scan\ntarget", True, True),
                  ("scan.stop", "Stop", False, True),
                  ("scan.refresh", "Refresh scan", False, True),
                  ("scan.remove", "Remove scan", False, False))),
        ("Mode", (("mode.size", "Size", False, False),
                  ("mode.allocated", "Allocated space", False, False),
                  ("mode.files", "Number of files", False, False),
                  ("mode.percent", "Percent", False, False))),
        ("Unit", (("unit.auto", "Auto", False, True),
                  ("unit.gb", "GB", False, False),
                  ("unit.mb", "MB", False, False),
                  ("unit.kb", "KB", False, False))),
        ("Directory Tree", (("tree.expand", "Expand", True, True),)),
        ("Scan Result", (("result.email", "Send by email", False, False),
                         ("result.export", "Export", False, True))),
        ("Tools", (("tools.search", "Open TreeSize\nFile Search", True, True),
                   ("tools.admin", "Start as administrator", False, False),
                   ("tools.options", "Options", False, True))),
    )),
    ("Scan", (
        ("Scan", (("scan.stop", "Stop", True, False),
                  ("scan.pause", "Pause", True, False),
                  ("scan.refresh", "Refresh", False, True),
                  ("scan.remove", "Remove scan", False, False))),
        ("Watch", (("scan.watch", "Watch for file\nsystem changes", True, False),)),
        ("Directory Tree", (("tree.expand", "Expand", False, True),
                            ("tree.find", "Find", False, False))),
        ("Result", (("result.export", "Export", False, True),
                    ("scan.exclude", "Exclude", False, True))),
        ("Compare", (("compare.saved", "Compare with saved scan", False, False),
                     ("compare.snapshot", "Compare with snapshot", False, False),
                     ("compare.path", "Compare with path", False, False),
                     ("view.changes", "Show size changes", False, False))),
        ("Schedule", (("scan.schedule", "Schedule\nthis scan", True, True),)),
    )),
    ("Tools", (
        ("Settings", (("tools.options", "Options", True, True),)),
        ("Tools", (("tools.search", "Open TreeSize File Search", False, True),
                   ("tools.duplicates", "Find duplicate files", False, False),
                   ("tools.scheduled", "Manage scheduled scans", False, False),
                   ("tools.snapshot", "Create snapshot", False, False))),
        ("System", (("tools.recyclebin", "Empty recycle bin", False, False),
                    ("tools.software", "Remove obsolete software", False, False),
                    ("tools.restore", "Configure Windows System Restore", False, False),
                    ("tools.mapdrive", "Map network drive", False, False))),
    )),
    ("View", (
        ("Views", (("view.select", "Select active\nView", True, True),)),
        ("Mode", (("mode.size", "Size", False, False),
                  ("mode.allocated", "Allocated space", False, False),
                  ("mode.files", "Number of files", False, False),
                  ("mode.percent", "Percent", False, False))),
        ("Unit", (("unit.auto", "Auto", False, False),
                  ("unit.tb", "TB", False, False),
                  ("unit.gb", "GB", False, False),
                  ("unit.mb", "MB", False, False),
                  ("unit.kb", "KB", False, False),
                  ("unit.b", "B", False, False),
                  ("unit.decimals", "Decimals", False, True))),
        ("Sort", (("sort.size", "Sort by size", False, False),
                  ("sort.name", "Sort by name", False, False),
                  ("view.group", "Group scans", False, False))),
        ("Show", (("panel.drives", "Drive list", False, False),
                  ("panel.overview", "Scan overview", False, False),
                  ("panel.status", "Status bar", False, False),
                  ("view.hideempty", "Hide empty folders", False, False),
                  ("view.hidesmall", "Hide elements smaller than", False, True))),
    )),
    # Contextual tab. Shown only while the Details view is active, which is
    # what "Details Tools" means in a ribbon: a tab that appears with its
    # object and disappears with it.
    ("Details Tools", (
        ("Columns", (("details.columns", "Choose\ncolumns", True, True),
                     ("details.reset", "Reset columns", False, False))),
        ("Fit", (("details.fit", "Fit columns to content", False, False),
                 ("details.autosize", "Autosize on refresh", False, False))),
    )),
    ("Help", (
        ("Help", (("help.contents", "Help contents", True, False),
                  ("help.about", "About", False, False))),
    )),
)

# Dropdown contents, keyed by the button's action id. A tuple entry is
# (item_id, label); None is a separator. Items become QActions in the same
# registry as the buttons, so the shell wires them exactly the same way.
MENUS: dict = {
    "scan.select": (),          # filled at runtime with the drive list
    "scan.stop": (("scan.stop.all", "Stop all scans"),),
    "scan.refresh": (("scan.refresh.all", "Refresh all scans"),
                     ("scan.refresh.selected", "Refresh selected folder"),
                     None,
                     ("scan.watch", "Watch for file system changes")),
    "tree.expand": (("tree.expand.1", "Expand one level"),
                    ("tree.expand.2", "Expand two levels"),
                    ("tree.expand.3", "Expand three levels"),
                    None,
                    ("tree.expand.all", "Expand all"),
                    # Spec 5.4's fifth entry. A fixed depth is too shallow on
                    # one branch and too deep on the next; a threshold is the
                    # one that follows where the space actually went.
                    ("tree.expand.size", "Expand to size…"),
                    None,
                    ("tree.collapse.all", "Collapse all")),
    "unit.auto": (("unit.auto", "Auto"), None,
                  ("unit.tb", "TB"), ("unit.gb", "GB"), ("unit.mb", "MB"),
                  ("unit.kb", "KB"), ("unit.b", "B"), None,
                  ("unit.decimals.0", "0 decimals"),
                  ("unit.decimals.1", "1 decimal"),
                  ("unit.decimals.2", "2 decimals")),
    "unit.decimals": (("unit.decimals.0", "0 decimals"),
                      ("unit.decimals.1", "1 decimal"),
                      ("unit.decimals.2", "2 decimals")),
    "result.export": (("export.file", "Export…"),
                      None,
                      ("export.csv", "Export to CSV…"),
                      ("export.xlsx", "Export to Excel…"),
                      ("export.pdf", "Export to PDF…"),
                      ("export.html", "Export to HTML…"),
                      ("export.xml", "Export to XML…"),
                      ("export.db", "Export to SQLite…"),
                      ("export.txt", "Export to Text…"),
                      None,
                      ("export.clipboard", "Copy to clipboard")),
    "scan.exclude": (("exclude.selected", "Exclude selected folder"),
                     ("exclude.pattern", "Exclude by pattern…"),
                     # Spec 3.6: exclusions are temporary or permanent. The
                     # two above last one scan; this one is kept in config.
                     ("exclude.permanent", "Always exclude by pattern…"),
                     None,
                     ("exclude.clear", "Clear all exclusions")),
    "view.select": (("view.go.chart", "Chart"),
                    ("view.go.details", "Details"),
                    ("view.go.extensions", "Extensions"),
                    ("view.go.groups", "File groups"),
                    ("view.go.users", "Users"),
                    ("view.go.age", "Age of Files"),
                    ("view.go.top", "Top Files")),
    "view.hidesmall": (("hidesmall.off", "Show everything"),
                       ("hidesmall.1mb", "Hide below 1 MB"),
                       ("hidesmall.10mb", "Hide below 10 MB"),
                       ("hidesmall.100mb", "Hide below 100 MB")),
    "tools.options": (("tools.options.open", "Options…"), None,
                      ("tools.options.export", "Export settings…"),
                      ("tools.options.import", "Import settings…"),
                      ("tools.options.reset", "Reset settings")),
    "tools.search": (("tools.search.open", "Find files\u2026"),
                     ("tools.duplicates", "Find duplicate files\u2026")),
    "scan.schedule": (("schedule.daily", "Run this scan daily"),
                      ("schedule.weekly", "Run this scan weekly"),
                      None,
                      ("schedule.remove", "Remove the scheduled task")),
    "details.columns": (),      # filled at runtime from the Details view
}

#: Tabs that appear only in a particular context, with the tab that owns them.
#: Contextual tabs, by their tab text. Spec 5.2 names this one.
CONTEXTUAL_TABS = {"Details Tools"}

# Buttons that hold a checked/unchecked state rather than firing once.
CHECKABLE = {
    "mode.size", "mode.allocated", "mode.files", "mode.percent",
    "unit.auto", "unit.tb", "unit.gb", "unit.mb", "unit.kb", "unit.b",
    "panel.drives", "panel.overview", "panel.status",
    "view.changes", "view.hideempty", "view.group", "scan.watch",
    "unit.decimals.0", "unit.decimals.1", "unit.decimals.2",
    "details.autosize",
    "hidesmall.off", "hidesmall.1mb", "hidesmall.10mb", "hidesmall.100mb",
}


class RibbonGroup(QFrame):
    """One bordered panel with a caption beneath it."""

    def __init__(self, caption: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ribbonGroup")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 2)
        outer.setSpacing(2)
        self._row = QHBoxLayout()
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(2)
        outer.addLayout(self._row, 1)
        label = QLabel(caption)
        label.setObjectName("ribbonGroupCaption")
        label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(label)
        self._small_column: QVBoxLayout | None = None
        explain_widget(self)

    def add_button(self, action: QAction, large: bool, dropdown: bool,
                   menu: QMenu | None = None) -> QToolButton:
        button = QToolButton(self)
        describe_control(button, 'button')
        button.setDefaultAction(action)
        button.setAutoRaise(True)
        if dropdown and menu is not None:
            button.setMenu(menu)
            # MenuButtonPopup splits the button: the face fires the action, the
            # arrow opens the menu. That is Pro's behaviour -- clicking "Expand"
            # expands, clicking its arrow offers how far.
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        elif dropdown:
            # No menu was declared for this id, so do not draw an arrow. An
            # arrow that opens nothing reads as broken, not as unfinished.
            pass
        if large:
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(LARGE_ICON)
            button.setObjectName("ribbonLargeButton")
            button.setSizePolicy(QSizePolicy.Policy.Preferred,
                                 QSizePolicy.Policy.Expanding)
            self._row.addWidget(button)
            self._small_column = None
        else:
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIconSize(SMALL_ICON)
            button.setObjectName("ribbonSmallButton")
            # Small buttons stack vertically, three to a column, then wrap --
            # which is what gives a ribbon its dense look rather than one long
            # row of little buttons.
            if self._small_column is None or self._small_column.count() >= 3:
                self._small_column = QVBoxLayout()
                self._small_column.setContentsMargins(0, 0, 0, 0)
                self._small_column.setSpacing(1)
                self._row.addLayout(self._small_column)
            self._small_column.addWidget(button)
        return button


class Ribbon(QWidget):
    """Tab bar over stacked pages. Owns the QActions; owns no behaviour."""

    tab_changed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.actions_by_id: dict[str, QAction] = {}
        self.menus_by_id: dict[str, QMenu] = {}
        self._buttons: dict[str, list[QToolButton]] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tab_bar = QTabBar(self)
        self.tab_bar.setObjectName("ribbonTabBar")
        self.tab_bar.setExpanding(False)
        self.tab_bar.setDrawBase(False)
        layout.addWidget(self.tab_bar)

        self.pages = QStackedWidget(self)
        self.pages.setObjectName("ribbonPages")
        layout.addWidget(self.pages)

        for tab_name, groups in RIBBON:
            self.tab_bar.addTab(tab_name)
            self.pages.addWidget(self._build_page(groups))

        # The File tab opens a backstage page, not a dropdown, so it sits at
        # index 0 and is handled by the shell rather than the stack.
        self.tab_bar.insertTab(0, "File")
        self._contextual = {}
        for name in CONTEXTUAL_TABS:
            index = next(i for i in range(self.tab_bar.count())
                         if self.tab_bar.tabText(i) == name)
            self._contextual[name] = index
            self.tab_bar.setTabVisible(index, False)
        self.tab_bar.currentChanged.connect(self._on_tab_changed)
        self.tab_bar.setCurrentIndex(1)
        explain_widget(self)

    def _build_page(self, groups) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(4, 4, 4, 2)
        row.setSpacing(0)
        for caption, buttons in groups:
            group = RibbonGroup(caption, page)
            for action_id, label, large, dropdown in buttons:
                action = self._action(action_id, label)
                group.add_button(action, large, dropdown,
                                 self._menu(action_id) if dropdown else None)
            row.addWidget(group)
            separator = QFrame(page)
            separator.setFrameShape(QFrame.Shape.VLine)
            separator.setObjectName("ribbonSeparator")
            row.addWidget(separator)
        row.addStretch(1)
        return page

    def _action(self, action_id: str, label: str) -> QAction:
        """One QAction per id, shared by every button that references it.

        Stop appears on both Home and Scan; sharing the action means enabling
        it once enables it everywhere, which is the behaviour a ribbon needs.
        """
        existing = self.actions_by_id.get(action_id)
        if existing is not None:
            return existing
        action = QAction(label.replace("\n", " "), self)
        describe_control(action, 'action')
        action.setObjectName(action_id)
        if action_id in CHECKABLE:
            action.setCheckable(True)
        self.actions_by_id[action_id] = action
        return action

    def _menu(self, action_id: str) -> QMenu | None:
        """One QMenu per action id, shared by every button that uses it."""
        if action_id not in MENUS:
            return None
        existing = self.menus_by_id.get(action_id)
        if existing is not None:
            return existing
        menu = QMenu(self)
        for entry in MENUS[action_id]:
            if entry is None:
                menu.addSeparator()
            else:
                describe_control(menu.addAction(self._action(entry[0], entry[1])))
        self.menus_by_id[action_id] = menu
        return menu

    def action(self, action_id: str) -> QAction:
        return self.actions_by_id[action_id]

    def menu(self, action_id: str) -> QMenu | None:
        return self.menus_by_id.get(action_id)

    def set_menu_items(self, action_id: str, items) -> None:
        """Rebuild a menu whose contents are only known at runtime.

        The scan-target list is the case: it is whatever drives exist now, not
        a fixed list decided when the ribbon was built.
        """
        menu = self.menus_by_id.get(action_id)
        if menu is None:
            return
        menu.clear()
        for label, callback in items:
            if label is None:
                menu.addSeparator()
                continue
            if isinstance(callback, (list, tuple)):
                # A LIST rather than a callable means a submenu. The quick
                # scan locations are long enough that one flat menu is a wall.
                if not callback:
                    # An empty submenu is a dead end the user can still open.
                    continue
                sub = menu.addMenu(label)
                for sub_label, sub_callback in callback:
                    if sub_label is None:
                        sub.addSeparator()
                    else:
                        describe_control(sub.addAction(sub_label, sub_callback))
                continue
            describe_control(menu.addAction(label, callback))

    def set_enabled(self, action_id: str, enabled: bool) -> None:
        action = self.actions_by_id.get(action_id)
        if action is not None:
            action.setEnabled(enabled)

    def set_contextual_visible(self, name: str, visible: bool) -> None:
        """Show or hide a contextual tab, without stranding the user on it."""
        index = self._contextual.get(name)
        if index is None:
            return
        if not visible and self.tab_bar.currentIndex() == index:
            self.tab_bar.setCurrentIndex(1)
        self.tab_bar.setTabVisible(index, visible)

    def _on_tab_changed(self, index: int) -> None:
        name = self.tab_bar.tabText(index)
        if index > 0:
            self.pages.setCurrentIndex(index - 1)
        self.tab_changed.emit(name)

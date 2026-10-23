"""The File tab's backstage page and the title row above the ribbon (spec 5.1).

Two pieces of Office-style chrome the ribbon needs to read as a ribbon:

- **Title row** — Quick Access Toolbar on the left, the "Find option (F6)" box
  on the right. Small, but its absence is the first thing that makes a ribbon
  look like a toolbar wearing a costume.
- **Backstage** — the File tab opens a full-pane page, not a dropdown. That is
  what File does in every ribbon application, and doing it as a menu is the
  usual tell that a clone was built from screenshots of the other tabs.

The Find box searches the ribbon's own actions, which is what Pro's "Find
option" does: it finds *commands*, not files.
"""

from modules.treesize.help_widgets import (
    describe_control, explain_widget,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QToolButton, QVBoxLayout, QWidget,
)


class TitleRow(QWidget):
    """Quick Access Toolbar, title, and the Find option box."""

    find_requested = pyqtSignal(str)

    def __init__(self, ribbon, parent=None) -> None:
        super().__init__(parent)
        self._ribbon = ribbon
        self.setObjectName("titleRow")
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 2, 6, 2)
        row.setSpacing(4)

        # QAT: the handful of commands worth reaching without changing tab.
        # NOT setDefaultAction: that binds the button's text to the action's
        # label, so the QAT would read "Refresh scan / Stop / Export" and swamp
        # the row. The QAT shows a glyph and carries the label in its tooltip,
        # which is what a Quick Access Toolbar is for.
        for action_id, glyph, tip in (("scan.refresh", "⟳", "Refresh scan"),
                                      ("scan.stop", "■", "Stop"),
                                      ("result.export", "⇪", "Export")):
            action = ribbon.action(action_id)
            button = QToolButton(self)
            describe_control(button, 'button')
            button.setText(glyph)
            button.setToolTip(tip)
            button.setAutoRaise(True)
            button.setObjectName("qatButton")
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.clicked.connect(action.trigger)
            # Enablement still follows the action, so Stop greys out between
            # scans exactly as it does on the ribbon.
            button.setEnabled(action.isEnabled())
            action.changed.connect(
                lambda b=button, a=action: b.setEnabled(a.isEnabled()))
            row.addWidget(button)

        row.addStretch(1)
        self.title = QLabel("TreeSize", self)
        self.title.setObjectName("paneTitle")
        row.addWidget(self.title)
        row.addStretch(1)

        self.find_box = QLineEdit(self)
        describe_control(self.find_box, 'find_box')
        self.find_box.setPlaceholderText("Find option (F6)")
        self.find_box.setObjectName("findOption")
        self.find_box.setFixedWidth(200)
        self.find_box.textChanged.connect(self.find_requested)
        row.addWidget(self.find_box)

        shortcut = QShortcut(QKeySequence("F6"), self)
        shortcut.activated.connect(self.focus_find)
        explain_widget(self)

    def focus_find(self) -> None:
        self.find_box.setFocus()
        self.find_box.selectAll()


class FindResults(QListWidget):
    """Ribbon commands matching what was typed into the Find box."""

    command_chosen = pyqtSignal(str)

    def __init__(self, ribbon, parent=None) -> None:
        super().__init__(parent)
        self._ribbon = ribbon
        self.setObjectName("findResults")
        self.setMaximumHeight(180)
        self.hide()
        self.itemActivated.connect(self._chosen)
        self.itemClicked.connect(self._chosen)

    def search(self, text: str) -> None:
        self.clear()
        query = text.strip().lower()
        if len(query) < 2:
            self.hide()
            return
        for action_id, action in sorted(self._ribbon.actions_by_id.items()):
            label = action.text()
            if query in label.lower() or query in action_id.lower():
                item = QListWidgetItem(f"{label}    ({action_id})")
                item.setData(Qt.ItemDataRole.UserRole, action_id)
                self.addItem(item)
        self.setVisible(self.count() > 0)

    def _chosen(self, item: QListWidgetItem) -> None:
        action_id = item.data(Qt.ItemDataRole.UserRole)
        if action_id:
            self.command_chosen.emit(action_id)
        self.hide()


class Backstage(QWidget):
    """The File tab's full-pane page."""

    closed = pyqtSignal()
    scan_requested = pyqtSignal(str)
    action_requested = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("backstage")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        rail = QWidget(self)
        rail.setObjectName("backstageRail")
        rail.setFixedWidth(190)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(8, 8, 8, 8)
        rail_layout.setSpacing(2)

        back = QPushButton("←  Back", rail)
        describe_control(back, 'back')
        back.setObjectName("backstageBack")
        back.clicked.connect(self.closed)
        rail_layout.addWidget(back)
        rail_layout.addSpacing(12)

        for label, action_id in (("New scan…", "scan.select"),
                                 ("Refresh", "scan.refresh"),
                                 ("Close scan", "scan.remove"),
                                 ("Export…", "result.export"),
                                 ("Options…", "tools.options.open"),
                                 ("About", "help.about")):
            button = QPushButton(label, rail)
            describe_control(button, 'button')
            button.setObjectName("backstageEntry")
            button.clicked.connect(
                lambda _c=False, a=action_id: self.action_requested.emit(a))
            rail_layout.addWidget(button)
        rail_layout.addStretch(1)
        layout.addWidget(rail)

        body = QWidget(self)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 20)
        heading = QLabel("Recent scans", body)
        heading.setObjectName("backstageHeading")
        body_layout.addWidget(heading)

        self.recent = QListWidget(body)
        self.recent.setObjectName("recentList")
        self.recent.itemActivated.connect(self._scan_chosen)
        self.recent.itemClicked.connect(self._scan_chosen)
        body_layout.addWidget(self.recent, 1)
        layout.addWidget(body, 1)
        explain_widget(self)

    def set_recent(self, targets) -> None:
        self.recent.clear()
        for target in targets:
            self.recent.addItem(QListWidgetItem(target))
        if not targets:
            item = QListWidgetItem("No scans yet — pick a drive or folder to begin.")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.recent.addItem(item)

    def _scan_chosen(self, item: QListWidgetItem) -> None:
        if item.flags() & Qt.ItemFlag.ItemIsEnabled:
            self.scan_requested.emit(item.text())
            self.closed.emit()

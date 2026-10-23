"""File search and duplicate finder dialogs (spec 8.1, 8.2).

Both are dialogs over the store the pane already holds, not new scans. Results
share the tree's context menu, so anything you can do to a row in the tree you
can do to a search hit — a clone stops feeling like one product the moment the
same row offers different actions in two places.
"""

from modules.treesize.help_widgets import (
    describe_control, describe_headers, explain_widget,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QProgressBar, QPushButton, QSpinBox, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout,
)

from ..store import duplicates, search
from ..store.search import Query
from .formatting import format_bytes, format_count

NodeRole = int(Qt.ItemDataRole.UserRole) + 1
PathRole = int(Qt.ItemDataRole.UserRole) + 2

SIZE_UNITS = (("bytes", 1), ("KB", 1024), ("MB", 1024 ** 2), ("GB", 1024 ** 3))


class SearchDialog(QDialog):
    """Spec 8.1: name, regex, size range, age, owner, attributes."""

    node_activated = pyqtSignal(int)

    def __init__(self, shell, parent=None) -> None:
        super().__init__(parent or shell)
        self._shell = shell
        self.setWindowTitle("TreeSize File Search")
        self.resize(820, 560)
        layout = QVBoxLayout(self)

        criteria = QGroupBox("Search for", self)
        form = QFormLayout(criteria)
        self.pattern = QLineEdit(criteria)
        describe_control(self.pattern, 'pattern')
        self.pattern.setPlaceholderText("Part of a name, or a wildcard like *.iso")
        self.pattern.returnPressed.connect(self.run)
        form.addRow("Name:", self.pattern)

        self.regex = QCheckBox("Treat as a regular expression", criteria)
        describe_control(self.regex, 'regex')
        form.addRow(self.regex)

        size_row = QHBoxLayout()
        self.min_size = QSpinBox(criteria)
        describe_control(self.min_size, 'min_size')
        self.min_size.setRange(0, 1_000_000)
        self.max_size = QSpinBox(criteria)
        describe_control(self.max_size, 'max_size')
        self.max_size.setRange(0, 1_000_000)
        self.size_unit = QComboBox(criteria)
        describe_control(self.size_unit, 'size_unit')
        for label, _factor in SIZE_UNITS:
            self.size_unit.addItem(label)
        self.size_unit.setCurrentIndex(2)          # MB
        size_row.addWidget(QLabel("at least"))
        size_row.addWidget(self.min_size)
        size_row.addWidget(QLabel("at most (0 = no limit)"))
        size_row.addWidget(self.max_size)
        size_row.addWidget(self.size_unit)
        form.addRow("Size:", size_row)

        age_row = QHBoxLayout()
        self.newer_than = QSpinBox(criteria)
        describe_control(self.newer_than, 'newer_than')
        self.newer_than.setRange(0, 100_000)
        self.older_than = QSpinBox(criteria)
        describe_control(self.older_than, 'older_than')
        self.older_than.setRange(0, 100_000)
        age_row.addWidget(QLabel("modified within"))
        age_row.addWidget(self.newer_than)
        age_row.addWidget(QLabel("days, or older than"))
        age_row.addWidget(self.older_than)
        age_row.addWidget(QLabel("days (0 = ignore)"))
        form.addRow("Age:", age_row)

        self.owner = QLineEdit(criteria)
        describe_control(self.owner, 'owner')
        form.addRow("Owner:", self.owner)

        flags = QHBoxLayout()
        self.include_folders = QCheckBox("Folders", criteria)
        describe_control(self.include_folders, 'include_folders')
        self.include_files = QCheckBox("Files", criteria)
        describe_control(self.include_files, 'include_files')
        self.include_files.setChecked(True)
        self.include_hidden = QCheckBox("Hidden", criteria)
        describe_control(self.include_hidden, 'include_hidden')
        self.include_hidden.setChecked(True)
        for box in (self.include_files, self.include_folders, self.include_hidden):
            flags.addWidget(box)
        flags.addStretch(1)
        form.addRow("Include:", flags)
        layout.addWidget(criteria)

        buttons = QHBoxLayout()
        self.run_button = QPushButton("Search", self)
        describe_control(self.run_button, 'run_button')
        self.run_button.clicked.connect(self.run)
        buttons.addWidget(self.run_button)
        self.status = QLabel("", self)
        buttons.addWidget(self.status, 1)
        layout.addLayout(buttons)

        self.results = QTreeWidget(self)
        self.results.setColumnCount(3)
        self.results.setHeaderLabels(["Name", "Size", "Path"])
        describe_headers(self.results)
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        self.results.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.header().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self._context_menu)
        self.results.itemDoubleClicked.connect(self._activated)
        layout.addWidget(self.results, 1)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        explain_widget(self)

    def _factor(self) -> int:
        return SIZE_UNITS[self.size_unit.currentIndex()][1]

    def build_query(self) -> Query:
        factor = self._factor()
        return Query(
            pattern=self.pattern.text().strip(),
            regex=self.regex.isChecked(),
            min_size=self.min_size.value() * factor,
            max_size=(self.max_size.value() * factor) or None,
            newer_than_days=self.newer_than.value() or None,
            older_than_days=self.older_than.value() or None,
            owner=self.owner.text().strip(),
            include_files=self.include_files.isChecked(),
            include_folders=self.include_folders.isChecked(),
            include_hidden=self.include_hidden.isChecked(),
        )

    def run(self) -> None:
        store = self._shell._store
        if store is None:
            self.status.setText("Scan something first.")
            return
        hits = search.search(store, self._shell._root, self.build_query())
        self.results.clear()
        total = 0
        for hit in hits:
            total += hit.size
            item = QTreeWidgetItem([hit.name, format_bytes(hit.size),
                                    store.path(hit.node)])
            item.setData(0, NodeRole, hit.node)
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight
                                  | Qt.AlignmentFlag.AlignVCenter)
            self.results.addTopLevelItem(item)
        self.status.setText(
            f"{format_count(len(hits))} match(es), {format_bytes(total)}"
            if hits else "No matches.")

    def _context_menu(self, pos) -> None:
        item = self.results.itemAt(pos)
        if item is None:
            return
        node = item.data(0, NodeRole)
        if node is None:
            return
        menu = self._shell.row_actions.menu_for(int(node), self.results)
        if menu is not None:
            explain_widget(menu)
            menu.exec(self.results.viewport().mapToGlobal(pos))

    def _activated(self, item, _column) -> None:
        node = item.data(0, NodeRole)
        if node is not None:
            self.node_activated.emit(int(node))


class DuplicatesDialog(QDialog):
    """Spec 8.2: group by size, then head hash, then full hash."""

    def __init__(self, shell, parent=None) -> None:
        super().__init__(parent or shell)
        self._shell = shell
        self._cancelled = False
        self.setWindowTitle("Find duplicate files")
        self.resize(820, 560)
        layout = QVBoxLayout(self)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Ignore files smaller than"))
        self.min_size = QSpinBox(self)
        describe_control(self.min_size, 'min_size')
        self.min_size.setRange(0, 1_000_000)
        self.min_size.setValue(1)
        controls.addWidget(self.min_size)
        controls.addWidget(QLabel("MB"))
        self.run_button = QPushButton("Find duplicates", self)
        describe_control(self.run_button, 'run_button')
        self.run_button.clicked.connect(self.run)
        controls.addWidget(self.run_button)
        self.cancel_button = QPushButton("Stop", self)
        describe_control(self.cancel_button, 'cancel_button')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        controls.addWidget(self.cancel_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        self.status = QLabel("", self)
        layout.addWidget(self.status)

        self.results = QTreeWidget(self)
        self.results.setColumnCount(3)
        self.results.setHeaderLabels(["File", "Size", "Recoverable"])
        describe_headers(self.results)
        self.results.setUniformRowHeights(True)
        self.results.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.results, 1)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        explain_widget(self)

    def _cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        store = self._shell._store
        if store is None:
            self.status.setText("Scan something first.")
            return
        self._cancelled = False
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.results.clear()
        self.status.setText("Comparing…")
        try:
            groups = duplicates.find_duplicates(
                store, self._shell._root,
                min_size=self.min_size.value() * 1024 ** 2,
                on_progress=self._progress,
                should_cancel=lambda: self._cancelled)
        finally:
            self.run_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
            self.progress.setValue(100)

        recoverable = 0
        for group in groups:
            recoverable += group.wasted
            parent = QTreeWidgetItem([
                f"{group.count} copies", format_bytes(group.size),
                format_bytes(group.wasted)])
            for path in group.paths:
                child = QTreeWidgetItem([path, "", ""])
                child.setData(0, PathRole, path)
                parent.addChild(child)
            self.results.addTopLevelItem(parent)
        self.status.setText(
            f"{format_count(len(groups))} group(s), "
            f"{format_bytes(recoverable)} recoverable"
            + (" — stopped early" if self._cancelled else ""))

    def _progress(self, percent: int) -> None:
        self.progress.setValue(percent)
        # The hashing runs on this thread, so the UI has to be given a chance
        # to repaint and to notice the Stop button.
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

    def _context_menu(self, pos) -> None:
        item = self.results.itemAt(pos)
        if item is None:
            return
        path = item.data(0, PathRole)
        if not path:
            return
        store = self._shell._store
        from ..scan.watcher import find_node
        node = find_node(store, self._shell._root, path)
        if node < 0:
            return
        menu = self._shell.row_actions.menu_for(node, self.results)
        if menu is not None:
            explain_widget(menu)
            menu.exec(self.results.viewport().mapToGlobal(pos))

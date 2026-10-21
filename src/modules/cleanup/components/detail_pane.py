"""Facts-only detail view. Path actions are requests for the owning tab."""

from html import escape

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget

from core.widget_life import widget_is_valid as _alive
from modules.cleanup.components.item_rows import RowFacts, knowledge_api


class _PathLabel(QLabel):
    """The complete path is available on hover and through Copy path."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self._path = path
        self.setToolTip(path)
        self.setAccessibleName(path)
        self.setMinimumWidth(32)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self._elide()

    def minimumSizeHint(self):
        return QSize(32, self.fontMetrics().height())

    def sizeHint(self):
        return QSize(80, self.fontMetrics().height())

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(
            self._path, Qt.TextElideMode.ElideMiddle, int(max(0, self.contentsRect().width()))))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class DetailPane(QWidget):
    open_folder = pyqtSignal(str)
    copy_path = pyqtSignal(str)
    exclude_path = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("detailPane")
        self.setAccessibleName("Cleanup item details")
        layout = QVBoxLayout(self)
        self.badge = QLabel(self)
        self.badge.setTextFormat(Qt.TextFormat.PlainText)
        self.badge.setAccessibleName("Item safety")
        layout.addWidget(self.badge)
        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(True)
        self.browser.setAccessibleName("Explanation and research sources")
        layout.addWidget(self.browser)
        self._paths = QVBoxLayout()
        layout.addLayout(self._paths)
        self.path_buttons = []
        self.clear()

    def clear(self):
        if not _alive(self):
            return
        self.badge.clear()
        self.browser.setPlainText("Select a row to see its explanation, paths and sources.")
        self._clear_paths()

    def _clear_paths(self):
        self.path_buttons = []
        while self._paths.count():
            item = self._paths.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def set_row(self, row: RowFacts):
        if not _alive(self):
            return
        self._clear_paths()
        self.badge.setText(row.badge_text)
        api = knowledge_api()
        action = (
            (
                "Contents deleted; root folder kept"
                if row.keep_root
                else "Folder and contents deleted"
            )
            if row.is_dir
            else "File deleted"
        )
        facts = [action]
        if row.keep_newer_than_hours:
            facts.append(f"Files newer than {row.keep_newer_than_hours} h kept")
        facts.extend(("Links unlinked, never followed", "Cloud-only files skipped"))
        notice = ""
        if row.research_more_cautious:
            notice += "<p>Research rates this more cautiously than the catalog.</p>"
        if not row.selected:
            reason = (
                row.unselected_reason or "The scanner or selection policy left this item unticked."
            )
            notice += f"<h3>Why is this unticked?</h3><p>{escape(reason)}</p>"
        if api:
            markup = api.detail_html(row.entry, row.item_facts()) + notice
        else:
            markup = (
                f"<h2>{escape(row.label)}</h2><p>{escape(row.badge_text)} · "
                f"{escape(row.size_text)} · {row.share_percent:.1f}%</p>{notice}"
                f"<p>{escape(row.description)}</p><p>Not yet researched</p>"
                "<h3>What this tool will do</h3><ul>"
                + "".join(f"<li>{escape(fact)}</li>" for fact in facts)
                + "</ul>"
            )
        self.browser.setHtml(markup)
        paths = (
            tuple(dict.fromkeys(child.path for child in row.children if child.path))
            if row.children
            else ((row.path,) if row.path else ())
        )
        for path in paths:
            container = QWidget(self)
            line = QHBoxLayout(container)
            line.setContentsMargins(0, 0, 0, 0)
            label = _PathLabel(path, container)
            line.addWidget(label, 1)
            for caption, signal in (
                ("Open folder", self.open_folder),
                ("Copy path", self.copy_path),
                ("Exclude…", self.exclude_path),
            ):
                button = QPushButton(caption, container)
                button.setAccessibleName(f"{caption}: {path}")
                button.setToolTip(f"{caption}: {path}")
                button.clicked.connect(
                    lambda checked=False, p=path, s=signal: s.emit(p) if _alive(self) else None
                )
                line.addWidget(button)
                self.path_buttons.append(button)
            self._paths.addWidget(container)

"""Middle-elided paths shared by detail panes; hover always shows the full path."""
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import QLabel, QSizePolicy


class ElidedPathLabel(QLabel):
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

    def setPath(self, path):
        self._path = path
        self.setToolTip(path)
        self.setAccessibleName(path)
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


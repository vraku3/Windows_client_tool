"""Visual Windows icon picker; native handles stay on workers, pixmaps on the UI."""
import ctypes
import logging
import os

from PyQt6 import sip
from PyQt6.QtCore import QSize, Qt, QThreadPool
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
                             QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QPushButton, QSpinBox, QVBoxLayout)
from core.worker import Worker, retire, track
from .widgets import extract_icon, icon_from_image

logger = logging.getLogger(__name__)


def icon_count(path):
    function = ctypes.WinDLL("shell32", use_last_error=True).ExtractIconExW
    function.argtypes = [ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    function.restype = ctypes.c_uint
    count = function(os.path.expandvars(path), -1, None, None, 0)
    if count == 0xFFFFFFFF:
        raise OSError("Could not count icons in this file.")
    return count


class IconPicker(QDialog):
    def __init__(self, parent=None, pool=None, extractor=extract_icon, counter=icon_count):
        super().__init__(parent)
        self.pool = pool if pool is not None else QThreadPool.globalInstance()
        self.extractor, self.counter = extractor, counter
        self._workers, self._generation = [], 0
        self.location = ""
        self.setWindowTitle("Choose navigation icon")
        self.resize(700, 540)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.source = QComboBox()
        self.source.addItems([r"%SystemRoot%\system32\imageres.dll", r"%SystemRoot%\system32\shell32.dll"])
        self.source.setToolTip("Browse real icons in a Windows library or a file you choose.")
        row.addWidget(self.source, 1)
        browse = QPushButton("Browse...")
        browse.setToolTip("Load icon thumbnails from an .ico, .dll or .exe file.")
        browse.clicked.connect(self.browse)
        row.addWidget(browse)
        layout.addLayout(row)
        self.icons = QListWidget()
        self.icons.setViewMode(QListWidget.ViewMode.IconMode)
        self.icons.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.icons.setMovement(QListWidget.Movement.Static)
        self.icons.setIconSize(QSize(40, 40))
        self.icons.setGridSize(QSize(100, 80))
        self.icons.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.icons.setToolTip("Select an extracted icon. Its label gives its zero-based index or explicit resource ID.")
        self.icons.currentItemChanged.connect(self.selected)
        layout.addWidget(self.icons, 1)
        row = QHBoxLayout()
        self.resource = QSpinBox()
        self.resource.setRange(-2147483647, 2147483647)
        self.resource.setValue(-3)
        self.resource.setToolTip("A negative number is a resource ID, for example -3 loads resource 3; zero or above is an index.")
        row.addWidget(self.resource)
        load = QPushButton("Preview index / resource ID")
        load.setToolTip("Extract this exact index or negative resource ID before allowing selection.")
        load.clicked.connect(self.load_resource)
        row.addWidget(load)
        layout.addLayout(row)
        self.preview = QLabel("Select an icon")
        self.preview.setToolTip("Preview of the successfully extracted selection.")
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.preview)
        self.status = QLabel("")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setToolTip("Use the selected icon only after extraction succeeds.")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setToolTip("Close without changing the shortcut icon.")
        self.ok.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.source.currentIndexChanged.connect(self.load_source)
        self.load_source()

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose icon source", "", "Icons (*.ico *.dll *.exe)")
        if path:
            self.source.addItem(path)
            self.source.setCurrentIndex(self.source.count() - 1)

    def load_source(self):
        self.icons.clear()
        self._load(None)

    def load_resource(self):
        self._load(self.resource.value())

    def _load(self, index):
        self._generation += 1
        generation, path = self._generation, self.source.currentText()
        self.location = ""
        self.ok.setEnabled(False)
        self.preview.clear()
        self.status.setText("Extracting icons...")
        retire(self._workers)

        def extract(worker):
            indices = range(self.counter(path)) if index is None else [index]
            rows, failed = [], 0
            for number in indices:
                if worker.is_cancelled:
                    return None
                location = f"{path},{number}"
                try:
                    image = self.extractor(location)
                    if image.isNull():
                        raise OSError("Extracted icon is empty")
                    rows.append((location, number, image))
                except (OSError, ValueError, RuntimeError) as error:
                    logger.warning("Icon extraction failed for %s: %s", location, error)
                    failed += 1
            return rows, failed

        worker = Worker(extract)
        track(worker)
        self._workers.append(worker)
        worker.signals.result.connect(lambda result: self._loaded(generation, result, index))
        worker.signals.error.connect(lambda error: self._failed(generation, error))
        worker.signals.finished.connect(lambda: self._workers.remove(worker) if worker in self._workers else None)
        self.pool.start(worker)

    def _failed(self, generation, error):
        if not sip.isdeleted(self) and generation == self._generation:
            self.status.setText(f"Could not extract icons: {error}")

    def _loaded(self, generation, result, requested):
        if sip.isdeleted(self) or generation != self._generation or result is None:
            return
        rows, failed = result
        for location, number, image in rows:
            label = f"Resource ID {abs(number)}" if number < 0 else f"Index {number}"
            item = QListWidgetItem(icon_from_image(image), label)
            item.setToolTip(location)
            item.setData(Qt.ItemDataRole.UserRole, (location, image))
            self.icons.addItem(item)
            if requested is not None:
                self.icons.setCurrentItem(item)
        self.status.setText(f"{len(rows)} icons extracted; {failed} failed. Select a thumbnail to use it.")

    def selected(self, item, previous=None):
        self.location = ""
        self.ok.setEnabled(False)
        if item is not None:
            location, image = item.data(Qt.ItemDataRole.UserRole)
            if not image.isNull():
                self.location = location
                self.preview.setPixmap(QPixmap.fromImage(image).scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio))
                self.ok.setEnabled(True)

    def accept(self):
        if self.location and self.ok.isEnabled():
            super().accept()

    def done(self, result):
        self._generation += 1
        retire(self._workers)
        super().done(result)

# src/modules/process_explorer/lower_pane/strings_view.py
from __future__ import annotations
import logging
from core.widget_life import widget_is_valid
import re
from typing import List

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTabWidget,
                              QListWidget, QLineEdit, QHBoxLayout,
                              QPushButton, QFileDialog)

from core.worker import Worker
from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)

_ASCII_RE  = re.compile(rb"[ -~]{4,}")
_UNICODE_RE = re.compile(rb"(?:[ -~]\x00){4,}")


def extract_strings(path: str, min_len: int = 4, encoding: str = "ascii") -> List[str]:
    """Extract printable strings from a binary file."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception as e:
        logger.warning("extract_strings failed for %s: %s", path, e)
        return []
    if encoding == "ascii":
        matches = _ASCII_RE.findall(data)
        return [m.decode("ascii", errors="replace") for m in matches if len(m) >= min_len]
    else:  # unicode
        matches = _UNICODE_RE.findall(data)
        return [m.decode("utf-16-le", errors="replace").replace("\x00", "")
                for m in matches if len(m) // 2 >= min_len]


class StringsView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        # Toolbar
        bar = QHBoxLayout()
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter strings…")
        self._filter.textChanged.connect(self._apply_filter)
        bar.addWidget(self._filter)
        self._save_btn = QPushButton("Save…")
        self._save_btn.clicked.connect(self._save)
        bar.addWidget(self._save_btn)
        layout.addLayout(bar)

        # Tabs: ASCII | Unicode
        self._tabs = QTabWidget()
        self._ascii_list = QListWidget()
        self._unicode_list = QListWidget()
        self._tabs.addTab(self._ascii_list, "ASCII")
        self._tabs.addTab(self._unicode_list, "Unicode")
        layout.addWidget(self._tabs)

        self._all_ascii: List[str] = []
        self._all_unicode: List[str] = []
        self._exe_path: str = ""
        self._thread_pool = None
        self._workers: List[Worker] = []

    def set_thread_pool(self, pool):
        self._thread_pool = pool

    def cancel(self) -> None:
        """Cancel any in-progress string extraction."""
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    def load_exe(self, path: str):
        self.cancel()
        self._exe_path = path
        self._ascii_list.clear()
        self._unicode_list.clear()
        if not path or self._thread_pool is None:
            return

        def do_work(worker):
            ascii_strs = extract_strings(path, encoding="ascii")
            unicode_strs = extract_strings(path, encoding="unicode")
            return ascii_strs, unicode_strs

        w = Worker(do_work)
        w.signals.result.connect(lambda result, w=w: self._on_strings_ready(w, result))
        w.signals.finished.connect(lambda _wn=w: self._workers.remove(_wn) if _wn in self._workers else None)
        self._workers.append(w)
        self._thread_pool.start(w)

    def _on_strings_ready(self, worker, result):
        if worker.is_cancelled:
            return
        if not widget_is_valid(self._ascii_list):
            return
        self._all_ascii, self._all_unicode = result
        self._apply_filter(self._filter.text())

    def _apply_filter(self, text: str):
        f = text.lower()
        self._ascii_list.clear()
        self._unicode_list.clear()
        for s in self._all_ascii:
            if not f or f in s.lower():
                self._ascii_list.addItem(s)
        for s in self._all_unicode:
            if not f or f in s.lower():
                self._unicode_list.addItem(s)

    def _save(self):
        parent = self.window() or self
        path, _ = QFileDialog.getSaveFileName(
            parent, "Save Strings", "", "Text Files (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("=== ASCII ===\n")
                f.write("\n".join(self._all_ascii))
                f.write("\n\n=== Unicode ===\n")
                f.write("\n".join(self._all_unicode))
        except OSError as e:
            self._error_banner.set_error(f"Could not save strings: {e}")

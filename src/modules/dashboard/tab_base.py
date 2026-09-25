"""Shared plumbing for the Dashboard's newer tabs.

`DashTab` is the widget half: it owns its workers, exposes the `start/stop/
cancel_all` trio the module calls, and gives `run()` so a tab reads something
slow on a worker and hears back on the UI thread -- with an error path, never a
silent failure. `DashModule` is the `BaseModule` half: a subclass only names
itself and its tab class.
"""
import logging
from typing import Callable, Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import COMWorker, Worker

logger = logging.getLogger(__name__)


class DashTab(QWidget):
    """A tab that reads on a worker and repaints on the UI thread."""

    #: Milliseconds between automatic refreshes while visible; None = manual.
    REFRESH_MS: Optional[int] = None

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._workers: list = []
        self._app = None
        self._busy = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    # subclasses implement refresh(); it usually calls self.run(...)
    def refresh(self) -> None:
        raise NotImplementedError

    def set_app(self, app) -> None:
        self._app = app

    def start(self) -> None:
        self.refresh()
        if self.REFRESH_MS:
            self._timer.start(self.REFRESH_MS)

    def stop(self) -> None:
        self._timer.stop()
        self.cancel_all()

    def cancel_all(self) -> None:
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()
        self._busy = False

    def _pool(self):
        return getattr(self._app, "thread_pool", None) if self._app else None

    def run(self, fn: Callable, on_result: Callable, on_error: Callable = None,
            com: bool = False) -> None:
        """Run `fn(worker)` on the pool. With no pool (tests) it runs inline."""
        pool = self._pool()
        if pool is None:
            try:
                on_result(fn(None))
            except Exception as e:      # inline path has nobody else to report it
                logger.warning("inline read failed: %s", e)
                (on_error or self._default_error)(str(e))
            return
        worker = (COMWorker if com else Worker)(fn)
        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error or self._default_error)
        self._workers.append(worker)
        pool.start(worker)

    def _default_error(self, message) -> None:
        self._busy = False
        logger.error("%s read failed: %s", type(self).__name__, message)


class DashModule(BaseModule):
    """A Dashboard child that hosts one `DashTab`."""

    tab_class = None
    requires_admin = False
    group = ModuleGroup.OVERVIEW

    def __init__(self) -> None:
        super().__init__()
        self._widget: Optional[DashTab] = None

    def on_start(self, app) -> None:
        self.app = app          # only the app: create_widget has not run yet

    def create_widget(self) -> QWidget:
        self._widget = self.tab_class()
        self._widget.set_app(self.app)
        return self._widget

    def on_activate(self) -> None:
        if self._widget is not None:
            self._widget.start()

    def on_deactivate(self) -> None:
        if self._widget is not None:
            self._widget.stop()

    def on_stop(self) -> None:
        if self._widget is not None:
            self._widget.stop()

    def get_refresh_interval(self) -> Optional[int]:
        return None         # the tab drives its own timer


# ---- small widgets several tabs share ------------------------------------------------

def make_chips(parent, filters, on_pick):
    """A row of exclusive filter buttons. Returns (layout, {key: (button, label)}).

    `filters` is the (key, label, predicate) tuples the engine modules expose.
    """
    from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton
    row = QHBoxLayout()
    row.setContentsMargins(4, 0, 4, 0)
    group = QButtonGroup(parent)
    group.setExclusive(True)
    chips = {}
    for key, label, _fn in filters:
        chip = QPushButton(label, parent)
        chip.setCheckable(True)
        chip.setChecked(key == "all")
        chip.clicked.connect(lambda _=False, k=key: on_pick(k))
        group.addButton(chip)
        chips[key] = (chip, label)
        row.addWidget(chip)
    row.addStretch(1)
    return row, chips


def set_chip_counts(chips, counts) -> None:
    for key, (chip, label) in chips.items():
        chip.setText(label if key == "all" else f"{label} ({counts.get(key, 0)})")


def fmt_size(n) -> str:
    if n is None:
        return ""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


class SortItem:
    """Built lazily so importing this module never needs a QApplication."""
    _cls = None

    @classmethod
    def make(cls, value, text):
        if cls._cls is None:
            from PyQt6.QtWidgets import QTableWidgetItem

            class _Item(QTableWidgetItem):
                def __lt__(self, other):
                    a, b = self.data(0x0100 + 5), other.data(0x0100 + 5)
                    try:
                        return a < b
                    except TypeError:
                        return str(a) < str(b)
            cls._cls = _Item
        item = cls._cls(text)
        item.setData(0x0100 + 5, value)
        return item


def numeric_item(value, text: str = None):
    """A table cell that shows `text` (default: the value) but SORTS on `value`."""
    return SortItem.make(value, str(value) if text is None else text)

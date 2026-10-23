"""Asynchronous app-facts presentation shared by installed-app lists."""
from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QThreadPool, QTimer, pyqtSlot, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem, QListWidget

from core.app_facts import (AppKey, AppFactsIndex, colour_role, installed_text,
                            last_used_text, tooltip)
from core.semantic_colors import semantic
from core.worker import Worker, retire, track

KEY_ROLE = Qt.ItemDataRole.UserRole + 96
FACTS_ROLE = Qt.ItemDataRole.UserRole + 97
BASE_TEXT_ROLE = Qt.ItemDataRole.UserRole + 98


class DateItem(QTableWidgetItem):
    def __init__(self, text, when=None):
        super().__init__(text)
        self.stamp = when.timestamp() if when else float('-inf')

    def __lt__(self, other):
        return self.stamp < getattr(other, 'stamp', float('-inf'))


def bind_row(table, row, entry, name_column=0, safety=True):
    """Bind before sorting resumes. One queued request handles the whole new list."""
    item = table.item(row, name_column)
    if item is None:
        return
    item.setData(KEY_ROLE, entry if isinstance(entry, AppKey) else AppKey.from_entry(entry))
    _controller(table, name_column, safety).schedule()


def bind_list(widget, item, key):
    item.setData(KEY_ROLE, key)
    item.setData(BASE_TEXT_ROLE, item.text())
    item.setData(FACTS_ROLE, None)
    _controller(widget, 0, True).schedule()


def bind_tree(widget, item, key):
    item.setData(0, KEY_ROLE, key)
    _controller(widget, 0, True).schedule()


def _controller(widget, name_column, safety):
    if not hasattr(widget, '_app_facts_controller'):
        widget._app_facts_controller = FactsController(widget, name_column, safety)
    return widget._app_facts_controller


class FactsController(QObject):
    updated = pyqtSignal()

    def __init__(self, widget, name_column, safety):
        super().__init__(widget)
        self.widget, self.name_column, self.safety = widget, name_column, safety
        self._workers, self._items = [], []
        self._pending, self._generation = False, 0
        self.columns = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start)

    def schedule(self):
        self._generation += 1
        if not self._pending:
            self._pending = True
            self._timer.start(0)

    def _rows(self):
        widget = self.widget
        if isinstance(widget, QTableWidget):
            return [widget.item(r, self.name_column) for r in range(widget.rowCount())]
        if isinstance(widget, QListWidget):
            return [widget.item(r) for r in range(widget.count())]
        return [widget.topLevelItem(r) for r in range(widget.topLevelItemCount())]

    def _get(self, item, role):
        return item.data(role) if isinstance(self.widget, (QTableWidget, QListWidget)) else item.data(0, role)

    def _columns(self):
        widget = self.widget
        if not isinstance(widget, QTableWidget):
            return
        labels = [widget.horizontalHeaderItem(c).text() if widget.horizontalHeaderItem(c) else ''
                  for c in range(widget.columnCount())]
        self.columns = []
        for label in ('Installed', 'Last used', 'Uninstall safety')[:3 if self.safety else 2]:
            if label not in labels:
                labels.append(label)
                widget.setColumnCount(len(labels))
                widget.setHorizontalHeaderItem(len(labels) - 1, QTableWidgetItem(label))
            self.columns.append(labels.index(label))

    @pyqtSlot()
    def _start(self):
        self._pending = False
        retire(self._workers)
        self._columns()
        self._items = [item for item in self._rows() if item and self._get(item, KEY_ROLE)]
        keys = [self._get(item, KEY_ROLE) for item in self._items]
        self._fill(None, 'â€¦')
        generation = self._generation

        def read(worker):
            index = AppFactsIndex.build()
            return generation, [index.lookup(key) for key in keys]

        worker = Worker(read)
        self._workers.append(worker)
        track(worker)    # alive until finished even if the list widget goes first
        worker.signals.result.connect(self._done)
        worker.signals.error.connect(self._failed)
        QThreadPool.globalInstance().start(worker)

    @pyqtSlot(object)
    def _done(self, payload):
        generation, facts = payload
        if generation == self._generation:
            self._fill(facts)

    @pyqtSlot(str)
    def _failed(self, error):
        if any(self.sender() is w.signals for w in self._workers):
            self._fill(None, 'could not read: ' + error)

    def _fill(self, facts, status=''):
        widget = self.widget
        if sip.isdeleted(widget):
            return
        sorting = widget.isSortingEnabled() if hasattr(widget, 'isSortingEnabled') else False
        blocked = widget.blockSignals(True)
        if hasattr(widget, 'setSortingEnabled'):
            widget.setSortingEnabled(False)
        try:
            for i, item in enumerate(self._items):
                if not sip.isdeleted(item):
                    self._fill_item(item, facts[i] if facts is not None else None, status)
        finally:
            if hasattr(widget, 'setSortingEnabled'):
                widget.setSortingEnabled(sorting)
            widget.blockSignals(blocked)
        self.updated.emit()

    def _fill_item(self, item, facts, status):
        tip = tooltip(facts) if facts else status
        values = [installed_text(facts), last_used_text(facts), facts.verdict] if facts else [status] * 3
        widget = self.widget
        if isinstance(widget, QTableWidget):
            item.setToolTip(tip)
            item.setData(FACTS_ROLE, facts)
            dates = [facts.installed, facts.last_used, None] if facts else [None] * 3
            for n, col in enumerate(self.columns):
                cell = DateItem(values[n], dates[n]) if n < 2 else QTableWidgetItem(values[n])
                cell.setToolTip(facts.breaks if n == 2 and facts else tip)
                if n == 2 and facts:
                    cell.setForeground(QColor(semantic(colour_role(facts))))
                widget.setItem(item.row(), col, cell)
        elif isinstance(widget, QListWidget):
            item.setToolTip(tip)
            item.setData(FACTS_ROLE, facts)
            base = item.data(BASE_TEXT_ROLE)
            item.setText(f'{base}  | Installed: {values[0]} | Last used: {values[1]} | Uninstall safety: {values[2]}')
        else:
            item.setToolTip(0, tip)
            item.setData(0, FACTS_ROLE, facts)


def selected_facts(table, column=0):
    row = table.currentRow()
    item = table.item(row, column) if row >= 0 else None
    facts = item.data(FACTS_ROLE) if item else None
    return tooltip(facts) if facts else 'App facts: â€¦'


class ModelFactsController(QObject):
    """App Buster's model shares facts with both its cards and Details view."""
    updated = pyqtSignal()

    def __init__(self, model):
        super().__init__(model)
        self.model = model
        self._workers = []
        self._pending = set()

    def request(self, rows):
        rows = [r for r in rows if '_facts' not in r.extra and r.key not in self._pending]
        if not rows:
            return
        if not self._pending:
            retire(self._workers)
        self._pending.update(r.key for r in rows)

        def read(worker):
            try:
                index = AppFactsIndex.build()
                return [(r, index.lookup(AppKey.from_entry(r)), '') for r in rows]
            except Exception as error:
                import logging
                logging.getLogger(__name__).warning('App facts could not read: %s', error)
                return [(r, None, 'could not read: ' + str(error)) for r in rows]

        worker = Worker(read)
        self._workers.append(worker)
        track(worker)    # alive until finished even if the model goes first
        worker.signals.result.connect(self._done)
        QThreadPool.globalInstance().start(worker)

    @pyqtSlot(object)
    def _done(self, payload):
        for record, facts, error in payload:
            record.extra['_facts'] = facts
            record.extra['_facts_error'] = error
            self._pending.discard(record.key)
        self.model.refresh_values()
        self.updated.emit()
        self.request(self.model.rows)

"""One Quick Fix card; registry, DPAPI, disk, and shell I/O stay on Workers."""
import logging

from PyQt6 import sip
from PyQt6.QtCore import QThreadPool, pyqtSlot
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox

from core.long_op_pool import get_long_op_pool
from core.worker import Worker, retire
from modules.quick_fix import browser_links as engine
from modules.quick_fix.fix_actions import FixAction

logger = logging.getLogger(__name__)


class BrowserLinksCard(QFrame):
    ACTION = FixAction('browser_links', 'Open web links in your browser',
                       'Chrome, Brave, Firefox: http, https and www web links. '
                       'Use the Windows default in Outlook/Teams, with Undo.', 'Web links')

    def __init__(self, parent=None, module_workers=None):
        super().__init__(parent)
        self._action = self.ACTION
        self._last_entry = None
        self._recurring_count = 0
        self._workers = []
        self._module_workers = module_workers
        self._active = None
        self._generation = 0
        self._state = None
        self._phase = ''
        self.setFrameShape(QFrame.Shape.StyledPanel)
        root = QVBoxLayout(self)
        title = QLabel(self.ACTION.title)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        root.addWidget(title)
        description = QLabel(self.ACTION.description)
        description.setWordWrap(True)
        root.addWidget(description)
        buttons = QHBoxLayout()
        self._buttons = {}
        for name in ('Chrome', 'Brave', 'Firefox'):
            button = QPushButton(name)
            button.setEnabled(False)
            button.setToolTip('Checking installation...')
            button.clicked.connect(lambda _checked=False, name=name: self._prepare(name))
            buttons.addWidget(button)
            self._buttons[name] = button
        root.addLayout(buttons)
        self._undo = QPushButton('Undo Outlook/Teams change')
        self._undo.setToolTip('Review restoring Outlook/Teams browser-link settings saved by this app, then confirm before applying. This does not undo Windows default-browser associations.')
        self._undo.setEnabled(False)
        self._undo.clicked.connect(self._prepare_undo)
        root.addWidget(self._undo)
        self._status = QLabel('Open Quick Fix to check browser and Outlook/Teams settings.')
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        self._details = QLabel('')
        self._details.setWordWrap(True)
        root.addWidget(self._details)

    def activate(self):
        if self._active is None:
            self._start('refresh', lambda worker: engine.read_state())

    def _start(self, phase, function):
        if self._active is not None:
            return
        generation = self._generation

        def run(worker):
            try:
                result = function(worker)
                return worker, generation, phase, result, ''
            except Exception as error:
                logger.exception('Browser links %s failed', phase)
                return worker, generation, phase, None, str(error)

        worker = Worker(run)
        self._active = worker
        self._phase = phase
        self._workers.append(worker)
        if self._module_workers is not None:
            self._module_workers.append(worker)
        worker.signals.result.connect(self._on_result)
        self._set_busy(True)
        self._status.setText('Waiting for Windows: click "Set default" in Settings (up to 120 seconds).'
                             if phase == 'poll' else 'Checking settings...' if phase in ('refresh', 'prepare', 'prepare_undo')
                             else 'Applying and verifying...')
        pool = get_long_op_pool() if phase == 'poll' else QThreadPool.globalInstance()
        pool.start(worker)

    def _set_busy(self, busy):
        for name, button in self._buttons.items():
            button.setEnabled(not busy and self._state is not None and name in self._state.browsers)
        self._undo.setEnabled(not busy and self._state is not None and self._state.undo_available)

    def _render(self, state):
        self._state = state
        for name, button in self._buttons.items():
            button.setToolTip('Review changing Windows web-link associations to ' + name + ', including using the Windows default browser in Outlook/Teams. Asks before applying; Undo restores only the saved Outlook/Teams change.' if name in state.browsers else
                              'Could not determine installation: ' + '; '.join(state.detection_errors)
                              if state.detection_errors else 'not installed')
        details = state.summary() + '\n' + state.processes
        if state.detection_errors:
            details += '\n' + '; '.join(state.detection_errors)
        self._details.setText(details)
        self._set_busy(self._active is not None)

    def _prepare(self, name):
        def prepare(worker):
            state = engine.read_state()
            if name not in state.browsers:
                raise RuntimeError(name + ': not installed or installation could not be read')
            return state, name, engine.confirmation(state, state.browsers[name])
        self._start('prepare', prepare)

    def _prepare_undo(self):
        def prepare(worker):
            values = engine._load_journal()
            state = engine.read_state()
            changes = '\n'.join(name + ': ' +
                                (f'restore saved binary ({len(value.data)} bytes)' if isinstance(value.data, bytes)
                                 else value.text()) +
                                (' (delete value)' if not value.present else f' (type {value.kind})')
                                for name, value in values.items())
            return state, '', ('Restore these exact original Office values:\n' + changes +
                               '\nRead back to verify, then remove the Undo backup. '
                               'Restart Outlook/Teams afterwards; neither app will be closed.\n' +
                               state.summary() + '\n' + state.processes)
        self._start('prepare_undo', prepare)

    def _confirm(self, text):
        dialog = QMessageBox(self)
        dialog.setWindowTitle(self.ACTION.title)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText(text)
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return dialog.exec() == QMessageBox.StandardButton.Ok

    def _apply(self, state, name):
        browser = state.browsers[name]

        def apply(worker):
            poll = False
            try:
                if worker.is_cancelled:
                    return engine.read_state(), 'Cancelled before changes', None
                message = engine.set_office_default(expected=state.office)
                if not browser.is_default(engine.read_defaults()) and not worker.is_cancelled:
                    message += '. ' + engine.open_default_settings(browser)
                    poll = True
            except Exception as error:
                logger.exception('Browser links apply failed')
                message = 'ERROR: ' + str(error)
            return engine.read_state(), message, browser if poll else None
        self._start('apply', apply)

    def _undo_apply(self):
        def undo(worker):
            try:
                message = engine.undo_office() if not worker.is_cancelled else 'Cancelled before changes'
            except Exception as error:
                logger.exception('Browser links Undo failed')
                message = 'ERROR: ' + str(error)
            return engine.read_state(), message, None
        self._start('undo', undo)

    @pyqtSlot(object)
    def _on_result(self, payload):
        if sip.isdeleted(self):
            return
        worker, generation, phase, result, error = payload
        if generation != self._generation or worker is not self._active:
            return
        self._active = None
        self._phase = ''
        if worker in self._workers:
            # Keep signals alive through their final queued delivery without
            # retaining every completed check for the lifetime of the module.
            self._workers.remove(worker)
            if self._module_workers is not None and worker in self._module_workers:
                self._module_workers.remove(worker)
            retire([worker])
        self._set_busy(False)
        if error:
            self._status.setText('ERROR: ' + error)
            return
        if phase == 'refresh':
            self._render(result)
            self._status.setText('Settings checked. Choose a browser.')
        elif phase in ('prepare', 'prepare_undo'):
            state, name, text = result
            self._render(state)
            self._status.setText('Settings checked.')
            if self._confirm(text):
                if phase == 'prepare':
                    self._apply(state, name)
                else:
                    self._undo_apply()
        elif phase in ('apply', 'undo'):
            state, message, browser = result
            self._render(state)
            self._status.setText(message)
            if browser is not None:
                def poll(worker):
                    message = engine.poll_defaults(browser, worker)
                    return engine.read_state(), message
                self._start('poll', poll)
        elif phase == 'poll':
            state, message = result
            self._render(state)
            self._status.setText(message + '. Restart Outlook/Teams if its preference changed.')

    def cancel(self):
        self._generation += 1
        self._active = None
        retire(self._workers)
        if not sip.isdeleted(self):
            self._set_busy(False)

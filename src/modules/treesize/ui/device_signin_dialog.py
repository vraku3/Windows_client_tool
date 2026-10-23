"""The device-flow sign-in dialog (spec 6.2).

Shows the user code, waits for the browser approval on a worker thread, and
hands back a token plus the app registration it came from.

The polling MUST NOT run on the UI thread. A device flow waits on a human
opening a browser, typing a code and clicking approve -- tens of seconds at
best -- and doing that on the UI thread freezes the whole application behind
a dialog that looks like it has hung, which is exactly when someone kills it.
"""

from modules.treesize.help_widgets import (
    describe_control, explain_widget,
)
import logging

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QPushButton, QVBoxLayout,
)

from ..targets import device_flow

logger = logging.getLogger(__name__)


class _SignInWorker(QThread):
    """Runs the flow off the UI thread. Cancellable, because a user who gives
    up must not leave a thread polling Microsoft until the code expires."""

    code_ready = pyqtSignal(object)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, provider, client_id, client_secret="", flow=None,
                 transport=None, parent=None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._client_id = client_id
        self._client_secret = client_secret
        self._flow = flow or device_flow.sign_in
        self._transport = transport
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            token = self._flow(
                self._provider, self._client_id, self.code_ready.emit,
                client_secret=self._client_secret, transport=self._transport,
                should_cancel=lambda: self._cancelled)
        except device_flow.DeviceFlowError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:                    # noqa: BLE001
            logger.warning("Device sign-in failed", exc_info=True)
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.finished_ok.emit(token)


class DeviceSignInDialog(QDialog):
    """Collects the app registration, then runs the flow and shows the code."""

    def __init__(self, backend_id: str, parent=None, flow=None,
                 transport=None) -> None:
        super().__init__(parent)
        self._backend_id = backend_id
        self._flow = flow
        self._transport = transport
        self._worker: _SignInWorker | None = None
        self.token = None
        self.client_id = ""
        self.tenant = ""

        self.setWindowTitle("Sign in")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.client_id_edit = QLineEdit(self)
        describe_control(self.client_id_edit, 'client_id_edit')
        self.client_id_edit.setPlaceholderText(
            "the Application (client) ID of your app registration")
        form.addRow("Client ID:", self.client_id_edit)

        self.tenant_edit = QLineEdit(self)
        describe_control(self.tenant_edit, 'tenant_edit')
        self.tenant_edit.setPlaceholderText("common, or your tenant id")
        self.secret_edit = QLineEdit(self)
        describe_control(self.secret_edit, 'secret_edit')
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        # The unused one is HIDDEN, not merely left out of the form. A widget
        # parented to the dialog but never added to a layout still renders --
        # at 0,0, on top of the form. Leaving it out is not the same as not
        # showing it.
        if backend_id == "sharepoint":
            form.addRow("Tenant:", self.tenant_edit)
            self.secret_edit.hide()
        else:
            # Google treats an installed-app secret as part of the client
            # identity rather than a credential; it still has to be sent.
            form.addRow("Client secret:", self.secret_edit)
            self.tenant_edit.hide()
        layout.addLayout(form)

        self.status = QLabel(
            "Enter the client ID, then Sign in. A code will appear here to "
            "type into your browser.", self)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)

        self.copy_button = QPushButton("Copy code", self)
        describe_control(self.copy_button, 'copy_button')
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_code)
        layout.addWidget(self.copy_button)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel, parent=self)
        self.sign_in_button = self.buttons.addButton(
            "Sign in", QDialogButtonBox.ButtonRole.AcceptRole)
        self.sign_in_button.clicked.connect(self.start)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._code = None
        explain_widget(self)

    # ---- the flow -------------------------------------------------------

    def provider(self):
        if self._backend_id == "sharepoint":
            return device_flow.microsoft(self.tenant_edit.text().strip())
        return device_flow.google()

    def start(self) -> None:
        client_id = self.client_id_edit.text().strip()
        if not client_id:
            self.status.setText(
                "A client ID is required — the device flow cannot start "
                "without one. Register an app and paste its ID here.")
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self.client_id = client_id
        self.tenant = self.tenant_edit.text().strip()
        self.sign_in_button.setEnabled(False)
        self.status.setText("Contacting the provider…")

        self._worker = _SignInWorker(
            self.provider(), client_id, self.secret_edit.text(),
            flow=self._flow, transport=self._transport, parent=self)
        self._worker.code_ready.connect(self._on_code)
        self._worker.finished_ok.connect(self._on_token)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_code(self, code) -> None:
        self._code = code
        self.copy_button.setEnabled(True)
        self.status.setText(code.instructions + "\n\nWaiting for approval…")

    def _on_token(self, token) -> None:
        self.token = token
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.status.setText(message)
        self.sign_in_button.setEnabled(True)

    def _copy_code(self) -> None:
        if self._code is not None:
            QApplication.clipboard().setText(self._code.user_code)
            self.status.setText(
                f"Copied {self._code.user_code}. "
                f"{self._code.instructions}\n\nWaiting for approval…")

    # ---- lifecycle ------------------------------------------------------

    def reject(self) -> None:
        """Cancelling must stop the poll, not just hide the dialog."""
        self._stop_worker()
        super().reject()

    def _stop_worker(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(3000)
            self._worker = None

    def closeEvent(self, event):
        self._stop_worker()
        super().closeEvent(event)

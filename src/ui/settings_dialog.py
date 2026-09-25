import logging

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """Global and per-module settings dialog."""

    def __init__(self, app_instance, parent: QWidget = None):
        super().__init__(parent)
        self._app = app_instance
        self.setWindowTitle("Settings")
        self.setMinimumSize(500, 400)

        layout = QVBoxLayout(self)

        # General settings
        general_group = QGroupBox("General")
        general_layout = QFormLayout(general_group)

        self._theme_combo = QComboBox()
        from core.phosphor import LABELS
        for name in self._app.theme.THEMES:
            self._theme_combo.addItem(LABELS.get(name, name), name)
        current = self._theme_combo.findData(self._app.config.get("app.theme", "dark"))
        self._theme_combo.setCurrentIndex(max(0, current))
        general_layout.addRow("Theme:", self._theme_combo)

        self._log_level_combo = QComboBox()
        self._log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._log_level_combo.setCurrentText(
            self._app.config.get("app.log_level", "INFO")
        )
        general_layout.addRow("Log Level:", self._log_level_combo)

        self._start_minimized = QCheckBox()
        self._start_minimized.setChecked(
            self._app.config.get("app.start_minimized", False)
        )
        general_layout.addRow("Start Minimized:", self._start_minimized)

        self._admin_check = QCheckBox()
        self._admin_check.setChecked(
            self._app.config.get("app.check_admin_on_start", True)
        )
        general_layout.addRow("Check Admin on Start:", self._admin_check)

        self._tray_check = QCheckBox()
        self._tray_check.setChecked(
            self._app.config.get("app.minimize_to_tray", False)
        )
        general_layout.addRow("Minimize to Tray on Close:", self._tray_check)

        layout.addWidget(general_group)

        # Module manager
        modules_group = QGroupBox("Modules")
        modules_layout = QVBoxLayout(modules_group)
        self._module_list = QListWidget()
        for mod in self._app.module_registry.modules:
            self._module_list.addItem(mod.name)
        modules_layout.addWidget(QLabel("Registered modules:"))
        modules_layout.addWidget(self._module_list)
        layout.addWidget(modules_group)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save_and_close(self):
        self._app.config.set("app.theme", self._theme_combo.currentData())
        self._app.config.set("app.log_level", self._log_level_combo.currentText())
        self._app.config.set("app.start_minimized", self._start_minimized.isChecked())
        self._app.config.set(
            "app.check_admin_on_start", self._admin_check.isChecked()
        )
        self._app.config.set("app.minimize_to_tray", self._tray_check.isChecked())
        self._app.theme.apply_theme(self._theme_combo.currentData())
        self._app.logger.set_level(self._log_level_combo.currentText())
        self._app.config.save()
        self.accept()

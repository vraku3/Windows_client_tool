"""Options dialog (spec 5.2's Tools group).

Settings that actually change behaviour, and nothing that does not. A dialog
full of controls wired to nothing is worse than a small one: it teaches people
the app ignores them.

Persistence goes through the host's ConfigManager when one is available, so
TreeSize's settings live where every other module's settings live rather than
in a private file.
"""

from modules.treesize.help_widgets import (
    describe_control, explain_widget,
)
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QSpinBox, QVBoxLayout,
)

from .formatting import Mode, Unit

CONFIG_PREFIX = "treesize."

DEFAULTS = {
    "unit": Unit.AUTO.value,
    "decimals": 1,
    "mode": Mode.SIZE.value,
    "charge_all_hardlinks": False,
    "exclude_hidden": False,
    # Spec 3.6's PERMANENT exclusions, as against the temporary ones the
    # Exclude menu adds for a single scan.
    "exclude_patterns": [],
    "collect_owners": False,
    "confirm_permanent_delete": True,
    "treemap_depth": 6,
    "top_files_limit": 100,
}


class OptionsDialog(QDialog):
    def __init__(self, settings: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("TreeSize Options")
        self.setMinimumWidth(420)
        self._settings = dict(DEFAULTS)
        self._settings.update(settings or {})

        layout = QVBoxLayout(self)

        display = QGroupBox("Display", self)
        form = QFormLayout(display)
        self.unit = QComboBox(display)
        describe_control(self.unit, 'unit')
        for unit in Unit:
            self.unit.addItem(unit.value, unit.value)
        self.unit.setCurrentIndex(max(0, self.unit.findData(self._settings["unit"])))
        form.addRow("Unit:", self.unit)

        self.decimals = QSpinBox(display)
        describe_control(self.decimals, 'decimals')
        self.decimals.setRange(0, 3)
        self.decimals.setValue(int(self._settings["decimals"]))
        form.addRow("Decimals:", self.decimals)

        self.mode = QComboBox(display)
        describe_control(self.mode, 'mode')
        for mode in Mode:
            self.mode.addItem(mode.value, mode.value)
        self.mode.setCurrentIndex(max(0, self.mode.findData(self._settings["mode"])))
        form.addRow("Default mode:", self.mode)
        layout.addWidget(display)

        scanning = QGroupBox("Scanning", self)
        scan_form = QFormLayout(scanning)
        self.charge_hardlinks = QCheckBox(
            "Charge every hard link its full size", scanning)
        describe_control(self.charge_hardlinks, 'charge_hardlinks')
        self.charge_hardlinks.setChecked(bool(self._settings["charge_all_hardlinks"]))
        self.charge_hardlinks.setToolTip(
            "Off (the default, and Pro's) counts a hard-linked file once, under "
            "the first path seen. On counts it under every path, so totals "
            "exceed the space actually used.")
        scan_form.addRow(self.charge_hardlinks)

        self.collect_owners = QCheckBox(
            "Determine the owner of every file (slower)", scanning)
        describe_control(self.collect_owners, 'collect_owners')
        self.collect_owners.setChecked(bool(self._settings["collect_owners"]))
        self.collect_owners.setToolTip(
            "Fills the Owner column and the Users view. A walk scan pays one "
            "security call per file for this; an MFT scan samples one file per "
            "distinct owner instead and costs almost nothing.")
        scan_form.addRow(self.collect_owners)

        self.exclude_hidden = QCheckBox("Exclude hidden files", scanning)
        describe_control(self.exclude_hidden, 'exclude_hidden')
        self.exclude_hidden.setChecked(bool(self._settings["exclude_hidden"]))
        scan_form.addRow(self.exclude_hidden)

        self.exclude_patterns = QLineEdit(scanning)
        describe_control(self.exclude_patterns, 'exclude_patterns')
        self.exclude_patterns.setText(
            ", ".join(self._settings.get("exclude_patterns") or ()))
        self.exclude_patterns.setPlaceholderText("node_modules, *.iso, *.tmp")
        self.exclude_patterns.setToolTip(
            "Applied to every scan and kept between sessions, unlike the "
            "Exclude menu on the Scan tab, which lasts one scan.")
        scan_form.addRow("Always exclude:", self.exclude_patterns)
        layout.addWidget(scanning)

        views = QGroupBox("Views", self)
        view_form = QFormLayout(views)
        self.treemap_depth = QSpinBox(views)
        describe_control(self.treemap_depth, 'treemap_depth')
        self.treemap_depth.setRange(1, 12)
        self.treemap_depth.setValue(int(self._settings["treemap_depth"]))
        self.treemap_depth.setToolTip(
            "How many levels the treemap draws. Deeper is slower and, past a "
            "point, smaller than a pixel.")
        view_form.addRow("Treemap depth:", self.treemap_depth)

        self.top_files_limit = QSpinBox(views)
        describe_control(self.top_files_limit, 'top_files_limit')
        self.top_files_limit.setRange(10, 1000)
        self.top_files_limit.setSingleStep(10)
        self.top_files_limit.setValue(int(self._settings["top_files_limit"]))
        view_form.addRow("Top Files count:", self.top_files_limit)
        layout.addWidget(views)

        safety = QGroupBox("Safety", self)
        safety_form = QFormLayout(safety)
        self.confirm_permanent = QCheckBox(
            "Confirm before deleting permanently", safety)
        describe_control(self.confirm_permanent, 'confirm_permanent')
        self.confirm_permanent.setChecked(
            bool(self._settings["confirm_permanent_delete"]))
        safety_form.addRow(self.confirm_permanent)
        note = QLabel("Protected system locations always require an explicit "
                      "override, whatever is set here.", safety)
        note.setWordWrap(True)
        note.setObjectName("optionsNote")
        safety_form.addRow(note)
        layout.addWidget(safety)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.RestoreDefaults, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(
            QDialogButtonBox.StandardButton.RestoreDefaults
        ).clicked.connect(self.restore_defaults)
        layout.addWidget(buttons)
        explain_widget(self)

    def restore_defaults(self) -> None:
        self.unit.setCurrentIndex(max(0, self.unit.findData(DEFAULTS["unit"])))
        self.decimals.setValue(DEFAULTS["decimals"])
        self.mode.setCurrentIndex(max(0, self.mode.findData(DEFAULTS["mode"])))
        self.charge_hardlinks.setChecked(DEFAULTS["charge_all_hardlinks"])
        self.exclude_hidden.setChecked(DEFAULTS["exclude_hidden"])
        self.exclude_patterns.setText("")
        self.collect_owners.setChecked(DEFAULTS["collect_owners"])
        self.treemap_depth.setValue(DEFAULTS["treemap_depth"])
        self.top_files_limit.setValue(DEFAULTS["top_files_limit"])
        self.confirm_permanent.setChecked(DEFAULTS["confirm_permanent_delete"])

    def values(self) -> dict:
        return {
            "unit": self.unit.currentData(),
            "decimals": self.decimals.value(),
            "mode": self.mode.currentData(),
            "charge_all_hardlinks": self.charge_hardlinks.isChecked(),
            "exclude_hidden": self.exclude_hidden.isChecked(),
            # A trailing comma is a typo, not a rule matching everything.
            "exclude_patterns": [p.strip() for p
                                 in self.exclude_patterns.text().split(",")
                                 if p.strip()],
            "collect_owners": self.collect_owners.isChecked(),
            "confirm_permanent_delete": self.confirm_permanent.isChecked(),
            "treemap_depth": self.treemap_depth.value(),
            "top_files_limit": self.top_files_limit.value(),
        }


def load_settings(config) -> dict:
    """Read settings from the host ConfigManager, falling back to defaults."""
    values = dict(DEFAULTS)
    if config is None:
        return values
    for key, default in DEFAULTS.items():
        try:
            values[key] = config.get(CONFIG_PREFIX + key, default)
        except Exception:                       # noqa: BLE001
            # A config backend that cannot read one key must not stop the
            # module loading; the default is always usable.
            values[key] = default
    return values


def save_settings(config, values: dict) -> None:
    if config is None:
        return
    for key, value in values.items():
        try:
            config.set(CONFIG_PREFIX + key, value)
        except Exception:                       # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "Could not persist TreeSize setting %s", key, exc_info=True)

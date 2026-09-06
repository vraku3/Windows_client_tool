# src/modules/tweaks/tweaks_module.py
import logging
import os
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSplitter, QTabWidget,
    QTextEdit, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker
from modules.tweaks.tweak_engine import (
    APPLIED, NOT_APPLIED, NOT_APPLICABLE, PARTIAL, UNKNOWN, TweakEngine,
)
from modules.tweaks.os_context import get_os_context
from modules.tweaks.app_catalog import AppCatalog, PROTECTED_APPS_DEFAULT
from modules.tweaks.preset_manager import PresetManager
from modules.tweaks.tweak_categories import CATEGORY_FILES
from core.semantic_colors import semantic

logger = logging.getLogger(__name__)


def _escape(text: str) -> str:
    """Reasons quote registry data verbatim, and REG_SZ values can contain
    `<` — which would silently eat the rest of a rich-text QLabel."""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_DEFS_DIR = os.path.join(os.path.dirname(__file__), "definitions")
_CATEGORY_FILES = CATEGORY_FILES

#: How each verdict reads in the Status column, and which semantic colour it
#: takes. "Not Applicable" is deliberately grey rather than red — it is not a
#: failure, it is a tweak that has nothing to do on this machine.
_STATUS_DISPLAY = {
    APPLIED:        ("✅ Applied",     "success"),
    NOT_APPLIED:    ("— Not Applied", None),
    PARTIAL:        ("◐ Partial",     "warning"),
    NOT_APPLICABLE: ("⊘ N/A",         None),
    UNKNOWN:        ("? Unknown",     "info"),
}

#: Status filter dropdown -> status code. "All" and "Applicable" are handled
#: separately because they match more than one code.
_FILTER_TO_STATUS = {
    "Applied":        APPLIED,
    "Not Applied":    NOT_APPLIED,
    "Partial":        PARTIAL,
    "Not Applicable": NOT_APPLICABLE,
    "Unknown":        UNKNOWN,
}


# ---------------------------------------------------------------------------
# Signals helper (must be QObject for pyqtSignal)
# ---------------------------------------------------------------------------

class _Signals(QObject):
    status_detected = pyqtSignal(str, str, str)  # tweak_id, status, reason
    apply_done      = pyqtSignal(bool, list) # success, errors
    apps_detected   = pyqtSignal(set, set, list)  # winget_ids, appx, desktop


# ---------------------------------------------------------------------------
# DetailsPanel — shows tweak info when a row is clicked
# ---------------------------------------------------------------------------

class _DetailsPanel(QFrame):
    """Side panel showing full details of a selected tweak."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(280)
        self.setMaximumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        self._title = QLabel()
        self._title.setWordWrap(True)
        self._title.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(self._title)

        self._risk = QLabel()
        layout.addWidget(self._risk)

        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._desc = QLabel()
        self._desc.setWordWrap(True)
        self._desc.setStyleSheet("color: #aaaaaa;")
        layout.addWidget(self._desc)

        layout.addWidget(QLabel("Steps:"))
        self._steps = QTextEdit()
        self._steps.setReadOnly(True)
        self._steps.setMaximumHeight(200)
        layout.addWidget(self._steps)

        self._presets = QLabel()
        self._presets.setWordWrap(True)
        self._presets.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(self._presets)

        layout.addStretch()

        self._apply_btn = QPushButton("Apply This Tweak")
        self._apply_btn.setEnabled(False)
        layout.addWidget(self._apply_btn)

        self._tweak = None
        self._status_cache: Dict[str, tuple] = {}
        self._apply_btn.clicked.connect(self._on_apply_clicked)

    def update_status(self, tweak_id: str, status: str, reason: str) -> None:
        """Remember every verdict, and repaint if it is the one on screen.

        Detection runs on a worker and lands row by row, so the panel is
        usually opened before the answer for that row arrives.
        """
        self._status_cache[tweak_id] = (status, reason)
        if self._tweak is not None and self._tweak.get("id") == tweak_id:
            self._render_status(tweak_id)

    def _render_status(self, tweak_id: str) -> None:
        status, reason = self._status_cache.get(tweak_id, (UNKNOWN, ""))
        text, colour = _STATUS_DISPLAY.get(status, (status, None))
        label = text.split(" ", 1)[-1]
        shade = semantic(colour) if colour else "#aaaaaa"
        detail = f"<br><span style='color:#999999'>{_escape(reason)}</span>" if reason else ""
        self._status.setText(
            f"<span style='color:{shade}'><b>STATUS: {label.upper()}</b></span>{detail}")

        applicable = status != NOT_APPLICABLE
        self._apply_btn.setEnabled(applicable)
        self._apply_btn.setText(
            "Apply This Tweak" if applicable else "Not Applicable On This PC")

    def set_tweak(self, tweak: Optional[Dict]) -> None:
        self._tweak = tweak
        if tweak is None:
            self._title.setText("Select a tweak")
            self._risk.setText("")
            self._status.setText("")
            self._desc.setText("Click a tweak to see its details.")
            self._steps.setText("")
            self._presets.setText("")
            self._apply_btn.setEnabled(False)
            return

        self._title.setText(tweak["name"])

        risk = tweak.get("risk", "low")
        risk_color = semantic("error" if risk == "high"
                              else "warning" if risk == "medium" else "success")
        self._risk.setText(f"<span style='color:{risk_color}'>RISK: {risk.upper()}</span>")

        self._desc.setText(tweak.get("description", "No description."))

        steps_text = ""
        for i, step in enumerate(tweak.get("steps", [])):
            step_type = step.get("type", "unknown")
            if step_type == "registry":
                steps_text += f"  [{i+1}] Registry: {step.get('key', '')}\\{step.get('value', '')} = {step.get('data', '')} ({step.get('kind', 'DWORD')})\n"
            elif step_type == "service":
                steps_text += f"  [{i+1}] Service: {step.get('name', '')} → start_type={step.get('start_type', '')}\n"
            elif step_type == "command":
                steps_text += f"  [{i+1}] Command: {step.get('cmd', '')[:80]}...\n"
            elif step_type == "script":
                steps_text += f"  [{i+1}] Script: {step.get('command', step.get('cmd', ''))[:80]}...\n"
            elif step_type == "appx":
                steps_text += f"  [{i+1}] AppX Remove: {step.get('package', '')}\n"
            elif step_type == "registry_delete":
                steps_text += f"  [{i+1}] Registry: delete {step.get('key', '')}\\{step.get('value', '')}\n"
            elif step_type == "scheduled_task":
                steps_text += f"  [{i+1}] Scheduled Task: {step.get('task_name', '')} → Disable\n"
            else:
                steps_text += f"  [{i+1}] {step_type}\n"
        self._steps.setText(steps_text.strip() or "No steps defined.")

        meta = [f"ID: {tweak.get('id', '')}",
                f"Category: {tweak.get('category', '')}"]
        applies = tweak.get("applies_to")
        if applies:
            meta.append("Applies to: " + ", ".join(
                f"{k}={v}" for k, v in applies.items()))
        self._presets.setText("\n".join(meta))

        self._render_status(tweak.get("id", ""))

    def _on_apply_clicked(self) -> None:
        if self._tweak is not None:
            self.apply_requested.emit(self._tweak)

    apply_requested = pyqtSignal(dict)


# ---------------------------------------------------------------------------
# TweakRow — one row in a tweak tab
# ---------------------------------------------------------------------------

class TweakRow(QWidget):
    def __init__(self, tweak: Dict, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.tweak = tweak
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(4)

        self.checkbox = QCheckBox()
        self.checkbox.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect, True)
        layout.addWidget(self.checkbox)

        name_label = QLabel(tweak["name"])
        name_label.setToolTip(tweak.get("description", ""))
        name_label.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect, True)
        layout.addWidget(name_label, stretch=1)

        risk = tweak.get("risk", "low")
        risk_label = QLabel(risk.upper())
        risk_label.setStyleSheet("color: %s;" % semantic(
            "error" if risk == "high"
            else "warning" if risk == "medium" else "success"))
        risk_label.setFixedWidth(70)
        risk_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        risk_label.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect, True)
        layout.addWidget(risk_label)

        self.status = UNKNOWN
        self.status_reason = ""
        self._name_label = name_label

        self.status_label = QLabel("? Unknown")
        self.status_label.setFixedWidth(110)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect, True)
        layout.addWidget(self.status_label)

        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setFixedWidth(70)
        self.apply_btn.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect, True)
        layout.addWidget(self.apply_btn)

        self.disable_btn = QPushButton("Disable")
        self.disable_btn.setFixedWidth(70)
        self.disable_btn.setToolTip("Revert this tweak back to how it was before it was applied")
        self.disable_btn.setAttribute(Qt.WidgetAttribute.WA_LayoutUsesWidgetRect, True)
        self.disable_btn.setVisible(False)  # only shown once status is detected as "applied"
        layout.addWidget(self.disable_btn)

        # Row highlight on hover
        self.setStyleSheet(
            "TweakRow:hover { background-color: #2a2d2e; }"
        )

    def set_status(self, status: str, reason: str = "") -> None:
        """Show the verdict, and put the *why* one hover away.

        The reason is the whole point of the rewrite: "Not Applied" alone is
        indistinguishable from "we couldn't read the key", so every row now
        carries the sentence that produced its label.
        """
        self.status = status
        self.status_reason = reason

        text, colour = _STATUS_DISPLAY.get(status, (status, None))
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"color: {semantic(colour)};" if colour else "")

        label = text.split(" ", 1)[-1]
        self.status_label.setToolTip(
            f"{label}\n{reason}" if reason else label)

        # A tweak with nothing to do here must not sit in a bulk selection and
        # then report a failure the user has to go and interpret.
        applicable = status != NOT_APPLICABLE
        self.checkbox.setEnabled(applicable)
        self.apply_btn.setEnabled(applicable)
        if not applicable:
            self.checkbox.setChecked(False)
            self.apply_btn.setToolTip(f"Not applicable on this PC — {reason}")
            self._name_label.setStyleSheet("color: #7a7a7a;")
        else:
            self.apply_btn.setToolTip("")
            self._name_label.setStyleSheet("")

        self.disable_btn.setVisible(status in (APPLIED, PARTIAL))

    @property
    def is_checked(self) -> bool:
        return self.checkbox.isChecked()

    def set_checked(self, checked: bool) -> None:
        self.checkbox.setChecked(checked)


# ---------------------------------------------------------------------------
# TweakTab — one category tab (Privacy, Performance, etc.)
# ---------------------------------------------------------------------------

class TweakTab(QWidget):
    def __init__(self, tweaks: List[Dict], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._tweaks = tweaks
        self._rows: Dict[str, TweakRow] = {}
        self._filter_text = ""
        self._filter_risk = "all"
        self._filter_status = "all"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Control bar
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 4, 4, 4)
        bar.setSpacing(4)

        self._select_all_btn = QPushButton("Select All")
        # A fixed width crops the label when the label is wider: these were
        # picked by eye and "Select Applied" (111px) had been given 100.
        # A minimum keeps the tidy alignment without ever cutting the text.
        self._select_all_btn.setMinimumWidth(
            max(80, self._select_all_btn.sizeHint().width()))
        self._select_all_btn.clicked.connect(self.select_all)
        bar.addWidget(self._select_all_btn)

        self._deselect_all_btn = QPushButton("Deselect All")
        self._deselect_all_btn.setMinimumWidth(
            max(90, self._deselect_all_btn.sizeHint().width()))
        self._deselect_all_btn.clicked.connect(self.deselect_all)
        bar.addWidget(self._deselect_all_btn)

        self._select_applied_btn = QPushButton("Select Applied")
        self._select_applied_btn.setMinimumWidth(
            max(100, self._select_applied_btn.sizeHint().width()))
        self._select_applied_btn.clicked.connect(
            lambda: self._select_by_status_filtered("applied"))
        bar.addWidget(self._select_applied_btn)

        self._select_not_btn = QPushButton("Select Not Applied")
        self._select_not_btn.setMinimumWidth(
            max(110, self._select_not_btn.sizeHint().width()))
        self._select_not_btn.clicked.connect(
            lambda: self._select_by_status_filtered("not_applied"))
        bar.addWidget(self._select_not_btn)

        bar.addStretch()

        self._count_label = QLabel("0 selected")
        bar.addWidget(self._count_label)

        layout.addLayout(bar)

        # Scroll area with rows
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)

        container = QWidget()
        self._container_layout = QVBoxLayout(container)
        self._container_layout.setSpacing(1)
        self._container_layout.setContentsMargins(0, 0, 0, 0)

        for tweak in tweaks:
            row = TweakRow(tweak)
            self._rows[tweak["id"]] = row
            self._container_layout.addWidget(row)

        self._container_layout.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)

        # Wire selection count update
        for row in self._rows.values():
            row.checkbox.stateChanged.connect(self._on_selection_changed)

    def apply_row_clicked(self, row: TweakRow) -> None:
        self.row_apply_requested.emit(row.tweak)

    def set_row_apply_handler(self, handler) -> None:
        for row in self._rows.values():
            row.apply_btn.clicked.connect(lambda _, r=row: handler(r))

    def set_row_disable_handler(self, handler) -> None:
        for row in self._rows.values():
            row.disable_btn.clicked.connect(lambda _, r=row: handler(r))

    def _on_selection_changed(self) -> None:
        count = sum(1 for r in self._rows.values() if r.is_checked)
        self._count_label.setText(f"{count} selected")

    def set_status(self, tweak_id: str, status: str, reason: str = "") -> None:
        if tweak_id in self._rows:
            self._rows[tweak_id].set_status(status, reason)

    def status_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for row in self._rows.values():
            counts[row.status] = counts.get(row.status, 0) + 1
        return counts

    def selected_tweaks(self) -> List[Dict]:
        return [t for t in self._tweaks
                if self._rows[t["id"]].is_checked]

    def apply_preset(self, tweak_ids: List[str]) -> None:
        for tweak_id, row in self._rows.items():
            row.set_checked(tweak_id in tweak_ids)

    def current_state(self) -> List[str]:
        return [t["id"] for t in self._tweaks
                if self._rows[t["id"]].is_checked]

    def select_all(self) -> None:
        for row in self._rows.values():
            row.set_checked(True)

    def deselect_all(self) -> None:
        for row in self._rows.values():
            row.set_checked(False)

    def select_by_status(self, status: str) -> None:
        """status: a status code, or 'all'.

        Matches on the row's stored status code, not on its label text — the
        old substring test selected every "Not Applied" row when asked for
        "Applied", because one string contains the other.
        """
        for row in self._rows.values():
            if not row.checkbox.isEnabled():
                continue
            if status == "all" or row.status == status:
                row.set_checked(True)
        self._on_selection_changed()

    def _select_by_status_filtered(self, status: str) -> None:
        """Select exactly the rows with this status, clearing the rest."""
        for row in self._rows.values():
            row.set_checked(row.checkbox.isEnabled() and row.status == status)
        self._on_selection_changed()

    # Signal emitted when row selection should show details
    row_selected = pyqtSignal(dict)
    row_apply_requested = pyqtSignal(dict)

    def mousePressEvent(self, event) -> None:
        # Find which row was clicked
        child = self.childAt(event.pos())
        if child:
            for row in self._rows.values():
                if row.isAncestorOf(child):
                    self.row_selected.emit(row.tweak)
                    break
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# AppManagerTab
# ---------------------------------------------------------------------------

class AppManagerTab(QWidget):
    """Remove installed packages, install catalogued ones.

    Everything below the queues was already implemented and unreachable:
    `AppCatalog.install_app`, `.remove_appx` and `.remove_app_winget`
    all existed, and nothing in the UI ever called them. The Apply
    Changes button was connected to nothing, the installed list's
    itemChanged was connected to nothing, and the module's own Apply
    Selected never looked at this tab -- so checking anything here and
    pressing Apply answered "Check at least one tweak to apply."
    """

    #: Someone pressed Apply Changes. The module does the work: this tab
    #: knows what is queued, not how to install anything.
    apply_requested = pyqtSignal()

    def __init__(self, catalog: AppCatalog, config,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._catalog = catalog
        self._config = config
        self._protected = set(
            config.get("tweaks.protected_apps", list(PROTECTED_APPS_DEFAULT))
            if config is not None else list(PROTECTED_APPS_DEFAULT)
        )
        self._remove_queue: set = set()
        #: Win32/desktop apps, queued by winget id. Kept apart from
        #: `_remove_queue` because they are removed by a different command --
        #: `winget uninstall`, not `Remove-AppxPackage`.
        self._remove_winget_queue: set = set()
        self._install_queue: set = set()
        #: True while a list is being rebuilt. setCheckState() fires
        #: itemChanged for every row, so without this a refresh of the
        #: installed list would queue every package on the machine for
        #: removal.
        self._populating = False
        self._applying = False

        layout = QVBoxLayout(self)

        # Filter bar
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel("Category:"))
        self._cat_combo = QComboBox()
        self._cat_combo.addItem("All")
        if catalog is not None:
            self._cat_combo.addItems(catalog.categories())
        self._cat_combo.currentTextChanged.connect(self._refresh_catalog_list)
        filter_bar.addWidget(self._cat_combo)
        filter_bar.addStretch()
        layout.addLayout(filter_bar)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Installed section
        installed_group = QGroupBox("Installed AppX Packages (check to remove)")
        ig_layout = QVBoxLayout(installed_group)
        self._installed_list = QListWidget()
        self._installed_list.itemChanged.connect(
            self._on_installed_item_changed)
        ig_layout.addWidget(self._installed_list)
        splitter.addWidget(installed_group)

        # Desktop apps section. AppX packages are only half of what is
        # installed on a Windows machine, and usually the less interesting
        # half: TreeSize's AppX package is just its shell context menu, while
        # TreeSize itself is an ordinary program with a registry uninstall
        # entry, which this tab had no way to touch.
        desktop_group = QGroupBox(
            "Installed Programs — Win32 and winget (check to remove)")
        dg_layout = QVBoxLayout(desktop_group)
        self._desktop_list = QListWidget()
        self._desktop_list.itemChanged.connect(self._on_desktop_item_changed)
        dg_layout.addWidget(self._desktop_list)
        splitter.addWidget(desktop_group)

        # Catalog section
        catalog_group = QGroupBox("Available to Install (via winget)")
        cg_layout = QVBoxLayout(catalog_group)
        self._catalog_list = QListWidget()
        self._catalog_list.itemChanged.connect(self._on_catalog_item_changed)
        cg_layout.addWidget(self._catalog_list)
        splitter.addWidget(catalog_group)

        layout.addWidget(splitter)

        # Apply bar
        apply_bar = QHBoxLayout()
        self._apply_label = QLabel("No changes queued")
        apply_bar.addWidget(self._apply_label)
        apply_bar.addStretch()
        self._apply_btn = QPushButton("Apply Changes")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self.apply_requested)
        apply_bar.addWidget(self._apply_btn)
        layout.addLayout(apply_bar)

        if catalog is not None:
            self._refresh_catalog_list()

    def populate_installed(self, installed_appx: set) -> None:
        self._populating = True
        self._installed_list.clear()
        for pkg in sorted(installed_appx):
            item = QListWidgetItem(pkg)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if pkg in self._protected:
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setText(f"{pkg}  🔒")
            else:
                item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, pkg)
            self._installed_list.addItem(item)
        self._populating = False
        # A package that has since been removed must not linger in the queue.
        self._remove_queue &= set(installed_appx)
        self._update_apply_label()

    def populate_installed_desktop(self, apps: list) -> None:
        """Fill the desktop-app list from `winget list` rows.

        Same guard as the AppX list: setCheckState fires itemChanged for every
        row, so without `_populating` a refresh queues every program on the
        machine for removal.
        """
        self._populating = True
        self._desktop_list.clear()
        for app in sorted(apps, key=lambda a: a.name.lower()):
            item = QListWidgetItem(app.display)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if app.app_id in self._remove_winget_queue
                else Qt.CheckState.Unchecked)
            if app.name in self._protected or app.app_id in self._protected:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setText(f"{app.display}  🔒")
            # The row shows a human name; what winget needs is the id.
            item.setData(Qt.ItemDataRole.UserRole, app.app_id)
            item.setToolTip(app.app_id)
            self._desktop_list.addItem(item)
        self._populating = False
        self._remove_winget_queue &= {app.app_id for app in apps}
        #: Names of what is installed, for the catalog list: plenty of apps
        #: are installed under a registry id winget invented
        #: (`ARP\Machine\X86\Google Chrome`) rather than under the id the
        #: catalog knows them by, and an id-only check offers to install a
        #: browser that is already on the machine.
        self._desktop_names = {app.name.lower() for app in apps}
        self._update_apply_label()

    def _on_desktop_item_changed(self, item: QListWidgetItem) -> None:
        if self._populating:
            return
        app_id = item.data(Qt.ItemDataRole.UserRole)
        if app_id is None:
            return
        if item.checkState() == Qt.CheckState.Checked:
            if app_id in self._protected:
                logger.warning("refusing to queue protected app %s", app_id)
                return
            self._remove_winget_queue.add(app_id)
        else:
            self._remove_winget_queue.discard(app_id)
        self._update_apply_label()

    def populate_installed_winget(self, installed_ids: set) -> None:
        self._installed_winget = installed_ids
        self._refresh_catalog_list()

    def _refresh_catalog_list(self) -> None:
        self._populating = True
        cat = self._cat_combo.currentText()
        entries = self._catalog.filter_by_category(cat)
        installed = getattr(self, "_installed_winget", set())
        self._catalog_list.clear()
        for entry in entries:
            item = QListWidgetItem(
                f"{entry['name']}  ({entry['publisher']})")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # Exact name match only. Prefixes would mark Notepad++
            # installed because Notepad is.
            already = (entry["winget_id"] in installed
                       or entry["name"].lower() in
                       getattr(self, "_desktop_names", set()))
            item.setCheckState(
                Qt.CheckState.Checked if entry["winget_id"] in self._install_queue
                else Qt.CheckState.Unchecked
            )
            if already:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setText(item.text() + "  ✅ Installed")
            item.setData(Qt.ItemDataRole.UserRole, entry["winget_id"])
            item.setToolTip(entry.get("description", ""))
            self._catalog_list.addItem(item)
        self._populating = False
        self._update_apply_label()

    def _on_installed_item_changed(self, item: QListWidgetItem) -> None:
        """A checked package is queued for removal.

        Protected packages are refused here as well as being disabled in
        the list: the flag stops a person clicking them, and this stops
        anything else putting one in the queue.
        """
        if self._populating:
            return
        pkg = item.data(Qt.ItemDataRole.UserRole)
        if pkg is None:
            return
        if item.checkState() == Qt.CheckState.Checked:
            if pkg in self._protected:
                logger.warning("refusing to queue protected package %s",
                               pkg)
                return
            self._remove_queue.add(pkg)
        else:
            self._remove_queue.discard(pkg)
        self._update_apply_label()

    def queued_changes(self) -> Dict[str, List[str]]:
        """What this tab would do, in a stable order."""
        return {"remove": sorted(self._remove_queue),
                "remove_winget": sorted(self._remove_winget_queue),
                "install": sorted(self._install_queue)}

    def has_queued_changes(self) -> bool:
        return bool(self._remove_queue or self._remove_winget_queue
                    or self._install_queue)

    def clear_queues(self) -> None:
        self._remove_queue.clear()
        self._remove_winget_queue.clear()
        self._install_queue.clear()
        self._update_apply_label()

    def set_applying(self, applying: bool) -> None:
        """Grey out Apply Changes while a run is in flight.

        Without this the tab's own button stayed live during an apply and a
        second click started a second winget, which fails because the first
        is still holding the installer -- and that failure was reported as
        the result.
        """
        self._applying = applying
        if applying:
            self._apply_btn.setEnabled(False)
        else:
            self._update_apply_label()

    def _on_catalog_item_changed(self, item: QListWidgetItem) -> None:
        if self._populating:
            return
        wid = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._install_queue.add(wid)
        else:
            self._install_queue.discard(wid)
        self._update_apply_label()

    def _update_apply_label(self) -> None:
        if getattr(self, "_applying", False):
            return
        n_remove = len(self._remove_queue) + len(self._remove_winget_queue)
        n_install = len(self._install_queue)
        if n_remove + n_install == 0:
            self._apply_label.setText("No changes queued")
            self._apply_btn.setEnabled(False)
        else:
            self._apply_label.setText(
                f"Remove {n_remove}, Install {n_install}")
            self._apply_btn.setEnabled(True)


# ---------------------------------------------------------------------------
# TweaksModule
# ---------------------------------------------------------------------------

class TweaksModule(BaseModule):
    name         = "Tweaks"
    icon         = "🧹"
    description  = "Debloater, privacy, performance, telemetry and UI tweaks."
    requires_admin = True
    group        = ModuleGroup.OPTIMIZE

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._tab_widgets: Dict[str, TweakTab] = {}
        self._app_tab: Optional[AppManagerTab] = None
        self._applying = False
        self._engine: Optional[TweakEngine] = None
        self._preset_mgr: Optional[PresetManager] = None
        self._catalog: Optional[AppCatalog] = None
        self._progress: Optional[QProgressBar] = None
        self._log_output: Optional[QTextEdit] = None
        self._tabs: Optional[QTabWidget] = None
        self._signals = _Signals()
        self._details_panel: Optional[_DetailsPanel] = None

    # ------------------------------------------------------------------
    # BaseModule lifecycle
    # ------------------------------------------------------------------

    def on_start(self, app) -> None:
        self.app = app
        self._engine = TweakEngine(backup_service=app.backup)
        self._preset_mgr = PresetManager()
        self._catalog = AppCatalog()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def on_activate(self) -> None:
        # Re-check every time the tab is opened, not just the first time — the
        # underlying registry/service/appx state can change from outside this
        # screen (a manual revert, another tool, a previous session), and a
        # stale "Applied"/"Not Applied" label is actively misleading.
        self._detect_statuses()
        self._detect_apps()

    def on_deactivate(self) -> None:
        self.cancel_all_workers()

    def get_status_info(self) -> str:
        return "Tweaks & Debloater"

    # ------------------------------------------------------------------
    # Widget
    # ------------------------------------------------------------------

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # Top: preset toolbar + search bar
        top_layout = QVBoxLayout()
        top_layout.setSpacing(4)
        top_layout.addWidget(self._build_preset_toolbar())
        top_layout.addWidget(self._build_search_bar())
        layout.addLayout(top_layout)

        # Main: tabs with details panel side-by-side
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        self._tabs = QTabWidget()
        for category, filename in _CATEGORY_FILES.items():
            path = os.path.join(_DEFS_DIR, filename)
            tweaks = TweakEngine.load_definitions(path)
            tab = TweakTab(tweaks)
            tab._category_name = category
            self._tab_widgets[category] = tab
            self._tabs.addTab(tab, category)
            tab.row_selected.connect(self._on_row_selected)
            tab.set_row_apply_handler(self._on_row_apply)
            tab.set_row_disable_handler(self._on_row_disable)

        config = self.app.config if self.app else None
        self._app_tab = AppManagerTab(self._catalog, config)
        # Its own Apply Changes button and the bottom bar's Apply Selected do
        # the same thing, so whichever one is pressed, what is checked happens.
        self._app_tab.apply_requested.connect(self._on_apply)
        self._tabs.addTab(self._app_tab, "Apps")

        main_splitter.addWidget(self._tabs)

        self._details_panel = _DetailsPanel()
        self._details_panel.apply_requested.connect(self._on_apply_single_tweak)
        main_splitter.addWidget(self._details_panel)
        main_splitter.setSizes([700, 300])

        layout.addWidget(main_splitter, stretch=1)

        layout.addWidget(self._build_bottom_bar())
        self._signals.status_detected.connect(self._on_status_detected)
        self._signals.apps_detected.connect(self._on_apps_detected)
        return self._widget

    def _build_search_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(QLabel("Search:"))
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Filter tweaks by name or description...")
        self._search_input.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search_input, stretch=1)

        # Risk filter
        layout.addWidget(QLabel("Risk:"))
        self._risk_filter = QComboBox()
        self._risk_filter.addItems(["All", "Low", "Medium", "High"])
        self._risk_filter.currentTextChanged.connect(self._on_filter_changed)
        layout.addWidget(self._risk_filter)

        # Status filter
        layout.addWidget(QLabel("Status:"))
        self._status_filter = QComboBox()
        self._status_filter.addItems([
            "All", "Applicable", "Applied", "Not Applied", "Partial",
            "Not Applicable", "Unknown",
        ])
        self._status_filter.setToolTip(
            "Applicable — everything except tweaks that have nothing to do on "
            "this PC\nPartial — some of the tweak's steps are in place\n"
            "Not Applicable — the target service, task, app or Windows version "
            "isn't on this PC")
        self._status_filter.currentTextChanged.connect(self._on_filter_changed)
        layout.addWidget(self._status_filter)

        # What "not applicable" is being judged against — without this the
        # verdict is unfalsifiable from the user's side.
        self._os_label = QLabel(get_os_context().friendly_name)
        self._os_label.setStyleSheet("color: #888888; font-size: 11px;")
        self._os_label.setToolTip(
            "Tweaks are checked against this Windows build, edition and "
            "architecture.")
        layout.addWidget(self._os_label)

        return bar

    def _build_preset_toolbar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("Preset:"))
        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(180)
        self._refresh_preset_combo()
        layout.addWidget(self._preset_combo)

        load_btn = QPushButton("Load")
        load_btn.clicked.connect(self._on_load_preset)
        layout.addWidget(load_btn)

        save_btn = QPushButton("Save As…")
        save_btn.clicked.connect(self._on_save_preset)
        layout.addWidget(save_btn)

        export_btn = QPushButton("Export…")
        export_btn.clicked.connect(self._on_export_preset)
        layout.addWidget(export_btn)

        import_btn = QPushButton("Import…")
        import_btn.clicked.connect(self._on_import_preset)
        layout.addWidget(import_btn)

        layout.addStretch()

        self._select_all_global_btn = QPushButton("Select All in Tab")
        self._select_all_global_btn.clicked.connect(self._on_select_all_tab)
        layout.addWidget(self._select_all_global_btn)

        self._deselect_all_global_btn = QPushButton("Deselect All in Tab")
        self._deselect_all_global_btn.clicked.connect(self._on_deselect_all_tab)
        layout.addWidget(self._deselect_all_global_btn)

        return bar

    def _build_bottom_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)

        self._apply_btn = QPushButton("Apply Selected")
        self._apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(self._apply_btn)

        undo_btn = QPushButton("↩ Undo Applied Tweaks…")
        undo_btn.setToolTip(
            "Every apply here is recorded automatically. Open this to revert a "
            "previous session — this is NOT the same as the 'System Restore' "
            "module in the sidebar (that one is Windows' own OS restore points)."
        )
        undo_btn.clicked.connect(self._open_restore_manager)
        layout.addWidget(undo_btn)

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setMaximumWidth(200)
        layout.addWidget(self._progress)

        self._log_output = QTextEdit()
        self._log_output.setReadOnly(True)
        self._log_output.setMaximumHeight(80)
        self._log_output.setVisible(False)
        layout.addWidget(self._log_output, stretch=1)

        return bar

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _on_search_changed(self, text: str) -> None:
        self._filter_tweaks()

    def _on_filter_changed(self) -> None:
        self._filter_tweaks()

    def _filter_tweaks(self) -> None:
        search_text = self._search_input.text().lower()
        risk_filter = self._risk_filter.currentText().lower()
        status_filter = self._status_filter.currentText()

        for tab in self._tab_widgets.values():
            for tweak in tab._tweaks:
                row = tab._rows.get(tweak["id"])
                if row is None:
                    continue

                # Text match — the status reason is searchable too, so
                # "not installed" pulls up everything N/A for that reason.
                name_match = not search_text or (
                    search_text in tweak["name"].lower() or
                    search_text in tweak.get("description", "").lower() or
                    search_text in row.status_reason.lower()
                )

                # Risk match
                risk_match = risk_filter == "all" or tweak.get("risk", "low") == risk_filter

                # Status match
                if status_filter == "All":
                    status_match = True
                elif status_filter == "Applicable":
                    status_match = row.status != NOT_APPLICABLE
                else:
                    status_match = row.status == _FILTER_TO_STATUS.get(status_filter)

                visible = name_match and risk_match and status_match
                row.setVisible(visible)

    # ------------------------------------------------------------------
    # Row interaction
    # ------------------------------------------------------------------

    def _on_row_selected(self, tweak: Dict) -> None:
        self._details_panel.set_tweak(tweak)

    def _on_row_apply(self, row: TweakRow) -> None:
        self._on_apply_single_tweak(row.tweak)

    def _on_apply_single_tweak(self, tweak: Dict) -> None:
        if not self.app:
            return

        risk = tweak.get("risk", "low")
        if risk == "high":
            reply = QMessageBox.question(
                self._widget, "Confirm High-Risk Tweak",
                f"You're about to apply a HIGH RISK tweak:\n\n{tweak['name']}\n\n"
                "Are you sure you want to continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        rp_id = self.app.backup.create_restore_point(
            f"Single tweak: {tweak['name']}", "Tweaks")

        errors = []
        logger.info("Applying single tweak: %s", tweak.get("name", ""))
        self._log_output.setVisible(True)

        def _worker_fn(worker):
            self._engine.apply_tweak(
                tweak, rp_id,
                on_error=lambda e: errors.append(e))
            return errors

        w = Worker(_worker_fn)
        w.signals.result.connect(self._on_single_apply_result)
        w.signals.error.connect(lambda e: self._log_output.append(f"Error: {e}"))
        self._workers.append(w)
        self.app.thread_pool.start(w)

    def _on_single_apply_result(self, errors: list) -> None:
        if self._log_output:
            if errors:
                for e in errors:
                    self._log_output.append(f"⚠ {e}")
            else:
                self._log_output.append("✅ Tweak applied successfully.")
        logger.info("Single tweak applied: %d error(s)", len(errors))
        self._detect_statuses()

    def _on_row_disable(self, row: TweakRow) -> None:
        self._on_disable_single_tweak(row.tweak)

    def _on_disable_single_tweak(self, tweak: Dict) -> None:
        """Revert one applied tweak back to its pre-apply state, without touching
        anything else — the per-row 'Disable' button. Uses BackupService.revert_tweak(),
        which targets just this tweak's most recent applied steps, unlike
        restore_point()/the Restore Manager dialog which undo a whole session.

        No confirmation dialog, by design (2026-08-16): this only ever restores a
        previously-recorded before-value — the same trust level as "Apply" already
        gets for low/medium risk tweaks, which also don't confirm."""
        if not self.app:
            return

        logger.info("Disabling single tweak: %s", tweak.get("name", ""))
        self._log_output.setVisible(True)

        def _worker_fn(worker):
            return self.app.backup.revert_tweak(tweak["id"])

        w = Worker(_worker_fn)
        w.signals.result.connect(self._on_single_disable_result)
        w.signals.error.connect(lambda e: self._log_output.append(f"Error: {e}"))
        self._workers.append(w)
        self.app.thread_pool.start(w)

    def _on_single_disable_result(self, result) -> None:
        if self._log_output:
            if result.success:
                self._log_output.append("✅ Tweak disabled (reverted).")
            elif result.partial:
                self._log_output.append(
                    f"⚠ Partially reverted — {len(result.failed_steps)} step(s) failed.")
                for e in result.errors:
                    self._log_output.append(f"⚠ {e}")
            else:
                self._log_output.append("❌ Could not disable this tweak.")
                for e in result.errors:
                    self._log_output.append(f"⚠ {e}")
        logger.info("Single tweak disable: success=%s partial=%s failed=%d",
                   result.success, result.partial, len(result.failed_steps))
        self._detect_statuses()

    # ------------------------------------------------------------------
    # Preset actions
    # ------------------------------------------------------------------

    def _refresh_preset_combo(self) -> None:
        if not self._preset_mgr:
            return
        self._preset_combo.clear()
        for p in self._preset_mgr.list_presets():
            self._preset_combo.addItem(p["name"])

    def _on_load_preset(self) -> None:
        name = self._preset_combo.currentText()
        if not name or not self._preset_mgr:
            return
        try:
            preset = self._preset_mgr.load_preset(name)
        except KeyError as e:
            QMessageBox.warning(self._widget, "Preset", str(e))
            return
        tweaks = preset.get("tweaks", {})
        for category, tab in self._tab_widgets.items():
            key = category.lower().replace(" ", "_")
            ids = tweaks.get(key, [])
            if not ids:
                ids = tweaks.get(category, [])
            # Expand wildcard ["*"] — means "all tweak IDs in this category"
            if ids == ["*"]:
                ids = [t["id"] for t in tab._tweaks]
            tab.apply_preset(ids)

    def _on_save_preset(self) -> None:
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self._widget, "Save Preset", "Preset name:")
        if not ok or not name.strip():
            return
        data = {
            "name": name.strip(), "version": 1,
            "tweaks": {cat: tab.current_state()
                       for cat, tab in self._tab_widgets.items()},
            "apps": {"remove": [], "install": [], "protected": []}
        }
        self._preset_mgr.save_preset(name.strip(), data)
        self._refresh_preset_combo()
        # _refresh_preset_combo() clears+repopulates the combo, which resets
        # currentIndex to 0 (the first builtin) — reselect the preset we just saved
        # so the UI doesn't silently jump to showing an unrelated preset.
        self._preset_combo.setCurrentText(name.strip())

    def _on_export_preset(self) -> None:
        name = self._preset_combo.currentText()
        if not name:
            return
        path, _ = QFileDialog.getSaveFileName(
            self._widget, "Export Preset", f"{name}.json",
            "JSON (*.json);;ZIP (*.zip)")
        if path:
            self._preset_mgr.export_preset(name, path)

    def _on_import_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self._widget, "Import Preset", "",
            "Preset files (*.json *.zip)")
        if path:
            try:
                name = self._preset_mgr.import_preset(path)
                self._refresh_preset_combo()
                QMessageBox.information(
                    self._widget, "Import", f"Preset '{name}' imported.")
            except Exception as e:
                QMessageBox.critical(self._widget, "Import failed", str(e))

    def _open_restore_manager(self) -> None:
        """Open the app's own change-history/undo dialog (Tools ▸ Restore Manager
        uses the same dialog). Kept as a local import to match main_window.py's
        lazy-import pattern for this dialog."""
        from ui.restore_manager import RestoreManagerDialog
        RestoreManagerDialog(self.app, self._widget).exec()

    def _on_select_all_tab(self) -> None:
        idx = self._tabs.currentIndex()
        tab_widgets = list(self._tab_widgets.values())
        if 0 <= idx < len(tab_widgets):
            tab_widgets[idx].select_all()

    def _on_deselect_all_tab(self) -> None:
        idx = self._tabs.currentIndex()
        tab_widgets = list(self._tab_widgets.values())
        if 0 <= idx < len(tab_widgets):
            tab_widgets[idx].deselect_all()

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _warn_nothing_selected(self) -> None:
        QMessageBox.information(
            self._widget, "Nothing selected",
            "Check at least one tweak, or an app to install or "
            "remove in the Apps tab.")

    def _confirm_app_changes(self, changes: Dict[str, list]) -> bool:
        """Removing somebody's applications is not a one-click thing.

        Installs are listed too, because winget will go and fetch
        them; the question is the same one either way -- is this
        actually what you meant to check.
        """
        lines = []
        if changes["remove"]:
            lines.append("Remove (Windows Store packages):")
            lines += [f"    {pkg}" for pkg in changes["remove"]]
        if changes.get("remove_winget"):
            lines.append("Uninstall (installed programs):")
            lines += [f"    {app}" for app in changes["remove_winget"]]
        if changes["install"]:
            lines.append("Install:")
            lines += [f"    {wid}" for wid in changes["install"]]
        answer = QMessageBox.question(
            self._widget, "Apply app changes?",
            "\n".join(lines),
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _on_apply(self) -> None:
        """Apply everything that is checked -- tweaks AND apps.

        The Apps tab is a tab of `self._tabs` but was never in
        `self._tab_widgets`, which is the only thing this used to
        look at. So anything checked there was invisible here, and
        the answer to pressing Apply Selected with two apps ticked
        was "Check at least one tweak to apply."
        """
        if self._applying:
            # Reported as "could not install Mozilla.Firefox" while Firefox
            # was installing perfectly well: a second Apply started a second
            # winget, winget will not run twice at once, and the second one's
            # refusal was reported as the outcome. The bottom bar's button was
            # disabled during a run and the Apps tab's own button was not, so
            # that was the way back in.
            logger.info("Tweaks: apply already running; ignoring the request")
            return

        tweaks_to_apply = []
        for tab in self._tab_widgets.values():
            tweaks_to_apply.extend(tab.selected_tweaks())

        app_changes = (self._app_tab.queued_changes() if self._app_tab
                       else {"remove": [], "remove_winget": [],
                             "install": []})
        has_apps = bool(app_changes["remove"] or app_changes["remove_winget"]
                        or app_changes["install"])

        if not tweaks_to_apply and not has_apps:
            self._warn_nothing_selected()
            return

        if has_apps and not self._confirm_app_changes(app_changes):
            return

        if not self.app:
            return

        rp_id = self.app.backup.create_restore_point(
            "Tweaks session", "Tweaks")

        self._applying = True
        self._apply_btn.setEnabled(False)
        if self._app_tab is not None:
            self._app_tab.set_applying(True)
        total = (len(tweaks_to_apply) + len(app_changes["remove"])
                 + len(app_changes["remove_winget"])
                 + len(app_changes["install"]))
        self._progress.setMaximum(total)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._log_output.clear()
        self._log_output.setVisible(True)
        errors = []

        logger.info("Tweaks: applying %d tweak(s), %d package removal(s), "
                    "%d app uninstall(s), %d install(s)",
                    len(tweaks_to_apply), len(app_changes["remove"]),
                    len(app_changes["remove_winget"]),
                    len(app_changes["install"]))

        def _worker_fn(worker):
            done = 0
            for tweak in tweaks_to_apply:
                if worker.is_cancelled:
                    return errors
                logger.info("Applying tweak: %s",
                            tweak.get("name", tweak.get("id", "")))
                self._engine.apply_tweak(
                    tweak, rp_id,
                    on_error=lambda e: errors.append(e))
                done += 1
                worker.signals.progress.emit(done)

            # Apps. The catalog has done this work all along; what
            # was missing was anything calling it.
            for pkg in app_changes["remove"]:
                if worker.is_cancelled:
                    return errors
                logger.info("Removing package: %s", pkg)
                if not self._catalog.remove_appx(
                        pkg, on_output=lambda line: logger.info(
                            "  %s", line)):
                    errors.append(f"Could not remove {pkg}")
                done += 1
                worker.signals.progress.emit(done)

            # Desktop apps. A different command entirely: an ordinary
            # program is not removable by Remove-AppxPackage.
            for app_id in app_changes["remove_winget"]:
                if worker.is_cancelled:
                    return errors
                logger.info("Uninstalling app: %s", app_id)
                if not self._catalog.remove_app_winget(
                        app_id, on_output=lambda line: logger.info(
                            "  %s", line)):
                    errors.append(f"Could not remove {app_id}")
                done += 1
                worker.signals.progress.emit(done)

            for winget_id in app_changes["install"]:
                if worker.is_cancelled:
                    return errors
                logger.info("Installing: %s", winget_id)
                if not self._catalog.install_app(
                        winget_id, on_output=lambda line: logger.info(
                            "  %s", line)):
                    errors.append(f"Could not install {winget_id}")
                done += 1
                worker.signals.progress.emit(done)
            return errors

        w = Worker(_worker_fn)
        w.signals.progress.connect(self._progress.setValue)
        w.signals.result.connect(self._on_apply_result)
        w.signals.error.connect(
            lambda e: self._log_output.append(f"Error: {e}"))
        self._workers.append(w)
        self.app.thread_pool.start(w)

    def _on_apply_result(self, errors: list) -> None:
        self._applying = False
        self._apply_btn.setEnabled(True)
        if self._app_tab is not None:
            self._app_tab.set_applying(False)
        self._progress.setVisible(False)
        for e in errors:
            self._log_output.append(f"⚠ {e}")
        if not errors:
            self._log_output.append("✅ Everything applied successfully.")
        logger.info("Tweaks apply complete: %d error(s)", len(errors))
        if self._app_tab is not None:
            self._app_tab.clear_queues()
        self._detect_statuses()

    # ------------------------------------------------------------------
    # Status detection
    # ------------------------------------------------------------------

    def _detect_statuses(self) -> None:
        if not self._engine:
            return
        all_tweaks = []
        for tab in self._tab_widgets.values():
            all_tweaks.extend(tab._tweaks)

        def _worker_fn(worker):
            def _emit(tweak, result):
                self._signals.status_detected.emit(
                    tweak["id"], result.status, result.reason)

            self._engine.detect_many(
                all_tweaks, _emit, is_cancelled=lambda: worker.is_cancelled)
            return None

        w = Worker(_worker_fn)
        self._workers.append(w)
        if self.app:
            self.app.thread_pool.start(w)

    def _on_status_detected(self, tweak_id: str, status: str, reason: str) -> None:
        for tab in self._tab_widgets.values():
            tab.set_status(tweak_id, status, reason)
        if self._details_panel is not None:
            self._details_panel.update_status(tweak_id, status, reason)
        self._filter_tweaks()  # Re-apply status filter if needed
        self._update_tab_labels()

    def _update_tab_labels(self) -> None:
        """Put "Network (3/45)" on the tab, counting only what is applicable —
        a category of forty tweaks where thirty do not apply to this build
        should not read as 3/40."""
        if self._tabs is None:
            return
        for index in range(self._tabs.count()):
            tab = self._tabs.widget(index)
            category = getattr(tab, "_category_name", None)
            if category is None:
                continue
            counts = tab.status_counts()
            total = sum(counts.values())
            na = counts.get(NOT_APPLICABLE, 0)
            applied = counts.get(APPLIED, 0)
            live = total - na
            self._tabs.setTabText(index, f"{category} ({applied}/{live})")
            self._tabs.setTabToolTip(
                index,
                f"{applied} applied, {counts.get(PARTIAL, 0)} partial, "
                f"{counts.get(NOT_APPLIED, 0)} not applied, "
                f"{na} not applicable, {counts.get(UNKNOWN, 0)} unknown")

    # ------------------------------------------------------------------
    # App detection
    # ------------------------------------------------------------------

    def _detect_apps(self) -> None:
        if not self._catalog or not self._app_tab:
            return

        def _worker_fn(worker):
            # One `winget list` answers both questions -- which catalog
            # entries are installed, and which desktop apps can be removed.
            output = self._catalog._winget_list_output()
            installed_ids = (self._catalog._parse_winget_list(output)
                             if output else set())
            desktop = (self._catalog.desktop_apps_from(output)
                       if output else [])
            installed_appx = self._catalog.detect_installed_appx()
            return installed_ids, installed_appx, desktop

        def _on_result(result):
            installed_ids, installed_appx, desktop = result
            self._signals.apps_detected.emit(installed_ids, installed_appx,
                                             desktop)

        w = Worker(_worker_fn)
        w.signals.result.connect(_on_result)
        self._workers.append(w)
        if self.app:
            self.app.thread_pool.start(w)

    def _on_apps_detected(self, installed_ids: set, installed_appx: set,
                          desktop: list) -> None:
        if self._app_tab:
            self._app_tab.populate_installed(installed_appx)
            self._app_tab.populate_installed_desktop(desktop)
            self._app_tab.populate_installed_winget(installed_ids)
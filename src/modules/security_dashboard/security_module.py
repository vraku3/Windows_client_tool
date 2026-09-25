"""
Security Dashboard — interactive security posture monitoring and control.

Provides real-time status of Windows security features with the ability
to toggle Defender settings, firewall profiles, SmartScreen, and more.
Each toggle stores its previous state for per-setting revert.
"""

import ctypes
import os
import threading
import re
import shutil
import tempfile
from ctypes import wintypes
from datetime import datetime
from typing import Any, Dict, List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QDialog, QHeaderView, QMenu, QFileDialog,
    QPushButton, QLabel, QFrame, QProgressBar, QCheckBox, QLineEdit,
    QPlainTextEdit, QTabWidget, QTableWidget, QGroupBox, QScrollArea, QSizePolicy,
)
from PyQt6.QtCore import QSize, QThreadPool, pyqtSignal
from PyQt6.QtGui import QColor

import logging

from core.admin_utils import is_admin
from core.base_module import BaseModule
from core.semantic_colors import semantic
from core.module_groups import ModuleGroup
from core.search_provider import FilterField, SearchProvider, SearchQuery, SearchResult
from core.table_ui import centered_item, fit_table
from core.worker import Worker, COMWorker
from ui.error_banner import ErrorBanner
from ui.empty_state import EmptyState
from modules.security_dashboard import snapshots
from modules.security_dashboard.catalog import load_catalog
from modules.security_dashboard.catalog.model import Category, ControlState
from modules.security_dashboard.applier import apply_batch
from modules.security_dashboard.elevated_helper import (
    build_elevated_command, changes_of, read_result_file,
    write_batch_file)
from modules.security_dashboard.profile import (
    available_baselines, export_profile, import_profile, plan_baseline,
    read_profile, write_profile)
from modules.security_dashboard.reverting import revert_batch
from modules.security_dashboard.staging import ChangeSet
from modules.security_dashboard.security_reader import (
    get_overview_status,
    get_security_events,
    run_quick_scan,
    run_update_definitions,
)

logger = logging.getLogger(__name__)
from core.widget_life import widget_is_valid

_widget_valid = widget_is_valid

COLOR_MAP = {
    "green": "#27AE60",
    "amber": "#E67E22",
    "red":   "#E74C3C",
}

# ── ControlCard: a catalog entry, rendered ─────────────────────────────────

class ControlCard(QFrame):
    """One `SecurityControl`, drawn. It stages; it never writes.

    `_ToggleCard` below is the thing this replaces, and the differences are
    the point:

    * **It renders by VALUE, not by truthiness.** 12 of the 149 controls are
      numeric -- NTLM level, cached logons, minimum password length, cloud
      block level, the four threat actions. This machine's `ntlm_level` reads
      3 and wants 5; drawn as a toggle that is a green "On".
    * **A numeric control's action is "set it to what the catalog
      recommends"**, never "the opposite of what it reads". `not 3` is False,
      False resolves to `off_steps`, and `ntlm_level`'s off_steps write the 3
      it already has -- while `defender_threat_severe`'s write
      `-SevereThreatDefaultAction None`, turning the severe-threat action off.
    * **Green means "at what the catalog wants"**, which is `desired`, not
      whatever colour the reader felt like. 14 controls have `desired=None`
      and are legitimately amber or red; those are not problems and are not
      coloured as though they were (Ruling 6).
    * **A reading of None is "Unknown"**, never "Off". A refused read is not
      an unset value.
    * **No hex literal anywhere.** Colours are resolved through
      `semantic()` at every render, so a theme change is followed rather than
      frozen at build time.
    """

    #: (control_id, target value). The pane stages it; nothing is written here.
    staged = pyqtSignal(str, object)

    _UNREAD = object()

    def __init__(self, control, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._control = control
        self._reading: Any = self._UNREAD
        self._staged_to: Any = self._UNREAD
        self._build_ui()

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(4)

        top = QHBoxLayout()
        self.title_label = QLabel(self._control.title)
        font = self.title_label.font()
        font.setBold(True)
        self.title_label.setFont(font)
        top.addWidget(self.title_label, 1)

        self.status_badge = QLabel("—")
        self.status_badge.setObjectName("controlStatusBadge")
        top.addWidget(self.status_badge)
        root.addLayout(top)

        self.description_label = QLabel(self._control.description)
        self.description_label.setWordWrap(True)
        self.description_label.setObjectName("controlDescription")
        root.addWidget(self.description_label)

        # Prefixed and italic, because a read-only card has no button either:
        # rendered as plain body text this sentence is indistinguishable from
        # the description above it, and the two say completely different
        # things -- what the setting is, versus why nobody can change it.
        reason = self._control.read_only_reason
        self.reason_label = QLabel(f"Read-only — {reason}" if reason else "")
        self.reason_label.setWordWrap(True)
        self.reason_label.setObjectName("controlReason")
        italic = self.reason_label.font()
        italic.setItalic(True)
        self.reason_label.setFont(italic)
        self.reason_label.setVisible(bool(reason))
        root.addWidget(self.reason_label)

        self.staged_label = QLabel("")
        self.staged_label.setWordWrap(True)
        root.addWidget(self.staged_label)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setVisible(False)
        root.addWidget(self.result_label)

        row = QHBoxLayout()
        row.addStretch(1)
        self.toggle_button = QPushButton(self._action_text())
        self.toggle_button.clicked.connect(self._on_action)
        self.toggle_button.setEnabled(self._control.writable)
        self.toggle_button.setVisible(self._control.writable)
        row.addWidget(self.toggle_button)
        root.addLayout(row)

        self._paint()

    # -- the value this control would be staged to -------------------------

    def _is_numeric(self) -> bool:
        """A control whose value is a number, not an on/off.

        Decided from the reading when there is one and from `desired`
        otherwise, so a control that has not been read yet still offers the
        right action.
        """
        for value in (self._reading, self._control.desired):
            if value is self._UNREAD or value is None:
                continue
            return not isinstance(value, bool)
        return False

    def _target(self) -> Any:
        """What a click would stage, or None if a click cannot mean anything.

        A numeric control has exactly one sensible target: what the catalog
        recommends. A boolean one flips -- and if it could not be read, it
        goes to `desired`, or to True when the catalog has no opinion either.
        """
        if not self._control.writable:
            return None
        if self._is_numeric():
            return self._control.desired
        if isinstance(self._reading, bool):
            return not self._reading
        if isinstance(self._control.desired, bool):
            return self._control.desired
        return True

    def _action_text(self) -> str:
        target = self._target()
        if target is None:
            return "Not changeable"
        if self._is_numeric():
            return f"Set to {target}"
        return "Turn off" if target is False else "Turn on"

    # -- rendering ---------------------------------------------------------

    def _reading_text(self) -> str:
        if self._reading is self._UNREAD:
            return "—"
        if self._reading is None:
            return "Unknown"
        if isinstance(self._reading, bool):
            return "On" if self._reading else "Off"
        return str(self._reading)

    def _reading_meaning(self) -> Optional[str]:
        """Which semantic colour the badge takes, or None for no opinion.

        Keyed off `desired`, never off the reader's own colour: a control the
        catalog has no opinion about is not a problem whatever it reads, and
        one that is away from what the catalog wants is not a success however
        truthy its value.
        """
        if self._reading is self._UNREAD:
            return None
        if self._reading is None:
            return "info"
        if self._control.desired is None:
            return None
        return "success" if self._reading == self._control.desired else "warning"

    def _paint(self) -> None:
        self.status_badge.setText(self._reading_text())
        meaning = self._reading_meaning()
        self.status_badge.setStyleSheet(
            f"font-weight: bold; padding: 2px 8px; color: {semantic(meaning)};"
            if meaning else "font-weight: bold; padding: 2px 8px;")
        if self._control.writable:
            self.toggle_button.setText(self._action_text())
            self.toggle_button.setEnabled(self._target() is not None)

    # -- the pane's side of it ---------------------------------------------

    def set_reading(self, value: Any) -> None:
        self._reading = value
        self._paint()

    def set_staged(self, to_value: Any) -> None:
        self._staged_to = to_value
        shown = ("Unknown" if to_value is None else
                 ("On" if to_value is True else
                  "Off" if to_value is False else str(to_value)))
        self.staged_label.setText(f"Staged: will be {shown}")
        self.staged_label.setStyleSheet(f"color: {semantic('info')};")

    def clear_staged(self) -> None:
        self._staged_to = self._UNREAD
        self.staged_label.setText("")
        self.staged_label.setStyleSheet("")

    def set_result(self, result) -> None:
        """Render what came back from `apply_batch` or `revert_batch`.

        The reason is a Windows command's own complaint and runs to a dozen
        lines of PowerShell error formatting -- measured, on a refused
        Set-MpPreference. The first line goes on the card; the whole thing is
        the tooltip, because it is the evidence.
        """
        state = result.state
        first_line = (result.reason or "").strip().splitlines()
        first_line = first_line[0] if first_line else ""

        if state is ControlState.APPLIED_VERIFIED:
            text, meaning = "Applied, and the machine confirms it", "success"
        elif state is ControlState.APPLIED_PENDING_REBOOT:
            text, meaning = ("Applied — takes effect after a restart, so it "
                             "cannot be checked yet"), "info"
        elif state is ControlState.APPLIED_UNVERIFIED:
            text = (f"Not confirmed: asked for {result.requested!r}, the "
                    f"machine still reads {result.observed!r}")
            meaning = "warning"
        else:
            text, meaning = f"Refused: {first_line}", "error"

        self.result_label.setText(text)
        self.result_label.setToolTip(result.reason or "")
        self.result_label.setStyleSheet(f"color: {semantic(meaning)};")
        self.result_label.setVisible(True)
        self.clear_staged()
        # `observed` is a reading taken after the write, so it is now the
        # freshest thing anyone has about this control -- fresher than the
        # badge, which still shows what the pane read before the batch ran.
        # Rendered without it, a card that applied False sits there saying
        # "On" beside "Applied, and the machine confirms it". Found by
        # rendering the card and looking at it; no assertion here caught it.
        self.set_reading(result.observed)

    def clear_result(self) -> None:
        self.result_label.setText("")
        self.result_label.setVisible(False)

    def _on_action(self) -> None:
        target = self._target()
        if target is None:
            return
        self.staged.emit(self._control.id, target)


# ── Reusable Toggle Card ───────────────────────────────────────────────────



# ── Read-Only Status Card ──────────────────────────────────────────────────

class _StatusCard(QFrame):
    """Read-only coloured status card with title, status badge, and detail rows."""

    def __init__(self, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)

        self._title_lbl = QLabel(title)
        font = self._title_lbl.font()
        font.setBold(True)
        pt = font.pointSize()
        if pt > 0:
            font.setPointSize(pt + 1)
        self._title_lbl.setFont(font)
        layout.addWidget(self._title_lbl)

        self._status_lbl = QLabel("Loading...")
        self._status_lbl.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(self._status_lbl)

        self._details_layout = QVBoxLayout()
        layout.addLayout(self._details_layout)
        layout.addStretch()

    _MIN_HEIGHT = 120

    def minimumSizeHint(self):
        """At least 120px, and never less than the contents need.

        This was `setMinimumHeight(120)`, but an EXPLICIT minimum replaces the
        one Qt derives from the layout, so a card with three detail rows (BitLocker,
        HVCI) could not grow past 120 and its rows were squashed until half
        their text was cut off."""
        hint = super().minimumSizeHint()
        return QSize(hint.width(), max(self._MIN_HEIGHT, hint.height()))

    def update_status(self, data: dict):
        color = COLOR_MAP.get(data.get("color", "amber"), "#888")
        status = data.get("status", "Unknown")
        self._status_lbl.setText(status)
        self._status_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {color};")
        self.setStyleSheet(f"QFrame {{ border-left: 4px solid {color}; }}")

        # Clear and rebuild detail rows
        while self._details_layout.count():
            item = self._details_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for k, v in data.get("details", []):
            row = QHBoxLayout()
            k_lbl = QLabel(f"{k}:")
            k_lbl.setStyleSheet("color: gray;")
            v_lbl = QLabel(str(v))
            row.addWidget(k_lbl)
            row.addStretch()
            row.addWidget(v_lbl)
            container = QWidget()
            container.setLayout(row)
            self._details_layout.addWidget(container)
        self.updateGeometry()          # the rows changed, so the needed height did


# ── Main Module ────────────────────────────────────────────────────────────



# --- launching the elevated child ------------------------------------------

class _ShellExecuteInfoW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_SEE_MASK_NOASYNC = 0x00000100
_SW_HIDE = 0

#: The user said No to the UAC prompt. Far and away the commonest reason a
#: launch does not happen, and NOT an error — reporting it as one teaches
#: people to distrust the pane's other messages.
ERROR_CANCELLED = 1223

#: Bound with use_last_error so ctypes actually captures GetLastError after
#: the call. Reading `ctypes.get_last_error()` after a `ctypes.windll.*`
#: call returns ctypes' private copy, which that path never populates — so
#: the code logged was zero or a leftover from something unrelated, and a
#: declined prompt was indistinguishable from a real failure.
#: dashboard/procengine/actions.py has always done it this way.
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def run_elevated_batch(changes, timeout_ms: int = 15 * 60 * 1000):
    """Run one batch through the elevated helper and read its report.

    `ShellExecuteExW` rather than `ShellExecuteW`, for one reason:
    SEE_MASK_NOCLOSEPROCESS hands back a process handle to wait on.
    ShellExecuteW returns nothing waitable, so the only way to tell the child
    had finished would be to poll for the result file -- which cannot
    distinguish "still working" from "died before writing anything", and this
    project has already been bitten by exactly that shape of guess.

    Returns a BatchResult, or None when the helper reported nothing at all.
    None means UNKNOWN: the caller re-reads and shows what the machine says.
    """
    folder = tempfile.mkdtemp(prefix="security-batch-")
    try:
        return _run_elevated_batch_in(folder, changes, timeout_ms)
    finally:
        # batch.json holds the staged changes AND the previous value of
        # each one, so leaving it behind is both litter and a small
        # disclosure. Every apply used to leave one in %TEMP% permanently.
        shutil.rmtree(folder, ignore_errors=True)


def _run_elevated_batch_in(folder: str, changes, timeout_ms: int):
    """The body of run_elevated_batch, with the staging folder supplied."""
    batch_path = os.path.join(folder, "batch.json")
    result_path = os.path.join(folder, "result.json")
    write_batch_file(changes, batch_path)
    executable, arguments = build_elevated_command(batch_path, result_path)

    info = _ShellExecuteInfoW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS | _SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = executable
    info.lpParameters = arguments
    info.nShow = _SW_HIDE

    if not _shell32.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == ERROR_CANCELLED:
            # Declining the prompt is a decision, not a failure.
            logger.info("the elevated helper was cancelled at the UAC prompt")
        else:
            logger.warning(
                "the elevated helper was not started (error %s)", code)
        return None

    try:
        _kernel32.WaitForSingleObject(info.hProcess, timeout_ms)
    finally:
        _kernel32.CloseHandle(info.hProcess)
    # Read before the caller's finally removes the folder.
    return read_result_file(result_path)


class PendingBar(QFrame):
    """What is staged, and the two buttons that act on it.

    Hidden until something is staged, so an untouched pane carries no chrome
    it does not need.
    """

    apply_requested = pyqtSignal()
    discard_requested = pyqtSignal()
    review_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # A bar added to a QVBoxLayout with no stretch factor and nothing
        # Expanding takes an equal share of the surplus height -- the d57cdf2
        # trap. Fixed vertically: it is a bar, not a pane.
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        self.summary_label = QLabel("")
        row.addWidget(self.summary_label, 1)

        self.review_button = QPushButton("Review…")
        self.review_button.clicked.connect(self.review_requested)
        self.discard_button = QPushButton("Discard")
        self.discard_button.clicked.connect(self.discard_requested)
        self.apply_button = QPushButton("Apply")
        self.apply_button.clicked.connect(self.apply_requested)
        for button in (self.review_button, self.discard_button,
                       self.apply_button):
            row.addWidget(button)
        self.hide()

    def set_changeset(self, changeset) -> None:
        count = len(changeset)
        if not count:
            self.hide()
            return
        parts = [f"{count} change{'s' if count != 1 else ''} staged"]
        if changeset.unread_before:
            parts.append(f"{len(changeset.unread_before)} could not be read "
                         "beforehand")
        if changeset.one_way_changes:
            parts.append(f"{len(changeset.one_way_changes)} cannot be undone")
        if changeset.needs_reboot:
            parts.append("a restart is needed")
        self.summary_label.setText(" — ".join(parts))
        self.show()


class ReviewDialog(QDialog):
    """Exactly what will be written, before anything is.

    The dialog shows the literal steps, because "apply 60 changes" is not
    something anyone can consent to. It also separates three things that a
    single count hides, all of which are real on this machine: changes that
    differ from what you want (46 of a 60-change baseline) from ones whose
    current value could not be read at all (the other 14); and the steps this
    tool cannot undo (19 of the 60 -- the four threat actions plus fifteen
    command steps, for which BackupService records nothing).
    """

    def __init__(self, changeset, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Review changes")
        self.resize(760, 520)
        self._changeset = changeset

        layout = QVBoxLayout(self)
        heading = QLabel(self._heading_text())
        heading.setWordWrap(True)
        layout.addWidget(heading)

        self._details = QPlainTextEdit(self.details_text())
        self._details.setReadOnly(True)
        layout.addWidget(self._details, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.apply_button = QPushButton("Apply these changes")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)

    def _heading_text(self) -> str:
        """The totals, at the TOP.

        A full baseline here is sixty changes: whoever is about to approve it
        should not have to scroll to the end of the list to find out that
        nineteen of them cannot be undone and some need a restart. The
        per-change lines below still say which.
        """
        count = len(self._changeset)
        parts = [f"{count} change{'s' if count != 1 else ''} will be applied "
                 "in one batch, with a restore point taken first."]
        unread = len(self._changeset.unread_before)
        one_way = len(self._changeset.one_way_changes)
        if unread:
            parts.append(f"{unread} of them could not be read beforehand, so "
                         "there is no record of what they were.")
        if one_way:
            parts.append(f"{one_way} cannot be undone by this tool.")
        if self._changeset.needs_reboot:
            parts.append("Some need a restart before they take effect.")
        return " ".join(parts)

    def details_text(self) -> str:
        lines: List[str] = []
        unread = {c.control_id for c in self._changeset.unread_before}
        one_way = {c.control_id for c in self._changeset.one_way_changes}
        for change in self._changeset.changes:
            control = change.control
            lines.append(f"{control.title}  [{control.id}]")
            lines.append(f"    {change.from_value!r}  ->  {change.to_value!r}")
            if change.control_id in unread:
                lines.append("    its current value could not be read, so "
                             "this is applied without knowing what it was")
            if change.control_id in one_way:
                lines.append("    cannot be undone by this tool: nothing "
                             "records what to put back")
            if control.requires_reboot:
                lines.append("    takes effect after a restart")
            for step in change.resolved_steps():
                lines.append(f"    {_step_text(step)}")
            lines.append("")
        if self._changeset.needs_reboot:
            lines.append("Some of these only take effect once you restart the "
                         "machine.")
        return "\n".join(lines)


#: What each state is called in front of a person. `applied_pending_reboot`
#: is a value in an enum, not a sentence, and the report is read by whoever
#: just pressed Apply.
_STATE_LABELS = {
    ControlState.APPLIED_VERIFIED: "applied, and the machine confirms it",
    ControlState.APPLIED_PENDING_REBOOT: "applied, awaiting a restart",
    ControlState.APPLIED_UNVERIFIED: "not confirmed",
    ControlState.REFUSED: "refused",
}


class ResultDialog(QDialog):
    """What the machine said afterwards -- which is not what was asked for.

    A batch of twelve where nine landed is not a batch of twelve. The counts
    lead with what was VERIFIED, and a refusal keeps its full text while
    leading with one line: a refused Set-MpPreference is a dozen lines of
    PowerShell error formatting, measured on this machine, and one of those
    fills the dialog.
    """

    def __init__(self, result, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("What changed")
        self.resize(760, 520)
        self._result = result

        layout = QVBoxLayout(self)
        summary = QLabel(self.summary_text())
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self._details = QPlainTextEdit(self.details_text())
        self._details.setReadOnly(True)
        layout.addWidget(self._details, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        if self.reboot_prompts():
            self.restart_note = QLabel(
                f"{self._pending_reboot_count()} change(s) take effect after a "
                "restart.")
            row.insertWidget(0, self.restart_note)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

    def _pending_reboot_count(self) -> int:
        return sum(1 for r in self._result.results
                   if r.state is ControlState.APPLIED_PENDING_REBOOT)

    def reboot_prompts(self) -> int:
        """One prompt for the batch, never one per control."""
        return 1 if self._pending_reboot_count() else 0

    def summary_lines(self) -> List[str]:
        results = self._result.results
        verified = sum(1 for r in results
                       if r.state is ControlState.APPLIED_VERIFIED)
        lines = [f"{verified} of {len(results)} verified"]
        if self._result.error:
            lines.insert(0, f"The batch could not be run: {self._result.error}")
        for state, label in (
                (ControlState.APPLIED_PENDING_REBOOT, "awaiting a restart"),
                (ControlState.APPLIED_UNVERIFIED, "not confirmed"),
                (ControlState.REFUSED, "refused")):
            count = sum(1 for r in results if r.state is state)
            if count:
                lines.append(f"{count} {label}")
        for r in results:
            if r.state is ControlState.REFUSED and r.reason:
                lines.append(r.reason.strip().splitlines()[0])
                break
        return lines

    def summary_text(self) -> str:
        return "  •  ".join(self.summary_lines())

    def details_text(self) -> str:
        lines: List[str] = []
        for r in self._result.results:
            lines.append(f"{r.control_id}: {_STATE_LABELS[r.state]}")
            lines.append(f"    asked for {r.requested!r}, "
                         f"the machine reads {r.observed!r}")
            if r.reason:
                for line in r.reason.strip().splitlines():
                    lines.append(f"    {line}")
            lines.append("")
        if self._result.rp_id:
            lines.append(f"Restore point: {self._result.rp_id}")
        if self._result.windows_restore_point:
            lines.append("A Windows restore point was taken as well.")
        return "\n".join(lines)


def _step_text(step: Dict) -> str:
    """One line naming what a step actually writes.

    "Apply 60 changes" is not something anyone can consent to; the registry
    value, the command, the service name is.
    """
    kind = step.get("type")
    if kind in ("registry", "registry_delete"):
        value = step.get("value") or "(default)"
        data = step.get("data")
        target = f"{step.get('key')}\\{value}"
        return (f"delete {target}" if kind == "registry_delete"
                else f"set {target} = {data!r}")
    if kind == "service":
        return f"service {step.get('name')} -> {step.get('start_type')}"
    if kind in ("command", "script"):
        return f"run {step.get('cmd') or step.get('command')}"
    if kind == "scheduled_task":
        return f"disable scheduled task {step.get('task_name')}"
    if kind == "appx":
        return f"remove package {step.get('package')}"
    return f"{kind}: {step}"


class _CategoryTab(QWidget):
    """One `Category`, drawn as a scrolling column of `ControlCard`s.

    Holds no reading logic of its own -- the pane reads, and hands the values
    down. What it does own is the "as of" line, because a pane that shows a
    value without saying when it read it invites someone to trust a number
    from twenty minutes ago.
    """

    def __init__(self, category, controls, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.category = category
        self.controls = sorted(controls, key=lambda c: c.title.lower())
        self.cards: Dict[str, ControlCard] = {}
        self.built = False
        self.loaded = False
        self.reading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self._status = QLabel("Not read yet")
        self._count = QLabel("")
        header.addWidget(self._status, 1)
        header.addWidget(self._count)
        root.addLayout(header)

        # The page is permanent and empty; the cards go into it on first show.
        # Never removeTab/insertTab to swap in a lazily built widget -- doing
        # that on the current index re-fires currentChanged and re-enters the
        # handler that asked for the build.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._inner = QWidget()
        self._column = QVBoxLayout(self._inner)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._scroll.setWidget(self._inner)
        root.addWidget(self._scroll, 1)

    def build_cards(self, on_staged) -> None:
        """Create this tab's cards. Once, and only when it is shown.

        Building all seven tabs up front costs **2.99s** on this machine for
        149 cards -- a three-second freeze the moment someone clicks Security
        Dashboard in the sidebar, before a single value has been read. Measured
        with tools/security_pane_timing.py, not guessed.
        """
        if self.built:
            return
        self.built = True
        for control in self.controls:
            card = ControlCard(control)
            card.staged.connect(on_staged)
            self.cards[control.id] = card
            self._column.addWidget(card)
        # Without this the cards share the surplus height equally and every
        # one of them stretches -- the d57cdf2 layout trap. The stretch takes
        # the leftover space instead.
        self._column.addStretch(1)

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def set_match_count(self, shown: int, total: int) -> None:
        self._count.setText("" if shown == total else f"{shown} of {total}")


class SecurityDashboardModule(BaseModule):
    name = "Security Dashboard"
    icon = "\U0001f6e1\ufe0f"
    description = "Windows security status overview with controls"
    #: NOT admin-gated. `ModuleRegistry.start_all()` refuses to start a
    #: module with this set, and that took the whole pane away from an
    #: ordinary user -- while 112 of its 149 controls read fine unelevated,
    #: and `_on_apply` has always handed writes to `run_elevated_batch()` for
    #: exactly this case. Reads are best-effort and say what they could not
    #: see; writes ask for elevation when somebody actually applies something.
    requires_admin = False
    group = ModuleGroup.SYSTEM

    def __init__(self):
        super().__init__()
        self._workers: list = []
        self._overview_worker = None
        self._loaded_overview = False
        #: The optional_features snapshot is an 8s DISM enumeration;
        #: it is warmed once, when the module is first opened.
        self._snapshot_prefetch_started = False
        self._loaded_events = False
        #: The catalog, and the pane's own copy of what it last read. Both
        #: are what staging and the filters work off, so neither the machine
        #: nor a snapshot is consulted again to answer "is this a problem".
        self.catalog: Dict[str, Any] = {}
        self._readings: Dict[str, Any] = {}
        self._changeset = ChangeSet()
        self._category_tabs: Dict[str, Any] = {}
        self._tab_names: Dict[int, str] = {}
        self._applying = False
        self._widget: Optional[QWidget] = None
        self._history_rows: List[Any] = []
        self._last_baseline_plan: Optional[dict] = None
        self._events_progress: Optional[QProgressBar] = None
        self._events_table: Optional[QTableWidget] = None

    def get_refresh_interval(self) -> Optional[int]:
        """No timer. Refresh is a button.

        The Overview sweep took 37.3s against a 30s timer, so the timer
        relaunched an unfinished sweep for as long as the tab stayed
        open. Seven category tabs over 149 controls is that shape again,
        with more ways to lose. Nothing here refreshes unless someone
        asks it to.
        """
        return None

    def get_search_provider(self) -> Optional[SearchProvider]:
        return SecuritySearchProvider()

    def refresh_data(self) -> None:
        self._refresh_overview()

    def on_start(self, app) -> None:
        self.app = app
        self.catalog = load_catalog()

    # ── Widget Creation ───────────────────────────────────────────────────

    def create_widget(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)

        # Error banner
        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        # Header
        header_row = QHBoxLayout()
        self._banner = QLabel("Security Status")
        font = self._banner.font()
        font.setBold(True)
        pt = font.pointSize()
        if pt > 0:
            font.setPointSize(pt + 2)
        self._banner.setFont(font)
        baseline_btn = QPushButton("Baselines…")
        baseline_btn.setToolTip(
            "Stage the difference between this machine and a baseline. "
            "Nothing is written until you press Apply.")
        baseline_btn.setMenu(self._build_baseline_menu())
        profile_btn = QPushButton("Profile…")
        profile_btn.setMenu(self._build_profile_menu())
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._manual_refresh)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        header_row.addWidget(self._banner, 1)
        header_row.addWidget(baseline_btn)
        header_row.addWidget(profile_btn)
        header_row.addWidget(refresh_btn)
        layout.addLayout(header_row)
        layout.addWidget(self._progress)

        # Said once, up front. Per-control "Requires administrator" is true
        # but scattered over seven tabs, and someone reading a partial view
        # should be told it is partial before they trust it.
        note = self._elevation_note()
        if note:
            self._elevation_label = QLabel(note)
            self._elevation_label.setWordWrap(True)
            self._elevation_label.setStyleSheet(
                f"color: {semantic('warning')};")
            layout.addWidget(self._elevation_label)

        layout.addWidget(self._build_filter_bar())

        # One tab per catalog category, plus Overview and the event log.
        # "Controls" and "Advanced" are gone: they were a grab bag of twenty
        # hand-wired cards, and which of the two a setting landed in was a
        # matter of when it was added.
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_overview_tab(), "Overview")
        for category in Category:
            # "&" is a keyboard mnemonic to Qt, so "Device & Boot" renders as
            # "Device  Boot" with the B underlined -- four of the seven tab
            # names carry one. Escaped for display; the index-to-name map
            # below is what the code looks names up by, because tabText()
            # hands back the escaped string.
            index = self._tabs.addTab(self._build_category_tab(category),
                                      category.value.replace("&", "&&"))
            self._tab_names[index] = category.value
        self._tabs.addTab(self._build_history_tab(), "History")
        self._tabs.addTab(self._build_events_tab(), "Security Events")
        # Connected AFTER the addTab loop: addTab fires currentChanged
        # synchronously for the first tab added, which would kick off a read
        # during create_widget.
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs, 1)

        # No stretch factor: the bar is Fixed vertically, so the tabs above
        # keep the surplus height (the d57cdf2 trap is a widget added here
        # that quietly takes an equal share of it).
        self._pending = PendingBar()
        self._pending.review_requested.connect(self._on_review_requested)
        self._pending.apply_requested.connect(self._on_apply_requested)
        self._pending.discard_requested.connect(self._on_discard_requested)
        layout.addWidget(self._pending)

        self._widget = w
        return w

    # ── Tab 1: Overview ───────────────────────────────────────────────────

    def _build_overview_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)

        # Core security grid (2x2)
        core_label = QLabel("Core Security")
        core_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #b0b0b0;")
        layout.addWidget(core_label)

        grid = QGridLayout()
        grid.setSpacing(12)
        self._defender_card = _StatusCard("\U0001f6e1 Windows Defender")
        self._firewall_card = _StatusCard("\U0001f525 Firewall")
        self._bitlocker_card = _StatusCard("\U0001f4be BitLocker")
        self._boot_card = _StatusCard("\U0001f510 Secure Boot & TPM")
        grid.addWidget(self._defender_card, 0, 0)
        grid.addWidget(self._firewall_card, 0, 1)
        grid.addWidget(self._bitlocker_card, 1, 0)
        grid.addWidget(self._boot_card, 1, 1)
        layout.addLayout(grid)

        # Additional checks grid
        adv_label = QLabel("Additional Protections")
        adv_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #b0b0b0;")
        layout.addWidget(adv_label)

        grid2 = QGridLayout()
        grid2.setSpacing(12)
        self._uac_card = _StatusCard("\U0001f6e1 UAC")
        self._smartscreen_card = _StatusCard("\U0001f4e7 SmartScreen")
        self._hvci_card = _StatusCard("\U0001f512 HVCI / Memory Integrity")
        self._credguard_card = _StatusCard("\U0001f6f0 Credential Guard")
        self._lsass_card = _StatusCard("\U0001f512 LSASS Protection")
        self._rdp_card = _StatusCard("\U0001f310 Remote Desktop")
        self._smbv1_card = _StatusCard("\U0001f5c4 SMBv1")
        self._tamper_card = _StatusCard("\U0001f512 Tamper Protection")
        self._applocker_card = _StatusCard("\U0001f4dc AppLocker / WDAC")
        self._winhello_card = _StatusCard("\U0001f440 Windows Hello")
        grid2.addWidget(self._uac_card, 0, 0)
        grid2.addWidget(self._smartscreen_card, 0, 1)
        grid2.addWidget(self._hvci_card, 1, 0)
        grid2.addWidget(self._credguard_card, 1, 1)
        grid2.addWidget(self._lsass_card, 2, 0)
        grid2.addWidget(self._rdp_card, 2, 1)
        grid2.addWidget(self._applocker_card, 3, 0)
        grid2.addWidget(self._winhello_card, 3, 1)
        grid2.addWidget(self._smbv1_card, 4, 0)
        grid2.addWidget(self._tamper_card, 4, 1)
        layout.addLayout(grid2)

        layout.addStretch()
        scroll.setWidget(tab)
        return scroll

    def _refresh_overview(self):
        """Load overview data in background.

        One sweep at a time. The auto-refresh timer fires every 30s and the
        sweep is not guaranteed to be quicker than that on every machine;
        without this guard each tick stacked another COMWorker on the global
        QThreadPool and another entry on `self._workers`, neither of which was
        ever removed.
        """
        if self._overview_worker is not None:
            logger.debug("overview sweep already in flight — skipping refresh")
            return

        self._progress.show()

        worker = COMWorker(lambda _w: get_overview_status())
        self._overview_worker = worker
        self._workers.append(worker)

        def on_finished():
            self._overview_worker = None
            try:
                self._workers.remove(worker)
            except ValueError:
                logger.debug("on_finished: giving up on this read", exc_info=True)
                pass   # cancel_all_workers() cleared the list first

        worker.signals.finished.connect(on_finished)

        def on_result(data: dict):
            if not _widget_valid(self._banner):
                return
            self._progress.hide()

            # Core cards
            self._defender_card.update_status(data["defender"])
            self._firewall_card.update_status(data["firewall"])
            self._bitlocker_card.update_status(data["bitlocker"])
            self._boot_card.update_status(data["secure_boot_tpm"])

            # Additional cards
            self._uac_card.update_status(data["uac"])
            self._smartscreen_card.update_status(data["smartscreen"])
            self._hvci_card.update_status(data["hvci"])
            self._credguard_card.update_status(data["credential_guard"])
            self._lsass_card.update_status(data["lsass_protection"])
            self._tamper_card.update_status(data["tamper_protection"])
            self._rdp_card.update_status(data["rdp"])
            self._smbv1_card.update_status(data["smbv1"])
            self._applocker_card.update_status(data.get("applocker", {}))
            self._winhello_card.update_status(data.get("windows_hello", {}))

            # Overall banner
            all_data = [data["defender"], data["firewall"],
                        data["bitlocker"], data["secure_boot_tpm"]]
            colors = [d.get("color", "amber") for d in all_data]
            if all(c == "green" for c in colors):
                overall, col = "Secure", "#27AE60"
            elif any(c == "red" for c in colors):
                overall, col = "Issues Detected", "#E74C3C"
            else:
                overall, col = "Warnings", "#E67E22"
            self._banner.setText(f"Security Status — {overall}")
            self._banner.setStyleSheet(f"font-weight: bold; color: {col};")

        def on_error(err):
            if not _widget_valid(self._banner):
                return
            self._progress.hide()
            emsg = str(err[1]) if isinstance(err, tuple) else str(err)
            self._error_banner.set_error(f"Failed to load security status: {emsg}")
            self._error_banner.show()
            self._banner.setText("Security Status — Error")

        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)

        QThreadPool.globalInstance().start(worker)

    # ── Tab 2: Controls ───────────────────────────────────────────────────




    # ── Tab 3: Advanced ────────────────────────────────────────────────────



    # ── Tab 4: CVEs ───────────────────────────────────────────────────────



    # ── Tab 5: Security Events ────────────────────────────────────────────

    def _build_events_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 4, 4, 4)

        events_group = QGroupBox("Recent Security Events")
        events_layout = QVBoxLayout(events_group)
        self._events_progress = QProgressBar()
        self._events_progress.setFixedHeight(3)
        self._events_progress.setRange(0, 0)
        self._events_progress.hide()
        events_layout.addWidget(self._events_progress)

        self._events_table = QTableWidget()
        self._events_table.setColumnCount(4)
        self._events_table.setHorizontalHeaderLabels(
            ["Time", "Event ID", "Description", "Logon Info"])
        fit_table(self._events_table, stretch=[2], content=[0, 1, 3])
        self._events_table.setAlternatingRowColors(True)
        self._events_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._events_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._events_table.setStyleSheet("""
            QTableWidget { background: #2d2d2d;
                           border: 1px solid #3c3c3c; border-radius: 4px; }
            QTableWidget::item { padding: 4px; }
            QTableWidget::item:selected { background: #094771; }
            QHeaderView::section { background: #3c3c3c; color: #b0b0b0;
                                   padding: 4px; border: none; }
        """)
        events_layout.addWidget(self._events_table)

        # Empty state
        self._events_empty = EmptyState(
            "🛡️", "No recent security events",
            "Nothing found in the Security log yet. Refresh to check again.",
            "Refresh",
        )
        self._events_empty.action_triggered.connect(self._load_events)
        events_layout.addWidget(self._events_empty)
        self._events_empty.hide()

        layout.addWidget(events_group, 1)
        return tab

    def _load_events(self):
        """Load security events in background."""
        if not _widget_valid(self._events_progress):
            return
        self._events_progress.show()

        worker = Worker(lambda _w: get_security_events(30))
        self._workers.append(worker)

        def on_result(events: list):
            if not _widget_valid(self._events_table):
                return
            self._events_progress.hide()
            self._update_events_table(events)

        def on_error(err):
            if not _widget_valid(self._events_table):
                return
            self._events_progress.hide()
            emsg = str(err[1]) if isinstance(err, tuple) else str(err)
            self._error_banner.set_error(f"Failed to load events: {emsg}")
            self._error_banner.show()

        worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error)

        QThreadPool.globalInstance().start(worker)

    def _update_events_table(self, events: list):
        if not _widget_valid(self._events_table):
            return
        self._events_table.setRowCount(0)
        if not events:
            self._events_table.hide()
            self._events_empty.show()
            return
        self._events_table.show()
        self._events_empty.hide()

        for ev in events:
            row = self._events_table.rowCount()
            self._events_table.insertRow(row)
            self._events_table.setItem(row, 0, centered_item(ev.get("time", "")))

            eid = ev.get("event_id", "")
            eid_item = centered_item(eid)
            eid_color = "#E74C3C" if eid == "1102" else "#e0e0e0"
            eid_item.setForeground(QColor(eid_color))
            self._events_table.setItem(row, 1, eid_item)

            self._events_table.setItem(row, 2, centered_item(
                ev.get("description", "")))

            # Logon type
            msg = ev.get("message", "")
            logon_info = ""
            m = re.search(r"LogonType:\s*(\d+)", msg)
            if m:
                logon_type = int(m.group(1))
                lt_map = {2: "Interactive", 3: "Network", 4: "Batch",
                          5: "Service", 7: "Unlock", 8: "Network Cleartext",
                          9: "New Credentials", 10: "Remote Interactive",
                          11: "Cached Interactive"}
                logon_info = f"LogonType {m.group(1)} ({lt_map.get(logon_type, '')})"
            self._events_table.setItem(row, 3, centered_item(logon_info))

    # ── Quick Actions ─────────────────────────────────────────────────────

    def _do_quick_scan(self):
        self._action_status_lbl.setText("Quick scan running...")
        self._action_status_lbl.setStyleSheet("color: #E67E22; font-size: 12px;")

        def do_scan(worker):
            return run_quick_scan()

        def on_result(res):
            if not _widget_valid(self._action_status_lbl):
                return
            success = res.get("success", False)
            msg = res.get("message", "")
            if success:
                self._action_status_lbl.setText(f"Scan completed: {msg}")
                self._action_status_lbl.setStyleSheet("color: #27AE60; font-size: 12px;")
            else:
                self._action_status_lbl.setText(f"Scan finished: {msg[:150]}")
                self._action_status_lbl.setStyleSheet("color: #E67E22; font-size: 12px;")

        w = Worker(do_scan)
        w.signals.result.connect(on_result)
        w.signals.error.connect(
            lambda _: _on_label_error(self._action_status_lbl, "Error running scan."))
        self._workers.append(w)

        QThreadPool.globalInstance().start(w)

    def _do_update_defs(self):
        self._action_status_lbl.setText("Updating definitions...")
        self._action_status_lbl.setStyleSheet("color: #E67E22; font-size: 12px;")

        def do_update(worker):
            return run_update_definitions()

        def on_result(res):
            if not _widget_valid(self._action_status_lbl):
                return
            success = res.get("success", False)
            msg = res.get("message", "")
            if success:
                self._action_status_lbl.setText(f"Definitions updated: {msg}")
                self._action_status_lbl.setStyleSheet("color: #27AE60; font-size: 12px;")
                # Refresh signature state
                if self._loaded_controls:
                    self._refresh_controls()
            else:
                self._action_status_lbl.setText(f"Update: {msg[:150]}")
                self._action_status_lbl.setStyleSheet("color: #E67E22; font-size: 12px;")

        w = Worker(do_update)
        w.signals.result.connect(on_result)
        w.signals.error.connect(
            lambda _: _on_label_error(self._action_status_lbl, "Error updating definitions."))
        self._workers.append(w)

        QThreadPool.globalInstance().start(w)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def _start_snapshot_prefetch(self, run=None) -> None:
        """Build the expensive optional_features snapshot off the tab reads.

        `Get-WindowsOptionalFeature -Online` is a DISM enumeration of every
        feature on the machine: 8.10s elevated, measured. Two tabs need it
        (Windows Features, and `telnet_client` on Firewall & Network), so
        whichever control asks first pays for it and the rest are free — which
        put ~8s on a tab nobody would guess owns it.

        `snapshots._cached` locks per name, so a tab that asks while this is
        still running waits for THIS build instead of starting a second one.
        Started when somebody opens the module, never at app launch: a user
        who never visits this pane should not pay for a DISM enumeration.
        """
        if self._snapshot_prefetch_started:
            return
        self._snapshot_prefetch_started = True

        def warm() -> None:
            try:
                snapshots.optional_features()
            except Exception:
                # An optimisation that failed. The tab that needs the list
                # reports its own reading, exactly as it did before.
                logger.debug("optional_features prefetch failed",
                             exc_info=True)

        if run is None:
            def run(fn):
                threading.Thread(target=fn, daemon=True,
                                 name="security-snapshot-prefetch").start()
        run(warm)

    def on_activate(self) -> None:
        self._start_snapshot_prefetch()
        if not self._loaded_overview:
            self._loaded_overview = True
            self._refresh_overview()
        if not self._loaded_events:
            self._loaded_events = True
            self._load_events()

    def _on_tab_changed(self, index: int):
        """Read a category tab the first time it is actually shown.

        Not on build: seven tabs read at construction is the whole
        catalog, 15.8s of it, to draw one visible pane.
        """
        if self._tabs.tabText(index) == "History":
            self._load_history()
            return
        tab = self._category_tabs.get(self._tab_names.get(index))
        if tab is not None:
            self._read_category(tab)

    def _manual_refresh(self):
        """Refresh: drop the caches, then re-read what is on screen.

        The caches are dropped FIRST -- 135 of the 149 readers answer out
        of them and they have no expiry, so a Refresh that skipped it would
        redraw exactly the same numbers and look broken -- but that happens
        inside the read job, not here. `snapshots.invalidate()` takes every
        per-name lock and waits for any fetch already running, which is up to
        30s per snapshot. Only the visible tab is re-read; the others reread
        when they are next shown.
        """
        self._error_banner.clear()
        self._error_banner.hide()
        self._refresh_overview()
        for tab in self._category_tabs.values():
            tab.loaded = False
        current = self._category_tabs.get(
            self._tab_names.get(self._tabs.currentIndex()))
        if current is not None:
            self._read_category(current, force=True, invalidate_first=True)
        if self._loaded_events:
            self._load_events()

    def on_deactivate(self) -> None:
        self.cancel_all_workers()

    def on_stop(self) -> None:
        self.cancel_all_workers()



    # ── Catalog-driven category tabs ──────────────────────────────────────

    def _build_filter_bar(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)

        self._filter_box = QLineEdit()
        self._filter_box.setPlaceholderText(
            "Filter controls — matches the name, the description and why it "
            "matters")
        self._filter_box.setClearButtonEnabled(True)
        self._filter_box.textChanged.connect(self._apply_filter)
        row.addWidget(self._filter_box, 1)

        self._only_problems = QCheckBox("Only problems")
        self._only_problems.setToolTip(
            "Controls whose reading differs from what the catalog recommends. "
            "A control the catalog has no opinion about is never a problem, "
            "and neither is one that could not be read.")
        self._only_changed = QCheckBox("Only staged")
        self._only_actionable = QCheckBox("Only changeable")
        for box in (self._only_problems, self._only_changed,
                    self._only_actionable):
            box.stateChanged.connect(self._apply_filter)
            row.addWidget(box)
        return bar

    def _build_category_tab(self, category) -> QWidget:
        tab = _CategoryTab(category, [c for c in self.catalog.values()
                                      if c.category is category])
        self._category_tabs[category.value] = tab
        return tab

    def show_category_tab(self, name: str) -> None:
        """Select a category tab and make sure it has been read."""
        for index, tab_name in self._tab_names.items():
            if tab_name == name:
                self._tabs.setCurrentIndex(index)
                break
        tab = self._category_tabs.get(name)
        if tab is not None:
            self._read_category(tab)

    def _dispatch(self, worker) -> None:
        """Where a Worker goes. Overridden in tests, which have no pool."""
        QThreadPool.globalInstance().start(worker)

    def _read_category(self, tab, force: bool = False,
                       invalidate_first: bool = False) -> None:
        """Read this tab's controls, once, off the UI thread.

        A tab reads only its OWN controls: the whole catalog is 15.8s warm
        (31.4s elevated), and 135 of the 149 controls are free -- the cost is
        twelve readers that each launch their own process. Reading everything
        because one tab was opened is the Overview 37.3s defect at scale.

        `tab.reading` is the same guard `test_security_overview_inflight.py`
        pins for Overview: a click on a tab whose read is still in flight must
        not start a second one.
        """
        tab.build_cards(self._on_card_staged)
        if tab.reading or (tab.loaded and not force):
            return
        tab.reading = True
        tab.set_status("Reading…")
        controls = list(tab.controls)

        def job(worker):
            if invalidate_first:
                # Inside the job, never on the UI thread: invalidate() takes
                # every per-name snapshot lock, so it blocks until any fetch
                # already in flight has finished -- up to the 30s timeout, per
                # snapshot. Measured at 189s in one suite run with several
                # fetches outstanding. Refresh must not be able to freeze the
                # window for that long.
                snapshots.invalidate()
            readings = {}
            for control in controls:
                if worker.is_cancelled:
                    return readings
                readings[control.id] = control.read()
            return readings

        # COMWorker, not Worker: these readers reach WMI (BitLocker, TPM,
        # encryptable volumes) and COMWorker is what calls CoInitialize on the
        # pool thread. Unelevated the two are indistinguishable -- every WMI
        # call here is refused, so nothing exercises COM at all, which is
        # exactly why the wrong one would survive testing on this machine.
        worker = COMWorker(job)
        worker.signals.result.connect(
            lambda readings, t=tab: self._on_category_read(t, readings))
        worker.signals.error.connect(
            lambda err, t=tab: self._on_category_error(t, err))
        worker.signals.finished.connect(
            lambda t=tab: self._on_category_finished(t))
        self._workers.append(worker)
        self._dispatch(worker)

    def _on_category_read(self, tab, readings: Dict[str, Any]) -> None:
        if not _widget_valid(tab):
            return
        self._readings.update(readings)
        for control_id, value in readings.items():
            card = tab.cards.get(control_id)
            if card is not None:
                card.set_reading(value)
        tab.loaded = True
        unread = sum(1 for v in readings.values() if v is None)
        stamp = datetime.now().strftime("%H:%M:%S")
        tab.set_status(
            f"Read at {stamp}"
            + (f" — {unread} could not be read" if unread else ""))
        self._apply_filter()

    def _on_category_error(self, err, tab) -> None:
        logger.error("category read failed: %s", err)
        if _widget_valid(tab):
            tab.set_status("The read failed — see the log")

    def _on_category_finished(self, tab) -> None:
        tab.reading = False
        self._workers = [w for w in self._workers if not w.is_finished()] \
            if hasattr(Worker, "is_finished") else self._workers[-1:]

    # ── Filtering ─────────────────────────────────────────────────────────

    def filter_controls(self, text: str = "", only_problems: bool = False,
                        only_changed: bool = False,
                        only_actionable: bool = False) -> List[Any]:
        """The catalog entries a filter would leave visible.

        `only_problems` keys off `desired`, NEVER off the reader's own colour
        (Ruling 6): 14 controls have no `desired` and their readers
        legitimately paint them amber or red, and a control that could not be
        READ is not a control that is wrong.
        """
        needle = (text or "").strip().lower()
        hits = []
        for control in self.catalog.values():
            if needle:
                haystack = (f"{control.title} {control.description} "
                            f"{control.why_it_matters} {control.id}").lower()
                if needle not in haystack:
                    continue
            if only_actionable and not control.writable:
                continue
            if only_changed and control.id not in self._changeset:
                continue
            if only_problems:
                reading = self._readings.get(control.id)
                if (control.desired is None or reading is None
                        or reading == control.desired):
                    continue
            hits.append(control)
        return hits

    def _apply_filter(self) -> None:
        if not self._category_tabs:
            return
        visible = {c.id for c in self.filter_controls(
            self._filter_box.text(),
            only_problems=self._only_problems.isChecked(),
            only_changed=self._only_changed.isChecked(),
            only_actionable=self._only_actionable.isChecked())}
        for tab in self._category_tabs.values():
            shown = 0
            for control_id, card in tab.cards.items():
                on = control_id in visible
                card.setVisible(on)
                shown += 1 if on else 0
            tab.set_match_count(shown, len(tab.cards))

    # ── Staging ───────────────────────────────────────────────────────────

    @property
    def changeset(self):
        return self._changeset

    def _on_card_staged(self, control_id: str, to_value: Any) -> None:
        """A card was clicked. Nothing is written; the change is staged.

        The reading the pane already holds is passed as `from_value`, so
        staging does not read the machine again -- a full baseline diff is
        12.68s without it and 0.001s with it.
        """
        control = self.catalog.get(control_id)
        if control is None:
            return
        try:
            if control_id in self._readings:
                self._changeset.add(control, to_value,
                                    from_value=self._readings[control_id])
            else:
                # Never `add(control, to_value)` here: with no from_value
                # ChangeSet reads the machine, and this runs on the UI
                # thread -- bitlocker_encryption_detail alone is 5.4s of
                # frozen window. A control the pane has not read is
                # honestly staged as unread, which is what
                # ChangeSet.unread_before exists to report.
                logger.warning("staging %s with no reading of its own",
                               control_id)
                self._changeset.add(control, to_value, from_value=None)
        except ValueError as exc:      # read-only, or staged to no value
            logger.warning("cannot stage %s: %s", control_id, exc)
            return
        card = self._card_for(control_id)
        if card is not None:
            if control_id in self._changeset:
                card.set_staged(to_value)
            else:
                card.clear_staged()     # staged back to what it already is
        self._on_changeset_changed()

    def _card_for(self, control_id: str):
        for tab in self._category_tabs.values():
            card = tab.cards.get(control_id)
            if card is not None:
                return card
        return None

    def _on_changeset_changed(self) -> None:
        if getattr(self, "_pending", None) is not None:
            self._pending.set_changeset(self._changeset)
        if self._only_changed.isChecked():
            self._apply_filter()


    # ── Applying ──────────────────────────────────────────────────────────

    def _on_review_requested(self) -> None:
        if self._ask_review(self._changeset):
            self._on_apply_requested()

    # The two modal moments are their own methods so a test can stand in for
    # them. A .exec() called straight from a handler blocks the event loop
    # forever under pytest -- it took a five-minute timeout to notice.
    def _ask_review(self, changeset) -> bool:
        return bool(ReviewDialog(changeset, self._widget).exec())

    def _show_result_dialog(self, result) -> None:
        ResultDialog(result, self._widget).exec()

    def _on_discard_requested(self) -> None:
        for change in self._changeset.changes:
            card = self._card_for(change.control_id)
            if card is not None:
                card.clear_staged()
        self._changeset.clear()
        self._on_changeset_changed()

    def _on_apply_requested(self) -> None:
        """Apply the staged batch, elevated if it needs to be.

        One UAC prompt for the batch, not one per control -- and when the app
        is already elevated there is no prompt at all. Either way the work is
        off the UI thread and the machine is re-read afterwards; a writer that
        returned is not evidence anything changed.
        """
        if self._applying or not len(self._changeset):
            return
        self._applying = True
        self._progress.show()
        self._pending.apply_button.setEnabled(False)

        changes = changes_of(self._changeset)

        def job(_worker):
            return self._elevated_or_in_process(changes)

        worker = COMWorker(job)
        worker.signals.result.connect(self._on_batch_result)
        worker.signals.error.connect(self._on_batch_error)
        worker.signals.finished.connect(self._on_batch_finished)
        self._workers.append(worker)
        self._dispatch(worker)

    def _elevated_or_in_process(self, changes):
        """Write here if we can, otherwise ask for one UAC prompt.

        Reached unelevated for the first time now the pane is no longer
        admin-gated: `run_elevated_batch` re-runs the batch elevated and
        reports back through a file.
        """
        if is_admin():
            return self._apply_in_process()
        return run_elevated_batch(changes)

    def _elevation_note(self) -> str:
        """What to say up front about running without administrator rights.

        Empty when elevated. Per-control "Requires administrator" is true but
        scattered across seven tabs; this says once that the view is partial
        and that applying will prompt.
        """
        if is_admin():
            return ""
        return ("Running without administrator rights — some settings cannot "
                "be read, and are shown as unknown rather than guessed. "
                "Applying a change will ask for elevation.")

    def _apply_in_process(self):
        from core.system_restore import create_restore_point
        from modules.tweaks.tweak_engine import TweakEngine
        backup = self.app.backup if self.app is not None else None
        if backup is None:
            raise RuntimeError("no backup service: refusing to write without "
                               "a way back")
        return apply_batch(self._changeset, TweakEngine(backup), backup,
                           create_windows_restore_point=create_restore_point)

    def _on_batch_result(self, result) -> None:
        if result is None:
            # The elevated helper wrote no usable result. That is UNKNOWN, and
            # the only honest thing to do is re-read and show what the machine
            # says now -- never "applied", never "failed".
            self._error_banner.set_error(
                "The elevated helper did not report back. Nothing here knows "
                "what it did; the tab has been re-read so the values below "
                "are current.")
            self._error_banner.show()
            self._manual_refresh()
            return
        for control_result in result.results:
            card = self._card_for(control_result.control_id)
            if card is not None:
                card.set_result(control_result)
            self._readings[control_result.control_id] = control_result.observed
        self._changeset.clear()
        self._on_changeset_changed()
        self._show_result_dialog(result)

    def _on_batch_error(self, err) -> None:
        logger.error("the batch failed: %s", err)
        self._error_banner.set_error(f"The batch could not be run: {err}")
        self._error_banner.show()

    def _on_batch_finished(self) -> None:
        self._applying = False
        self._progress.hide()
        self._pending.apply_button.setEnabled(True)


    # ── History, baselines and profiles ───────────────────────────────────

    def _build_baseline_menu(self) -> QMenu:
        menu = QMenu()
        self._baseline_menu = menu       # held: a QMenu with no parent is collected
        for name in available_baselines():
            action = menu.addAction(name.capitalize())
            action.triggered.connect(
                lambda _checked=False, n=name: self._on_baseline_requested(n))
        if not available_baselines():
            menu.addAction("No baselines are installed").setEnabled(False)
        return menu

    def _build_profile_menu(self) -> QMenu:
        menu = QMenu()
        self._profile_menu = menu
        menu.addAction("Export this machine…").triggered.connect(
            self._on_export_profile)
        menu.addAction("Import and stage…").triggered.connect(
            self._on_import_profile)
        return menu

    def _on_export_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self._widget, "Export this machine's security profile",
            "security-profile.json", "JSON (*.json)")
        if path:
            data = self.export_profile_to(path)
            self._banner.setText(
                f"Exported {len(data['controls'])} control(s); "
                f"{len(data['unreadable'])} could not be read")

    def _on_import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self._widget, "Import a security profile", "", "JSON (*.json)")
        if path:
            staged = self.import_profile_from(path)
            if staged is not None:
                self._banner.setText(f"{staged} change(s) staged from profile")


    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)

        row = QHBoxLayout()
        self._history_status = QLabel("Not loaded yet")
        reload_button = QPushButton("Reload")
        reload_button.clicked.connect(self._load_history)
        self._revert_button = QPushButton("Revert the selected batch")
        self._revert_button.setEnabled(False)
        self._revert_button.clicked.connect(self._on_revert_requested)
        row.addWidget(self._history_status, 1)
        row.addWidget(reload_button)
        row.addWidget(self._revert_button)
        column.addLayout(row)

        self._history_table = QTableWidget(0, 4)
        self._history_table.setHorizontalHeaderLabels(
            ["When", "What", "Changes", "State"])
        fit_table(self._history_table, stretch=[1], content=[0, 2, 3])
        self._history_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._history_table.itemSelectionChanged.connect(
            self._on_history_selection)
        column.addWidget(self._history_table, 1)
        return page

    def history_rows(self) -> List[Any]:
        """This module's own restore points, newest first.

        Filtered by module: BackupService is shared with Tweaks, Debloat and
        the performance tuner, and offering to revert one of THEIR batches
        from this pane would be a surprise nobody asked for.
        """
        backup = getattr(self.app, "backup", None) if self.app else None
        if backup is None:
            return []
        try:
            points = backup.list_restore_points()
        except Exception:
            logger.warning("could not list restore points", exc_info=True)
            return []
        return [p for p in points if p.module == "Security Dashboard"]

    def _load_history(self) -> None:
        rows = self.history_rows()
        self._history_table.setRowCount(len(rows))
        for index, point in enumerate(rows):
            for column, text in enumerate((point.created_at, point.label,
                                           str(point.step_count),
                                           point.status)):
                self._history_table.setItem(index, column,
                                            centered_item(str(text)))
        _fit_columns(self._history_table)
        self._history_status.setText(
            f"{len(rows)} batch(es) applied from this pane"
            if rows else "This pane has not changed anything yet")
        self._history_rows = rows
        self._on_history_selection()

    def _on_history_selection(self) -> None:
        rows = getattr(self, "_history_rows", [])
        index = self._history_table.currentRow()
        self._revert_button.setEnabled(
            0 <= index < len(rows)
            and rows[index].status != "restored")

    def _on_revert_requested(self) -> None:
        rows = getattr(self, "_history_rows", [])
        index = self._history_table.currentRow()
        if not (0 <= index < len(rows)):
            return
        point = rows[index]
        backup = getattr(self.app, "backup", None) if self.app else None
        if backup is None:
            return

        def job(_worker):
            return revert_batch(point.id, backup, self.catalog)

        worker = COMWorker(job)
        worker.signals.result.connect(self._on_batch_result)
        worker.signals.error.connect(self._on_batch_error)
        worker.signals.finished.connect(self._load_history)
        self._workers.append(worker)
        self._dispatch(worker)

    # -- baselines ---------------------------------------------------------

    def plan_for_baseline(self, name: str) -> dict:
        """What a baseline would do, without doing any of it.

        The pane's own readings are passed in: without them this reads all 149
        controls, 12.7s, to answer a question about values it already has.
        """
        return plan_baseline(name, self.catalog, readings=self._readings)

    def _on_baseline_requested(self, name: str) -> None:
        plan = self.plan_for_baseline(name)
        self._changeset.clear()
        for change in plan["staged"].changes:
            self._changeset.add(change.control, change.to_value,
                                from_value=change.from_value)
            card = self._card_for(change.control_id)
            if card is not None:
                card.set_staged(change.to_value)
        self._on_changeset_changed()
        self._last_baseline_plan = plan

    def export_profile_to(self, path: str) -> dict:
        data = export_profile(self.catalog, readings=self._readings)
        write_profile(data, path)
        return data

    def import_profile_from(self, path: str) -> Optional[int]:
        """Stage a profile from disk. None means the file was not one."""
        data = read_profile(path)
        if data is None:
            self._error_banner.set_error(
                f"{os.path.basename(path)} is not a profile this app wrote.")
            self._error_banner.show()
            return None
        staged = import_profile(data, self.catalog, readings=self._readings)
        self._changeset.clear()
        for change in staged.changes:
            self._changeset.add(change.control, change.to_value,
                                from_value=change.from_value)
            card = self._card_for(change.control_id)
            if card is not None:
                card.set_staged(change.to_value)
        self._on_changeset_changed()
        return len(self._changeset)



def _fit_columns(table, padding: int = 24, cap: int = 520) -> None:
    """Size every column to the widest thing actually in it.

    A column's default width is a guess until it has met the real data: the
    Firewall table's guessed defaults clipped 393 of 544 real rule names and
    rendered every program path as the useless "C:...". Measured with
    fontMetrics().horizontalAdvance, capped so one long label cannot push the
    rest off the pane, and left Interactive -- QHeaderView.Fixed refuses a
    user's drag SILENTLY.
    """
    # Interactive FIRST. A section in Stretch or ResizeToContents mode
    # silently ignores setColumnWidth — it computes its own — so setting the
    # widths and then switching modes threw every width away. The history
    # table is built with `fit_table(..., stretch=[1], content=[0, 2, 3])`,
    # so that was all four columns: the Label column came out at whatever
    # share of the pane the stretch gave it (224px) rather than the 444px
    # its content needed.
    table.horizontalHeader().setSectionResizeMode(
        QHeaderView.ResizeMode.Interactive)

    metrics = table.fontMetrics()
    for column in range(table.columnCount()):
        header_item = table.horizontalHeaderItem(column)
        widest = metrics.horizontalAdvance(
            header_item.text() if header_item else "")
        for row in range(table.rowCount()):
            item = table.item(row, column)
            if item is not None:
                widest = max(widest, metrics.horizontalAdvance(item.text()))
        table.setColumnWidth(column, min(widest + padding, cap))

def _on_label_error(label, message: str):
    """Helper: show error on a QLabel if it still exists."""
    if _widget_valid(label):
        label.setText(message)
        label.setStyleSheet("color: #E74C3C; font-size: 12px;")


# ── Search Provider ────────────────────────────────────────────────────────

class SecuritySearchProvider(SearchProvider):
    """Search across security dashboard checks.

    Returns the current security posture so users can search for
    terms like "defender", "firewall", "bitlocker", etc.
    """

    module_name = "Security Dashboard"

    def search(self, query: SearchQuery) -> list:
        if not query.text:
            return []
        q = query.text.lower()
        results = []

        try:
            from modules.security_dashboard.security_reader import (
                check_defender, check_firewall, check_bitlocker,
                check_secure_boot_tpm, check_uac, check_smartscreen,
                check_hvci, check_credential_guard, check_lsass_protection,
                check_tamper_protection, check_pua_protection,
                check_controlled_folder_access, check_cloud_protection,
                check_network_protection_defender, check_rdp, check_smbv1,
            )
            checks = {
                "defender": ("Windows Defender", check_defender),
                "firewall": ("Windows Firewall", check_firewall),
                "bitlocker": ("BitLocker Drive Encryption", check_bitlocker),
                "secure boot": ("Secure Boot & TPM", check_secure_boot_tpm),
                "tpm": ("TPM Module", check_secure_boot_tpm),
                "uac": ("User Account Control", check_uac),
                "smartscreen": ("SmartScreen Filter", check_smartscreen),
                "hvci": ("HVCI / Memory Integrity", check_hvci),
                "memory integrity": ("Memory Integrity", check_hvci),
                "credential guard": ("Credential Guard", check_credential_guard),
                "lsass": ("LSASS Protection", check_lsass_protection),
                "tamper": ("Tamper Protection", check_tamper_protection),
                "pua": ("PUA Protection", check_pua_protection),
                "controlled folder": ("Controlled Folder Access",
                                       check_controlled_folder_access),
                "ransomware": ("Controlled Folder Access",
                               check_controlled_folder_access),
                "cloud": ("Cloud-Delivered Protection", check_cloud_protection),
                "network protection": ("Network Protection",
                                       check_network_protection_defender),
                "rdp": ("Remote Desktop", check_rdp),
                "remote desktop": ("Remote Desktop", check_rdp),
                "smbv1": ("SMBv1 Protocol", check_smbv1),
                "smb": ("SMBv1 Protocol", check_smbv1),
            }
            from datetime import datetime
            for term, (label, fn) in checks.items():
                if term in q or any(w in q for w in label.lower().split()):
                    try:
                        data = fn()
                        status = data.get("status", "Unknown")
                        color = data.get("color", "amber")
                        results.append(SearchResult(
                            timestamp=datetime.now(),
                            source="Security Dashboard",
                            type=f"Security Check ({color})",
                            summary=f"{label}: {status}",
                            detail=data.get("details", []),
                            relevance=0.9,
                        ))
                    except Exception:
                        logger.warning("Security search failed for '%s'", label, exc_info=True)
        except Exception:
            logger.warning("Security search provider failed", exc_info=True)
        return results[:20]

    def get_filterable_fields(self) -> list:
        return [
            FilterField("severity", "Status", ["green", "amber", "red"]),
        ]

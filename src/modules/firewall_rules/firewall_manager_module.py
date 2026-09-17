"""Firewall Rules Manager — view and manage Windows Firewall rules via netsh."""

import logging
import ntpath
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import QEvent, QObject, Qt, QThreadPool
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QLabel, QProgressBar, QLineEdit,
    QComboBox, QMessageBox, QInputDialog, QFileDialog,
)

from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.module_groups import ModuleGroup
from core.semantic_colors import semantic
from core.table_ui import centered_item, center_header
from core.worker import Worker
from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)


@dataclass
class FirewallRule:
    name: str
    enabled: str
    direction: str
    action: str
    protocol: str
    local_port: str
    remote_port: str
    program: str
    profile: str


_COLS = [
    "Name", "Enabled", "Direction", "Action",
    "Protocol", "Local Port", "Remote Port", "Program", "Profile",
]
# Widths measured against a real 544-rule dump of this machine at 9pt, not
# guessed: at the old 200/150/80 the Name column clipped 393 rows, Program
# clipped 309 and rendered every path as the useless "C:...", and Profile --
# the narrowest content in the table -- was the stretch section soaking up
# 750px of empty space. These are scaled by the zoom factor, so they are the
# widths at _BASE_FONT_PT, not absolutes.
_COL_WIDTHS = [330, 70, 80, 70, 80, 110, 110, 380, 150]

# Font zoom. The pane starts at the application font size and the user can
# move it; _BASE_FONT_PT is filled in from the running app at build time.
_MIN_FONT_PT = 6
_MAX_FONT_PT = 24
_FONT_CFG_KEY = "firewall.font_point_size"

# No column may swallow the pane on "Fit Columns": one WindowsApps path is
# 645px wide at 9pt and would push Profile off-screen on its own.
_FIT_MAX_WIDTH = 520
# Slack added around a measured text width, so a value does not sit flush
# against the column edge. The same number tests/test_firewall_table_fit.py
# uses in its _needed() helper — they have to agree, or the guard is testing
# a different rule from the one the pane implements.
_FIT_PADDING = 12

# The widest value each column must be able to show, measured live against
# whatever font this display actually renders. Only columns with a definite
# answer appear here.
#
# Indices 1-4 and 8 have a bounded vocabulary: netsh emits exactly these
# words, so "wide enough for the longest of them" is a complete requirement.
# Index 7 (Program) is unbounded, but an ordinary system32 path is the case
# it must not clip — a WindowsApps path is allowed to elide, and every cell
# carries its full value as a tooltip.
#
# Index 0 (Name) is deliberately absent. Sizing it to its widest real value
# puts a 544-rule table at _FIT_MAX_WIDTH on the first row that runs long.
_COL_EXEMPLARS = {
    1: "Enabled",                        # Yes / No, header is the widest
    2: "Direction",                      # In / Out
    3: "Action",                         # Allow / Block
    4: "ICMPv6",                         # TCP / UDP / ICMPv4 / ICMPv6 / Any
    7: r"C:\WINDOWS\system32\CastSrv.exe",
    8: "Domain,Private,Public",          # every profile at once
}


class _CtrlWheelZoom(QObject):
    """Turns Ctrl+wheel over the table into a font-size nudge.

    Lives in its own QObject because BaseModule is a plain ABC and cannot be
    installed as an event filter.
    """

    def __init__(self, nudge: Callable[[int], None], parent=None) -> None:
        super().__init__(parent)
        self._nudge = nudge

    def eventFilter(self, obj, event) -> bool:
        if (event.type() == QEvent.Type.Wheel
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            delta = event.angleDelta().y()
            if delta:
                self._nudge(1 if delta > 0 else -1)
            return True  # swallow it so the table does not also scroll
        return False


# ------------------------------------------------------------------
# netsh data fetching / manipulation
# ------------------------------------------------------------------

# netsh's field names are not the ones you would guess, and every mismatch
# leaves a column silently blank for all ~550 rules rather than erroring:
#   * the plain `show rule` output has NO Program line at all -- it appears
#     only under `verbose`, which is why the Program column and the
#     search-by-program filter had never matched anything;
#   * the names are `Profiles`, `LocalPort`, `RemotePort` -- not the spaced
#     forms. Both spellings are accepted here in case a locale or an older
#     Windows emits the other one.
# Verified against a real 544-rule dump from this machine (0.7s, 540 KB).
_FIELD_MAP = {
    "Rule Name": "name",
    "Enabled": "enabled",
    "Direction": "direction",
    "Action": "action",
    "Protocol": "protocol",
    "LocalPort": "local_port",
    "Local Port": "local_port",
    "RemotePort": "remote_port",
    "Remote Port": "remote_port",
    "Program": "program",
    "Profiles": "profile",
    "Profile": "profile",
}

_FIELD_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(k) for k in _FIELD_MAP) + r"):\s*(.*)$"
)


def fetch_firewall_rules() -> List[FirewallRule]:
    """Parse 'netsh advfirewall firewall show rule name=all verbose'.

    `verbose` is required, not cosmetic: without it netsh omits the Program
    line entirely. It costs nothing measurable (~0.7s for 544 rules here).
    """
    proc = subprocess.run(
        ["netsh", "advfirewall", "firewall", "show", "rule", "name=all", "verbose"],
        capture_output=True, text=True, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    raw = proc.stdout
    return _parse_rules(raw)


def _parse_rules(raw: str) -> List[FirewallRule]:
    """Split netsh output into FirewallRule dataclass instances."""
    rules: List[FirewallRule] = []
    # Rules are separated by blank lines; each block starts with Rule Name:
    blocks = re.split(r"\n\s*\n", raw)

    for block in blocks:
        data: dict = {}
        for line in block.split("\n"):
            if not line.strip():
                continue
            # Format: "  Field Name:        value"
            m = _FIELD_RE.match(line)
            if m:
                data[_FIELD_MAP[m.group(1)]] = m.group(2).strip()

        if data.get("name"):
            rules.append(FirewallRule(
                name=data.get("name", ""),
                enabled=data.get("enabled", ""),
                direction=data.get("direction", ""),
                action=data.get("action", ""),
                protocol=data.get("protocol", ""),
                local_port=data.get("local_port", ""),
                remote_port=data.get("remote_port", ""),
                program=data.get("program", ""),
                profile=data.get("profile", ""),
            ))
    return rules


# ------------------------------------------------------------------
# Addressing a rule for modification
# ------------------------------------------------------------------
#
# netsh CANNOT address every rule it lists. Store-app rules are named with an
# MRT indirect string -- `@{Package_1.2.3_x64__abc?ms-resource://Pkg/Res/Name}`
# -- and `netsh ... name=<that string>` answers "No rules match the specified
# criteria." So does the *resolved* form ("Windows Calculator"). 33 of the 355
# distinct rule names on this machine are like this, and double-clicking any of
# them used to raise CalledProcessError, surfacing as a bare
# "returned non-zero exit status 1".
#
# PowerShell's Get/Set/Remove-NetFirewallRule address them fine by DisplayName,
# which is exactly the resolved form -- so netsh stays the fast default and
# PowerShell is the fallback for what it cannot reach (~0.9s, only when needed).


def _mrt_lookup(indirect: str) -> str:
    """Resolve one MRT indirect string to its display name via SHLoadIndirectString.

    Returns "" when MRT cannot resolve it -- the referenced package is not
    installed on this machine. Kept separate from `resolve_display_name` so
    tests can pin resolution without loading system DLLs.
    """
    try:
        import ctypes
        from ctypes import wintypes

        fn = ctypes.WinDLL("shlwapi.dll").SHLoadIndirectString
        fn.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT,
                       ctypes.c_void_p]
        buf = ctypes.create_unicode_buffer(1024)
        if fn(indirect, buf, len(buf), None) == 0 and buf.value:
            return buf.value
    except Exception:
        logger.warning("Could not resolve indirect rule name %r", indirect,
                       exc_info=True)
    return ""


def resolve_display_name(name: str) -> str:
    """Resolve an `@{Package?ms-resource://...}` rule name to its display name.

    Returns the input unchanged for ordinary names, and also if resolution
    fails -- the caller is no worse off than before.
    """
    if not name.startswith("@{"):
        return name
    return _mrt_lookup(name) or name


def _powershell_firewall(cmdlet: str, display_name: str, extra: str = "") -> Tuple[bool, str]:
    """Run a *-NetFirewallRule cmdlet against a rule's DisplayName.

    The name is single-quote escaped rather than interpolated into a shell
    string -- rule names are Windows-supplied text, not something to trust.
    """
    escaped = display_name.replace("'", "''")
    command = "%s -DisplayName '%s'%s -ErrorAction Stop" % (
        cmdlet, escaped, (" " + extra) if extra else "")
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0 and "ErrorRecord" not in output:
        return True, output
    return False, output or "%s exited with code %d" % (cmdlet, proc.returncode)


def set_rule_enabled(name: str, enable: bool) -> Tuple[bool, str]:
    """Enable or disable a rule, reporting why rather than raising."""
    ok, message = _run_netsh(
        ["advfirewall", "firewall", "set", "rule",
         "name=%s" % name, "new", "enable=%s" % ("yes" if enable else "no")]
    )
    if ok:
        return True, message

    display = resolve_display_name(name)
    ok, ps_message = _powershell_firewall(
        "Set-NetFirewallRule", display,
        "-Enabled %s" % ("True" if enable else "False"))
    if ok:
        return True, ps_message
    return False, "netsh: %s\nPowerShell: %s" % (message, ps_message)


def delete_rule(name: str) -> Tuple[bool, str]:
    """Delete every rule with this name, reporting why rather than raising."""
    ok, message = _run_netsh(
        ["advfirewall", "firewall", "delete", "rule", "name=%s" % name]
    )
    if ok:
        return True, message

    display = resolve_display_name(name)
    ok, ps_message = _powershell_firewall("Remove-NetFirewallRule", display)
    if ok:
        return True, ps_message
    return False, "netsh: %s\nPowerShell: %s" % (message, ps_message)


def _netsh_or_raise(args: List[str], what: str) -> str:
    """Run netsh, and raise with ITS words when it refuses.

    check=True raises CalledProcessError, whose message is
    "returned non-zero exit status 1" -- while netsh has already written the
    reason ("The requested operation requires elevation.", "A rule with the
    same name already exists.") to stdout, where check=True discards it. These
    calls run inside a Worker whose error signal goes straight to the user, so
    the number was all anybody saw.
    """
    ok, output = _run_netsh(args)
    if ok:
        return output
    raise RuntimeError(f"{what}: {output}" if output else
                       f"{what}: netsh gave no reason")


def netsh_block_program(exe_path: str) -> None:
    rule_name = f"Block {Path(exe_path).stem}"
    _netsh_or_raise(
        ["advfirewall", "firewall", "add", "rule",
         f"name={rule_name}",
         "dir=out", "action=block",
         f"program={exe_path}", "enable=yes"],
        f"could not block {Path(exe_path).name}")


def netsh_open_port(port: int, direction: str = "in") -> None:
    name = f"Allow {direction.title()} Port {port}"
    _netsh_or_raise(
        ["advfirewall", "firewall", "add", "rule",
         f"name={name}",
         f"dir={direction}", "action=allow",
         f"localport={port}", "protocol=tcp", "enable=yes"],
        f"could not open port {port}")


def netsh_export_rules(path: str) -> None:
    _netsh_or_raise(["advfirewall", "export", f'"{path}"'],
                    "could not export the firewall rules")


def netsh_import_rules(path: str) -> None:
    _netsh_or_raise(["advfirewall", "import", f'"{path}"'],
                    "could not import the firewall rules")


# ------------------------------------------------------------------
# Unblocking — finding and removing *block* rules by program or folder
# ------------------------------------------------------------------
#
# Windows Firewall has no notion of a blocked folder: rules name an executable.
# "Unblock a folder" therefore means "remove every block rule whose program
# lives under this folder", which is what find_block_rules_in_folder does.

# What netsh writes in the Program column for a rule that is not scoped to an
# executable on disk. "System" is kernel-mode traffic (real rules use it — 144
# of the 544 on this machine), and it is a literal, not a path.
_ANY_PROGRAM = {"", "any", "system"}

# The show-rule output says In/Out; the delete verb wants in/out.
_DIRECTION_FLAGS = {"in": "in", "inbound": "in", "out": "out", "outbound": "out"}


def normalize_program_path(path: str) -> str:
    """Case-folded, env-expanded, separator-normalised path, for comparison only.

    netsh reports plenty of built-in rules with the variable unexpanded
    (%SystemRoot%\\system32\\svchost.exe), so a raw string compare against a
    path chosen in a file dialog silently misses them. ntpath is used
    explicitly rather than os.path so the comparison is Windows-shaped even
    when these functions are exercised off Windows.
    """
    if not path:
        return ""
    expanded = os.path.expandvars(path.strip().strip('"'))
    if not expanded:
        return ""
    return ntpath.normcase(ntpath.normpath(expanded))


def _is_program_scoped(rule: "FirewallRule") -> bool:
    return rule.program.strip().lower() not in _ANY_PROGRAM


def find_block_rules_for_program(rules: List["FirewallRule"],
                                 exe_path: str) -> List["FirewallRule"]:
    """Every Block rule pointing at exactly this executable, in either direction."""
    target = normalize_program_path(exe_path)
    if not target:
        return []
    return [
        r for r in rules
        if r.action == "Block"
        and _is_program_scoped(r)
        and normalize_program_path(r.program) == target
    ]


def find_block_rules_in_folder(rules: List["FirewallRule"],
                               folder: str) -> List["FirewallRule"]:
    """Every Block rule whose program lives anywhere under this folder."""
    root = normalize_program_path(folder)
    if not root:
        return []
    prefix = root.rstrip("\\") + "\\"
    matches = []
    for r in rules:
        if r.action != "Block" or not _is_program_scoped(r):
            continue
        if normalize_program_path(r.program).startswith(prefix):
            matches.append(r)
    return matches


def _run_netsh(args: List[str]) -> Tuple[bool, str]:
    """Run a netsh subcommand, returning (rc == 0, combined output).

    The caller must NOT treat the boolean as proof the rule is gone — netsh
    reports "No rules match the specified criteria." on stdout with a non-zero
    exit, and locale affects the wording. The real check is re-reading the
    rules afterwards, which is what the module does.
    """
    proc = subprocess.run(
        ["netsh"] + args,
        capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode == 0:
        return True, output
    return False, output or "netsh exited with code %d" % proc.returncode


def netsh_delete_matching_rule(rule: "FirewallRule") -> Tuple[bool, str]:
    """Delete one rule, narrowed by name + program + direction.

    Rule names are NOT unique in Windows Firewall — deleting by name alone
    takes every same-named rule with it, including ones for other programs.
    """
    args = ["advfirewall", "firewall", "delete", "rule", "name=%s" % rule.name]
    if _is_program_scoped(rule):
        args.append("program=%s" % rule.program)
    flag = _DIRECTION_FLAGS.get(rule.direction.strip().lower())
    if flag:
        args.append("dir=%s" % flag)
    return _run_netsh(args)


def unblock_rules(rules: List["FirewallRule"]) -> Tuple[int, List[str]]:
    """Delete each of the given rules. Returns (deleted_count, error lines).

    Keeps going after a failure so one protected rule does not abandon the
    rest, and collapses duplicates: a rule present on several profiles shows
    up once per profile but one netsh delete removes them all.
    """
    deleted = 0
    errors: List[str] = []
    seen = set()
    for r in rules:
        key = (r.name, r.program, r.direction)
        if key in seen:
            continue
        seen.add(key)
        ok, message = netsh_delete_matching_rule(r)
        if ok:
            deleted += 1
        else:
            errors.append("%s: %s" % (r.name, message))
    return deleted, errors


# ------------------------------------------------------------------
# Module
# ------------------------------------------------------------------

class FirewallManagerModule(BaseModule):
    name = "Firewall Rules"
    icon = "🛡️"
    description = "View and manage Windows Firewall rules"
    group = ModuleGroup.MANAGE
    requires_admin = True

    def create_widget(self) -> QWidget:
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(8, 8, 8, 8)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        # ---- Toolbar ----
        toolbar = QHBoxLayout()

        self._refresh_btn = QPushButton("Refresh")
        self._block_btn = QPushButton("Block Program")
        self._unblock_btn = QPushButton("Unblock Program")
        self._unblock_btn.setToolTip(
            "Remove every firewall block rule that targets a chosen executable"
        )
        self._unblock_folder_btn = QPushButton("Unblock Folder")
        self._unblock_folder_btn.setToolTip(
            "Remove every firewall block rule whose program lives under a chosen folder"
        )
        self._open_port_btn = QPushButton("Open Port")
        self._delete_btn = QPushButton("Delete Rule")
        self._export_btn = QPushButton("Export Rules")
        self._import_btn = QPushButton("Import Rules")
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Search name or program...")
        self._search_edit.setMaximumWidth(220)
        self._search_edit.setMinimumWidth(160)

        self._dir_combo = QComboBox()
        self._dir_combo.addItems(["All", "Inbound", "Outbound"])

        self._action_combo = QComboBox()
        self._action_combo.addItems(["All", "Allow", "Block"])

        self._profile_combo = QComboBox()
        self._profile_combo.addItems(["All", "Domain", "Private", "Public"])

        self._delete_btn.setEnabled(False)

        # Text size controls. Ctrl+wheel and Ctrl+/Ctrl- do the same thing,
        # but neither is discoverable, so the buttons carry the shortcuts in
        # their tooltips.
        self._zoom_out_btn = QPushButton("A-")
        self._zoom_out_btn.setToolTip("Smaller text (Ctrl+- or Ctrl+wheel down)")
        self._zoom_out_btn.setFixedWidth(32)
        self._zoom_in_btn = QPushButton("A+")
        self._zoom_in_btn.setToolTip("Larger text (Ctrl++ or Ctrl+wheel up)")
        self._zoom_in_btn.setFixedWidth(32)
        self._zoom_reset_btn = QPushButton("A")
        self._zoom_reset_btn.setToolTip("Reset text size (Ctrl+0)")
        self._zoom_reset_btn.setFixedWidth(28)
        self._fit_btn = QPushButton("Fit Columns")
        self._fit_btn.setToolTip(
            "Widen every column to its widest visible value (capped so one "
            "long path cannot push the rest off-screen)"
        )

        for btn in (self._refresh_btn, self._block_btn, self._unblock_btn,
                    self._unblock_folder_btn, self._open_port_btn,
                    self._delete_btn, self._export_btn, self._import_btn):
            toolbar.addWidget(btn)
        toolbar.addWidget(QLabel("Direction:"))
        toolbar.addWidget(self._dir_combo)
        toolbar.addWidget(QLabel("Action:"))
        toolbar.addWidget(self._action_combo)
        toolbar.addWidget(QLabel("Profile:"))
        toolbar.addWidget(self._profile_combo)
        toolbar.addWidget(self._search_edit)
        toolbar.addStretch()
        toolbar.addWidget(self._fit_btn)
        toolbar.addWidget(self._zoom_out_btn)
        toolbar.addWidget(self._zoom_reset_btn)
        toolbar.addWidget(self._zoom_in_btn)
        layout.addLayout(toolbar)

        # ---- Progress ----
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)

        # ---- Table ----
        self._table = QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.setSortingEnabled(True)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # A path elided on the right collapses to "C:..." and tells the user
        # nothing; elided in the middle it still shows the drive and the exe.
        self._table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._table.setWordWrap(False)

        header = self._table.horizontalHeader()
        # Interactive, not Fixed: Fixed silently refuses the drag, so there
        # was no way for the user to widen a column that did not fit.
        for i in range(len(_COLS)):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(24)
        center_header(self._table)

        layout.addWidget(self._table, 1)

        # ---- Status bar ----
        self._status_lbl = QLabel("Click Refresh to load firewall rules.")
        self._status_lbl.setStyleSheet("color: #888;")
        layout.addWidget(self._status_lbl)

        # ---- Text size ----
        self._base_font_pt = self._resolve_base_font_pt(outer)
        self._font_pt = self._load_saved_font_pt()
        self._apply_font()
        self._install_zoom_shortcuts(outer)
        # BaseModule is not a QObject, so the wheel filter needs its own.
        self._wheel_filter = _CtrlWheelZoom(self._nudge_font, outer)
        self._table.viewport().installEventFilter(self._wheel_filter)

        self._all_rules: List[FirewallRule] = []
        self._outer = outer

        # ---- Signal connections ----
        self._refresh_btn.clicked.connect(self._do_refresh)
        self._block_btn.clicked.connect(self._block_program)
        self._unblock_btn.clicked.connect(self._unblock_program)
        self._unblock_folder_btn.clicked.connect(self._unblock_folder)
        self._open_port_btn.clicked.connect(self._open_port)
        self._delete_btn.clicked.connect(self._delete_rule)
        self._export_btn.clicked.connect(self._export_rules)
        self._import_btn.clicked.connect(self._import_rules)
        self._search_edit.textChanged.connect(self._apply_filter)
        self._dir_combo.currentTextChanged.connect(self._apply_filter)
        self._action_combo.currentTextChanged.connect(self._apply_filter)
        self._profile_combo.currentTextChanged.connect(self._apply_filter)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(self._on_double_click)
        self._zoom_in_btn.clicked.connect(lambda: self._nudge_font(1))
        self._zoom_out_btn.clicked.connect(lambda: self._nudge_font(-1))
        self._zoom_reset_btn.clicked.connect(self._reset_font)
        self._fit_btn.clicked.connect(self._fit_columns)

        return outer

    # ------------------------------------------------------------------
    # Text size
    # ------------------------------------------------------------------

    def _resolve_base_font_pt(self, widget: QWidget) -> int:
        """The application's own point size, or 9 if Qt reports pixels."""
        for font in (widget.font(),
                     QApplication.instance().font() if QApplication.instance()
                     else widget.font()):
            pt = font.pointSize()
            if pt > 0:
                return pt
        return 9

    def _load_saved_font_pt(self) -> int:
        saved = None
        if self.app is not None:
            try:
                saved = self.app.config.get(_FONT_CFG_KEY, None)
            except Exception:
                logger.debug("Ignored config read failure", exc_info=True)
        try:
            pt = int(saved)
        except (TypeError, ValueError):
            pt = self._base_font_pt
        return max(_MIN_FONT_PT, min(_MAX_FONT_PT, pt))

    def _save_font_pt(self) -> None:
        if self.app is None:
            return
        try:
            self.app.config.set(_FONT_CFG_KEY, self._font_pt)
        except Exception:
            logger.debug("Ignored config write failure", exc_info=True)

    def _apply_font(self) -> None:
        """Push the current size into the table, its header and the status bar.

        Row height and column widths both move with it -- a bigger font in a
        fixed-height row just clips the descenders, and a bigger font in the
        old widths clips more text, not less.
        """
        font = self._table.font()
        font.setPointSize(self._font_pt)
        self._table.setFont(font)

        header_font = self._table.horizontalHeader().font()
        header_font.setPointSize(self._font_pt)
        self._table.horizontalHeader().setFont(header_font)
        self._table.verticalHeader().setFont(header_font)
        self._status_lbl.setFont(header_font)

        line = self._table.fontMetrics().height()
        self._table.verticalHeader().setDefaultSectionSize(line + 10)

        scale = self._font_pt / float(self._base_font_pt or 9)
        for i, width in enumerate(_COL_WIDTHS):
            # The tuned constant scaled by point size, but never narrower
            # than this display actually needs — see _measured_floor.
            self._table.setColumnWidth(
                i, max(24, int(round(width * scale)), self._measured_floor(i)))

    def _nudge_font(self, step: int) -> None:
        new_pt = max(_MIN_FONT_PT, min(_MAX_FONT_PT, self._font_pt + step))
        if new_pt == self._font_pt:
            return
        self._font_pt = new_pt
        self._apply_font()
        self._save_font_pt()
        self._status_lbl.setText("Text size: %dpt" % self._font_pt)

    def _reset_font(self) -> None:
        if self._font_pt == self._base_font_pt:
            return
        self._font_pt = self._base_font_pt
        self._apply_font()
        self._save_font_pt()
        self._status_lbl.setText("Text size reset to %dpt." % self._font_pt)

    def _install_zoom_shortcuts(self, widget: QWidget) -> None:
        for keys, slot in (
            (("Ctrl++", "Ctrl+="), lambda: self._nudge_font(1)),
            (("Ctrl+-",), lambda: self._nudge_font(-1)),
            (("Ctrl+0",), self._reset_font),
        ):
            for key in keys:
                shortcut = QShortcut(QKeySequence(key), widget)
                shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.activated.connect(slot)

    def _fit_columns(self) -> None:
        """Size every column to its widest visible value, capped."""
        self._table.resizeColumnsToContents()
        for i in range(self._table.columnCount()):
            self._table.setColumnWidth(
                i, min(self._table.columnWidth(i) + 12, _FIT_MAX_WIDTH))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _do_refresh(self) -> None:
        self._set_buttons_enabled(False)
        self._progress.show()
        self._status_lbl.setText("Loading firewall rules...")

        def work(_w):
            return fetch_firewall_rules()

        worker = Worker(work)
        worker.signals.result.connect(self._on_rules_loaded)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_rules_loaded(self, rules: List[FirewallRule]) -> None:
        self._all_rules = rules
        self._progress.hide()
        self._set_buttons_enabled(True)
        self._apply_filter()
        self._status_lbl.setText(f"{len(rules)} firewall rule(s) loaded.")

    def _on_error(self, err: str) -> None:
        self._progress.hide()
        self._set_buttons_enabled(True)
        self._status_lbl.setText(f"Error: {err}")
        self._error_banner.set_error(f"Failed to load rules: {err}")

    def _block_program(self) -> None:
        exe_path, _ = QFileDialog.getOpenFileName(
            self._outer, "Select Program to Block",
            "", "Executables (*.exe);;All Files (*)",
        )
        if not exe_path:
            return

        reply = QMessageBox.question(
            self._outer, "Confirm Block",
            f"Block outbound traffic for:\n{exe_path}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._progress.show()
        self._status_lbl.setText(f"Creating block rule for {Path(exe_path).stem}...")

        def work(_w):
            netsh_block_program(exe_path)
            return fetch_firewall_rules()

        worker = Worker(work)
        worker.signals.result.connect(self._on_rules_loaded)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _open_port(self) -> None:
        port, ok = QInputDialog.getInt(
            self._outer, "Open Port",
            "Port number (1-65535):", 80, 1, 65535,
        )
        if not ok:
            return

        reply = QMessageBox.question(
            self._outer, "Confirm Open Port",
            f"Allow inbound TCP traffic on port {port}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._progress.show()
        self._status_lbl.setText(f"Opening port {port}...")

        def work(_w):
            netsh_open_port(port)
            return fetch_firewall_rules()

        worker = Worker(work)
        worker.signals.result.connect(self._on_rules_loaded)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _unblock_program(self) -> None:
        exe_path, _ = QFileDialog.getOpenFileName(
            self._outer, "Select Program to Unblock",
            "", "Executables (*.exe);;All Files (*)",
        )
        if not exe_path:
            return
        self._start_unblock_scan(
            find_block_rules_for_program, exe_path, "program", Path(exe_path).name
        )

    def _unblock_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self._outer, "Select Folder to Unblock"
        )
        if not folder:
            return
        self._start_unblock_scan(
            find_block_rules_in_folder, folder, "folder", folder
        )

    def _start_unblock_scan(self, finder: Callable, target: str,
                            kind: str, label: str) -> None:
        """Re-read the live rules, then ask before deleting the ones that match.

        Rules are fetched fresh rather than filtered out of self._all_rules:
        that list may be stale or never loaded, and deleting off a stale list
        is how you delete a rule the user cannot see.
        """
        self._set_buttons_enabled(False)
        self._progress.show()
        self._status_lbl.setText("Looking for block rules for %s..." % label)

        def work(_w):
            return finder(fetch_firewall_rules(), target)

        def on_found(matches: List[FirewallRule]) -> None:
            self._progress.hide()
            self._set_buttons_enabled(True)

            if not matches:
                self._status_lbl.setText("No block rules found for %s." % label)
                QMessageBox.information(
                    self._outer, "Nothing to Unblock",
                    "No Windows Firewall block rule references this %s:\n%s"
                    % (kind, target),
                )
                return

            listing = "\n".join(
                "\u2022 %s  (%s \u2192 %s)" % (r.name, r.direction, r.program)
                for r in matches[:15]
            )
            if len(matches) > 15:
                listing += "\n\u2026 and %d more" % (len(matches) - 15)
            noun = "rule" if len(matches) == 1 else "rules"

            if not confirm_destructive(
                self._outer,
                "Unblock %s" % kind.title(),
                "Remove %d firewall block %s for this %s?"
                % (len(matches), noun, kind),
                detail=listing,
            ):
                self._status_lbl.setText("Unblock cancelled.")
                return

            self._start_unblock_delete(matches, finder, target, label)

        worker = Worker(work)
        worker.signals.result.connect(on_found)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _start_unblock_delete(self, matches: List[FirewallRule], finder: Callable,
                              target: str, label: str) -> None:
        self._set_buttons_enabled(False)
        self._progress.show()
        self._status_lbl.setText(
            "Removing %d block rule(s) for %s..." % (len(matches), label)
        )

        def work(_w):
            deleted, errors = unblock_rules(matches)
            # Ask Windows what is actually left rather than trusting netsh's
            # exit code — it reports "no rules match" as a failure and its
            # wording is localised.
            rules = fetch_firewall_rules()
            return deleted, errors, finder(rules, target), rules

        def on_done(result) -> None:
            deleted, errors, leftover, rules = result
            self._on_rules_loaded(rules)

            if leftover or errors:
                detail = "\n".join(errors[:10])
                QMessageBox.warning(
                    self._outer, "Unblock Incomplete",
                    "Removed %d rule(s), but %d block rule(s) still reference %s."
                    % (deleted, len(leftover), label)
                    + ("\n\n" + detail if detail else ""),
                )
                self._status_lbl.setText(
                    "Unblock incomplete \u2014 %d block rule(s) remain for %s."
                    % (len(leftover), label)
                )
            else:
                QMessageBox.information(
                    self._outer, "Unblocked",
                    "Removed %d firewall block rule(s) for:\n%s" % (deleted, target),
                )
                self._status_lbl.setText(
                    "Unblocked %s (%d rule(s) removed)." % (label, deleted)
                )

        worker = Worker(work)
        worker.signals.result.connect(on_done)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _delete_rule(self) -> None:
        name = self._get_selected_name()
        if not name:
            return

        reply = QMessageBox.warning(
            self._outer, "Delete Rule",
            f"Delete firewall rule:\n{name}?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._progress.show()
        self._status_lbl.setText(f"Deleting rule '{name}'...")

        def work(_w):
            ok, message = delete_rule(name)
            rules = fetch_firewall_rules()
            # Verify against Windows rather than trusting the exit code.
            gone = not any(r.name == name for r in rules)
            return ok and gone, message, rules

        def on_done(result):
            ok, message, rules = result
            self._on_rules_loaded(rules)
            if ok:
                self._status_lbl.setText(f"Deleted rule '{name}'.")
                return
            self._status_lbl.setText(f"Could not delete rule '{name}'.")
            QMessageBox.warning(
                self._outer, "Could Not Delete Rule",
                "Windows would not delete this rule:\n%s\n\n%s" % (name, message),
            )

        worker = Worker(work)
        worker.signals.result.connect(on_done)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _on_double_click(self, item: QTableWidgetItem) -> None:
        """Toggle enabled/disabled state on double-click."""
        row = item.row()
        name_item = self._table.item(row, 0)
        if not name_item:
            return
        name = name_item.text()
        enabled_item = self._table.item(row, 1)
        current_state = enabled_item.text() if enabled_item else ""

        # Infer desired state: if currently True (enabled), disable; else enable
        new_state = current_state != "Yes"

        wanted = "Yes" if new_state else "No"
        self._status_lbl.setText(
            f"Setting rule '{name}' to {'enabled' if new_state else 'disabled'}..."
        )

        def work(_w):
            ok, message = set_rule_enabled(name, new_state)
            rules = fetch_firewall_rules()
            # Don't trust the exit code -- read back what the rule now says.
            applied = any(r.name == name and r.enabled == wanted for r in rules)
            return ok and applied, message, rules

        def on_done(result):
            ok, message, rules = result
            self._on_rules_loaded(rules)
            if ok:
                self._status_lbl.setText(
                    f"Rule '{name}' is now {'enabled' if new_state else 'disabled'}."
                )
                return
            self._status_lbl.setText(f"Could not change rule '{name}'.")
            QMessageBox.warning(
                self._outer, "Could Not Change Rule",
                "Windows would not %s this rule:\n%s\n\n%s"
                % ("enable" if new_state else "disable", name, message),
            )

        worker = Worker(work)
        worker.signals.result.connect(on_done)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _export_rules(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self._outer, "Export Firewall Rules",
            "rules.wfw", "Windows Firewall Rules (*.wfw)",
        )
        if not path:
            return

        self._progress.show()
        self._status_lbl.setText("Exporting rules...")

        def work(_w):
            netsh_export_rules(path)
            return fetch_firewall_rules()

        worker = Worker(work)
        worker.signals.result.connect(self._on_rules_loaded)
        worker.signals.error.connect(self._on_error)
        QMessageBox.information(
            self._outer, "Export Complete",
            f"Rules exported to:\n{path}",
        )

        def on_export_result(rules):
            self._progress.hide()
            self._status_lbl.setText(f"Rules exported successfully ({len(rules)} rules loaded).")

        worker.signals.result.connect(on_export_result)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    def _import_rules(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self._outer, "Import Firewall Rules",
            "", "Windows Firewall Rules (*.wfw);;All Files (*)",
        )
        if not path:
            return

        reply = QMessageBox.question(
            self._outer, "Confirm Import",
            f"Import firewall rules from:\n{path}\n\nExisting rules with the same names will be overwritten.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._progress.show()
        self._status_lbl.setText("Importing rules...")

        def work(_w):
            netsh_import_rules(path)
            return fetch_firewall_rules()

        worker = Worker(work)
        worker.signals.result.connect(self._on_rules_loaded)
        worker.signals.error.connect(self._on_error)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)

    # ------------------------------------------------------------------
    # Filtering / table population
    # ------------------------------------------------------------------

    def _apply_filter(self) -> None:
        search = self._search_edit.text().lower()
        dir_f = self._dir_combo.currentText()
        action_f = self._action_combo.currentText()
        profile_f = self._profile_combo.currentText()

        visible = []
        for r in self._all_rules:
            if search and search not in r.name.lower() and search not in r.program.lower():
                continue
            if dir_f != "All" and r.direction != dir_f:
                continue
            if action_f != "All" and r.action != action_f:
                continue
            if profile_f != "All" and profile_f not in r.profile:
                continue
            visible.append(r)

        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(visible))
        for row, rule in enumerate(visible):
            vals = [
                rule.name, rule.enabled, rule.direction, rule.action,
                rule.protocol, rule.local_port, rule.remote_port,
                rule.program, rule.profile,
            ]
            tip = (
                "%s\nProgram: %s\nProtocol: %s  Local: %s  Remote: %s\n"
                "Profile: %s" % (
                    rule.name, rule.program or "(any)", rule.protocol or "-",
                    rule.local_port or "-", rule.remote_port or "-",
                    rule.profile or "-")
            )
            for col, val in enumerate(vals):
                item = centered_item(val, sortable=(col == 0))
                # Whatever the column width, the full value is one hover away.
                item.setToolTip(tip)
                # Colour coding
                if rule.action == "Allow":
                    item.setForeground(QColor(semantic("success")))
                elif rule.action == "Block":
                    item.setForeground(QColor(semantic("error")))
                if rule.enabled == "No":
                    item.setForeground(QColor("#888888"))
                self._table.setItem(row, col, item)
        self._table.setSortingEnabled(True)

        total = len(self._all_rules)
        self._status_lbl.setText(
            f"Showing {len(visible)} / {total} rule(s)"
            + (" (filtered)" if total != len(visible) else "")
        )

    def _measured_floor(self, col: int) -> int:
        """The width column `col` needs on THIS display, measured live.

        `_COL_WIDTHS` are pixel constants measured on one machine at 9pt and
        then scaled by the point-size ratio. Scaling by point size is not the
        same thing as the text fitting: Qt's advance width for a string also
        moves with the display's DPI, the installed UI font and the Windows
        text scale. 70px held "Allow" on the CI runner and clipped it at 72px
        here — a real failure of the rule, which read as a flaky test.

        So each column is also measured against its own exemplar and its
        header, and the wider of constant-and-measurement wins. Only columns
        with something definite to fit have an exemplar: Name is deliberately
        allowed to elide (every cell carries the full value as a tooltip),
        and sizing it to its widest real value would put a 544-rule table at
        the cap on every column.
        """
        metrics = self._table.fontMetrics()
        header_metrics = self._table.horizontalHeader().fontMetrics()

        head = self._table.horizontalHeaderItem(col)
        needed = header_metrics.horizontalAdvance(head.text()) if head else 0
        exemplar = _COL_EXEMPLARS.get(col)
        if exemplar:
            needed = max(needed, metrics.horizontalAdvance(exemplar))
        return min(needed + _FIT_PADDING, _FIT_MAX_WIDTH)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        has_selection = bool(self._table.selectedItems())
        self._delete_btn.setEnabled(has_selection)

    def _get_selected_name(self) -> Optional[str]:
        items = self._table.selectedItems()
        if not items:
            return None
        row = items[0].row()
        name_item = self._table.item(row, 0)
        return name_item.text() if name_item else None

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self._refresh_btn.setEnabled(enabled)
        self._block_btn.setEnabled(enabled)
        self._unblock_btn.setEnabled(enabled)
        self._unblock_folder_btn.setEnabled(enabled)
        self._open_port_btn.setEnabled(enabled)
        self._export_btn.setEnabled(enabled)
        self._import_btn.setEnabled(enabled)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_start(self, app=None) -> None:
        self.app = app

    def get_refresh_interval(self) -> Optional[int]:
        return 60_000

    def refresh_data(self) -> None:
        self._do_refresh()

    def on_activate(self) -> None:
        if not getattr(self, "_loaded", False):
            self._loaded = True
            self._do_refresh()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def on_deactivate(self) -> None:
        pass

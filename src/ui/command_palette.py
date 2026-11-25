from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout,
)
from core.table_ui import set_role

logger = logging.getLogger(__name__)


@dataclass
class PaletteItem:
    kind: str           # "Module", "Quick Fix", "Script", "Action"
    title: str
    subtitle: str
    target_module: str
    payload: Optional[dict] = None
    icon: str = ""


class CommandPalette(QDialog):
    """Universal Ctrl+K / Ctrl+P Command & Navigation Palette.

    Indexes modules, quick fixes, built-in and user scripts, and system tools.
    Type to filter across titles and descriptions; Up/Down/Enter to navigate.
    """

    def __init__(self, modules: List, on_select: Callable[..., None], parent=None):
        super().__init__(parent)
        self._modules = modules
        self._on_select = on_select
        self._items: List[PaletteItem] = []
        self.setObjectName("commandPalette")
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setFixedWidth(580)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search modules, quick fixes, scripts, or actions (Ctrl+K / Ctrl+P)…")
        layout.addWidget(self._search)

        self._list = QListWidget()
        self._list.setMaximumHeight(360)
        layout.addWidget(self._list)

        hint = QLabel("  ↑↓ navigate   ↵ select   Esc dismiss")
        set_role(hint, "muted")
        layout.addWidget(hint)

        self._collect_index()
        self._populate("")
        self._search.textChanged.connect(self._populate)
        self._list.itemActivated.connect(self._activate)
        self._search.installEventFilter(self)

    # ------------------------------------------------------------------
    # Index Collection
    # ------------------------------------------------------------------

    def _collect_index(self) -> None:
        """Aggregate modules, quick fix actions, and scripts into a unified index."""
        self._items.clear()

        # 1. Modules
        for mod in self._modules:
            icon = getattr(mod, "icon", "📂") or "📂"
            group = getattr(mod, "group", "")
            desc = getattr(mod, "description", "") or ""
            grp_str = f" • {group.name}" if hasattr(group, "name") else (f" • {group}" if group else "")
            self._items.append(PaletteItem(
                kind="Module",
                title=f"{icon}  {mod.name}",
                subtitle=f"{desc}{grp_str}".strip(),
                target_module=mod.name,
                payload=None,
            ))

        # 2. Quick Fix Actions
        try:
            from modules.quick_fix.fix_actions import ALL_ACTIONS
            for act in ALL_ACTIONS:
                cat_str = f" • {act.category}" if getattr(act, "category", "") else ""
                self._items.append(PaletteItem(
                    kind="Quick Fix",
                    title=f"🔧  {act.title}",
                    subtitle=f"{act.description}{cat_str}".strip(),
                    target_module="Quick Fix",
                    payload={"action_id": act.id},
                ))
        except Exception as e:
            logger.debug("CommandPalette: could not index Quick Fix actions: %s", e)

        # 3. Scripts Hub (Catalog + User Scripts)
        try:
            from modules.scripts_hub.catalog import CATALOG
            for s in CATALOG:
                cat_str = f" • {s.category}" if getattr(s, "category", "") else ""
                self._items.append(PaletteItem(
                    kind="Script",
                    title=f"📜  {s.title}",
                    subtitle=f"{s.description}{cat_str}".strip(),
                    target_module="Scripts",
                    payload={"script_id": s.id},
                ))
        except Exception as e:
            logger.debug("CommandPalette: could not index catalog scripts: %s", e)

        try:
            from modules.scripts_hub.library import load_user_scripts
            for us in load_user_scripts():
                self._items.append(PaletteItem(
                    kind="Script",
                    title=f"📁  {us.title}",
                    subtitle=f"{us.description} • User Script".strip(),
                    target_module="Scripts",
                    payload={"script_id": us.id},
                ))
        except Exception as e:
            logger.debug("CommandPalette: could not index user scripts: %s", e)

        # 4. Direct Diagnostic & System Tools
        self._items.append(PaletteItem(
            kind="Action",
            title="📋  Investigation Casebook & Incident Workspace",
            subtitle="Diagnostic bookmarks, case notes, root-cause tags, and executive team brief export",
            target_module="Casebook",
            payload=None,
        ))
        self._items.append(PaletteItem(
            kind="Action",
            title="🛡️  Live Process Tree & Anomaly Hunter",
            subtitle="Parent-child process tree analysis and MITRE ATT&CK execution anomaly detection",
            target_module="Process Hunter",
            payload=None,
        ))
        self._items.append(PaletteItem(
            kind="Action",
            title="📸  System Baseline Snapshot & Compare ('Before & After')",
            subtitle="Capture machine services, autostarts, and tasks baseline and compare changes",
            target_module="System Snapshot",
            payload=None,
        ))
        self._items.append(PaletteItem(
            kind="Action",
            title="⚡  Run 60-Category Storage & Junk Sweep",
            subtitle="Deep TreeSize Pro directory crawl and 60-category safe cleanup audit",
            target_module="Scripts",
            payload={"script_id": "user:invoke-winstoragetreeandjunksweep.ps1"},
        ))
        self._items.append(PaletteItem(
            kind="Action",
            title="🔍  System Information & Live Specs",
            subtitle="Processor, motherboard, BIOS, memory, GPU and storage hardware attribution",
            target_module="System Information",
            payload=None,
        ))

    # ------------------------------------------------------------------
    # Keyboard Navigation
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self._search and isinstance(event, QKeyEvent):
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._list.setFocus()
                if self._list.count() > 0 and self._list.currentRow() < 0:
                    self._list.setCurrentRow(0)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._activate()
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._activate()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Filtering & Activation
    # ------------------------------------------------------------------

    def _populate(self, text: str):
        self._list.clear()
        q = text.strip().lower()
        query_words = q.split() if q else []

        matches = []
        for item in self._items:
            if not query_words:
                matches.append(item)
                continue
            haystack = f"{item.kind} {item.title} {item.subtitle}".lower()
            if all(w in haystack for w in query_words):
                matches.append(item)

        for item in matches[:60]:
            label = f"[{item.kind}]  {item.title}"
            if item.subtitle:
                sub = item.subtitle
                if len(sub) > 65:
                    sub = sub[:62] + "…"
                label = f"{label}\n    {sub}"

            listItem = QListWidgetItem(label)
            listItem.setData(Qt.ItemDataRole.UserRole, (item.target_module, item.payload))
            self._list.addItem(listItem)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _activate(self, item=None):
        if item is None:
            item = self._list.currentItem()
        if item:
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, tuple) and len(data) == 2:
                target, payload = data
                try:
                    self._on_select(target, payload=payload)
                except TypeError:
                    self._on_select(target)
            elif data:
                self._on_select(data)
            self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        self._search.setFocus()
        self._search.clear()
        self._populate("")

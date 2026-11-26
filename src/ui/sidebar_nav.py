# src/ui/sidebar_nav.py
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget,
)


class SidebarNav(QWidget):
    """Vertical navigation sidebar.

    Emits module_selected(module_name: str) when a module button is clicked.
    Groups are collapsible headers; includes a quick filter box.
    Collapsed mode shows icon-only (48 px wide).
    """

    module_selected = pyqtSignal(str)
    collapsed_changed = pyqtSignal(bool)  # True = collapsed, False = expanded

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._collapsed = False
        self._is_admin = False
        self._module_buttons: Dict[str, List[Tuple[str, QPushButton]]] = {}
        self._group_headers: Dict[str, QPushButton] = {}
        self._collapsed_groups: set[str] = set()
        self._btn_map: Dict[str, QPushButton] = {}
        self._group_order: List[str] = []
        self._active_name: Optional[str] = None

        self._build_layout()
        self.setMinimumWidth(180)
        self.setMaximumWidth(240)

    def _build_layout(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._toggle_btn = QPushButton("◀")
        self._toggle_btn.setFixedHeight(28)
        self._toggle_btn.setToolTip("Collapse sidebar")
        self._toggle_btn.clicked.connect(self._toggle_collapse)
        outer.addWidget(self._toggle_btn)

        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Filter tools...")
        self._filter_edit.setClearButtonEnabled(True)
        self._filter_edit.setContentsMargins(6, 4, 6, 4)
        self._filter_edit.textChanged.connect(self._apply_filter)
        outer.addWidget(self._filter_edit)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(scroll.Shape.NoFrame)

        self._nav_widget = QWidget()
        self._nav_layout = QVBoxLayout(self._nav_widget)
        self._nav_layout.setContentsMargins(0, 4, 0, 4)
        self._nav_layout.setSpacing(0)
        self._nav_layout.addStretch()

        scroll.setWidget(self._nav_widget)
        outer.addWidget(scroll)

    def set_admin(self, is_admin: bool) -> None:
        self._is_admin = is_admin
        for name, btn in self._btn_map.items():
            if btn.property("requires_admin") and not is_admin:
                btn.setEnabled(False)
                btn.setToolTip("Requires administrator")
            elif btn.property("requires_admin") and is_admin:
                btn.setEnabled(True)
                btn.setToolTip(btn.property("display_name") or name)

    def add_module(self, group: str, name: str, icon: str,
                   display: str, requires_admin: bool) -> None:
        if group not in self._module_buttons:
            self._module_buttons[group] = []
            self._group_order.append(group)
            stretch_idx = self._nav_layout.count() - 1
            header = QPushButton(f"▾ {group}")
            header.setObjectName("sidebarGroupHeader")
            header.setFlat(True)
            header.clicked.connect(lambda _=False, g=group: self._toggle_group(g))
            self._nav_layout.insertWidget(stretch_idx, header)
            self._group_headers[group] = header

        stretch_idx = self._nav_layout.count() - 1
        btn = QPushButton(f"{icon}  {display}" if not self._collapsed else icon)
        btn.setObjectName("sidebarModuleBtn")
        btn.setCheckable(True)
        btn.setProperty("module_name", name)
        btn.setProperty("requires_admin", requires_admin)
        btn.setProperty("display_name", display)
        btn.setProperty("icon_char", icon)
        btn.setToolTip(display)
        btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn.setFixedHeight(32)

        if requires_admin and not self._is_admin:
            btn.setEnabled(False)
            btn.setToolTip("Requires administrator")

        btn.clicked.connect(lambda checked, n=name: self._on_btn_clicked(n))
        self._nav_layout.insertWidget(stretch_idx, btn)
        self._module_buttons[group].append((name, btn))
        self._btn_map[name] = btn

    def toggle_collapse(self) -> None:
        """Public wrapper to programmatically toggle sidebar collapse state."""
        self._toggle_collapse()

    def select(self, name: str) -> None:
        if self._active_name and self._active_name in self._btn_map:
            self._btn_map[self._active_name].setChecked(False)
        self._active_name = name
        if name in self._btn_map:
            self._btn_map[name].setChecked(True)

    def _on_btn_clicked(self, name: str) -> None:
        self.select(name)
        self.module_selected.emit(name)

    def _toggle_group(self, group: str) -> None:
        if group in self._collapsed_groups:
            self._collapsed_groups.remove(group)
        else:
            self._collapsed_groups.add(group)
        hdr = self._group_headers.get(group)
        if hdr is not None:
            hdr.setText(f"▸ {group}" if group in self._collapsed_groups else f"▾ {group}")
        self._apply_filter(self._filter_edit.text())

    def _apply_filter(self, text: str) -> None:
        q = (text or "").strip().lower()
        for group, items in self._module_buttons.items():
            any_visible = False
            group_collapsed = (group in self._collapsed_groups) and not q and not self._collapsed
            for name, btn in items:
                display = (btn.property("display_name") or name).lower()
                matches = not q or (q in display or q in group.lower())
                visible = matches and not group_collapsed
                btn.setVisible(visible)
                if matches:
                    any_visible = True
            hdr = self._group_headers.get(group)
            if hdr is not None:
                hdr.setVisible((not self._collapsed) and any_visible)

    def _toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        self.collapsed_changed.emit(self._collapsed)
        self._filter_edit.setVisible(not self._collapsed)
        if self._collapsed:
            self.setMaximumWidth(48)
            self.setMinimumWidth(48)
            self._toggle_btn.setText("▶")
            for name, btn in self._btn_map.items():
                icon = btn.property("icon_char") or ""
                btn.setText(icon)
        else:
            self.setMaximumWidth(240)
            self.setMinimumWidth(180)
            self._toggle_btn.setText("◀")
            for name, btn in self._btn_map.items():
                icon = btn.property("icon_char") or ""
                display = btn.property("display_name") or name
                btn.setText(f"{icon}  {display}")
        self._apply_filter(self._filter_edit.text())

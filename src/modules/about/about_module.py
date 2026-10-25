
from modules.about.help_widgets import (
    explain_widget,
)
import os
import platform
import sys

from PyQt6 import QtCore
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.table_ui import set_role
from core.module_groups import ModuleGroup
from _version import __version__ as _APP_VERSION

_GITHUB_URL = "https://github.com/vraku3/Windows_client_tool"


def _kv_row(key: str, value: str) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 2, 0, 2)
    k = QLabel(key)
    set_role(k, "muted")
    k.setMinimumWidth(160)
    v = QLabel(value)
    v.setWordWrap(True)
    v.setToolTip(value)
    v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(k)
    layout.addWidget(v, 1)
    return row


class AboutModule(BaseModule):
    name = "About"
    icon = "\u2139\ufe0f"
    description = "App version, environment info, and shortcuts"
    requires_admin = False
    group = ModuleGroup.TOOLS

    def create_widget(self) -> QWidget:
        outer = QFrame()
        outer.setObjectName("glanceTile")
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(4)

        title = QLabel("WinClientTool")
        set_role(title, "heading")
        layout.addWidget(title)

        sub = QLabel(f"Version {_APP_VERSION}")
        set_role(sub, "muted")
        layout.addWidget(sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)

        layout.addWidget(sep)
        layout.addSpacing(12)

        env_label = QLabel("Environment")
        set_role(env_label, "heading")
        layout.addWidget(env_label)
        data_dir = getattr(getattr(self, "app", None), "app_data_dir", "")
        layout.addWidget(_kv_row("Build date", "Not recorded in this build"))
        layout.addWidget(_kv_row("Run mode", "Packaged application" if getattr(sys, "frozen", False) else "From source"))
        layout.addWidget(_kv_row("Data folder", data_dir or "Unavailable before app start"))
        layout.addWidget(_kv_row("Log folder", os.path.join(data_dir, "logs") if data_dir else "Unavailable before app start"))
        layout.addWidget(_kv_row("Python", sys.version.split()[0]))
        layout.addWidget(_kv_row("PyQt6", QtCore.PYQT_VERSION_STR))
        layout.addWidget(_kv_row("Qt runtime", QtCore.qVersion()))
        layout.addWidget(_kv_row("Platform", platform.version()))
        layout.addWidget(_kv_row("Architecture", platform.machine()))

        layout.addSpacing(16)
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)

        layout.addWidget(sep2)
        layout.addSpacing(12)

        kb_label = QLabel("Keyboard Shortcuts")
        set_role(kb_label, "heading")
        layout.addWidget(kb_label)
        shortcuts = [
            ("Ctrl+P", "Command Palette — navigate to any module"),
            ("Ctrl+,", "Settings"),
            ("Ctrl+F", "Search within current module"),
            ("F5", "Refresh current module"),
            ("Escape", "Clear search / dismiss dialog"),
        ]
        for keys, desc in shortcuts:
            row = QWidget()
            rlay = QHBoxLayout(row)
            rlay.setContentsMargins(0, 1, 0, 1)
            badge = QLabel(keys)
            set_role(badge, "mono")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rlay.addWidget(badge)
            rlay.addSpacing(12)
            rlay.addWidget(QLabel(desc))
            rlay.addStretch()
            layout.addWidget(row)

        layout.addSpacing(16)
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)

        layout.addWidget(sep3)
        layout.addSpacing(8)

        github = QLabel(f'Source: <a href="{_GITHUB_URL}">{_GITHUB_URL}</a>')
        github.setOpenExternalLinks(True)
        github.setObjectName("link")
        layout.addWidget(github)

        layout.addStretch()

        wrapper = QWidget()
        wl = QVBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(outer, 1)

        explain_widget(wrapper, introduction=True)
        return wrapper

    def on_start(self, app): self.app = app
    def on_stop(self): pass
    def on_activate(self): pass
    def on_deactivate(self): pass


from modules.about.help_widgets import (
    explain_widget,
)
import platform
import sys

from PyQt6 import QtCore
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from _version import __version__ as _APP_VERSION

_GITHUB_URL = "https://github.com/your-repo/windows-client-tool"


def _kv_row(key: str, value: str) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 2, 0, 2)
    k = QLabel(key)
    k.setStyleSheet("color: #888; min-width: 180px;")
    v = QLabel(value)
    v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(k)
    layout.addWidget(v)
    layout.addStretch()
    return row


class AboutModule(BaseModule):
    name = "About"
    icon = "\u2139\ufe0f"
    description = "App version, environment info, and shortcuts"
    requires_admin = False
    group = ModuleGroup.TOOLS

    def create_widget(self) -> QWidget:
        outer = QWidget()
        outer.setMaximumWidth(700)
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(4)

        title = QLabel("Windows 11 Tweaker & Optimizer")
        title.setStyleSheet("font-size: 20px; font-weight: bold; margin-bottom: 2px;")
        layout.addWidget(title)

        sub = QLabel(f"Version {_APP_VERSION}")
        sub.setStyleSheet("color: #888; font-size: 13px; margin-bottom: 16px;")
        layout.addWidget(sub)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #333;")
        layout.addWidget(sep)
        layout.addSpacing(12)

        env_label = QLabel("Environment")
        env_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #aaa; margin-bottom: 6px;")
        layout.addWidget(env_label)
        layout.addWidget(_kv_row("Python", sys.version.split()[0]))
        layout.addWidget(_kv_row("PyQt6", QtCore.qVersion()))
        layout.addWidget(_kv_row("Platform", platform.version()))
        layout.addWidget(_kv_row("Architecture", platform.machine()))

        layout.addSpacing(16)
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #333;")
        layout.addWidget(sep2)
        layout.addSpacing(12)

        kb_label = QLabel("Keyboard Shortcuts")
        kb_label.setStyleSheet("font-size: 13px; font-weight: bold; color: #aaa; margin-bottom: 6px;")
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
            badge.setStyleSheet(
                "background: #333; color: #ccc; border-radius: 3px; "
                "padding: 2px 7px; font-family: monospace; min-width: 80px;"
            )
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rlay.addWidget(badge)
            rlay.addSpacing(12)
            rlay.addWidget(QLabel(desc))
            rlay.addStretch()
            layout.addWidget(row)

        layout.addSpacing(16)
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet("color: #333;")
        layout.addWidget(sep3)
        layout.addSpacing(8)

        github = QLabel(f'Source: <a href="{_GITHUB_URL}">{_GITHUB_URL}</a>')
        github.setOpenExternalLinks(True)
        github.setObjectName("link")
        layout.addWidget(github)

        layout.addStretch()

        wrapper = QWidget()
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(outer)
        wl.addStretch()
        explain_widget(wrapper, introduction=True)
        return wrapper

    def on_start(self, app): self.app = app
    def on_stop(self): pass
    def on_activate(self): pass
    def on_deactivate(self): pass

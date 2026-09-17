# src/modules/process_explorer/sysinternals_tab.py
from __future__ import annotations
import logging
import os
import shutil
import subprocess
from typing import Optional

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QLineEdit, QComboBox, QScrollArea,
                              QGroupBox, QGridLayout)
from PyQt6.QtCore import Qt

from ui.error_banner import ErrorBanner

logger = logging.getLogger(__name__)

_UNC_BASE = r"\\live.sysinternals.com\tools"
_WEBCLIENT_SERVICE = "WebClient"

TOOLS = [
    # (category, tool_name, exe_filename, description)
    ("Process",      "Process Explorer", "procexp64.exe",   "Detailed process/thread viewer"),
    ("Process",      "Process Monitor",  "Procmon64.exe",   "File/registry/network activity"),
    ("Process",      "Autoruns",         "Autoruns64.exe",  "All autostart locations"),
    ("Process",      "PsExec",           "PsExec64.exe",    "Remote process launcher"),
    ("Process",      "PsKill",           "PsKill.exe",      "Kill processes by name or PID"),
    ("Process",      "PsList",           "PsList.exe",      "List process details"),
    ("Process",      "PsService",        "PsService.exe",   "View and control services"),
    ("Process",      "PsSuspend",        "PsSuspend.exe",   "Suspend/resume processes"),
    ("Network",      "TCPView",          "Tcpview64.exe",   "Active TCP/UDP endpoints"),
    ("Network",      "PsPing",           "PsPing.exe",      "Network latency/bandwidth test"),
    ("Network",      "Whois",            "whois64.exe",     "WHOIS domain lookup"),
    ("Security",     "Sigcheck",         "sigcheck64.exe",  "File signature + VirusTotal check"),
    ("Security",     "AccessChk",        "accesschk64.exe", "Object permissions viewer"),
    ("Security",     "SDelete",          "sdelete64.exe",   "Secure file deletion"),
    ("File/Disk",    "Handle",           "handle64.exe",    "Which files are open"),
    ("File/Disk",    "Streams",          "streams64.exe",   "Find NTFS alternate data streams"),
    ("File/Disk",    "DiskMon",          "Diskmon.exe",     "Disk activity monitor"),
    ("File/Disk",    "PendMoves",        "pendmoves.exe",   "Pending file rename/delete ops"),
    ("System Info",  "Coreinfo",         "Coreinfo.exe",    "Logical CPU topology info"),
    ("System Info",  "RAMMap",           "RAMMap.exe",      "RAM usage details"),
    ("System Info",  "VMMap",            "vmmap.exe",       "Virtual memory map"),
    ("System Info",  "WinObj",           "winobj.exe",      "NT namespace object viewer"),
    ("System Info",  "BgInfo",           "Bginfo64.exe",    "Desktop background system info"),
    ("System Info",  "ZoomIt",           "ZoomIt.exe",      "Screen zoom and annotation"),
]


def _get_cache_dir() -> str:
    base = os.environ.get("APPDATA", os.path.expanduser("~"))
    d = os.path.join(base, "WindowsTweaker", "sysinternals")
    os.makedirs(d, exist_ok=True)
    return d


def _is_cached(exe: str) -> bool:
    return os.path.isfile(os.path.join(_get_cache_dir(), exe))


def _is_webclient_running() -> bool:
    try:
        import win32serviceutil
        status = win32serviceutil.QueryServiceStatus(_WEBCLIENT_SERVICE)
        return status[1] == 4  # SERVICE_RUNNING
    except Exception:
        logger.warning("Ignored Exception checking WebClient service", exc_info=True)
        return False


def _start_webclient() -> bool:
    try:
        import win32serviceutil
        win32serviceutil.StartService(_WEBCLIENT_SERVICE)
        return True
    except Exception as e:
        logger.error("Could not start WebClient: %s", e)
        return False


def _launch(exe: str) -> bool:
    cached = os.path.join(_get_cache_dir(), exe)
    path = cached if os.path.isfile(cached) else os.path.join(_UNC_BASE, exe)
    try:
        subprocess.Popen([path], creationflags=subprocess.DETACHED_PROCESS)
        return True
    except Exception as e:
        logger.error("Launch failed for %s: %s", exe, e)
        return False


def _cache_tool(exe: str) -> bool:
    src = os.path.join(_UNC_BASE, exe)
    dst = os.path.join(_get_cache_dir(), exe)
    try:
        shutil.copy2(src, dst)
        return True
    except Exception as e:
        logger.error("Cache failed for %s: %s", exe, e)
        return False


#: The warning banner paints its own fixed background, so it must paint its
#: own foreground too — otherwise the text comes from the active theme, and
#: `dark.qss` sets `QLabel { color: #d4d4d4 }`, which on this pale yellow is
#: about 1.3:1 and cannot be read. The link needs the same treatment: Qt's
#: default link blue is barely better here. Both colours below clear WCAG AA
#: (4.5:1) against the background, and `tests/test_sysinternals_banner.py`
#: computes the ratios rather than trusting this comment.
BANNER_STYLE = (
    "background:#fff3cd;"
    "color:#664d03;"
    "padding:6px;"
    "border:1px solid #e0c877;"
    "border-radius:4px;"
)

#: A Qt stylesheet cannot reach the `<a>` inside a QLabel's rich text — there
#: is no descendant selector for it — so the link is coloured inline in the
#: markup instead. Qt's default link blue on this background is 3.1:1.
BANNER_LINK_COLOR = "#0a4275"


class SysinternalsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)

        # Warning banner (hidden by default)
        self._banner = QLabel()
        self._banner.setStyleSheet(BANNER_STYLE)
        self._banner.setOpenExternalLinks(False)
        self._banner.linkActivated.connect(self._on_start_webclient)
        self._banner.hide()
        self._layout.addWidget(self._banner)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        self._layout.addWidget(self._error_banner)

        # Filter bar
        bar = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search tools…")
        self._search.textChanged.connect(self._rebuild)
        bar.addWidget(self._search)
        self._cat_combo = QComboBox()
        self._cat_combo.addItem("All")
        cats = sorted({t[0] for t in TOOLS})
        self._cat_combo.addItems(cats)
        self._cat_combo.currentTextChanged.connect(self._rebuild)
        bar.addWidget(self._cat_combo)
        refresh_btn = QPushButton("Refresh Cache Status")
        refresh_btn.clicked.connect(self._rebuild)
        bar.addWidget(refresh_btn)
        self._layout.addLayout(bar)

        # Scrollable tool list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._content)
        self._layout.addWidget(scroll)

        self._rebuild()

    def showEvent(self, event):
        super().showEvent(event)
        if not _is_webclient_running():
            self._banner.setText(
                "⚠ Sysinternals Live requires the WebClient service to be running. "
                f"<a href='start_webclient' style='color:{BANNER_LINK_COLOR}'>"
                "Start WebClient Service</a>"
            )
            self._banner.show()
        else:
            self._banner.hide()

    def _on_start_webclient(self, _):
        if _start_webclient():
            self._banner.hide()
        else:
            self._error_banner.set_error(
                "Failed to start WebClient service. Run as administrator.")

    def _rebuild(self):
        # Clear content
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        q = self._search.text().lower()
        cat = self._cat_combo.currentText()

        current_cat = None
        group: Optional[QGroupBox] = None
        grid: Optional[QGridLayout] = None
        row = 0

        for category, name, exe, desc in TOOLS:
            if cat != "All" and category != cat:
                continue
            if q and q not in name.lower() and q not in desc.lower():
                continue

            if category != current_cat:
                current_cat = category
                group = QGroupBox(category)
                grid = QGridLayout(group)
                self._content_layout.addWidget(group)
                row = 0

            cached = _is_cached(exe)
            status = "✅ cached" if cached else "☁ live"

            name_lbl = QLabel(f"<b>{name}</b>")
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("color: gray;")
            status_lbl = QLabel(status)

            launch_btn = QPushButton("Launch")
            launch_btn.setFixedWidth(70)
            _exe = exe  # capture for lambda
            launch_btn.clicked.connect(lambda checked, e=_exe: _launch(e))

            cache_btn = QPushButton("Cache")
            cache_btn.setFixedWidth(60)
            cache_btn.clicked.connect(lambda checked, e=_exe: (_cache_tool(e), self._rebuild()))

            grid.addWidget(name_lbl,   row, 0)
            grid.addWidget(desc_lbl,   row, 1)
            grid.addWidget(status_lbl, row, 2)
            grid.addWidget(launch_btn, row, 3)
            grid.addWidget(cache_btn,  row, 4)
            row += 1

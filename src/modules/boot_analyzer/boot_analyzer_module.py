"""Boot Performance Analyzer — measure and optimize Windows boot time."""
import subprocess
import re
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker
from modules.boot_analyzer import boot_history
from modules.boot_analyzer.boot_history_view import BootHistoryPanel
import logging

logger = logging.getLogger(__name__)


class BootAnalyzerModule(BaseModule):
    name = "Boot Analyzer"
    icon = "🚀"
    description = "Analyze and optimize Windows boot performance"
    group = ModuleGroup.SYSTEM
    requires_admin = False

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._worker: Optional[Worker] = None
        self._scanning = False
        self._loaded = False

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(8, 8, 8, 8)

        # Scroll area for content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(12)

        # Title
        title = QLabel("Boot Performance Analysis")
        title.setObjectName("heading")
        content_layout.addWidget(title)

        self._history_panel = BootHistoryPanel()
        content_layout.addWidget(self._history_panel)

        # Info cards container
        self._info_cards = QVBoxLayout()
        self._info_cards.setSpacing(8)
        content_layout.addLayout(self._info_cards)

        # Action buttons row
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        refresh_btn = QPushButton("🔄 Refresh Analysis")
        refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_btn.clicked.connect(self._load_info)
        actions_layout.addWidget(refresh_btn)

        reduce_timeout_btn = QPushButton("⏱️ Reduce Boot Timeout to 3s")
        reduce_timeout_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reduce_timeout_btn.clicked.connect(self._reduce_timeout)
        actions_layout.addWidget(reduce_timeout_btn)

        toggle_faststart_btn = QPushButton("🔌 Fast Startup Info")
        toggle_faststart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle_faststart_btn.clicked.connect(self._show_fast_startup_info)
        actions_layout.addWidget(toggle_faststart_btn)

        actions_layout.addStretch()
        content_layout.addLayout(actions_layout)
        content_layout.addStretch()

        scroll.setWidget(content)
        layout.addWidget(scroll)

        return self._widget

    def on_start(self, app) -> None:
        self.app = app

    def on_activate(self) -> None:
        if not self._loaded:
            self._loaded = True
            self._load_info()

    def on_deactivate(self) -> None:
        self.cancel_all_workers()
        # A cancelled worker never reaches _display_info, which is the only
        # place this was cleared: leaving it set froze every later refresh.
        self._scanning = False

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def get_status_info(self) -> str:
        return "Boot Analyzer — boot time optimization"

    def get_refresh_interval(self) -> Optional[int]:
        return 120_000

    def refresh_data(self) -> None:
        self._load_info()

    def _load_info(self) -> None:
        # The auto-refresh timer can tick before this tab has ever been
        # opened, and a composite builds a child's widget only on first
        # show — there is then nothing to load into.
        if self._widget is None:
            return
        if self._scanning:
            return
        self._scanning = True
        # Clear existing cards
        while self._info_cards.count():
            item = self._info_cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Placeholder while loading
        loading = QLabel("Analyzing boot configuration...")
        loading.setStyleSheet("color: #888; font-size: 13px; padding: 8px;")
        self._info_cards.addWidget(loading)

        def do_analyze(worker):
            info = {}

            # Boot type (UEFI vs BIOS)
            try:
                result = subprocess.run(
                    ["bcdedit", "/enum", "firmware"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if result.returncode != 0:
                    info["boot_type"] = "Unknown"      # refused: not proof of BIOS
                else:
                    info["boot_type"] = "UEFI" if "UEFI" in result.stdout else "BIOS/Legacy"
            except Exception:
                logger.debug("Failed to detect boot type", exc_info=True)
                info["boot_type"] = "Unknown"

            # Boot timeout and entry count from one bcdedit run. A refusal
            # (bcdedit needs elevation) stays None -- never a made-up "Optimal".
            try:
                result = subprocess.run(
                    ["bcdedit", "/enum", "all"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                bcd = boot_history.parse_bcd(result.stdout)
            except Exception:
                logger.warning("Failed to read the boot configuration", exc_info=True)
                bcd = {"timeout": None, "entries": None}
            info["boot_timeout"] = bcd["timeout"]
            info["boot_entries"] = bcd["entries"]

            # Last boot time
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime | Get-Date -Format 'yyyy-MM-dd HH:mm'"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                info["last_boot"] = result.stdout.strip() or "N/A"
            except Exception:
                logger.debug("Failed to get last boot time", exc_info=True)
                info["last_boot"] = "N/A"

            # Fast Startup status via powercfg
            try:
                result = subprocess.run(
                    ["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "HIBERNATE"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                info["fast_startup"] = "Enabled" if "Enabled" in result.stdout else "Disabled"
            except Exception:
                logger.debug("Failed to detect fast startup status", exc_info=True)
                info["fast_startup"] = "Unknown"

            # Current uptime
            try:
                result = subprocess.run(
                    ["powershell", "-Command",
                     "(Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime | Select-Object -ExpandProperty Days"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                days = result.stdout.strip()
                info["uptime_days"] = f"{days} days" if days else "N/A"
            except Exception:
                logger.debug("Failed to get uptime", exc_info=True)
                info["uptime_days"] = "N/A"

            try:
                info["facts"] = boot_history.read_boot_facts()
            except Exception as error:  # noqa: BLE001 - shown in the panel, not swallowed
                logger.warning("Boot history failed: %s", error, exc_info=True)
                info["facts"] = boot_history.BootFacts(problems=[f"boot history failed: {error}"])
            return info

        self._worker = Worker(do_analyze)
        self._worker.signals.result.connect(self._display_info)
        self._workers.append(self._worker)
        self.app.thread_pool.start(self._worker)

    def _display_info(self, info: dict) -> None:
        self._scanning = False
        facts = info.get("facts")
        if facts is not None:
            if facts.fast_startup is not None:
                info["fast_startup"] = "Enabled" if facts.fast_startup else "Disabled"
            self._history_panel.set_facts(facts)
        # Clear
        while self._info_cards.count():
            item = self._info_cards.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        cards_data = [
            (
                "🖥️ Boot Mode",
                info.get("boot_type", "N/A"),
                "UEFI is faster and more secure" if info.get("boot_type") == "UEFI"
                else "Could not read the firmware configuration (bcdedit needs administrator)"
                if info.get("boot_type") == "Unknown"
                else "BIOS/Legacy mode — consider migrating to UEFI for better performance"
            ),
            (
                "⏱️ Boot Timeout",
                f"{info['boot_timeout']} seconds" if isinstance(info.get("boot_timeout"), int)
                else "Could not read",
                "Could not read the boot configuration (bcdedit needs administrator)"
                if not isinstance(info.get("boot_timeout"), int)
                else "⚠️ Consider reducing to 3 seconds" if info["boot_timeout"] > 3
                else "✅ Optimal"
            ),
            (
                "📋 Boot Entries",
                str(info["boot_entries"]) if isinstance(info.get("boot_entries"), int) else "Could not read",
                "Could not read the boot configuration (bcdedit needs administrator)"
                if not isinstance(info.get("boot_entries"), int)
                else "More entries = longer boot menu delay" if info["boot_entries"] > 2
                else "✅ Normal"
            ),
            (
                "🔌 Fast Startup",
                info.get("fast_startup", "N/A"),
                "Hybrid shutdown — kernel state saved to disk on shutdown"
                if info.get("fast_startup") == "Enabled"
                else "Standard shutdown — full kernel initialization on boot"
            ),
            (
                "🕐 Last Boot",
                info.get("last_boot", "N/A"),
                f"System uptime: {info.get('uptime_days', 'N/A')}"
            ),
        ]

        for title, value, detail in cards_data:
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background: #2d2d2d;
                    border: 1px solid #3c3c3c;
                    border-radius: 6px;
                    padding: 4px;
                }
            """)
            card_layout = QGridLayout(card)
            card_layout.setContentsMargins(12, 8, 12, 8)
            card_layout.setSpacing(2)

            t = QLabel(title)
            t.setStyleSheet("font-size: 11px; color: #888;")
            card_layout.addWidget(t, 0, 0)

            v = QLabel(str(value))
            v.setObjectName("metric")
            card_layout.addWidget(v, 1, 0)

            d = QLabel(detail)
            d.setStyleSheet("font-size: 11px; color: #aaa;")
            card_layout.addWidget(d, 2, 0)

            self._info_cards.addWidget(card)

    def _reduce_timeout(self) -> None:
        reply = QMessageBox.question(
            self._widget, "Reduce Boot Timeout",
            "Reduce Windows boot timeout to 3 seconds?\n\n"
            "This requires administrator privileges.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            subprocess.run(["bcdedit", "/timeout", "3"], check=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            QMessageBox.information(self._widget, "Done", "Boot timeout set to 3 seconds.")
            self._load_info()
        except Exception as e:
            QMessageBox.warning(
                self._widget, "Failed",
                f"Could not change timeout:\n{e}\n\nMake sure you are running as Administrator."
            )

    def _show_fast_startup_info(self) -> None:
        QMessageBox.information(
            self._widget, "Fast Startup",
            "Fast Startup information:\n\n"
            "Fast Startup (Hybrid Boot) saves the kernel and driver state to a "
            "hibernation file on shutdown, making subsequent boots faster.\n\n"
            "To toggle Fast Startup:\n"
            "  1. Open Control Panel → Power Options\n"
            "  2. Click 'Choose what the power buttons do'\n"
            "  3. Click 'Change settings that are currently unavailable'\n"
            "  4. Check/uncheck 'Turn on fast startup'\n\n"
            "Note: Requires admin privileges. Disable if you dual-boot — "
            "it can prevent other OSes from mounting the Windows partition."
        )

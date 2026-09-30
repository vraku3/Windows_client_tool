"""Boot Performance Analyzer — measure and optimize Windows boot time."""
import subprocess
import re
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QTableWidget, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.table_ui import centered_item, fit_table, set_role
from core.worker import Worker
from modules.boot_analyzer.boot_history import (
    BootHistoryResult,
    get_boot_history,
    trend_summary,
)
import logging

logger = logging.getLogger(__name__)

try:
    from PyQt6 import sip

    def _widget_is_valid(w) -> bool:
        return w is not None and not sip.isdeleted(w)
except ImportError:  # pragma: no cover - sip always ships with PyQt6 here
    def _widget_is_valid(w) -> bool:
        return w is not None


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

        # Info cards container
        self._info_cards = QVBoxLayout()
        self._info_cards.setSpacing(8)
        content_layout.addLayout(self._info_cards)

        # Boot-time trend: measured from the System log (see boot_history.py)
        history_title = QLabel("Boot Time Trend")
        history_title.setObjectName("heading")
        content_layout.addWidget(history_title)

        self._boot_trend_label = QLabel("")
        content_layout.addWidget(self._boot_trend_label)

        self._boot_history_table = QTableWidget(0, 3)
        self._boot_history_table.setHorizontalHeaderLabels(
            ["Boot Time", "Time to Ready", "Prior Shutdown"]
        )
        self._boot_history_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._boot_history_table.setSelectionMode(
            QTableWidget.SelectionMode.NoSelection
        )
        self._boot_history_table.verticalHeader().setVisible(False)
        fit_table(self._boot_history_table, stretch=(0,), content=(1, 2))
        content_layout.addWidget(self._boot_history_table)

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
                info["boot_type"] = "UEFI" if "UEFI" in result.stdout else "BIOS/Legacy"
            except Exception:
                logger.debug("Failed to detect boot type", exc_info=True)
                info["boot_type"] = "Unknown"

            # Boot timeout
            try:
                result = subprocess.run(
                    ["bcdedit", "/enum", "all"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                timeout_m = re.search(r'timeout\s*:\s*(\d+)', result.stdout, re.IGNORECASE)
                info["boot_timeout"] = int(timeout_m.group(1)) if timeout_m else "N/A"
            except Exception:
                logger.debug("Failed to detect boot timeout", exc_info=True)
                info["boot_timeout"] = "N/A"

            # Number of boot entries
            try:
                result = subprocess.run(
                    ["bcdedit", "/enum", "all"],
                    capture_output=True, text=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                info["boot_entries"] = result.stdout.count("bootloader")
            except Exception:
                logger.debug("Failed to count boot entries", exc_info=True)
                info["boot_entries"] = "N/A"

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

            # Boot-time trend from the System log (see boot_history.py for
            # why this reads System rather than Diagnostics-Performance).
            info["boot_history"] = get_boot_history()

            return info

        self._worker = Worker(do_analyze)
        self._worker.signals.result.connect(self._display_info)
        self._workers.append(self._worker)
        self.app.thread_pool.start(self._worker)

    def _display_info(self, info: dict) -> None:
        self._scanning = False
        # A worker can still be in flight when the tab is torn down (switch
        # away mid-scan); cancel() only sets a flag, it does not stop an
        # already-queued signal. Without this guard, a result landing after
        # the widget is gone crashes the app (measured elsewhere in this
        # codebase as exit -1073740791, not a catchable exception).
        if not _widget_is_valid(self._widget):
            return
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
                else "BIOS/Legacy mode — consider migrating to UEFI for better performance"
            ),
            (
                "⏱️ Boot Timeout",
                f"{info.get('boot_timeout', 'N/A')} seconds",
                "⚠️ Consider reducing to 3 seconds"
                if isinstance(info.get("boot_timeout"), int) and info.get("boot_timeout", 0) > 3
                else "✅ Optimal"
            ),
            (
                "📋 Boot Entries",
                str(info.get("boot_entries", "N/A")),
                "More entries = longer boot menu delay"
                if isinstance(info.get("boot_entries"), int) and info.get("boot_entries", 0) > 2
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

        self._render_boot_history(info.get("boot_history"))

    def _render_boot_history(self, result: Optional[BootHistoryResult]) -> None:
        """Fill the Boot Time Trend table and summary label. `result` is
        `None` only when this dict predates the boot-history key ever being
        set, which never happens on the live path -- guarded anyway since
        `_display_info` also runs against whatever a test hands it."""
        if result is None:
            return
        if not result.available:
            self._boot_trend_label.setText(
                f"Boot history could not be read: {result.reason}"
            )
            set_role(self._boot_trend_label, "statusWarning")
            self._boot_history_table.setRowCount(0)
            return

        summary = trend_summary(result.records)
        if summary is None:
            self._boot_trend_label.setText(
                "Not enough measured boots yet for a trend."
            )
            set_role(self._boot_trend_label, "statusInfo")
        else:
            self._boot_trend_label.setText(summary)
            if "slower" in summary:
                role = "statusWarning"
            elif "faster" in summary:
                role = "statusSuccess"
            else:
                role = "statusInfo"
            set_role(self._boot_trend_label, role)

        newest_first = list(reversed(result.records))
        self._boot_history_table.setRowCount(len(newest_first))
        for row, rec in enumerate(newest_first):
            self._boot_history_table.setItem(
                row, 0,
                centered_item(rec.boot_time.astimezone().strftime("%Y-%m-%d %H:%M")),
            )
            duration_text = (
                f"{rec.duration_seconds:.0f}s"
                if rec.duration_seconds is not None
                else "Unknown"
            )
            self._boot_history_table.setItem(row, 1, centered_item(duration_text))
            if rec.prior_shutdown_clean is None:
                shutdown_text = "Unknown"
            elif rec.prior_shutdown_clean:
                shutdown_text = "Clean shutdown"
            else:
                shutdown_text = "Unexpected shutdown"
            self._boot_history_table.setItem(row, 2, centered_item(shutdown_text))

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

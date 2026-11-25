"""Unit tests for Quick Fix UI components."""

from unittest.mock import MagicMock
import pytest

from PyQt6.QtWidgets import QApplication

from modules.quick_fix.quick_fix_playbooks import PLAYBOOKS
from modules.quick_fix.quick_fix_scanner import HealthFinding, QuickFixHealthReport
from modules.quick_fix.quick_fix_ui_components import (
    _CategoryChipsBar,
    _ConsoleDrawer,
    _HealthScanBanner,
    _PlaybookCard,
    _PlaybookSection,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app


def test_category_chips_bar(qapp):
    bar = _CategoryChipsBar()
    signals = []
    bar.category_changed.connect(signals.append)

    bar.select_category("network")
    assert "network" in signals

    bar.set_needs_attention_count(3)
    assert "3" in bar._buttons["attention"].text()


def test_health_scan_banner(qapp):
    banner = _HealthScanBanner()
    scan_signals = []
    rec_signals = []
    banner.scan_requested.connect(lambda: scan_signals.append(True))
    banner.show_recommended_requested.connect(lambda: rec_signals.append(True))

    banner._scan_btn.click()
    assert len(scan_signals) == 1

    banner.set_scanning(True)
    assert not banner._scan_btn.isEnabled()

    banner.set_scanning(False)
    assert banner._scan_btn.isEnabled()

    # Clean report
    clean_report = QuickFixHealthReport(findings=[], recommended_keys=set())
    banner.set_report(clean_report)
    assert "clean" in banner._status_lbl.text().lower()

    # Issue report
    issue_report = QuickFixHealthReport(
        findings=[HealthFinding("wmi", "WMI", "Corrupt", "danger", ["wmi_salvage"])],
        recommended_keys={"wmi_salvage"},
    )
    banner.set_report(issue_report)
    assert "1 actionable issue" in banner._status_lbl.text()
    assert not banner._show_recommended_btn.isHidden()


def test_playbook_card_and_section(qapp):
    section = _PlaybookSection()
    requested = []
    section.playbook_requested.connect(requested.append)

    assert len(section._cards) == len(PLAYBOOKS)
    first_card = section._cards[0]
    first_card._run_btn.click()
    assert len(requested) == 1
    assert requested[0] == PLAYBOOKS[0]

    section.set_playbook_running(True)
    assert not first_card._run_btn.isEnabled()

    section._toggle_collapse()
    assert not section._cards_container.isVisible()


def test_console_drawer(qapp):
    drawer = _ConsoleDrawer()
    drawer.append_line("Test log entry")
    assert "Test log entry" in drawer._output.toPlainText()

    drawer.set_timer_text("Completed in 1.2s")
    assert drawer._timer_lbl.text() == "Completed in 1.2s"

    drawer.clear()
    assert drawer._output.toPlainText() == ""

    drawer._toggle_console()
    assert not drawer._output.isVisible()

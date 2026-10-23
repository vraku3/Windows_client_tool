"""Network explanations cover construction and data-created rows, without actions."""
import pytest

from ui.tooltip_audit import audit_modules, inspect_widget


@pytest.mark.parametrize("capture_reason", [None, "Npcap/scapy unavailable for this test"])
def test_network_group_has_no_explanation_gaps(tmp_path, monkeypatch, capture_reason):
    from modules.network_diagnostics import network_tools

    monkeypatch.setattr(network_tools, "packet_capture_unavailable_reason", lambda: capture_reason)
    rows = audit_modules(str(tmp_path), "Network Diagnostics")
    expected = {"Network Diagnostics"} | {
        "Network Diagnostics > " + name for name in (
            "Health", "Network Diagnostics", "Shared Resources", "Wi-Fi Analyzer",
            "Remote Tools", "Network Extras", "Hosts Editor",
        )
    }
    assert {row["module"] for row in rows} == expected
    assert [(row["module"], row["error"], row["gaps"]) for row in rows
            if row["error"] or row["total"]] == []


def test_network_help_catalogs_are_nonempty():
    from ui.help import network_text as help_text

    for name in ("INTRO", "CARDS", "FIX_HELP", "QUICK_HELP", "CONTROL_HELP",
                 "BUTTON_HELP", "HEADERS", "CONTEXT_HEADERS"):
        catalog = getattr(help_text, name)
        assert catalog and all(isinstance(value, str) and value.strip() for value in catalog.values())


def test_repairs_distinguish_confirmation_verification_and_no_undo():
    from ui.help.network_text import FIX_HELP, QUICK_HELP

    assert "after confirmation" in FIX_HELP["reset_winsock"]
    assert "without confirmation" in QUICK_HELP["Reset Winsock"]
    for text in (FIX_HELP["reset_winsock"], QUICK_HELP["Reset Winsock"]):
        assert "restart" in text and "verified immediately" in text and "no undo" in text
    assert "without running /release" in FIX_HELP["renew_dhcp"]
    assert "all IPv4 DHCP adapters" in QUICK_HELP["IP Release"]


def test_every_built_network_tab_has_a_top_introduction(tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QLabel, QScrollArea, QTabWidget
    from ui import tooltip_audit

    def inspect(root, name):
        for tabs in ([root] if isinstance(root, QTabWidget) else []) + root.findChildren(QTabWidget):
            for index in range(tabs.count()):
                page = tabs.widget(index)
                if isinstance(page, QScrollArea):
                    page = page.widget()
                assert page.layout() is not None, (name, tabs.tabText(index))
                label = page.layout().itemAt(0).widget()
                assert isinstance(label, QLabel) and label.objectName() == "moduleIntro", (name, tabs.tabText(index))
        return inspect_widget(root, name)

    monkeypatch.setattr(tooltip_audit, "inspect_widget", inspect)
    rows = audit_modules(str(tmp_path), "Network Diagnostics")
    assert not [(row["module"], row["error"]) for row in rows if row["error"]]


def test_health_rows_include_evidence_and_escape_machine_text(qapp):
    from modules.network_diagnostics.network_health_module import NetworkHealthModule
    from modules.network_diagnostics.network_health import Finding, WARNING

    module = NetworkHealthModule()
    root = module.create_widget()
    try:
        module._health._findings = [Finding("apipa", WARNING, "Local address", "<img src=x>", items=["169.254.1.2"])]
        module._health._fill()
        tip = module._health._table.item(0, 1).toolTip()
        assert "APIPA" in tip and "169.254.1.2" in tip
        assert "&lt;img src=x&gt;" in tip and "<img src=x>" not in tip
    finally:
        module.on_stop()
        root.deleteLater()


def test_hosts_rows_created_after_construction_keep_explanations(qapp):
    from modules.hosts_editor.hosts_editor_module import HostsEditorModule

    module = HostsEditorModule()
    root = module.create_widget()
    try:
        module._add_row(True, "0.0.0.0", "example.com", "Example", -1)
        assert module._table.cellWidget(0, 0).toolTip()
        assert all(module._table.item(0, col).toolTip() for col in range(1, 4))
        assert inspect_widget(root, "Hosts Editor")["total"] == 0
    finally:
        module.on_stop()
        root.deleteLater()

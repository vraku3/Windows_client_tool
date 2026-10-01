"""Services' Depends On / Required By lists jump to the named service instead
of being read-only text -- a dependency that IS a service in the current list
is clickable; a driver or group (never in the service table) is not.
"""
from PyQt6.QtCore import Qt

from modules.services_manager.services_module import ServicesModule


def _module(qapp):
    m = ServicesModule()
    m.app = type("A", (), {"thread_pool": None})()
    m.create_widget()
    m._all_services = [
        {"Name": "RpcSs", "Display Name": "Remote Procedure Call", "Status": "Running",
         "Start Type": "Auto", "Impact": "High", "PID": "900", "Path": ""},
        {"Name": "Dhcp", "Display Name": "DHCP Client", "Status": "Running",
         "Start Type": "Auto", "Impact": "Medium", "PID": "1200", "Path": ""},
    ]
    m._apply_filter()
    return m


def test_a_known_dependency_is_clickable_and_an_unknown_one_is_not(qapp):
    m = _module(qapp)
    m._fill_service_link_list(m._detail_deps_list, [("RpcSs", ""), ("BFE", "")], "(none)")
    known = m._detail_deps_list.item(0)
    unknown = m._detail_deps_list.item(1)
    assert known.data(Qt.ItemDataRole.UserRole) == "RpcSs"
    assert unknown.data(Qt.ItemDataRole.UserRole) is None
    assert "Not a service in this list" in unknown.toolTip()


def test_double_clicking_a_known_dependency_selects_it_in_the_table(qapp, monkeypatch):
    m = _module(qapp)
    # _select_service_by_name selects a row, which fires _on_selection_changed
    # and starts a REAL worker (sc.exe) via _load_detail -- stub that one call
    # so this test checks only the jump/selection logic, not a live subprocess.
    monkeypatch.setattr(m, "_load_detail", lambda: None)
    m._fill_service_link_list(m._detail_deps_list, [("Dhcp", "")], "(none)")
    m._jump_to_dependency(m._detail_deps_list.item(0))
    assert m._get_selected_name() == "Dhcp"


def test_an_empty_dependency_list_says_so(qapp):
    m = _module(qapp)
    m._fill_service_link_list(m._detail_deps_list, [], "(no dependencies)")
    assert m._detail_deps_list.count() == 1
    assert m._detail_deps_list.item(0).text() == "(no dependencies)"


def test_required_by_shows_the_display_name_too(qapp):
    m = _module(qapp)
    m._fill_service_link_list(m._detail_rby_list, [("RpcSs", "Remote Procedure Call")], "(none)")
    assert "Remote Procedure Call" in m._detail_rby_list.item(0).text()


def test_a_disabled_dependency_is_flagged_and_still_clickable(qapp):
    """A dependency that is itself a known service AND Disabled must be
    visibly marked (Windows refuses to start the selected service while that
    holds) while staying navigable -- it is still a real row in the table."""
    from core.semantic_colors import semantic
    from PyQt6.QtGui import QColor

    m = _module(qapp)
    m._fill_service_link_list(m._detail_deps_list, [("RpcSs", "")], "(none)",
                              blocked={"rpcss"})
    item = m._detail_deps_list.item(0)
    assert "DISABLED" in item.text()
    assert item.foreground().color() == QColor(semantic("warning"))
    assert item.data(Qt.ItemDataRole.UserRole) == "RpcSs"


def test_a_dependency_not_blocked_has_no_warning_markup(qapp):
    m = _module(qapp)
    m._fill_service_link_list(m._detail_deps_list, [("RpcSs", "")], "(none)", blocked=set())
    item = m._detail_deps_list.item(0)
    assert "DISABLED" not in item.text()


def test_apply_audit_reports_blocked_dependencies(qapp):
    m = _module(qapp)
    m._table.selectRow(0)  # selects RpcSs so _get_selected_service() finds it
    m._apply_audit(None, ["BrokenDep"])
    text = m._detail_audit_value.toPlainText()
    assert "BrokenDep" in text and "cannot start" in text

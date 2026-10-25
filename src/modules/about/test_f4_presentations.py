"""Fake-data checks for F4 summaries and their unreadable states."""
import datetime
from types import SimpleNamespace as NS
import pytest
from PyQt6.QtWidgets import QApplication


@pytest.fixture(scope="module", autouse=True)
def qt():
    app = QApplication.instance() or QApplication([])
    yield app


def test_certificates_count_and_tile_filter():
    from modules.certificate_viewer.cert_module import _CertTab
    from modules.certificate_viewer.cert_reader import CertInfo
    tab = _CertTab("MY", "user", None)
    cert = CertInfo("Example", "CN=Example", "Issuer", datetime.datetime.now(), "ABC", "N/A", False, b"", -3, "",
                    sig_algorithm="sha256RSA", key_algorithm="RSA", key_size=2048)
    tab._on_result([cert])
    assert tab._glance["Expired"].value.text() == "1"
    assert tab._glance["Weak algorithm"].value.text() == "0"
    tab._glance["Expired"].clicked.emit()
    assert tab._chip == "Expired" and tab._table.rowCount() == 1
    cert.key_size = 0
    tab._on_result([cert])
    assert tab._glance["Weak algorithm"].severity == "unknown"
    tab._on_error("Store denied")
    assert all(t.reason == "Store denied" for t in tab._glance.values())
    assert tab._empty_state.variant == "error"


def test_accounts_missing_membership_is_not_zero_admins():
    from modules.local_users.users_module import LocalUsersModule
    from modules.local_users import accounts as acc
    module = LocalUsersModule()
    root = module.create_widget()
    account = acc.Account("A", "", "", acc.UF_DONT_EXPIRE_PASSWD, 1000, 0, 0, 0, "", "sid", ["Administrators"])
    module._on_result(acc.Snapshot([account], {}))
    assert module._glance["Administrators"].value.text() == "1"
    assert module._glance["Password never expires"].value.text() == "1"
    account.groups_error = "Membership denied"
    module._on_result(acc.Snapshot([account], {}))
    assert module._glance["Administrators"].severity == "unknown"
    module._on_error("Accounts denied")
    assert module._glance["All"].reason == "Accounts denied"


def test_policy_missing_scope_and_unread_drift():
    from modules.gpresult.gpresult_module import GPResultModule
    module = GPResultModule()
    root = module.create_widget()
    result = NS(error="", computer=NS(available=False), user=NS(available=True, applied_gpos=[object()]))
    module._update_glance(result, NS(files=[], results=[NS(state="different", is_drift=True)]))
    assert module._glance["computer"].severity == "unknown"
    assert module._glance["user"].value.text() == "1"
    assert module._glance["drift"].value.text() == "1"
    module._update_glance(result, NS(files=[], results=[NS(state="unreadable", is_drift=False)]))
    assert module._glance["drift"].severity == "unknown"


def test_environment_overrides_exclude_merged_path():
    from modules.env_vars.env_vars_module import EnvVarsModule
    module = EnvVarsModule()
    root = module.create_widget()
    rows = [NS(name="Path"), NS(name="TEMP")]
    module._update_glance("System", rows, "")
    assert module._glance["User overrides"].severity == "unknown"
    module._update_glance("User", [NS(name="PATH"), NS(name="temp")], "")
    assert module._glance["User overrides"].value.text() == "1"
    module._update_glance("System", None, "Denied")
    assert module._glance["System"].reason == "Denied"
    assert module._glance["User overrides"].reason == "Denied"


def test_software_update_count_requires_successful_check(monkeypatch):
    from modules.software_inventory import software_module as sm
    monkeypatch.setattr(sm, "bind_row", lambda *a, **k: None)
    pane = sm._SoftwarePane()
    pane.set_entries([])
    assert pane._glance["Apps"].value.text() == "0"
    assert pane._glance["Updates available"].severity == "unknown"
    pane._on_winget(({}, "No upgrades"))
    assert pane._glance["Updates available"].value.text() == "0"
    pane._on_load_error("Inventory denied")
    assert all(t.reason == "Inventory denied" for t in pane._glance.values())


def test_firewall_counts_require_a_readable_snapshot():
    from modules.firewall_rules.firewall_manager_module import FirewallManagerModule
    from modules.firewall_rules import firewall_audit as fa
    module = FirewallManagerModule()
    root = module.create_widget()
    rule, extra = fa.parse_registry_rule("v2.30|Action=Allow|Active=TRUE|Dir=In|Protocol=6|Name=Example|")
    module._snap = fa.Snapshot(rules=[rule], extras=[extra])
    module._on_rules_loaded([rule], fresh_snapshot=True)
    assert module._glance["all"].value.text() == "1"
    assert module._glance["any_any"].value.text() == "1"
    module._snap.error = "Registry refused"
    module._refresh_audit_view()
    assert all(t.reason == "Registry refused" for t in module._glance.values())


def test_report_starts_with_section_preview_and_generate_action(monkeypatch):
    from modules.system_report.report_module import SystemReportModule
    module = SystemReportModule()
    calls = []
    monkeypatch.setattr(module, "_do_generate", lambda: calls.append("generate"))
    root = module.create_widget()
    assert not module._report_start.isHidden()
    assert module._preview.isHidden()
    module._report_empty.action_button.click()
    assert calls == ["generate"]


def test_registry_starts_with_bookmark_actions():
    from modules.registry_explorer.registry_module import RegistryExplorerModule
    from PyQt6.QtWidgets import QPushButton
    module = RegistryExplorerModule()
    root = module.create_widget()
    assert not module._navigation_start.isHidden()
    assert module._values_table.isHidden()
    buttons = module._navigation_start.findChildren(QPushButton)
    assert buttons and all(b.toolTip() for b in buttons)


def test_quick_fix_summary_is_catalog_data_and_search_can_be_cleared():
    from modules.quick_fix.quick_fix_module import QuickFixModule
    module = QuickFixModule()
    root = module.create_widget()
    assert int(module._glance["Repair actions"].value.text()) == len(module._cards)
    assert int(module._glance["Categories"].value.text()) == len(module._category_headers)
    module._search.setText("zzzz no matching repair")
    assert not module._filter_empty.isHidden()
    module._filter_empty.action_button.click()
    assert module._search.text() == "" and module._filter_empty.isHidden()


def test_about_shows_real_paths_and_discloses_missing_build_date():
    from modules.about.about_module import AboutModule
    from PyQt6.QtWidgets import QLabel
    module = AboutModule()
    module.app = NS(app_data_dir="C:/ExampleData")
    root = module.create_widget()
    texts = [label.text() for label in root.findChildren(QLabel)]
    assert "C:/ExampleData" in texts
    assert "Not recorded in this build" in texts

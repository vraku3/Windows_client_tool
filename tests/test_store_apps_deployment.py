"""Store Apps: deployment failures, NonRemovable/SignatureKind, refusal paths.

Synthetic events are copied from this machine's real
AppXDeploymentServer/Operational log (2026-10-09); the real-machine tests read
the live log and the live Get-AppxPackage output, unelevated.
"""
import subprocess
from datetime import datetime, timezone

import pytest

from core import appx_service
from modules.store_apps import deployment_failures as df
from modules.store_apps.appx_errors import (
    PACKAGES_IN_USE, describe, first_hresult, parse_hresult,
)
from modules.store_apps.package_info import (
    NON_REMOVABLE_REASON, SOURCE_SIDELOADED, package_source,
)
from modules.store_apps.store_apps_module import is_system_package

_HDR = "<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'>"


def _ev(event_id, when, sid="S-1-5-21-1-1001", **data):
    items = "".join(f"<Data Name='{k}'>{v}</Data>" for k, v in data.items())
    return (f"{_HDR}<System><EventID>{event_id}</EventID>"
            f"<TimeCreated SystemTime='{when}'/>"
            f"<Security UserID='{sid}'/></System>"
            f"<EventData>{items}</EventData></Event>")


def _fail(when, full, code="0x80073d02", caller="svchost.exe,wuauserv",
          summary="error 0x80073D02: Unable to install because the following "
                  "apps need to be closed X."):
    return _ev(404, when, SummaryError=summary, PackageFullName=full,
               ErrorCode=code, CallingProcess=caller)


def _ok(when, full, op="6"):
    return _ev(400, when, DeploymentOperation=op, PackageFullName=full)


CROSS = "MicrosoftWindows.CrossDevice_1.26082.74.0_neutral_~_cw5n1h2txyewy"
SKETCH = "Microsoft.ScreenSketch_2022.2608.41.0_neutral_~_8wekyb3d8bbwe"
WHATS = "5319275A.WhatsAppDesktop_2.2639.100.0_neutral_~_cv1g1gvanyjgm"
MDODR = "MdOdrMcpFilterPackage_1.0.0.0_neutral__cw5n1h2txyewy"

INSTALLED = {
    "MicrosoftWindows.CrossDevice": "1.26072.116.0",
    "Microsoft.ScreenSketch": "11.2608.41.0",
    "5319275A.WhatsAppDesktop": "2.2639.100.0",
}


# ---------------------------------------------------------------- errors

def test_hresult_parsing_and_meaning():
    assert parse_hresult("0x80073d02") == PACKAGES_IN_USE
    assert parse_hresult("") is None
    assert describe(0x80073CFB)[0] == "ERROR_PACKAGE_ALREADY_EXISTS"
    assert describe(0x80073D02)[0] == "ERROR_PACKAGES_IN_USE"
    name, meaning = describe(0x12345678)
    assert name == "" and "not an AppX deployment code" in meaning
    assert first_hresult("HRESULT 0x00000001 then 0x80073CFA") == 0x80073CFA


# --------------------------------------------------------- package info

def test_non_removable_flag_protects_settings_and_security():
    # Both live outside SystemApps; only Windows' own flag catches them.
    assert is_system_package("windows.immersivecontrolpanel",
                             r"C:\Windows\ImmersiveControlPanel", True)
    assert is_system_package(
        "Microsoft.SecHealthUI",
        r"C:\Program Files\WindowsApps\microsoft.sechealthui_1000_x64__8wekyb3d8bbwe",
        True)
    assert not is_system_package("windows.immersivecontrolpanel",
                                 r"C:\Windows\ImmersiveControlPanel", False)
    # The Store is not NonRemovable but stays protected by name.
    assert is_system_package("Microsoft.WindowsStore", r"C:\Program Files\x", False)
    assert "0x80073CFA" in NON_REMOVABLE_REASON


def test_package_source_labels():
    assert package_source("Developer")[0] == SOURCE_SIDELOADED
    assert package_source("Store")[0] == "Store"
    assert package_source("System")[0] == "Windows"
    assert package_source(None)[0] == ""
    assert package_source("Weird")[0] == "Weird"


# ------------------------------------------------------ failure parsing

def test_split_full_name_full_family_and_empty():
    assert df.split_full_name(CROSS) == ("MicrosoftWindows.CrossDevice", "1.26082.74.0")
    assert df.split_full_name("Microsoft.Edge.GameAssist_8wekyb3d8bbwe") == (
        "Microsoft.Edge.GameAssist", "")
    assert df.split_full_name("") == ("", "")
    assert df.is_bundle(CROSS) and not df.is_bundle(MDODR)


def test_parse_events_reads_structured_fields():
    xml = _fail("2026-10-09T06:05:19.8177180Z", CROSS)
    (f,) = df.parse_events_xml(xml)
    assert f.package_name == "MicrosoftWindows.CrossDevice"
    assert f.attempted_version == "1.26082.74.0"
    assert f.code == PACKAGES_IN_USE
    assert f.caller == "svchost.exe,wuauserv"
    assert f.when == datetime(2026, 10, 9, 6, 5, 19, 817718, tzinfo=timezone.utc)


def test_bundle_version_mismatch_is_resolved_only_by_a_later_success():
    """ScreenSketch's bundle 2022.x failed against installed 11.x: by version
    it looks pending; the later Register 400 says it landed."""
    fails = df.parse_events_xml(_fail("2026-10-07T06:57:16Z", SKETCH))
    succ = df.parse_success_xml(_ok("2026-10-07T09:43:08Z", SKETCH))
    (g,) = df.group_failures(fails, INSTALLED, succ)
    assert g.state == df.STATE_RESOLVED and g.resolved_at is not None
    (g,) = df.group_failures(fails, INSTALLED, {})
    assert g.state == df.STATE_PENDING


def test_success_before_the_failure_does_not_resolve_it():
    fails = df.parse_events_xml(_fail("2026-10-09T06:05:19Z", CROSS))
    # A Stage success (op 4) right before, and an old Register -- neither counts.
    succ = df.parse_success_xml(_ok("2026-10-09T06:05:18Z", CROSS, op="4")
                                + _ok("2026-10-01T00:00:00Z", CROSS))
    (g,) = df.group_failures(fails, INSTALLED, succ)
    assert g.state == df.STATE_PENDING
    assert g.installed_version == "1.26072.116.0"


def test_repeats_group_and_same_version_installed_is_resolved():
    xml = (_fail("2026-10-08T09:41:01Z", WHATS, caller="a")
           + _fail("2026-10-08T13:56:37Z", WHATS, caller="b"))
    (g,) = df.group_failures(df.parse_events_xml(xml), INSTALLED, {})
    assert g.count == 2 and g.caller == "b"
    assert g.first < g.last
    assert g.state == df.STATE_RESOLVED


def test_not_installed_unknown_and_ordering():
    xml = (_fail("2026-10-09T05:48:55Z", MDODR, code="0x80073cf9")
           + _fail("2026-10-07T06:48:46Z", "", code="0x80073cf8")
           + _fail("2026-10-07T06:17:47Z", "Microsoft.Edge.GameAssist_8wekyb3d8bbwe",
                   code="0x80070005")
           + _fail("2026-10-09T06:05:19Z", CROSS)
           + _fail("2026-10-08T13:56:37Z", WHATS))
    groups = df.group_failures(df.parse_events_xml(xml), INSTALLED, {})
    states = [(g.package_name, g.state) for g in groups]
    assert states[0] == ("MicrosoftWindows.CrossDevice", df.STATE_PENDING)
    assert ("MdOdrMcpFilterPackage", df.STATE_NOT_INSTALLED) in states
    assert ("", df.STATE_UNKNOWN) in states
    assert ("Microsoft.Edge.GameAssist", df.STATE_UNKNOWN) in states
    assert states[-1][1] == df.STATE_RESOLVED
    blank = next(g for g in groups if not g.package_name)
    assert blank.display_name.startswith("(no package")


def test_refused_log_read_is_an_error_not_an_empty_list(monkeypatch):
    monkeypatch.setattr(df, "_wevtutil", lambda args, timeout=20: (
        5, "", "Access is denied.\r\n\nFailed to open event query.\r\nAccess is denied.\r\n"))
    report = df.read_failures(INSTALLED)
    assert report.groups == []
    assert "Access is denied" in report.read_error


def test_success_read_failure_falls_back_to_versions(monkeypatch):
    calls = []

    def fake(args, timeout=20):
        calls.append(args[0])
        if "EventID=404" in args[0]:
            return 0, _fail("2026-10-08T13:56:37Z", WHATS), ""
        return 5, "", "Access is denied."
    monkeypatch.setattr(df, "_wevtutil", fake)
    report = df.read_failures(INSTALLED)
    assert report.read_error == ""
    assert report.groups[0].state == df.STATE_RESOLVED  # same version installed
    assert report.log_since is None


# --------------------------------------------------- appx_service refusal

def test_scope_is_none_when_every_attempt_is_refused(monkeypatch):
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: False)
    monkeypatch.setattr(appx_service, "_run_ps_bounded", lambda cmd, timeout=60: (
        1, "", "Get-AppxPackage : Access is denied."))
    assert appx_service.fetch_packages_with_scope_or_none() is None
    assert appx_service._enumerate() is None


def test_scope_reports_per_user_fallback(monkeypatch):
    monkeypatch.setattr("core.admin_utils.is_admin", lambda: False)
    outputs = iter([(1, "", "Access is denied."),
                    (0, '[{"Name":"A","IsFramework":false}]', "")])
    monkeypatch.setattr(appx_service, "_run_ps_bounded",
                        lambda cmd, timeout=60: next(outputs))
    packages, all_users = appx_service.fetch_packages_with_scope_or_none()
    assert [p["Name"] for p in packages] == ["A"] and all_users is False


def test_select_projects_enums_as_strings_and_reads_non_removable():
    assert "[string]$_.SignatureKind" in appx_service._SELECT
    assert "NonRemovable" in appx_service._SELECT


def test_module_scan_raises_on_unreadable_list_instead_of_empty(qapp, tmp_path, monkeypatch):
    """A refused Get-AppxPackage used to land as "No Store apps found"."""
    from modules.store_apps import store_apps_module as sam
    monkeypatch.setattr(sam, "fetch_packages_with_scope_or_none", lambda: None)
    mod = sam.StoreAppsModule()
    mod.on_start(_fake_app(tmp_path))
    mod.create_widget()
    errors = []
    mod._on_load_error = errors.append
    started = []
    mod.app.thread_pool = type("P", (), {"start": lambda self, w: started.append(w)})()
    mod._load_apps()
    worker = started[0]
    worker.signals.error.connect(errors.append)
    worker.run()
    assert errors and "unknown, not empty" in str(errors[0])


# ------------------------------------------------------------- real machine

@pytest.mark.skipif(not hasattr(subprocess, "CREATE_NO_WINDOW"), reason="Windows only")
def test_real_get_appxpackage_fields_are_strings_and_flags():
    scoped = appx_service.fetch_packages_with_scope_or_none()
    assert scoped is not None, "Get-AppxPackage could not be read unelevated"
    packages, _ = scoped
    assert packages, "Get-AppxPackage returned nothing on a real Windows install"
    archs = {p.get("Architecture") for p in packages}
    assert all(isinstance(a, str) for a in archs), archs   # not enum numbers
    kinds = {p.get("SignatureKind") for p in packages}
    assert kinds <= {"Store", "System", "Developer", "Enterprise", "None"}, kinds
    settings = [p for p in packages if p["Name"] == "windows.immersivecontrolpanel"]
    if settings:
        assert settings[0]["NonRemovable"] is True
        assert is_system_package(settings[0]["Name"], settings[0]["InstallLocation"],
                                 settings[0]["NonRemovable"])


@pytest.mark.skipif(not hasattr(subprocess, "CREATE_NO_WINDOW"), reason="Windows only")
def test_real_deployment_log_reads_unelevated():
    packages, _ = appx_service.fetch_packages_with_scope_or_none()
    installed = {p["Name"]: p["Version"] for p in packages}
    report = df.read_failures(installed)
    assert report.read_error == "", report.read_error
    assert report.log_since is not None
    assert sum(g.count for g in report.groups) == report.events
    for g in report.groups:
        assert g.state in df.STATE_LABELS
        if g.state == df.STATE_PENDING:
            assert g.package_name in installed


# ------------------------------------------------------------------- UI

def _report():
    xml = (_fail("2026-10-09T06:05:19Z", CROSS)
           + _fail("2026-10-08T13:56:37Z", WHATS))
    groups = df.group_failures(df.parse_events_xml(xml), INSTALLED, {})
    return df.FailureReport(groups=groups, events=2,
                            log_since=datetime(2026, 10, 2, tzinfo=timezone.utc))


def test_failures_panel_rows_and_refusal(qapp):
    from modules.store_apps.failures_panel import DeploymentFailuresPanel
    panel = DeploymentFailuresPanel()
    panel.show_report(_report())
    assert panel.table.rowCount() == 2
    assert panel.table.item(0, 0).text() == "Still pending"
    assert panel.table.item(0, 2).text() == "0x80073D02"
    panel.show_report(df.FailureReport(read_error="Could not read X: Access is denied."))
    assert panel._stack.currentIndex() == 1
    assert panel._stack.widget(1).title == "Could not read the deployment log"
    panel.show_report(df.FailureReport(log_since=datetime(2026, 10, 2, tzinfo=timezone.utc)))
    assert panel._stack.widget(1).title == "No failed installs or updates"


def _fake_app(tmp_path):
    from PyQt6.QtCore import QThreadPool

    from core.backup_service import BackupService
    from core.config_manager import ConfigManager

    class FakeApp:
        pass

    FakeApp.backup = BackupService(str(tmp_path))
    FakeApp.config = ConfigManager(str(tmp_path), {"version": 1})
    FakeApp.config.load()
    FakeApp.thread_pool = QThreadPool.globalInstance()
    return FakeApp


def test_module_marks_pending_update_source_and_arch(qapp, tmp_path):
    from modules.store_apps.store_apps_module import StoreAppsModule

    mod = StoreAppsModule()
    mod.on_start(_fake_app(tmp_path))
    mod.create_widget()
    apps = [
        {"Name": "MicrosoftWindows.CrossDevice", "Version": "1.26072.116.0",
         "InstallLocation": r"C:\Program Files\WindowsApps\x", "SignatureKind": "System",
         "Architecture": "X64", "NonRemovable": False},
        {"Name": "NotepadPlusPlus", "Version": "1.0.0.0",
         "InstallLocation": r"C:\Program Files\WindowsApps\y", "SignatureKind": "Developer",
         "Architecture": "Neutral", "NonRemovable": False},
        {"Name": "windows.immersivecontrolpanel", "Version": "10.0.6.1000",
         "InstallLocation": r"C:\Windows\ImmersiveControlPanel", "SignatureKind": "System",
         "Architecture": "Neutral", "NonRemovable": True},
    ]
    mod._on_load_result((apps, None, False, _report()), None)
    rows = {mod._table.item(r, 0).data(256): r for r in range(mod._table.rowCount())}
    cross = rows["MicrosoftWindows.CrossDevice"]
    assert mod._table.item(cross, 2).text().endswith("⚠")
    assert mod._table.item(cross, 6).text() == "X64"
    assert mod._table.item(rows["windows.immersivecontrolpanel"], 4).text() == "❌ System"
    assert mod._tabs.tabText(1).endswith("(1 pending)")
    assert not mod._scope_label.isHidden()

    mod._filter_combo.setCurrentIndex(3)  # Sideloaded
    visible = [mod._table.item(r, 0).data(256) for r in range(mod._table.rowCount())
               if not mod._table.isRowHidden(r)]
    assert visible == ["NotepadPlusPlus"]

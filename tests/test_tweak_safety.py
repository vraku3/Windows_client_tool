import json
from unittest.mock import MagicMock

import pytest

from core.backup_service import BackupService
from modules.tweaks.safety_policy import SafetyPolicy
from modules.tweaks.tweak_engine import TweakEngine, NOT_APPLIED
from modules.tweaks.tweaks_module import TweakRow, TweakTab, TweaksModule


def registry_step(key=r"HKCU\Software\Test", value="V", data=1):
    return {"type": "registry", "key": key, "value": value, "data": data, "kind": "DWORD"}


def tweak(tid="test", steps=None):
    return {
        "id": tid,
        "name": tid,
        "description": "Test",
        "risk": "low",
        "steps": steps or [registry_step()],
    }


def policy_file(tmp_path, machine="pc"):
    path = tmp_path / "tweak_safety.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "machine": machine,
                "blocks": [
                    {
                        "tweak_id": "incident",
                        "reason": "Recovered after stability incident",
                        "steps": [registry_step()],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "step",
    [
        {"type": "service", "name": "LicenseManager", "start_type": "disabled"},
        {"type": "service", "name": "pimindexmaintenancesvc_abc", "start_type": "disabled"},
        {"type": "service", "name": "amd3dvcacheSvc", "start_type": "disabled"},
        registry_step(
            r"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\LicenseManager", "Start", 4
        ),
        {
            "type": "registry_delete",
            "key": r"HKLM\SYSTEM\CurrentControlSet\Services\RpcSs",
            "value": "Start",
        },
    ],
)
def test_protected_service_disable_is_refused_even_with_another_tweak_id(step):
    assert SafetyPolicy().reason(tweak("alias", [step]))


def test_service_restoration_and_unrelated_privacy_change_are_allowed():
    policy = SafetyPolicy()
    assert not policy.reason(
        tweak("restore", [{"type": "service", "name": "LicenseManager", "start_type": "manual"}])
    )
    assert not policy.reason(tweak())


def test_recovered_change_is_blocked_by_id_and_normalized_operation(tmp_path):
    policy = SafetyPolicy.from_file(policy_file(tmp_path), machine_name="PC")
    assert "stability" in policy.reason(tweak("incident"))
    assert policy.reason(
        tweak("renamed", [registry_step(r"hkey_current_user/software/TEST/", "v", 1)])
    )
    assert not policy.reason(tweak("different", [registry_step(data=0)]))


def test_machine_quarantine_does_not_claim_another_pc_is_affected(tmp_path):
    assert not SafetyPolicy.from_file(policy_file(tmp_path), machine_name="different").reason(
        tweak("incident")
    )


@pytest.mark.parametrize(
    "payload", ["{broken", "{}", '{"schema_version":999,"machine":"pc","blocks":[]}']
)
def test_unreadable_or_invalid_policy_refuses_apply_instead_of_silently_ignoring(tmp_path, payload):
    path = tmp_path / "tweak_safety.json"
    path.write_text(payload, encoding="utf-8")
    assert "policy" in SafetyPolicy.from_file(path, machine_name="pc").reason(tweak()).lower()


def test_engine_rejects_entire_tweak_before_any_step_or_backup(tmp_path, monkeypatch):
    backup = BackupService(str(tmp_path))
    engine = TweakEngine(backup)
    apply = MagicMock()
    monkeypatch.setattr(engine, "_apply_step", apply)
    errors = []
    unsafe = tweak(
        "custom",
        [registry_step(), {"type": "service", "name": "LicenseManager", "start_type": "disabled"}],
    )
    assert engine.apply_tweak(unsafe, "unused", on_error=errors.append) is False
    apply.assert_not_called()
    assert errors and "licens" in errors[0].lower()
    backup._conn.close()


def test_engine_loads_quarantine_from_its_own_backup_directory(tmp_path, monkeypatch):
    import platform

    policy_file(tmp_path, machine=platform.node())
    backup = BackupService(str(tmp_path))
    engine = TweakEngine(backup)
    assert engine.safety_reason(tweak("incident"))
    backup._conn.close()


def test_protected_row_stays_unselectable_after_status_refresh(qapp):
    row = TweakRow(tweak())
    row.set_safety_reason("Protected after recovery")
    row.set_status(NOT_APPLIED, "Value absent")
    row.set_checked(True)
    assert not row.is_checked
    assert not row.apply_btn.isEnabled()
    assert "recovery" in row.apply_btn.toolTip()
    assert row.status == NOT_APPLIED


def test_imported_preset_and_select_all_skip_protected_rows(qapp):
    tab = TweakTab(
        [tweak("blocked"), tweak("allowed", [registry_step(data=0)])],
        safety_reasons={"blocked": "Recovered change"},
    )
    tab.apply_preset(["blocked", "allowed"])
    assert [t["id"] for t in tab.selected_tweaks()] == ["allowed"]
    tab.select_all()
    assert [t["id"] for t in tab.selected_tweaks()] == ["allowed"]


def test_direct_single_handler_refuses_before_restore_point(qapp, monkeypatch):
    from PyQt6.QtWidgets import QWidget, QMessageBox

    module = TweaksModule()
    module.app = MagicMock()
    module._widget = QWidget()
    module._engine = MagicMock()
    module._engine.safety_reason.return_value = "Blocked after recovery"
    warning = MagicMock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    module._on_apply_single_tweak(tweak())
    module.app.backup.create_restore_point.assert_not_called()
    warning.assert_called_once()


def test_details_panel_stays_blocked_when_detection_arrives(qapp):
    from modules.tweaks.tweaks_module import _DetailsPanel

    panel = _DetailsPanel()
    panel.set_safety_reasons({"incident": "Recovered change"})
    panel.set_tweak(tweak("incident"))
    panel.update_status("incident", NOT_APPLIED, "Default value")
    assert not panel._apply_btn.isEnabled()
    assert "Recovered change" in panel._status.text()


def test_bulk_handler_refuses_protected_selection_before_any_backup(qapp, monkeypatch):
    from PyQt6.QtWidgets import QWidget, QMessageBox

    module = TweaksModule()
    module.app = MagicMock()
    module._widget = QWidget()
    module._tab_widgets = {"Test": MagicMock(selected_tweaks=lambda: [tweak()])}
    module._engine = MagicMock()
    module._engine.safety_reason.return_value = "Protected"
    monkeypatch.setattr(QMessageBox, "warning", MagicMock())
    module._on_apply()
    module.app.backup.create_restore_point.assert_not_called()


def test_quarantine_tool_requires_verified_recovery_history(tmp_path):
    import sqlite3
    from tools.protect_recovered_tweaks import make_blocks

    db = sqlite3.connect(tmp_path / "tweaks.db")
    db.execute(
        "CREATE TABLE tweak_steps (id TEXT,tweak_id TEXT,before_value TEXT,after_value TEXT,step_type TEXT,target TEXT,value_name TEXT,reg_kind INTEGER,reverted_at TEXT,revert_error TEXT)"
    )
    db.execute(
        "INSERT INTO tweak_steps VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("step", "tweak", "null", "1", "registry", r"HKCU\Software\Test", "V", 4, None, None),
    )
    db.commit()
    plan = [{"id": "step", "restore_value": None}]
    with pytest.raises(ValueError, match="successfully recorded"):
        make_blocks(tmp_path, plan, "test recovery")
    db.execute("UPDATE tweak_steps SET reverted_at='2026-10-31'")
    db.commit()
    blocks = make_blocks(tmp_path, plan, "test recovery")
    assert blocks[0]["tweak_id"] == "tweak"
    assert blocks[0]["steps"][0]["data"] == 1
    assert "no individual cause" in blocks[0]["reason"]
    db.execute(
        "INSERT INTO tweak_steps VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "step2",
            "tweak",
            "null",
            "2",
            "registry",
            r"HKCU\Software\Test",
            "Other",
            4,
            "2026-10-31",
            None,
        ),
    )
    db.commit()
    combined = make_blocks(
        tmp_path, plan + [{"id": "step2", "restore_value": None}], "test recovery"
    )
    assert len(combined) == 1
    assert len(combined[0]["steps"]) == 2
    db.close()


def test_blocked_filter_keeps_safety_separate_from_detected_state(qapp):
    tab = TweakTab(
        [tweak("blocked"), tweak("allowed")], safety_reasons={"blocked": "Recovered change"}
    )
    module = TweaksModule()
    module._tab_widgets = {"Test": tab}
    module._search_input = MagicMock()
    module._search_input.text.return_value = ""
    module._risk_filter = MagicMock()
    module._risk_filter.currentText.return_value = "All"
    module._status_filter = MagicMock()
    module._status_filter.currentText.return_value = "Blocked for Safety"
    module._filter_tweaks()
    assert not tab._rows["blocked"].isHidden()
    assert tab._rows["allowed"].isHidden()

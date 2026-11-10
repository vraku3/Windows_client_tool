"""Tests for sysadmin security checks: SMB server signing, driver blocklist, and LSASS protection."""
import pytest
from modules.security_dashboard import security_reader


@pytest.fixture
def registry(monkeypatch):
    """A fake registry: {(key, value): data}, everything else absent."""
    values = {}

    def read(key, value, kind="REG_DWORD"):
        return values.get((key, value))

    monkeypatch.setattr(security_reader, "_reg_read", read)
    return values


def test_smb_client_signing_required(registry):
    key = r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters"
    registry[(key, "RequireSecuritySignature")] = 1

    result = security_reader.check_smb_signing()
    assert result["enabled"] is True
    assert result["status"] == "Required"
    assert result["color"] == "green"


def test_smb_client_signing_not_required(registry):
    key = r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters"
    registry[(key, "RequireSecuritySignature")] = 0

    result = security_reader.check_smb_signing()
    assert result["enabled"] is False
    assert result["status"] == "Not Required"
    assert result["color"] == "red"


def test_smb_server_signing_required(registry):
    key = r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
    registry[(key, "RequireSecuritySignature")] = 1

    result = security_reader.check_smb_server_signing()
    assert result["enabled"] is True
    assert result["status"] == "Required"
    assert result["color"] == "green"


def test_smb_server_signing_not_required(registry):
    key = r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters"
    registry[(key, "RequireSecuritySignature")] = 0

    result = security_reader.check_smb_server_signing()
    assert result["enabled"] is False
    assert result["status"] == "Not Required"
    assert result["color"] == "red"


def test_vulnerable_driver_blocklist_enabled(registry):
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\CI\Config"
    registry[(key, "VulnerableDriverBlocklistEnable")] = 1

    result = security_reader.check_vulnerable_driver_blocklist()
    assert result["enabled"] is True
    assert result["status"] == "Enabled"
    assert result["color"] == "green"


def test_vulnerable_driver_blocklist_disabled(registry):
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\CI\Config"
    registry[(key, "VulnerableDriverBlocklistEnable")] = 0

    result = security_reader.check_vulnerable_driver_blocklist()
    assert result["enabled"] is False
    assert result["status"] == "Disabled"
    assert result["color"] == "red"


def test_lsass_protection_via_runaspplboot(registry):
    key = r"HKLM\SYSTEM\CurrentControlSet\Control\Lsa"
    # RunAsPPL is missing, but Windows 11 RunAsPPLBoot is 2
    registry[(key, "RunAsPPL")] = None
    registry[(key, "RunAsPPLBoot")] = 2

    result = security_reader.check_lsass_protection()
    assert result["enabled"] is True
    assert "Protected" in result["status"]
    assert result["color"] == "green"


def test_smb_guest_auth_default_is_disabled(registry):
    # None = default, modern Windows disables insecure guest auth
    result = security_reader.check_smb_guest_auth()
    assert result["enabled"] is False
    assert result["color"] == "green"
    assert "Disabled" in result["status"]


def test_smb_guest_auth_enabled_is_red(registry):
    key = r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters"
    registry[(key, "AllowInsecureGuestAuth")] = 1

    result = security_reader.check_smb_guest_auth()
    assert result["enabled"] is True
    assert result["status"] == "Enabled"
    assert result["color"] == "red"


def test_smb_guest_auth_disabled_is_green(registry):
    key = r"HKLM\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters"
    registry[(key, "AllowInsecureGuestAuth")] = 0

    result = security_reader.check_smb_guest_auth()
    assert result["enabled"] is False
    assert result["status"] == "Disabled"
    assert result["color"] == "green"


def test_wpad_machine_policy_disables_wpad(registry):
    key = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\WinHttp"
    registry[(key, "DisableWpad")] = 1

    result = security_reader.check_wpad()
    assert result["enabled"] is False
    assert result["color"] == "green"
    assert "Disabled (machine policy)" in result["status"]


def test_netbios_per_adapter_audit(monkeypatch, registry):
    node_key = r"HKLM\SYSTEM\CurrentControlSet\Services\NetBT\Parameters"
    registry[(node_key, "NodeType")] = 2  # P-node

    # Fake 2 interfaces: 1 disabled, 1 default
    monkeypatch.setattr(
        security_reader,
        "_reg_enum_subkeys",
        lambda k: ["Tcpip_{GUID1}", "Tcpip_{GUID2}"] if "Interfaces" in k else []
    )
    registry[(rf"{node_key}\Interfaces\Tcpip_{{GUID1}}", "NetbiosOptions")] = 2
    registry[(rf"{node_key}\Interfaces\Tcpip_{{GUID2}}", "NetbiosOptions")] = 0

    result = security_reader.check_netbios_tcpip()
    assert result["status"] == "P-node: no broadcast name resolution"
    assert result["color"] == "green"
    details_str = str(result["details"])
    assert "Active on 1/2 interfaces (1 disabled)" in details_str


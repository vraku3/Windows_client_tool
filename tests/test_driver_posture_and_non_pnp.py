"""Unit tests for driver manager kernel posture and non-PnP kernel driver audit."""
import os
import winreg
import pytest
from modules.driver_manager.driver_reader import (
    check_kernel_posture,
    check_testsigning_status,
    _resolve_driver_path,
    fetch_non_pnp_drivers,
    KernelPosture,
)


def test_resolve_driver_path(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")

    # \SystemRoot\ prefix
    assert _resolve_driver_path(r"\SystemRoot\System32\drivers\afd.sys", "afd") == os.path.normpath(
        r"C:\Windows\System32\drivers\afd.sys"
    )

    # system32\ relative prefix
    assert _resolve_driver_path(r"System32\drivers\null.sys", "null") == os.path.normpath(
        r"C:\Windows\System32\drivers\null.sys"
    )

    # \??\ NT prefix
    assert _resolve_driver_path(r"\??\C:\tools\driver.sys", "test") == os.path.normpath(
        r"C:\tools\driver.sys"
    )

    # Missing ImagePath defaults to System32\drivers\<svc>.sys
    assert _resolve_driver_path(None, "custom_svc") == os.path.normpath(
        r"C:\Windows\System32\drivers\custom_svc.sys"
    )


def test_check_kernel_posture_clean(monkeypatch):
    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def fake_query(key, name):
        if name == "SystemStartOptions":
            return (" NOEXECUTE=OPTIN  FVEBOOT=2666496  NOVGA", 1)
        if name == "VulnerableDriverBlocklistEnable":
            return (1, 4)
        raise FileNotFoundError

    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query)

    posture = check_kernel_posture()
    assert isinstance(posture, KernelPosture)
    assert posture.testsigning is False
    assert posture.no_integrity_checks is False
    assert posture.vulnerable_driver_blocklist is True
    assert posture.is_degraded is False
    assert posture.warnings == []


def test_check_kernel_posture_testsigning(monkeypatch):
    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def fake_query(key, name):
        if name == "SystemStartOptions":
            return (" TESTSIGNING  NOEXECUTE=OPTIN ", 1)
        if name == "VulnerableDriverBlocklistEnable":
            return (1, 4)
        raise FileNotFoundError

    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query)

    posture = check_kernel_posture()
    assert posture.testsigning is True
    assert posture.is_degraded is True
    assert any("Testsigning" in w for w in posture.warnings)


def test_check_kernel_posture_nointegritychecks(monkeypatch):
    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def fake_query(key, name):
        if name == "SystemStartOptions":
            return (" DISABLE_INTEGRITY_CHECKS  NOEXECUTE=OPTIN ", 1)
        if name == "VulnerableDriverBlocklistEnable":
            return (1, 4)
        raise FileNotFoundError

    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query)

    posture = check_kernel_posture()
    assert posture.no_integrity_checks is True
    assert posture.is_degraded is True
    assert any("Signature Enforcement is DISABLED" in w for w in posture.warnings)


def test_check_kernel_posture_vdb_disabled(monkeypatch):
    class FakeKey:
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def fake_query(key, name):
        if name == "SystemStartOptions":
            return (" NOEXECUTE=OPTIN ", 1)
        if name == "VulnerableDriverBlocklistEnable":
            return (0, 4)  # Disabled
        raise FileNotFoundError

    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: FakeKey())
    monkeypatch.setattr(winreg, "QueryValueEx", fake_query)

    posture = check_kernel_posture()
    assert posture.vulnerable_driver_blocklist is False
    assert posture.is_degraded is True
    assert any("Vulnerable Driver Blocklist is DISABLED" in w for w in posture.warnings)

    compat = check_testsigning_status()
    assert compat["is_degraded"] is True


def test_live_kernel_posture():
    posture = check_kernel_posture()
    assert isinstance(posture, KernelPosture)
    assert isinstance(posture.raw_options, str)
    assert posture.refusal_reason is None


def test_live_non_pnp_drivers_enumeration():
    # Fast scan without live Authenticode checking to avoid slow catalog parsing in test
    drivers = fetch_non_pnp_drivers(verify_signatures=False)
    assert isinstance(drivers, list)
    assert len(drivers) > 50  # Windows systems have hundreds of kernel services
    first = drivers[0]
    assert first.driver_class in ("Kernel Driver", "File System Driver")
    assert first.device_id.startswith("SERVICE\\")


"""Device, boot, service and feature readers, as Task 8 binds them.

Three readers rendered a status but returned nothing the catalog could compare
against, and one pair of readers asks the machine the *same question twice*:

  check_autorun          only the "Disabled (All)" branch returned `enabled`,
      so a machine with AutoRun partly or fully ON read None -- unknown --
      rather than "AutoRun can run".
  check_hvci             returned no `enabled` and no `available` at all, on
      any path, so a control bound to it could never read a value.
  check_security_log_size / check_system_log_size  rendered "N MB" as text
      with no number behind it.

  check_core_isolation_summary and check_memory_integrity_registry read the
      SAME registry value (DeviceGuard\\Scenarios\\HypervisorEnforcedCodeIntegrity
      \\Enabled) and differ only in their labels. One is bound; the other is
      named in NOT_A_CONTROL as its duplicate, so the catalog cannot grow two
      cards that write one value.
"""
import pytest

from modules.security_dashboard import security_reader
from modules.security_dashboard import snapshots


@pytest.fixture(autouse=True)
def _elevated(monkeypatch):
    """These tests describe what the readers do with an ANSWER.

    Unelevated, two of them are no longer asked at all: the
    MicrosoftVolumeEncryption and MicrosoftTpm namespaces refuse an ordinary
    user at a fixed ~5s, and Get-BitLockerVolume takes 5.41s to refuse by
    exiting 0 with empty stdout, so all three are pre-empted (see
    test_security_unelevated_reads.py). Feeding a reader canned output only
    means anything in the state where it runs the command.
    """
    monkeypatch.setattr(security_reader, "is_admin", lambda: True)

@pytest.fixture
def registry(monkeypatch):
    values = {}

    def read(key, value, kind=None):
        return values.get((key, value))

    monkeypatch.setattr(security_reader, "_reg_read", read)
    return values


@pytest.fixture
def powershell(monkeypatch):
    rules = []

    def fake_ps(cmd, timeout=30):
        for needle, response in rules:
            if needle in cmd:
                return response
        return 1, "", "no rule for this command"

    monkeypatch.setattr(security_reader, "_ps", fake_ps)
    return rules


_AUTORUN_KEY = (r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion"
                r"\Policies\Explorer")
_HVCI_KEY = (r"HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios"
             r"\HypervisorEnforcedCodeIntegrity")


# -- check_autorun -----------------------------------------------------------

def test_autorun_not_configured_reads_as_able_to_run(registry):
    result = security_reader.check_autorun()

    assert result["enabled"] is True, (
        "no policy means AutoRun is not disabled, which is what the card's "
        "own red status already said")
    assert result["available"] is True


def test_autorun_disabled_everywhere_reads_false(registry):
    registry[(_AUTORUN_KEY, "NoDriveTypeAutoRun")] = 255

    assert security_reader.check_autorun()["enabled"] is False


def test_autorun_partly_disabled_still_reads_as_able_to_run(registry):
    registry[(_AUTORUN_KEY, "NoDriveTypeAutoRun")] = 145

    result = security_reader.check_autorun()

    assert result["enabled"] is True
    assert result["color"] == "amber"


# -- check_usb_storage --------------------------------------------------------
# This reader goes straight through winreg.OpenKey/QueryValueEx rather than
# the `_reg_read` helper (it needs to tell "key absent" apart from "access
# denied", which `_reg_read` collapses into one None), so it needs its own
# fake rather than the `registry` fixture above.

class _FakeUsbstorKey:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_usbstor_start(monkeypatch, value):
    monkeypatch.setattr(security_reader.winreg, "OpenKey",
                        lambda hive, sub: _FakeUsbstorKey())
    monkeypatch.setattr(security_reader.winreg, "QueryValueEx",
                        lambda handle, name: (value, 4))


def test_usb_storage_start_3_reads_allowed(monkeypatch):
    _patch_usbstor_start(monkeypatch, 3)

    result = security_reader.check_usb_storage()

    assert result["available"] is True
    assert result["enabled"] is True, "Start=3 is the permissive default"
    assert result["color"] == "amber"


def test_usb_storage_start_4_reads_blocked(monkeypatch):
    _patch_usbstor_start(monkeypatch, 4)

    result = security_reader.check_usb_storage()

    assert result["enabled"] is False
    assert result["color"] == "green"


def test_usb_storage_missing_service_key_is_a_definite_state(monkeypatch):
    """No USBSTOR key at all means no mass-storage driver, not a refusal."""
    def raise_missing(hive, sub):
        raise FileNotFoundError()

    monkeypatch.setattr(security_reader.winreg, "OpenKey", raise_missing)

    result = security_reader.check_usb_storage()

    assert result["available"] is True
    assert result["enabled"] is False


def test_usb_storage_access_denied_is_never_collapsed_into_a_value(monkeypatch):
    """A refused read must come back `available: False`, and `.read()` must
    then answer None -- never False, which would claim USB storage is
    blocked when the truth is simply unknown."""
    def raise_denied(hive, sub):
        raise PermissionError()

    monkeypatch.setattr(security_reader.winreg, "OpenKey", raise_denied)

    result = security_reader.check_usb_storage()
    assert result["available"] is False

    from modules.security_dashboard.catalog.model import Category, SecurityControl
    control = SecurityControl(
        id="usb_storage_test", title="t", category=Category.DEVICE_BOOT,
        description="d", why_it_matters="w",
        reader=security_reader.check_usb_storage,
        off_steps=({"type": "registry", "key": "HKLM\\A", "value": "V",
                    "data": 1, "kind": "DWORD"},))
    assert control.read() is None


def test_usb_storage_real_machine_reads_a_definite_value():
    """No mocking: the real USBSTOR service key on THIS machine, read
    unelevated. Measured 2026-09-29: Start=3 (allowed, the out-of-box
    default) -- this only pins that the reader comes back with a real,
    available answer, not a specific Start value that could change."""
    result = security_reader.check_usb_storage()

    assert result["available"] is True
    assert result["enabled"] in (True, False)
    assert result["status"] in ("Allowed", "Blocked")


# -- check_hvci --------------------------------------------------------------

def test_hvci_running_reads_as_enabled(powershell):
    powershell.append(("Win32_DeviceGuard", (
        0, '{"VirtualizationBasedSecurityStatus":2,'
           '"SecurityServicesRunning":[2],"SecurityServicesConfigured":[2]}', "")))

    result = security_reader.check_hvci()

    assert result["available"] is True
    assert result["enabled"] is True


def test_hvci_off_reads_as_disabled(powershell):
    powershell.append(("Win32_DeviceGuard", (
        0, '{"VirtualizationBasedSecurityStatus":0,'
           '"SecurityServicesRunning":0}', "")))

    result = security_reader.check_hvci()

    assert result["available"] is True
    assert result["enabled"] is False


def test_a_refused_deviceguard_query_is_not_a_verdict(powershell):
    powershell.append(("Win32_DeviceGuard", (1, "", "Access is denied.")))

    result = security_reader.check_hvci()

    assert result["available"] is False
    assert result.get("enabled") is not True


# -- the event log sizes -----------------------------------------------------

def test_the_security_log_size_carries_its_number(powershell):
    powershell.append(("ListLog Security", (0, "196", "")))

    result = security_reader.check_security_log_size()

    assert result["megabytes"] == 196
    assert result["available"] is True


def test_the_system_log_size_carries_its_number(powershell):
    powershell.append(("ListLog System", (0, "64", "")))

    assert security_reader.check_system_log_size()["megabytes"] == 64


def test_a_log_size_that_could_not_be_read_says_so(powershell):
    powershell.append(("ListLog Security", (1, "", "Access is denied.")))

    result = security_reader.check_security_log_size()

    assert result["available"] is False
    assert result.get("megabytes") is None


# -- the duplicate pair ------------------------------------------------------

def test_the_two_memory_integrity_readers_read_the_same_value(registry):
    """Kept as a live check that they really are duplicates: if they ever
    diverge, NOT_A_CONTROL's claim that one stands for the other is wrong."""
    registry[(_HVCI_KEY, "Enabled")] = 1

    summary = security_reader.check_core_isolation_summary()
    policy = security_reader.check_memory_integrity_registry()

    assert summary["enabled"] == policy["enabled"] is True

    registry[(_HVCI_KEY, "Enabled")] = 0
    assert (security_reader.check_core_isolation_summary()["enabled"]
            == security_reader.check_memory_integrity_registry()["enabled"]
            is False)


def test_one_of_the_duplicate_pair_is_bound_and_the_other_is_excluded():
    from modules.security_dashboard.catalog import NOT_A_CONTROL, load_catalog

    bound = {c.reader for c in load_catalog().values()}
    summary = security_reader.check_core_isolation_summary
    policy = security_reader.check_memory_integrity_registry

    assert (summary in bound) != (policy in bound), (
        "exactly one of the two readers for this registry value is a control")
    excluded = ("check_core_isolation_summary" in NOT_A_CONTROL
                or "check_memory_integrity_registry" in NOT_A_CONTROL)
    assert excluded, "the other must say in NOT_A_CONTROL why it is not bound"


@pytest.mark.parametrize("first,second", [
    ("check_service_diag_track", "check_service_diagtrack"),
    ("check_service_maps_broker", "check_service_mapsbroker"),
])
def test_two_readers_for_one_service_produce_only_one_control(first, second):
    """DiagTrack and MapsBroker each have two readers, through two different
    helpers. Two writable controls for one service would let the catalog stage
    two conflicting changes to the same start type."""
    from modules.security_dashboard.catalog import NOT_A_CONTROL, load_catalog

    bound = {c.reader for c in load_catalog().values()}
    a = getattr(security_reader, first)
    b = getattr(security_reader, second)

    assert (a in bound) != (b in bound), f"{first}/{second}: bind exactly one"
    assert first in NOT_A_CONTROL or second in NOT_A_CONTROL


# -- Get-WinEvent refuses by exiting 0 ---------------------------------------

#: Verbatim from this machine, unelevated, 2026-08-29. rc is 0, stdout is "0",
#: and the refusal is on stderr -- note that PowerShell WRAPS the message, so
#: "unauthorized" and "operation" land on different lines and a marker
#: matching the whole phrase would miss it.
REAL_WINEVENT_REFUSAL = (
    "Get-WinEvent : Could not retrieve information about the Security log. "
    "Error: Attempted to perform an unauthorized \noperation..\n"
    "At line:1 char:2")


def test_a_security_log_query_that_exits_zero_while_refusing(powershell):
    """rc 0 and stdout "0" -- so the size reader reported "0 MB" in red, a
    verdict about a log it was not allowed to look at."""
    powershell.append(("ListLog Security", (0, "0", REAL_WINEVENT_REFUSAL)))

    result = security_reader.check_security_log_size()

    assert result["available"] is False
    assert result.get("megabytes") is None
    assert "0 MB" not in result["status"]


def test_the_shared_refusal_detector_catches_the_wrapped_message():
    from modules.security_dashboard import snapshots as snap

    assert snap._looks_refused(0, "0", REAL_WINEVENT_REFUSAL) is not None


# -- a service control must write the service its reader reads ---------------

def test_every_service_step_names_the_service_its_reader_checks():
    """Caught a real one while Task 8 was being written: an entry titled
    "Remote Access Connection Manager", reading check_service_remote_access
    _connection, carried a step disabling **RemoteRegistry**. Nothing else
    would have noticed -- the id, the title and the prose were all consistent,
    and only the service name was wrong.

    The reader's service name is the first string it passes to _svc_check or
    _check_service, so the two can be compared without running anything.
    """
    import inspect
    import re

    from modules.security_dashboard.catalog import load_catalog

    pattern = re.compile(r'_(?:svc_check|check_service)\(\s*"([^"]+)"')
    mismatches = []
    checked = 0

    for cid, control in load_catalog().items():
        steps = control.on_steps + control.off_steps
        service_steps = [s for s in steps if s.get("type") == "service"]
        if not service_steps:
            continue
        try:
            source = inspect.getsource(control.reader)
        except (OSError, TypeError):
            continue
        found = pattern.search(source)
        if not found:
            continue
        expected = found.group(1).lower()
        checked += 1
        for step in service_steps:
            if step["name"].lower() != expected:
                mismatches.append(
                    f"{cid}: step writes {step['name']!r}, reader reads "
                    f"{found.group(1)!r}")

    assert checked >= 15, f"only checked {checked} service controls"
    assert not mismatches, "\n".join(mismatches)


# -- the last three readers with nothing for the catalog to compare ----------

def test_the_windows_update_service_reader_says_whether_it_is_running(
        monkeypatch):
    """It returned status and colour but never `enabled`, so the control bound
    to it -- one of the few in this file whose desired state is True -- read
    None on every machine."""
    monkeypatch.setattr(snapshots, "service_states",
                        lambda: {"wuauserv": {"status": "Running",
                                              "start_type": "Manual"}})
    monkeypatch.setattr(snapshots, "unavailable", lambda name: None)

    result = security_reader.check_wu_service()

    assert result["enabled"] is True
    assert result["available"] is True


def test_a_stopped_windows_update_service_reads_false(monkeypatch):
    monkeypatch.setattr(snapshots, "service_states",
                        lambda: {"wuauserv": {"status": "Stopped",
                                              "start_type": "Disabled"}})
    monkeypatch.setattr(snapshots, "unavailable", lambda name: None)

    assert security_reader.check_wu_service()["enabled"] is False


def test_bcdedit_refusing_is_not_a_test_signing_verdict(monkeypatch):
    """bcdedit needs elevation; unelevated it cannot report the boot flags at
    all, and the card must not read that as "test signing is off"."""
    monkeypatch.setattr(security_reader, "_cmd_run",
                        lambda *a, **k: (1, "", "Access is denied."))

    result = security_reader.check_test_signing()

    assert result["available"] is False
    assert result.get("enabled") is None


#: The shape of `bcdedit /enum` on a machine where test signing was explicitly
#: turned OFF. Note `recoveryenabled Yes`, which is on nearly every machine.
BCDEDIT_TESTSIGNING_OFF = """Windows Boot Loader
-------------------
identifier              {current}
device                  partition=C:
description             Windows 11
recoveryenabled         Yes
integrityservices       Enable
testsigning             No
nx                      OptIn
"""

BCDEDIT_TESTSIGNING_ON = BCDEDIT_TESTSIGNING_OFF.replace(
    "testsigning             No", "testsigning             Yes")


def test_test_signing_off_is_not_read_as_on_because_of_another_yes(monkeypatch):
    """`"testsigning" in out and "yes" in out` matches the whole output, so
    `recoveryenabled Yes` on the same machine turned an explicit
    `testsigning No` into a red "Enabled (DANGER)"."""
    monkeypatch.setattr(security_reader, "_cmd_run",
                        lambda *a, **k: (0, BCDEDIT_TESTSIGNING_OFF, ""))

    result = security_reader.check_test_signing()

    assert result["enabled"] is False
    assert result["color"] == "green"


def test_test_signing_on_is_still_caught(monkeypatch):
    monkeypatch.setattr(security_reader, "_cmd_run",
                        lambda *a, **k: (0, BCDEDIT_TESTSIGNING_ON, ""))

    result = security_reader.check_test_signing()

    assert result["enabled"] is True
    assert result["color"] == "red"


# -- the service snapshot speaks in numbers ----------------------------------
#
# Get-Service | ConvertTo-Json serialises both enums as INTEGERS, and this
# machine's snapshot is full of them: status 1 = Stopped, 4 = Running;
# start type 2 = Automatic, 3 = Manual, 4 = Disabled. Two consequences the
# cards showed: `_check_service` and check_wu_service put the raw number in
# front of the user ("Windows Update Service: 1"), and `_svc_check` labelled
# every DISABLED service "Manual", because it only tested for Automatic.

def _services(monkeypatch, table):
    monkeypatch.setattr(snapshots, "service_states", lambda: table)
    monkeypatch.setattr(snapshots, "unavailable", lambda name: None)


def test_a_stopped_service_is_not_shown_as_the_number_one(monkeypatch):
    _services(monkeypatch, {"wuauserv": {"status": 1, "start_type": 3}})

    result = security_reader.check_wu_service()

    assert "1" != result["status"]
    assert "Stopped" in result["status"]


def test_a_disabled_service_is_not_labelled_manual(monkeypatch):
    _services(monkeypatch, {"webclient": {"status": 1, "start_type": 4}})

    result = security_reader.check_service_webclient()

    assert "Disabled" in str(result["details"]), (
        "start type 4 is Disabled; only Automatic was ever tested for")
    assert "Manual" not in str(result["details"])


def test_a_service_control_can_tell_disabled_from_merely_stopped(monkeypatch):
    """Windows Update is trigger-started: stopped is its normal state, and
    DISABLED is the one that matters. A control that could only see `running`
    would flag every healthy machine."""
    _services(monkeypatch, {"wuauserv": {"status": 1, "start_type": 3}})
    assert security_reader.check_wu_service()["disabled"] is False

    _services(monkeypatch, {"wuauserv": {"status": 1, "start_type": 4}})
    assert security_reader.check_wu_service()["disabled"] is True


def test_the_windows_update_control_reads_not_disabled_not_running():
    from modules.security_dashboard.catalog import load_catalog

    control = load_catalog()["service_windows_update"]

    assert control.read_value is not None
    assert control.read_value({"disabled": False}) is True
    assert control.read_value({"disabled": True}) is False


# -- two enum-as-number displays, and a substring guard that ate its own
#    success case ------------------------------------------------------------

#: icacls, run ELEVATED, on this machine. Note the last line: EVERY successful
#: icacls run ends with "Failed processing 0 files", so a guard testing for the
#: substring "Failed processing" rejects its own success case. That guard was
#: added in Task 7 to catch the unelevated failure, and the elevated probe is
#: what caught it rejecting a perfectly good ACL.
REAL_ICACLS_OK = (
    r"C:\Windows\System32\config\SAM NT AUTHORITY\SYSTEM:(I)(F)" "\n"
    r"                                  BUILTIN\Administrators:(I)(F)" "\n"
    "\n"
    "Successfully processed 1 files; Failed processing 0 files\n")

REAL_ICACLS_DENIED = (
    r"C:\Windows\System32\config\SAM: Access is denied." "\n"
    "Successfully processed 0 files; Failed processing 1 files\n")


def test_a_successful_icacls_is_not_rejected_by_its_own_summary_line(
        monkeypatch, powershell):
    monkeypatch.setattr(security_reader, "_file_present", lambda path: True)
    powershell.append(("icacls", (0, REAL_ICACLS_OK, "")))

    result = security_reader.check_sam_hive_permissions()

    assert result["available"] is True, (
        '"Failed processing 0 files" is what SUCCESS looks like')
    assert result["enabled"] is True


def test_an_icacls_that_really_failed_is_still_caught(monkeypatch, powershell):
    monkeypatch.setattr(security_reader, "_file_present", lambda path: True)
    powershell.append(("icacls", (0, REAL_ICACLS_DENIED, "")))

    result = security_reader.check_sam_hive_permissions()

    assert result["available"] is False


#: Get-BitLockerVolume | ConvertTo-Json on this machine, elevated. Both enums
#: come back as INTEGERS, and the card rendered them literally: "0 (0)".
REAL_BITLOCKER_JSON = (
    '[{"MountPoint":"C:","EncryptionMethod":0,"VolumeStatus":0,'
    '"ProtectionStatus":0},'
    '{"MountPoint":"E:","EncryptionMethod":0,"VolumeStatus":0,'
    '"ProtectionStatus":0}]')


def test_bitlocker_volume_state_is_named_not_numbered(powershell):
    powershell.append(("Get-BitLockerVolume", (0, REAL_BITLOCKER_JSON, "")))

    result = security_reader.check_bitlocker_encryption()

    rendered = str(result["details"])
    assert "0 (0)" not in rendered, "showed the raw enum values to the user"
    assert "Fully Decrypted" in rendered
    assert "None" in rendered


def test_an_encrypted_volume_reads_as_protected(powershell):
    powershell.append(("Get-BitLockerVolume", (
        0, '[{"MountPoint":"C:","EncryptionMethod":7,"VolumeStatus":1,'
           '"ProtectionStatus":1}]', "")))

    result = security_reader.check_bitlocker_encryption()

    assert result["enabled"] is True
    assert "XTS-AES 256" in str(result["details"])
    assert "Fully Encrypted" in str(result["details"])

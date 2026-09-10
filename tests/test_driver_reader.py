import json

import pytest

from modules.driver_manager import driver_reader as dr


def test_a_malformed_json_payload_raises_a_clear_error(monkeypatch):
    class FakeProc:
        stdout = "{not valid json"
        returncode = 0
    monkeypatch.setattr(dr.subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(dr.DriverReadError, match="could not parse"):
        dr.fetch_drivers()


def test_classify_provider_is_shared_by_table_and_export():
    assert dr.classify_provider("Microsoft") == "Microsoft"
    assert dr.classify_provider("Realtek Semiconductor Corp.") == "Third-Party"
    assert dr.classify_provider("Microsoft-compatible XYZ Corp") == "Third-Party"


def test_error_code_decodes_to_a_known_meaning():
    assert "disabled" in dr.decode_error_code(22).lower()
    assert dr.decode_error_code(0) == ""


def test_an_unrecognized_error_code_says_so_rather_than_guessing():
    assert "unrecognized" in dr.decode_error_code(9999).lower()


def test_an_unparseable_driver_date_is_flagged_not_blanked():
    info = dr._build_driver_info({
        "Name": "X", "Class": "Net", "Version": "1.0",
        "Date": "not-a-date", "Publisher": "Vendor", "IsSigned": True,
        "ErrorCode": 0})
    assert "date unreadable" in info.flags.lower()


def test_devices_with_no_driver_are_included():
    devices = dr._merge_driverless_devices(
        drivers=[], driverless_raw='[{"Name":"Unknown device","ConfigManagerErrorCode":28}]')
    assert len(devices) == 1
    assert devices[0].version == ""
    assert devices[0].error_code == 28


def test_driverless_devices_already_present_are_not_duplicated():
    existing = dr.DriverInfo(
        device_name="Known device", driver_class="Net", version="1.0",
        date="2020-01-01", publisher="Vendor", signed=True,
        error_code=0, flags="")
    devices = dr._merge_driverless_devices(
        drivers=[existing],
        driverless_raw='[{"Name":"Known device","ConfigManagerErrorCode":28}]')
    assert len(devices) == 1
    assert devices[0] is existing


def test_merge_driverless_devices_tolerates_empty_or_malformed_input():
    existing = [dr.DriverInfo(
        device_name="Known device", driver_class="Net", version="1.0",
        date="2020-01-01", publisher="Vendor", signed=True,
        error_code=0, flags="")]
    assert dr._merge_driverless_devices(existing, "") == existing
    assert dr._merge_driverless_devices(existing, "{not valid json") == existing


def test_old_threshold_days_is_configurable():
    recent = (dr.datetime.datetime.now() - dr.datetime.timedelta(days=10)).strftime("%Y%m%d")
    old_by_default_only = (dr.datetime.datetime.now() - dr.datetime.timedelta(days=100)).strftime("%Y%m%d")

    # With the default 730-day threshold, a 100-day-old driver is not "Old".
    info_default = dr._build_driver_info({
        "Name": "X", "Class": "Net", "Version": "1.0",
        "Date": old_by_default_only, "Publisher": "Vendor", "IsSigned": True,
        "ErrorCode": 0})
    assert "old" not in info_default.flags.lower()

    # With a tighter 30-day threshold, that same 100-day-old driver is "Old".
    info_tight = dr._build_driver_info({
        "Name": "X", "Class": "Net", "Version": "1.0",
        "Date": old_by_default_only, "Publisher": "Vendor", "IsSigned": True,
        "ErrorCode": 0}, old_threshold_days=30)
    assert "old" in info_tight.flags.lower()

    # A genuinely recent driver is not "Old" against a threshold it is
    # comfortably inside of.
    info_recent = dr._build_driver_info({
        "Name": "X", "Class": "Net", "Version": "1.0",
        "Date": recent, "Publisher": "Vendor", "IsSigned": True,
        "ErrorCode": 0}, old_threshold_days=30)
    assert "old" not in info_recent.flags.lower()


def test_pseudo_classes_constant_covers_known_non_hardware_classes():
    assert dr._PSEUDO_CLASSES == {"SoftwareComponent", "SoftwareDevice", "PrintQueue"}


def test_fetch_drivers_chunks_the_wmi_query(monkeypatch):
    """A single 90s call across every driver dies with zero partial
    results if it times out. Chunking means a slow query loses at most
    one chunk's worth, not the whole list."""
    calls = []
    def fake_run(cmd, **k):
        calls.append(k.get("timeout"))
        class R: stdout = "[]"; returncode = 0
        return R()
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    dr.fetch_drivers()
    assert all(t and t <= 30 for t in calls), \
        "expected per-chunk timeouts well under the old flat 90s"


def test_a_chunk_timeout_loses_only_that_chunk_not_the_whole_refresh(monkeypatch):
    """Every subprocess.run call in the chunked refresh has timeout=30 but
    nothing used to catch subprocess.TimeoutExpired -- it propagated all
    the way out of fetch_drivers(), discarding every chunk already
    successfully collected. One slow/hung class query must cost at most
    that one chunk's worth, per this exact code's own comment."""
    net_device = [{"Name": "Real NIC", "Class": "Net", "Version": "1.0",
                   "Date": "", "Publisher": "Vendor", "IsSigned": True,
                   "ErrorCode": 0, "InfName": "", "DeviceID": "NET\\1"}]

    def fake_run(cmd, **k):
        ps_cmd = cmd[-1]
        if "DeviceClass='Display'" in ps_cmd:
            raise dr.subprocess.TimeoutExpired(cmd=cmd, timeout=k.get("timeout", 30))
        class R:
            returncode = 0
            stdout = "[]"
        if "DeviceClass='Net'" in ps_cmd:
            R.stdout = json.dumps(net_device)
        return R()

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    drivers = dr.fetch_drivers()  # must not raise
    names = {d.device_name for d in drivers}
    assert "Real NIC" in names, \
        "the Net chunk's driver must survive the Display chunk timing out"


def test_same_name_devices_are_not_collapsed_but_true_duplicates_are(monkeypatch):
    """Regression: device_name alone is not a safe dedup key for merging
    the class chunks -- Windows commonly reports several distinct physical
    devices under an identical generic name (multiple "USB Root Hub"
    entries, several "Generic PnP Monitor"s). A name-only dedup silently
    erased whichever one a later chunk/the final unfiltered pass happened
    to encounter second -- exactly backwards for a tool whose job is
    surfacing a problem device. device_id (the PNP device instance id) is
    the real unique key; the final unfiltered pass genuinely re-returns
    every device the class chunks already collected, so it still needs
    SOME dedup or the list would double."""
    usb_devices = [
        {"Name": "USB Root Hub (USB 3.0)", "Class": "USB", "Version": "1.0",
         "Date": "", "Publisher": "Microsoft", "IsSigned": True,
         "ErrorCode": 0, "InfName": "usb.inf", "DeviceID": "USB\\ROOT_HUB30\\1"},
        {"Name": "USB Root Hub (USB 3.0)", "Class": "USB", "Version": "1.0",
         "Date": "", "Publisher": "Microsoft", "IsSigned": True,
         "ErrorCode": 0, "InfName": "usb.inf", "DeviceID": "USB\\ROOT_HUB30\\2"},
    ]

    def fake_run(cmd, **k):
        ps_cmd = cmd[-1]
        class R:
            returncode = 0
            stdout = "[]"
        if "DeviceClass='USB'" in ps_cmd:
            R.stdout = json.dumps(usb_devices)
        elif "-Filter" not in ps_cmd and "Win32_PnPSignedDriver" in ps_cmd:
            # the final, unfiltered catch-all pass -- genuinely re-returns
            # every device the class chunks already saw.
            R.stdout = json.dumps(usb_devices)
        return R()

    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    drivers = dr.fetch_drivers()
    hubs = [d for d in drivers if d.device_name == "USB Root Hub (USB 3.0)"]
    assert len(hubs) == 2, "two DISTINCT devices sharing a name must both survive"
    assert {d.device_id for d in hubs} == {"USB\\ROOT_HUB30\\1", "USB\\ROOT_HUB30\\2"}


def test_whql_certified_true_only_for_the_real_whcp_signer(monkeypatch):
    def fake_run(cmd, **k):
        class R:
            returncode = 0
            stdout = json.dumps([
                {"Name": "AMD Radeon RX 7900 XTX", "Class": "Display",
                 "Version": "1.0", "Date": "", "Publisher": "AMD",
                 "IsSigned": True, "ErrorCode": 0, "InfName": "oem1.inf",
                 "DeviceID": "PCI\\VEN_1002", "HardWareID": "PCI\\VEN_1002;PCI\\VEN_1002&DEV_744C",
                 "Signer": "Microsoft Windows Hardware Compatibility Publisher"},
                {"Name": "WAN Miniport (IP)", "Class": "Net",
                 "Version": "1.0", "Date": "", "Publisher": "Microsoft",
                 "IsSigned": True, "ErrorCode": 0, "InfName": "netvmini.inf",
                 "DeviceID": "ROOT\\MS_NDISWANIP", "HardWareID": "ROOT\\MS_NDISWANIP",
                 "Signer": "Microsoft Windows"},
            ])
        return R()
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    drivers = dr.fetch_drivers()
    by_name = {d.device_name: d for d in drivers}
    assert by_name["AMD Radeon RX 7900 XTX"].whql_certified is True
    assert by_name["WAN Miniport (IP)"].whql_certified is False


def test_hardware_id_takes_the_first_of_a_semicolon_joined_array():
    info = dr._build_driver_info({
        "Name": "Test Device", "Class": "Net", "Version": "1.0", "Date": "",
        "Publisher": "V", "IsSigned": True, "ErrorCode": 0,
        "HardWareID": "PCI\\VEN_1234&DEV_5678;PCI\\VEN_1234",
        "Signer": "Microsoft Windows Hardware Compatibility Publisher",
    })
    assert info.hardware_id == "PCI\\VEN_1234&DEV_5678"
    assert info.whql_certified is True


def test_driver_store_size_returns_none_for_an_inbox_driver():
    d = dr.DriverInfo(device_name="X", driver_class="Net", version="1.0",
                      date="", publisher="V", signed=True, error_code=0,
                      flags="", inf_name="usb.inf")  # inbox, no oem number
    assert dr.driver_store_size(d) is None


def test_driver_store_size_delegates_to_the_cleanup_scanners_sizing_code(monkeypatch):
    d = dr.DriverInfo(device_name="X", driver_class="Net", version="1.0",
                      date="", publisher="V", signed=True, error_code=0,
                      flags="", inf_name="oem42.inf")
    from modules.cleanup.cleanup_scanner import driver_store as ds
    captured = {}
    def fake_package_size(package):
        captured["published"] = package.published
        return 12345
    monkeypatch.setattr(ds, "package_size", fake_package_size)
    assert dr.driver_store_size(d) == 12345
    assert captured["published"] == "oem42.inf"


def test_duplicate_hardware_ids_groups_only_real_collisions():
    a = dr.DriverInfo(device_name="Generic Driver A", driver_class="Net",
                      version="1.0", date="", publisher="X", signed=True,
                      error_code=0, flags="", hardware_id="PCI\\VEN_AAAA")
    b = dr.DriverInfo(device_name="Generic Driver B", driver_class="Net",
                      version="1.0", date="", publisher="Y", signed=True,
                      error_code=0, flags="", hardware_id="PCI\\VEN_AAAA")
    c = dr.DriverInfo(device_name="Unique Driver", driver_class="Net",
                      version="1.0", date="", publisher="Z", signed=True,
                      error_code=0, flags="", hardware_id="PCI\\VEN_BBBB")
    no_id = dr.DriverInfo(device_name="No HWID", driver_class="Net",
                          version="1.0", date="", publisher="W", signed=True,
                          error_code=0, flags="", hardware_id="")
    groups = dr.detect_duplicate_hardware_ids([a, b, c, no_id])
    assert groups == {"PCI\\VEN_AAAA": [a, b]}


def test_duplicate_hardware_id_is_surfaced_as_a_flag(monkeypatch):
    """detect_duplicate_hardware_ids() was tested but had no production
    consumer -- fetch_drivers() must now flag every device in a real
    collision group, and leave an unrelated device alone."""
    def fake_run(cmd, **k):
        class R:
            returncode = 0
            stdout = json.dumps([
                {"Name": "Generic Driver A", "Class": "Net", "Version": "1.0",
                 "Date": "", "Publisher": "X", "IsSigned": True, "ErrorCode": 0,
                 "InfName": "oem1.inf", "DeviceID": "NET\\1",
                 "HardWareID": "PCI\\VEN_AAAA"},
                {"Name": "Generic Driver B", "Class": "Net", "Version": "1.0",
                 "Date": "", "Publisher": "Y", "IsSigned": True, "ErrorCode": 0,
                 "InfName": "oem2.inf", "DeviceID": "NET\\2",
                 "HardWareID": "PCI\\VEN_AAAA"},
                {"Name": "Unique Driver", "Class": "Net", "Version": "1.0",
                 "Date": "", "Publisher": "Z", "IsSigned": True, "ErrorCode": 0,
                 "InfName": "oem3.inf", "DeviceID": "NET\\3",
                 "HardWareID": "PCI\\VEN_BBBB"},
            ])
        return R()
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    drivers = dr.fetch_drivers()
    by_name = {d.device_name: d for d in drivers}
    assert "Shared Hardware ID" in by_name["Generic Driver A"].flags
    assert "Shared Hardware ID" in by_name["Generic Driver B"].flags
    assert "Shared Hardware ID" not in by_name["Unique Driver"].flags


def test_list_restore_points_returns_none_on_a_failed_read(monkeypatch):
    def fake_run(cmd, **k):
        class R:
            returncode = 1
            stdout = ""
            stderr = "Access is denied."
        return R()
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    assert dr.list_restore_points() is None


def test_list_restore_points_returns_empty_list_when_none_exist(monkeypatch):
    def fake_run(cmd, **k):
        class R:
            returncode = 0
            stdout = ""
        return R()
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    assert dr.list_restore_points() == []


def test_list_restore_points_returns_none_when_exit_zero_hides_a_refusal(monkeypatch):
    """Get-ComputerRestorePoint needs elevation; unelevated, PowerShell can
    exit 0 while writing the real refusal to stderr and producing no
    stdout at all -- collapsing that into [] would report "No restore
    points found." with no mention of admin rights (finding I2)."""
    def fake_run(cmd, **k):
        class R:
            returncode = 0
            stdout = ""
            stderr = "Access is denied."
        return R()
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    assert dr.list_restore_points() is None


def test_list_restore_points_parses_real_shaped_output(monkeypatch):
    def fake_run(cmd, **k):
        class R:
            returncode = 0
            stdout = json.dumps({
                "SequenceNumber": 42, "Description": "Driver update",
                "RestorePointType": 12, "CreationTime": "20260101120000.000000-000",
            })
        return R()
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    points = dr.list_restore_points()
    assert points == [{
        "SequenceNumber": 42, "Description": "Driver update",
        "RestorePointType": 12, "CreationTime": "20260101120000.000000-000",
    }]


def test_list_restore_points_returns_none_on_timeout(monkeypatch):
    """Restore point query timeout should return None, not raise."""
    def fake_run(cmd, **k):
        raise dr.subprocess.TimeoutExpired(cmd=cmd, timeout=k.get("timeout", 30))
    monkeypatch.setattr(dr.subprocess, "run", fake_run)
    assert dr.list_restore_points() is None


def test_list_restore_points_returns_none_on_malformed_json(monkeypatch):
    """Malformed JSON output should return None, not raise."""
    class FakeProc:
        stdout = "{not valid json"
        returncode = 0
    monkeypatch.setattr(dr.subprocess, "run", lambda *a, **k: FakeProc())
    assert dr.list_restore_points() is None

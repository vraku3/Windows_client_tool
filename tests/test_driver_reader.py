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

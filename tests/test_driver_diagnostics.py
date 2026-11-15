import datetime

from core.types import LogEntry
from modules.driver_manager import driver_diagnostics as dd


def _entry(product_name="", message=""):
    return LogEntry(
        timestamp=datetime.datetime.now(), source="Reliability",
        level="Error", message=message,
        raw={"product_name": product_name},
    )


def test_crashes_for_matches_by_substring_in_product_name():
    records = [
        _entry(product_name="AMD Radeon RX 7900 XTX", message="Driver crashed"),
        _entry(product_name="Realtek Audio", message="unrelated"),
    ]
    result = dd.crashes_for("AMD Radeon RX 7900 XTX", records)
    assert len(result) == 1
    assert result[0].message == "Driver crashed"


def test_crashes_for_also_matches_when_the_name_is_only_in_the_message():
    # reliability_reader falls back to product_name AS the message when
    # there's no separate WMI Message text -- so a record with an empty
    # raw["product_name"] but the device name in `message` must still match.
    records = [_entry(product_name="", message="AMD Radeon RX 7900 XTX")]
    assert len(dd.crashes_for("AMD Radeon RX 7900 XTX", records)) == 1


def test_crashes_for_returns_empty_when_nothing_matches():
    assert dd.crashes_for("Nonexistent Device", [_entry(product_name="Other")]) == []


def test_crashes_for_matches_when_the_record_field_is_a_truncated_prefix_of_the_full_device_name():
    # The queried device name is FULL; the Reliability record's product_name
    # is a TRUNCATED prefix of it (missing the trailing "XTX"). Unlike the
    # two tests above (which use identical strings on both sides, so both
    # `needle in text` and `text in needle` are trivially true), this is a
    # genuine substring relationship in only ONE direction: `needle in text`
    # is false here because the needle is longer than the text. Only the
    # `text in needle` half of crashes_for's match can catch this.
    full = "AMD Radeon RX 7900 XTX"
    truncated = "AMD Radeon RX 7900"
    assert truncated in full and full not in truncated  # sanity: real substring, not equality
    records = [_entry(product_name=truncated, message="unrelated")]
    result = dd.crashes_for(full, records)
    assert len(result) == 1


def test_crashes_for_matches_when_the_queried_device_name_is_a_truncated_prefix_of_the_record_field():
    # The reverse direction: the queried device name is the TRUNCATED one
    # and the Reliability record carries the FULL product name. Only the
    # `needle in text` half of crashes_for's match can catch this one --
    # `text in needle` is false here because the text is longer than the
    # needle.
    truncated = "AMD Radeon RX 7900"
    full = "AMD Radeon RX 7900 XTX"
    assert truncated in full and full not in truncated  # sanity: real substring, not equality
    records = [_entry(product_name=full, message="unrelated")]
    result = dd.crashes_for(truncated, records)
    assert len(result) == 1


def test_every_suggested_action_is_a_real_sentence():
    from modules.driver_manager.driver_reader import _ERROR_CODE_MEANINGS
    for code in _ERROR_CODE_MEANINGS:
        action = dd.suggested_action(code)
        assert isinstance(action, str) and len(action) > 10


def test_suggested_action_for_an_unknown_code_still_says_something():
    assert len(dd.suggested_action(99999)) > 10


def test_suggested_actions_cover_exactly_the_known_error_codes():
    # test_every_suggested_action_is_a_real_sentence only checks the string
    # length of whatever suggested_action() returns -- and the generic
    # fallback for an unrecognised code is also > 10 chars, so that test
    # alone would still pass even if _SUGGESTED_ACTIONS were missing an
    # entry for one of the 14 known codes (it would just silently fall
    # through to the fallback). This test verifies the real guarantee: every
    # code in driver_reader's _ERROR_CODE_MEANINGS has its OWN curated entry.
    from modules.driver_manager.driver_reader import _ERROR_CODE_MEANINGS
    assert set(dd._SUGGESTED_ACTIONS.keys()) == set(_ERROR_CODE_MEANINGS.keys())


def test_parse_driver_load_failures():
    events = [
        {
            "Time": "2026-11-14 11:53:37",
            "Message": "The driver \\Driver\\WUDFRd failed to load.\r\nDevice: HID\\VID_1395&PID_005C&MI_03&Col03\\c&2385ed86&0&0002\r\nStatus: 0xC0000365",
        },
        {
            "Time": "2026-11-13 09:09:55",
            "Message": "The driver \\Driver\\WUDFRd failed to load.\r\nDevice: HID\\VID_1395&PID_005C&MI_03&Col03\\c&2385ed86&0&0002\r\nStatus: 0xC0000365",
        },
        {
            "Time": "2026-11-14 11:53:26",
            "Message": "The driver \\Driver\\WUDFRd failed to load.\r\nDevice: ROOT\\WINDOWSHELLOFACESOFTWAREDRIVER\\0000\r\nStatus: 0xC0000365",
        },
    ]
    failures = dd.parse_driver_load_failures(events)
    assert len(failures) == 2

    # Grouped by (driver, device)
    hid_fail = next(f for f in failures if "1395" in f.device_id)
    assert hid_fail.driver == "\\Driver\\WUDFRd"
    assert hid_fail.count == 2
    assert hid_fail.status == "0xC0000365"
    assert "STATUS_DEVICE_NOT_READY" in hid_fail.status_meaning
    assert len(hid_fail.sample_times) == 2

    face_fail = next(f for f in failures if "WINDOWSHELLO" in f.device_id)
    assert face_fail.count == 1


def test_ntstatus_meaning_known_and_unknown():
    assert "STATUS_DEVICE_NOT_READY" in dd.ntstatus_meaning("0xC0000365")
    assert "STATUS_INVALID_IMAGE_HASH" in dd.ntstatus_meaning("0xC0000428")
    assert dd.ntstatus_meaning("0x12345678") == "0x12345678"


def test_driver_load_failures_for():
    f1 = dd.DriverLoadFailure(
        driver="\\Driver\\WUDFRd",
        device_id="HID\\VID_1395&PID_005C",
        status="0xC0000365",
        status_meaning="Not ready",
        count=1,
        most_recent="2026-11-14",
    )
    assert dd.driver_load_failures_for("VID_1395", [f1]) == [f1]
    assert dd.driver_load_failures_for("OtherDevice", [f1]) == []
    assert dd.driver_load_failures_for("", [f1]) == []

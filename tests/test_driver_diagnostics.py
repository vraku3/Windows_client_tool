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


def test_every_suggested_action_is_a_real_sentence():
    from modules.driver_manager.driver_reader import _ERROR_CODE_MEANINGS
    for code in _ERROR_CODE_MEANINGS:
        action = dd.suggested_action(code)
        assert isinstance(action, str) and len(action) > 10


def test_suggested_action_for_an_unknown_code_still_says_something():
    assert len(dd.suggested_action(99999)) > 10

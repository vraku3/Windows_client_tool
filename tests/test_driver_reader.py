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

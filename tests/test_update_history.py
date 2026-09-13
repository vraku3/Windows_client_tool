import json
import os

import pytest

from modules.driver_manager.vendor_updates import update_history as uh


@pytest.fixture(autouse=True)
def _isolated_history_path(tmp_path, monkeypatch):
    path = str(tmp_path / "update_history.json")
    monkeypatch.setattr(uh, "_history_path", lambda: path)
    return path


def test_get_returns_none_when_nothing_recorded_yet():
    assert uh.get("PCI\\DEV1") is None


def test_get_returns_none_for_empty_device_id():
    assert uh.get("") is None


def test_record_check_then_get_round_trips():
    uh.record_check("PCI\\DEV1", "AMD Radeon RX 7900 XTX", "AMD",
                    uh.OUTCOME_UPDATE_FOUND, seen_vendor_version="26.8.1")
    result = uh.get("PCI\\DEV1")
    assert result is not None
    assert result.device_name == "AMD Radeon RX 7900 XTX"
    assert result.vendor == "AMD"
    assert result.last_check_outcome == uh.OUTCOME_UPDATE_FOUND
    assert result.last_seen_vendor_version == "26.8.1"
    assert result.last_checked_at is not None


def test_record_check_persists_across_a_fresh_load(tmp_path, _isolated_history_path):
    uh.record_check("PCI\\DEV1", "AMD Radeon RX 7900 XTX", "AMD", uh.OUTCOME_NO_UPDATE)
    # a second, independent read (simulating a new process) must see it
    assert os.path.exists(_isolated_history_path)
    with open(_isolated_history_path) as f:
        data = json.load(f)
    assert "PCI\\DEV1" in data


def test_record_check_overwrites_previous_check_fields_but_keeps_apply_fields():
    uh.record_applied("PCI\\DEV1", "AMD Radeon RX 7900 XTX", "AMD", "26.8.1", "32.0.1", "light")
    uh.record_check("PCI\\DEV1", "AMD Radeon RX 7900 XTX", "AMD", uh.OUTCOME_NO_UPDATE)
    result = uh.get("PCI\\DEV1")
    assert result.last_check_outcome == uh.OUTCOME_NO_UPDATE
    # the apply record from before must survive a later check
    assert result.last_applied_vendor_version == "26.8.1"
    assert result.last_applied_mode == "light"


def test_record_applied_then_get_round_trips():
    uh.record_applied("PCI\\DEV1", "AMD Radeon RX 7900 XTX", "AMD",
                      "26.8.1", "32.0.31041.3013", "full")
    result = uh.get("PCI\\DEV1")
    assert result.last_applied_vendor_version == "26.8.1"
    assert result.last_applied_windows_version == "32.0.31041.3013"
    assert result.last_applied_mode == "full"
    assert result.last_applied_at is not None


def test_record_functions_are_no_ops_for_an_empty_device_id():
    uh.record_check("", "x", "AMD", uh.OUTCOME_NO_UPDATE)
    uh.record_applied("", "x", "AMD", "1.0", "1.0", "light")
    assert uh.get("") is None


def test_get_recovers_from_a_corrupt_json_file(_isolated_history_path):
    with open(_isolated_history_path, "w") as f:
        f.write("{ not valid json")
    assert uh.get("PCI\\DEV1") is None


def test_get_recovers_when_the_file_is_a_json_array_not_an_object(_isolated_history_path):
    with open(_isolated_history_path, "w") as f:
        json.dump([1, 2, 3], f)
    assert uh.get("PCI\\DEV1") is None


def test_get_drops_unknown_fields_from_a_future_version_of_the_file(_isolated_history_path):
    with open(_isolated_history_path, "w") as f:
        json.dump({"PCI\\DEV1": {"device_id": "PCI\\DEV1", "some_future_field": "x"}}, f)
    result = uh.get("PCI\\DEV1")
    assert result is not None
    assert result.device_id == "PCI\\DEV1"


# ---------------------------------------------------------------------
# status_label -- pure function, the core "is it up to date" logic
# ---------------------------------------------------------------------

def test_status_label_never_checked():
    assert uh.status_label("1.0", None) == "Not checked yet"


def test_status_label_update_found_not_yet_applied():
    h = uh.DeviceHistory(device_id="d", vendor="AMD",
                         last_checked_at="2026-09-13T12:00:00+00:00",
                         last_check_outcome=uh.OUTCOME_UPDATE_FOUND,
                         last_seen_vendor_version="26.8.1")
    label = uh.status_label("32.0.31041.3013", h)
    assert "26.8.1" in label
    assert "not compared" in label.lower()


def test_status_label_no_update_found():
    h = uh.DeviceHistory(device_id="d", vendor="AMD",
                         last_checked_at="2026-09-13T12:00:00+00:00",
                         last_check_outcome=uh.OUTCOME_NO_UPDATE)
    label = uh.status_label("32.0.31041.3013", h)
    assert "no update" in label.lower()


def test_status_label_check_failed_shows_the_error():
    h = uh.DeviceHistory(device_id="d", vendor="AMD",
                         last_checked_at="2026-09-13T12:00:00+00:00",
                         last_check_outcome=uh.OUTCOME_CHECK_FAILED,
                         last_check_error="network unreachable")
    label = uh.status_label("32.0.31041.3013", h)
    assert "failed" in label.lower()
    assert "network unreachable" in label


def test_status_label_up_to_date_when_both_versions_match_what_we_applied():
    h = uh.DeviceHistory(device_id="d", vendor="AMD",
                         last_applied_at="2026-09-13T12:00:00+00:00",
                         last_applied_vendor_version="26.8.1",
                         last_applied_windows_version="32.0.31041.3013",
                         last_seen_vendor_version="26.8.1")
    label = uh.status_label("32.0.31041.3013", h)
    assert "up to date" in label.lower()
    assert "26.8.1" in label


def test_status_label_windows_version_changed_since_our_install():
    # Windows Update (or a manual reinstall) changed the driver after
    # this app applied one -- must say so, never claim "up to date".
    h = uh.DeviceHistory(device_id="d", vendor="AMD",
                         last_applied_at="2026-09-13T12:00:00+00:00",
                         last_applied_vendor_version="26.8.1",
                         last_applied_windows_version="32.0.31041.3013",
                         last_seen_vendor_version="26.8.1")
    label = uh.status_label("99.0.99999.9999", h)
    assert "up to date" not in label.lower()
    assert "changed" in label.lower()
    assert "re-check" in label.lower()


def test_status_label_vendor_published_something_newer_since_our_install():
    h = uh.DeviceHistory(device_id="d", vendor="AMD",
                         last_applied_at="2026-09-13T12:00:00+00:00",
                         last_applied_vendor_version="26.8.1",
                         last_applied_windows_version="32.0.31041.3013",
                         last_seen_vendor_version="26.9.5")
    label = uh.status_label("32.0.31041.3013", h)
    assert "update available" in label.lower()
    assert "26.9.5" in label
    assert "26.8.1" in label


def test_status_label_applied_but_windows_version_not_yet_confirmed():
    # record_applied can be called with applied_windows_version=None when
    # a fresh post-install read wasn't available -- must not be treated
    # as a mismatch (which would wrongly say "the driver changed").
    h = uh.DeviceHistory(device_id="d", vendor="AMD",
                         last_applied_at="2026-09-13T12:00:00+00:00",
                         last_applied_vendor_version="26.8.1",
                         last_applied_windows_version=None)
    label = uh.status_label("32.0.31041.3013", h)
    assert "not yet confirmed" in label.lower()
    assert "changed" not in label.lower()


def test_status_label_applied_status_takes_priority_over_a_stale_check_outcome():
    # last_check_outcome can be stale relative to last_applied_at (e.g. a
    # check happened, then we installed) -- the applied facts must win.
    h = uh.DeviceHistory(device_id="d", vendor="AMD",
                         last_checked_at="2026-09-01T00:00:00+00:00",
                         last_check_outcome=uh.OUTCOME_UPDATE_FOUND,
                         last_seen_vendor_version="26.8.1",
                         last_applied_at="2026-09-13T12:00:00+00:00",
                         last_applied_vendor_version="26.8.1",
                         last_applied_windows_version="32.0.31041.3013")
    label = uh.status_label("32.0.31041.3013", h)
    assert "up to date" in label.lower()


def test_get_all_returns_every_recorded_device():
    uh.record_check("PCI\\DEV1", "AMD Radeon RX 7900 XTX", "AMD", uh.OUTCOME_NO_UPDATE)
    uh.record_check("PCI\\DEV2", "Realtek NIC", "Realtek", uh.OUTCOME_UPDATE_FOUND,
                    seen_vendor_version="1.2.3")
    result = uh.get_all()
    assert set(result.keys()) == {"PCI\\DEV1", "PCI\\DEV2"}
    assert result["PCI\\DEV2"].last_seen_vendor_version == "1.2.3"


def test_get_all_returns_empty_dict_when_nothing_recorded():
    assert uh.get_all() == {}


def test_get_all_skips_a_malformed_individual_record(_isolated_history_path):
    with open(_isolated_history_path, "w") as f:
        json.dump({
            "PCI\\GOOD": {"device_id": "PCI\\GOOD", "vendor": "AMD"},
            "PCI\\BAD": "not even a dict",
        }, f)
    result = uh.get_all()
    assert "PCI\\GOOD" in result
    assert "PCI\\BAD" not in result

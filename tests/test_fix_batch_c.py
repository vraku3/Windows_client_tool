"""Whole-app review batch C: AMD display-driver hold, stage failures."""
import types

from modules.updates.windows_updater import is_held_amd_display_driver
from modules.updates.stage_runners import stage_failures


def _u(**kw):
    base = dict(Type=2, Title="", DriverClass="", DriverManufacturer="")
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_an_amd_display_driver_is_held():
    assert is_held_amd_display_driver(_u(DriverClass="Display",
                                         DriverManufacturer="Advanced Micro Devices, Inc."))
    assert is_held_amd_display_driver(_u(Title="Advanced Micro Devices, Inc. - Display - 32.0.21042.62"))


def test_other_drivers_and_software_are_not_held():
    assert not is_held_amd_display_driver(_u(DriverClass="System",
                                             DriverManufacturer="Advanced Micro Devices, Inc."))
    assert not is_held_amd_display_driver(_u(DriverClass="Display", DriverManufacturer="Intel Corporation"))
    assert not is_held_amd_display_driver(_u(Type=1, Title="Advanced Micro Devices - Display"))


def test_stage_failures_count_what_failed():
    assert stage_failures("wu", {"results": [{"kb": "KB1", "success": False, "message": "0x80070005"}]})
    assert stage_failures("winget", {"results": [{"id": "A.B", "state": "FAILED"}]})
    assert stage_failures("dism", {"error": "DISM exited 87"})
    assert not stage_failures("winget", {"results": [{"id": "A.B", "state": "UNCONFIRMED"}]})
    assert not stage_failures("wu", {"results": [], "installed_count": 0})


def test_partial_metadata_still_holds_an_amd_display_driver():
    """Class without a maker: the title must still decide (a Codex finding)."""
    assert is_held_amd_display_driver(_u(DriverClass="Display", DriverManufacturer="",
                                         Title="Advanced Micro Devices, Inc. - Display - 32.0"))

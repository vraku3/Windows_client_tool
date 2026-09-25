import datetime
from modules.hardware_inventory import hardware_reader as hr


def test_dates_are_readable():
    assert hr.wmi_date_text("20221202000000.000000+000") == "2022-12-02"
    assert hr.wmi_date_text(datetime.datetime(2025, 11, 12)) == "2025-11-12"
    assert hr.wmi_date_text(None) == ""


def test_colour_count_is_dropped():
    assert hr.video_mode_text("2560 x 1440 x 4294967296 colors") == "2560 x 1440"
    assert hr.video_mode_text("") == ""


def test_adapter_ram_is_never_negative(monkeypatch):
    monkeypatch.setattr(hr, "_registry_vram", lambda name: None)
    assert "-" not in hr.gpu_memory_text("x", -1048576)
    assert hr.gpu_memory_text("x", -1048576).startswith("4.0 GB")
    monkeypatch.setattr(hr, "_registry_vram", lambda name: 24 * 1024 ** 3)
    assert hr.gpu_memory_text("x", -1048576) == "24.0 GB"

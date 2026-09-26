"""Hardware asset engine: EDID, memory slot map, firmware findings, records."""
import csv
import io
import json
from datetime import date

import pytest

from modules.hardware_inventory import asset_parse as ap


def _edid(mfg="GBT", product=10044, serial=0, week=36, year=2025, w=59, h=33,
          name=b"MO27Q28G\n", text_serial=b"25362F004687\n"):
    letters = [ord(c) - ord("A") + 1 for c in mfg]
    word = (letters[0] << 10) | (letters[1] << 5) | letters[2]
    b = bytearray(128)
    b[0:8] = bytes([0, 255, 255, 255, 255, 255, 255, 0])
    b[8], b[9] = word >> 8, word & 0xFF                    # big-endian
    b[10], b[11] = product & 0xFF, product >> 8            # little-endian
    b[12:16] = serial.to_bytes(4, "little")
    b[16], b[17] = week, year - 1990
    b[21], b[22] = w, h
    for off, tag, text in ((54, 0xFC, name), (72, 0xFF, text_serial)):
        blk = bytearray(18)
        blk[3] = tag
        blk[5:5 + len(text)] = text
        if len(text) < 13:
            blk[5 + len(text):18] = b" " * (13 - len(text))
        b[off:off + 18] = blk
    return bytes(b)


def test_edid_fields_decode_with_correct_endianness():
    m = ap.parse_edid(_edid())
    assert m.manufacturer_id == "GBT" and m.make == "Gigabyte"
    assert m.product_code == 10044
    assert m.name == "MO27Q28G" and m.serial == "25362F004687"
    assert m.manufactured == "2025-W36"
    assert m.diagonal_inches == pytest.approx(26.6, abs=0.1)


def test_edid_descriptor_filled_to_13_bytes_and_nul_terminated():
    # The Gigabyte here fills all 13 bytes and ends with NUL, not newline.
    m = ap.parse_edid(_edid(text_serial=b"ABCDEFGHIJK\x00\x00"))
    assert m.serial == "ABCDEFGHIJK"
    assert "\x00" not in m.name + m.serial


def test_edid_falls_back_to_numeric_serial():
    m = ap.parse_edid(_edid(serial=292849, text_serial=b""))
    assert m.serial == "292849"


def test_not_an_edid_is_none():
    assert ap.parse_edid(b"\x00" * 128) is None
    assert ap.parse_edid(b"") is None
    assert ap.parse_edid(b"short") is None


def test_unknown_vendor_shows_its_three_letters():
    assert ap.parse_edid(_edid(mfg="QQQ")).make == "QQQ"


def _stick(loc="DIMM 1", cap=32 * 1024 ** 3, speed=4800, conf=6000, **kw):
    return dict(DeviceLocator=loc, BankLabel="P0", Capacity=cap, SMBIOSMemoryType=34,
                FormFactor=8, Speed=speed, ConfiguredClockSpeed=conf, DataWidth=64, TotalWidth=64, **kw)


def test_slot_map_adds_empty_rows_only_when_count_known():
    slots = ap.build_slot_map([_stick(), _stick("DIMM 2")], 4)
    assert [s.populated for s in slots] == [True, True, False, False]
    assert slots[0].mem_type == "DDR5" and slots[0].form_factor == "DIMM"
    assert len(ap.build_slot_map([_stick()], None)) == 1


def test_speed_notes_and_findings():
    above = ap.build_slot_map([_stick(conf=6000)], 1)[0]
    assert "above" in above.speed_note
    below = ap.build_slot_map([_stick(conf=4000)], 1)
    assert any(f.severity == "warning" for f in ap.memory_findings(below))
    mixed = ap.build_slot_map([_stick(cap=8 * 1024 ** 3), _stick("DIMM 2")], 2)
    assert any("Mixed" in f.title for f in ap.memory_findings(mixed))


def test_ecc_bits_detected_from_widths():
    ecc = ap.build_slot_map([dict(_stick(), TotalWidth=72)], 1)[0]
    assert ecc.ecc_bits
    assert ap.ecc_mode_name(3) == "None" and ap.ecc_mode_name(6) == "Multi-bit ECC"


def test_placeholders():
    for v in ("", "Default string", "To be filled by O.E.M.", "0000000000", "None"):
        assert ap.is_placeholder(v), v
    assert not ap.is_placeholder("M80-HC004600572")


def test_warranty_links():
    assert ap.warranty_lookup("Dell Inc.", "ABC1234")[1].endswith("/ABC1234/overview")
    assert ap.warranty_lookup("LENOVO", "PF1234")[0] == "Lenovo"
    # A placeholder serial must not be interpolated into a vendor URL.
    assert ap.warranty_lookup("Dell Inc.", "To be filled by O.E.M.") == ("", "")
    assert ap.warranty_lookup("Unknown Co", "X1") == ("", "")
    assert ap.warranty_lookup("ASRock", "Default string")[0] == "ASRock"
    assert "%2F" in ap.warranty_lookup("Dell Inc.", "A/B")[1]


def test_battery_wear():
    b = ap.BatteryHealth(50000, 40000)
    assert b.wear_percent == 20.0 and b.health_percent == 80.0
    assert ap.BatteryHealth(None, 40000).wear_percent is None
    assert ap.BatteryHealth(50000, 60000).wear_percent == 0.0


def test_firmware_findings():
    fw = ap.FirmwareInfo(firmware_mode="Legacy BIOS", secure_boot=False, tpm_present=False,
                         virtualization=False, bios_date=date(2015, 1, 1))
    titles = [f.title for f in ap.firmware_findings(fw, today=date(2026, 9, 1))]
    assert {"Legacy BIOS boot", "Secure Boot is off", "No TPM detected"} <= set(titles)
    assert any("virtualization" in t for t in titles) and any("5 years" in t for t in titles)


def test_unreadable_state_is_reported_not_treated_as_off():
    fw = ap.FirmwareInfo(secure_boot=None, secure_boot_reason="refused", tpm_present=None,
                         tpm_reason="needs admin")
    f = ap.firmware_findings(fw)
    assert not any("Secure Boot is off" == x.title for x in f)
    assert any("could not be read" in x.title for x in f) and len(f) == 2


def test_tpm_1_2_is_flagged():
    fw = ap.FirmwareInfo(tpm_present=True, tpm_version="1.2")
    assert any("not version 2.0" in f.title for f in ap.firmware_findings(fw))


def _record():
    slots = ap.build_slot_map([_stick()], 2)
    return ap.build_asset_record(
        hostname="H", manufacturer="Dell Inc.", model="XPS", serial="ABC1234", cpu="CPU",
        ram_bytes=32 * 1024 ** 3, slots=slots,
        disks=[{"Model": "Virtual Disk", "Size": "1.0 GB", "Serial": ""},
               {"Model": "SSD", "Size": "1.0 TB", "Serial": "S1"},
               {"Model": "SSD", "Size": "1.0 TB", "Serial": "S1"},
               {"Model": "Card Reader", "Size": "N/A", "Serial": "x"}],
        monitors=[ap.parse_edid(_edid(mfg="DEL", name=b"Dell S2719DGF\n"))],
        os_name="Win11", bios="1.0", fw=ap.FirmwareInfo(firmware_mode="UEFI", secure_boot=True,
                                                         tpm_present=True, tpm_version="2.0"),
        generated="2026-01-01")


def test_asset_record_filters_virtual_and_duplicate_drives():
    r = _record()
    assert r["Storage"].count("SSD") == 1 and "Virtual" not in r["Storage"] and "Card" not in r["Storage"]
    assert r["Secure Boot"] == "on" and r["TPM"] == "2.0" and r["Boot mode"] == "UEFI"
    assert r["Warranty lookup"].startswith("Dell:")
    assert r["Monitors"].startswith("Dell S2719DGF")      # make not repeated


def test_export_formats_round_trip():
    r = _record()
    assert json.loads(ap.record_to_json(r)) == r
    rows = list(csv.reader(io.StringIO(ap.record_to_csv(r))))
    assert rows[0] == list(r.keys()) and rows[1] == list(r.values())
    md = ap.record_to_markdown({"Hostname": "H", "Storage": "a | b"})
    assert "a \\| b" in md and md.startswith("### Asset record: H")


def test_placeholder_serial_is_not_exported():
    r = ap.build_asset_record(
        hostname="H", manufacturer="X", model="M", serial="Default string", cpu="", ram_bytes=0,
        slots=[], disks=[], monitors=[], os_name="", bios="", fw=ap.FirmwareInfo())
    assert r["Serial number"] == ""


def test_real_machine_monitors_and_firmware_are_plausible():
    pytest.importorskip("wmi")
    import pythoncom
    from modules.hardware_inventory import asset_reader as ar
    pythoncom.CoInitialize()
    try:
        monitors, _err = ar.read_monitors()
        for m in monitors:
            assert len(m.manufacturer_id) == 3
            assert m.year == 0 or 1995 <= m.year <= date.today().year
            assert m.diagonal_inches is None or 5 < m.diagonal_inches < 120
        slots = ar.read_memory_slots()
        for s in slots.slots:
            if s.populated:
                assert s.capacity_bytes >= 512 * 1024 ** 2
        fw = ar.read_firmware()
        assert fw.firmware_mode in (None, "UEFI", "Legacy BIOS")
        battery, note = ar.read_battery()
        assert battery is not None or note
    finally:
        pythoncom.CoUninitialize()

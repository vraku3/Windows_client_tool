r"""Naming a monitor: `DisplayConfigGetDeviceInfo`, against this machine.

`display_config` answers *what* is on the desktop; nothing in it can say
which of three 2560x1440 rows is the Dell. That is this module's job, and
the one thing that will silently ruin it is the EDID manufacturer id:

* **`edidManufactureId` arrives byte-swapped.** The raw USHORT for the Dell
  here is `0xAC10`; decoding that five-bits-per-letter gives `K@P`. Swapped
  to `0x10AC` it decodes to `DEL`, which is Dell's real PNP id. `K@P` is not
  an error, it is three printable characters -- nothing downstream can tell
  it is wrong, so it is pinned here for all three panels on this machine.

The other rules are this project's standing ones, applied to an API that
breaks both of them cheerfully: a non-zero return is a refusal and must not
surface as a blank name, and `rc == 0` with a zeroed buffer is not an answer
either.
"""
import json
import pathlib

import pytest

from modules.monitor_control import display_config as dc
from modules.monitor_control import monitor_identity as mi

EDID_FIXTURE = (pathlib.Path(__file__).resolve().parent / "data"
                / "edid_blobs.json")


# -- the byte swap, which is the whole trap ----------------------------
#
# (raw USHORT as Windows returns it, the swapped value, the PNP id it is)
EDID_IDS = [
    (0xAC10, 0x10AC, "DEL"),   # Dell       -- S2719DGF, target 256
    (0x541C, 0x1C54, "GBT"),   # Gigabyte   -- MO27Q28G, target 264
    (0x6D1E, 0x1E6D, "GSM"),   # LG Electr. -- LG ULTRAWIDE, target 265
]


@pytest.mark.parametrize("raw,swapped,_name", EDID_IDS)
def test_the_raw_id_is_byte_swapped(raw, swapped, _name):
    assert mi.byte_swap16(raw) == swapped


@pytest.mark.parametrize("_raw,swapped,name", EDID_IDS)
def test_the_swapped_id_decodes_to_the_pnp_id(_raw, swapped, name):
    assert mi.decode_manufacturer_id(swapped) == name


@pytest.mark.parametrize("raw,_swapped,name", EDID_IDS)
def test_manufacturer_from_edid_swaps_then_decodes(raw, _swapped, name):
    assert mi.manufacturer_from_edid(raw) == name


@pytest.mark.parametrize("raw,_swapped,name", EDID_IDS)
def test_decoding_without_the_swap_is_plausible_garbage(raw, _swapped, name):
    """Not an exception -- three printable characters that are simply wrong."""
    wrong = mi.decode_manufacturer_id(raw)
    assert wrong != name
    assert len(wrong) == 3


def test_the_dell_id_decoded_unswapped_is_exactly_the_garbage_seen():
    assert mi.decode_manufacturer_id(0xAC10) == "K@P"


def test_five_bits_per_letter_with_a_equal_to_one():
    #  A=1, B=2, C=3  ->  (1 << 10) | (2 << 5) | 3
    assert mi.decode_manufacturer_id((1 << 10) | (2 << 5) | 3) == "ABC"


# -- building the record, without touching the API ---------------------

def _built(**over):
    fields = dict(
        target_id=256,
        flags=mi.TARGET_NAME_FLAG_FRIENDLY_NAME_FROM_EDID
              | mi.TARGET_NAME_FLAG_EDID_IDS_VALID,
        output_technology=0x0A,
        edid_manufacture_id=0xAC10,
        edid_product_code_id=0x41A8,
        connector_instance=0,
        friendly_name="S2719DGF",
        device_path=r"\\?\DISPLAY#DEL41A8#5&12345#{guid}",
    )
    fields.update(over)
    return mi.build_target_name(**fields)


def test_a_built_record_carries_the_swapped_manufacturer():
    assert _built().manufacturer_id == "DEL"


def test_the_edid_ids_valid_flag_is_read_from_bit_two():
    assert _built().edid_valid is True
    assert _built(flags=0x1).edid_valid is False


def test_invalid_edid_ids_are_reported_as_unknown_not_as_garbage():
    """Decoding an id the API says is invalid gives a real-looking string."""
    record = _built(flags=0x1)
    assert record.manufacturer_id is None
    assert record.product_code is None


def test_a_valid_product_code_is_an_int():
    assert _built().product_code == 0x41A8


def test_an_empty_friendly_name_falls_back_to_something_usable():
    record = _built(friendly_name="", flags=0x4)
    assert record.friendly_name
    assert record.friendly_name.strip() == record.friendly_name


def test_the_fallback_prefers_the_device_path_over_a_bare_number():
    record = _built(friendly_name="", flags=0x4)
    assert "DEL41A8" in record.friendly_name


def test_with_no_name_and_no_path_the_fallback_still_names_the_target():
    record = mi.build_target_name(
        target_id=265, flags=0, output_technology=0, edid_manufacture_id=0,
        edid_product_code_id=0, connector_instance=0,
        friendly_name="", device_path="")
    assert record.friendly_name
    assert "265" in record.friendly_name


# -- refusals ----------------------------------------------------------

def test_a_non_zero_return_is_a_failure_not_an_empty_name(monkeypatch):
    monkeypatch.setattr(mi, "_device_info", lambda packet: 5)  # ACCESS_DENIED
    assert mi.target_name((0, 0), 256) is None
    assert mi.source_gdi_name((0, 0), 0) is None
    assert mi.adapter_name((0, 0)) is None


def test_rc_zero_with_an_empty_buffer_is_not_an_answer(monkeypatch):
    """The API succeeding is not the same as the API telling us anything."""
    monkeypatch.setattr(mi, "_device_info", lambda packet: 0)
    assert mi.source_gdi_name((0, 0), 0) is None
    assert mi.adapter_name((0, 0)) is None


def test_a_refusal_names_the_error_in_the_log(monkeypatch, caplog):
    monkeypatch.setattr(mi, "_device_info", lambda packet: 5)
    with caplog.at_level("WARNING"):
        mi.target_name((0, 0), 256)
    assert "ERROR_ACCESS_DENIED" in caplog.text


def test_rc_zero_still_returns_a_record_when_the_buffer_has_content(monkeypatch):
    """Guard against the refusal tests passing for the wrong reason."""
    def fake(packet):
        packet.monitorFriendlyDeviceName = "S2719DGF"
        packet.monitorDevicePath = r"\\?\DISPLAY#DEL41A8#5&1#{g}"
        packet.edidManufactureId = 0xAC10
        packet.edidProductCodeId = 0x41A8
        packet.flags = 0x5
        packet.outputTechnology = 0x0A
        return 0

    monkeypatch.setattr(mi, "_device_info", fake)
    record = mi.target_name((0, 0), 256)
    assert record is not None
    assert record.friendly_name == "S2719DGF"
    assert record.manufacturer_id == "DEL"


# -- the EDID, against three real blobs --------------------------------
#
# Captured from this machine's registry into tests/data/edid_blobs.json,
# complete and unmodified, the way the topology fixture was captured. A
# synthetic blob would agree with whatever the parser did.

EDID_BLOBS = json.loads(EDID_FIXTURE.read_text(encoding="utf-8"))["monitors"]

#: The panels' real native resolutions. The Dell and the Gigabyte are 1440p;
#: the LG is an ultrawide, which is exactly why "biggest enumerated mode" is
#: not a usable definition of native.
NATIVE = {"DELD0E6": (2560, 1440),
          "GBT273C": (2560, 1440),
          "GSM59F1": (2560, 1080)}


def _blob(pnp):
    return bytes.fromhex(EDID_BLOBS[pnp]["edid_hex"])


@pytest.mark.parametrize("pnp", sorted(NATIVE))
def test_the_preferred_timing_is_the_panel_native_resolution(pnp):
    assert mi.parse_edid_preferred_timing(_blob(pnp)) == NATIVE[pnp]


def test_a_blob_with_an_extension_block_is_still_read():
    """The Gigabyte's blob is 384 bytes; the base block is the first 128."""
    assert len(_blob("GBT273C")) == 384
    assert mi.parse_edid_preferred_timing(_blob("GBT273C")) == (2560, 1440)


def test_a_broken_header_is_not_trusted():
    corrupt = bytearray(_blob("DELD0E6"))
    corrupt[0] = 0x01
    assert mi.parse_edid_preferred_timing(bytes(corrupt)) is None


def test_a_failed_checksum_is_not_trusted():
    """The bytes still decode to a plausible 2560x1440. They are not believed."""
    corrupt = bytearray(_blob("DELD0E6"))
    corrupt[100] ^= 0xFF
    assert sum(corrupt[:128]) % 256 != 0
    assert mi.parse_edid_preferred_timing(bytes(corrupt)) is None


@pytest.mark.parametrize("blob", [b"", b"\x00" * 64,
                                  bytes.fromhex("00ffffffffffff00") + b"\x00" * 40])
def test_a_short_or_empty_blob_is_none(blob):
    assert mi.parse_edid_preferred_timing(blob) is None


def test_an_all_zero_descriptor_is_not_a_zero_by_zero_panel():
    """A base block that checksums but carries no timing must answer None."""
    blob = bytearray(128)
    blob[0:8] = bytes.fromhex("00ffffffffffff00")
    blob[127] = (-sum(blob[:127])) % 256
    assert sum(blob) % 256 == 0
    assert mi.parse_edid_preferred_timing(bytes(blob)) is None


# -- finding the blob from the device path ------------------------------

@pytest.mark.parametrize("pnp", sorted(NATIVE))
def test_the_registry_instance_comes_out_of_the_device_path(pnp):
    entry = EDID_BLOBS[pnp]
    assert mi.device_instance_from_path(entry["device_path"]) == \
        entry["registry_instance"]


@pytest.mark.parametrize("path", ["", "nonsense", r"\\?\USB#VID_046D#5&1#{g}",
                                  r"\\?\DISPLAY#DELD0E6"])
def test_a_path_that_is_not_a_display_instance_is_none(path):
    assert mi.device_instance_from_path(path) is None


def test_an_unreadable_edid_is_none_not_a_resolution(monkeypatch):
    monkeypatch.setattr(mi, "read_edid", lambda device_path: None)
    assert mi.native_resolution(
        EDID_BLOBS["DELD0E6"]["device_path"]) is None


def test_native_resolution_parses_what_the_registry_returned(monkeypatch):
    monkeypatch.setattr(mi, "read_edid", lambda device_path: _blob("GSM59F1"))
    assert mi.native_resolution("anything") == (2560, 1080)


# -- against the real machine ------------------------------------------

EXPECTED_NAMES = {256: "S2719DGF", 264: "MO27Q28G", 265: "LG ULTRAWIDE"}
EXPECTED_MAKERS = {256: "DEL", 264: "GBT", 265: "GSM"}


def _live_monitors():
    try:
        monitors = dc.query().monitors()
    except OSError as exc:                      # DisplayConfigError included
        pytest.skip(f"QueryDisplayConfig is unavailable here: {exc}")
    if not monitors:
        pytest.skip("no monitors reported by QueryDisplayConfig")
    return {m.target_id: m for m in monitors}


def _live_monitor(target_id):
    monitors = _live_monitors()
    if target_id not in monitors:
        pytest.skip(f"target {target_id} is not attached to this machine")
    return monitors[target_id]


@pytest.mark.parametrize("target_id", sorted(EXPECTED_NAMES))
def test_live_friendly_names(target_id):
    monitor = _live_monitor(target_id)
    record = mi.target_name(monitor.adapter, target_id)
    assert record is not None, "DisplayConfigGetDeviceInfo refused"
    assert record.friendly_name == EXPECTED_NAMES[target_id]


@pytest.mark.parametrize("target_id", sorted(EXPECTED_MAKERS))
def test_live_manufacturer_ids_need_the_swap(target_id):
    monitor = _live_monitor(target_id)
    record = mi.target_name(monitor.adapter, target_id)
    assert record is not None
    assert record.edid_valid is True
    assert record.manufacturer_id == EXPECTED_MAKERS[target_id]


def test_live_every_monitor_has_a_device_path():
    for target_id, monitor in _live_monitors().items():
        record = mi.target_name(monitor.adapter, target_id)
        assert record is not None
        assert record.device_path.startswith("\\\\?\\")


def test_live_the_gdi_bridge_is_not_the_display_number():
    r"""source_id 0 -> \\.\DISPLAY1 and 1 -> \\.\DISPLAY2 here, but that is
    a fact about this machine, not a rule: the id is an index, not a name."""
    try:
        topology = dc.query()
    except OSError as exc:
        pytest.skip(f"QueryDisplayConfig is unavailable here: {exc}")
    seen = {}
    for path in topology.active_paths():
        name = mi.source_gdi_name(path.adapter, path.source_id)
        assert name, f"no GDI name for source {path.source_id}"
        assert name.startswith("\\\\.\\DISPLAY")
        seen[path.source_id] = name
    if 0 in seen:
        assert seen[0] == "\\\\.\\DISPLAY1"
    if 1 in seen:
        assert seen[1] == "\\\\.\\DISPLAY2"
    assert len(set(seen.values())) == len(seen), "two sources, one GDI name"


EXPECTED_NATIVE = {256: (2560, 1440), 264: (2560, 1440), 265: (2560, 1080)}


@pytest.mark.parametrize("target_id", sorted(EXPECTED_NATIVE))
def test_live_the_native_resolution_comes_from_the_real_edid(target_id):
    monitor = _live_monitor(target_id)
    record = mi.target_name(monitor.adapter, target_id)
    assert record is not None
    assert mi.native_resolution(record.device_path) == EXPECTED_NATIVE[target_id]


def test_live_the_edid_read_is_a_real_registry_read():
    """Guard against the live test above passing off the fixture as a read."""
    monitor = _live_monitor(256)
    record = mi.target_name(monitor.adapter, 256)
    blob = mi.read_edid(record.device_path)
    assert blob is not None, "the EDID could not be read from the registry"
    assert blob[:8] == bytes.fromhex("00ffffffffffff00")
    assert sum(blob[:128]) % 256 == 0


def test_live_the_native_resolution_is_not_the_biggest_mode_offered():
    r"""The whole reason this exists.

    The Dell enumerates 3840x2160 because the AMD driver will downsample to
    it. The panel is 2560x1440, and a "best mode" built on the enumeration
    alone recommends the blurrier, slower one.
    """
    from modules.monitor_control import display_modes as dm

    # Whichever panel currently exercises it, not a hardcoded target. Which
    # monitor offers downsampled modes is not fixed: on this machine the
    # panel doing so changed during a single session, along with the
    # `\\.\DISPLAYn` names and the mode lists themselves.
    for path in dc.query().active_paths():
        record = mi.target_name(path.adapter, path.target_id)
        gdi = mi.source_gdi_name(path.adapter, path.source_id)
        if record is None or gdi is None:
            continue
        native = mi.native_resolution(record.device_path)
        offered = dm.resolutions(gdi)
        if not native or not offered:
            continue
        biggest = offered[0]
        if biggest == native:
            continue
        # Found one: the driver offers more pixels than the glass has.
        assert biggest[0] * biggest[1] > native[0] * native[1]
        assert dm.best_mode(gdi, native=native).resolution == native
        assert dm.best_mode(gdi).resolution == biggest
        return
    pytest.skip("no panel here is currently offered a mode above its native")


def test_live_the_adapter_is_named():
    monitors = _live_monitors()
    monitor = next(iter(monitors.values()))
    name = mi.adapter_name(monitor.adapter)
    assert name, "adapter name could not be read"
    assert "\\" in name

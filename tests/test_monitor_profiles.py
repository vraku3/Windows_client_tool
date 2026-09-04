r"""Display profiles: what identifies a monitor, and when a profile refuses.

The whole module hangs off one decision, so most of these tests defend it:
**a monitor is identified by its EDID, never by `\\.\DISPLAY2`, never by a
CCD target id, never by a path index.** Those three all renumber. On this
machine the primary is `\\.\DISPLAY1` and the secondary `\\.\DISPLAY2`; swap
the two DisplayPort cables and the names swap with them while the monitors
have not moved at all. A profile that restored 2560x1440@144 "to DISPLAY2"
would then configure the other panel — which is worse than refusing, because
it looks like it worked.

The EDID blobs below are the real ones read out of
`HKLM\SYSTEM\CurrentControlSet\Enum\DISPLAY\...\Device Parameters\EDID` on
this machine (readable unelevated), trimmed to the 128-byte base block. They
are here rather than invented because two of the byte orders are not
guessable: the manufacturer id is BIG-endian and the product code that
follows it is LITTLE-endian, in the same struct, two bytes apart.
"""
from __future__ import annotations

import json

import pytest

from modules.monitor_control import profiles as P

# ── real EDID base blocks from this machine ────────────────────────────
#
# Only the fields the identity uses are filled in; the rest is zeroed. The
# header, manufacturer, product, serial and the descriptor blocks are byte
# for byte what the registry holds.


def _edid(mfg: str, product: int, serial32: int,
          descriptors: list[tuple[int, bytes]]) -> bytes:
    """Assemble a 128-byte base block. Mirrors the real layout exactly."""
    blob = bytearray(128)
    blob[0:8] = b"\x00\xff\xff\xff\xff\xff\xff\x00"
    packed = 0
    for i, ch in enumerate(mfg):
        packed |= (ord(ch) - 64) << (10 - 5 * i)
    blob[8:10] = packed.to_bytes(2, "big")        # BIG endian
    blob[10:12] = product.to_bytes(2, "little")   # LITTLE endian
    blob[12:16] = serial32.to_bytes(4, "little")
    for offset, (tag, text) in zip((54, 72, 90, 108), descriptors):
        blob[offset:offset + 3] = b"\x00\x00\x00"
        blob[offset + 3] = tag
        blob[offset + 4] = 0x00
        body = text[:13].ljust(13, b" ") if len(text) < 13 else text[:13]
        blob[offset + 5:offset + 18] = body
    return bytes(blob)


#: Gigabyte MO27Q28G. Its 4-byte numeric serial is the 0x01010101 filler —
#: the only real serial it has is the 0xFF descriptor.
#:
#: That descriptor is **NUL-terminated, not newline-terminated**, and fills
#: all 13 bytes: `b"25362F004687\x00"`. EDID 1.4 says terminate with 0x0A and
#: pad with 0x20, which is what the Dell below actually does — this panel
#: does not. Read off the real registry blob on 2026-09-04; the fixture said
#: `\n` for a long time, which is why nothing caught the NUL surviving into
#: the key.
EDID_GIGABYTE = _edid("GBT", 0x273C, 0x01010101,
                      [(0xFC, b"MO27Q28G\n"), (0xFF, b"25362F004687\x00")])

#: Dell S2719DGF. Carries both a real numeric serial and an 0xFF descriptor.
EDID_DELL = _edid("DEL", 0xD0E6, 808797013,
                  [(0xFC, b"S2719DGF\n"), (0xFF, b"8DYM7P2\n")])

#: LG ULTRAWIDE. No 0xFF descriptor at all — the numeric serial is all there
#: is, and a parser that only reads descriptors leaves this monitor
#: unidentified.
EDID_LG = _edid("GSM", 0x59F1, 292849, [(0xFC, b"LG ULTRAWIDE\n")])


# ── EDID parsing ───────────────────────────────────────────────────────

def test_the_manufacturer_is_big_endian_and_the_product_is_not():
    """Two bytes apart, opposite byte orders. Reading either the other way
    round yields a plausible-looking id that matches no monitor."""
    edid = P.parse_edid(EDID_DELL)
    assert edid.manufacturer == "DEL"
    assert edid.product_code == "D0E6"


@pytest.mark.parametrize("blob,mfg,product", [
    (EDID_GIGABYTE, "GBT", "273C"),
    (EDID_DELL, "DEL", "D0E6"),
    (EDID_LG, "GSM", "59F1"),
])
def test_the_parsed_id_matches_the_windows_hardware_id(blob, mfg, product):
    r"""Windows builds `DISPLAY#GBT273C#...` from these same two fields, so
    a correct parse reproduces the hardware id in the device path."""
    edid = P.parse_edid(blob)
    assert edid.manufacturer + edid.product_code == mfg + product


def test_a_descriptor_serial_is_preferred_over_the_numeric_one():
    edid = P.parse_edid(EDID_DELL)
    assert edid.serial == "8DYM7P2"
    assert edid.serial_source == P.SERIAL_FROM_DESCRIPTOR


def test_a_nul_terminated_descriptor_does_not_carry_the_nul_into_the_key():
    r"""The Gigabyte terminates its 0xFF descriptor with `\x00` and fills all
    13 bytes, where the spec says 0x0A padded with spaces.

    `.strip()` does not remove NUL — it is not whitespace — so the serial
    came out as `'25362F004687\x00'` and the EDID key with it. That key is
    written into profile JSON, compared against live monitors, and shown in
    labels, and it is exactly the "key that looks stable and is not" this
    module exists to prevent: anything that normalises the NUL away stops
    matching, and a profile silently declines to apply.
    """
    edid = P.parse_edid(EDID_GIGABYTE)
    assert edid.serial == "25362F004687"
    assert "\x00" not in (edid.serial or "")


def test_no_descriptor_text_keeps_a_control_character():
    """The model name comes through the same parser as the serial."""
    for blob in (EDID_GIGABYTE, EDID_DELL, EDID_LG):
        edid = P.parse_edid(blob)
        for text in (edid.serial or "", edid.model_name):
            assert not any(ch < " " for ch in text), repr(text)


def test_a_filler_numeric_serial_is_not_mistaken_for_a_serial():
    """0x01010101 is what the Gigabyte reports. Four identical bytes is
    filler, not an identity — but its 0xFF descriptor is real."""
    edid = P.parse_edid(EDID_GIGABYTE)
    assert edid.serial == "25362F004687"


def test_a_monitor_with_no_descriptor_serial_falls_back_to_the_numeric_one():
    """The LG has no 0xFF block. Giving up here would leave it keyed on
    model alone, which two identical LGs would share."""
    edid = P.parse_edid(EDID_LG)
    assert edid.serial == "292849"
    assert edid.serial_source == P.SERIAL_FROM_NUMERIC


def test_a_blob_that_is_not_an_edid_is_refused_not_guessed():
    with pytest.raises(P.EdidError):
        P.parse_edid(b"\x00" * 128)


def test_a_truncated_blob_is_refused():
    with pytest.raises(P.EdidError):
        P.parse_edid(EDID_DELL[:60])


# ── identity ───────────────────────────────────────────────────────────

def _identity(blob, path, name):
    edid = P.parse_edid(blob)
    return P.MonitorIdentity(
        manufacturer=edid.manufacturer,
        product_code=edid.product_code,
        serial=edid.serial,
        serial_source=edid.serial_source,
        friendly_name=name,
        device_path=path,
    )


DELL = _identity(
    EDID_DELL,
    r"\\?\DISPLAY#DELD0E6#7&327e9bec&0&UID256#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}",
    "S2719DGF")
GIGABYTE = _identity(
    EDID_GIGABYTE,
    r"\\?\DISPLAY#GBT273C#7&327e9bec&0&UID264#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}",
    "MO27Q28G")
LG = _identity(
    EDID_LG,
    r"\\?\DISPLAY#GSM59F1#7&327e9bec&0&UID265#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}",
    "LG ULTRAWIDE")


def test_the_key_is_built_from_the_edid_alone():
    assert DELL.key == "DEL-D0E6-8DYM7P2"


def test_the_key_contains_no_display_number_and_no_target_id():
    r"""The three unstable identifiers, named so this cannot regress
    silently: `\\.\DISPLAYn`, the CCD target id, and the UID in the device
    path (which encodes the connector, so it changes when a cable moves)."""
    for identity in (DELL, GIGABYTE, LG):
        assert "DISPLAY" not in identity.key
        assert "UID" not in identity.key
        assert "\\" not in identity.key


def test_an_unidentifiable_monitor_says_so_rather_than_keying_on_the_path():
    """A monitor whose EDID could not be read is NOT quietly keyed on its
    device path. It carries the reason, and `identified` is False — the
    difference between 'could not determine' and a value."""
    unknown = P.MonitorIdentity(
        manufacturer="DEL", product_code="D0E6", serial=None,
        serial_source="", friendly_name="S2719DGF",
        device_path=DELL.device_path,
        reason="EDID not readable: registry key absent")
    assert unknown.identified is False
    assert unknown.reason
    assert unknown.key == "DEL-D0E6"


# ── profiles ───────────────────────────────────────────────────────────

def _monitor(identity, *, active=True, resolution=(2560, 1440), refresh=143.998,
             position=(0, 0), orientation=P.ROTATION_IDENTITY, primary=False):
    return P.ProfileMonitor(
        identity=identity, active=active, resolution=resolution,
        refresh_hz=refresh, position=position, orientation=orientation,
        primary=primary, target_id=256, adapter=(65601, 0))


def _profile(name="desk"):
    return P.DisplayProfile(name=name, created_at="2026-09-03T12:00:00", monitors=[
        _monitor(GIGABYTE, position=(0, 0), primary=True),
        _monitor(DELL, position=(2560, 0)),
        _monitor(LG, active=False, resolution=None, refresh=0.0, position=None),
    ])


def test_a_profile_records_every_field_the_topology_has():
    monitor = _profile().monitors[0]
    assert monitor.resolution == (2560, 1440)
    assert monitor.refresh_hz == pytest.approx(143.998)
    assert monitor.position == (0, 0)
    assert monitor.orientation == P.ROTATION_IDENTITY
    assert monitor.primary is True


def test_a_profile_survives_a_round_trip_through_disk(tmp_path):
    original = _profile()
    P.save_profile(original, directory=str(tmp_path))
    loaded = P.load_profile("desk", directory=str(tmp_path))
    assert loaded == original


def test_the_round_trip_preserves_the_inactive_monitor(tmp_path):
    """A profile that dropped its inactive monitors could not describe
    'the LG is unplugged in this layout', only 'these two are on'."""
    P.save_profile(_profile(), directory=str(tmp_path))
    loaded = P.load_profile("desk", directory=str(tmp_path))
    off = [m for m in loaded.monitors if not m.active]
    assert [m.identity.key for m in off] == [LG.key]
    assert off[0].resolution is None


def test_the_saved_file_is_json_and_keys_on_edid(tmp_path):
    path = P.save_profile(_profile(), directory=str(tmp_path))
    data = json.loads(open(path, encoding="utf-8").read())
    keys = [m["identity"]["key"] for m in data["monitors"]]
    assert keys == ["GBT-273C-25362F004687", "DEL-D0E6-8DYM7P2", "GSM-59F1-292849"]


def test_a_profile_written_by_another_version_still_loads(tmp_path):
    """Unknown fields ignored, missing ones defaulted. The failure mode this
    prevents is a user losing their profiles on upgrade."""
    P.save_profile(_profile(), directory=str(tmp_path))
    path = P.profile_path("desk", directory=str(tmp_path))
    data = json.loads(open(path, encoding="utf-8").read())
    data["invented_by_the_future"] = True
    data["monitors"][0].pop("orientation")
    open(path, "w", encoding="utf-8").write(json.dumps(data))
    loaded = P.load_profile("desk", directory=str(tmp_path))
    assert loaded.monitors[0].orientation == P.ROTATION_UNSPECIFIED


def test_listing_profiles_names_them(tmp_path):
    P.save_profile(_profile("desk"), directory=str(tmp_path))
    P.save_profile(_profile("laptop only"), directory=str(tmp_path))
    assert sorted(p.name for p in P.list_profiles(directory=str(tmp_path))) == [
        "desk", "laptop only"]


def test_listing_reports_the_monitor_count_without_loading_the_payload(tmp_path):
    P.save_profile(_profile("desk"), directory=str(tmp_path))
    summary = P.list_profiles(directory=str(tmp_path))[0]
    assert summary.monitor_count == 3
    assert summary.active_count == 2


def test_listing_an_empty_directory_is_empty_not_an_error(tmp_path):
    assert P.list_profiles(directory=str(tmp_path)) == []


def test_deleting_a_profile_removes_it(tmp_path):
    P.save_profile(_profile(), directory=str(tmp_path))
    assert P.delete_profile("desk", directory=str(tmp_path)) is True
    assert P.list_profiles(directory=str(tmp_path)) == []


def test_deleting_a_profile_that_is_not_there_is_false_not_an_exception(tmp_path):
    assert P.delete_profile("never existed", directory=str(tmp_path)) is False


def test_a_name_with_path_separators_cannot_escape_the_profile_directory(tmp_path):
    with pytest.raises(ValueError):
        P.save_profile(_profile(r"..\..\evil"), directory=str(tmp_path))


def test_profiles_live_under_the_apps_own_appdata_directory(monkeypatch):
    r"""The same `%APPDATA%\WindowsTweaker` every other persisted thing in
    this app uses — computed, not imported from `app`, so this module stays
    testable with no Qt."""
    monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
    assert P.default_profile_dir() == (
        r"C:\Users\test\AppData\Roaming\WindowsTweaker\monitor_profiles")


# ── can_apply: the refusal ─────────────────────────────────────────────

def test_a_profile_whose_monitors_are_all_present_can_be_applied():
    ok, reason = P.can_apply(_profile(), present=[GIGABYTE, DELL, LG])
    assert ok is True
    assert reason


def test_a_missing_monitor_is_refused_and_named():
    """The reason has to name the monitor, not the count. 'One monitor is
    missing' sends someone to look at three cables."""
    ok, reason = P.can_apply(_profile(), present=[GIGABYTE])
    assert ok is False
    assert "S2719DGF" in reason
    assert DELL.key in reason


def test_every_missing_monitor_is_named_not_just_the_first():
    ok, reason = P.can_apply(_profile(), present=[])
    assert ok is False
    assert "S2719DGF" in reason and "MO27Q28G" in reason


def test_a_monitor_the_profile_records_as_off_need_not_be_present():
    """The LG is recorded switched off. Requiring it would make this profile
    unusable for exactly the situation it describes."""
    ok, reason = P.can_apply(_profile(), present=[GIGABYTE, DELL])
    assert ok is True
    assert "LG ULTRAWIDE" in reason  # still disclosed, not silently ignored


def test_a_present_monitor_that_matches_two_profile_entries_is_refused():
    """Two identical panels whose EDIDs carry no serial share a key. Applying
    would be a coin flip over which one gets which resolution."""
    twin = P.MonitorIdentity(manufacturer="DEL", product_code="D0E6",
                             serial=None, serial_source="",
                             friendly_name="S2719DGF", device_path="a",
                             reason="EDID carries no serial number")
    profile = P.DisplayProfile(name="twins", created_at="x", monitors=[
        _monitor(twin, position=(0, 0)), _monitor(twin, position=(2560, 0))])
    ok, reason = P.can_apply(profile, present=[twin, twin])
    assert ok is False
    assert "ambiguous" in reason.lower()


def test_a_profile_with_no_monitors_is_refused():
    ok, reason = P.can_apply(
        P.DisplayProfile(name="empty", created_at="x", monitors=[]), present=[DELL])
    assert ok is False
    assert reason


def test_apply_refuses_before_touching_anything_when_a_monitor_is_absent():
    """`apply_profile` must not be a second, weaker gate: it asks `can_apply`
    and raises, so there is no path that half-configures a desktop."""
    writes = []
    with pytest.raises(P.ProfileRefused) as excinfo:
        P.apply_profile(_profile(), present=[GIGABYTE], confirm=True,
                        writer=lambda *a, **k: writes.append(a))
    assert "S2719DGF" in str(excinfo.value)
    assert writes == []


#: EDID key -> GDI device name, supplied to `apply_profile` so a plan can be
#: built and asserted against without those monitors being attached. The plan
#: is pure decision-making; needing the hardware to check it would mean the
#: only machine that could run these tests is this one, on a day when nothing
#: had been unplugged.
FAKE_DEVICES = {
    GIGABYTE.key: "\\\\.\\DISPLAY1",
    DELL.key: "\\\\.\\DISPLAY2",
    LG.key: "\\\\.\\DISPLAY3",
}


def test_apply_will_not_write_without_an_explicit_confirmation():
    """Belt and braces around a call that reconfigures the user's desktop:
    the default is a plan, never a write."""
    writes = []
    plan = P.apply_profile(_profile(), present=[GIGABYTE, DELL, LG],
                           writer=lambda *a, **k: writes.append(a),
                           devices=FAKE_DEVICES)
    assert writes == []
    assert plan.would_change


def test_the_apply_plan_addresses_monitors_by_identity_not_by_index():
    plan = P.apply_profile(_profile(), present=[GIGABYTE, DELL, LG],
                           devices=FAKE_DEVICES)
    assert [step.monitor_key for step in plan.steps] == [
        GIGABYTE.key, DELL.key, LG.key]


def test_the_apply_plan_detaches_the_monitor_the_profile_records_as_off():
    plan = P.apply_profile(_profile(), present=[GIGABYTE, DELL, LG],
                           devices=FAKE_DEVICES)
    off = [s for s in plan.steps if s.monitor_key == LG.key][0]
    assert off.detach is True
    assert off.resolution is None


def test_the_apply_plan_carries_exactly_one_primary():
    plan = P.apply_profile(_profile(), present=[GIGABYTE, DELL, LG],
                           devices=FAKE_DEVICES)
    assert sum(1 for s in plan.steps if s.primary) == 1


# ── against the real machine, read-only ────────────────────────────────

@pytest.fixture
def live_profile():
    try:
        profile = P.capture_profile("live-test")
    except OSError as exc:
        pytest.skip(f"no display topology available: {exc}")
    if not profile.monitors:
        pytest.skip("no monitors reported")
    return profile


def test_live_capture_finds_the_monitors_that_are_on(live_profile):
    assert any(m.active for m in live_profile.monitors)


def test_live_capture_identifies_every_monitor_or_says_why_not(live_profile):
    """Read-only. A monitor whose EDID could not be read must carry a reason
    — an empty serial with no explanation is the bug this pins."""
    for monitor in live_profile.monitors:
        assert monitor.identity.manufacturer
        assert monitor.identity.identified or monitor.identity.reason


def test_live_keys_are_unique(live_profile):
    keys = [m.identity.key for m in live_profile.monitors]
    assert len(keys) == len(set(keys)), f"colliding identities: {keys}"


def test_live_capture_can_be_applied_back_to_the_machine_it_came_from(live_profile):
    """Not applied — asked. Capturing and immediately asking `can_apply`
    against the same machine must say yes, or the identity matching is
    broken in a way no synthetic test would show."""
    ok, reason = P.can_apply(live_profile)
    assert ok is True, reason


def test_live_capture_round_trips_through_disk(tmp_path, live_profile):
    P.save_profile(live_profile, directory=str(tmp_path))
    assert P.load_profile("live-test", directory=str(tmp_path)) == live_profile

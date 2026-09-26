"""The event-ID and bugcheck knowledge tables (Qt-free)."""
from modules.diagnose import knowledge as k


def test_kernel_power_41_is_known_under_both_spellings():
    long_name = k.lookup_event("Microsoft-Windows-Kernel-Power", 41)
    short_name = k.lookup_event("Kernel-Power", 41)
    assert long_name is not None and long_name is short_name
    assert "BugcheckCode 0" in long_name.cause


def test_an_id_is_only_meaningful_for_its_provider():
    # 7 is a disk error from `disk`; from anyone else it says nothing.
    assert k.lookup_event("disk", 7) is not None
    assert k.lookup_event("SomeVendorService", 7) is None


def test_unknown_event_is_none_not_a_guess():
    assert k.lookup_event("disk", 999999) is None
    assert k.lookup_event("", 41) is None


def test_dcom_10016_is_known_by_classic_source_name():
    assert "harmless" in k.lookup_event("DCOM", 10016).cause.lower()


def test_bugcheck_names_and_parameters():
    info = k.bugcheck_info(0x133)
    assert info.name == "DPC_WATCHDOG_VIOLATION"
    assert k.bugcheck_info(0xD1).params[0] == "Address referenced"
    assert k.bugcheck_info(0xC000021A).name.startswith("STATUS_SYSTEM_PROCESS")


def test_bugcheck_accepts_signed_and_padded_values():
    assert k.bugcheck_info(0x0000000A).name == "IRQL_NOT_LESS_OR_EQUAL"
    assert k.bugcheck_info(-1073741286).name == "STATUS_SYSTEM_PROCESS_TERMINATED"  # 0xC000021A signed


def test_unknown_bugcheck_is_none_and_label_is_just_hex():
    assert k.bugcheck_info(0xDEADBEEF) is None
    assert k.bugcheck_label(0xDEADBEEF) == "0xDEADBEEF"
    assert k.bugcheck_label(0x141) == "0x00000141 VIDEO_ENGINE_TIMEOUT_DETECTED"


def test_live_kernel_event_codes():
    assert k.is_live_kernel_event(0xA1000001)
    assert not k.is_live_kernel_event(0x133)


def test_every_table_entry_says_something():
    for (provider, eid), info in k._EVENTS.items():
        assert provider == provider.lower(), provider
        assert info.title and info.meaning and info.cause, (provider, eid)
    for code, info in k._BUGCHECKS.items():
        assert info.name == info.name.upper() and info.cause, hex(code)
        assert len(info.params) in (0, 4), hex(code)

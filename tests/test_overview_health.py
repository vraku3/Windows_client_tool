from modules.dashboard import overview_health as oh

GB = 1024 ** 3


def test_disk_thresholds():
    assert oh.judge_disk("C:", 50 * GB, 100 * GB) is None
    assert oh.judge_disk("C:", 10 * GB, 100 * GB).severity == oh.WARNING
    assert oh.judge_disk("C:", 2 * GB, 100 * GB).severity == oh.CRITICAL
    assert oh.judge_disk("C:", 0, 0) is None


def test_memory_and_commit():
    assert oh.judge_memory(50, 1, 2) is None
    assert oh.judge_memory(90, 1, 2).severity == oh.WARNING
    assert oh.judge_memory(97, 1, 2).severity == oh.CRITICAL
    assert oh.judge_commit(50, 1, 2) is None
    assert oh.judge_commit(90, 1, 2) is not None


def test_uptime_only_flags_long_runs():
    assert oh.judge_uptime(86400) is None
    assert "20 days" in oh.judge_uptime(20 * 86400).title


def test_a_refused_read_is_unknown_never_fine():
    assert oh.judge_shutdowns(None).severity == oh.UNKNOWN
    assert oh.judge_reboot(None).severity == oh.UNKNOWN
    assert oh.judge_shutdowns(0) is None
    assert oh.judge_reboot([]) is None


def test_shutdown_severity_scales():
    assert oh.judge_shutdowns(1).severity == oh.WARNING
    assert oh.judge_shutdowns(5).severity == oh.CRITICAL


def test_drivers_not_scanned_is_silent_but_problems_are_reported():
    assert oh.judge_drivers(None) is None
    assert oh.judge_drivers(0) is None
    assert "3 driver" in oh.judge_drivers(3).title


def test_sorted_worst_first_and_unknown_last():
    fs = [oh.Finding(oh.UNKNOWN, "u"), oh.Finding(oh.INFO, "i"),
          oh.Finding(oh.CRITICAL, "c"), oh.Finding(oh.WARNING, "w")]
    assert [f.title for f in oh.sort_findings(fs)] == ["c", "w", "i", "u"]


def test_collect_uses_injected_readers():
    found = oh.collect_findings(driver_problems=2,
                                shutdown_counter=lambda: 4,
                                reboot_reader=lambda: ["Windows Update"])
    titles = " | ".join(f.title for f in found)
    assert "restart is pending" in titles and "unexpected shutdown" in titles
    assert "driver" in titles


def test_history_is_capped():
    h = oh.History(3)
    for v in range(10):
        h.add(v)
    assert h.values() == [7.0, 8.0, 9.0]


def test_summary_text_lists_findings():
    text = oh.summary_text("PC", "Win 11", "cpu", "1h", [oh.Finding(oh.WARNING, "x", "y")], ["RAM: 1"])
    assert "[warning] x - y" in text and "RAM: 1" in text
    assert "Nothing needs attention" in oh.summary_text("a", "b", "c", "d", [], [])


def test_real_reboot_and_shutdown_readers_do_not_raise():
    r = oh.pending_reboot_reasons()
    assert r is None or isinstance(r, list)
    c = oh.unexpected_shutdown_count()
    assert c is None or c >= 0

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


def test_quick_tools_are_well_formed_and_launch_errors_are_reported(monkeypatch):
    assert len(oh.QUICK_TOOLS) >= 6
    for label, argv, tip in oh.QUICK_TOOLS:
        assert label and argv and tip
    monkeypatch.setattr(oh.os, "startfile", lambda *_: (_ for _ in ()).throw(OSError("nope")), raising=False)
    assert "nope" in oh.launch_tool(["x.msc"])
    calls = []
    monkeypatch.setattr(oh.subprocess, "Popen", lambda argv, **k: calls.append(argv))
    assert oh.launch_tool(["taskmgr.exe"]) is None and calls == [["taskmgr.exe"]]


def test_handle_leak_finding_names_the_process_and_ignores_system():
    rows = [("chrome.exe", 10, 900), ("leaky.exe", 11, 45_000), ("System", 4, 90_000)]
    found = oh.judge_handles(rows)
    assert len(found) == 1 and "leaky.exe" in found[0].title and "45,000" in found[0].title
    assert oh.judge_handles([("a.exe", 9, 100)]) == []


def test_no_pagefile_is_reported_and_a_pagefile_is_not():
    assert oh.judge_pagefile(0) is not None
    assert oh.judge_pagefile(4 * 1024 ** 3) is None


def test_network_summary_and_identity_read_this_machine():
    rows = oh.network_summary()
    assert rows is None or all(len(r) == 3 for r in rows)
    lines = oh.identity_lines()
    assert lines[0].startswith("User:") and lines[1].startswith("Elevated:")


def test_recent_chip_exists_and_fresh_processes_pass_it():
    from modules.dashboard import process_view as pv
    assert "recent" in [k for k, _, _ in pv.FILTERS]
    from core.procengine.snapshot import SnapshotSource
    import os
    src = SnapshotSource(); src.read()
    me = src.read().by_pid[os.getpid()]
    assert isinstance(pv.passes("recent", me), bool)


def test_overview_cpu_tile_shows_package_power_when_the_hardware_has_it(qapp):
    from modules.dashboard.dashboard_module import _DashboardWidget
    w = _DashboardWidget()
    first = w._power_text()                     # primes the meter
    import time; time.sleep(1.1)
    text = w._power_text()
    assert text == "" or text.strip().endswith(" W")
    w.stop_timer()


def test_self_usage_reports_this_process_and_cpu_is_a_delta_not_always_zero():
    import time
    first = oh.self_usage()
    assert "MB" in first and "threads" in first and "handles" in first
    end = time.time() + 0.4
    while time.time() < end:                    # burn a little CPU so the delta is non-zero
        sum(range(20000))
    second = oh.self_usage()
    cpu = float(second.split("% CPU")[0].split(",")[-1])
    assert cpu > 0.0, second


def test_self_usage_of_a_vanished_process_says_so_instead_of_crashing():
    import psutil

    class Gone:
        def oneshot(self):
            raise psutil.NoSuchProcess(1)
    assert "could not be read" in oh.self_usage(Gone())

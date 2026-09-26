"""Event triage logic: presets, grouping, frequency, correlation, detail HTML."""
from datetime import datetime, timedelta

from core.types import LogEntry
from modules.event_viewer import event_analysis as ea
from modules.event_viewer.event_detail import detail_html

NOW = datetime(2026, 9, 26, 12, 0, 0)


def ev(provider, eid, level="Error", minutes_ago=0, message="msg", source=None):
    return LogEntry(timestamp=NOW - timedelta(minutes=minutes_ago), source=source or provider,
                    level=level, message=message, raw={"event_id": eid, "provider": provider})


def _sample():
    return [
        ev("Microsoft-Windows-Kernel-Power", 41, "Critical", 5),
        ev("EventLog", 6008, "Error", 5),
        ev("Service Control Manager", 7031, "Error", 30),
        ev("disk", 153, "Warning", 40),
        ev("disk", 153, "Warning", 41),
        ev("Application Error", 1000, "Error", 90),
        ev("Microsoft-Windows-WHEA-Logger", 17, "Warning", 200),
        ev("Microsoft-Windows-Kernel-PnP", 219, "Warning", 300),
        ev("Microsoft-Windows-Security-Auditing", 4625, "Warning", 10),
        ev("Microsoft-Windows-Time-Service", 134, "Warning", 500),
        ev("Microsoft-Windows-Kernel-General", 12, "Info", 1000),
        ev("Something", 7, "Info", 1),  # id 7 from a stranger is not a disk error
    ]


def test_each_preset_picks_what_it_names():
    entries = _sample()
    pick = lambda key: {(e.raw["provider"], e.raw["event_id"]) for e in ea.apply_preset(entries, key)}
    assert pick("shutdown") == {("Microsoft-Windows-Kernel-Power", 41), ("EventLog", 6008)}
    assert pick("service") == {("Service Control Manager", 7031)}
    assert pick("disk") == {("disk", 153)}
    assert pick("appcrash") == {("Application Error", 1000)}
    assert pick("whea") == {("Microsoft-Windows-WHEA-Logger", 17)}
    assert pick("driver") == {("Microsoft-Windows-Kernel-PnP", 219)}
    assert pick("logon") == {("Microsoft-Windows-Security-Auditing", 4625)}
    assert pick("time") == {("Microsoft-Windows-Time-Service", 134)}
    assert ("Something", 7) not in pick("disk")


def test_preset_counts_cover_every_preset_in_one_pass():
    counts = ea.preset_counts(_sample())
    assert set(counts) == {p.key for p in ea.PRESETS}
    assert counts["all"] == 12 and counts["disk"] == 2 and counts["errors"] == 4


def test_filter_since_and_text():
    entries = _sample()
    assert len(ea.filter_since(entries, 1, NOW)) == 7   # 5, 5, 30, 40, 41, 10, 1 minutes ago
    assert ea.filter_since(entries, None, NOW) == entries
    assert [e.raw["event_id"] for e in ea.filter_text(entries, "6008")] == [6008]
    assert len(ea.filter_text(entries, "  DISK ")) == 2


def test_grouping_counts_and_orders_by_frequency():
    rows = ea.group_entries(_sample())
    assert rows[0].raw["count"] == 2 and rows[0].raw["event_id"] == 153
    assert rows[0].message.startswith("x2")
    assert rows[0].timestamp == NOW - timedelta(minutes=40)   # the LAST occurrence
    assert ea.row_values(rows[0]) == [153, 2]
    assert ea.row_values(_sample()[0]) == [41, ""]


def test_group_takes_the_worst_level():
    rows = ea.group_entries([ev("p", 1, "Warning", 1), ev("p", 1, "Error", 2), ev("p", 1, "Info", 3)])
    assert rows[0].level == "Error"


def test_frequency_windows_and_sparkline():
    entries = [ev("p", 9, minutes_ago=m) for m in (5, 10, 50, 300, 2000)]
    entries.append(ev("q", 1, minutes_ago=3000))
    f = ea.frequency(entries, entries[0], NOW)
    assert (f.total, f.last_hour, f.last_24h) == (5, 3, 4)
    assert len(f.spark) == 24 and set(f.spark) <= set("▁▂▃▄▅▆▇█")


def test_sparkline_empty_and_degenerate():
    assert ea.sparkline([], NOW, NOW + timedelta(hours=1)) == ""
    assert ea.sparkline([NOW], NOW, NOW) == ""


def test_correlate_finds_nearby_problems_not_chatter():
    target = ev("Microsoft-Windows-Kernel-Power", 41, "Critical", 5)
    near_error = ev("disk", 153, "Warning", 5.5)
    chatter = ev("x", 1, "Info", 5.2)
    far = ev("y", 2, "Error", 60)
    pairs = ea.correlate([target, near_error, chatter, far], target, 60)
    assert [e for _off, e in pairs] == [near_error]
    assert pairs[0][0] < 0  # it happened before the target


def test_detail_html_explains_counts_and_escapes():
    hostile = ev("Microsoft-Windows-Kernel-Power", 41, "Critical", 5, message="<script>x</script> & y")
    other = ev("disk", 153, "Warning", 5.5, message="a <b> tag")
    html = detail_html(hostile, [hostile, other], NOW)
    assert "Unexpected reboot" in html
    assert "Frequency:" in html
    assert "<script>" not in html and "&lt;b&gt; tag" in html
    assert "disk 153" in html


def test_detail_html_for_an_unknown_event_still_reports_frequency():
    e = ev("Vendor", 5, "Warning", 1)
    html = detail_html(e, [e], NOW)
    assert "Usual cause" not in html and "Frequency:" in html


def test_summary_text_names_findings_and_caveats():
    text = ea.summary_text(_sample(), ["Security log not read."], "last 24 hours")
    assert "12 events in the last 24 hours" in text
    assert "1 critical" in text and "unexpected shutdowns" in text
    assert text.endswith("Security log not read.")
    assert "No events" in ea.summary_text([], [])


def test_entry_as_text_is_pasteable():
    e = ev("disk", 153, "Warning", 1, message="I/O retried")
    e.raw.update(log_name="System", computer="PC", record_number="9", data={"k": "v"})
    text = ea.entry_as_text(e)
    assert "ID 153" in text and "I/O retried" in text and "k = v" in text

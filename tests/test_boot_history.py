"""Boot-time trend history: pairing and refusal-reporting logic, plus one
real-machine assertion against this machine's own System log.

`_pair_boot` and `trend_summary` are pure functions over plain datetimes --
no pywin32, no Qt -- so the pairing rules are tested directly with synthetic
timestamps. `get_boot_history` itself is exercised against the real machine:
this app already treats the `System` log as unelevated-readable everywhere
else (Event Viewer, Reliability, CBS), so a refusal here would be a real
regression worth catching, not a flake to route around.
"""
from datetime import datetime, timedelta, timezone

from modules.boot_analyzer.boot_history import (
    BootHistoryResult,
    BootRecord,
    _pair_boot,
    get_boot_history,
    trend_summary,
)

_UTC = timezone.utc


def _dt(hour, minute=0, second=0, day=1):
    return datetime(2026, 9, day, hour, minute, second, tzinfo=_UTC)


def test_pair_boot_finds_the_ready_event_and_the_prior_clean_shutdown():
    boot_time = _dt(8, 0, 0)
    shutdowns = [
        (_dt(6, 0, 0), 6006),   # clean shutdown before this boot
        (_dt(8, 0, 33), 6005),  # ready ~33s later
    ]
    rec = _pair_boot(boot_time, shutdowns)
    assert rec.duration_seconds == 33.0
    assert rec.prior_shutdown_clean is True
    assert rec.prior_shutdown_time == _dt(6, 0, 0)


def test_pair_boot_flags_an_unexpected_prior_shutdown():
    boot_time = _dt(8, 0, 0)
    shutdowns = [(_dt(6, 0, 0), 6008), (_dt(8, 0, 40), 6005)]
    rec = _pair_boot(boot_time, shutdowns)
    assert rec.prior_shutdown_clean is False


def test_pair_boot_missing_ready_event_is_none_not_zero():
    """No 6005 after this boot (log rotated, or it hasn't happened yet) must
    report `duration_seconds=None`, never 0 or a guessed value."""
    boot_time = _dt(8, 0, 0)
    rec = _pair_boot(boot_time, [(_dt(6, 0, 0), 6006)])
    assert rec.ready_time is None
    assert rec.duration_seconds is None


def test_pair_boot_missing_prior_shutdown_is_none_not_assumed_clean():
    """No 6006/6008 before this boot must report `prior_shutdown_clean=None`
    -- collapsing that into "clean" would be a refused/missing read shown as
    a definite answer, exactly what CLAUDE.md's rule against that exists to
    prevent."""
    boot_time = _dt(8, 0, 0)
    rec = _pair_boot(boot_time, [(_dt(8, 0, 30), 6005)])
    assert rec.prior_shutdown_clean is None
    assert rec.prior_shutdown_time is None


def test_trend_summary_needs_at_least_four_measured_boots():
    records = [
        BootRecord(_dt(h), _dt(h, 0, 30), 30.0, None, None) for h in range(3)
    ]
    assert trend_summary(records) is None


def test_trend_summary_reports_slower():
    durations = [20.0, 20.0, 40.0, 40.0]
    records = [
        BootRecord(_dt(h), _dt(h, 0, int(d)), d, None, None)
        for h, d in enumerate(durations)
    ]
    summary = trend_summary(records)
    assert summary is not None
    assert "slower" in summary


def test_trend_summary_ignores_unmeasured_boots():
    """A boot with no measured duration must not pollute the average or be
    silently treated as 0."""
    records = [
        BootRecord(_dt(0), _dt(0, 0, 30), 30.0, None, None),
        BootRecord(_dt(1), None, None, None, None),  # unmeasured
        BootRecord(_dt(2), _dt(2, 0, 30), 30.0, None, None),
        BootRecord(_dt(3), _dt(3, 0, 30), 30.0, None, None),
        BootRecord(_dt(4), _dt(4, 0, 30), 30.0, None, None),
    ]
    summary = trend_summary(records)
    assert summary is not None
    assert "steady" in summary


def test_boot_history_result_refusal_is_not_an_empty_success():
    """`available=False` must carry its own reason and never be mistaken
    for `available=True, records=[]` -- the two mean completely different
    things (could not read vs. genuinely no data)."""
    refused = BootHistoryResult(available=False, reason="Access is denied", records=[])
    empty = BootHistoryResult(available=True, reason=None, records=[])
    assert refused.available is not empty.available
    assert refused.reason is not None
    assert empty.reason is None


def test_get_boot_history_against_the_real_machine():
    """Real-machine assertion: this machine boots regularly (confirmed via
    `Get-WinEvent -FilterHashtable @{LogName='System';
    ProviderName='Microsoft-Windows-Kernel-General'; Id=12}` returning
    real, recent timestamps during manual probing for this feature), so the
    System-log query must succeed unelevated and return at least one boot
    with a measured duration in a plausible range. A refusal here is a real
    regression, not a environment quirk to skip past.
    """
    result = get_boot_history(max_boots=15)
    assert result.available, f"System log boot-history query refused: {result.reason}"
    assert len(result.records) >= 1
    measured = [r.duration_seconds for r in result.records if r.duration_seconds is not None]
    assert measured, "no boot on this machine paired with a ready (6005) event"
    for d in measured:
        # A boot that took under a second or over an hour would mean the
        # pairing logic matched the wrong events, not a real machine state.
        assert 1.0 < d < 3600.0
    # Boots must come back oldest-first.
    times = [r.boot_time for r in result.records]
    assert times == sorted(times)

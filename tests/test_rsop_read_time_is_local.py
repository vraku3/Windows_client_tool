r"""gpresult stamps ReadTime in UTC; the pane printed it as if it were local.

Seen 2026-09-25 (UTC+3): "Collected 07:26:49" while the clock said 10:26.
Only the display is converted -- the stored value stays exactly as gpresult
wrote it, so saved snapshots remain comparable.
"""
import datetime

from modules.gpresult.rsop_parser import local_read_time


def _expected_local(utc_text):
    utc = datetime.datetime.fromisoformat(utc_text).replace(tzinfo=datetime.timezone.utc)
    return utc.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def test_a_utc_stamp_is_shown_in_local_time():
    assert local_read_time("2026-09-25T07:26:49.9721808Z") == _expected_local("2026-09-25T07:26:49")


def test_the_seven_digit_fraction_gpresult_writes_is_accepted():
    assert local_read_time("2026-09-25T07:26:49.9721808Z")


def test_a_stamp_with_no_zone_is_left_alone_not_guessed():
    assert local_read_time("2026-09-25T07:26:49") == "2026-09-25 07:26:49"


def test_garbage_and_empty_are_safe():
    assert local_read_time("") == ""
    assert local_read_time(None) == ""
    assert local_read_time("not a date at all") == "not a date at all"[:19].replace("T", " ")

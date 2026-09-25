from modules.local_users.users_module import format_password_age


def test_never_set_is_a_dash_not_zero_days():
    assert format_password_age(0) == "\u2014"
    assert format_password_age(None) == "\u2014"


def test_changed_today_is_zero_days():
    assert format_password_age(3600) == "0"


def test_whole_days():
    assert format_password_age(20 * 86400 + 5) == "20"

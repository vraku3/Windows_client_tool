import pytest

from modules.security_dashboard import security_reader as sr


def test_a_refused_security_log_read_is_an_error_not_an_empty_list(monkeypatch):
    monkeypatch.setattr(sr, "_cmd_run", lambda *a, **k: (5, "", "Access is denied."))
    with pytest.raises(sr.SecurityLogUnreadable, match="Access is denied"):
        sr.get_security_events()


def test_a_genuinely_empty_log_is_still_an_empty_list(monkeypatch):
    monkeypatch.setattr(sr, "_cmd_run", lambda *a, **k: (0, "", ""))
    assert sr.get_security_events() == []


def test_debloat_legend_has_no_html_entities():
    import pathlib
    src = pathlib.Path(sr.__file__).parents[1] / "debloat" / "debloat_module.py"
    assert "&nbsp;" not in src.read_text(encoding="utf-8")

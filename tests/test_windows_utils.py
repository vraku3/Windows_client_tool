# tests/test_windows_utils.py
"""is_reboot_pending() now delegates to core.pending_reboot.check(); the
fine-grained cases (deletions vs replacements, refusals) live in
tests/test_pending_reboot.py. These pin the delegation."""
from core import pending_reboot as pr


def _state(reasons):
    return lambda: pr.PendingRestart(reasons=list(reasons))


def test_reboot_pending_when_something_needs_it(monkeypatch):
    import core.windows_utils as m
    monkeypatch.setattr(pr, "check", _state(["Windows Update"]))
    assert m.is_reboot_pending() is True


def test_reboot_not_pending_for_housekeeping_only(monkeypatch):
    import core.windows_utils as m
    monkeypatch.setattr(pr, "check", lambda: pr.PendingRestart(
        housekeeping=[pr.PendingOp(pr.DELETE, r"C:\Program Files\Microsoft OneDrive\26.1\x.dll")]))
    assert m.is_reboot_pending() is False


def test_reboot_pending_false_all_absent(monkeypatch):
    import core.windows_utils as m
    monkeypatch.setattr(pr, "check", _state([]))
    assert m.is_reboot_pending() is False

def test_ps_quote_escapes_single_quotes():
    from core.windows_utils import ps_quote
    assert ps_quote("Microsoft.WindowsCalculator") == "Microsoft.WindowsCalculator"
    assert ps_quote("it's a package") == "it''s a package"
    assert ps_quote("") == ""

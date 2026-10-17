"""A refused read-back after a revert is not proof the revert worked.

With the before-value unknown, `_verify` compared the reading to the value
the change had set: `None` (we could not look) differs from everything, so
"could not read" was reported as APPLIED_VERIFIED.
"""
from modules.security_dashboard import reverting
from modules.security_dashboard.catalog.model import ControlState


class _Control:
    requires_reboot = False

    def __init__(self, value):
        self._value = value

    def read(self):
        return self._value


def test_an_unreadable_value_after_a_revert_is_unverified():
    result = reverting._verify(_Control(None), "x", before=True, expected=reverting._UNKNOWN)
    assert result.state == ControlState.APPLIED_UNVERIFIED
    assert "could not be read back" in result.reason


def test_a_value_that_moved_off_the_changed_one_is_verified():
    result = reverting._verify(_Control(False), "x", before=True, expected=reverting._UNKNOWN)
    assert result.state == ControlState.APPLIED_VERIFIED

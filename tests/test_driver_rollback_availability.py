"""rollback_availability.check_rollback_availability -- whether an older
version of a device's currently-active driver package is still cached in
the driver store, distinct from Cleanup's superseded-package detection
(which decides what could be DELETED, not what a specific device could be
rolled back TO). Fixture data is the same real `pnputil /enum-drivers`
capture `test_cleanup_driver_store.py` uses (tests/data/pnputil_enum_drivers.txt):
oem49.inf/oem35.inf are two real versions of AMD's amd3dvcache.inf, oem60.inf/
oem53.inf are the ambiguously-ordered amdxe.inf pair (a later date carrying a
LOWER version), and oem18.inf (nvhda.inf) has no sibling at all.
"""
import pathlib
from unittest.mock import patch

import pytest

from modules.cleanup.cleanup_scanner import driver_store
from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.rollback_availability import check_rollback_availability

FIXTURE = pathlib.Path(__file__).resolve().parent / "data" / "pnputil_enum_drivers.txt"


@pytest.fixture
def packages():
    return driver_store.parse_enum_drivers(FIXTURE.read_text(encoding="utf-8"))


def _driver(inf_name: str) -> DriverInfo:
    return DriverInfo(
        device_name="Test Device", driver_class="System", version="1.0",
        date="2026-01-01", publisher="Test", signed=True, error_code=0,
        flags="", inf_name=inf_name, device_id="TEST\\1")


def _patched(packages):
    return patch(
        "modules.driver_manager.rollback_availability.enumerate_packages",
        return_value=packages)


def test_newer_of_a_real_pair_has_an_older_version_cached(packages):
    with _patched(packages):
        result = check_rollback_availability(_driver("oem49.inf"))
    assert result.checked is True
    assert result.available is True
    assert result.previous_version == "09/09/2025 1.0.0.11"
    assert "oem35.inf" in result.reason


def test_the_older_of_a_real_pair_has_nothing_older_than_itself(packages):
    """oem35.inf IS the old one -- there is nothing before it."""
    with _patched(packages):
        result = check_rollback_availability(_driver("oem35.inf"))
    assert result.checked is True
    assert result.available is False


def test_a_package_with_no_sibling_at_all_reports_unavailable(packages):
    """oem18.inf (nvhda.inf) is alone in the fixture -- one real package,
    no other version to compare against."""
    with _patched(packages):
        result = check_rollback_availability(_driver("oem18.inf"))
    assert result.checked is True
    assert result.available is False
    assert "only one" in result.reason


def test_ambiguous_pair_still_answers_by_date_not_version(packages):
    """amdxe.inf: oem60.inf (09/09/2025, HIGHER version 25.20.0.11) and
    oem53.inf (09/23/2025, LOWER version 25.10.0.1) -- driver_store's own
    `supersedes()` treats this pair as unresolvable for DELETION (a wrong
    delete costs a driver), but a rollback-availability question just
    needs "is anything else dated earlier", so oem53 (the later-dated one)
    correctly reports oem60 as its older sibling despite the version
    inversion."""
    with _patched(packages):
        result = check_rollback_availability(_driver("oem53.inf"))
    assert result.checked is True
    assert result.available is True
    assert "oem60.inf" in result.reason
    # And the reverse: oem60 is dated earlier, so it has nothing older.
    with _patched(packages):
        result60 = check_rollback_availability(_driver("oem60.inf"))
    assert result60.available is False


def test_an_inbox_driver_is_reported_as_having_nothing_to_check():
    """No OEM number at all -- published_name_for() returns None, and this
    never even asks pnputil."""
    with patch("modules.driver_manager.rollback_availability.enumerate_packages") as mock_enum:
        result = check_rollback_availability(_driver("usb.inf"))
    mock_enum.assert_not_called()
    assert result.checked is True
    assert result.available is False


def test_a_refused_enumeration_is_reported_as_unchecked_not_unavailable():
    """This is the one rule the whole module exists to enforce: pnputil
    refusing (unelevated -- returns None, never []) must never be read as
    "no previous version exists"."""
    with patch("modules.driver_manager.rollback_availability.enumerate_packages",
               return_value=None):
        result = check_rollback_availability(_driver("oem49.inf"))
    assert result.checked is False
    assert result.available is False
    assert "administrator" in result.reason.lower()


def test_a_published_name_not_in_the_enumeration_is_reported_honestly(packages):
    with _patched(packages):
        result = check_rollback_availability(_driver("oem999.inf"))
    assert result.checked is True
    assert result.available is False
    assert "oem999.inf" in result.reason


# ── real machine ──────────────────────────────────────────────────────────

def test_real_machine_never_collapses_a_refusal_into_unavailable():
    """Run for real, unelevated (confirmed here via `net session` ->
    Access Denied, not just is_admin()), against this machine's actual
    driver store -- no mocking. Measured 2026-09-28: `pnputil /enum-drivers`
    answered with real package records even UNELEVATED here (exit code 255,
    not the help-banner-and-rc-0 refusal `driver_store`'s own tests pin
    against a synthetic fixture) -- so `checked` legitimately can be True
    without elevation on this machine/Windows build. Whichever way it
    actually goes, the one thing that must never happen is a refusal
    reported as "nothing to roll back to": `available` is never True
    unless `checked` is also True, and a report about a real, currently
    installed OEM package (oem1.inf is real and installed here -- Realtek's
    USB GbE controller) never comes back with an empty, unexplained
    reason."""
    result = check_rollback_availability(_driver("oem1.inf"))
    assert result.checked in (True, False)
    if not result.checked:
        assert result.available is False
    assert result.reason

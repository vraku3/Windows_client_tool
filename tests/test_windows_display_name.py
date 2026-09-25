r"""The Dashboard said "AMD64 Family 26 Model 68 Stepping 0, AuthenticAMD" and
Hardware said "Windows-11-10.0.26200-SP0" (Python's platform strings). The
registry's ProductName still says "Windows 10 Pro" on Windows 11, so the major
version has to come from the build number."""
import pytest

from core import windows_utils as wu


def test_windows_11_is_recognised_from_the_build_not_the_product_name():
    assert wu.format_windows_name("Windows 10 Pro", "25H2", "26200", "9550") == \
        "Windows 11 Pro 25H2 (build 26200.9550)"


def test_windows_10_stays_windows_10():
    assert wu.format_windows_name("Windows 10 Home", "22H2", "19045", "5487") == \
        "Windows 10 Home 22H2 (build 19045.5487)"


def test_a_missing_revision_or_version_is_left_out_not_invented():
    assert wu.format_windows_name("Windows 10 Pro", "", "26200", "") == \
        "Windows 11 Pro (build 26200)"


def test_an_unreadable_build_falls_back_to_what_the_registry_said():
    assert wu.format_windows_name("Windows 10 Pro", "25H2", "n/a", "1") == "Windows 10 Pro"
    assert wu.format_windows_name("", "", "", "") == "Windows"


@pytest.mark.real_machine
def test_real_machine_names_are_friendly():
    assert wu.windows_display_name().startswith("Windows 1")
    assert "Family" not in wu.cpu_brand_name() and wu.cpu_brand_name()

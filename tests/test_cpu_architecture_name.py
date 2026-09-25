from modules.hardware_inventory.hardware_reader import cpu_architecture_name


def test_codes_are_named_not_shown_raw():
    assert cpu_architecture_name(9) == "x64 (AMD64)"
    assert cpu_architecture_name(12) == "ARM64"
    assert cpu_architecture_name(None) == ""
    assert "99" in cpu_architecture_name(99)

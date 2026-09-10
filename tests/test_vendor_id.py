from modules.driver_manager.vendor_updates.vendor_id import vendor_for_hardware_id


def test_recognizes_nvidia_from_a_pci_hardware_id():
    assert vendor_for_hardware_id("PCI\\VEN_10DE&DEV_2684&SUBSYS_88761458") == "NVIDIA"


def test_recognizes_amd_from_a_pci_hardware_id():
    assert vendor_for_hardware_id("PCI\\VEN_1002&DEV_744C&SUBSYS_0B361002") == "AMD"


def test_recognizes_intel_from_a_pci_hardware_id():
    assert vendor_for_hardware_id("PCI\\VEN_8086&DEV_A780&SUBSYS_00000000") == "Intel"


def test_unrecognized_vendor_id_returns_none():
    assert vendor_for_hardware_id("PCI\\VEN_FFFF&DEV_0000") is None


def test_empty_hardware_id_returns_none():
    assert vendor_for_hardware_id("") is None


def test_malformed_hardware_id_returns_none():
    assert vendor_for_hardware_id("not a hardware id at all") is None


def test_is_case_insensitive_on_the_vendor_hex():
    assert vendor_for_hardware_id("PCI\\ven_10de&dev_2684") == "NVIDIA"

from modules.driver_manager.vendor_updates.vendor_id import vendor_for_hardware_id


def test_recognizes_nvidia_from_a_pci_hardware_id():
    assert vendor_for_hardware_id("PCI\\VEN_10DE&DEV_2684&SUBSYS_88761458") == "NVIDIA"


def test_recognizes_amd_from_a_pci_hardware_id():
    assert vendor_for_hardware_id("PCI\\VEN_1002&DEV_744C&SUBSYS_0B361002") == "AMD"


def test_recognizes_amd_from_its_own_pci_vendor_id_not_just_the_ati_heritage_one():
    # Real machine data: AMD ships PCI devices under BOTH 0x1002 (ATI
    # heritage -- GPUs) and 0x1022 (AMD's own -- platform security
    # processor, SMBUS, chipset). A real "AMD PSP 11.0 Device" on this
    # exact machine has hardware_id "PCI\VEN_1022&DEV_1649&SUBSYS_16491022&REV_00".
    assert vendor_for_hardware_id("PCI\\VEN_1022&DEV_1649&SUBSYS_16491022&REV_00") == "AMD"


def test_recognizes_intel_from_a_pci_hardware_id():
    assert vendor_for_hardware_id("PCI\\VEN_8086&DEV_A780&SUBSYS_00000000") == "Intel"


def test_recognizes_realtek_from_a_pci_hardware_id():
    # Real machine data: "Realtek PCIe 5GbE Family Controller" reports
    # hardware_id "PCI\VEN_10EC&DEV_8126&SUBSYS_81261849&REV_01".
    assert vendor_for_hardware_id("PCI\\VEN_10EC&DEV_8126&SUBSYS_81261849&REV_01") == "Realtek"


def test_recognizes_mediatek_from_a_pci_hardware_id():
    # Real machine data: "RZ717 WiFi 7 160MHz" reports hardware_id
    # "PCI\VEN_14C3&DEV_0717&SUBSYS_071714C3&REV_00".
    assert vendor_for_hardware_id("PCI\\VEN_14C3&DEV_0717&SUBSYS_071714C3&REV_00") == "MediaTek"


def test_unrecognized_vendor_id_returns_none():
    assert vendor_for_hardware_id("PCI\\VEN_FFFF&DEV_0000") is None


def test_empty_hardware_id_returns_none():
    assert vendor_for_hardware_id("") is None


def test_malformed_hardware_id_returns_none():
    assert vendor_for_hardware_id("not a hardware id at all") is None


def test_is_case_insensitive_on_the_vendor_hex():
    assert vendor_for_hardware_id("PCI\\ven_10de&dev_2684") == "NVIDIA"


def test_falls_back_to_device_name_when_theres_no_pci_vendor_id_at_all():
    # Real machine data: a CPU is not a PCI device. Windows exposes it as
    # a generic ACPI processor object -- the real "AMD Processor" entry on
    # this exact machine has hardware_id "ACPI\VEN_ACPI&DEV_0007", where
    # "ACPI" is a literal placeholder, not a real vendor ID. There is no
    # PCI vendor ID to read here, ever -- device_name is the only signal.
    assert vendor_for_hardware_id("ACPI\\VEN_ACPI&DEV_0007", "AMD Processor") == "AMD"


def test_falls_back_to_device_name_for_intel_and_nvidia_too():
    assert vendor_for_hardware_id("ACPI\\VEN_ACPI&DEV_0007", "Intel Processor") == "Intel"
    assert vendor_for_hardware_id("ACPI\\VEN_ACPI&DEV_0007", "NVIDIA Something") == "NVIDIA"


def test_device_name_fallback_is_case_insensitive():
    assert vendor_for_hardware_id("ACPI\\VEN_ACPI&DEV_0007", "amd processor") == "AMD"


def test_device_name_fallback_never_fires_when_a_real_pci_vendor_id_matched():
    # A real PCI match must win even if the device's own name happens to
    # mention a different vendor's word (an edge case, but the PCI ID is
    # the authoritative signal, never overridden by name text).
    assert vendor_for_hardware_id("PCI\\VEN_10DE&DEV_2684", "NVIDIA GeForce (AMD FreeSync compatible)") == "NVIDIA"


def test_no_fallback_match_still_returns_none():
    assert vendor_for_hardware_id("ACPI\\VEN_ACPI&DEV_0007", "Some Unrelated Device") is None
    assert vendor_for_hardware_id("", "") is None

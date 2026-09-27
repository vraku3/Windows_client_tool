"""USB and PCI device inventory (no Qt)."""
from modules.hardware_inventory import device_reader as dr


def test_composite_children_are_folded_under_the_parent():
    real = dr.read_usb_devices()
    assert real is not None and real
    assert all("&MI_" not in d.device_id for d in real)


def test_usb_bus_controllers_and_hubs_are_not_listed_as_devices():
    real = dr.read_usb_devices()
    assert all(d.pnp_class != "USB" for d in real)


def test_vendor_id_and_product_id_are_extracted():
    real = dr.read_usb_devices()
    with_ids = [d for d in real if d.vendor_id]
    assert with_ids
    assert all(len(d.vendor_id) == 4 for d in with_ids)


def test_pci_devices_are_read_and_classified():
    pci = dr.read_pci_devices()
    assert pci is not None and pci
    classes = {d.pnp_class for d in pci}
    assert "Display" in classes or "SCSIAdapter" in classes or "Net" in classes


def test_known_vendor_ids_get_a_friendly_name():
    pci = dr.read_pci_devices()
    amd = [d for d in pci if d.vendor_id == "1022"]
    assert amd and all("AMD" in d.vendor or "Advanced Micro" in d.vendor for d in amd)


def test_problem_devices_combines_both_lists_and_excludes_ok():
    usb = [dr.Device("a", "V", "C", "USB\\1", "OK"),
           dr.Device("b", "V", "C", "USB\\2", "Error")]
    pci = [dr.Device("c", "V", "C", "PCI\\1", "Degraded")]
    problems = dr.problem_devices(usb, pci)
    assert [d.name for d in problems] == ["b", "c"]
    assert dr.problem_devices(None, None) == []


def test_a_refused_read_is_none_not_an_empty_list(monkeypatch):
    import wmi

    class BoomWMI:
        def Win32_PnPEntity(self):
            raise OSError("refused")
    monkeypatch.setattr(wmi, "WMI", lambda: BoomWMI())
    assert dr.read_usb_devices() is None
    assert dr.read_pci_devices() is None


def test_devices_tab_renders_real_data(qapp):
    from modules.hardware_inventory import hardware_tabs as ht
    from PyQt6.QtWidgets import QVBoxLayout, QWidget
    host = QWidget()
    layout = QVBoxLayout(host)
    ht.setup_devices(layout, ht.load_devices())
    assert layout.count() > 0


def test_a_refused_read_says_so_in_the_tab(qapp):
    from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

    from modules.hardware_inventory import hardware_tabs as ht
    host = QWidget()
    layout = QVBoxLayout(host)
    ht.setup_devices(layout, (None, None))
    labels = [w.text() for w in host.findChildren(QLabel)]
    assert any("could not be read" in t for t in labels)

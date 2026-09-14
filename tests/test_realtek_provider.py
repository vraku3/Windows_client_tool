from unittest.mock import MagicMock

from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates import realtek_provider as rtp


def _driver(name="Realtek PCIe 5GbE Family Controller", version="10.74.1128.2024",
           hardware_id="PCI\\VEN_10EC&DEV_8126&SUBSYS_81261849&REV_01"):
    return DriverInfo(device_name=name, driver_class="Net", version=version,
                      date="", publisher="Realtek", signed=True, error_code=0,
                      flags="", hardware_id=hardware_id)


# A trimmed-down shape of the real, live-verified cate_id=584 page: several
# Windows driver variants in one table, only one of which is the plain,
# broadly-compatible installer _pick_windows_driver_row must choose.
_CATEGORY_PAGE_HTML = """
<h2 class="title">Windows</h2>
<table><tbody>
<tr><td><a href="/Download/ToDownload?type=direct&amp;downloadid=4715"><img></a></td>
<td>DASH all-in-one Installer for Win10/Win11</td><td>11.031</td><td>2026/09/09</td><td>10 MB</td></tr>
<tr><td><a href="/Download/ToDownload?type=direct&amp;downloadid=4357"><img></a></td>
<td>Win10/Win11 Auto Installation Program (NDIS) - Not Support Power Saving</td><td>10.80.20</td><td>2026/08/28</td><td>5 MB</td></tr>
<tr><td><a href="/Download/ToDownload?type=direct&amp;downloadid=3613"><img></a></td>
<td>Win10/Win11 Auto Installation Program (NDIS)</td><td>10.80.50</td><td>2026/08/28</td><td>5 MB</td></tr>
<tr><td><a href="/Download/ToDownload?type=direct&amp;downloadid=4103"><img></a></td>
<td>Win11 Auto Installation Program (NetAdapterCx)</td><td>11.031.50</td><td>2026/08/28</td><td>5 MB</td></tr>
</tbody></table>
<h2 class="title">Unix (Linux)</h2>
<table><tbody>
<tr><td><a href="/Download/ToDownload?type=direct&amp;downloadid=3728"><img></a></td>
<td>FreeBSD</td><td>1.102.01</td><td>2026/04/02</td><td>192 KB</td></tr>
</tbody></table>
"""

_NETADAPTERCX_ONLY_HTML = """
<h2 class="title">Windows</h2>
<table><tbody>
<tr><td><a href="/Download/ToDownload?type=direct&amp;downloadid=4103"><img></a></td>
<td>Win11 Auto Installation Program (NetAdapterCx)</td><td>11.031.50</td><td>2026/08/28</td><td>5 MB</td></tr>
</tbody></table>
"""


def _fake_urlopen(body: bytes):
    def fake(request, timeout=None):
        m = MagicMock()
        m.read.return_value = body
        m.__enter__ = lambda s: m
        m.__exit__ = lambda *a: False
        return m
    return fake


def test_pick_windows_driver_row_prefers_plain_ndis_over_everything_else():
    row = rtp._pick_windows_driver_row(_CATEGORY_PAGE_HTML)
    assert row is not None
    download_id, desc, version, date = row
    assert download_id == "3613"
    assert version == "10.80.50"
    assert "Not Support Power Saving" not in desc
    assert "DASH" not in desc


def test_pick_windows_driver_row_falls_back_to_netadaptercx_when_no_ndis_row():
    row = rtp._pick_windows_driver_row(_NETADAPTERCX_ONLY_HTML)
    assert row is not None
    assert row[2] == "11.031.50"


def test_pick_windows_driver_row_returns_none_when_no_windows_section():
    assert rtp._pick_windows_driver_row("<html>nothing here</html>") is None


def test_check_for_update_routes_pci_hardware_id_to_the_pcie_category(monkeypatch):
    captured = []

    def fake_fetch(url):
        captured.append(url)
        return _CATEGORY_PAGE_HTML

    monkeypatch.setattr(rtp, "_fetch", fake_fetch)
    provider = rtp.RealtekProvider()
    result = provider.check_for_update(_driver())
    assert result is not None
    assert "cate_id=584" in captured[0]
    assert result.vendor == "Realtek"
    assert result.latest_version == "10.80.50"
    assert result.manual_download_only is True
    assert result.download_url == captured[0]


def test_check_for_update_routes_usb_hardware_id_to_the_usb_category(monkeypatch):
    captured = []

    def fake_fetch(url):
        captured.append(url)
        return _CATEGORY_PAGE_HTML

    monkeypatch.setattr(rtp, "_fetch", fake_fetch)
    provider = rtp.RealtekProvider()
    driver = _driver(name="Realtek USB GbE Family Controller", version="11.19.602.2025",
                     hardware_id="USB\\VID_0BDA&PID_8153&REV_3100")
    provider.check_for_update(driver)
    assert "cate_id=585" in captured[0]


def test_check_for_update_returns_none_for_a_non_pci_non_usb_hardware_id():
    provider = rtp.RealtekProvider()
    result = provider.check_for_update(_driver(hardware_id="ACPI\\VEN_ACPI&DEV_0007"))
    assert result is None


def test_check_for_update_returns_none_for_a_realtek_usb_hub_not_a_nic(monkeypatch):
    # Real machine data: "Generic USB Hub" carries USB\VID_0BDA (Realtek
    # makes hub controller chips too) but driver_class is "USB", not
    # "Net" -- must never be routed to a NIC download page.
    fetched = []
    monkeypatch.setattr(rtp, "_fetch", lambda url: fetched.append(url) or _CATEGORY_PAGE_HTML)
    driver = DriverInfo(device_name="Generic USB Hub", driver_class="USB", version="1.0",
                        date="", publisher="Realtek", signed=True, error_code=0, flags="",
                        hardware_id="USB\\VID_0BDA&PID_5411&REV_0001")
    provider = rtp.RealtekProvider()
    result = provider.check_for_update(driver)
    assert result is None
    assert fetched == []  # never even tried to fetch a category page


def test_check_for_update_returns_none_when_the_page_fetch_fails(monkeypatch, caplog):
    monkeypatch.setattr(rtp, "_fetch", lambda url: None)
    provider = rtp.RealtekProvider()
    result = provider.check_for_update(_driver())
    assert result is None


def test_check_for_update_returns_none_when_no_usable_row_is_found(monkeypatch):
    monkeypatch.setattr(rtp, "_fetch", lambda url: "<html>no windows section</html>")
    provider = rtp.RealtekProvider()
    assert provider.check_for_update(_driver()) is None


def test_provider_registers_itself_on_import():
    from modules.driver_manager.vendor_updates import provider as pv
    assert "Realtek" in pv._PROVIDERS
    assert isinstance(pv._PROVIDERS["Realtek"], rtp.RealtekProvider)

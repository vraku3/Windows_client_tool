import pytest

from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates import provider as pv


def _driver(hardware_id="PCI\\VEN_10DE&DEV_2684", version="1.0"):
    return DriverInfo(device_name="Test GPU", driver_class="Display",
                      version=version, date="", publisher="V", signed=True,
                      error_code=0, flags="", hardware_id=hardware_id)


class _FakeProvider:
    vendor_name = "NVIDIA"
    allowed_download_domains = ["download.nvidia.com"]
    expected_signer = "NVIDIA Corporation"

    def check_for_update(self, driver):
        return pv.UpdateInfo(
            vendor="NVIDIA", current_version=driver.version,
            latest_version="999.99", download_url="https://us.download.nvidia.com/x.exe",
            installer_signer="NVIDIA Corporation")


@pytest.fixture(autouse=True)
def _clean_registry():
    pv._PROVIDERS.clear()
    yield
    pv._PROVIDERS.clear()


def test_provider_for_finds_a_registered_provider():
    pv.register_provider(_FakeProvider())
    result = pv.provider_for(_driver())
    assert result is not None
    assert result.vendor_name == "NVIDIA"


def test_provider_for_returns_none_reason_unrecognized_vendor_when_no_provider_registered():
    driver = _driver(hardware_id="PCI\\VEN_FFFF&DEV_0000")
    assert pv.provider_for(driver) is None
    assert pv.no_provider_reason(driver) == pv.NoProviderReason.UNRECOGNIZED_VENDOR


def test_provider_for_returns_none_reason_no_adapter_when_vendor_recognized_but_unregistered():
    driver = _driver(hardware_id="PCI\\VEN_1002&DEV_744C")  # AMD, recognized, no provider
    assert pv.provider_for(driver) is None
    assert pv.no_provider_reason(driver) == pv.NoProviderReason.NO_ADAPTER_FOR_VENDOR


def test_update_info_is_frozen():
    info = pv.UpdateInfo(vendor="NVIDIA", current_version="1.0",
                         latest_version="2.0", download_url="https://x",
                         installer_signer="X")
    with pytest.raises(Exception):
        info.vendor = "changed"

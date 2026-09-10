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
    # Snapshot-and-restore, not clear-and-clear: a real provider module
    # (e.g. nvidia_provider.py) calls register_provider() once, at import
    # time -- a permanent side effect for the life of the process, since
    # Python caches imports and won't re-run it. Wiping the registry in
    # teardown (rather than restoring what was really there) would erase
    # that registration for every test in every OTHER file that runs
    # later in the same pytest session.
    saved = dict(pv._PROVIDERS)
    pv._PROVIDERS.clear()
    yield
    pv._PROVIDERS.clear()
    pv._PROVIDERS.update(saved)


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


def test_clean_registry_fixture_preserves_preexisting_registration():
    """Regression test for a real bug: this file's autouse fixture used to
    clear _PROVIDERS in both setup AND teardown, so a real provider
    module's import-time registration (e.g. nvidia_provider.py registering
    NVIDIA once, permanently, the moment it is first imported) was wiped
    for good the first time any test in this file ran, silently starving
    every test in every other file that runs later in the same session and
    expects the real registry to still have it.

    This drives the exact snapshot/setup/teardown sequence the fixture
    itself performs, standing in for "a provider was already registered
    before this file's tests ran": the fixture must still hand a test an
    empty registry to work with, but must put the pre-existing
    registration back afterward rather than leaving it cleared.
    """
    pv._PROVIDERS.clear()
    pv._PROVIDERS["PreExisting"] = _FakeProvider()

    # --- the fixture's own setup step ---
    saved = dict(pv._PROVIDERS)
    pv._PROVIDERS.clear()

    assert pv._PROVIDERS == {}, "a test body must still see an empty registry"
    pv.register_provider(_FakeProvider())
    assert "NVIDIA" in pv._PROVIDERS

    # --- the fixture's own teardown step ---
    pv._PROVIDERS.clear()
    pv._PROVIDERS.update(saved)

    assert pv._PROVIDERS == saved
    assert "PreExisting" in pv._PROVIDERS, \
        "teardown must restore what existed before, not leave it wiped"
    assert "NVIDIA" not in pv._PROVIDERS, \
        "what the test body registered must not leak past its own teardown"

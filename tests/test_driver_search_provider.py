from modules.driver_manager.driver_search_provider import DriverSearchProvider
from modules.driver_manager.driver_reader import DriverInfo
from core.search_provider import SearchQuery


def _drivers():
    return [
        DriverInfo(device_name="Realtek Ethernet Controller", driver_class="Net",
                   version="1.0.0", date="2024-01-01", publisher="Realtek",
                   signed=True, error_code=0, flags=""),
        DriverInfo(device_name="Generic USB Hub", driver_class="USB",
                   version="2.0.0", date="2023-05-01", publisher="Microsoft",
                   signed=True, error_code=0, flags=""),
    ]


def test_finds_a_driver_by_device_name():
    provider = DriverSearchProvider([_drivers()])
    results = provider.search(SearchQuery(text="realtek"))
    assert any("Realtek Ethernet Controller" in r.summary for r in results)


def test_finds_a_driver_by_class():
    provider = DriverSearchProvider([_drivers()])
    results = provider.search(SearchQuery(text="usb"))
    assert any("Generic USB Hub" in r.summary for r in results)


def test_empty_query_returns_nothing():
    provider = DriverSearchProvider([_drivers()])
    assert provider.search(SearchQuery(text="")) == []


def test_no_drivers_loaded_yet_returns_nothing():
    # Before a first refresh, the live cell holds an empty list.
    provider = DriverSearchProvider([[]])
    assert provider.search(SearchQuery(text="realtek")) == []


def test_the_ref_is_live_not_a_snapshot():
    ref = [[]]
    provider = DriverSearchProvider(ref)
    assert provider.search(SearchQuery(text="realtek")) == []
    ref[0] = _drivers()
    results = provider.search(SearchQuery(text="realtek"))
    assert any("Realtek Ethernet Controller" in r.summary for r in results)


def test_module_name_is_set_for_the_search_engines_per_provider_filter():
    assert DriverSearchProvider([[]]).module_name == "Driver Manager"


def test_filterable_fields_is_empty():
    assert DriverSearchProvider([[]]).get_filterable_fields() == []

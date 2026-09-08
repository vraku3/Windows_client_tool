from modules.store_apps.store_apps_search_provider import StoreAppsSearchProvider
from core.search_provider import SearchQuery


class _FakeModule:
    """Stand-in for StoreAppsModule -- only `_apps` is read."""

    def __init__(self, apps):
        self._apps = apps


def _apps():
    return [
        {"Name": "Microsoft.XboxApp", "Publisher": "CN=Microsoft Corporation",
         "Version": "1.0.0.0", "InstallLocation": r"C:\Program Files\WindowsApps\Xbox"},
        {"Name": "SpotifyAB.SpotifyMusic", "Publisher": "CN=Spotify AB",
         "Version": "2.0.0.0", "InstallLocation": r"C:\Program Files\WindowsApps\Spotify"},
    ]


def test_finds_an_app_by_name():
    provider = StoreAppsSearchProvider(_FakeModule(_apps()))
    results = provider.search(SearchQuery(text="xbox"))
    assert any("Microsoft.XboxApp" in r.summary for r in results)


def test_finds_an_app_by_publisher():
    provider = StoreAppsSearchProvider(_FakeModule(_apps()))
    results = provider.search(SearchQuery(text="spotify ab"))
    assert any("SpotifyAB.SpotifyMusic" in r.summary for r in results)


def test_empty_query_returns_nothing():
    provider = StoreAppsSearchProvider(_FakeModule(_apps()))
    assert provider.search(SearchQuery(text="")) == []


def test_no_apps_loaded_yet_returns_nothing():
    provider = StoreAppsSearchProvider(_FakeModule([]))
    assert provider.search(SearchQuery(text="xbox")) == []


def test_the_module_handle_is_live_not_a_snapshot():
    module = _FakeModule([])
    provider = StoreAppsSearchProvider(module)
    assert provider.search(SearchQuery(text="xbox")) == []
    module._apps = _apps()
    results = provider.search(SearchQuery(text="xbox"))
    assert any("Microsoft.XboxApp" in r.summary for r in results)


def test_module_name_is_set_for_the_search_engines_per_provider_filter():
    assert StoreAppsSearchProvider(_FakeModule([])).module_name == "Store Apps"


def test_filterable_fields_is_empty():
    assert StoreAppsSearchProvider(_FakeModule([])).get_filterable_fields() == []

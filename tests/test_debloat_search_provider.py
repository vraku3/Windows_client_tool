from modules.debloat.debloat_search_provider import DebloatSearchProvider
from core.search_provider import SearchQuery


def test_finds_an_app_by_name():
    provider = DebloatSearchProvider()
    results = provider.search(SearchQuery(text="xbox"))
    assert any(r.type == "app" and "Xbox" in r.summary for r in results)


def test_finds_a_tweak_by_name():
    provider = DebloatSearchProvider()
    results = provider.search(SearchQuery(text="cortana"))
    assert any(r.type == "tweak" for r in results)


def test_an_exact_match_outranks_a_substring_match():
    provider = DebloatSearchProvider()
    results = provider.search(SearchQuery(text="widgets"))
    assert results, "expected at least one match for 'widgets'"
    assert results == sorted(results, key=lambda r: -r.relevance)


def test_empty_query_returns_nothing():
    provider = DebloatSearchProvider()
    assert provider.search(SearchQuery(text="")) == []


def test_module_name_is_set_for_the_search_engines_per_provider_filter():
    assert DebloatSearchProvider().module_name == "Debloat"


def test_filterable_fields_distinguish_apps_from_tweaks():
    fields = DebloatSearchProvider().get_filterable_fields()
    kinds = next(f for f in fields if f.name == "type")
    assert set(kinds.values) == {"app", "tweak"}

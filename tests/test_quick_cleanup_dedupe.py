"""Quick Cleanup's headline total must not count a directory twice.

`_render_results` summed `result.total_size` per category with no
cross-category deduplication, and the categories overlap: `%TEMP%` and
`%LOCALAPPDATA%\\Temp` are one directory, so "Temp Files" and "Crash Dumps"
both measured it. Measured on this machine before the fix: **2.10 GB
claimed against 1.39 GB really there — 50% too high**, on the pane whose
whole job is one big number.

Deduplicating only inside each category would not have helped; the overlap
is BETWEEN them. Each unique path is attributed to the first category that
claims it, in display order, so the pie slices still sum to the total
rather than quietly disagreeing with it.
"""
import tempfile

import pytest

from modules.cleanup.cleanup_scanner import ScanItem, ScanResult


def _result(*paths, size=100):
    r = ScanResult()
    r.items = [ScanItem(path=p, size=size, is_dir=True) for p in paths]
    r.total_size = size * len(paths)
    return r


@pytest.fixture
def tab(qapp):
    from app import App
    from modules.cleanup.cleanup_module import CleanupModule

    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    module = CleanupModule()
    module.on_start(app)
    widget = module.create_widget()          # held, or Qt destroys the tree
    found = module._quick
    found._keep_alive = widget
    yield found
    try:
        app.shutdown()
    except Exception:  # noqa: BLE001 - teardown
        pass


def test_a_directory_claimed_twice_is_counted_once(tab):
    first = tab._categories[0][0]
    second = tab._categories[1][0]
    shared = r"C:\Users\x\AppData\Local\Temp"

    deduped = tab._deduplicate_across_categories({
        first: _result(shared),
        second: _result(shared),
    })

    total = sum(r.total_size for r in deduped.values())
    assert total == 100, f"counted twice: {total}"


def test_the_first_category_in_display_order_keeps_it(tab):
    first = tab._categories[0][0]
    second = tab._categories[1][0]
    shared = r"C:\Users\x\AppData\Local\Temp"

    deduped = tab._deduplicate_across_categories({
        second: _result(shared),
        first: _result(shared),
    })

    assert deduped[first].total_size == 100
    assert deduped[second].total_size == 0, (
        "the later category must give it up, whatever order the results "
        "arrived in — the workers finish in any order")


def test_a_nested_path_is_dropped_by_the_parent_that_holds_it(tab):
    first = tab._categories[0][0]
    second = tab._categories[1][0]

    deduped = tab._deduplicate_across_categories({
        first: _result(r"C:\Temp", size=1000),
        second: _result(r"C:\Temp\dumps", size=250),
    })

    assert sum(r.total_size for r in deduped.values()) == 1000


def test_categories_that_do_not_overlap_are_untouched(tab):
    first = tab._categories[0][0]
    second = tab._categories[1][0]

    deduped = tab._deduplicate_across_categories({
        first: _result(r"C:\A"),
        second: _result(r"C:\B"),
    })

    assert sum(r.total_size for r in deduped.values()) == 200


def test_the_slices_still_sum_to_the_total(tab):
    """The reason each path is attributed to one category rather than just
    dropped from the total: a pie whose slices disagree with the number
    beside it is worse than either."""
    first = tab._categories[0][0]
    second = tab._categories[1][0]
    third = tab._categories[2][0]
    shared = r"C:\Users\x\AppData\Local\Temp"

    deduped = tab._deduplicate_across_categories({
        first: _result(shared, r"C:\A"),
        second: _result(shared, r"C:\B"),
        third: _result(r"C:\C"),
    })

    slices = [r.total_size for r in deduped.values() if r.total_size]
    assert sum(slices) == 400, "one shared + A + B + C = four unique paths"


def test_a_browser_result_is_left_alone(tab):
    """The browser category holds BrowserResult objects, not a ScanResult;
    it must pass through untouched rather than raise."""
    browser_like = [object()]
    deduped = tab._deduplicate_across_categories({"browser": browser_like})
    assert deduped["browser"] is browser_like

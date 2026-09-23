"""Real bug (2026-09-23): `FilterPanel.filters_changed` was emitted on every
date/time/checkbox change but nothing was ever connected to it -- so opening
the filter panel, adjusting a date range or a source checkbox on an active
search silently did nothing until the next keystroke re-triggered
`SearchBar.search_requested` on its own.

`MainWindow._on_filters_changed` is now wired to `filters_changed` and
re-runs the active search using the search bar's own current text --
`FilterPanel.build_query` cannot supply it, since the panel does not know
what is currently typed (it always builds with `text=""`).
"""
import tempfile

import pytest


@pytest.fixture
def window(qapp, monkeypatch):
    import ui.main_window as mw
    from app import App

    monkeypatch.setattr(mw, "is_admin", lambda: True)
    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    win = mw.MainWindow(app)
    yield win
    win.close()
    try:
        app.shutdown()
    except Exception:  # noqa: BLE001 - teardown
        pass


def test_changing_a_filter_reruns_the_active_search(window, monkeypatch):
    window._search_bar._input.setText("disk error")

    calls = []
    monkeypatch.setattr(window, "_on_search",
                        lambda text, regex: calls.append((text, regex)))

    window._filter_panel.filters_changed.emit(object())
    assert calls == [("disk error", False)]


def test_changing_a_filter_with_no_active_search_is_a_no_op(window):
    """Empty search box: `_on_search`'s own early return applies, same as
    it would for any other trigger of a blank search."""
    window._search_bar._input.setText("")
    window._search_results.setVisible(True)  # prove this gets cleared, not left stale

    window._filter_panel.filters_changed.emit(object())
    assert window._search_results.isVisible() is False


def test_the_filters_changed_signal_is_actually_connected(window):
    """The exact shape of the original bug: nothing listened."""
    assert window._filter_panel.receivers(
        window._filter_panel.filters_changed) > 0

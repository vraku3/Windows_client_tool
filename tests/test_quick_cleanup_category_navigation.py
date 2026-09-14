"""Category cards on Quick Cleanup's dashboard jump to that category's own
deep-dive tab -- a real navigation flow the Cleanup/Quick Cleanup merge
specifically makes possible, since both were previously separate,
unreachable-from-each-other destinations.
"""


def test_clicking_a_main_category_card_calls_the_callback_with_its_id(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    clicked = []
    tab = QuickCleanupTab(on_category_clicked=clicked.append)
    tab.build(categories=[("browser", "Browser Caches", "#4dd0e1")], advanced_categories=[])

    card = tab._legend_cards[0]
    card.clicked.emit()

    assert clicked == ["browser"]


def test_advanced_category_cards_are_not_wired_to_navigation(qapp):
    """Advanced categories don't map 1:1 (or many:1) onto a single tab the
    way the 10 main categories do, so they stay inert -- clicking one
    must not raise even with no callback reachable for it."""
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    clicked = []
    tab = QuickCleanupTab(on_category_clicked=clicked.append)
    tab.build(categories=[("temp", "Temp Files", "#4caf50")],
             advanced_categories=[("recent", "Recent Files", "#90caf9")])

    card = tab._adv_cards[0]
    card.clicked.emit()  # must not raise

    assert clicked == []


def test_no_callback_given_is_safe_to_click(qapp):
    from modules.cleanup.components.quick_cleanup_tab import QuickCleanupTab

    tab = QuickCleanupTab()  # no on_category_clicked at all
    tab.build(categories=[("temp", "Temp Files", "#4caf50")], advanced_categories=[])

    tab._legend_cards[0].clicked.emit()  # must not raise

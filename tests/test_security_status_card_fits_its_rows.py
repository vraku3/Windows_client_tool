r"""Security overview cards clipped their detail rows.

Seen 2026-09-25: BitLocker's and HVCI's rows were squashed until half their
text was cut off. `_StatusCard` set an explicit `setMinimumHeight(120)`, and an
explicit minimum replaces the one Qt derives from the layout, so the card could
never grow to fit three detail rows.
"""
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from modules.security_dashboard.security_module import _StatusCard


def _data(rows):
    return {"status": "Partial", "color": "amber",
            "details": [(f"Row {i}", f"value {i}") for i in range(rows)]}


def test_a_card_with_no_rows_keeps_its_120px_floor(qapp):
    card = _StatusCard("Empty")
    card.update_status(_data(0))
    assert card.minimumSizeHint().height() >= 120


def test_a_card_with_many_rows_asks_for_more_than_the_floor(qapp):
    card = _StatusCard("Busy")
    card.update_status(_data(6))
    assert card.minimumSizeHint().height() > 120


def test_the_card_actually_gets_the_height_its_rows_need(qapp):
    """The visible failure: laid out in a parent, the card must be at least as
    tall as its own minimum, so no row is squashed."""
    parent = QWidget()
    layout = QVBoxLayout(parent)
    card = _StatusCard("Busy")
    layout.addWidget(card)
    card.update_status(_data(5))
    parent.resize(500, 100)                 # deliberately too short
    parent.show()
    qapp.processEvents()
    assert card.height() >= card.minimumSizeHint().height()
    for i in range(card._details_layout.count()):
        row = card._details_layout.itemAt(i).widget()
        assert row.height() >= row.minimumSizeHint().height(), i


def test_adding_rows_later_grows_the_minimum(qapp):
    card = _StatusCard("Late")
    card.update_status(_data(0))
    before = card.minimumSizeHint().height()
    card.update_status(_data(6))
    assert card.minimumSizeHint().height() > before

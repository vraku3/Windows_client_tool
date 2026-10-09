"""Filter chips: a row of exclusive buttons with live counts.

Shared by the Dashboard tabs and Thermal Control; it lived in
`modules.dashboard.tab_base`, which made Thermal Control import the
Dashboard while the Dashboard (Flight Recorder) imports Thermal Control.
"""


def make_chips(parent, filters, on_pick):
    """A row of exclusive filter buttons. Returns (layout, {key: (button, label)}).

    `filters` is the (key, label, predicate) tuples the engine modules expose.
    """
    from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton
    row = QHBoxLayout()
    row.setContentsMargins(4, 0, 4, 0)
    group = QButtonGroup(parent)
    group.setExclusive(True)
    chips = {}
    for key, label, _fn in filters:
        chip = QPushButton(label, parent)
        chip.setCheckable(True)
        chip.setChecked(key == "all")
        chip.clicked.connect(lambda _=False, k=key: on_pick(k))
        group.addButton(chip)
        chips[key] = (chip, label)
        row.addWidget(chip)
    row.addStretch(1)
    return row, chips


def set_chip_counts(chips, counts) -> None:
    for key, (chip, label) in chips.items():
        chip.setText(label if key == "all" else f"{label} ({counts.get(key, 0)})")

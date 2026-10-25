"""Dashboard composition helpers; summaries never substitute zero for a refused read."""
from ui.glance import GlanceStrip, GlanceTile
from ui.chip_bar import ChipBar


def summary(parent, layout, captions):
    tiles = {key: GlanceTile(label, tooltip=tip, parent=parent)
             for key, label, tip in captions}
    layout.addWidget(GlanceStrip(tiles.values(), parent))
    for tile in tiles.values():
        tile.value.setWordWrap(True)
        tile.sub.setWordWrap(True)
        tile.set(None, reason="Waiting for the first reading")
    return tiles


def unreadable(tiles, reason):
    for tile in tiles.values():
        tile.set_unreadable(reason)


def make_chips(parent, filters, on_pick):
    """Compatibility for existing Dashboard filters, backed by the shared ChipBar."""
    from PyQt6.QtWidgets import QHBoxLayout
    bar = ChipBar([(key, label, 0) for key, label, _fn in filters], parent=parent)
    bar.selection_changed.connect(lambda keys: on_pick(keys[0]) if keys else None)
    row = QHBoxLayout()
    row.addWidget(bar)
    return row, {key: (chip, chip.label) for key, chip in bar.chips.items()}


def set_chip_counts(chips, counts):
    for key, (chip, _label) in chips.items():
        chip.set_count(counts.get(key, 0))


def process_matches(info, key):
    if key == "elevated":
        return info.details.elevated is True
    if key == "high":
        return info.rates.cpu_percent is not None and info.rates.cpu_percent >= 10
    return True

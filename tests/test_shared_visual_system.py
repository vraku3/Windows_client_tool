"""Headless contracts for the shared visual system."""
from pathlib import Path
import importlib.util
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel, QTableWidget, QTableWidgetItem, QStyleOptionViewItem
from ui.module_intro import module_intro, add_module_intro
from ui.glance import GlanceTile, GlanceStrip
from ui.chip_bar import ChipBar
from ui.empty_state import EmptyState
from ui.table_style import apply_table_style


class Config:
    def __init__(self):
        self.data = {}

    def get(self, key, default):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def test_intro_collapse_elision_persistence_and_tooltip(qapp):
    config = Config()
    text = "A long first sentence explaining this pane and the selected measurements. More details here."
    intro = module_intro(text, config=config)
    intro.resize(300, 100)
    intro.show()
    qapp.processEvents()
    assert intro.details.isHidden()
    assert intro.label.text().endswith("…")
    assert intro.toolTip() == text
    intro.toggle.click()
    assert not intro.details.isHidden()
    second = module_intro(text, config=config)
    assert second.toggle.isChecked()
    intro.toggle.click()
    assert intro.details.isHidden()
    intro.close()


@pytest.mark.parametrize("grid", [False, True])
def test_shared_intro_insertion_leaves_child_panes_independent(qapp, grid):
    root = QWidget()
    layout = QGridLayout(root) if grid else QVBoxLayout(root)
    child = QWidget()
    QVBoxLayout(child)
    child_intro = add_module_intro(child, "Child explanation.")
    layout.addWidget(child)
    first = add_module_intro(root, "Parent explanation.")
    assert add_module_intro(root, "Duplicate text.") is first
    assert len(root.findChildren(QLabel, "moduleIntro")) == 2
    assert child_intro is not first


def test_decorators_share_idempotence(qapp):
    from modules.cleanup.help_ui import intro
    from ui.help.debloat_widgets import add_intro
    from ui.help.network_ui import _page_intro
    root = QWidget()
    QVBoxLayout(root)
    first = module_intro("Already explained.")
    root.layout().addWidget(first)
    for decorate in (intro, add_intro, _page_intro):
        decorate(root, "Repeated explanation.")
    assert root.layout().count() == 1


def test_tiles_values_severities_unknown_trends_and_keyboard(qapp):
    tile = GlanceTile("Files")
    tile.set(0, "Measured")
    assert tile.value.text() == "0"
    for severity in ("ok", "info", "warning", "danger", "unknown", "muted"):
        tile.set_severity(severity)
        tile.grab()
    tile.set_unreadable("Access denied")
    assert tile.value.text() == "—" and tile.severity == "unknown"
    assert "Access denied" in tile.toolTip()
    tile.set(5, severity="ok")
    assert tile.reason == "" and tile.severity == "ok"
    tile.set_history([1, None, 4, float("nan"), 2])
    assert tile.history == [1, 4, 2]
    clicks = []
    tile.clicked.connect(lambda: clicks.append(True))
    tile.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
    assert clicks == [True]
    strip = GlanceStrip([tile, GlanceTile("Other"), GlanceTile("Third")])
    strip.set_available_width(300)
    assert strip.columns() == 1
    strip.set_available_width(1000)
    assert strip.columns() == 3
    assert GlanceStrip().columns() == 1


def test_glance_accent_resolves_current_theme_at_paint_time(qapp):
    from PyQt6.QtGui import QColor
    from core.semantic_colors import set_theme, current_theme, semantic
    original = current_theme()
    tile = GlanceTile("Status", "warning")
    tile.resize(240, 100)
    try:
        for theme in ("dark", "light"):
            set_theme(theme)
            tile.repaint()
            image = tile.grab().toImage()
            assert image.pixelColor(1, 20) == QColor(semantic("warning"))
    finally:
        set_theme(original)


def test_late_tile_value_after_deletion_is_ignored(qapp):
    from PyQt6 import sip
    tile = GlanceTile("Status")
    sip.delete(tile)
    tile.set_value(4)


@pytest.mark.parametrize("exclusive", [True, False])
def test_chip_counts_selection_and_keyboard_focus(qapp, exclusive):
    bar = ChipBar((("all", "All", 8), ("errors", "Errors", 0)), exclusive=exclusive)
    assert bar.chips["errors"].property("zero")
    bar.chips["all"].setChecked(True)
    bar.chips["errors"].click()
    assert bar.selected() == (["errors"] if exclusive else ["all", "errors"])
    bar.set_counts({"errors": 3})
    assert bar.chips["errors"].text() == "Errors  3"
    assert not bar.chips["errors"].property("zero")
    assert bar.chips["errors"].focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_empty_loading_error_and_action(qapp):
    state = EmptyState("!", "No data", "Read the source.", "Retry")
    actions = []
    state.action_triggered.connect(lambda: actions.append(True))
    state.action_button.click()
    assert actions == [True]
    state.set_variant("loading", progress_text="Reading records")
    assert not state.progress.isHidden()
    state.set_variant("error", reason="Access denied")
    assert state.progress.isHidden()
    assert state.hint_label.text() == "Access denied"
    state.set_last_checked("Checked now")
    assert state.last_checked_label.text() == "Checked now"


def test_table_delegate_aligns_present_and_future_rows(qapp):
    table = QTableWidget(1, 3)
    apply_table_style(table, numeric_columns=(1,), date_columns=(2,))
    for col in range(3):
        table.setItem(0, col, QTableWidgetItem("value"))
        option = QStyleOptionViewItem()
        table.itemDelegate().initStyleOption(option, table.model().index(0, col))
        expected = Qt.AlignmentFlag.AlignLeft if col == 0 else Qt.AlignmentFlag.AlignRight
        assert option.displayAlignment & expected
    assert table.alternatingRowColors()
    assert table.verticalHeader().defaultSectionSize() == 30


def _gallery():
    spec = importlib.util.spec_from_file_location("ui_gallery", Path(__file__).parents[1] / "tools/ui_gallery.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gallery_handles_slotted_ui_objects(qapp):
    class Slotted:
        __slots__ = ()
    Slotted.__module__ = "ui.fake"
    class Owner:
        __module__ = "ui.fake"
        child = None
    owner = Owner()
    owner.child = Slotted()
    assert _gallery().tracked_workers(owner, None) == []


def test_gallery_contact_sheet_has_three_by_two_cells(tmp_path):
    from PIL import Image
    Image.new("RGB", (140, 90)).save(tmp_path / "sample.png")
    panes = [{"file": "sample.png", "label": f"Pane {i}"} for i in range(7)]
    _gallery().write_sheets(tmp_path, panes, "dark")
    sheets = list((tmp_path / "sheets").glob("*.png"))
    assert len(sheets) == 2
    with Image.open(sheets[0]) as sheet:
        assert sheet.size == (1500, 740)

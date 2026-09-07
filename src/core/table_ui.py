"""Shared table helpers: columns that adapt to the window, centred text.

Tables were hand-tuned one fixed column width at a time, which guaranteed a
horizontal scrollbar as soon as the window shrank and left long values cut
off. These helpers encode the choices a data table keeps repeating:

* header labels are centred,
* text-heavy columns share the free width (``Stretch``) or hug their content
  (``ResizeToContents``), so the table always fills the pane and follows the
  window as it is resized,
* cell text is centred unless the column holds prose.

The ``firewall`` pane keeps its own deliberate fit/zoom machinery and the log
viewer keeps its lines left-aligned; everything else can call these.
"""
from typing import Any, Iterable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem


class _SortableItem(QTableWidgetItem):
    """QTableWidgetItem that compares case-insensitively for alpha sorting."""

    def __lt__(self, other) -> bool:
        return self.text().lower() < other.text().lower()


#: Where a NumericSortItem's real value lives. Past UserRole+1..9, which
#: several modules already use for their own per-cell payload (e.g. a
#: package name or tweak id) — +10 is unclaimed.
_NUMERIC_SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 10


class NumericSortItem(QTableWidgetItem):
    """A cell that DISPLAYS formatted text but SORTS on a real number.

    Every "Size" or "Version" column that formats bytes/versions into text
    before display has this bug latently: a plain QTableWidgetItem sorts
    that text alphabetically, so "10 GB" lands before "9 GB". Store the
    real value once, here, and every such column gets a correct sort for
    the cost of one extra constructor argument. `value` is stored as-is
    (not coerced to float) so a tuple sort key -- e.g. appx_service's
    _version_key(), used for the Version column -- compares correctly via
    Python's native tuple ordering; Size columns keep passing a float/int
    exactly as before, unaffected by dropping the coercion.
    """

    def __init__(self, text: str, value: Any):
        super().__init__(text)
        self.setData(_NUMERIC_SORT_ROLE, value)

    def __lt__(self, other) -> bool:
        mine = self.data(_NUMERIC_SORT_ROLE)
        theirs = other.data(_NUMERIC_SORT_ROLE) \
            if isinstance(other, QTableWidgetItem) else None
        if mine is not None and theirs is not None:
            return mine < theirs
        return self.text().lower() < other.text().lower()

    @staticmethod
    def value(item) -> Any:
        return item.data(_NUMERIC_SORT_ROLE) if isinstance(item, QTableWidgetItem) else None


def centered_item(text: str = "", sortable: bool = False) -> QTableWidgetItem:
    """A table item whose text is centred (optionally sortable A-Z)."""
    item = _SortableItem(text) if sortable else QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


def set_role(widget, role: str) -> None:
    """Give `widget` a semantic role the stylesheets paint (see the
    "Semantic label roles" block in dark.qss / light.qss).

    Qt resolves objectName selectors when a widget is polished. Setting the
    name on a widget that is ALREADY on screen changes nothing until the
    style is asked again — so a status label toggling between
    "statusError" and "statusSuccess" would keep whichever colour it was
    first given, silently. unpolish/polish is what makes the change take.

    Preferred over `setStyleSheet("color: ...")`: an inline sheet beats the
    application sheet and is never revisited, so it survives a theme switch
    unchanged. That is how a pane ends up showing dark-theme text on a
    light ground.
    """
    if widget.objectName() == role:
        return
    widget.setObjectName(role)
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


def describe_table(table: QTableWidget, name: str = "") -> None:
    """Give `table` a name a screen reader can announce.

    Without one, an assistive technology reads a grid of unlabelled cells
    and the user has no way to know which table they are in — there were
    zero setAccessibleName calls in 92,000 lines.

    When no name is supplied it is built from the column headers, which
    every table has and which describe it accurately: "table with columns
    Name, PID, User name, CPU". Derived rather than required, so the 51
    call sites of fit_table/fit_last get it without each having to invent
    a name.
    """
    if not name:
        headers = []
        for column in range(table.columnCount()):
            item = table.horizontalHeaderItem(column)
            if item is not None and item.text().strip():
                headers.append(item.text().strip())
        name = ("table with columns " + ", ".join(headers)) if headers else "table"
    table.setAccessibleName(name)
    if not table.accessibleDescription():
        table.setAccessibleDescription(
            f"{table.rowCount()} rows, {table.columnCount()} columns")


def center_header(table: QTableWidget) -> None:
    """Centre the header labels of an existing table.

    Does not touch column sizing, so panes with bespoke fit logic (firewall,
    logs) can adopt the look without losing their widths.
    """
    table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)


def fit_table(table: QTableWidget, stretch: Iterable[int] = (),
              content: Iterable[int] = ()) -> None:
    """Centre the header and let columns fill the pane.

    Columns in ``stretch`` share the free width; columns in ``content`` hug
    their widest cell (and header); anything else keeps Qt's Interactive mode
    so the user can still drag sections.
    """
    center_header(table)
    describe_table(table)
    table.horizontalHeader().setStretchLastSection(False)
    header = table.horizontalHeader()
    for col in stretch:
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
    for col in content:
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)


def fit_last(table: QTableWidget) -> None:
    """Classic layout: content-sized columns with the last one stretching."""
    center_header(table)
    describe_table(table)
    header = table.horizontalHeader()
    count = table.columnCount()
    for col in range(count - 1):
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(count - 1, QHeaderView.ResizeMode.Stretch)

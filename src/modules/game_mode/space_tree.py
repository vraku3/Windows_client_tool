"""The "Free up space" location tree: every place a clean would touch, ticked by the owner.

Group -> scan item (path, size, newest file, safety) -> for a folder, its
largest top-level entries plus one "N more" row. What is ticked becomes the
`choice` `SpaceCleaner.clean` narrows each group's fresh scan to; nothing here
deletes or measures (measuring is `SpaceCleaner.breakdown`, on a worker).
"""
from __future__ import annotations

import ntpath
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

from core.formatting import format_size

from .engine import space

ROLE = Qt.ItemDataRole.UserRole
CHECKABLE = Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
COLUMNS = ["Location", "Present", "Newest file / status", "Safety", "Eligible"]


@dataclass
class Chosen:
    """What the owner ticked: the clean's `choice`, and the confirmation's rows."""
    choice: dict[str, dict] = field(default_factory=dict)
    rows: dict[str, list[tuple[str, int]]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(size for rows in self.rows.values() for _path, size in rows)

    @property
    def count(self) -> int:
        return sum(len(rows) for rows in self.rows.values())


def build_tree(parent) -> QTreeWidget:
    tree = QTreeWidget(parent)
    tree.setHeaderLabels(COLUMNS)
    tree.setAlternatingRowColors(True)
    tree.setUniformRowHeights(True)
    head = tree.header()
    head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for column in (1, 2, 3, 4):
        head.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    head.setStretchLastSection(False)
    return tree


def newest_text(stamp) -> str:
    return f"newest {datetime.fromtimestamp(stamp):%Y-%m-%d}" if stamp else "--"


def _age_rule(hours: int) -> str:
    return (f" Files modified in the last {hours} hours are kept: installers read their extracted "
            "files back." if hours else "")


def _row(parent, texts, tip, data, checkable=True) -> QTreeWidgetItem:
    item = QTreeWidgetItem(parent, texts)
    for column in range(len(COLUMNS)):
        item.setToolTip(column, tip)
    item.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    item.setData(0, ROLE, data)
    if checkable:
        item.setFlags(CHECKABLE)
        item.setCheckState(0, Qt.CheckState.Unchecked)
    else:
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


def _add_children(row, where: space.Location) -> None:
    rule = _age_rule(where.keep_newer_than_hours)
    for entry in where.children:
        name = ntpath.basename(entry.path) + ("\\" if entry.is_dir else "")
        _row(row, [name, format_size(entry.size), newest_text(entry.newest), where.safety, format_size(entry.eligible_bytes)],
             f"{entry.path}\n{format_size(entry.size)} on disk.{rule}",
             ("child", [(entry.path, entry.is_dir, entry.eligible_bytes)]))
    if where.rest:
        size = sum(e.size for e in where.rest)
        eligible = sum(e.eligible_bytes for e in where.rest)
        stamps = [e.newest for e in where.rest if e.newest]
        _row(row, [f"{len(where.rest)} more ({format_size(size)})", format_size(size),
                   newest_text(max(stamps) if stamps else None), where.safety, format_size(eligible)],
             f"The {len(where.rest)} smaller entries in {where.path}, together.{rule}",
             ("child", [(e.path, e.is_dir, e.eligible_bytes) for e in where.rest]))


def _add_location(group_row, gid: str, where: space.Location) -> None:
    rule = _age_rule(where.keep_newer_than_hours)
    if not where.deletable:
        _row(group_row, [where.path, format_size(where.size), newest_text(where.newest),
                         "danger -- not part of this cleanup", "--"],
             f"{where.path}\nMarked dangerous by its scanner; never deleted here.", None, checkable=False)
        return
    status = newest_text(where.newest)
    if where.error:
        status = "could not check"
    elif where.size == 0:
        status = "Nothing to clean"
    elif where.eligible_bytes == 0 and where.keep_newer_than_hours:
        status = f"kept: all files newer than {where.keep_newer_than_hours} h"
    tip = f"{where.path}\n{format_size(where.size)}.{rule}" + (
        f"\nCould not check its contents ({where.error})."
        if where.error else "")
    row = _row(group_row, [where.path, format_size(where.size) if not where.error else "--", status, where.safety,
                           format_size(where.eligible_bytes) if not where.error else "--"], tip,
               ("item", gid, where.path, where.eligible_bytes, where.is_dir), checkable=not bool(where.error))
    if where.children or where.rest:
        row.setFlags(row.flags() | Qt.ItemFlag.ItemIsAutoTristate)
        _add_children(row, where)


def fill(tree: QTreeWidget, breakdown) -> None:
    """(Re)build from `SpaceCleaner.breakdown()`; None = not measured yet."""
    tree.blockSignals(True)
    tree.clear()
    for group in space.GROUPS:
        found = None if breakdown is None else breakdown.get(group.id)
        if found is None or isinstance(found, str):
            text = "not measured yet -- click Refresh" if found is None else f"could not check ({found})"
            _row(tree, [group.label, "", text, ""], f"{group.why}\n{text}", ("group", group.id), checkable=False)
            continue
        total = sum(w.size for w in found if w.deletable)
        eligible = sum(w.eligible_bytes for w in found)
        row = _row(tree, [group.label, format_size(total), f"{len(found)} location(s)" if found else "Nothing to clean", "", format_size(eligible)],
                   group.why, ("group", group.id), checkable=bool(found))
        font = row.font(0)
        font.setBold(True)
        row.setFont(0, font)
        if found:
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsAutoTristate)
        for where in found:
            _add_location(row, group.id, where)
        if found and group.default_checked:
            row.setCheckState(0, Qt.CheckState.Checked)
    tree.blockSignals(False)


def _children_of(row) -> list[QTreeWidgetItem]:
    return [row.child(i) for i in range(row.childCount())]


def chosen(tree: QTreeWidget) -> Chosen:
    """Exactly what is ticked. A fully ticked folder is cleaned whole (so its
    age floor and guards apply to everything in it); a partly ticked one only
    through its ticked entries."""
    out = Chosen()
    candidates = []
    for g in range(tree.topLevelItemCount()):
        group_row = tree.topLevelItem(g)
        gid = group_row.data(0, ROLE)[1]
        for row in _children_of(group_row):
            data = row.data(0, ROLE)
            if not data or row.checkState(0) == Qt.CheckState.Unchecked:
                continue
            _kind, _gid, path, size, is_dir = data
            if row.checkState(0) == Qt.CheckState.Checked:
                candidates.append(SimpleNamespace(gid=gid, root=path, path=path, size=size, is_dir=is_dir, whole=True))
                continue
            for child in _children_of(row):
                if child.checkState(0) == Qt.CheckState.Checked:
                    for child_path, is_dir, child_size in child.data(0, ROLE)[1]:
                        candidates.append(SimpleNamespace(gid=gid, root=path, path=child_path,
                            size=child_size, is_dir=is_dir, whole=False))
    for item in space._dedupe(candidates):
        picks = out.choice.setdefault(item.gid, {})
        out.rows.setdefault(item.gid, []).append((item.path, item.size))
        if item.whole:
            picks[item.root] = None
        else:
            picks.setdefault(item.root, []).append((item.path, item.is_dir))
    return out

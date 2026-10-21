"""The Optimise OS tab. Reads and mutations run on tracked Workers.

Three pages: reversible settings (journal + exact Revert), one-way "Free up
space" (separate confirmation, never journaled) and "Apps you may not use"
(suggestions only; App Buster does any removal).
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                             QPushButton, QRadioButton, QStackedWidget, QTabWidget, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from core.formatting import format_size
from core.table_ui import set_role
from core.widget_life import widget_is_valid
from core.worker import Worker
from ui.error_banner import ErrorBanner

from . import confirm_dialog, space_tree
from .engine import app_usage, optimise, space

logger = logging.getLogger(__name__)


def _read_all(engine, selected):
    """Everything Refresh shows. Each optional section fails on its own."""
    rows = engine.inspect(selected)
    extras = {"notes": [], "space": None, "usage": None}
    try:
        extras["notes"] = engine.notes()
    except (OSError, ValueError) as error:
        logger.warning("optimise OS: notes unreadable: %s", error)
        extras["notes"] = [f"Notes could not be read: {error}"]
    if getattr(engine, "space", None) is not None:
        try:
            extras["space"] = engine.space.breakdown()
        except (OSError, ValueError, RuntimeError) as error:
            logger.warning("optimise OS: space breakdown failed: %s", error)
            extras["space"] = error
    if getattr(engine, "usage", None) is not None:
        try:
            extras["usage"] = engine.usage.analyse()
        except (OSError, ValueError) as error:
            logger.warning("optimise OS: app usage failed: %s", error)
            extras["usage"] = error
    return rows, extras


class OptimiseTab(QWidget):
    def __init__(self, pool, parent=None, optimiser=None, host_busy=lambda: False,
                 busy_changed=lambda: None, navigate=lambda name: None) -> None:
        super().__init__(parent)
        self._pool = pool
        self._engine = optimiser if optimiser is not None else optimise.Optimiser()
        self._host_busy = host_busy
        self._busy_changed = busy_changed
        self._navigate = navigate
        self._workers: list[Worker] = []
        self._worker = None
        self._loaded = False
        self._selected = optimise.profile_ids("tailored")
        self._rows = []
        self._pending = None
        self._summary = ""
        lay = QVBoxLayout(self)
        lay.addLayout(self._build_bar())
        note = QLabel("Persistent preferences, independent of Game Mode ON/OFF: Apply and Revert work "
                      "in either state. While Game Mode is ON, only Re-enable SysMain (if Game Mode "
                      "stopped it) and USB selective suspend (Game Mode switched the power plan) wait "
                      "until Game Mode OFF. Tick optional rows yourself; read-only rows belong to Tweaks.", self)
        note.setWordWrap(True)
        set_role(note, "muted")
        lay.addWidget(note)
        self._banner = ErrorBanner("", self)
        self._banner.hide()
        lay.addWidget(self._banner)
        self._status = QLabel("No data -- click Refresh", self)
        self._status.setWordWrap(True)
        lay.addWidget(self._status)
        self._observed = QLabel("", self)
        self._observed.setWordWrap(True)
        set_role(self._observed, "statusWarning")
        self._observed.hide()
        lay.addWidget(self._observed)
        self._pages = QTabWidget(self)
        self._pages.addTab(self._build_settings(), "Settings (reversible)")
        self._pages.addTab(self._build_space(), "Free up space (one-way)")
        self._pages.addTab(self._build_usage(), "Apps you may not use")
        lay.addWidget(self._pages, 1)

    # ---- pages ---------------------------------------------------------------------------------
    def _build_bar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self._tailored = QRadioButton("Tailored (this PC)", self)
        self._maximum = QRadioButton("Maximum", self)
        self._tailored.setChecked(True)
        self._tailored.toggled.connect(lambda checked: checked and self._profile("tailored"))
        self._maximum.toggled.connect(lambda checked: checked and self._profile("maximum"))
        self._apply = QPushButton("Apply", self)
        set_role(self._apply, "accentButton")
        self._revert = QPushButton("Revert", self)
        self._refresh = QPushButton("Refresh", self)
        self._apply.clicked.connect(self._preview_apply)
        self._revert.clicked.connect(self._preview_revert)
        self._refresh.clicked.connect(self.refresh)
        for widget in (self._tailored, self._maximum, self._apply, self._revert, self._refresh):
            bar.addWidget(widget)
        bar.addStretch()
        return bar

    def _build_settings(self) -> QWidget:
        settings = QWidget(self)
        settings_lay = QVBoxLayout(settings)
        settings_lay.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget(self)
        self._tree = QTreeWidget(self)
        self._tree.setHeaderLabels(["Setting", "Now", "Profile target", "Status", "Why"])
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(True)
        self._tree.setWordWrap(True)
        head = self._tree.header()
        for column in (0, 2, 3):
            head.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 4):
            head.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self._tree.itemChanged.connect(self._toggled)
        self._tree.currentItemChanged.connect(self._details_changed)
        self._stack.addWidget(self._tree)
        empty = QLabel("No data -- click Refresh", self)
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(empty)
        self._stack.setCurrentIndex(1)
        settings_lay.addWidget(self._stack, 3)
        self._notes = QPlainTextEdit(self)
        self._notes.setReadOnly(True)
        self._notes.setPlainText("Select a setting to see what it does and what it costs.")
        settings_lay.addWidget(self._notes, 1)
        return settings

    def _build_space(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        warn = QLabel(space.ONE_WAY_NOTE, page)
        warn.setWordWrap(True)
        set_role(warn, "statusWarning")
        lay.addWidget(warn)
        hint = QLabel("Expand a group to see every location with its size and newest file; tick "
                      "exactly what goes. A folder ticked in part deletes only its ticked entries.", page)
        hint.setWordWrap(True)
        set_role(hint, "muted")
        lay.addWidget(hint)
        self._space_tree = space_tree.build_tree(page)
        self._space_tree.itemChanged.connect(self._space_ticked)
        space_tree.fill(self._space_tree, None)
        lay.addWidget(self._space_tree, 3)
        self._dism_box = QCheckBox("Component store cleanup (DISM; can take several minutes)", page)
        self._dism_box.setToolTip(space.DISM_NOTE)
        lay.addWidget(self._dism_box)
        for text in (space.DISM_NOTE, "Excluded on purpose: " + "; ".join(
                f"{name} ({why})" for name, why in space.EXCLUDED) + "."):
            label = QLabel(text, page)
            label.setWordWrap(True)
            set_role(label, "muted")
            lay.addWidget(label)
        row = QHBoxLayout()
        self._space_btn = QPushButton("Free up space...", page)
        self._space_btn.clicked.connect(self._preview_space)
        row.addWidget(self._space_btn)
        self._space_ticked_label = QLabel("Nothing ticked.", page)
        set_role(self._space_ticked_label, "muted")
        row.addWidget(self._space_ticked_label)
        row.addStretch()
        lay.addLayout(row)
        self._space_result = QPlainTextEdit(page)
        self._space_result.setReadOnly(True)
        self._space_result.setPlainText("Nothing deleted yet. Locations and sizes are read on Refresh.")
        lay.addWidget(self._space_result, 1)
        if getattr(self._engine, "space", None) is None:
            page.setEnabled(False)
        return page

    def _build_usage(self) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        note = QLabel("Suggestions only, from the usage records Windows keeps (UserAssist, BAM, Prefetch). "
                      "\"No usage record\" means unknown, never unused. Removal happens in App Buster, "
                      "which reads back every result.", page)
        note.setWordWrap(True)
        set_role(note, "muted")
        lay.addWidget(note)
        self._usage_summary = QLabel("No data -- click Refresh", page)
        self._usage_summary.setWordWrap(True)
        lay.addWidget(self._usage_summary)
        self._usage_tree = QTreeWidget(page)
        self._usage_tree.setHeaderLabels(["App", "Kind", "State", "Last seen", "Source", "Location"])
        self._usage_tree.setRootIsDecorated(False)
        self._usage_tree.setAlternatingRowColors(True)
        head = self._usage_tree.header()
        for column in range(5):
            head.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self._usage_tree, 1)
        row = QHBoxLayout()
        self._buster_btn = QPushButton("Open in App Buster", page)
        self._buster_btn.setToolTip("App Buster removes apps and reads back every result.")
        self._buster_btn.clicked.connect(lambda: self._navigate("App Buster"))
        row.addWidget(self._buster_btn)
        row.addStretch()
        lay.addLayout(row)
        return page

    @property
    def busy(self) -> bool:
        return self._worker is not None or self._pending is not None

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._loaded:
            self.refresh()

    def cancel(self) -> None:
        """Stop between steps; an in-flight mutation completes its journal/read-back."""
        self._pending = None
        for worker in self._workers:
            worker.cancel()
        self._loaded = False

    def _profile(self, profile: str) -> None:
        self._selected = optimise.profile_ids(profile)
        if self._rows:
            self._fill(self._rows)

    def _toggled(self, item, column) -> None:
        if column != 0:
            return
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not key or optimise.BY_ID[key].read_only:
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            self._selected.add(key)
        else:
            self._selected.discard(key)
        self._fill(self._rows)

    def _fill(self, rows) -> None:
        if not widget_is_valid(self._tree):
            return
        current = self._tree.currentItem()
        current_id = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        self._rows = rows
        self._tree.blockSignals(True)
        self._tree.clear()
        for row in rows:
            spec = optimise.BY_ID[row.id]
            status = row.status
            why = row.why
            offered = row.offered and not spec.read_only
            if status in ("Will change", "Not applicable") and offered and row.before is not None:
                status = "Will change" if row.id in self._selected else "Not applicable"
                why = spec.why + (" Not selected; tick it to opt in." if row.id not in self._selected else "")
            target = optimise.display(row.target) if row.target is not None else "Could not read"
            if spec.read_only:
                target = "Observed only" if spec.kind == "plan" else f"{spec.value} (read only)"
            elif not row.offered:
                target = "Not offered here"
            elif row.id not in self._selected:
                target = "Not selected"
            item = QTreeWidgetItem([spec.label, optimise.display(row.before), target, status, why])
            item.setData(0, Qt.ItemDataRole.UserRole, row.id)
            item.setToolTip(4, why)
            if offered:
                item.setCheckState(0, Qt.CheckState.Checked if row.id in self._selected else Qt.CheckState.Unchecked)
            self._tree.addTopLevelItem(item)
            if row.id == current_id:
                self._tree.setCurrentItem(item)
        self._tree.blockSignals(False)
        self._stack.setCurrentIndex(0)

    def _fill_extras(self, extras) -> None:
        notes = extras.get("notes") or []
        self._observed.setText("\n".join(notes))
        self._observed.setVisible(bool(notes))
        breakdown = extras.get("space")
        if isinstance(breakdown, Exception):
            self._space_result.setPlainText(f"Locations could not be read: {breakdown}")
        elif breakdown is not None:
            space_tree.fill(self._space_tree, breakdown)
            self._space_ticked()
        usage = extras.get("usage")
        if isinstance(usage, Exception):
            self._usage_summary.setText(f"Usage records could not be read: {usage}")
        elif usage is not None:
            self._usage_summary.setText(usage.summary())
            self._usage_tree.clear()
            for app in usage.apps:
                last = f"{app.last:%Y-%m-%d}" if app.last else "--"
                state = app.state if app.state != app_usage.UNKNOWN else (
                    f"{app.state} (unknown, not unused; {usage.reach_text()})")
                item = QTreeWidgetItem([app.name, app.kind, state, last, app.source, app.location])
                item.setToolTip(2, state)
                self._usage_tree.addTopLevelItem(item)

    def _details_changed(self, item, previous=None) -> None:
        if item is None:
            return
        spec = optimise.BY_ID[item.data(0, Qt.ItemDataRole.UserRole)]
        row = next(r for r in self._rows if r.id == spec.id)
        self._notes.setPlainText(f"{spec.label}\n\nNow: {optimise.display(row.before)}\n\n{item.text(4)}")

    def refresh(self) -> None:
        selected = set(self._selected)
        self._start("refresh", lambda worker: _read_all(self._engine, selected))

    def _preview_apply(self) -> None:
        selected = set(self._selected)

        def work(worker):
            return self._engine.inspect(selected), selected

        self._start("preview_apply", work)

    def _preview_revert(self) -> None:
        def work(worker):
            return self._engine._load()["items"]

        self._start("preview_revert", work)

    def _space_ticked(self, *_args) -> None:
        picked = space_tree.chosen(self._space_tree)
        self._space_ticked_label.setText(
            f"Ticked: {format_size(picked.total)} in {picked.count} location(s)." if picked.count
            else "Nothing ticked.")

    def _preview_space(self) -> None:
        if self.busy or self._host_busy():
            return
        picked = space_tree.chosen(self._space_tree)
        dism = self._dism_box.isChecked()
        if not picked.choice and not dism:
            self._space_result.setPlainText("Tick at least one location first.")
            return
        lines = []
        for gid, rows in picked.rows.items():
            group = space.BY_ID[gid]
            lines.append(f"{group.label}: {group.why}\n" + "\n".join(
                f"  {path} -- {format_size(size)}" for path, size in rows))
        if picked.count:
            lines.append(f"Total: {format_size(picked.total)} in {picked.count} location(s), measured "
                         "on Refresh; what is freed is measured as it goes.")
        if dism:
            lines.append(f"Component store cleanup: {space.DISM_NOTE}")
        text = ("\n\n".join(lines) + "\n\nExcluded: "
                + ", ".join(name for name, _why in space.EXCLUDED) + ".")
        if not self._confirm("Free up space (one-way)", "Delete exactly these? " + space.ONE_WAY_NOTE, text):
            return
        selected = set(picked.choice) | ({space.DISM_ID} if dism else set())
        choice = picked.choice
        self._start("space", lambda w: self._engine.space.clean(
            selected, log=lambda line: logger.info("optimise OS DISM: %s", line),
            cancelled=lambda: w.is_cancelled, choice=choice))

    def _set_controls(self) -> None:
        enabled = not self.busy
        for widget in (self._tailored, self._maximum, self._apply, self._revert, self._refresh, self._tree,
                       self._space_btn, self._space_tree, self._dism_box):
            widget.setEnabled(enabled)
        self._busy_changed()

    def _start(self, kind, work) -> None:
        if self.busy or self._host_busy():
            return
        if kind != "refresh" or not self._summary:
            self._banner.hide()
        self._status.setText("Reading this PC..." if kind in ("refresh", "preview_apply", "preview_revert")
                             else "Applying..." if kind == "apply"
                             else "Freeing up space (measured as it goes)..." if kind == "space"
                             else "Restoring recorded before-state...")
        worker = Worker(work)
        self._worker = worker
        self._workers.append(worker)
        worker.signals.result.connect(lambda payload: self._done(worker, kind, payload))
        worker.signals.error.connect(lambda error: self._failed(worker, error))
        worker.signals.finished.connect(lambda: self._finished(worker))
        self._set_controls()
        pool = self._pool() if callable(self._pool) else self._pool
        pool.start(worker)

    def _done(self, worker, kind, payload) -> None:
        if not widget_is_valid(self) or worker is not self._worker or worker.is_cancelled:
            return
        if kind == "refresh":
            self._loaded = True
            rows, extras = payload
            self._fill(rows)
            self._fill_extras(extras)
            self._status.setText(self._summary or "Read complete. Select a setting for details and costs.")
        elif kind == "preview_apply":
            rows, selected = payload
            self._fill(rows)
            changes = [r for r in rows if r.id in selected and r.status == "Will change"]
            if not changes:
                self._status.setText("Nothing to change. " + "; ".join(
                    f"{optimise.BY_ID[r.id].label}: {r.status}" for r in rows if r.id in selected))
                return
            expected = {r.id: r.before for r in changes}
            text = "\n\n".join(
                f"{optimise.BY_ID[r.id].label}: {optimise.display(r.before)} -> {optimise.display(r.target)}\n"
                f"{optimise.BY_ID[r.id].why}" for r in changes)
            if any(optimise.BY_ID[r.id].kind == "capability" for r in changes):
                text += ("\n\nRemoving Windows features: " + optimise.CAPABILITY_REVERT_NOTE
                         + " A restart may be needed.")
            other = [r for r in rows if r.id in selected and r not in changes]
            if other:
                text += "\n\nLeft alone:\n" + "\n".join(
                    f"{optimise.BY_ID[r.id].label}: {r.status} -- {r.why}" for r in other)
            if (self._confirm("Apply Optimise OS", "Apply exactly these changes?", text)
                    and widget_is_valid(self) and not worker.is_cancelled):
                action = ("apply", lambda w: self._engine.apply(selected, expected, lambda: w.is_cancelled))
                if self._worker is worker:
                    self._pending = action
                else:  # finished may have been delivered inside the confirmation event loop
                    self._start(*action)
        elif kind == "preview_revert":
            if not payload:
                self._status.setText("No Optimise OS changes are recorded to revert.")
                return
            text = "\n\n".join(
                f"{optimise.BY_ID[e['id']].label} -> {optimise.display(e['before'])}" for e in reversed(payload))
            if any(optimise.BY_ID[e["id"]].kind == "capability" for e in payload):
                text += "\n\nRe-adding Windows features: " + optimise.CAPABILITY_REVERT_NOTE
            if (self._confirm("Revert Optimise OS",
                              "Restore only these recorded before-states, in reverse order?", text)
                    and widget_is_valid(self) and not worker.is_cancelled):
                action = ("revert", lambda w: self._engine.revert(lambda: w.is_cancelled))
                if self._worker is worker:
                    self._pending = action
                else:
                    self._start(*action)
        elif kind == "space":
            text = payload.text()
            self._space_result.setPlainText(text)
            self._status.setText("Free up space finished. " + text.splitlines()[0])
            if payload.skipped or (payload.dism and payload.dism.get("returncode")):
                self._banner.set_error("Free up space: some parts were refused or failed.\n" + text)
            selected = set(self._selected)
            self._pending = ("refresh", lambda w: _read_all(self._engine, selected))
        else:
            self._summary = payload.text()
            self._status.setText(self._summary)
            self._notes.setPlainText(self._summary)
            if payload.failed:
                self._banner.set_error("Some settings could not be verified. The journal is kept; retry Revert.\n"
                                       + "\n".join(f"{optimise.BY_ID[k].label}: {v}" for k, v in payload.failed.items()))
            selected = set(self._selected)
            self._pending = ("refresh", lambda w: _read_all(self._engine, selected))

    def _confirm(self, title, lead, details) -> bool:
        return confirm_dialog.confirm(self, title, lead, details)

    def _failed(self, worker, error) -> None:
        if not widget_is_valid(self) or worker is not self._worker:
            return
        logger.warning("optimise OS: worker failed: %s", error)
        self._status.setText(str(error))
        self._banner.set_error(str(error))

    def _finished(self, worker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)
        if not widget_is_valid(self) or worker is not self._worker:
            return
        self._worker = None
        pending, self._pending = self._pending, None
        self._set_controls()
        if pending:
            self._start(*pending)

"""One stage of a Debloat "Run All" pass: apply a preset's tweaks, then its
apps, against one already-created restore point. Sequencing only -- every
actual write goes through the same TweakEngine.apply_tweak / app-removal
path the Apps and Tweaks tabs already use; this file adds no new way to
change the machine, only a way to run several presets back to back.
"""
import logging
from typing import Callable, Dict, List, Set

from PyQt6.QtCore import QThreadPool
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from core.worker import Worker
from modules.tweaks.tweak_engine import TweakEngine

logger = logging.getLogger(__name__)


def run_stage(preset_name: str, tweaks: List[dict], catalog: Dict[str, dict],
             engine, rp_id: str,
             resolve_tweak_ids: Callable[[dict, List[dict]], Set[str]],
             resolve_app_entry_ids: Callable[[dict, Dict[str, dict]], Set[str]],
             apply_apps: Callable[[Set[str], str], int]) -> Dict[str, int]:
    from modules.debloat import debloat_presets as dp
    preset = dp.load_preset(preset_name)

    tweak_ids = resolve_tweak_ids(preset, tweaks)
    by_id = {t["id"]: t for t in tweaks}
    tweaks_applied = sum(
        1 for tid in tweak_ids
        if tid in by_id and engine.apply_tweak(by_id[tid], rp_id))

    app_ids = resolve_app_entry_ids(preset, catalog)
    apps_applied = apply_apps(app_ids, rp_id) if app_ids else 0

    return {"tweaks_applied": tweaks_applied, "apps_applied": apps_applied}


#: Internal name -> display label, in the order the checkbox row shows
#: them. Matches the labels already used by the preset buttons elsewhere in
#: debloat_module.py.
_PRESET_LABELS = (
    ("light", "Light Debloat"),
    ("full", "Full Debloat"),
    ("privacy", "Privacy-Focused"),
    ("custom", "Custom"),
)


class RunAllTab(QWidget):
    """Sequences the four Debloat presets -- tweaks then apps, per preset --
    through one shared restore point. Follows UpdatesModule's `_RunAllTab`
    layout shape: a row of plain QCheckBox widgets (not a QListWidget), a
    Run/Stop/status button row, and a QPlainTextEdit transcript fed by
    `worker.signals.log_line`.

    Takes the `DebloatToolsModule` instance directly rather than a separate
    `set_app(app)` call -- it already exposes everything this tab needs:
    `.app`, `._session`, `._load_tweak_definitions`, `._load_debloat_entries`,
    and `.TweakEngine`/`._engine`.
    """

    def __init__(self, module, parent=None):
        super().__init__(parent)
        self._module = module
        self._workers: list = []
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        desc = QLabel(
            "Runs the checked presets in sequence -- each preset's tweaks, "
            "then its apps -- against one shared restore point."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        checks_row = QHBoxLayout()
        self._checks: Dict[str, QCheckBox] = {}
        for key, label in _PRESET_LABELS:
            chk = QCheckBox(label)
            chk.setChecked(False)
            self._checks[key] = chk
            checks_row.addWidget(chk)
        checks_row.addStretch()
        layout.addLayout(checks_row)

        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Run All")
        self._run_btn.clicked.connect(self._do_run)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._do_stop)
        self._status_lbl = QLabel("")
        btn_row.addWidget(self._run_btn)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._status_lbl)
        layout.addLayout(btn_row)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9))
        layout.addWidget(self._log, 1)

    def _selected_presets(self) -> List[str]:
        return [key for key, _label in _PRESET_LABELS if self._checks[key].isChecked()]

    def _do_run(self) -> None:
        module = self._module
        if module.app is None:
            return
        if not module.require_admin():
            return
        presets = self._selected_presets()
        if not presets:
            self._status_lbl.setText("Select at least one preset.")
            return

        self._run_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._log.clear()
        self._status_lbl.setText("Running…")

        from modules.debloat import debloat_presets as dp

        tweaks = (module._load_tweak_definitions("tweak")
                 + module._load_tweak_definitions("ai"))
        catalog = module._load_debloat_entries()
        rp_id = module._session.restore_point_id("Run All")
        engine = module._engine or TweakEngine(module.app.backup)

        def apply_apps_fn(ids, rp):
            return apply_app_entries(ids, rp, catalog, engine)[0]

        def _run(worker):
            for preset_name in presets:
                if worker.is_cancelled:
                    worker.signals.log_line.emit("Cancelled.")
                    break
                label = dict(_PRESET_LABELS)[preset_name]
                worker.signals.log_line.emit(f"--- preset: {label} ---")
                result = run_stage(
                    preset_name, tweaks, catalog, engine, rp_id,
                    dp.resolve_tweak_ids, dp.resolve_app_entry_ids,
                    apply_apps_fn,
                )
                worker.signals.log_line.emit(
                    f"{label}: {result['tweaks_applied']} tweak(s), "
                    f"{result['apps_applied']} app(s) applied")
            return None

        w = Worker(_run)
        w.signals.log_line.connect(self._log.appendPlainText)
        w.signals.result.connect(self._on_run_done)
        w.signals.error.connect(self._on_run_error)
        self._workers.append(w)
        QThreadPool.globalInstance().start(w)

    def _do_stop(self) -> None:
        for w in self._workers:
            w.cancel()
        self._stop_btn.setEnabled(False)

    def _on_run_done(self, _result) -> None:
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status_lbl.setText("Run complete.")

    def _on_run_error(self, err: str) -> None:
        self._run_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._status_lbl.setText(f"Error: {err}")
        self._log.appendPlainText(f"Error: {err}")

    def _cancel_all(self) -> None:
        for w in self._workers:
            w.cancel()
        self._workers.clear()


def apply_app_entries(entry_ids, rp_id, entries, engine, is_cancelled=lambda: False,
                      on_progress=None):
    """Apply a set of Debloat app-catalog entry ids against an already-open
    restore point. Returns (success_count, targeted_package_names). Shared
    by `_do_apply_apps` (the Apps tab's own button) and `RunAllTab` -- the
    actual write path is identical either way, only what triggers it
    differs."""
    success, targeted = 0, []
    for i, eid in enumerate(entry_ids):
        if is_cancelled():
            break
        entry = entries.get(eid)
        if entry:
            pkg = entry.get("package", eid)
            targeted.append(pkg)
            logger.info("Debloat: removing %s", pkg)
            if engine.apply_tweak(entry, rp_id):
                success += 1
        if on_progress:
            on_progress(i + 1)
    return success, targeted

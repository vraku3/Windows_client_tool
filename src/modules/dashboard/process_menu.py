"""The right-click menu on a process row.

Task Manager's Details menu, over the verified actions in
`procengine/actions.py`.

Two things are deliberate:

- **Every destructive action confirms, and names what it will hit.** "End
  process tree" on a browser can take twenty processes with it; a dialog that
  says "End 20 processes?" is a different decision from "End task?".
- **The outcome is reported, always.** A kill that was refused says so. The
  engine already returns a reason rather than a boolean precisely so this
  layer has something true to show, and silently doing nothing is the failure
  mode that makes a process manager untrustworthy.
"""
import logging
from core.widget_life import widget_is_valid
import os
import subprocess
import webbrowser
from typing import List

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QFileDialog, QInputDialog, QMenu,
                             QMessageBox)

from core.procengine.actions import (PRIORITY_LABELS, create_dump, end_process,
                                 end_process_tree, restart_process,
                                 resume_process, run_as, set_affinity,
                                 set_priority, suspend_process)
from core.procengine.snapshot import descendants_of

logger = logging.getLogger(__name__)


def _widget_valid(widget) -> bool:
    """Whether the widget a worker result wants to paint on still exists.

    A Verify-signature or VirusTotal check runs on a Worker, and the menu
    can be closed while it is out. A worker that fired after its host
    widget was deleted is guarded with sip -- the same guard the lower
    panes use -- because a Qt call on a deleted object is a dead process,
    not a traceback.
    """
    return widget_is_valid(widget)


class ProcessMenu(QObject):
    """Builds and runs the context menu for the selected processes."""

    #: Something changed, so whoever owns the table should read again.
    changed = pyqtSignal()

    def __init__(self, widget) -> None:
        super().__init__(widget)
        self._widget = widget
        self._app = None

    def set_app(self, app) -> None:
        """The app gives access to config (VirusTotal API key) and the
        thread pool for background checks."""
        self._app = app

    def show(self, pids: List[int], info, position) -> None:
        if not pids:
            return
        menu = QMenu(self._widget)
        many = len(pids) > 1

        end = menu.addAction(f"End task ({len(pids)})" if many else "End task")
        end.triggered.connect(lambda: self._end(pids))

        tree = menu.addAction("End process tree")
        tree.setEnabled(not many)
        tree.triggered.connect(lambda: self._end_tree(pids[0]))

        restart = menu.addAction("Restart")
        restart.setEnabled(not many)
        restart.triggered.connect(lambda: self._restart(pids[0]))

        menu.addSeparator()
        menu.addAction("Suspend").triggered.connect(
            lambda: self._simple(pids, suspend_process, "Suspend", True))
        menu.addAction("Resume").triggered.connect(
            lambda: self._simple(pids, resume_process, "Resume", False))

        runas = menu.addAction("Run as administrator")
        runas.setEnabled(not many)
        runas.triggered.connect(lambda: self._run_as(pids[0]))

        priority = menu.addMenu("Set priority")
        for key, label in PRIORITY_LABELS:
            action = priority.addAction(label)
            action.triggered.connect(
                lambda _checked=False, k=key: self._priority(pids, k))

        affinity = menu.addAction("Set affinity…")
        affinity.setEnabled(not many)
        affinity.triggered.connect(lambda: self._affinity(pids[0]))

        menu.addSeparator()
        dump = menu.addAction("Create dump file…")
        dump.setEnabled(not many)
        dump.triggered.connect(lambda: self._dump(pids[0], info))

        location = menu.addAction("Open file location")
        location.setEnabled(not many and bool(_path_of(info)))
        location.triggered.connect(lambda: self._open_location(info))

        search = menu.addAction("Search online")
        search.setEnabled(not many)
        search.triggered.connect(lambda: self._search(info))

        signature = menu.addAction("Verify signature")
        signature.setEnabled(not many and bool(_path_of(info)))
        signature.triggered.connect(lambda: self._signature(info))

        vt = menu.addAction("Check VirusTotal")
        vt.setEnabled(not many and bool(_path_of(info)))
        vt.triggered.connect(lambda: self._virustotal(info))

        copy = menu.addAction("Copy details")
        copy.triggered.connect(lambda: self._copy(info))

        menu.exec(position)

    # ---- the destructive ones -------------------------------------------

    def _end(self, pids: List[int]) -> None:
        if not self._confirm(
                "End task",
                f"End {len(pids)} processes?" if len(pids) > 1
                else f"End process {pids[0]}?",
                "Unsaved work in the process will be lost."):
            return
        self._report("End task",
                     [(pid, end_process(pid)) for pid in pids])

    def _end_tree(self, pid: int) -> None:
        """Counts the tree before asking. "End 23 processes?" is a different
        question from "End task?", and the person deserves the real one."""
        try:
            members = descendants_of(pid)
        except OSError:
            members = []
        total = len(members) + 1
        if not self._confirm(
                "End process tree",
                f"End this process and its {len(members)} "
                f"descendant{'s' if len(members) != 1 else ''} "
                f"({total} in total)?",
                "Unsaved work in any of them will be lost."):
            return
        self._report("End process tree", [(pid, end_process_tree(pid))])

    def _restart(self, pid: int) -> None:
        if not self._confirm(
                "Restart",
                f"Restart process {pid}?",
                "The process will be ended and started again. Unsaved work "
                "in it will be lost."):
            return
        self._report("Restart", [(pid, restart_process(pid))])

    def _run_as(self, pid: int) -> None:
        if not self._confirm(
                "Run as administrator",
                f"Start a new elevated instance of process {pid}?",
                "This process keeps running. Windows will ask to elevate "
                "the new instance."):
            return
        self._report("Run as administrator", [(pid, run_as(pid))])

    def _simple(self, pids, action, title: str, confirm: bool) -> None:
        if confirm and not self._confirm(
                title, f"{title} {len(pids)} process(es)?",
                "A suspended process stops responding until it is resumed."):
            return
        self._report(title, [(pid, action(pid)) for pid in pids])

    def _priority(self, pids, level: str) -> None:
        self._report("Set priority",
                     [(pid, set_priority(pid, level)) for pid in pids])

    def _affinity(self, pid: int) -> None:
        cores = os.cpu_count() or 1
        text, ok = QInputDialog.getText(
            self._widget, "Set affinity",
            f"Cores to allow (0-{cores - 1}, comma separated):",
            text=",".join(str(core) for core in range(cores)))
        if not ok:
            return
        try:
            chosen = [int(part) for part in text.split(",") if part.strip()]
        except ValueError:
            self._show_error("Set affinity: that is not a list of core numbers.")
            return
        self._report("Set affinity", [(pid, set_affinity(pid, chosen))])

    def _dump(self, pid: int, info) -> None:
        name = getattr(info, "name", None) or f"process-{pid}"
        target, _filter = QFileDialog.getSaveFileName(
            self._widget, "Save dump", f"{name}-{pid}.dmp",
            "Dump files (*.dmp)")
        if not target:
            return
        self._report("Create dump", [(pid, create_dump(pid, target))])

    # ---- the harmless ones ----------------------------------------------

    def _open_location(self, info) -> None:
        path = _path_of(info)
        if not path:
            return
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except OSError as error:
            logger.warning("Could not open %s: %s", path, error)

    def _search(self, info) -> None:
        name = getattr(info, "name", "")
        if name:
            webbrowser.open(
                f"https://www.bing.com/search?q={name}+process")

    def _signature(self, info) -> None:
        """The Authenticode verdict on the process's own executable.

        Read on the worker when there is a pool, inline otherwise -- the
        engine caches per path, so a repeat check is free.
        """
        path = _path_of(info)
        if not path:
            return
        if self._app is not None and getattr(self._app, "thread_pool", None):
            from core.worker import Worker
            from core.procengine.signatures import verify_signature

            w = Worker(lambda _worker: verify_signature(path))
            w.signals.result.connect(
                lambda facts: self._show_signature(facts))
            self._app.thread_pool.start(w)
        else:
            from core.procengine.signatures import verify_signature

            self._show_signature(verify_signature(path))

    def _show_signature(self, facts) -> None:
        if self._widget is None or not _widget_valid(self._widget):
            return
        from core.procengine.signatures import (INVALID,
                                            NOT_SIGNED, VALID)

        if facts.status == VALID:
            icon, text = "🟢", f"Valid — signed by {facts.signer or 'unknown'}"
        elif facts.status == NOT_SIGNED:
            icon, text = "🟡", "Not signed"
        elif facts.status == INVALID:
            icon, text = "🔴", f"Invalid — {facts.reason or 'signature does not hold'}"
        else:
            icon, text = "⚪", f"Could not verify — {facts.reason or 'unknown'}"
        QMessageBox.information(
            self._widget, "Signature",
            f"{icon} {facts.path}\n\n{text}")

    def _virustotal(self, info) -> None:
        """Query VirusTotal for the executable's SHA-256.

        Requires the API key configured as `virustotal.api_key`, so it is
        told apart from a check that was merely refused: no key is a
        message, not a silent disable. Runs on a Worker -- hashing a large
        binary and a network call are both off the UI thread.
        """
        path = _path_of(info)
        if not path:
            return
        api_key = ""
        if self._app is not None and getattr(self._app, "config", None) is not None:
            api_key = self._app.config.get("virustotal.api_key", "")
        if not api_key:
            QMessageBox.information(
                self._widget, "VirusTotal",
                "No API key configured. Set 'virustotal.api_key' in settings.")
            return
        from core.virustotal_client import (VTClient,
                                                                compute_sha256)

        sha = compute_sha256(path)
        if not sha:
            self._show_error("VirusTotal: could not compute SHA-256 of the executable.")
            return
        client = VTClient(api_key=api_key)

        def do_check(_worker):
            return client.check(sha)

        if self._app is not None and getattr(self._app, "thread_pool", None):
            from core.worker import Worker

            w = Worker(do_check)
            w.signals.result.connect(self._on_vt_result)
            self._app.thread_pool.start(w)
        else:
            self._on_vt_result(client.check(sha))

    def _on_vt_result(self, result) -> None:
        if self._widget is None or not _widget_valid(self._widget):
            return

        if not result.found:
            QMessageBox.information(
                self._widget, "VirusTotal",
                "This file is unknown to VirusTotal (0 detections "
                "recorded). SHA-256:\n" + result.sha256)
            return
        icon = "🟢" if result.malicious == 0 else (
            "🟠" if result.malicious <= 3 else "🔴")
        QMessageBox.information(
            self._widget, "VirusTotal",
            f"{icon} {result.score}\nSHA-256: {result.sha256}")

    def _copy(self, info) -> None:
        if info is None:
            return
        lines = [f"Name: {info.name}", f"PID: {info.pid}"]
        for label, value in (("Path", info.details.path),
                             ("Command line", info.details.cmdline),
                             ("User", info.details.user)):
            lines.append(f"{label}: {value if value else 'not available'}")
        QApplication.clipboard().setText("\n".join(lines))

    # ---- talking to the person ------------------------------------------

    def _show_error(self, message: str) -> None:
        """Show a plain error via the host tab's ErrorBanner.

        Falls back to a QMessageBox if the host widget does not carry one --
        defensive only, since both current hosts (ProcessesTab, DetailsTab)
        always do.
        """
        banner = getattr(self._widget, "error_banner", None)
        if banner is not None:
            banner.set_error(message)
        else:
            QMessageBox.warning(self._widget, "Error", message)

    def _confirm(self, title: str, question: str, detail: str) -> bool:
        box = QMessageBox(self._widget)
        box.setWindowTitle(title)
        box.setText(question)
        box.setInformativeText(detail)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setStandardButtons(QMessageBox.StandardButton.Yes
                               | QMessageBox.StandardButton.No)
        # Defaults to No: this is the same discipline core/confirm.py applies.
        box.setDefaultButton(QMessageBox.StandardButton.No)
        return box.exec() == QMessageBox.StandardButton.Yes

    def _report(self, title: str, outcomes) -> None:
        """Say what happened. Failures are never swallowed.

        Successes are silent -- the row disappearing is the feedback -- but a
        refusal that says nothing is how someone concludes the button is
        broken.
        """
        failures = [(pid, result) for pid, result in outcomes if not result.ok]
        self.changed.emit()
        if not failures:
            return
        detail = "; ".join(f"PID {pid}: {result.message}"
                           for pid, result in failures)
        if len(failures) == len(outcomes):
            summary = f"{title} failed."
        else:
            summary = (f"{title} failed for {len(failures)} "
                      f"of {len(outcomes)} processes.")
        self._show_error(f"{summary} {detail}")


def _path_of(info):
    return getattr(getattr(info, "details", None), "path", None)

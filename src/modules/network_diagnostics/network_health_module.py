"""Network Health: findings, DNS comparison, ping with jitter. Thin Qt over
`network_health`, `dns_client`, `ping_stats` and `network_fixes`."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6 import sip
from PyQt6.QtGui import QBrush, QColor, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.module_groups import ModuleGroup
from core.semantic_colors import semantic
from core.table_ui import center_header, set_role
from core.worker import Worker
from modules.network_diagnostics import dns_client, network_fixes, network_health, route_table
from modules.network_diagnostics.ping_stats import PingStats
from modules.perfmon.perfmon_charts import _QtLineChart

logger = logging.getLogger(__name__)

PUBLIC_RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
_SEV_COLOUR = {network_health.ERROR: "error", network_health.WARNING: "warning",
               network_health.OK: "success", network_health.INFO: "info", network_health.UNKNOWN: "warning"}
_SEV_LABEL = {network_health.ERROR: "Error", network_health.WARNING: "Warning", network_health.OK: "OK",
              network_health.INFO: "Info", network_health.UNKNOWN: "Unknown"}


def _alive(w: Any) -> bool:
    return w is not None and not sip.isdeleted(w)


def _read_only_table(cols: List[str]) -> QTableWidget:
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    center_header(t)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    return t


class _HealthPane(QWidget):
    """Findings list, detail panel, copy-report and the fix buttons."""

    def __init__(self, module: "NetworkHealthModule") -> None:
        super().__init__()
        self._module = module
        self._findings: List[network_health.Finding] = []
        self._snap: Dict[str, Any] = {}
        self._probes: Dict[str, Any] = {}
        self._running = False
        self._build()

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self._run_btn = QPushButton("Run checks")
        self._run_btn.clicked.connect(self.run_checks)
        self._copy_btn = QPushButton("Copy report")
        self._copy_btn.setToolTip("Copy a plain-text report (findings, adapters, routes, DNS timings) for a ticket")
        self._copy_btn.clicked.connect(self._copy_report)
        self._copy_btn.setEnabled(False)
        self._hide_ok = QCheckBox("Hide passing checks")
        self._hide_ok.toggled.connect(self._fill)
        self._summary = QLabel("Not run yet")
        bar.addWidget(self._run_btn)
        bar.addWidget(self._copy_btn)
        bar.addWidget(self._hide_ok)
        bar.addWidget(self._summary, 1)
        lay.addLayout(bar)

        self._table = _read_only_table(["Result", "Finding"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.itemSelectionChanged.connect(self._show_detail)
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._fix_row = QHBoxLayout()
        self._fix_btns: Dict[str, QPushButton] = {}
        for key, spec in network_fixes.FIXES.items():
            b = QPushButton(spec.label + ("  (admin)" if spec.needs_admin else ""))
            b.clicked.connect(lambda _c=False, k=key: self._do_fix(k))
            self._fix_row.addWidget(b)
            self._fix_btns[key] = b
        self._fix_row.addStretch(1)
        self._fix_status = QLabel("")
        self._fix_status.setWordWrap(True)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self._table)
        low = QWidget()
        low_l = QVBoxLayout(low)
        low_l.setContentsMargins(0, 0, 0, 0)
        low_l.addWidget(self._detail)
        low_l.addLayout(self._fix_row)
        low_l.addWidget(self._fix_status)
        split.addWidget(low)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)

    # -- running ---------------------------------------------------------
    def run_checks(self) -> None:
        if self._running:
            return
        self._running = True
        self._run_btn.setEnabled(False)
        self._summary.setText("Checking...")

        def work(worker):
            snap = network_health.collect_snapshot()
            if worker.is_cancelled:
                return None
            probes = network_health.run_probes(snap, lambda: worker.is_cancelled)
            return snap, probes

        w = Worker(work)
        w.signals.result.connect(lambda r: self._done(r) if _alive(self) else None)
        w.signals.error.connect(lambda e: self._failed(e) if _alive(self) else None)
        w.signals.cancelled.connect(lambda: self._cancelled() if _alive(self) else None)
        self._module.track(w)

    def _cancelled(self) -> None:
        """A cancelled Worker emits neither result nor error: reset here or the button stays dead."""
        self._running = False
        self._run_btn.setEnabled(True)
        self._summary.setText("Cancelled")

    def _failed(self, msg: str) -> None:
        self._running = False
        self._run_btn.setEnabled(True)
        self._summary.setText("Checks failed: " + msg)
        set_role(self._summary, "statusError")
        logger.warning("network health run failed: %s", msg)

    def _done(self, result) -> None:
        self._running = False
        self._run_btn.setEnabled(True)
        if result is None:
            return
        self._snap, self._probes = result
        self._findings = network_health.evaluate(self._snap, self._probes)
        text = network_health.summarize(self._findings)
        self._summary.setText(text)
        bad = any(f.severity == network_health.ERROR for f in self._findings)
        warn = any(f.severity in (network_health.WARNING, network_health.UNKNOWN) for f in self._findings)
        set_role(self._summary, "statusError" if bad else "statusWarning" if warn else "statusSuccess")
        self._copy_btn.setEnabled(True)
        self._fill()

    def _visible(self) -> List[network_health.Finding]:
        hide = self._hide_ok.isChecked()
        return [f for f in self._findings if not (hide and f.severity == network_health.OK)]

    def _fill(self) -> None:
        rows = self._visible()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for r, f in enumerate(rows):
            sev = QTableWidgetItem(_SEV_LABEL[f.severity])
            sev.setForeground(QBrush(QColor(semantic(_SEV_COLOUR[f.severity]))))
            sev.setData(Qt.ItemDataRole.UserRole, r)
            self._table.setItem(r, 0, sev)
            self._table.setItem(r, 1, QTableWidgetItem(f.title))
        if rows:
            self._table.selectRow(0)
        else:
            self._detail.clear()

    def _selected(self) -> Optional[network_health.Finding]:
        idx = self._table.currentRow()
        rows = self._visible()
        return rows[idx] if 0 <= idx < len(rows) else None

    def _show_detail(self) -> None:
        f = self._selected()
        if f is None:
            return
        lines = [f.title, "", f.detail] if f.detail else [f.title]
        lines += ["", *[f"  - {i}" for i in f.items]] if f.items else []
        if f.fix:
            lines += ["", f"Suggested fix: {network_fixes.FIXES[f.fix].label}"]
        self._detail.setPlainText("\n".join(lines))

    # -- report / fixes ---------------------------------------------------
    def _copy_report(self) -> None:
        QGuiApplication.clipboard().setText(network_health.build_report(self._snap, self._probes, self._findings))
        self._fix_status.setText("Report copied to the clipboard.")

    def _do_fix(self, key: str) -> None:
        spec = network_fixes.FIXES[key]
        if not confirm_destructive(self, spec.confirm_title, spec.confirm_text, irreversible=spec.reboot):
            return
        self._fix_status.setText(f"Running: {spec.label}...")
        for b in self._fix_btns.values():
            b.setEnabled(False)
        w = Worker(lambda _w: network_fixes.RUNNERS[key]())
        w.signals.result.connect(lambda r: self._fix_done(spec, r) if _alive(self) else None)
        w.signals.error.connect(lambda e: self._fix_done(spec, network_fixes.FixResult(False, e)) if _alive(self) else None)
        self._module.track(w)

    def _fix_done(self, spec: network_fixes.FixSpec, res: network_fixes.FixResult) -> None:
        for b in self._fix_btns.values():
            b.setEnabled(True)
        tag = {True: "Verified", False: "NOT verified", None: "Unverified"}[res.verified]
        self._fix_status.setText(f"{spec.label}: {'done' if res.ok else 'failed'} ({tag}). {res.message}"
                                 + (" Restart required." if res.reboot_needed else ""))
        if res.reboot_needed:
            QMessageBox.information(self, spec.label, "Restart the computer to finish resetting Winsock.")
        if res.ok and not res.reboot_needed:
            self.run_checks()


class _DnsPane(QWidget):
    """Ask several resolvers the same question and compare."""

    def __init__(self, module: "NetworkHealthModule") -> None:
        super().__init__()
        self._module = module
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self._name = QLineEdit("microsoft.com")
        self._name.returnPressed.connect(self._run)
        self._type = QComboBox()
        self._type.addItems(["A", "AAAA", "MX", "TXT", "NS", "CNAME"])
        self._go = QPushButton("Query")
        self._go.clicked.connect(self._run)
        row.addWidget(QLabel("Name"))
        row.addWidget(self._name, 1)
        row.addWidget(self._type)
        row.addWidget(self._go)
        lay.addLayout(row)
        self._extra = QLineEdit()
        self._extra.setPlaceholderText("Resolvers to ask: system DNS servers are added automatically; add more, comma-separated")
        self._extra.setText(", ".join(PUBLIC_RESOLVERS))
        lay.addWidget(self._extra)
        self._verdict = QLabel("")
        self._verdict.setWordWrap(True)
        lay.addWidget(self._verdict)
        self._table = _read_only_table(["Resolver", "Source", "Time (ms)", "Result", "Answers"])
        lay.addWidget(self._table, 1)
        self._sources: Dict[str, str] = {}

    def _resolvers(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for d in self._module.system_dns():
            out[d["server"]] = f"system ({d['adapter']})"
        for s in self._extra.text().replace(";", ",").split(","):
            s = s.strip()
            if s and s not in out:
                out[s] = "custom"
        return out

    def _run(self) -> None:
        name, qtype = self._name.text().strip(), self._type.currentText()
        if not name:
            return
        self._sources = self._resolvers()
        self._go.setEnabled(False)
        self._verdict.setText("Querying...")
        servers = list(self._sources)

        def work(worker):
            return [dns_client.query(s, name, qtype, 2.5) for s in servers if not worker.is_cancelled]

        w = Worker(work)
        w.signals.result.connect(lambda r: self._show(r) if _alive(self) else None)
        w.signals.error.connect(lambda e: self._verdict.setText("Failed: " + e) if _alive(self) else None)
        w.signals.cancelled.connect(lambda: self._go.setEnabled(True) if _alive(self) else None)
        self._module.track(w)

    def _show(self, replies: List[dns_client.DnsReply]) -> None:
        self._go.setEnabled(True)
        self._verdict.setText(dns_client.compare(replies))
        self._table.setRowCount(len(replies))
        for r, rep in enumerate(replies):
            rtt = "-" if rep.rtt_ms is None else f"{rep.rtt_ms:.0f}"
            result = rep.error or rep.rcode or ""
            answers = "; ".join(rep.answers) if rep.answers else ("(none)" if rep.ok else "")
            if rep.cnames:
                answers = "CNAME " + " > ".join(rep.cnames) + ("  |  " + answers if rep.answers else "")
            if rep.truncated_udp:
                result += " (via TCP)"
            for c, txt in enumerate([rep.server, self._sources.get(rep.server, ""), rtt, result, answers]):
                item = QTableWidgetItem(txt)
                if c == 3 and not rep.ok:
                    item.setForeground(QBrush(QColor(semantic("error"))))
                self._table.setItem(r, c, item)
        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setStretchLastSection(True)


class _PingPane(QWidget):
    """Continuous ping with loss, jitter and an RTT history chart."""

    def __init__(self, module: "NetworkHealthModule") -> None:
        super().__init__()
        self._module = module
        self._stats = PingStats()
        self._in_flight = False
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self._host = QLineEdit("1.1.1.1")
        self._host.setPlaceholderText("Host or IP")
        self._btn = QPushButton("Start")
        self._btn.clicked.connect(self._toggle)
        self._reset = QPushButton("Reset")
        self._reset.clicked.connect(self._clear)
        row.addWidget(QLabel("Target"))
        row.addWidget(self._host, 1)
        row.addWidget(self._btn)
        row.addWidget(self._reset)
        lay.addLayout(row)
        self._chart = _QtLineChart("Round-trip time (ms) - a lost probe plots as 0", "ms", color=semantic("info"))
        lay.addWidget(self._chart, 1)
        self._stat_label = QLabel(self._stats.summary())
        self._stat_label.setWordWrap(True)
        lay.addWidget(self._stat_label)

    def stop(self) -> None:
        self._timer.stop()
        self._btn.setText("Start")

    def _toggle(self) -> None:
        if self._timer.isActive():
            self.stop()
            return
        if not self._host.text().strip():
            return
        self._timer.start()
        self._btn.setText("Stop")
        self._tick()

    def _clear(self) -> None:
        self._stats = PingStats()
        self._chart._data.clear()
        self._chart.update()
        self._stat_label.setText(self._stats.summary())

    def _tick(self) -> None:
        if self._in_flight:
            return
        self._in_flight = True
        host = self._host.text().strip()
        w = Worker(lambda _w: network_health.ping_once(host, 1000))
        w.signals.result.connect(lambda r: self._got(r) if _alive(self) else None)
        w.signals.error.connect(lambda e: self._got({"ok": False, "rtt_ms": None, "error": e}) if _alive(self) else None)
        w.signals.cancelled.connect(lambda: setattr(self, "_in_flight", False) if _alive(self) else None)
        self._module.track(w)

    def _got(self, r: Dict[str, Any]) -> None:
        self._in_flight = False
        if r.get("error"):
            self.stop()
            self._stat_label.setText("Ping could not run: " + str(r["error"]))
            return
        rtt = r["rtt_ms"] if r["ok"] else None
        self._stats.add(rtt)
        # A lost probe is drawn as the previous height would lie; plot 0 and say so in the title.
        self._chart.add_point(rtt if rtt is not None else 0.0)
        # Zero-based axis: auto-scaling turns 1 ms of jitter around 113 ms into a wall-to-wall zigzag.
        self._chart._y_range = (0.0, max(20.0, (self._stats.max or 0.0) * 1.25))
        self._stat_label.setText(self._stats.summary())


class _RoutesPane(QWidget):
    """The full route table and ARP/neighbour cache -- "why is this going out
    the wrong interface" and "is this MAC actually resolved" are both here,
    where the Health tab's findings only look at the default route."""

    def __init__(self, module: "NetworkHealthModule") -> None:
        super().__init__()
        self._module = module
        self._snapshot: Optional[route_table.RouteSnapshot] = None
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Routes and ARP/neighbour cache</b>"))
        self._status = QLabel("")
        set_role(self._status, "muted")
        top.addWidget(self._status, 1)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self._refresh_btn)
        lay.addLayout(top)

        lay.addWidget(QLabel("Routes (default route first, then by metric)"))
        self._routes = _read_only_table(
            ["Destination", "Next hop", "Interface", "Metric", "Protocol", "Store"])
        lay.addWidget(self._routes, 2)

        lay.addWidget(QLabel("Neighbours (ARP / NDP cache) — problems listed first"))
        self._neighbors = _read_only_table(["IP address", "MAC address", "Interface", "State"])
        lay.addWidget(self._neighbors, 2)

    def refresh(self) -> None:
        self._refresh_btn.setEnabled(False)
        self._status.setText("Reading...")
        w = Worker(lambda _w: route_table.read_routes_and_neighbors())
        w.signals.result.connect(lambda r: self._done(r) if _alive(self) else None)
        w.signals.error.connect(lambda e: self._failed(str(e)) if _alive(self) else None)
        self._module.track(w)

    def _failed(self, message: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._status.setText(f"Could not read routes: {message}")

    def _done(self, snapshot: Optional[route_table.RouteSnapshot]) -> None:
        self._refresh_btn.setEnabled(True)
        self._snapshot = snapshot
        if snapshot is None:
            self._status.setText("Windows would not report the route table (PowerShell refused).")
            self._routes.setRowCount(0)
            self._neighbors.setRowCount(0)
            return
        self._fill_routes(snapshot)
        self._fill_neighbors(snapshot)
        problems = route_table.problem_neighbors(snapshot)
        self._status.setText(
            f"{len(snapshot.routes)} route(s), {len(snapshot.neighbors)} neighbour(s)"
            + (f"   —   {len(problems)} unresolved" if problems else ""))

    def _fill_routes(self, snapshot: route_table.RouteSnapshot) -> None:
        self._routes.setRowCount(len(snapshot.routes))
        for r, route in enumerate(snapshot.routes):
            values = (route.destination, route.next_hop or "—", route.interface,
                     str(route.metric), route.protocol, route.store)
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                if route.is_default:
                    item.setForeground(QColor(semantic("info")))
                self._routes.setItem(r, c, item)

    def _fill_neighbors(self, snapshot: route_table.RouteSnapshot) -> None:
        self._neighbors.setRowCount(len(snapshot.neighbors))
        for r, n in enumerate(snapshot.neighbors):
            values = (n.ip, n.mac or "—", n.interface, n.state)
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                if n.is_problem:
                    item.setForeground(QColor(semantic("warning")))
                self._neighbors.setItem(r, c, item)


class NetworkHealthModule(BaseModule):
    name = "Health"
    icon = "🩺"
    description = "Network health findings, DNS comparison and ping with jitter"
    requires_admin = False
    group = ModuleGroup.SYSTEM

    def __init__(self) -> None:
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._health: Optional[_HealthPane] = None
        self._ping: Optional[_PingPane] = None
        self._routes: Optional["_RoutesPane"] = None

    def on_start(self, app) -> None:
        self.app = app

    def track(self, worker: Worker) -> None:
        self._workers.append(worker)
        self.app.thread_pool.start(worker)

    def system_dns(self) -> List[Dict[str, Any]]:
        health = self._health
        snap = health._snap if health is not None else {}
        return network_health.dns_servers_in_use(snap) if snap else []

    def create_widget(self) -> QWidget:
        tabs = QTabWidget()
        self._health = _HealthPane(self)
        self._ping = _PingPane(self)
        self._routes = _RoutesPane(self)
        tabs.addTab(self._health, "Health")
        tabs.addTab(_DnsPane(self), "DNS compare")
        tabs.addTab(self._ping, "Ping and jitter")
        tabs.addTab(self._routes, "Routes")
        self._widget = tabs
        return tabs

    def on_activate(self) -> None:
        h = self._health
        if h is not None and not h._findings:
            h.run_checks()
        if self._routes is not None and self._routes._snapshot is None:
            self._routes.refresh()

    def _stop_activity(self) -> None:
        if self._ping is not None and _alive(self._ping):
            self._ping.stop()
        self.cancel_all_workers()

    def on_deactivate(self) -> None:
        self._stop_activity()

    def on_stop(self) -> None:
        self._stop_activity()

    def get_refresh_interval(self) -> Optional[int]:
        return None

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
from ui.help.network_ui import control_help, explain_tabs, item_help, finding_help
from core.base_module import BaseModule
from core.confirm import confirm_destructive
from core.module_groups import ModuleGroup
from core.semantic_colors import semantic
from core.table_ui import set_role
from core.worker import Worker
from ui.chip_bar import ChipBar
from ui.table_style import apply_table_style
from ui.help.network_overview import add_glance, unreadable
from modules.network_diagnostics import dns_benchmark, dns_cache, dns_client, network_fixes, network_health, port_reader, route_table, socket_analytics
from modules.network_diagnostics.ping_stats import PingStats
from modules.perfmon.perfmon_charts import _QtLineChart

logger = logging.getLogger(__name__)

PUBLIC_RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
_SEV_COLOUR = {network_health.ERROR: "error", network_health.WARNING: "warning",
               network_health.OK: "success", network_health.INFO: "info", network_health.UNKNOWN: "warning"}
_SEV_LABEL = {network_health.ERROR: "⛔ Error", network_health.WARNING: "⚠️ Warning", network_health.OK: "✅ OK",
              network_health.INFO: "ℹ️ Info", network_health.UNKNOWN: "❓ Unknown"}


def _alive(w: Any) -> bool:
    return w is not None and not sip.isdeleted(w)


def _read_only_table(cols: List[str]) -> QTableWidget:
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    numeric = [i for i, c in enumerate(cols) if c in ("Time (ms)", "Metric", "TTL", "Port", "PID")]
    apply_table_style(t, numeric_columns=numeric)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setStretchLastSection(False)
    for c in range(len(cols)):
        t.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
        t.setColumnWidth(c, 160)
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
        self._glance = add_glance(lay, [
            ('errors', 'Errors', 'danger', 'Failed health checks. Select to show errors.'),
            ('warnings', 'Warnings', 'warning', 'Health warnings. Select to show warnings.'),
            ('gateway', 'Gateway latency', 'info', 'Fastest measured reply from a default IPv4 gateway. ICMP may be blocked.'),
            ('dns', 'DNS resolving', 'ok', 'Configured resolvers that returned an address, out of all completed DNS probes.'),
        ])
        self._chips = ChipBar([(k, label, 0) for k, label in
                               [('all', 'All'), ('errors', 'Errors'), ('warnings', 'Warnings'), ('unknown', 'Unknown')]])
        self._chips.selection_changed.connect(lambda _: self._fill())
        for key in ('errors', 'warnings'):
            self._glance[key].setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self._glance[key].setCursor(Qt.CursorShape.PointingHandCursor)
            self._glance[key].clicked.connect(lambda k=key: self._chips.chips[k].setChecked(True))
        lay.addWidget(self._chips)
        bar = QHBoxLayout()
        self._run_btn = QPushButton("Run checks")
        self._run_btn.setObjectName("accentButton")
        self._run_btn.clicked.connect(self.run_checks)
        self._copy_btn = QPushButton("Copy report")
        self._copy_btn.setToolTip("Copy a plain-text report (findings, adapters, routes, DNS timings) for a ticket")
        self._copy_btn.clicked.connect(self._copy_report)
        self._copy_btn.setEnabled(False)
        self._hide_ok = QCheckBox("Hide passing checks")
        self._hide_ok.toggled.connect(self._fill)
        self._search = QLineEdit()
        control_help(self._search, 'self._search')
        self._search.setPlaceholderText("Filter findings…")
        self._search.textChanged.connect(lambda _: self._fill())
        self._summary = QLabel("Not run yet")
        bar.addWidget(self._run_btn)
        bar.addWidget(self._copy_btn)
        bar.addWidget(self._hide_ok)
        bar.addWidget(self._search)
        bar.addWidget(self._summary, 1)
        lay.addLayout(bar)

        self._table = _read_only_table(["Result", "Finding"])
        self._table.setColumnWidth(0, 120)
        self._table.setColumnWidth(1, 380)
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
        unreadable(self._glance, "Checks are running")

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
        unreadable(self._glance, "Checks were cancelled; run checks again")

    def _failed(self, msg: str) -> None:
        self._running = False
        self._run_btn.setEnabled(True)
        self._summary.setText("Checks failed: " + msg)
        set_role(self._summary, "statusError")
        logger.warning("network health run failed: %s", msg)
        unreadable(self._glance, str(msg))

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
        self._update_glance()
        self._fill()

    def _update_glance(self) -> None:
        counts = {key: sum(f.severity == severity for f in self._findings)
                  for key, severity in [('errors', network_health.ERROR),
                                        ('warnings', network_health.WARNING), ('unknown', network_health.UNKNOWN)]}
        self._chips.set_counts({'all': len(self._findings), **counts})
        for key in ('errors', 'warnings'):
            self._glance[key].set(counts[key], 'checks need attention' if counts[key] else 'No findings',
                                  severity=('danger' if key == 'errors' else 'warning') if counts[key] else 'ok')
        gateways = [p for k, p in self._probes.items() if k.startswith('gw:')]
        times = [p['rtt_ms'] for p in gateways if p.get('ok') and p.get('rtt_ms') is not None]
        self._glance['gateway'].set(f'{min(times):.1f} ms' if times else None,
                                   'Fastest gateway reply', reason='No gateway replied or the probe was unavailable')
        replies = [p for k, p in self._probes.items() if k.startswith('dns:')]
        good = sum(p.ok and p.resolved for p in replies)
        self._glance['dns'].set(f'{good} / {len(replies)}' if replies else None,
                               'Resolvers answered' if good else 'No resolver answered',
                               reason='No DNS probe result was available',
                               severity='ok' if good == len(replies) else 'warning' if good else 'danger')

    def _visible(self) -> List[network_health.Finding]:
        hide = self._hide_ok.isChecked()
        selected = self._chips.selected()
        severity = {'errors': network_health.ERROR, 'warnings': network_health.WARNING,
                    'unknown': network_health.UNKNOWN}.get(selected[0] if selected else 'all')
        query = self._search.text().strip().lower() if hasattr(self, "_search") else ""
        out = []
        for f in self._findings:
            if hide and f.severity == network_health.OK:
                continue
            if severity is not None and f.severity != severity:
                continue
            if query:
                haystack = f"{f.title} {f.detail} {' '.join(f.items)}".lower()
                if query not in haystack:
                    continue
            out.append(f)
        return out

    def _fill(self) -> None:
        rows = self._visible()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for r, f in enumerate(rows):
            sev = QTableWidgetItem(_SEV_LABEL[f.severity])
            sev.setToolTip(finding_help(f))
            sev.setForeground(QBrush(QColor(semantic(_SEV_COLOUR[f.severity]))))
            sev.setData(Qt.ItemDataRole.UserRole, r)
            self._table.setItem(r, 0, item_help(sev, self._table, 0))
            title = QTableWidgetItem(f.title)
            title.setToolTip(finding_help(f))
            self._table.setItem(r, 1, title)
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
        control_help(self._name, 'self._name')
        self._name.returnPressed.connect(self._run)
        self._type = QComboBox()
        control_help(self._type, 'self._type')
        self._type.addItems(["A", "AAAA", "MX", "TXT", "NS", "CNAME"])
        self._go = QPushButton("Query")
        self._go.clicked.connect(self._run)
        row.addWidget(QLabel("Name"))
        row.addWidget(self._name, 1)
        row.addWidget(self._type)
        row.addWidget(self._go)
        lay.addLayout(row)
        self._extra = QLineEdit()
        control_help(self._extra, 'self._extra')
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
                self._table.setItem(r, c, item_help(item, self._table, c))
        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setStretchLastSection(False)


class _DnsBenchmarkPane(QWidget):
    """Benchmark DNS resolvers against top domains to measure latency and packet loss."""

    def __init__(self, module: "NetworkHealthModule") -> None:
        super().__init__()
        self._module = module
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>DNS Resolver Latency Benchmark</b>"))
        self._status = QLabel("Click 'Run Benchmark' to test latency across public and system DNS resolvers.")
        set_role(self._status, "muted")
        top.addWidget(self._status, 1)

        self._flush_btn = QPushButton("Flush DNS Cache")
        self._flush_btn.clicked.connect(self._flush_dns)
        top.addWidget(self._flush_btn)

        self._run_btn = QPushButton("Run Benchmark")
        self._run_btn.clicked.connect(self.run_benchmark)
        top.addWidget(self._run_btn)
        lay.addLayout(top)

        self._table = _read_only_table(["Rank", "Resolver", "Server IP", "Avg Latency", "Min / Max", "Reliability", "Sample Response"])
        self._table.setColumnWidth(0, 100)
        self._table.setColumnWidth(1, 190)
        self._table.setColumnWidth(2, 120)
        self._table.setColumnWidth(3, 110)
        self._table.setColumnWidth(4, 120)
        self._table.setColumnWidth(5, 110)
        self._table.setColumnWidth(6, 220)
        lay.addWidget(self._table, 1)

    def run_benchmark(self) -> None:
        self._run_btn.setEnabled(False)
        self._status.setText("Benchmarking DNS resolvers (5 queries each)...")

        def work(worker: Worker):
            return dns_benchmark.run_dns_benchmark(is_cancelled=lambda: worker.is_cancelled)

        w = Worker(work)
        w.signals.result.connect(lambda res: self._on_done(res) if _alive(self) else None)
        w.signals.error.connect(lambda err: self._on_error(str(err)) if _alive(self) else None)
        self._module.track(w)

    def _on_done(self, results: List[dns_benchmark.BenchmarkResult]) -> None:
        self._run_btn.setEnabled(True)
        if not results:
            self._status.setText("No benchmark results returned.")
            return

        fastest = results[0]
        if fastest.avg_rtt_ms is not None:
            self._status.setText(f"Fastest resolver: {fastest.name} ({fastest.server}) at {fastest.avg_rtt_ms:.1f} ms average latency.")
            set_role(self._status, "statusSuccess")
        else:
            self._status.setText("All DNS benchmark queries timed out or failed.")
            set_role(self._status, "statusWarning")

        self._table.setRowCount(len(results))
        for r, res in enumerate(results):
            rank_str = "🏆 Fastest" if res.is_fastest else f"#{r + 1}"
            avg_str = f"{res.avg_rtt_ms:.1f} ms" if res.avg_rtt_ms is not None else "Failed"
            min_max_str = f"{res.min_rtt_ms:.1f} / {res.max_rtt_ms:.1f} ms" if res.min_rtt_ms is not None else "—"
            rel_str = f"{res.success_rate_pct:.0f}% ({res.samples_ok}/{res.samples_sent})"
            sample_str = res.sample_answer or res.error_summary or "—"

            vals = [rank_str, res.name, res.server, avg_str, min_max_str, rel_str, sample_str]
            for c, txt in enumerate(vals):
                item = QTableWidgetItem(txt)
                if res.is_fastest and c in (0, 3):
                    item.setForeground(QColor(semantic("success")))
                elif res.avg_rtt_ms is None:
                    item.setForeground(QColor(semantic("error")))
                self._table.setItem(r, c, item)

    def _on_error(self, err: str) -> None:
        self._run_btn.setEnabled(True)
        self._status.setText(f"DNS benchmark error: {err}")
        set_role(self._status, "statusError")

    def _flush_dns(self) -> None:
        try:
            import subprocess
            subprocess.run(["ipconfig.exe", "/flushdns"], capture_output=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self._status.setText("DNS resolver cache successfully flushed.")
            set_role(self._status, "statusSuccess")
        except Exception as e:
            self._status.setText(f"Failed to flush DNS cache: {e}")


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
        control_help(self._host, 'self._host')
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
                self._routes.setItem(r, c, item_help(item, self._routes, c))
    def _fill_neighbors(self, snapshot: route_table.RouteSnapshot) -> None:
        self._neighbors.setRowCount(len(snapshot.neighbors))
        for r, n in enumerate(snapshot.neighbors):
            values = (n.ip, n.mac or "—", n.interface, n.state)
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                if n.is_problem:
                    item.setForeground(QColor(semantic("warning")))
                self._neighbors.setItem(r, c, item_help(item, self._neighbors, c))

class _DnsCachePane(QWidget):
    """Every record `ipconfig /displaydns` currently holds -- not the
    resolver comparison DNS compare runs live, this is what is ALREADY
    cached, which is what actually answers the next lookup."""

    def __init__(self, module: "NetworkHealthModule") -> None:
        super().__init__()
        self._module = module
        self._entries: List[dns_cache.DnsCacheEntry] = []
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>DNS resolver cache</b>"))
        self._status = QLabel("")
        set_role(self._status, "muted")
        top.addWidget(self._status, 1)
        self._filter = QLineEdit()
        control_help(self._filter, 'self._filter')
        self._filter.setPlaceholderText("Filter by name...")
        self._filter.textChanged.connect(self._apply_filter)
        top.addWidget(self._filter)
        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self._refresh_btn)
        self._flush_btn = QPushButton("Flush Cache")
        self._flush_btn.clicked.connect(self._flush)
        top.addWidget(self._flush_btn)
        lay.addLayout(top)
        self._table = _read_only_table(["Name", "Type", "TTL", "Section", "Data"])
        lay.addWidget(self._table, 1)

    def refresh(self) -> None:
        self._set_buttons_enabled(False)
        self._status.setText("Reading...")
        w = Worker(lambda _w: dns_cache.read_dns_cache())
        w.signals.result.connect(lambda r: self._done(r) if _alive(self) else None)
        w.signals.error.connect(lambda e: self._failed(str(e)) if _alive(self) else None)
        self._module.track(w)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self._refresh_btn.setEnabled(enabled)
        self._flush_btn.setEnabled(enabled)

    def _failed(self, message: str) -> None:
        self._set_buttons_enabled(True)
        self._status.setText(f"Could not read the DNS cache: {message}")

    def _done(self, entries: Optional[List[dns_cache.DnsCacheEntry]]) -> None:
        self._set_buttons_enabled(True)
        if entries is None:
            self._entries = []
            self._table.setRowCount(0)
            from core.admin_utils import is_admin
            if is_admin():
                self._status.setText("Windows would not report the DNS cache (ipconfig refused).")
            else:
                # Current Windows builds refuse `ipconfig /displaydns` without
                # elevation ("The requested operation requires elevation."),
                # and Get-DnsClientCache answers an EMPTY cache instead of an
                # error -- so this is the one honest reader, and the reason is
                # worth saying rather than a bare "refused".
                self._status.setText("Reading the DNS cache needs administrator rights on "
                                     "this version of Windows. Run the app as administrator.")
            return
        self._entries = entries
        self._status.setText(f"{len(entries)} cached record(s).")
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self._filter.text().strip().lower()
        rows = [e for e in self._entries if needle in e.name.lower()] if needle else self._entries
        self._table.setRowCount(len(rows))
        for r, e in enumerate(rows):
            values = (e.name, e.type_name, str(e.ttl) if e.ttl is not None else "—", e.section, e.data)
            for c, text in enumerate(values):
                self._table.setItem(r, c, item_help(QTableWidgetItem(text), self._table, c))
        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setStretchLastSection(False)

    def _flush(self) -> None:
        self._set_buttons_enabled(False)
        self._status.setText("Flushing...")
        w = Worker(lambda _w: network_fixes.flush_dns())
        w.signals.result.connect(lambda r: self._flushed(r) if _alive(self) else None)
        w.signals.error.connect(lambda e: self._failed(str(e)) if _alive(self) else None)
        self._module.track(w)

    def _flushed(self, result: network_fixes.FixResult) -> None:
        self._status.setText(result.message)
        self.refresh()


class _PortsPane(QWidget):
    """Listening TCP sockets and bound UDP endpoints across all processes."""

    def __init__(self, module: "NetworkHealthModule") -> None:
        super().__init__()
        self._module = module
        self._entries: List[port_reader.ListeningPort] = []
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Listening ports & bound sockets</b>"))
        self._status = QLabel("")
        set_role(self._status, "muted")
        top.addWidget(self._status, 1)

        self._filter = QLineEdit()
        control_help(self._filter, 'self._filter')
        self._filter.setPlaceholderText("Filter port, process, service...")
        self._filter.textChanged.connect(self._apply_filter)
        top.addWidget(self._filter)

        self._copy_btn = QPushButton("Copy list")
        self._copy_btn.setToolTip("Copy the filtered listening ports list to clipboard")
        self._copy_btn.clicked.connect(self._copy_list)
        top.addWidget(self._copy_btn)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self._refresh_btn)
        lay.addLayout(top)

        self._chips = ChipBar([
            ("all", "All", 0),
            ("public", "Public (0.0.0.0 / ::)", 0),
            ("loopback", "Loopback", 0),
            ("third_party", "Third-party", 0),
        ])
        self._chips.selection_changed.connect(lambda _: self._apply_filter())
        lay.addWidget(self._chips)

        cols = ["Proto", "Port", "Scope", "Service", "Process", "PID", "User", "Address"]
        self._table = _read_only_table(cols)
        lay.addWidget(self._table, 1)

    def refresh(self) -> None:
        self._refresh_btn.setEnabled(False)
        self._status.setText("Reading listening ports...")
        w = Worker(lambda _w: port_reader.read_listening_ports())
        w.signals.result.connect(lambda r: self._done(r) if _alive(self) else None)
        w.signals.error.connect(lambda e: self._failed(str(e)) if _alive(self) else None)
        self._module.track(w)

    def _failed(self, message: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._status.setText(f"Could not read listening ports: {message}")

    def _done(self, result: Tuple[List[port_reader.ListeningPort], List[str]]) -> None:
        self._refresh_btn.setEnabled(True)
        ports, problems = result
        self._entries = ports

        pub_count = sum(1 for p in ports if p.is_public)
        loop_count = sum(1 for p in ports if p.scope == "Loopback")
        third_count = sum(1 for p in ports if not p.is_system)

        self._chips.set_counts({
            "all": len(ports),
            "public": pub_count,
            "loopback": loop_count,
            "third_party": third_count,
        })

        prob_msg = f" ({'; '.join(problems)})" if problems else ""
        self._status.setText(
            f"{len(ports)} listening port(s) — {pub_count} public, {third_count} third-party{prob_msg}"
        )
        self._apply_filter()

    def _filtered_entries(self) -> List[port_reader.ListeningPort]:
        sel = getattr(self._chips, "selected_key", None) or (self._chips.selected()[0] if self._chips.selected() else "all")
        needle = self._filter.text().strip().lower()

        entries = self._entries
        if sel == "public":
            entries = [p for p in entries if p.is_public]
        elif sel == "loopback":
            entries = [p for p in entries if p.scope == "Loopback"]
        elif sel == "third_party":
            entries = [p for p in entries if not p.is_system]

        if needle:
            entries = [
                p for p in entries
                if needle in p.process_name.lower()
                or needle in str(p.port)
                or needle in p.service_name.lower()
                or needle in p.proto.lower()
                or needle in p.scope.lower()
                or needle in p.username.lower()
                or needle in (p.process_path or "").lower()
            ]
        return entries

    def _apply_filter(self) -> None:
        rows = self._filtered_entries()
        self._table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            values = [
                p.proto,
                str(p.port),
                p.scope,
                p.service_name or "—",
                p.process_name,
                str(p.pid) if p.pid is not None else "—",
                p.username or "—",
                f"{p.ip}:{p.port}",
            ]
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                if c == 2 and p.is_public:
                    item.setForeground(QColor(semantic("warning")))
                if c == 4 and p.process_path:
                    item.setToolTip(f"{p.process_name}\nPath: {p.process_path}\nUser: {p.username or 'unknown'}")
                self._table.setItem(r, c, item_help(item, self._table, c))
        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setStretchLastSection(False)

    def _copy_list(self) -> None:
        rows = self._filtered_entries()
        lines = ["Proto\tPort\tScope\tService\tProcess\tPID\tUser\tEndpoint"]
        for p in rows:
            lines.append(f"{p.proto}\t{p.port}\t{p.scope}\t{p.service_name}\t{p.process_name}\t{p.pid or ''}\t{p.username}\t{p.endpoint}")
        QGuiApplication.clipboard().setText("\n".join(lines))
        self._status.setText(f"Copied {len(rows)} port(s) to clipboard.")


class _SocketsPane(QWidget):
    """Active TCP sockets, connection state distribution & ephemeral port exhaustion analytics."""

    def __init__(self, module: "NetworkHealthModule") -> None:
        super().__init__()
        self._module = module
        self._snapshot: Optional[socket_analytics.SocketAnalyticsSnapshot] = None
        self._entries: List[socket_analytics.SocketConnection] = []
        lay = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Active TCP sockets & connection analytics</b>"))
        self._status = QLabel("")
        set_role(self._status, "muted")
        top.addWidget(self._status, 1)

        self._filter = QLineEdit()
        control_help(self._filter, 'self._filter')
        self._filter.setPlaceholderText("Filter process, PID, IP, port, service...")
        self._filter.textChanged.connect(self._apply_filter)
        top.addWidget(self._filter)

        self._copy_btn = QPushButton("Copy list")
        self._copy_btn.setToolTip("Copy the filtered active connections list to clipboard")
        self._copy_btn.clicked.connect(self._copy_list)
        top.addWidget(self._copy_btn)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self._refresh_btn)
        lay.addLayout(top)

        # Alert banner for socket leaks or dynamic port exhaustion
        self._alert_banner = QLabel("")
        self._alert_banner.setWordWrap(True)
        self._alert_banner.setVisible(False)
        set_role(self._alert_banner, "statusWarning")
        lay.addWidget(self._alert_banner)

        self._chips = ChipBar([
            ("all", "All", 0),
            ("close_wait", "CLOSE_WAIT (Leaked)", 0),
            ("established", "ESTABLISHED", 0),
            ("time_wait", "TIME_WAIT", 0),
            ("syn_sent", "SYN_SENT", 0),
            ("listen", "LISTEN", 0),
        ])
        self._chips.selection_changed.connect(lambda _: self._apply_filter())
        lay.addWidget(self._chips)

        cols = ["Process", "PID", "State", "Local Endpoint", "Foreign Endpoint", "Service", "Dynamic Port?", "Path"]
        self._table = _read_only_table(cols)
        lay.addWidget(self._table, 1)

    def refresh(self) -> None:
        self._refresh_btn.setEnabled(False)
        self._status.setText("Reading active sockets and dynamic port limits...")
        w = Worker(lambda _w: socket_analytics.read_socket_analytics())
        w.signals.result.connect(lambda r: self._done(r) if _alive(self) else None)
        w.signals.error.connect(lambda e: self._failed(str(e)) if _alive(self) else None)
        self._module.track(w)

    def _failed(self, message: str) -> None:
        self._refresh_btn.setEnabled(True)
        self._status.setText(f"Could not read active sockets: {message}")

    def _done(self, snapshot: socket_analytics.SocketAnalyticsSnapshot) -> None:
        self._refresh_btn.setEnabled(True)
        self._snapshot = snapshot
        self._entries = snapshot.connections

        sc = snapshot.state_counts
        self._chips.set_counts({
            "all": len(snapshot.connections),
            "close_wait": sc.get("CLOSE_WAIT", 0),
            "established": sc.get("ESTABLISHED", 0),
            "time_wait": sc.get("TIME_WAIT", 0),
            "syn_sent": sc.get("SYN_SENT", 0),
            "listen": sc.get("LISTEN", 0),
        })

        ephem = snapshot.ephemeral
        status_txt = (
            f"{len(snapshot.connections)} sockets | Dynamic ports: {ephem.in_use}/{ephem.num_ports} "
            f"({ephem.percent_used:.1f}%) [{ephem.status}] | "
            f"ESTABLISHED: {sc.get('ESTABLISHED', 0)}, TIME_WAIT: {sc.get('TIME_WAIT', 0)}, CLOSE_WAIT: {sc.get('CLOSE_WAIT', 0)}"
        )
        self._status.setText(status_txt)

        if snapshot.alerts:
            self._alert_banner.setText("⚠️ " + "\n• ".join(snapshot.alerts[:3]))
            self._alert_banner.setVisible(True)
        else:
            self._alert_banner.setVisible(False)

        self._apply_filter()

    def _filtered_entries(self) -> List[socket_analytics.SocketConnection]:
        sel = getattr(self._chips, "selected_key", None) or (self._chips.selected()[0] if self._chips.selected() else "all")
        needle = self._filter.text().strip().lower()

        entries = self._entries
        if sel == "close_wait":
            entries = [c for c in entries if c.state == "CLOSE_WAIT"]
        elif sel == "established":
            entries = [c for c in entries if c.state == "ESTABLISHED"]
        elif sel == "time_wait":
            entries = [c for c in entries if c.state == "TIME_WAIT"]
        elif sel == "syn_sent":
            entries = [c for c in entries if c.state == "SYN_SENT"]
        elif sel == "listen":
            entries = [c for c in entries if c.state == "LISTEN"]

        if needle:
            entries = [
                c for c in entries
                if needle in c.process_name.lower()
                or needle in str(c.pid or "")
                or needle in str(c.local_port)
                or needle in str(c.foreign_port)
                or needle in c.local_endpoint.lower()
                or needle in c.foreign_endpoint.lower()
                or needle in c.service_name.lower()
                or needle in c.state.lower()
                or needle in (c.process_path or "").lower()
            ]
        return entries

    def _apply_filter(self) -> None:
        rows = self._filtered_entries()
        self._table.setRowCount(len(rows))
        for r, c in enumerate(rows):
            values = [
                c.process_name,
                str(c.pid) if c.pid is not None else "—",
                c.state,
                c.local_endpoint,
                c.foreign_endpoint,
                c.service_name or "—",
                "Yes" if c.is_ephemeral else "No",
                c.process_path or "—",
            ]
            for col_idx, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col_idx == 2:
                    if c.is_leak_suspect:
                        item.setForeground(QColor(semantic("warning")))
                    elif c.state == "ESTABLISHED":
                        item.setForeground(QColor(semantic("success")))
                if col_idx == 0 and c.process_path:
                    item.setToolTip(f"{c.process_name}\nPID: {c.pid}\nPath: {c.process_path}\nUser: {c.username or 'unknown'}")
                self._table.setItem(r, col_idx, item_help(item, self._table, col_idx))
        self._table.resizeColumnsToContents()
        self._table.horizontalHeader().setStretchLastSection(False)

    def _copy_list(self) -> None:
        rows = self._filtered_entries()
        lines = ["Process\tPID\tState\tLocal Endpoint\tForeign Endpoint\tService\tEphemeral\tPath"]
        for c in rows:
            lines.append(f"{c.process_name}\t{c.pid or ''}\t{c.state}\t{c.local_endpoint}\t{c.foreign_endpoint}\t{c.service_name}\t{c.is_ephemeral}\t{c.process_path}")
        QGuiApplication.clipboard().setText("\n".join(lines))
        self._status.setText(f"Copied {len(rows)} socket(s) to clipboard.")


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
        self._ports: Optional[_PortsPane] = None
        self._sockets: Optional[_SocketsPane] = None
        self._ping: Optional[_PingPane] = None
        self._routes: Optional["_RoutesPane"] = None
        self._dns_cache: Optional["_DnsCachePane"] = None

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
        self._ports = _PortsPane(self)
        self._sockets = _SocketsPane(self)
        self._ping = _PingPane(self)
        self._routes = _RoutesPane(self)
        self._dns_cache = _DnsCachePane(self)
        tabs.addTab(self._health, "Health")
        tabs.addTab(self._ports, "Listening ports")
        tabs.addTab(self._sockets, "Sockets & connections")
        tabs.addTab(_DnsPane(self), "DNS compare")
        tabs.addTab(_DnsBenchmarkPane(self), "DNS benchmark")
        tabs.addTab(self._dns_cache, "DNS cache")
        tabs.addTab(self._ping, "Ping and jitter")
        tabs.addTab(self._routes, "Routes")
        self._widget = tabs
        return explain_tabs(tabs, "Health")

    def on_activate(self) -> None:
        h = self._health
        if h is not None and not h._findings:
            h.run_checks()
        if self._ports is not None and not self._ports._entries:
            self._ports.refresh()
        if self._sockets is not None and not self._sockets._entries:
            self._sockets.refresh()
        if self._routes is not None and self._routes._snapshot is None:
            self._routes.refresh()
        if self._dns_cache is not None and not self._dns_cache._entries:
            self._dns_cache.refresh()

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

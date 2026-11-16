"""Process Explorer's System Information window.

Five tabs -- Summary, CPU, Memory, I/O and GPU -- over the engine that the
Performance tab already reads. Almost nothing here is a new measurement:
`cpuinfo` has the processor times, `meminfo` the commit and pool figures,
`sysinfo` the context switches, page faults and system-wide I/O, and
`gpuinfo` the adapters. This window is the arrangement Process Explorer
puts them in, which is a different question from Task Manager's and worth
having beside it: Task Manager asks "what is using this machine", and this
asks "what is this machine doing".

A dialog rather than a sixth Performance panel because that is what it is
in Process Explorer -- something you open next to the process list and read
while the list keeps running.

Every graph is `PerfGraph`, so the int-coordinate rule in `perf_graph.py`
applies to everything drawn here.
"""
import logging
from typing import Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QDialog, QFormLayout, QGridLayout, QHBoxLayout,
                             QLabel, QTabWidget, QVBoxLayout, QWidget)

from core.semantic_colors import semantic
from core.table_ui import set_role

from ui.perf_graph import CoreGrid, PerfGraph
from core.procengine.columns import fmt_bytes
from core.procengine.cpuinfo import core_loads, cpu_static, processor_times, \
    uptime_seconds
from core.procengine.gpuinfo import GpuSampler, adapter_facts
from core.procengine.meminfo import memory_status
from core.procengine.sysinfo import page_size, system_counters, system_rates

logger = logging.getLogger(__name__)

REFRESH_MS = 1000


class SystemInformationDialog(QDialog):
    """The System Information window. Owns its timer and its PDH query."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("System Information")
        self.resize(760, 720)
        self._workers: list = []
        self._previous_cores = None
        self._previous_counters = None
        self._gpu = None
        self._gpu_facts = {}
        self._gpu_rows = {}
        self._page = page_size()
        self._values = {}
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)

    # ---- construction ---------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        self._build_summary()
        self._build_cpu()
        self._build_memory()
        self._build_io()
        self._build_gpu()

    def _build_summary(self) -> None:
        """The four graphs Process Explorer opens on, side by side."""
        page = QWidget(self)
        grid = QGridLayout(page)
        self.summary_graphs = {}
        for index, (name, colour, ceiling) in enumerate((
                ("CPU usage", "info", 100.0),
                ("Commit charge", "warning", 100.0),
                ("Physical memory", "success", 100.0),
                ("I/O bytes", "match", 1024.0))):
            caption = QLabel(name, page)
            set_role(caption, "heading")
            graph = PerfGraph(semantic(colour), ceiling, page)
            graph.setMinimumHeight(110)
            reading = QLabel("—", page)
            set_role(reading, "muted")
            row, column = divmod(index, 2)
            cell = QVBoxLayout()
            cell.addWidget(caption)
            cell.addWidget(graph)
            cell.addWidget(reading)
            holder = QWidget(page)
            holder.setLayout(cell)
            grid.addWidget(holder, row, column)
            self.summary_graphs[name] = (graph, reading)
        self.tabs.addTab(page, "Summary")

    def _build_cpu(self) -> None:
        page = QWidget(self)
        column = QVBoxLayout(page)

        static = cpu_static()
        title = QLabel(static.name or "Processor", page)
        set_role(title, "heading")
        column.addWidget(title)

        self.cpu_graph = PerfGraph(semantic("info"), 100.0, page)
        self.cpu_graph.setMinimumHeight(130)
        column.addWidget(self.cpu_graph)

        self.cpu_cores = CoreGrid(page)
        column.addWidget(self.cpu_cores, 1)

        column.addLayout(self._form(page, "cpu", (
            "Processes", "Threads", "Handles", "Up time",
            "Context switches", "System calls")))
        column.addStretch(0)
        self.tabs.addTab(page, "CPU")

    def _build_memory(self) -> None:
        page = QWidget(self)
        column = QVBoxLayout(page)

        commit = QLabel("Commit charge", page)
        set_role(commit, "heading")
        column.addWidget(commit)
        self.commit_graph = PerfGraph(semantic("warning"), 100.0, page)
        self.commit_graph.setMinimumHeight(110)
        column.addWidget(self.commit_graph)

        physical = QLabel("Physical memory", page)
        set_role(physical, "heading")
        column.addWidget(physical)
        self.physical_graph = PerfGraph(semantic("success"), 100.0, page)
        self.physical_graph.setMinimumHeight(110)
        column.addWidget(self.physical_graph)

        pair = QHBoxLayout()
        pair.addLayout(self._form(page, "commit", (
            "Current", "Limit", "Peak", "Total", "Available", "Cached")))
        pair.addLayout(self._form(page, "kernel", (
            "Paged pool", "Non-paged pool", "Paged allocations",
            "Non-paged allocations", "Free system PTEs", "Page faults/s")))
        column.addLayout(pair)
        column.addStretch(0)
        self.tabs.addTab(page, "Memory")

    def _build_io(self) -> None:
        page = QWidget(self)
        column = QVBoxLayout(page)
        title = QLabel("System I/O", page)
        set_role(title, "heading")
        column.addWidget(title)

        self.io_graph = PerfGraph(semantic("match"), 1024.0, page)
        self.io_graph.setMinimumHeight(150)
        column.addWidget(self.io_graph)

        column.addLayout(self._form(page, "io", (
            "Reads", "Read bytes", "Writes", "Write bytes",
            "Other", "Other bytes", "Page reads/s")))
        column.addStretch(0)
        self.tabs.addTab(page, "I/O")

    def _build_gpu(self) -> None:
        page = QWidget(self)
        column = QVBoxLayout(page)
        self.gpu_area = QVBoxLayout()
        column.addLayout(self.gpu_area, 1)
        column.addStretch(0)
        self.tabs.addTab(page, "GPU")

    def _form(self, parent, prefix: str, names) -> QFormLayout:
        """A labelled column of readings, each starting at a dash."""
        form = QFormLayout()
        for name in names:
            caption = QLabel(name, parent)
            set_role(caption, "muted")
            value = QLabel("—", parent)
            self._values[f"{prefix}.{name}"] = value
            form.addRow(caption, value)
        return form

    def _set(self, key: str, text: str) -> None:
        label = self._values.get(key)
        if label is not None:
            label.setText(text)

    # ---- lifecycle ------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh()
        self._timer.start(REFRESH_MS)

    def closeEvent(self, event) -> None:
        self.stop()
        super().closeEvent(event)

    def stop(self) -> None:
        self._timer.stop()
        if self._gpu is not None:
            self._gpu.close()
            self._gpu = None
        self.cancel_all()

    def cancel_all(self) -> None:
        for worker in self._workers:
            worker.cancel()
        self._workers.clear()

    # ---- the reading ----------------------------------------------------

    def refresh(self) -> None:
        try:
            self._refresh_cpu()
            self._refresh_memory()
            self._refresh_io()
            self._refresh_gpu()
        except OSError as error:
            # One bad reading must not stop the timer, or the window goes
            # permanently dead after a hiccup.
            logger.warning("System Information refresh failed: %s", error)

    def _refresh_cpu(self) -> None:
        current = processor_times()
        load = None
        if self._previous_cores is not None:
            loads = core_loads(self._previous_cores, current)
            if loads:
                load = sum(entry.total for entry in loads) / len(loads)
                self.cpu_cores.push([entry.total for entry in loads])
        self._previous_cores = current

        self.cpu_graph.push(load)
        self._push_summary("CPU usage", load, _percent(load))
        self._set("cpu.Up time", _uptime(uptime_seconds()))

    def _refresh_memory(self) -> None:
        status = memory_status()
        self._set("cpu.Processes", f"{status.processes:,}")
        self._set("cpu.Threads", f"{status.threads:,}")
        self._set("cpu.Handles", f"{status.handles:,}")

        commit_percent = (status.committed / status.commit_limit * 100.0
                          if status.commit_limit else None)
        self.commit_graph.push(commit_percent)
        self._push_summary(
            "Commit charge", commit_percent,
            f"{fmt_bytes(status.committed)} of "
            f"{fmt_bytes(status.commit_limit)}")

        self.physical_graph.push(status.used_percent)
        self._push_summary(
            "Physical memory", status.used_percent,
            f"{fmt_bytes(status.in_use)} of {fmt_bytes(status.total)}")

        self._set("commit.Current", fmt_bytes(status.committed))
        self._set("commit.Limit", fmt_bytes(status.commit_limit))
        self._set("commit.Peak", fmt_bytes(status.commit_peak))
        self._set("commit.Total", fmt_bytes(status.total))
        self._set("commit.Available", fmt_bytes(status.available))
        self._set("commit.Cached", fmt_bytes(status.cached))
        self._set("kernel.Paged pool", fmt_bytes(status.kernel_paged))
        self._set("kernel.Non-paged pool", fmt_bytes(status.kernel_nonpaged))

    def _refresh_io(self) -> None:
        current = system_counters()
        rates = system_rates(self._previous_counters, current)
        if current is not None:
            self._previous_counters = current
            self._set("kernel.Paged allocations",
                      _delta(current.paged_pool_allocs,
                             current.paged_pool_frees))
            self._set("kernel.Non-paged allocations",
                      _delta(current.nonpaged_pool_allocs,
                             current.nonpaged_pool_frees))
            self._set("kernel.Free system PTEs",
                      f"{current.free_system_ptes:,}")

        self._set("cpu.Context switches", _per_second(rates.context_switches))
        self._set("cpu.System calls", _per_second(rates.system_calls))
        self._set("kernel.Page faults/s", _per_second(rates.page_faults))
        self._set("io.Page reads/s", _per_second(rates.page_reads))
        self._set("io.Reads", _per_second(rates.io_read_ops))
        self._set("io.Writes", _per_second(rates.io_write_ops))
        self._set("io.Other", _per_second(rates.io_other_ops))
        self._set("io.Read bytes", _rate(rates.io_read_bps))
        self._set("io.Write bytes", _rate(rates.io_write_bps))
        self._set("io.Other bytes", _rate(rates.io_other_bps))

        total = rates.io_total_bps
        self.io_graph.push(total)
        # Scaled to the busiest moment in the window rather than to any
        # fixed ceiling, for the reason the network graph is: system I/O
        # spans idle to gigabytes a second, and a fixed axis is a flat line
        # for one of those.
        self.io_graph.set_ceiling(_ceiling_for(self.io_graph))
        self._push_summary("I/O bytes", total, _rate(total))

    def _push_summary(self, name: str, value: Optional[float],
                      caption: str) -> None:
        graph, reading = self.summary_graphs[name]
        graph.push(value)
        if name == "I/O bytes":
            graph.set_ceiling(_ceiling_for(graph))
        reading.setText(caption)

    def _refresh_gpu(self) -> None:
        if self._gpu is None:
            self._gpu = GpuSampler()
            self._gpu_facts = {facts.luid: facts
                               for facts in adapter_facts()}
        usage = self._gpu.sample()
        if not usage:
            return
        for entry in usage:
            facts = self._gpu_facts.get(entry.luid)
            if facts is not None and facts.software:
                continue
            row = self._gpu_row(entry.luid, facts)
            row["graph"].push(entry.utilisation)
            row["reading"].setText(
                f"{_percent(entry.utilisation)}   ·   dedicated "
                f"{_bytes_or_dash(entry.dedicated_bytes)}   ·   shared "
                f"{_bytes_or_dash(entry.shared_bytes)}")

    def _gpu_row(self, luid: int, facts):
        row = self._gpu_rows.get(luid)
        if row is None:
            name = facts.name if facts is not None and facts.name \
                else f"Adapter {luid:#x}"
            title = QLabel(name, self)
            set_role(title, "heading")
            graph = PerfGraph(semantic("match"), 100.0, self)
            graph.setMinimumHeight(110)
            reading = QLabel("—", self)
            set_role(reading, "muted")
            for widget in (title, graph, reading):
                self.gpu_area.addWidget(widget)
            row = {"graph": graph, "reading": reading}
            self._gpu_rows[luid] = row
        return row


# ---- formatting ---------------------------------------------------------

def _percent(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _rate(value: Optional[float]) -> str:
    return "—" if value is None else f"{fmt_bytes(value)}/s"


def _per_second(value: Optional[float]) -> str:
    """A count per second, or a dash when there has been only one reading.

    Never "0": every counter here is cumulative, so the first tick has no
    rate at all, and a zero would say the machine is doing nothing.
    """
    return "—" if value is None else f"{value:,.0f}/s"


def _bytes_or_dash(value: Optional[int]) -> str:
    return "—" if value is None else fmt_bytes(value)


def _delta(allocations: int, frees: int) -> str:
    """Outstanding pool allocations, with the two totals behind it.

    Process Explorer shows allocs and frees; the number anyone actually
    reads is the difference, so it leads.

    Both at zero is NOT an answer, and the panel says so. A machine
    carrying 1.2 GB of paged pool has not made zero pool allocations --
    the two readings contradict each other, which is the proof that the
    kernel is not maintaining these counters rather than reporting an
    empty pool. Windows agrees it is not: `\\Memory\\Pool Paged Allocs`
    reads 0 on this machine too, beside a `Pool Paged Bytes` of 1.34 GB.
    Rendered as "0" it looks like a measurement, which is the one thing
    an unmeasured value must never look like.
    """
    if allocations == 0 and frees == 0:
        return "not tracked by this Windows build"
    return f"{allocations - frees:,}  ({allocations:,} / {frees:,})"


def _ceiling_for(graph) -> float:
    seen = [value for value in graph.history() if value]
    return max(seen) * 1.15 if seen else 1024.0


def _uptime(seconds: float) -> str:
    total = int(seconds)
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{days}:{hours:02d}:{minutes:02d}:{secs:02d}"

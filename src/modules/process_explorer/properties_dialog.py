# src/modules/process_explorer/properties_dialog.py
from __future__ import annotations
import logging
import subprocess
from typing import Optional

import psutil
from PyQt6.QtWidgets import (QDialog, QTabWidget, QWidget, QVBoxLayout,
                              QHBoxLayout, QGridLayout, QLabel, QTextEdit,
                              QTableWidget, QPushButton, QDialogButtonBox, QLineEdit)
from PyQt6.QtCore import Qt, QTimer

from core.semantic_colors import semantic
from core.table_ui import centered_item, fit_table

from ui.perf_graph import PerfGraph
from modules.process_explorer.process_node import ProcessNode
from modules.process_explorer.lower_pane.thread_view import ThreadView
from modules.process_explorer.lower_pane.network_view import NetworkView
from modules.process_explorer.lower_pane.strings_view import StringsView

logger = logging.getLogger(__name__)


class ProcessPropertiesDialog(QDialog):
    def __init__(self, node: ProcessNode, thread_pool=None, parent=None):
        super().__init__(parent)
        self._node = node
        self._thread_pool = thread_pool
        self.setWindowTitle(f"Properties — {node.name} (PID {node.pid})")
        # Sized for ELEVEN tabs. At the old 700x500 -- chosen when there
        # were six -- the tab bar overflowed into scroll arrows at both
        # ends, so half the window was reachable only by scrolling a strip
        # of text nobody thinks to scroll.
        self.resize(1000, 660)

        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._thread_view: Optional[ThreadView] = None
        self._network_view: Optional[NetworkView] = None
        self._strings_view: Optional[StringsView] = None

        # One watch feeds every live tab, so the CPU, I/O and GPU figures
        # on the four of them always come from the same instant. Separate
        # pollers would drift and quietly disagree with each other.
        self._watch = None
        self._live_timer = None
        self._gpu_sampler = None
        self._perf_rows = {}
        self._io_rows = {}
        self._graphs = {}

        # Process Explorer's tab order.
        self._build_image_tab()
        self._build_performance_tab()
        self._build_performance_graph_tab()
        self._build_disk_network_tab()
        self._build_gpu_graph_tab()
        self._build_threads_tab()
        self._build_network_tab()
        self._build_security_tab()
        self._build_environment_tab()
        self._build_job_tab()
        self._build_strings_tab()
        self._start_watching()

    def _row(self, label: str, value: str) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(f"<b>{label}:</b>")
        # Wide enough for "Sandboxed (AppContainer):", the longest label any
        # row uses -- at the old 140px it was truncated to "Sandboxed
        # (AppContaine" with no ellipsis, unreadable at a glance.
        lbl.setFixedWidth(190)
        val = QLabel(value)
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        val.setWordWrap(True)
        h.addWidget(lbl)
        h.addWidget(val, 1)
        return w

    def _build_image_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        n = self._node
        layout.addWidget(self._row("Image", n.exe))
        layout.addWidget(self._row("Command Line", n.cmdline or "—"))
        layout.addWidget(self._row("Working Dir", "—"))
        layout.addWidget(self._row("PID", str(n.pid)))
        layout.addWidget(self._row("Parent PID", str(n.parent_pid)))
        layout.addWidget(self._row("User", n.user))
        layout.addWidget(self._row("Status", n.status))
        layout.addWidget(self._row("Integrity", n.integrity_level))

        if n.exe:
            signature = _exe_signature(n.exe)
            layout.addWidget(self._row("Signature", signature))
        else:
            layout.addWidget(self._row("Signature", "no executable to verify"))

        open_btn = QPushButton("Open File Location")
        open_btn.clicked.connect(lambda: subprocess.Popen(
            ["explorer", "/select,", n.exe]) if n.exe else None)
        layout.addWidget(open_btn)
        layout.addStretch()
        self._tabs.addTab(w, "Image")

    # ---- the live tabs --------------------------------------------------

    def _figure_grid(self, parent, names, store) -> QWidget:
        """A two-column block of captioned figures, each starting at a dash."""
        holder = QWidget(parent)
        grid = QGridLayout(holder)
        for index, name in enumerate(names):
            caption = QLabel(name, holder)
            caption.setStyleSheet("font-size: 11px;")
            value = QLabel("—", holder)
            row, column = divmod(index, 2)
            grid.addWidget(caption, row, column * 2)
            grid.addWidget(value, row, column * 2 + 1)
            store[name] = value
        return holder

    def _build_performance_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(self._figure_grid(w, (
            "CPU", "Cycles", "Kernel time", "User time", "Elapsed",
            "Threads", "Handles", "Base priority",
            "Private bytes", "Peak private bytes", "Working set",
            "Peak working set", "Virtual size", "Peak virtual size",
            "Paged pool", "Non-paged pool", "Page faults", "Hard faults",
        ), self._perf_rows))
        layout.addStretch()
        self._tabs.addTab(w, "Performance")

    def _build_performance_graph_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        for key, title, colour, ceiling in (
                ("cpu", "CPU", "info", 100.0),
                ("private", "Private bytes", "success", 1024.0),
                ("io", "I/O bytes", "warning", 1024.0)):
            caption = QLabel(title, w)
            caption.setStyleSheet("font-weight: 600;")
            graph = PerfGraph(semantic(colour), ceiling, w)
            graph.setMinimumHeight(100)
            reading = QLabel("—", w)
            reading.setStyleSheet("font-size: 11px;")
            layout.addWidget(caption)
            layout.addWidget(graph)
            layout.addWidget(reading)
            self._graphs[key] = (graph, reading)
        self._tabs.addTab(w, "Performance Graph")

    def _build_disk_network_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(self._figure_grid(w, (
            "Reads", "Read bytes", "Writes", "Write bytes",
            "Other", "Other bytes", "Read rate", "Write rate",
        ), self._io_rows))
        note = QLabel(
            "Network figures per process need a driver Process Explorer "
            "ships and this does not; the TCP/IP tab lists this process's "
            "connections instead.", w)
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 11px;")
        layout.addWidget(note)
        layout.addStretch()
        self._tabs.addTab(w, "Disk and Network")

    def _build_gpu_graph_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        caption = QLabel("GPU utilisation", w)
        caption.setStyleSheet("font-weight: 600;")
        graph = PerfGraph(semantic("match"), 100.0, w)
        graph.setMinimumHeight(120)
        reading = QLabel("—", w)
        reading.setStyleSheet("font-size: 11px;")
        layout.addWidget(caption)
        layout.addWidget(graph)
        layout.addWidget(reading)
        self._graphs["gpu"] = (graph, reading)
        self._gpu_memory = QLabel("—", w)
        layout.addWidget(self._gpu_memory)
        layout.addStretch()
        self._tabs.addTab(w, "GPU Graph")

    def _build_job_tab(self):
        """Whether the process is in a job, and what we cannot say about it.

        `IsProcessInJob` answers for another process. The job's LIMITS do
        not: `QueryInformationJobObject` needs a handle to the job itself,
        which is unnamed for most jobs, and Process Explorer reads them
        through the signed driver it ships. So this states the fact it has
        and names the gap rather than showing an empty limits table that
        reads as "no limits are set".
        """
        w = QWidget()
        layout = QVBoxLayout(w)
        in_job, reason = _in_job(self._node.pid)
        if in_job is None:
            layout.addWidget(self._row("In a job", f"could not tell — {reason}"))
        elif in_job:
            layout.addWidget(self._row("In a job", "Yes"))
            note = QLabel(
                "The job's limits and its other members cannot be read "
                "without a handle to the job object itself. Most jobs are "
                "unnamed, and Process Explorer reads them through the "
                "signed kernel driver it ships; this tool has no driver, "
                "so it reports what it can see rather than an empty limits "
                "table that would read as “no limits are set”.", w)
            note.setWordWrap(True)
            note.setStyleSheet("font-size: 11px;")
            layout.addWidget(note)
        else:
            layout.addWidget(self._row("In a job", "No"))
        layout.addStretch()
        self._tabs.addTab(w, "Job")

    # ---- the live half --------------------------------------------------

    def _start_watching(self):
        from core.procengine.gpuinfo import GpuSampler
        from core.procengine.procwatch import ProcessWatch

        try:
            self._gpu_sampler = GpuSampler()
        except Exception as error:  # noqa: BLE001
            logger.debug("No GPU counters for the properties window: %s",
                         error)
            self._gpu_sampler = None
        self._watch = ProcessWatch(self._node.pid, gpu=self._gpu_sampler)
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._tick)
        self._tick()
        self._live_timer.start(1000)

    def _tick(self):
        if self._watch is None:
            return
        if self._gpu_sampler is not None:
            self._gpu_sampler.sample()
        sample = self._watch.sample()
        if sample is None:
            self._process_gone()
            return
        self._show_performance(sample)
        self._show_io(sample)
        self._show_graphs(sample)

    def _process_gone(self):
        """The process exited while its window was open.

        Not an error, and not a reason to blank every figure to zero: the
        last reading stays on screen and the title says what happened, so
        the window remains a record of what the process was doing.
        """
        if self._live_timer is not None:
            self._live_timer.stop()
        why = (self._watch.exited_because if self._watch else None) or "exited"
        self.setWindowTitle(
            f"Properties — {self._node.name} (PID {self._node.pid}) — {why}")

    def _show_performance(self, sample):
        raw = sample.raw
        self._perf_rows["CPU"].setText(_percent(sample.cpu_percent))
        self._perf_rows["Cycles"].setText(f"{raw.cycles:,}")
        self._perf_rows["Kernel time"].setText(_duration(raw.kernel_time))
        self._perf_rows["User time"].setText(_duration(raw.user_time))
        self._perf_rows["Elapsed"].setText(_elapsed(raw.create_time))
        self._perf_rows["Threads"].setText(f"{raw.threads:,}")
        self._perf_rows["Handles"].setText(f"{raw.handles:,}")
        self._perf_rows["Base priority"].setText(str(raw.base_priority))
        for name, value in (
                ("Private bytes", raw.private_bytes),
                ("Peak private bytes", raw.peak_pagefile),
                ("Working set", raw.working_set),
                ("Peak working set", raw.peak_working_set),
                ("Virtual size", raw.virtual_size),
                ("Peak virtual size", raw.peak_virtual_size),
                ("Paged pool", raw.paged_pool),
                ("Non-paged pool", raw.nonpaged_pool)):
            self._perf_rows[name].setText(_bytes(value))
        self._perf_rows["Page faults"].setText(f"{raw.page_faults:,}")
        self._perf_rows["Hard faults"].setText(f"{raw.hard_faults:,}")

    def _show_io(self, sample):
        raw = sample.raw
        self._io_rows["Reads"].setText(f"{raw.read_ops:,}")
        self._io_rows["Writes"].setText(f"{raw.write_ops:,}")
        self._io_rows["Other"].setText(f"{raw.other_ops:,}")
        self._io_rows["Read bytes"].setText(_bytes(raw.read_bytes))
        self._io_rows["Write bytes"].setText(_bytes(raw.write_bytes))
        self._io_rows["Other bytes"].setText(_bytes(raw.other_bytes))
        self._io_rows["Read rate"].setText(_rate(sample.read_bps))
        self._io_rows["Write rate"].setText(_rate(sample.write_bps))

    def _show_graphs(self, sample):
        cpu_graph, cpu_reading = self._graphs["cpu"]
        cpu_graph.push(sample.cpu_percent)
        cpu_reading.setText(_percent(sample.cpu_percent))

        private_graph, private_reading = self._graphs["private"]
        private = float(sample.raw.private_bytes or 0)
        private_graph.push(private)
        private_graph.set_ceiling(_ceiling(private_graph))
        private_reading.setText(_bytes(sample.raw.private_bytes))

        io_graph, io_reading = self._graphs["io"]
        io_graph.push(sample.io_total_bps)
        io_graph.set_ceiling(_ceiling(io_graph))
        io_reading.setText(_rate(sample.io_total_bps))

        gpu_graph, gpu_reading = self._graphs["gpu"]
        gpu_graph.push(sample.gpu_percent)
        gpu_reading.setText(_percent(sample.gpu_percent))
        self._gpu_memory.setText(
            f"Dedicated {_bytes(sample.gpu_dedicated)}   ·   "
            f"shared {_bytes(sample.gpu_shared)}")

    def _build_threads_tab(self):
        tv = ThreadView()
        tv.load_pid(self._node.pid)
        self._thread_view = tv
        self._tabs.addTab(tv, "Threads")

    def _build_network_tab(self):
        nv = NetworkView()
        nv.load_pid(self._node.pid)
        self._network_view = nv
        self._tabs.addTab(nv, "TCP/IP")

    def _build_security_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        te = QTextEdit()
        te.setReadOnly(True)
        try:
            import win32security
            import win32api
            import win32con
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, self._node.pid)
            token = win32security.OpenProcessToken(handle, win32con.TOKEN_QUERY)
            user_sid, attr = win32security.GetTokenInformation(token, win32security.TokenUser)
            name, domain, _ = win32security.LookupAccountSid(None, user_sid)
            te.setPlainText(f"User: {domain}\\{name}\nSID: {win32security.ConvertSidToStringSid(user_sid)}")
        except Exception as e:
            te.setPlainText(f"Security info unavailable: {e}\n(Requires elevated privileges)")
        layout.addWidget(te)
        layout.addWidget(self._row("Integrity", self._node.integrity_level))
        layout.addWidget(self._row("Sandboxed (AppContainer)",
                                    _appcontainer_text(self._node.appcontainer)))
        self._tabs.addTab(w, "Security")

    def _build_environment_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        search = QLineEdit()
        search.setPlaceholderText("Filter…")
        layout.addWidget(search)
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Variable", "Value"])
        fit_table(table, stretch=[1], content=[0])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(table)

        env_items = []
        try:
            env = psutil.Process(self._node.pid).environ()
            env_items = sorted(env.items())
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            logger.debug("Ignored (psutil.AccessDenied, psutil.NoSuchProcess)", exc_info=True)

        table.setRowCount(len(env_items))
        for r, (k, v) in enumerate(env_items):
            table.setItem(r, 0, centered_item(k))
            table.setItem(r, 1, centered_item(v))

        def _filter(text):
            f = text.lower()
            for row in range(table.rowCount()):
                k_item = table.item(row, 0)
                v_item = table.item(row, 1)
                visible = (not f or f in (k_item.text() if k_item else "").lower()
                           or f in (v_item.text() if v_item else "").lower())
                table.setRowHidden(row, not visible)

        search.textChanged.connect(_filter)
        self._tabs.addTab(w, "Environment")

    def _build_strings_tab(self):
        sv = StringsView()
        if self._thread_pool:
            sv.set_thread_pool(self._thread_pool)
        sv.load_exe(self._node.exe)
        self._strings_view = sv
        self._tabs.addTab(sv, "Strings")

    def done(self, r: int) -> None:
        if self._live_timer is not None:
            self._live_timer.stop()
        if self._gpu_sampler is not None:
            # The PDH query lives in the performance-counter service, not
            # in this process, so an abandoned one outlives the dialog.
            self._gpu_sampler.close()
            self._gpu_sampler = None
        if self._thread_view is not None:
            self._thread_view.cancel()
        if self._network_view is not None:
            self._network_view.cancel()
        if self._strings_view is not None:
            self._strings_view.cancel()
        super().done(r)


# ---- formatting ---------------------------------------------------------

#: 100-nanosecond ticks per second, the unit the process times use.
HUNDRED_NS = 10_000_000


def _exe_signature(exe: str) -> str:
    """The Authenticode signature of the executable, for the Image tab.

    `verify_signature` never raises and caches per path, so this costs
    nothing on repeat reads and cannot crash the dialog on a file that is
    gone. The wording follows the engine's statuses: an unsigned file says
    so plainly, and a refusal names its reason rather than reading as a
    clean bill of health.
    """
    try:
        from core.procengine.signatures import (
            INVALID, NOT_SIGNED, VALID, verify_signature)
    except ImportError:  # pragma: no cover
        return "signature check unavailable"

    facts = verify_signature(exe)
    if facts.status == VALID:
        return f"Valid — signed by {facts.signer or 'an unknown signer'}"
    if facts.status == NOT_SIGNED:
        return "Not signed"
    if facts.status == INVALID:
        return f"Invalid — {facts.reason or 'the signature does not hold'}"
    return f"Could not verify — {facts.reason or 'unknown reason'}"


def _appcontainer_text(appcontainer: Optional[bool]) -> str:
    """The Security tab's wording for `ProcessNode.appcontainer`.

    Never "No" for a refusal: `None` means the token could not be read
    (the ordinary case for a process that is not ours, unelevated), which
    is a different fact from a token that was read and is not an
    AppContainer token.
    """
    if appcontainer is None:
        return "Could not tell — the process token could not be read"
    if appcontainer:
        return "Yes — this process runs inside an AppContainer sandbox"
    return "No"


def _percent(value) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _bytes(value) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} TB"


def _rate(value) -> str:
    """Bytes per second, or a dash where there is not a reading yet.

    Never "0 B/s" for an unmeasured rate: the first tick of any process
    has no rate, and zero there says it is doing nothing.
    """
    return "—" if value is None else f"{_bytes(value)}/s"


def _duration(ticks) -> str:
    """A process time, in 100ns ticks, as h:mm:ss.mmm."""
    if ticks is None:
        return "—"
    seconds = ticks / HUNDRED_NS
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}.{int((seconds % 1) * 1000):03d}"


def _elapsed(create_time) -> str:
    """How long the process has been running.

    `create_time` is a FILETIME -- 100ns ticks since 1601 -- so it is
    turned into a Unix epoch before being compared with the clock. Off by
    the 1601-to-1970 offset it reads as several centuries, which is
    wrong in a way nobody would misread, but also useless.
    """
    if not create_time:
        return "—"
    import time as _time

    epoch = create_time / HUNDRED_NS - 11644473600
    seconds = max(0.0, _time.time() - epoch)
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _ceiling(graph) -> float:
    """The graph's own peak, with a floor so a flat series is not scaled
    against zero."""
    seen = [value for value in graph.history() if value]
    return max(seen) * 1.15 if seen else 1024.0


def _in_job(pid: int):
    """`(in_a_job, reason)` -- whether the process belongs to a job object.

    `IsProcessInJob` answers this for another process with only
    PROCESS_QUERY_LIMITED_INFORMATION. The job's limits are a different
    matter; see `_build_job_tab`.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                     wintypes.DWORD]
    kernel32.IsProcessInJob.restype = wintypes.BOOL
    kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                        ctypes.POINTER(wintypes.BOOL)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None, "the process could not be opened"
    try:
        result = wintypes.BOOL()
        if not kernel32.IsProcessInJob(handle, None, ctypes.byref(result)):
            return None, "IsProcessInJob was refused"
        return bool(result.value), None
    finally:
        kernel32.CloseHandle(handle)

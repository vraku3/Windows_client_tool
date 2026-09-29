"""Process Explorer's Properties window, eleven tabs.

The graphs are the same pure-QPainter widgets the Performance tab uses, so
these tests paint for real: a float coordinate reaching a PyQt6 draw call
raises inside `paintEvent`, a Qt virtual, where the exception goes to
`sys.excepthook` and then `qFatal()`. Uncatchable, and the window dies.
"""
import os
import subprocess
import sys
import time

import pytest
from PyQt6.QtGui import QImage, QPainter

from modules.process_explorer.process_node import ProcessNode
from modules.process_explorer.properties_dialog import (
    ProcessPropertiesDialog, _appcontainer_text, _bytes, _duration, _elapsed,
    _in_job, _percent, _rate,
)

MY_PID = os.getpid()

EXPECTED_TABS = [
    "Image", "Performance", "Performance Graph", "Disk and Network",
    "GPU Graph", "Threads", "TCP/IP", "Security", "Environment", "Job",
    "Strings",
]


def _node(pid=MY_PID, name="python.exe"):
    return ProcessNode(pid=pid, name=name, exe=sys.executable, cmdline="",
                       user="me", status="running", parent_pid=0)


def _paint(widget, width=760, height=620):
    widget.resize(width, height)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    widget.render(painter)
    painter.end()
    return image


@pytest.fixture
def dialog(qapp):
    window = ProcessPropertiesDialog(_node())
    # A second reading, WITH a gap. Ticking twice in the same millisecond
    # yields no rate at all and is right to: a rate is a difference over
    # an interval, and there has not been one. The real window ticks at
    # 1 Hz.
    time.sleep(0.3)
    window._tick()
    yield window
    window.done(0)
    window.deleteLater()


# ---- the tabs -----------------------------------------------------------

def test_it_offers_process_explorers_eleven_tabs(dialog):
    labels = [dialog._tabs.tabText(index)
              for index in range(dialog._tabs.count())]
    assert labels == EXPECTED_TABS


def test_every_tab_paints(dialog):
    for index in range(dialog._tabs.count()):
        dialog._tabs.setCurrentIndex(index)
        _paint(dialog)


# ---- the live figures ---------------------------------------------------

def test_the_performance_figures_are_filled_in(dialog):
    for name in ("Cycles", "Threads", "Handles", "Private bytes",
                 "Working set", "Virtual size", "Elapsed", "Kernel time"):
        assert dialog._perf_rows[name].text() != "—", name


def test_the_cpu_figure_is_measured_after_two_readings(dialog):
    assert dialog._perf_rows["CPU"].text().endswith("%")


def test_the_io_figures_are_filled_in(dialog):
    for name in ("Reads", "Read bytes", "Writes", "Write bytes"):
        assert dialog._io_rows[name].text() != "—", name


def test_the_first_reading_shows_no_rate(qapp):
    """One reading is not a rate, and a zero would claim the process is
    doing nothing at the moment someone opened the window to find out."""
    window = ProcessPropertiesDialog(_node())
    try:
        assert window._perf_rows["CPU"].text() == "—"
        assert window._io_rows["Read rate"].text() == "—"
        assert window._graphs["cpu"][0].history() == [None]
    finally:
        window.done(0)


def test_the_graphs_accumulate(dialog):
    dialog._tick()
    assert len(dialog._graphs["cpu"][0].history()) >= 3


def test_the_gpu_tab_reports_memory(dialog):
    assert "Dedicated" in dialog._gpu_memory.text()


# ---- a process that exits while the window is open ----------------------

def test_a_process_that_exits_stops_the_window_without_blanking_it(qapp):
    """Not an error, and not a reason to zero every figure: the last
    reading stays on screen and the title says what happened."""
    child = subprocess.Popen([sys.executable, "-c",
                              "import time; time.sleep(30)"])
    window = ProcessPropertiesDialog(_node(pid=child.pid, name="python.exe"))
    try:
        window._tick()
        assert window._perf_rows["Threads"].text() != "—"
        threads = window._perf_rows["Threads"].text()

        child.kill()
        child.wait()
        time.sleep(0.3)
        window._tick()

        assert not window._live_timer.isActive()
        assert "exited" in window.windowTitle()
        assert window._perf_rows["Threads"].text() == threads, \
            "the last reading must survive the process"
    finally:
        window.done(0)
        child.poll()


def test_closing_releases_the_gpu_query(qapp):
    """The PDH query lives in the performance-counter service, so an
    abandoned one outlives the dialog."""
    window = ProcessPropertiesDialog(_node())
    assert window._gpu_sampler is not None
    window.done(0)
    assert window._gpu_sampler is None


def test_closing_stops_the_timer(qapp):
    window = ProcessPropertiesDialog(_node())
    window.done(0)
    assert not window._live_timer.isActive()


# ---- the Job tab, and what it cannot say --------------------------------

def test_whether_we_are_in_a_job_can_be_answered():
    in_job, reason = _in_job(MY_PID)
    assert in_job in (True, False), reason


def test_a_dead_pid_is_refused_rather_than_answered():
    in_job, reason = _in_job(999_999)
    assert in_job is None and reason


def test_the_job_tab_names_the_driver_limit_rather_than_faking_it(dialog):
    """A job's limits need a handle to the job object, which is unnamed
    for most jobs; Process Explorer reads them through its signed driver.
    An empty limits table would read as "no limits are set"."""
    index = EXPECTED_TABS.index("Job")
    page = dialog._tabs.widget(index)
    from PyQt6.QtWidgets import QLabel

    text = " ".join(label.text() for label in page.findChildren(QLabel))
    assert "In a job" in text


# ---- the Security tab's AppContainer row ---------------------------------

def test_the_security_tab_shows_the_sandbox_status(dialog):
    """Distinct from the Job tab and from `is_immersive`: this is the
    process's own token, not a package identity or a job membership."""
    index = EXPECTED_TABS.index("Security")
    page = dialog._tabs.widget(index)
    from PyQt6.QtWidgets import QLabel

    text = " ".join(label.text() for label in page.findChildren(QLabel))
    assert "Sandboxed (AppContainer)" in text


def test_appcontainer_text_never_says_no_for_a_refusal():
    """A refused token read is `None`. Reporting it as plain "No" would say
    the process is definitely not sandboxed when the truth is nobody
    knows."""
    assert "Could not tell" in _appcontainer_text(None)
    assert _appcontainer_text(True).startswith("Yes")
    assert _appcontainer_text(False) == "No"


# ---- formatting ---------------------------------------------------------

def test_an_unmeasured_value_is_a_dash():
    assert _percent(None) == "—"
    assert _bytes(None) == "—"
    assert _rate(None) == "—"
    assert _duration(None) == "—"
    assert _elapsed(None) == "—"


def test_a_measured_zero_is_not_a_dash():
    assert _percent(0.0) == "0.0%"
    assert _bytes(0) == "0 B"
    assert _rate(0.0) == "0 B/s"


def test_bytes_scale():
    assert _bytes(512) == "512 B"
    assert _bytes(2048) == "2.0 KB"
    assert _bytes(5 * 1024 ** 3) == "5.0 GB"


def test_a_process_time_reads_as_a_duration():
    assert _duration(10_000_000) == "0:00:01.000"
    assert _duration(3600 * 10_000_000) == "1:00:00.000"


def test_elapsed_time_converts_the_filetime_epoch(monkeypatch):
    """A FILETIME counts from 1601. Compared with the clock without the
    offset it reads as several centuries -- wrong in a way nobody would
    misread, and useless.

    The wall clock is pinned so a clock step between the fixture and the
    assertion cannot push the answer onto the other side of the 1h mark.
    """
    now = 1_700_000_000.0
    monkeypatch.setattr(time, "time", lambda: now)

    one_hour_ago = int((now + 11644473600 - 3600) * 10_000_000)
    assert _elapsed(one_hour_ago).startswith("1:00")

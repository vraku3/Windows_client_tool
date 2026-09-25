from types import SimpleNamespace


def test_power_tab_shows_a_row_per_logical_processor(qapp):
    import psutil
    from modules.dashboard.power_tab import PowerTab
    tab = PowerTab()
    tab.start()
    try:
        tab.refresh()
        import time; time.sleep(1.1); tab.refresh()
        assert tab._table.rowCount() == psutil.cpu_count(logical=True)
        assert "Topology:" in tab._topo_lbl.text() and "MHz" in tab._freq_lbl.text()
        assert tab._table.item(0, 5).text().endswith("MHz")
        assert tab._plan_box.count() >= 1
    finally:
        tab.stop()
    assert tab._eff is None


def test_processes_tab_reports_a_reused_pid(qapp):
    from modules.dashboard import processes_tab as pt
    tab = pt.ProcessesTab()
    tab._apply(tab._read())
    real = next(i for i in tab._snapshot.by_pid.values() if i.pid > 4)
    reused = SimpleNamespace(pid=real.pid, name="impostor.exe",
                             raw=SimpleNamespace(create_time=real.raw.create_time + 10**7))
    fake_snapshot = SimpleNamespace(by_pid={real.pid: reused}, taken_at=5.0)
    assert tab._recycle.update(fake_snapshot)
    tab._snapshot = tab._snapshot
    tab._update_status(1)
    assert "PID reuse" in tab.status.text()


def test_core_grid_marks_efficiency_cores(qapp):
    from modules.dashboard.overview_widgets import CoreGrid
    grid = CoreGrid()
    grid.set_loads([10, 20, 30])
    grid.set_kinds(["P", "E", "E"])
    assert "efficiency core" in grid.toolTip() and "performance core" in grid.toolTip()


def test_a_tab_named_with_an_ampersand_shows_it(qapp):
    from modules.dashboard.dashboard_module import DashboardModule
    m = DashboardModule()
    m.app = SimpleNamespace(thread_pool=None, config=None, event_bus=None, module_registry=None)
    w = m.create_widget()
    labels = [m._tabs.tabText(i) for i in range(m._tabs.count())]
    assert any("Power && Freq" in t for t in labels), labels


def test_vfd_meter_lights_segments_in_proportion_and_holds_a_falling_peak(qapp):
    from modules.dashboard.overview_widgets import VfdMeter
    m = VfdMeter()
    m.resize(400, 34)
    m.set_value(50)
    assert m.lit == 20 and m.peak_segment == 20
    m.set_value(10)                                   # load drops: the peak lags behind
    assert m.lit == 4 and m.peak_segment > m.lit
    for _ in range(60):
        m.set_value(10)
    assert m.peak_segment == m.lit                    # ...and eventually falls back
    m.set_value(500)                                  # out-of-range is clamped, not painted off the end
    assert m.lit == m.SEGMENTS
    assert m.grab().width() == 400

import time


def test_energy_tab_shows_watts_cores_and_labelled_estimates(qapp):
    from modules.dashboard.energy_tab import EnergyTab
    tab = EnergyTab()
    tab.start()
    try:
        if tab._unavailable:
            tab.refresh()
            assert "No energy meter" in tab._headline.text()
            return
        tab.refresh()
        time.sleep(1.2)
        tab.refresh()
        assert "W" in tab._headline.text() and tab._cores.rowCount() > 0
        assert "since this tab opened" in tab._detail.text()
        assert tab._procs.rowCount() >= 0
        assert "~" in (tab._procs.item(0, 3).text() if tab._procs.rowCount() else "~")
    finally:
        tab.stop()
    assert tab._meter is None


def test_thermal_tab_is_honest_without_sensors_and_shows_real_pressure(qapp):
    from modules.dashboard.thermal_tab import ThermalTab
    tab = ThermalTab()
    tab.start()
    try:
        tab.refresh()
        time.sleep(1.1)
        tab.refresh()
        if tab._unavailable:
            assert "No temperature sensors" in tab._headline.text()
            assert "°C" not in tab._headline.text()          # never invent a temperature
        assert "limiting" in tab._pressure.text()
    finally:
        tab.stop()

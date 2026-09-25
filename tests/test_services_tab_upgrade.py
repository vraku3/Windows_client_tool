"""Services tab: filter chips, search, detail panel, logon column, startup menu."""
import pytest

from modules.dashboard import services_tab as st


def _svc(name, **kw):
    base = {"Name": name, "Display Name": name + " Service", "Status": "Running",
            "Start Type": "Auto", "PID": "10", "Description": "desc " + name,
            "Impact": "Low", "Path": r"C:\Program Files\X\x.exe", "StartName": "LocalSystem"}
    base.update(kw)
    return base


@pytest.fixture
def tab(qapp):
    t = st.ServicesTab()
    t._apply([_svc("Alpha"), _svc("Beta", Status="Stopped"),
              _svc("Gamma", **{"Start Type": "Disabled"}, Status="Stopped"),
              _svc("Delta", Path=r"C:\WINDOWS\system32\svchost.exe -k x")])
    return t


def test_chips_show_counts(tab):
    assert tab._chips["stopped"][0].text() == "Stopped (2)"
    assert tab._chips["disabled"][0].text() == "Disabled (1)"


def test_a_chip_narrows_the_table(tab):
    tab._set_filter("stopped")
    assert tab._table.rowCount() == 2
    tab._set_filter("all")
    assert tab._table.rowCount() == 4


def test_search_filters_by_description(tab):
    tab._search.setText("desc beta")
    assert tab._table.rowCount() == 1


def test_selecting_a_service_fills_the_detail_and_it_survives_a_refresh(tab):
    tab._table.selectRow(1)
    assert "Service" in tab.detail.toPlainText()
    name = tab._selected_name()
    tab._repopulate()
    assert tab._selected_name() == name and tab.detail.toPlainText()


def test_logon_column_is_filled(tab):
    assert tab._table.item(0, st.LOGON).text() == "LocalSystem"


def test_menu_offers_startup_type_and_copy(tab):
    titles = [a.text() for a in tab._menu_for(_svc("Alpha")).actions()]
    assert "Startup type" in titles and "Copy service name" in titles

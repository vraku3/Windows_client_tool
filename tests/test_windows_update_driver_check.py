"""Fakes the COM boundary the same way test_windows_updater.py does --
a real WUA search needs the service, the network, and a pending driver
update, none of which this dev machine has right now (confirmed live:
zero pending updates of any kind)."""
from modules.driver_manager.driver_reader import DriverInfo
from modules.driver_manager.vendor_updates import windows_update_driver_check as wudc


class _Coll:
    def __init__(self, items):
        self._items = list(items)

    @property
    def Count(self):
        return len(self._items)

    def Item(self, i):
        return self._items[i]


class _DriverUpdate:
    def __init__(self, hwid, title="Intel Graphics Driver Update",
                manufacturer="Intel Corporation", driver_class="DISPLAY"):
        self.DriverHardwareID = hwid
        self.Title = title
        self.DriverManufacturer = manufacturer
        self.DriverClass = driver_class


def _install_fake_search(monkeypatch, updates, raise_on_search=None):
    import win32com.client

    class _Searcher:
        def Search(self, criteria):
            if raise_on_search:
                raise raise_on_search
            return type("R", (), {"Updates": _Coll(updates)})()

    class _Session:
        def CreateUpdateSearcher(self):
            return _Searcher()

    monkeypatch.setattr(win32com.client, "Dispatch", lambda progid: _Session())


def _driver(hardware_id="PCI\\VEN_8086&DEV_A780&SUBSYS_00000000"):
    return DriverInfo(device_name="Intel Graphics", driver_class="Display",
                      version="1.0", date="", publisher="Intel", signed=True,
                      error_code=0, flags="", hardware_id=hardware_id)


def test_finds_a_matching_driver_update(monkeypatch):
    _install_fake_search(monkeypatch, [_DriverUpdate("PCI\\VEN_8086&DEV_A780")])
    result = wudc.find_windows_update_driver(_driver())
    assert result is not None
    assert result.title == "Intel Graphics Driver Update"
    assert result.manufacturer == "Intel Corporation"
    assert result.driver_class == "DISPLAY"


def test_matches_when_the_wu_update_hardware_id_is_a_more_general_prefix(monkeypatch):
    # driver's own hardware_id carries &SUBSYS_.../&REV_..., the WU
    # update's own is the shorter, more general VEN&DEV prefix.
    _install_fake_search(monkeypatch, [_DriverUpdate("PCI\\VEN_8086&DEV_A780")])
    result = wudc.find_windows_update_driver(
        _driver("PCI\\VEN_8086&DEV_A780&SUBSYS_12345678&REV_01"))
    assert result is not None


def test_matches_when_the_devices_own_hardware_id_is_the_more_general_one(monkeypatch):
    _install_fake_search(monkeypatch, [_DriverUpdate("PCI\\VEN_8086&DEV_A780&SUBSYS_12345678")])
    result = wudc.find_windows_update_driver(_driver("PCI\\VEN_8086&DEV_A780"))
    assert result is not None


def test_returns_none_when_no_update_matches(monkeypatch):
    _install_fake_search(monkeypatch, [_DriverUpdate("PCI\\VEN_10DE&DEV_2684")])
    result = wudc.find_windows_update_driver(_driver())
    assert result is None


def test_returns_none_when_no_updates_are_pending(monkeypatch):
    _install_fake_search(monkeypatch, [])
    assert wudc.find_windows_update_driver(_driver()) is None


def test_returns_none_for_an_empty_hardware_id():
    assert wudc.find_windows_update_driver(_driver(hardware_id="")) is None


def test_returns_none_and_logs_when_the_search_itself_raises(monkeypatch, caplog):
    _install_fake_search(monkeypatch, [], raise_on_search=OSError("WU service unavailable"))
    with caplog.at_level("WARNING"):
        result = wudc.find_windows_update_driver(_driver())
    assert result is None
    assert any("windows_update_driver_check" in r.name for r in caplog.records)


def test_skips_one_bad_update_without_aborting_the_whole_scan(monkeypatch, caplog):
    class _BrokenUpdate:
        @property
        def DriverHardwareID(self):
            raise RuntimeError("COM property access failed")

    good = _DriverUpdate("PCI\\VEN_8086&DEV_A780")
    _install_fake_search(monkeypatch, [_BrokenUpdate(), good])
    with caplog.at_level("WARNING"):
        result = wudc.find_windows_update_driver(_driver())
    assert result is not None
    assert result.title == good.Title


def test_find_many_searches_once_and_matches_multiple_devices(monkeypatch):
    calls = []

    def counting_search(criteria):
        calls.append(criteria)
        return type("R", (), {"Updates": _Coll([
            _DriverUpdate("PCI\\VEN_8086&DEV_A780", title="Intel Graphics Update"),
            _DriverUpdate("PCI\\VEN_10EC&DEV_8126", title="Realtek Update",
                         manufacturer="Realtek"),
        ])})()

    import win32com.client

    class _Searcher:
        Search = staticmethod(counting_search)

    class _Session:
        def CreateUpdateSearcher(self):
            return _Searcher()

    monkeypatch.setattr(win32com.client, "Dispatch", lambda progid: _Session())

    d1 = _driver("PCI\\VEN_8086&DEV_A780&SUBSYS_1")
    d1.device_id = "PCI\\D1"
    d2 = _driver("PCI\\VEN_10EC&DEV_8126&SUBSYS_2")
    d2.device_id = "PCI\\D2"
    d3 = _driver("PCI\\VEN_FFFF&DEV_0000")  # no match
    d3.device_id = "PCI\\D3"

    result = wudc.find_windows_update_drivers_for_many([d1, d2, d3])

    assert len(calls) == 1  # ONE search for all three devices, not three
    assert set(result.keys()) == {"PCI\\D1", "PCI\\D2"}
    assert result["PCI\\D1"].title == "Intel Graphics Update"
    assert result["PCI\\D2"].manufacturer == "Realtek"


def test_find_many_returns_empty_dict_when_search_fails(monkeypatch):
    _install_fake_search(monkeypatch, [], raise_on_search=OSError("no WU service"))
    d1 = _driver()
    d1.device_id = "PCI\\D1"
    assert wudc.find_windows_update_drivers_for_many([d1]) == {}


def test_find_many_skips_devices_with_no_hardware_id_or_device_id(monkeypatch):
    _install_fake_search(monkeypatch, [_DriverUpdate("PCI\\VEN_8086&DEV_A780")])
    d1 = _driver(hardware_id="")
    d1.device_id = "PCI\\D1"
    assert wudc.find_windows_update_drivers_for_many([d1]) == {}


def test_handles_missing_manufacturer_or_class_gracefully(monkeypatch):
    class _SparseUpdate:
        DriverHardwareID = "PCI\\VEN_8086&DEV_A780"
        Title = "Some Update"
        DriverManufacturer = None
        DriverClass = None

    _install_fake_search(monkeypatch, [_SparseUpdate()])
    result = wudc.find_windows_update_driver(_driver())
    assert result is not None
    assert result.manufacturer == "Unknown"
    assert result.driver_class == "Unknown"

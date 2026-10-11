import psutil

from modules.cleanup.cleanup_scanner import lock_owners


class _FakeRm:
    def __init__(self, **returns):
        self.r = returns
        self.get_calls = 0

    def RmStartSession(self, *a):
        return self.r.get("start", 0)

    def RmRegisterResources(self, *a):
        return self.r.get("reg", 0)

    def RmGetList(self, *a):
        self.get_calls += 1
        g = self.r.get("get", 0)
        return g(self, *a) if callable(g) else g

    def RmEndSession(self, *a):
        return self.r.get("end", 0)


def _patch_dll(monkeypatch, fake):
    monkeypatch.setattr(lock_owners.ctypes, "WinDLL", lambda name: fake)


def test_refused_calls_are_logged_with_operation(monkeypatch, caplog):
    names = {"start": "RmStartSession", "reg": "RmRegisterResources",
             "get": "RmGetList", "end": "RmEndSession"}
    for op, label in names.items():
        caplog.clear()
        _patch_dll(monkeypatch, _FakeRm(**{op: 5}))
        with caplog.at_level("WARNING"):
            assert lock_owners.owners_of(["C:\\x.log"]) == {}
        assert any(label in r.getMessage() and "5" in r.getMessage()
                   and "x.log" in r.getMessage() and r.levelname == "WARNING"
                   for r in caplog.records), op


def test_second_get_list_more_data_is_retried(monkeypatch):
    def get(self, handle, needed, count, infos, reasons):
        if self.get_calls == 1:
            needed._obj.value = 1
            return 234
        if self.get_calls == 2:
            needed._obj.value = 2
            return 234
        count._obj.value = 1
        infos[0].Process.dwProcessId = 4242
        infos[0].strAppName = "Thing"
        return 0

    fake = _FakeRm(get=get)
    _patch_dll(monkeypatch, fake)
    monkeypatch.setattr(lock_owners, "_display_name", lambda pid, fb: "thing.exe")
    assert lock_owners.owners_of(["C:\\x.log"]) == {"thing.exe": 1}
    assert fake.get_calls == 3


def test_get_list_gives_up_with_a_warning(monkeypatch, caplog):
    def get(self, handle, needed, count, infos, reasons):
        needed._obj.value = 1
        return 234

    _patch_dll(monkeypatch, _FakeRm(get=get))
    with caplog.at_level("WARNING"):
        assert lock_owners.owners_of(["C:\\x.log"]) == {}
    assert any("gave up" in r.getMessage() for r in caplog.records)


class _P:
    def __init__(self, name, parent=None, boom=None):
        self._n, self._p, self._b = name, parent, boom

    def name(self):
        return self._n

    def parent(self):
        if self._b:
            raise self._b
        return self._p


def test_webview2_is_attributed_to_its_host(monkeypatch):
    teams = _P("ms-teams.exe")
    chain = {1: _P("msedgewebview2.exe", _P("MSEdgeWebView2.exe", teams))}
    monkeypatch.setattr(psutil, "Process", lambda pid: chain[pid])
    assert lock_owners._display_name(1, "x") == "ms-teams.exe (WebView2)"


def test_webview2_falls_back_when_parent_walk_fails(monkeypatch):
    chain = {1: _P("msedgewebview2.exe", boom=psutil.AccessDenied()),
             2: _P("msedgewebview2.exe")}
    monkeypatch.setattr(psutil, "Process", lambda pid: chain[pid])
    assert lock_owners._display_name(1, "x") == "msedgewebview2.exe"
    assert lock_owners._display_name(2, "x") == "msedgewebview2.exe"

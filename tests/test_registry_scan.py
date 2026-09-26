import datetime

from modules.registry_explorer import registry_scan as rs


class FakeKey:
    def __init__(self, path):
        self.path = path

    def Close(self):
        pass


class FakeBackend(rs.Backend):
    """tree: {path: (last_write_filetime, {value: data})}; children derived from paths."""

    def __init__(self, tree, refuse=()):
        self.tree = tree
        self.refuse = set(refuse)

    def open(self, hive, path):
        if path in self.refuse:
            raise PermissionError("denied")
        return FakeKey(path)

    def _kids(self, path):
        pre = path + "\\" if path else ""
        return sorted({p[len(pre):].split("\\")[0] for p in self.tree if p.startswith(pre) and p != path})

    def info(self, key):
        ft_, vals = self.tree.get(key.path, (0, {}))
        return len(self._kids(key.path)), len(vals), ft_

    def subkey(self, key, i):
        return self._kids(key.path)[i]

    def value(self, key, i):
        name = list(self.tree[key.path][1])[i]
        return name, self.tree[key.path][1][name], 1


def ft(dt):
    return int((dt - datetime.datetime(1601, 1, 1)).total_seconds() * 10_000_000)


OLD = datetime.datetime(2020, 1, 1)
NEW = datetime.datetime(2026, 9, 1)
TREE = {
    "Soft": (ft(OLD), {}),
    "Soft\\Alpha": (ft(OLD), {"Path": "C:\\tools\\x", "Other": 5}),
    "Soft\\Beta": (ft(NEW), {}),
    "Soft\\Beta\\Secret": (ft(OLD), {"k": "v"}),
}


def run(**kw):
    return rs.scan("HKEY_LOCAL_MACHINE\\Soft", backend=FakeBackend(TREE, kw.pop("refuse", ())), **kw)


def test_filetime_conversion():
    assert rs.filetime_to_datetime(ft(NEW)) == NEW


def test_key_name_search():
    r = run(text="beta")
    assert [h.path for h in r.hits] == ["HKEY_LOCAL_MACHINE\\Soft\\Beta"]


def test_value_data_search_only_when_asked():
    assert not run(text="tools", in_names=False).hits
    r = run(text="tools", in_names=False, in_values=True)
    assert r.hits[0].what == "value data" and "Path" in r.hits[0].detail


def test_modified_since():
    r = run(modified_since=datetime.datetime(2026, 1, 1))
    assert [h.path.split("\\")[-1] for h in r.hits] == ["Beta"]


def test_refused_keys_are_reported_not_hidden():
    r = run(text="secret", refuse={"Soft\\Beta\\Secret"})
    assert not r.hits and r.refused and "denied" in r.refused[0]


def test_max_hits_truncates():
    r = run(text="a", max_hits=1)
    assert r.truncated and len(r.hits) == 1


def test_cancel():
    r = rs.scan("HKEY_LOCAL_MACHINE\\Soft", text="a", is_cancelled=lambda: True, backend=FakeBackend(TREE))
    assert r.keys_visited == 0


def test_unknown_hive_is_refusal():
    assert rs.scan("HKEY_NOPE\\x").refused


def test_real_machine_scan_is_plausible():
    r = rs.scan("HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run", text="zzzz-no-such")
    assert r.keys_visited >= 1 and not r.hits
    recent = rs.scan("HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon",
                     modified_since=datetime.datetime(2000, 1, 1))
    assert recent.hits and recent.hits[0].last_write.year >= 2000

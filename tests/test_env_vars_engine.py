"""Env-var engines. Fakes only: nothing here writes the registry."""
import winreg

from modules.env_vars import env_ops, path_analysis as pa
from modules.env_vars.env_ops import EnvVar


def test_split_path_quotes_and_empties():
    assert pa.split_path('C:\\a;"C:\\b;c";;C:\\d;') == ["C:\\a", "C:\\b;c", "C:\\d"]


def test_expand_known_and_unknown():
    assert pa.expand("%FOO%\\x;%NOPE%", {"foo": "C:\\f"}) == "C:\\f\\x;%NOPE%"


def test_analyse_findings():
    dirs = {"c:\\a", "c:\\b"}
    files = {"c:\\a": {"tool.exe"}, "c:\\b": {"tool.exe", "x.exe"}}
    rep = pa.analyse("C:\\a;C:\\A\\;C:\\b;C:\\gone;%UNDEF%\\z", False, {},
                     exists_fn=lambda p: p.lower().rstrip("\\") in dirs,
                     list_fn=lambda p: files.get(p.lower().rstrip("\\")))
    kinds = pa.summarize(rep)
    assert kinds["duplicate"] == 1
    assert kinds["missing"] == 1
    assert kinds["unexpanded"] == 1
    assert kinds["shadowed"] == 1
    assert any("tool.exe" in f.message and "entry 1" in f.message for f in rep.findings)


def test_unreadable_folder_is_unknown_not_clean():
    rep = pa.analyse("C:\\a", False, {}, exists_fn=lambda p: True, list_fn=lambda p: None)
    assert [f.severity for f in rep.findings] == [pa.SEV_UNKNOWN]


def test_writable_finding_only_early_system_entries():
    val = ";".join(f"C:\\d{i}" for i in range(12))
    rep = pa.analyse(val, True, {}, exists_fn=lambda p: True, list_fn=lambda p: set(),
                     writable_fn=lambda p: p == "C:\\d1" or p == "C:\\d11")
    w = [f for f in rep.findings if f.kind == "writable"]
    assert [f.entry_index for f in w] == [1]
    rep2 = pa.analyse(val, False, {}, exists_fn=lambda p: True, list_fn=lambda p: set(),
                      writable_fn=lambda p: True)
    assert not [f for f in rep2.findings if f.kind == "writable"]


def test_parse_icacls():
    bad = "C:\\x BUILTIN\\Administrators:(OI)(CI)(F)\n       BUILTIN\\Users:(OI)(CI)(M)\n"
    ok = "C:\\x NT SERVICE\\TrustedInstaller:(F)\n       BUILTIN\\Users:(OI)(CI)(RX)\n"
    denied = "C:\\x BUILTIN\\Users:(DENY)(W)\n"
    assert pa.parse_icacls(bad) is True
    assert pa.parse_icacls(ok) is False
    assert pa.parse_icacls(denied) is False
    assert pa.parse_icacls("Access is denied.") is None


def test_parse_icacls_real_machine_ranges():
    assert pa.icacls_writable("C:\\Windows\\System32") is False


class FakeReg(env_ops.RegistryBackend):
    def __init__(self, data, lie=False, refuse=False):
        self.data = dict(data)
        self.lie = lie
        self.refuse = refuse

    def read_all(self, hive, path):
        return [EnvVar(k, v[0], v[1]) for k, v in self.data.items()]

    def write(self, hive, path, name, value, kind):
        if self.refuse:
            raise PermissionError("denied")
        if not self.lie:
            self.data[name] = (value, kind)

    def delete(self, hive, path, name):
        if self.refuse:
            raise PermissionError("denied")
        if not self.lie:
            self.data.pop(name, None)


def test_set_keeps_existing_type_and_broadcasts():
    reg = FakeReg({"A": ("1", winreg.REG_SZ)})
    calls = []
    r = env_ops.set_verified(None, "", "A", "%X%", backend=reg, broadcast=lambda: calls.append(1))
    assert r.ok and reg.data["A"] == ("%X%", winreg.REG_SZ) and calls


def test_new_var_type_follows_content():
    assert env_ops.choose_kind("plain", None) == winreg.REG_SZ
    assert env_ops.choose_kind("%A%\\x", None) == winreg.REG_EXPAND_SZ


def test_write_that_did_not_land_is_reported():
    reg = FakeReg({}, lie=True)
    calls = []
    r = env_ops.set_verified(None, "", "A", "v", backend=reg, broadcast=lambda: calls.append(1))
    assert not r.ok and not calls
    reg.data["B"] = ("v", winreg.REG_SZ)
    assert not env_ops.delete_verified(None, "", "B", backend=reg, broadcast=lambda: None).ok


def test_refusal_reported():
    r = env_ops.set_verified(None, "", "A", "v", backend=FakeReg({}, refuse=True), broadcast=lambda: None)
    assert not r.ok and "refused" in r.message


def test_snapshot_diff():
    old = {"system": {"A": "1", "B": "2"}, "user": {}}
    new = {"system": {"A": "9", "C": "3"}, "user": {"U": "x"}}
    lines = env_ops.diff_snapshots(old, new)
    assert len(lines) == 4
    assert any("changed A" in ln for ln in lines) and any("removed B" in ln for ln in lines)

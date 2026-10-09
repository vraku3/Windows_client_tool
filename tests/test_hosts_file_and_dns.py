"""Hosts editor: absent file, true location, byte-faithful save, DNS cross-check.

Real-machine facts these pin (2026-10-09): this machine has NO hosts file in
System32\\drivers\\etc, and every Save used to fail on that ("the backup
failed"); DataBasePath is REG_EXPAND_SZ %SystemRoot%\\System32\\drivers\\etc;
DnsQuery_W with NO_HOSTS_FILE answers 9003 for a nonexistent name and 9501
for a name with no record of that family.
"""
import os

import pytest

from modules.hosts_editor import hosts_analysis as ha
from modules.hosts_editor import hosts_dns_check as dc
from modules.hosts_editor import hosts_file as hf


def _rows(lines):
    return [{"origin": e.index, "enabled": e.enabled, "ip": e.ip, "hosts": list(e.hosts),
             "comment": e.comment} for e in ha.entries(lines)]


# --------------------------------------------------------------- absent file

def test_absent_file_is_empty_not_an_error(tmp_path):
    r = hf.read_hosts_text(str(tmp_path / "hosts"))
    assert r.exists is False and r.error == "" and r.editable and r.text == ""


def test_save_creates_an_absent_file_with_nothing_to_back_up(tmp_path):
    path, bdir = str(tmp_path / "hosts"), str(tmp_path / "bk")
    read = hf.read_hosts_text(path)
    text = ha.serialize([], [{"origin": -1, "enabled": True, "ip": "10.0.0.1",
                              "hosts": ["box.test"], "comment": ""}])
    out = hf.save_hosts(path, text, read, bdir)
    assert out.ok and out.created and out.backup is None
    assert open(path, "rb").read() == b"10.0.0.1\tbox.test\r\n"
    assert not os.path.exists(bdir)          # nothing was backed up, and that is fine


def test_failed_first_write_removes_the_file_it_created(tmp_path, monkeypatch):
    path = str(tmp_path / "hosts")

    def half_write(p, data):
        open(p, "wb").write(data[:3])
        return "the file read back differs from what was written"
    monkeypatch.setattr(hf, "write_bytes_verified", half_write)
    out = hf.replace_verified(path, b"10.0.0.1 x.test\r\n", str(tmp_path / "bk"))
    assert not out.ok and out.created and out.rollback_error == ""
    assert not os.path.exists(path)


# --------------------------------------------------------------- byte-faithful

@pytest.mark.parametrize("data", [
    b"# caf\xe9 server\r\n127.0.0.1 localhost\r\n",                    # ANSI cp1252
    b"\xef\xbb\xbf# hosts\r\n10.0.0.1 a.test\r\n",                     # UTF-8 with BOM
    "\ufeff# hosts\r\n10.0.0.1 a.test\r\n".encode("utf-16-le"),        # Notepad "Unicode"
    "\ufeff# hosts\r\n10.0.0.1 a.test\r\n".encode("utf-16-be"),
    b"# unix endings\n10.0.0.1 a.test\n",                              # LF only
])
def test_untouched_save_is_byte_identical(tmp_path, data):
    path = tmp_path / "hosts"
    path.write_bytes(data)
    read = hf.read_hosts_text(str(path))
    assert read.editable, read.read_only_reason
    lines = ha.parse_hosts(read.text)
    out = hf.save_hosts(str(path), ha.serialize(lines, _rows(lines)), read, str(tmp_path / "bk"))
    assert out.ok and out.backup and path.read_bytes() == data
    assert open(out.backup, "rb").read() == data


def test_old_decode_would_have_corrupted_an_ansi_comment():
    """The bug this replaces: utf-8 errors=replace, then written as UTF-8."""
    data = b"# caf\xe9\r\n"
    old = data.decode("utf-8", errors="replace").encode("utf-8")
    assert old != data and b"\xef\xbf\xbd" in old
    r = hf.decode_hosts(data, ansi="cp1252")
    assert r.text.startswith("# caf\u00e9") and hf.encode_hosts(r.text, r.encoding, r.newline, r.bom) == data


def test_edit_keeps_encoding_and_bom(tmp_path):
    path = tmp_path / "hosts"
    path.write_bytes(b"\xef\xbb\xbf# h\r\n10.0.0.1 a.test\r\n")
    read = hf.read_hosts_text(str(path))
    lines = ha.parse_hosts(read.text)
    rows = _rows(lines)
    rows[0]["ip"] = "10.0.0.2"
    assert hf.save_hosts(str(path), ha.serialize(lines, rows), read, str(tmp_path / "bk")).ok
    assert path.read_bytes() == b"\xef\xbb\xbf# h\r\n10.0.0.2\ta.test\r\n"


@pytest.mark.parametrize("data", [b"1\x002\x00", b"\x81\x8d\x8f\x90\x9d"])
def test_undecodable_file_is_read_only_and_never_written(tmp_path, data):
    path = tmp_path / "hosts"
    path.write_bytes(data)
    read = hf.decode_hosts(data, ansi="cp1252")
    assert not read.editable and "read-only" in read.read_only_reason
    out = hf.save_hosts(str(path), "10.0.0.1 x.test\n", read, str(tmp_path / "bk"))
    assert not out.ok and path.read_bytes() == data


def test_character_the_encoding_cannot_hold_is_refused(tmp_path):
    path = tmp_path / "hosts"
    path.write_bytes(b"# caf\xe9\r\n")
    read = hf.decode_hosts(path.read_bytes(), ansi="cp1252")
    out = hf.save_hosts(str(path), "# \u4e2d\u6587\n", read, str(tmp_path / "bk"))
    assert not out.ok and "cannot hold" in out.error and path.read_bytes() == b"# caf\xe9\r\n"


def test_failed_write_restores_the_original_bytes(tmp_path, monkeypatch):
    path = tmp_path / "hosts"
    original = b"# caf\xe9\r\n10.0.0.1 a.test\r\n"
    path.write_bytes(original)
    monkeypatch.setattr(hf, "write_bytes_verified", lambda p, d: open(p, "wb").write(b"junk") and "mismatch")
    out = hf.replace_verified(str(path), b"new", str(tmp_path / "bk"))
    assert not out.ok and out.backup and not out.created and path.read_bytes() == original


def test_backup_failure_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "hosts"
    path.write_bytes(b"10.0.0.1 a.test\r\n")

    def refuse(*_a):
        raise OSError("disk full")
    monkeypatch.setattr(hf, "backup_file", refuse)
    out = hf.replace_verified(str(path), b"new", str(tmp_path / "bk"))
    assert not out.ok and "backup failed" in out.error and path.read_bytes() == b"10.0.0.1 a.test\r\n"


def test_restore_is_byte_for_byte_and_works_when_no_file_exists(tmp_path):
    bak = tmp_path / "hosts_20260101_000000.txt"
    bak.write_bytes(b"\xef\xbb\xbf10.0.0.9 r.test\r\n")
    out, data = hf.restore_from(str(bak), str(tmp_path / "hosts"), str(tmp_path / "bk"))
    assert out.ok and out.created and (tmp_path / "hosts").read_bytes() == data == bak.read_bytes()


def test_unreadable_file_is_an_error_not_empty(tmp_path, monkeypatch):
    def refuse(*_a, **_k):
        raise PermissionError(13, "Access is denied")
    monkeypatch.setattr("builtins.open", refuse)
    r = hf.read_hosts_text(str(tmp_path / "hosts"))
    assert r.error and not r.editable


# --------------------------------------------------------------- location

def test_location_default_relocated_absent_and_refused():
    assert hf.hosts_location(lambda: r"%SystemRoot%\System32\drivers\etc").relocated is False
    moved = hf.hosts_location(lambda: r"C:\ProgramData\x")
    assert moved.relocated and moved.path.lower() == r"c:\programdata\x\hosts"
    assert hf.hosts_location(lambda: None).path == hf.default_hosts_path()

    def refuse():
        raise PermissionError(5, "Access is denied")
    denied = hf.hosts_location(refuse)
    assert denied.error and denied.path == hf.default_hosts_path() and not denied.relocated
    assert hf.location_findings(True, r"C:\x", r"C:\x\hosts", "")[0].severity == ha.SEV_HIGH
    assert hf.location_findings(False, None, "p", denied.error)[0].key == "location_unknown"
    assert hf.location_findings(False, "d", "p", "") == []


def test_real_machine_location_and_read():
    """This machine: default DataBasePath, readable unelevated; the file may be absent."""
    loc = hf.hosts_location()
    assert loc.error == "" and loc.configured, loc
    assert loc.path.lower().endswith(os.path.join("drivers", "etc", "hosts"))
    r = hf.read_hosts_text(loc.path)
    assert r.error == "", r.error            # absent or decoded, never refused
    if not r.exists:
        assert r.text == "" and r.editable


# --------------------------------------------------------------- DNS check

def _lines(text):
    return ha.parse_hosts(text)


def test_classify_every_status_code():
    assert dc.classify("a", "1.2.3.4", (0, ["1.2.3.4", "5.6.7.8"], "")).status == dc.AGREES
    d = dc.classify("a", "1.2.3.4", (0, ["5.6.7.8"], ""))
    assert d.status == dc.DIFFERS and d.answers == ["5.6.7.8"]
    assert dc.classify("a", "2001:DB8::1", (0, ["2001:db8::1"], "")).status == dc.AGREES
    assert dc.classify("a", "1.2.3.4", (9003, [], "")).status == dc.NXDOMAIN
    assert dc.classify("a", "1.2.3.4", (9501, [], "")).status == dc.NO_RECORDS
    assert dc.classify("a", "1.2.3.4", (123, [], "")).status == dc.NXDOMAIN
    assert dc.classify("a", "1.2.3.4", (1460, [], "")).status == dc.UNKNOWN
    assert dc.classify("a", "1.2.3.4", (-1, [], "dnsapi missing")).status == dc.UNKNOWN


def test_check_skips_sinks_localhost_disabled_and_duplicates():
    lines = _lines("0.0.0.0 ads.test\n127.0.0.1 localhost\n::1 localhost\n# 10.0.0.1 off.test\n"
                   "10.0.0.2 on.test\n10.0.0.2 on.test\nfe80::1%3 ll.test\n")
    asked = []

    def fake(name, rtype):
        asked.append((name, rtype))
        return 0, [], ""
    dc.check_entries(lines, query=fake)
    assert asked == [("on.test", dc.DNS_TYPE_A), ("ll.test", dc.DNS_TYPE_AAAA)]


def test_findings_group_and_follow_current_rows():
    lines = _lines("10.0.0.1 a.test b.test\n10.0.0.2 c.test\n")
    checks = [dc.DnsCheck("a.test", "10.0.0.1", dc.DIFFERS, ["9.9.9.9"]),
              dc.DnsCheck("b.test", "10.0.0.1", dc.DIFFERS, ["9.9.9.8"]),
              dc.DnsCheck("c.test", "10.0.0.2", dc.UNKNOWN, detail="timeout"),
              dc.DnsCheck("gone.test", "10.0.0.3", dc.AGREES, ["10.0.0.3"])]
    f = {x.key: x for x in dc.dns_findings(checks, lines)}
    assert f["dns_differs"].title.startswith("2 ") and f["dns_differs"].lines == [0]
    assert f["dns_unknown"].severity == ha.SEV_LOW and "not evidence" in f["dns_unknown"].detail
    assert "dns_agrees" not in f                         # its line is gone; never point at the wrong row
    edited = _lines("10.0.0.9 a.test\n10.0.0.2 c.test\n")  # a.test re-pinned since the check
    assert "dns_differs" not in {x.key for x in dc.dns_findings(checks, edited)}
    assert "2 differs" in dc.summary(checks) and "1 unknown" in dc.summary(checks)


def test_real_machine_dns_without_hosts_file():
    """DnsQuery_W(NO_HOSTS_FILE|BYPASS_CACHE): nonexistent name is 9003, real one resolves."""
    rc, answers, err = dc.query_without_hosts("no-such-host-zzq.invalid", dc.DNS_TYPE_A)
    assert err == ""
    if rc not in (0, dc.DNS_ERROR_RCODE_NAME_ERROR):
        pytest.skip("no DNS on this machine right now (code %d)" % rc)
    assert rc == dc.DNS_ERROR_RCODE_NAME_ERROR and answers == []
    rc, answers, _ = dc.query_without_hosts("one.one.one.one", dc.DNS_TYPE_A)
    assert rc == 0 and "1.1.1.1" in answers
    checks = dc.check_entries(_lines("192.0.2.10 one.one.one.one\n1.1.1.1 one.one.one.one\n"))
    assert [c.status for c in checks] == [dc.DIFFERS, dc.AGREES]


# --------------------------------------------------------------- the pane

class _App:
    def __init__(self):
        from PyQt6.QtCore import QThreadPool
        self.thread_pool = QThreadPool.globalInstance()


def _pane(qapp, monkeypatch, tmp_path, admin, data=None):
    from modules.hosts_editor import hosts_editor_module as mod
    path = tmp_path / "hosts"
    if data is not None:
        path.write_bytes(data)
    monkeypatch.setattr(mod.hf, "hosts_location", lambda: hf.HostsLocation(str(path), "x", False))
    monkeypatch.setattr(mod, "backup_dir", lambda: str(tmp_path / "bk"))
    monkeypatch.setattr(mod, "confirm_destructive", lambda *a, **k: True)
    m = mod.HostsEditorModule()
    monkeypatch.setattr(m, "_is_admin", lambda: admin)
    m.create_widget()
    m.on_start(_App())
    m.on_activate()
    return m, path


def test_pane_is_read_only_unelevated(qapp, monkeypatch, tmp_path):
    from modules.hosts_editor.hosts_editor_module import HostsEditorModule
    assert HostsEditorModule.read_only_unelevated is True
    m, _ = _pane(qapp, monkeypatch, tmp_path, admin=False, data=b"10.0.0.1 a.test\r\n")
    assert not m._buttons["Save"].isEnabled() and m._buttons["Check against DNS"].isEnabled()
    assert m._buttons["Backup"].isEnabled() and not m._banner.isHidden()
    assert not m._table.cellWidget(0, 0).isEnabled()


def test_pane_saves_a_new_file_when_none_exists(qapp, monkeypatch, tmp_path):
    m, path = _pane(qapp, monkeypatch, tmp_path, admin=True)
    assert "No hosts file" in m._status.text() and m._buttons["Save"].isEnabled()
    m._add_entry()
    m._table.item(0, 2).setText("box.test")
    m._save()
    assert path.read_bytes() == b"0.0.0.0\tbox.test\r\n"
    assert "created" in m._status.text()

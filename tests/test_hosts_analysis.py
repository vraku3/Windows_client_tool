"""Hosts file model: lossless save, validation, findings, backups."""
import os

from modules.hosts_editor import hosts_analysis as ha

SAMPLE = """# Copyright (c) 1993-2009 Microsoft Corp.
#
# This is a sample HOSTS file used by Microsoft TCP/IP for Windows.
#      102.54.94.97     rhino.acme.com          # source server
127.0.0.1 localhost
0.0.0.0 ads.example.com  # ads
"""


def test_prose_comments_are_not_entries_but_commented_ips_are():
    lines = ha.parse_hosts(SAMPLE)
    ents = ha.entries(lines)
    hosts = [h for e in ents for h in e.hosts]
    assert "(c)" not in hosts and "localhost" in hosts and "rhino.acme.com" in hosts
    rhino = next(e for e in ents if "rhino.acme.com" in e.hosts)
    assert rhino.enabled is False and rhino.comment == "source server"
    assert ha.parse_line("# 1.2.3.4 is our proxy server").is_entry is False


def _rows(lines, **override):
    return [{"origin": e.index, "enabled": e.enabled, "ip": e.ip, "hosts": list(e.hosts),
             "comment": e.comment} for e in ha.entries(lines)]


def test_serialize_untouched_is_byte_identical():
    lines = ha.parse_hosts(SAMPLE)
    assert ha.serialize(lines, _rows(lines)) == SAMPLE


def test_serialize_keeps_comments_on_edit_delete_and_add():
    lines = ha.parse_hosts(SAMPLE)
    rows = _rows(lines)
    rows = [r for r in rows if "rhino.acme.com" not in r["hosts"]]       # delete
    rows[0]["ip"] = "127.0.0.2"                                            # edit
    rows.append({"origin": -1, "enabled": True, "ip": "0.0.0.0", "hosts": ["new.test"], "comment": ""})
    out = ha.serialize(lines, rows)
    assert out.startswith("# Copyright (c) 1993-2009 Microsoft Corp.\n#\n# This is a sample")
    assert "rhino" not in out and "127.0.0.2\tlocalhost" in out and out.rstrip().endswith("new.test")


def test_validation():
    assert ha.valid_ip("::1") and ha.valid_ip("10.0.0.1") and not ha.valid_ip("999.1.1.1")
    assert ha.valid_hostname("a-b.example.com") and not ha.valid_hostname("-bad.com")
    assert not ha.valid_hostname("a..b")


def _live(*specs):
    return [ha.HostLine("", True, True, ip, [h], "", i) for i, (ip, h) in enumerate(specs)]


def test_conflict_duplicate_and_blocked_known_domain():
    f = {x.key: x for x in ha.analyse(_live(
        ("1.1.1.1", "a.test"), ("2.2.2.2", "A.test"), ("3.3.3.3", "b.test"), ("3.3.3.3", "b.test"),
        ("0.0.0.0", "login.live.com"), ("9.9.9.9", "update.microsoft.com")))}
    assert f["conflict"].severity == "high" and f["conflict"].lines == [0, 1]
    assert f["duplicate"].lines == [2, 3]
    assert "login.live.com" in f["blocked_known"].title
    assert f["redirect"].lines == [5]


def test_disabled_entries_produce_no_findings():
    lines = [ha.HostLine("", True, False, "0.0.0.0", ["login.live.com"], "", 0)]
    assert not [x for x in ha.analyse(lines) if x.key == "blocked_known"]


def test_bad_ip_is_high():
    lines = [ha.HostLine("", True, True, "not-an-ip", ["x.test"], "", 0)]
    assert any(x.key == "bad_ip" and x.severity == "high" for x in ha.analyse(lines))


def test_backup_list_and_verified_write():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        _backup_body(Path(d))


def _backup_body(tmp_path):
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    bdir = str(tmp_path / "bk")
    assert ha.list_backups(bdir) == []                # missing folder is "none", not an error
    b1 = ha.backup_hosts(str(hosts), bdir)
    b2 = ha.backup_hosts(str(hosts), bdir)
    assert b1 != b2 and len(ha.list_backups(bdir)) == 2
    assert ha.write_and_verify(str(hosts), "0.0.0.0 x.test\n") is None
    assert "x.test" in hosts.read_text()
    assert ha.write_and_verify(str(tmp_path / "nodir" / "hosts"), "x") is not None


def test_real_hosts_file_parses_if_readable():
    from modules.hosts_editor.hosts_editor_module import HOSTS_PATH
    if not os.path.exists(HOSTS_PATH):
        return
    try:
        text = open(HOSTS_PATH, encoding="utf-8", errors="replace").read()
    except OSError:
        return
    lines = ha.parse_hosts(text)
    assert len(lines) == len(text.splitlines())
    assert ha.serialize(lines, _rows(lines)) == text.replace("\r\n", "\n") + ("" if text.endswith("\n") or not text else "\n")

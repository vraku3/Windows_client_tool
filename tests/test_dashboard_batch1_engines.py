"""Connections, folder sizes, system info and installed-apps logic (no Qt)."""
import os
from types import SimpleNamespace

from modules.dashboard import connections as cn
from modules.dashboard import folder_sizes as fs
from modules.dashboard import installed_apps as ia
from modules.dashboard import sysinfo as si


# ---- connections ------------------------------------------------------------------

def _c(**kw):
    base = dict(proto="TCP4", local_ip="127.0.0.1", local_port=80, remote_ip="",
                remote_port=0, state="LISTEN", pid=5, process="x.exe")
    base.update(kw)
    return cn.Connection(**base)


def test_external_vs_private_addresses():
    assert cn.is_external("8.8.8.8")
    assert not cn.is_external("192.168.1.5") and not cn.is_external("127.0.0.1")
    assert not cn.is_external("fe80::1%12") and not cn.is_external("")
    assert cn.is_external("2606:4700::1111")


def test_filters_and_counts():
    rows = [_c(), _c(state="ESTABLISHED", remote_ip="8.8.8.8", remote_port=443),
            _c(proto="UDP4", state="")]
    counts = cn.filter_counts(rows)
    assert counts["listening"] == 2 and counts["external"] == 1 and counts["udp"] == 1
    assert len(cn.visible(rows, "external", "")) == 1
    assert len(cn.visible(rows, "all", "8.8.8")) == 1


def test_endpoint_formats_ipv6_with_brackets():
    assert cn.endpoint("::1", 80) == "[::1]:80" and cn.endpoint("1.2.3.4", 5) == "1.2.3.4:5"
    assert cn.endpoint("", 0) == ""


def test_by_process_counts_sockets():
    rows = [_c(pid=1, process="a"), _c(pid=1, process="a"), _c(pid=2, process="b")]
    assert cn.by_process(rows)[0] == ("a", 1, 2)


def test_real_machine_connections_are_readable():
    rows = cn.read_connections()
    assert rows is not None and rows
    assert any(r.state == "LISTEN" for r in rows)
    assert all(isinstance(r.pid, int) for r in rows)


# ---- folder sizes -------------------------------------------------------------------

def test_scan_sums_folders_and_loose_files(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f1").write_bytes(b"x" * 1000)
    (tmp_path / "a" / "sub").mkdir()
    (tmp_path / "a" / "sub" / "f2").write_bytes(b"y" * 500)
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "f3").write_bytes(b"z" * 100)
    (tmp_path / "loose.bin").write_bytes(b"q" * 40)
    scan = fs.scan_top_level(str(tmp_path))
    sizes = dict(scan.entries)
    assert sizes["a"] == 1500 and sizes["b"] == 100 and sizes["(files in the root)"] == 40
    assert scan.entries[0][0] == "a" and scan.total == 1640 and not scan.cancelled


def test_scan_can_be_cancelled_and_says_so(tmp_path):
    for n in range(3):
        (tmp_path / f"d{n}").mkdir()
    scan = fs.scan_top_level(str(tmp_path), cancelled=lambda: True)
    assert scan.cancelled


def test_an_unreadable_root_is_counted_not_hidden():
    scan = fs.scan_top_level(os.path.join("Z:" + os.sep, "definitely", "not", "here"))
    assert scan.unreadable >= 1 and scan.entries == []


def test_volumes_lists_the_system_drive():
    assert any(v["total"] > 0 for v in fs.volumes())


# ---- installed apps ------------------------------------------------------------------

def _app(name="A", size="", date_="", type_="64-bit", publisher="P", version="1"):
    return SimpleNamespace(name=name, size_mb=size, install_date=date_, type_=type_,
                           publisher=publisher, version=version)


def test_size_parsing_and_unknown_is_none():
    assert ia.size_bytes("1.5 GB") == int(1.5 * 1024 ** 3)
    assert ia.size_bytes("200 MB") == 200 * 1024 ** 2
    assert ia.size_bytes("") is None and ia.size_bytes("n/a") is None


def test_total_reports_how_many_had_no_size():
    total, unknown = ia.total_size([_app(size="1 MB"), _app(size=""), _app(size="2 MB")])
    assert total == 3 * 1024 ** 2 and unknown == 1


def test_recent_and_big_filters():
    from datetime import date, timedelta
    fresh = (date.today() - timedelta(days=3)).isoformat()
    old = (date.today() - timedelta(days=400)).isoformat()
    rows = [_app("new", date_=fresh), _app("old", date_=old, size="3 GB")]
    assert [e.name for e in ia.visible(rows, "recent", "")] == ["new"]
    assert [e.name for e in ia.visible(rows, "big", "")] == ["old"]
    assert ia.visible(rows, "all", "OLD")[0].name == "old"


# ---- system info -----------------------------------------------------------------------

def test_sections_render_to_text_and_a_failing_reader_is_named(monkeypatch):
    from modules.hardware_inventory import hardware_reader as hr
    monkeypatch.setattr(hr, "get_gpu_info", lambda: (_ for _ in ()).throw(RuntimeError("wmi down")))
    monkeypatch.setattr(hr, "get_overview", lambda: [("Hostname", "PC")])
    sections = si.collect_sections()
    titles = [t for t, _ in sections]
    assert titles[0] == "System" and "Graphics" in titles
    gpu = dict(sections)[ "Graphics"]
    assert gpu[0][0] == "Could not be read" and "wmi down" in gpu[0][1]
    text = si.to_text(sections)
    assert "== System ==" in text and "Hostname: PC" in text

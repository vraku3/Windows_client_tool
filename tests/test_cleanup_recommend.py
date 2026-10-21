"""Cleanup recommendations (WP7): every rule fires above its threshold and not
at it, the title carries the measured number, nothing targets the owner's model
folders, and the engine cannot delete anything."""
import ast
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.formatting import format_size  # noqa: E402
from modules.cleanup.cleanup_scanner import recommend as rc  # noqa: E402
from modules.cleanup.cleanup_scanner.scan_snapshot import ScanSnapshot, SnapshotRow  # noqa: E402

HOME = r"C:\Users\tester"
NOW = datetime(2026, 10, 21, 12, 0, 0)
GB, MB = rc.GB, rc.MB


def row(id_, path, size, safety="safe", tab="System", label=None, category="system"):
    return SnapshotRow(id_, label or id_.replace("_", " ").title(), tab, category, safety, safety,
                       path, int(size), safety == "safe")


def snap(*rows):
    return ScanSnapshot(rows=rows)


def run(*rows, **kw):
    kw.setdefault("now", NOW)
    return rc.evaluate(rc.RecoInputs(snapshot=snap(*rows), **kw), home=HOME)


def ids(recs):
    return {r.id.split(".")[0] for r in recs}


def one(recs, rule):
    found = [r for r in recs if r.id.split(".")[0] == rule]
    assert len(found) == 1, [r.id for r in recs]
    return found[0]


# ── low_free_space ──

def test_low_free_space_fires_below_percent_with_largest_safe_items():
    recs = run(row("temp_files", r"C:\Windows\Temp", 3 * GB),
               row("wu_cache", r"C:\Windows\SoftwareDistribution\Download", 9 * GB, "caution"),
               drives=[("C:\\", 50 * GB, 1000 * GB)], history=[{"at": NOW.isoformat()}])
    r = one(recs, "low_free_space")
    assert r.severity == rc.HIGH and r.title == f"C: has {format_size(50 * GB)} free (5 %)"
    assert r.target_ids == ("temp_files",) and r.target_tab == rc.TAB_OVERVIEW and r.navigates
    assert recs[0] is r                                     # HIGH sorts first


def test_low_free_space_fires_below_absolute_floor_and_not_at_it():
    assert "low_free_space" in ids(run(drives=[("D:\\", 20 * GB - 1, 100 * GB)]))
    assert "low_free_space" not in ids(run(drives=[("D:\\", 20 * GB, 100 * GB)]))   # 20 %, 20 GB
    assert "low_free_space" not in ids(run(drives=[("D:\\", 100 * GB, 1000 * GB)]))  # exactly 10 %


def test_low_free_space_without_safe_items_does_not_navigate():
    r = one(run(drives=[("E:\\", 1 * GB, 100 * GB)]), "low_free_space")
    assert not r.navigates and "TreeSize" in r.evidence


# ── wu_leftovers ──

WU = (r"C:\Windows\SoftwareDistribution\Download", 1 * GB)


def test_wu_leftovers_threshold_and_navigation():
    assert "wu_leftovers" not in ids(run(row("wu_cache", WU[0], WU[1], "caution", tab="wu")))
    r = one(run(row("wu_cache", WU[0], WU[1] + 1, "caution", tab="wu")), "wu_leftovers")
    assert format_size(WU[1] + 1) in r.title and r.target_tab == "wu" and r.navigates


def test_wu_leftovers_pending_restart_says_restart_first_and_does_not_navigate():
    r = one(run(row("wu_cache", WU[0], 4 * GB, "caution"), pending_reboot=True), "wu_leftovers")
    assert r.title.startswith("Restart first") and not r.navigates
    r = one(run(row("wu_cache", WU[0], 4 * GB, "caution"), pending_reboot=None), "wu_leftovers")
    assert "Could not read" in r.evidence and not r.navigates


# ── old_temp ──

def test_old_temp_needs_both_totals_and_sets_age_filter():
    big = row("temp_files", r"C:\Users\tester\AppData\Local\Temp", 12 * GB)
    assert rc.needs_old_temp_probe(snap(big), HOME)
    r = one(run(big, old_temp_bytes=5 * GB + 1), "old_temp")
    assert format_size(5 * GB + 1) in r.title and r.age_days == 7 and r.target_ids == ("temp_files",)
    assert "old_temp" not in ids(run(big, old_temp_bytes=5 * GB))
    assert "old_temp" not in ids(run(big, old_temp_bytes=None))       # not measured is not an answer
    small = row("temp_files", r"C:\Windows\Temp", 5 * GB)
    assert not rc.needs_old_temp_probe(snap(small), HOME)
    assert "old_temp" not in ids(run(small, old_temp_bytes=9 * GB))


def test_old_temp_probe_judges_each_file_not_the_folder(tmp_path):
    temp = tmp_path / "Temp"
    (temp / "sub").mkdir(parents=True)
    old_f, new_f, kept = temp / "sub" / "old.bin", temp / "new.bin", temp / "keep" / "old.bin"
    kept.parent.mkdir()
    for f, n in ((old_f, 3000), (new_f, 500), (kept, 700)):
        f.write_bytes(b"x" * n)
    ts = NOW.timestamp()
    os.utime(old_f, (ts - 8 * 86400,) * 2)
    os.utime(kept, (ts - 30 * 86400,) * 2)
    os.utime(new_f, (ts - 1 * 86400,) * 2)
    # The folder itself was written just now, which is what the catalog's age floor tests.
    s = snap(row("temp_files", str(temp), 4200))
    assert rc.measure_old_temp(s, HOME, now=ts, excluded=lambda p: p.endswith("keep")) == 3000
    assert rc.measure_old_temp(snap(row("temp_files", str(tmp_path / "missing" / "Temp"), 9 * GB)),
                               HOME, now=ts, excluded=lambda p: False) is None


def test_repeat_holder_never_says_close_system():
    assert "repeat_holder" not in ids(run(history=_cleans(["System"], ["System"], ["svchost.exe"])))


# ── recycle_bin ──

def test_recycle_bin_lists_drives_and_says_permanent():
    recs = run(row("per_drive_recycle_bin", r"C:\$Recycle.Bin", 1 * GB, "caution"),
               row("per_drive_recycle_bin", r"E:\$Recycle.Bin", 1 * GB, "caution"))
    r = one(recs, "recycle_bin")
    assert r.title == f"Recycle Bin: {format_size(2 * GB)} on C:, E:" and "permanent" in r.note
    assert "recycle_bin" not in ids(run(row("recycle_bin", r"C:\$Recycle.Bin", 1 * GB, "caution")))


# ── crash_dumps ──

def test_crash_dumps_need_size_and_age():
    d = row("minidump", r"C:\Windows\Minidump", 500 * MB + 1, "caution")
    r = one(run(d, newest_dump_age_days=31), "crash_dumps")
    assert "31 days" in r.title and format_size(500 * MB + 1) in r.title and "investigating" in r.evidence
    assert "crash_dumps" not in ids(run(d, newest_dump_age_days=30))
    assert "crash_dumps" not in ids(run(d, newest_dump_age_days=None))
    assert "crash_dumps" not in ids(run(row("minidump", r"C:\Windows\Minidump", 500 * MB, "caution"),
                                        newest_dump_age_days=90))


def test_newest_age_days_reads_mtime(tmp_path):
    f = tmp_path / "a.dmp"
    f.write_bytes(b"x")
    old = (NOW - timedelta(days=40)).timestamp()
    os.utime(f, (old, old))
    assert rc.newest_age_days([str(f), str(tmp_path / "missing")], now=NOW) == pytest.approx(40, abs=0.01)
    assert rc.newest_age_days([str(tmp_path / "missing")], now=NOW) is None


# ── browser_running ──

CHROME = r"C:\Users\tester\AppData\Local\Google\Chrome\User Data\Default\Cache"


def test_browser_running_needs_size_and_a_running_browser():
    hist = [{"at": "2026-10-20T10:00:00", "owners": {"chrome.exe": 136}}]
    r = one(run(row("chrome_cache_full", CHROME, 500 * MB + 1, tab="browser"),
                running={"chrome.exe"}, history=hist), "browser_running")
    assert r.title.startswith("Close Chrome first") and "136 files" in r.evidence
    assert r.target_tab == "browser"
    assert "browser_running" not in ids(run(row("chrome_cache_full", CHROME, 500 * MB), running={"chrome.exe"}))
    assert "browser_running" not in ids(run(row("chrome_cache_full", CHROME, 2 * GB), running={"steam.exe"}))
    assert "browser_running" not in ids(run(row("chrome_cache_full", CHROME, 2 * GB), running=None))


# ── repeat_holder ──

def _cleans(*owner_sets):
    return [{"at": (NOW - timedelta(days=10 - i)).isoformat(), "owners": dict.fromkeys(o, 5)}
            for i, o in enumerate(owner_sets)]


def test_repeat_holder_two_of_last_three():
    r = one(run(history=_cleans(["steam.exe"], [], ["steam.exe"])), "repeat_holder")
    assert r.title == "steam.exe held files in 2 of the last 3 cleans"
    # Only once in the last three (an older clean does not count).
    assert "repeat_holder" not in ids(run(history=_cleans(["steam.exe"], ["steam.exe"], [], [], ["x.exe"])))


# ── dev_caches / shader_caches ──

def test_dev_caches_threshold():
    npm = r"C:\Users\tester\AppData\Local\npm-cache"
    r = one(run(row("npm_cache", npm, 2 * GB + 1, tab="dev")), "dev_caches")
    assert r.severity == rc.INFO and format_size(2 * GB + 1) in r.title and r.target_tab == "dev"
    assert "dev_caches" not in ids(run(row("npm_cache", npm, 2 * GB)))


def test_shader_caches_is_counter_advice_without_action():
    p = r"C:\Users\tester\AppData\Local\D3DSCache"
    r = one(run(row("d3d_shader_cache", p, 200 * MB + 1, "caution")), "shader_caches")
    assert "stutter" in r.evidence and not r.navigates and r.severity == rc.INFO
    assert "shader_caches" not in ids(run(row("d3d_shader_cache", p, 200 * MB, "caution")))


# ── stale_clean ──

def test_stale_clean_days_and_safe_threshold():
    safe = row("temp_files", r"C:\Windows\Temp", 3 * GB)
    old = [{"at": (NOW - timedelta(days=41)).isoformat()}]
    r = one(run(safe, history=old), "stale_clean")
    assert r.title == f"No clean in 41 days; {format_size(3 * GB)} is safe to remove"
    assert "stale_clean" not in ids(run(safe, history=[{"at": (NOW - timedelta(days=30)).isoformat()}]))
    assert "stale_clean" not in ids(run(row("temp_files", r"C:\Windows\Temp", 2 * GB), history=old))
    assert one(run(safe), "stale_clean").title.startswith("No clean recorded yet")


# ── growth ──

def _summaries(totals, days_apart=3.5, cats=None):
    out = []
    for i, t in enumerate(totals):
        out.append({"at": (NOW - timedelta(days=days_apart * (len(totals) - 1 - i))).isoformat(),
                    "total_size": t, "categories": (cats or [{}] * len(totals))[i]})
    return out


def test_growth_fires_above_five_gb_a_week_and_names_the_category():
    s = _summaries([10 * GB, 12 * GB, 16 * GB], cats=[{"system": 1 * GB}, {}, {"system": 6 * GB}])
    r = one(run(summaries=s), "growth")
    assert format_size(6 * GB) in r.title and "system" in r.evidence and r.target_tab == rc.TAB_DISCOVER
    # Exactly 5 GB a week does not fire (3.5 days apart: +2.5 GB per step = 5 GB/week).
    assert "growth" not in ids(run(summaries=_summaries([10 * GB, 12.5 * GB, 15 * GB], days_apart=1.75)))


def test_growth_needs_three_summaries_and_a_real_time_span():
    assert "growth" not in ids(run(summaries=_summaries([10 * GB, 40 * GB])))
    # Rescans minutes apart must not extrapolate into a weekly rate.
    assert "growth" not in ids(run(summaries=_summaries([10 * GB, 20 * GB, 40 * GB], days_apart=0.01)))
    # ...but with an older summary on file, the rate is measured against it.
    s = [{"at": (NOW - timedelta(days=7)).isoformat(), "total_size": 10 * GB}] + \
        _summaries([14 * GB, 15 * GB, 17 * GB], days_apart=0.01)
    r = one(run(summaries=s), "growth")
    assert "7 days" in r.title and format_size(7 * GB) in r.title


# ── model_caches ──

def test_model_caches_is_information_only():
    hf = HOME + r"\.cache\huggingface"
    r = one(run(model_caches={hf: 30 * GB}), "model_caches")
    assert r.severity == rc.INFO and format_size(30 * GB) in r.title
    assert "huggingface-cli delete-cache" in r.evidence
    assert not r.navigates and r.target_ids == () and r.target_tab is None and r.action_label == ""
    assert "model_caches" not in ids(run(model_caches={hf: 0}))
    assert "model_caches" not in ids(run(model_caches={}))


def test_measure_model_caches_only_existing_folders(tmp_path):
    (tmp_path / ".cache" / "huggingface").mkdir(parents=True)
    got = rc.measure_model_caches(str(tmp_path), size_of=lambda p: 123)
    assert got == {str(tmp_path / ".cache" / "huggingface"): 123}


# ── disk_cleanup_unconfigured ──

def test_disk_cleanup_only_when_read_as_zero():
    r = one(run(disk_cleanup_categories=0), "disk_cleanup_unconfigured")
    assert "0 categories" in r.title and r.target_tab == rc.TAB_LARGE
    assert "disk_cleanup_unconfigured" not in ids(run(disk_cleanup_categories=3))
    assert "disk_cleanup_unconfigured" not in ids(run(disk_cleanup_categories=None))


# ── protection of the owner's model folders ──

@pytest.mark.parametrize("path", [
    HOME + r"\.cache\huggingface\hub\models--x",
    HOME + r"\.cache\huggingface",
    HOME + r"\.cache\codex-runtimes\node",
])
def test_protected_paths_are_never_targeted_or_counted(path):
    assert rc.is_protected(path, HOME)
    # Even a row a scanner mislabelled as Temp/safe/dev/shader must not be offered.
    recs = run(row("temp_files", path, 900 * GB), row("npm_cache", path + r"\npm-cache", 900 * GB),
               row("hf_shader", path + r"\D3DSCache", 900 * GB),
               drives=[("C:\\", 1 * GB, 100 * GB)], old_temp_bytes=900 * GB)
    assert {r.id.split(".")[0] for r in recs} == {"low_free_space"}
    assert one(recs, "low_free_space").target_ids == ()


def test_a_folder_containing_the_models_is_protected_too():
    assert rc.is_protected(HOME + r"\.cache", HOME)
    recs = run(row("temp_files", HOME + r"\.cache", 900 * GB), old_temp_bytes=900 * GB,
               drives=[("C:\\", 1 * GB, 100 * GB)])
    assert {r.id.split(".")[0] for r in recs} == {"low_free_space"}
    assert one(recs, "low_free_space").target_ids == ()


def test_ids_shared_with_a_protected_path_are_dropped_from_targets():
    recs = run(row("temp_files", r"C:\Windows\Temp", 3 * GB),
               row("temp_files", HOME + r"\.cache\huggingface\tmp", 1 * GB),
               drives=[("C:\\", 1 * GB, 100 * GB)])
    assert one(recs, "low_free_space").target_ids == ()


def test_unrelated_cache_is_not_protected():
    assert not rc.is_protected(HOME + r"\.cache\pip", HOME)
    assert not rc.is_protected(r"C:\Windows\Temp", HOME)


# ── engine guarantees ──

def test_engine_imports_nothing_that_deletes():
    src = Path(rc.__file__).read_text(encoding="utf-8")
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    joined = " ".join(names)
    for bad in ("deleter", "clean_safe_runner", "delete_items", "delete_path", "rmtree", "PyQt6"):
        assert bad not in joined, bad
    assert "os.remove" not in src and "unlink(" not in src and "rmdir" not in src


def test_a_failing_rule_does_not_hide_the_others(monkeypatch):
    def boom(inp, rows):
        raise RuntimeError("broken")
    monkeypatch.setattr(rc, "RULES", (boom, rc.rule_disk_cleanup))
    assert ids(run(disk_cleanup_categories=0)) == {"disk_cleanup_unconfigured"}


def test_cross_tab_duplicate_counts_once():
    p = r"C:\Windows\SoftwareDistribution\Download"
    recs = run(row("wu_cache", p, 600 * MB, "caution", tab="wu"),
               row("windowsupdate_orch_cache", p, 600 * MB, "caution", tab="System"))
    assert "wu_leftovers" not in ids(recs)     # 600 MB once, not 1.2 GB


def test_sorted_by_severity_then_size():
    recs = run(row("npm_cache", r"C:\x\npm-cache", 3 * GB, tab="dev"),
               row("wu_cache", WU[0], 2 * GB, "caution", tab="wu"),
               drives=[("C:\\", 1 * GB, 100 * GB)], disk_cleanup_categories=0)
    sev = [rc.SEVERITY_RANK[r.severity] for r in recs]
    assert sev == sorted(sev) and recs[0].severity == rc.HIGH


def test_tooltip_escapes_and_marks_unresearched():
    rec = rc.Recommendation("x", rc.INFO, "<b>t</b>", "<script>e</script>", target_ids=("nope_id",))
    html = rc.tooltip_html(rec, snap(SnapshotRow("nope_id", "<img>", "System", "system", "safe", "safe",
                                                 r"C:\a", 1, True, description="desc <i>")))
    assert "<script>" not in html and "&lt;script&gt;" in html and "&lt;img&gt;" in html
    assert "Not yet researched" in html


# ── dismissals ──

def test_dismissal_expires():
    store = rc.dismiss({}, "wu_leftovers", now=NOW)
    rec = rc.Recommendation("wu_leftovers", rc.WARNING, "t", "e")
    assert rc.visible([rec], store, now=NOW + timedelta(days=29)) == []
    assert rc.visible([rec], store, now=NOW + timedelta(days=31)) == [rec]
    # Expired entries are dropped when the store is rewritten.
    store = rc.dismiss({"old": (NOW - timedelta(days=1)).isoformat(), "bad": 5}, "y", now=NOW)
    assert set(store) == {"y"}


# ── panel (Qt) ──

@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _Config:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


def _recs(n):
    return [rc.Recommendation(f"r{i}", rc.INFO if i else rc.HIGH, f"Title {i}", "ev", "wu", (f"id{i}",),
                              "Go", age_days=i) for i in range(n)]


def test_panel_cards_navigate_and_dismiss(qapp):
    from modules.cleanup.components.recommendations_panel import RecommendationsPanel
    cfg = _Config()
    panel = RecommendationsPanel(config=cfg)
    assert panel.stack.currentIndex() == 1                         # empty before a scan
    got = []
    panel.navigate.connect(lambda tab, ids_, age: got.append((tab, ids_, age)))
    panel.set_recommendations(_recs(7))
    assert len(panel.cards) == 5 and panel.more.text() == "Show 2 more"
    assert panel.cards[0].objectName() == "recoCardHigh" and panel.cards[1].objectName() == "recoCard"
    assert all(c.toolTip() for c in panel.cards)
    panel.cards[1].action.click()
    assert got == [("wu", ["id1"], 1)]
    panel.cards[0].dismiss_btn.click()
    assert "r0" in cfg.get(rc.DISMISS_CONFIG_KEY)
    assert [r.id for r in panel.recommendations()][0] == "r1"
    panel._toggle_more()
    assert len(panel.cards) == 6
    panel.set_recommendations([])
    assert panel.stack.currentIndex() == 1 and panel.empty.text() == "Nothing needs attention: no recommendation applies to this scan."


def test_panel_info_card_has_no_action_button(qapp):
    from modules.cleanup.components.recommendations_panel import RecommendationsPanel
    panel = RecommendationsPanel()
    panel.set_recommendations(run(model_caches={HOME + r"\.cache\huggingface": 30 * GB}))
    assert len(panel.cards) == 1 and panel.cards[0].action is None


def test_panel_refresh_runs_on_worker_and_is_guarded(qapp):
    from PyQt6.QtCore import QThreadPool
    from modules.cleanup.components.recommendations_panel import RecommendationsPanel

    def gather(snapshot, is_cancelled, **kw):
        return rc.RecoInputs(snapshot=snapshot, disk_cleanup_categories=0, now=NOW)

    pool = QThreadPool()
    panel = RecommendationsPanel(thread_pool=pool, gather=gather)
    panel.refresh(snap())
    pool.waitForDone(5000)
    for _ in range(50):
        qapp.processEvents()
        if panel.cards:
            break
    assert [c.rec.id for c in panel.cards] == ["disk_cleanup_unconfigured"]
    assert panel._workers == []
    # A late result after the widget is gone must not crash.
    panel.refresh(snap())
    panel.deleteLater()
    qapp.processEvents()
    pool.waitForDone(5000)
    qapp.processEvents()

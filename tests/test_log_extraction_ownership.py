import json
import os
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.log_viewer import archives as a


@pytest.fixture
def managed(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path / "WindowsTweaker" / "logviewer-extract"


def bundle(tmp_path):
    path = tmp_path / "logs.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("nested/a.log", "a line\n")
    return path


def test_zip_is_owned_and_release_only_removes_managed_folder(tmp_path, managed):
    paths, problem = a.extract_zip(str(bundle(tmp_path)))
    assert not problem
    root = Path(paths[0]).parents[1]
    assert root.parent == managed
    owner = json.loads((root / "owner.json").read_text())
    assert owner["pid"] == os.getpid() and owner["created"] > 0
    a.release_extraction(paths[0])
    assert not root.exists()
    other = tmp_path / "elsewhere"
    other.mkdir()
    a.release_extraction(str(other / "a.log"))
    assert other.exists()


def test_failure_removes_only_automatically_created_extraction(tmp_path, managed):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"invalid")
    assert a.extract_zip(str(bad))[1]
    assert not list(managed.iterdir())
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    a.extract_zip(str(bad), into=str(explicit))
    assert explicit.exists()


def test_stale_sweep_preserves_live_and_unverifiable_owners(tmp_path, managed, monkeypatch):
    import psutil
    managed.mkdir(parents=True)
    folders = []
    for n in range(1, 5):
        folder = managed / f"00000000-0000-0000-0000-{n:012}"
        folder.mkdir()
        (folder / "owner.json").write_text(json.dumps({"pid": n, "created": 10.0}))
        folders.append(folder)
    def process(pid):
        if pid == 1:
            raise psutil.NoSuchProcess(pid)
        if pid == 4:
            raise psutil.AccessDenied(pid)
        return SimpleNamespace(create_time=lambda: 10.0 if pid == 2 else 20.0)
    monkeypatch.setattr(psutil, "Process", process)
    a.cleanup_stale_extractions()
    assert [p.exists() for p in folders] == [False, True, False, True]


def test_viewer_keeps_extraction_for_reload_then_releases_on_replace_and_stop(tmp_path, managed, qapp):
    from modules.log_viewer.log_viewer_module import LogViewerWidget
    w = LogViewerWidget()
    w.open(str(bundle(tmp_path)))
    extracted = Path(w._paths[0])
    w.reload()
    assert extracted.exists()
    plain = tmp_path / "plain.log"
    plain.write_text("plain\n")
    w.open(str(plain))
    assert not extracted.exists()
    w.open(str(bundle(tmp_path)))
    extracted = Path(w._paths[0])
    w.stop()
    assert not extracted.exists()


def test_legacy_leftovers_require_exact_prefix_old_flat_extraction_output(tmp_path, monkeypatch):
    from modules.game_mode.engine.space import scan_logviewer_leftovers
    monkeypatch.setenv("TEMP", str(tmp_path))
    for name, member, old in [("logviewer_cab_good", "CbsPersist_1.cab", True),
                              ("logviewer_cab_new", "CbsPersist_1.cab", False),
                              ("logviewer_cab_mixed", "important.docx", True),
                              ("other_logviewer_cab_", "CbsPersist_1.cab", True)]:
        folder = tmp_path / name
        folder.mkdir()
        file = folder / member
        file.write_text("extracted CBS text\n")
        if old:
            stamp = time.time() - 7200
            os.utime(file, (stamp, stamp))
            os.utime(folder, (stamp, stamp))
    assert [Path(i.path).name for i in scan_logviewer_leftovers().items] == ["logviewer_cab_good"]


@pytest.mark.parametrize("outcome", ["timeout", "launch", "partial"])
def test_cab_failures_remove_partial_output(tmp_path, managed, monkeypatch, outcome):
    import subprocess
    path = tmp_path / "input.cab"
    path.write_bytes(b"MSCFfake")
    expand = tmp_path / "expand.exe"
    expand.touch()
    monkeypatch.setattr(a, "_EXPAND", str(expand))
    def run(args, **kwargs):
        (Path(args[-1]) / "partial.log").write_text("partial")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("expand", 120)
        if outcome == "launch":
            raise OSError("launch refused")
        return SimpleNamespace(returncode=1, stdout="", stderr="corrupt cab")
    monkeypatch.setattr(subprocess, "run", run)
    result, problem = a.extract_cab(str(path))
    assert not result and problem
    assert not list(managed.iterdir())


def test_releasing_one_zip_member_preserves_other_open_members(tmp_path, managed):
    paths, _ = a.extract_zip(str(bundle(tmp_path)))
    first = Path(paths[0])
    second = first.with_name("b.log")
    second.write_text("other")
    a.release_extraction(str(first), keep=[str(second)])
    assert first.exists() and second.exists()
    a.release_extraction(str(second))
    assert not second.exists()


def test_module_start_sweeps_and_navigation_keeps_document_until_stop(tmp_path, managed, monkeypatch, qapp):
    from modules.log_viewer.log_viewer_module import LogViewerModule
    swept = []
    monkeypatch.setattr(a, "cleanup_stale_extractions", lambda: swept.append(True))
    module = LogViewerModule()
    module.on_start(SimpleNamespace(config=None))
    assert swept == [True]
    w = module.create_widget()
    w.open(str(bundle(tmp_path)))
    path = Path(w._paths[0])
    module.on_deactivate()
    assert path.exists()
    w.reload()
    assert path.exists()
    module.on_stop()
    assert not path.exists()


def test_widget_close_releases_extraction(tmp_path, managed, qapp):
    from modules.log_viewer.log_viewer_module import LogViewerWidget
    w = LogViewerWidget()
    w.open(str(bundle(tmp_path)))
    path = Path(w._paths[0])
    w.close()
    assert not path.exists()

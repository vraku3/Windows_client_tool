"""Superseded versions keep both the newest and anything still in use."""
from types import SimpleNamespace

import pytest

from modules.cleanup.cleanup_scanner import scanners_system as scanners


def versions(root, *names):
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        folder = root / name
        folder.mkdir()
        (folder / "payload").write_bytes(b"old-version")
    return root


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("psutil.process_iter", lambda **kwargs: iter(()))
    return tmp_path


def test_numeric_rule_keeps_newest_current_and_non_versions(tmp_path):
    root = versions(tmp_path / "versions", "v8", "v9", "v10", "logs", "v1.bad")
    (root / "v11").write_text("not a directory")
    assert scanners.superseded_versions(root, current=root / "v8") == [str(root / "v9")]


def test_mathworks_marker_keeps_older_current(local):
    root = versions(local / "MathWorks" / "ServiceHost", "v1.1", "v1.2", "v1.3")
    current = str(root / "v1.1").replace("\\", "\\\\")
    (root / "LatestInstall.info").write_text(f"LatestDSInstallRoot {current}\n")
    result = scanners.scan_superseded_app_versions()
    assert [item.path for item in result.items] == [str(root / "v1.2")]
    assert result.total_size == len(b"old-version")
    assert all(item.safety == "caution" and not item.selected for item in result.items)


def test_squirrel_requires_updater_and_app_folders(local):
    root = versions(local / "Discord", "app-9.0", "app-10.0", "logs", "v99")
    versions(local / "Other", "app-1", "app-2")
    (root / "Update.exe").write_bytes(b"")
    assert [i.path for i in scanners.scan_superseded_app_versions().items] == [str(root / "app-9.0")]


def test_running_executable_excludes_old_folder(local, monkeypatch):
    root = versions(local / "Slack", "app-1", "app-2", "app-3")
    (root / "Update.exe").write_bytes(b"")
    process = SimpleNamespace(info={"exe": str(root / "app-1" / "bin" / "Slack.exe").upper()})
    monkeypatch.setattr("psutil.process_iter", lambda **kwargs: iter([process]))
    assert [i.path for i in scanners.scan_superseded_app_versions().items] == [str(root / "app-2")]


@pytest.mark.parametrize("process_name, kept", [("SLACK.EXE", True), ("csrss.exe", False)])
def test_unreadable_executable_only_keeps_matching_old_folder(local, monkeypatch, process_name, kept):
    root = versions(local / "Slack", "app-1", "app-2")
    (root / "Update.exe").write_bytes(b"")
    nested = root / "app-1" / "bin"
    nested.mkdir()
    (nested / "Slack.EXE").write_bytes(b"app")
    process = SimpleNamespace(info={"exe": None, "name": process_name})
    monkeypatch.setattr("psutil.process_iter", lambda **kwargs: iter([process]))
    paths = [i.path for i in scanners.scan_superseded_app_versions().items]
    assert paths == ([] if kept else [str(root / "app-1")])


def test_unreadable_marker_falls_back_and_warns(local, caplog):
    root = versions(local / "MathWorks" / "ServiceHost", "v9", "v10")
    (root / "LatestInstall.info").mkdir()
    assert [i.path for i in scanners.scan_superseded_app_versions().items] == [str(root / "v9")]
    assert "LatestInstall.info" in caplog.text


def test_minimum_age_is_respected(local):
    root = versions(local / "Postman", "app-1", "app-2")
    (root / "Update.exe").write_bytes(b"")
    assert scanners.scan_superseded_app_versions(min_age_days=1).items == []

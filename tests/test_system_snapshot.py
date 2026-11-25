"""Unit tests for System Snapshot and Baseline Differential."""
from core import system_snapshot


def test_system_snapshot_comparison():
    snap_a = system_snapshot.SystemSnapshot(
        label="Baseline A",
        timestamp="2026-11-24T00:00:00",
        os_build="10.0.22631",
        computer_name="PC-WORKSTATION",
        services={
            "wuauserv": {"name": "wuauserv", "display": "Windows Update", "state": "RUNNING"},
            "bits": {"name": "bits", "display": "BITS", "state": "STOPPED"},
        },
        autostarts={
            "hklm\\run\\security": {"hive": "HKLM", "path": "run", "name": "security", "command": "defender.exe"},
        },
        tasks={"googleupdate": "Ready"},
        drivers=["nvlddmkm.sys"],
    )

    snap_b = system_snapshot.SystemSnapshot(
        label="Post-Install B",
        timestamp="2026-11-24T01:00:00",
        os_build="10.0.22631",
        computer_name="PC-WORKSTATION",
        services={
            "wuauserv": {"name": "wuauserv", "display": "Windows Update", "state": "STOPPED"},
            "newservice": {"name": "newservice", "display": "New Software Svc", "state": "RUNNING"},
        },
        autostarts={
            "hklm\\run\\security": {"hive": "HKLM", "path": "run", "name": "security", "command": "defender.exe"},
            "hkcu\\run\\malware": {"hive": "HKCU", "path": "run", "name": "malware", "command": "suspicious.exe"},
        },
        tasks={"googleupdate": "Ready", "newtask": "Ready"},
        drivers=["nvlddmkm.sys", "newdriver.sys"],
    )

    diff = system_snapshot.compare_snapshots(snap_a, snap_b)
    assert diff.has_changes is True

    # Services
    assert len(diff.services_added) == 1
    assert diff.services_added[0]["name"] == "newservice"
    assert len(diff.services_removed) == 1
    assert diff.services_removed[0]["name"] == "bits"
    assert len(diff.services_modified) == 1
    assert diff.services_modified[0]["old_state"] == "RUNNING"
    assert diff.services_modified[0]["new_state"] == "STOPPED"

    # Autostarts
    assert len(diff.autostarts_added) == 1
    assert diff.autostarts_added[0]["name"] == "malware"

    # Tasks
    assert "newtask" in diff.tasks_added

    # Drivers
    assert "newdriver.sys" in diff.drivers_added

    # Markdown format
    md = system_snapshot.format_diff_markdown(diff)
    assert "New Software Svc" in md
    assert "malware" in md
    assert "newdriver.sys" in md


def test_snapshot_dialog_initialization(qapp):
    from ui.snapshot_dialog import SnapshotDialog
    dlg = SnapshotDialog()
    assert dlg.windowTitle() == "System Baseline Snapshot & Differential Analysis"
    assert dlg._list_snapshots.count() == 0

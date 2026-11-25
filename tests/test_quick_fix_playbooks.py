"""Unit tests for Quick Fix triage playbooks."""

from unittest.mock import MagicMock
import pytest

from modules.quick_fix.quick_fix_playbooks import (
    PLAYBOOKS,
    Playbook,
    PlaybookResult,
    get_playbook,
    run_playbook,
)


def test_playbook_catalog():
    assert len(PLAYBOOKS) == 4
    keys = [p.key for p in PLAYBOOKS]
    assert "network_repair" in keys
    assert "wu_reset_full" in keys
    assert "ui_cache_refresh" in keys
    assert "system_vss_refresh" in keys

    pb = get_playbook("network_repair")
    assert pb is not None
    assert pb.title == "Complete Network Stack Repair"
    assert pb.reboot_required is True
    assert len(pb.action_keys) == 5


def test_run_playbook_success():
    logs = []
    steps = []

    def log_cb(line):
        logs.append(line)

    def step_cb(cur, tot, name):
        steps.append((cur, tot, name))

    test_pb = Playbook(
        key="test_pb",
        title="Test Playbook",
        description="Testing execution",
        category="Test",
        icon="🧪",
        action_keys=["flush_dns", "flush_arp"],
    )

    result = run_playbook(
        test_pb,
        output_cb=log_cb,
        step_cb=step_cb,
        create_restore_point_fn=lambda: True,
    )

    assert isinstance(result, PlaybookResult)
    assert result.executed_steps == 2
    assert result.total_steps == 2
    assert len(steps) == 2
    assert any("=== Starting Playbook: Test Playbook ===" in l for l in logs)
    assert any("System Restore Point created successfully" in l for l in logs)


def test_run_playbook_cancellation():
    logs = []
    cancelled = True

    test_pb = Playbook(
        key="test_cancel",
        title="Cancel Playbook",
        description="Testing cancel",
        category="Test",
        icon="🛑",
        action_keys=["flush_dns", "flush_arp"],
    )

    result = run_playbook(
        test_pb,
        output_cb=logs.append,
        is_cancelled=lambda: cancelled,
    )

    assert result.executed_steps == 0
    assert result.success is False
    assert any("cancelled by user" in l for l in logs)

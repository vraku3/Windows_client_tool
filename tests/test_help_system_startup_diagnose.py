"""Explanation coverage for Batch D, without activating machine actions."""
import pytest

from ui.tooltip_audit import audit_modules


@pytest.mark.parametrize("name", [
    "Startup & Boot", "System Management", "Diagnose", "System Health", "Driver Manager",
])
def test_batch_d_has_no_explanation_gaps(tmp_path, name):
    rows = audit_modules(str(tmp_path / name.replace("&", "and")), name)
    assert rows
    assert [(r["module"], r["error"], r["gaps"]) for r in rows
            if r["error"] or r["total"]] == []


def test_service_and_task_help_uses_factual_fallbacks():
    from modules.services_manager.help_text import SERVICES, service_help
    from modules.scheduled_tasks.help_text import TASKS, task_help
    assert SERVICES and all(text.strip() for text in SERVICES.values())
    assert TASKS and all(text.strip() for text in TASKS.values())
    assert "Example vendor description" in service_help({
        "Name": "UnknownVendorSvc", "Description": "Example vendor description"})
    assert "ExampleVendorTask" in task_help("\\ExampleVendorTask", "Vendor's own description")
    assert "Vendor's own description" in task_help("\\ExampleVendorTask", "Vendor's own description")


def test_event_help_is_provider_specific():
    from modules.event_viewer.help_text import event_help
    assert "unclean" in event_help("Microsoft-Windows-Kernel-Power", 41).lower()
    assert not event_help("UnrelatedVendor", 41)
    assert "service" in event_help("Service Control Manager", 7031).lower()

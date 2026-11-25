"""Triage Playbooks (Recipes) for WinClientTool Quick Fix.

Orchestrates sequential execution of related FixActions with step tracking,
live timestamped console streaming, optional restore point creation, and
cancellation support.
"""

from dataclasses import dataclass, field
import logging
import time
from typing import Callable, List, Optional

from modules.quick_fix.fix_actions import (
    ALL_ACTIONS,
    FixAction,
    begin_tracking,
    end_tracking,
)

logger = logging.getLogger(__name__)


@dataclass
class Playbook:
    key: str
    title: str
    description: str
    category: str
    icon: str
    action_keys: List[str]
    reboot_required: bool = False
    estimated_duration: str = "~15s"


@dataclass
class PlaybookResult:
    playbook_key: str
    success: bool
    executed_steps: int
    total_steps: int
    failures: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


PLAYBOOKS: List[Playbook] = [
    Playbook(
        key="network_repair",
        title="Complete Network Stack Repair",
        description="Flushes DNS, resets Winsock catalog and TCP/IP stack, flushes ARP cache, and forces time resync.",
        category="Network",
        icon="🌐",
        action_keys=["flush_dns", "winsock", "tcpip", "flush_arp", "sync_time"],
        reboot_required=True,
        estimated_duration="~20s",
    ),
    Playbook(
        key="wu_reset_full",
        title="Windows Update Total Reset",
        description="Stops update services, clears BITS/Cryptographic state, resets catalog, re-registers core DLLs, and restarts services.",
        category="Windows Update",
        icon="🔄",
        action_keys=["reset_crypto_bits", "wu_reset", "wu_dlls", "wu_scan"],
        reboot_required=False,
        estimated_duration="~45s",
    ),
    Playbook(
        key="ui_cache_refresh",
        title="Desktop UI & Cache Refresh",
        description="Purges corrupted icon cache, thumbnail databases, and gracefully restarts Windows Explorer.",
        category="Cache & UI",
        icon="🎨",
        action_keys=["icon_cache", "thumb_cache", "explorer"],
        reboot_required=False,
        estimated_duration="~10s",
    ),
    Playbook(
        key="system_vss_refresh",
        title="System Service & VSS Refresh",
        description="Restarts Volume Shadow Copy writers, rebuilds performance counters, and verifies WMI repository.",
        category="System Repairs",
        icon="🛡️",
        action_keys=["reset_vss_writers", "perf_reset", "wmi_salvage"],
        reboot_required=False,
        estimated_duration="~35s",
    ),
]


def get_playbook(key: str) -> Optional[Playbook]:
    """Look up a playbook by unique key."""
    for pb in PLAYBOOKS:
        if pb.key == key:
            return pb
    return None


def _get_action_map() -> dict[str, FixAction]:
    """Build a mapping of action key to FixAction instance."""
    return {a.key: a for a in ALL_ACTIONS}


def _run_single_step(
    action: FixAction,
    step_num: int,
    total_steps: int,
    output_cb: Callable[[str], None],
    step_cb: Optional[Callable[[int, int, str], None]] = None,
) -> List[str]:
    """Execute a single FixAction step within a playbook."""
    if step_cb:
        try:
            step_cb(step_num, total_steps, action.title)
        except Exception as e:
            logger.debug("Playbook step_cb error: %s", e)

    output_cb(f"[{time.strftime('%H:%M:%S')}] [Step {step_num}/{total_steps}] >>> {action.title}...")
    begin_tracking()
    step_failures: List[str] = []
    try:
        if action.fn:
            action.fn(output_cb)
        step_failures = end_tracking()
    except Exception as e:
        logger.debug("Exception running step %s: %s", action.key, e)
        output_cb(f"  Error: {e}")
        step_failures = end_tracking()
        step_failures.append(str(e))

    if step_failures:
        output_cb(f"[{time.strftime('%H:%M:%S')}] [Step {step_num}/{total_steps}] ✖ {action.title} reported {len(step_failures)} warning(s)/failure(s)")
    else:
        output_cb(f"[{time.strftime('%H:%M:%S')}] [Step {step_num}/{total_steps}] ✔ {action.title} completed successfully")

    return step_failures


def run_playbook(
    playbook: Playbook,
    output_cb: Callable[[str], None],
    step_cb: Optional[Callable[[int, int, str], None]] = None,
    create_restore_point_fn: Optional[Callable[[], bool]] = None,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> PlaybookResult:
    """Execute a multi-step triage recipe sequentially."""
    start_time = time.monotonic()
    output_cb(f"[{time.strftime('%H:%M:%S')}] === Starting Playbook: {playbook.title} ===")
    output_cb(f"[{time.strftime('%H:%M:%S')}] {playbook.description}")

    # Step 0: Pre-flight restore point if requested
    if create_restore_point_fn:
        output_cb(f"[{time.strftime('%H:%M:%S')}] Creating pre-execution System Restore Point...")
        try:
            rp_ok = create_restore_point_fn()
            if rp_ok:
                output_cb(f"[{time.strftime('%H:%M:%S')}] System Restore Point created successfully.")
            else:
                output_cb(f"[{time.strftime('%H:%M:%S')}] Notice: Restore point creation skipped or refused (proceeding with caution).")
        except Exception as e:
            logger.debug("Error creating restore point for playbook: %s", e)
            output_cb(f"[{time.strftime('%H:%M:%S')}] Notice: Restore point error: {e} (proceeding)")

    action_map = _get_action_map()
    total_steps = len(playbook.action_keys)
    executed_steps = 0
    all_failures: List[str] = []

    for idx, act_key in enumerate(playbook.action_keys, start=1):
        if is_cancelled and is_cancelled():
            output_cb(f"[{time.strftime('%H:%M:%S')}] Playbook cancelled by user at step {idx}/{total_steps}.")
            break

        action = action_map.get(act_key)
        if not action:
            output_cb(f"[{time.strftime('%H:%M:%S')}] Warning: FixAction '{act_key}' not found, skipping step.")
            continue

        step_failures = _run_single_step(action, idx, total_steps, output_cb, step_cb)
        all_failures.extend(step_failures)
        executed_steps += 1

    elapsed = time.monotonic() - start_time
    success = len(all_failures) == 0 and executed_steps == total_steps

    status_str = "SUCCESS" if success else ("CANCELLED" if (is_cancelled and is_cancelled()) else "COMPLETED WITH WARNINGS")
    output_cb(f"[{time.strftime('%H:%M:%S')}] === Playbook {status_str}: {executed_steps}/{total_steps} steps in {elapsed:.1f}s ({len(all_failures)} failure(s)) ===")

    if playbook.reboot_required:
        output_cb(f"[{time.strftime('%H:%M:%S')}] ⚠ A system restart is recommended to complete all repairs.")

    return PlaybookResult(
        playbook_key=playbook.key,
        success=success,
        executed_steps=executed_steps,
        total_steps=total_steps,
        failures=all_failures,
        elapsed_seconds=elapsed,
    )

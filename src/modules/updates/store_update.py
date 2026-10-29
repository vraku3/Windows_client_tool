"""Update selected Microsoft Store offers through winget (Qt-free)."""
from __future__ import annotations

from typing import Callable, List


def update_apps(
    app,
    targets,
    *,
    log: Callable[[str], None] = lambda _line: None,
    is_cancelled: Callable[[], bool] = lambda: False,
    verify: bool = True,
) -> List[tuple]:
    """Upgrade each Store offer by exact winget id and record its outcome.

    Returns `[(AppUpdate, UpdateOutcome)]`. Cancellation is checked between
    packages; an installer already running is never killed.
    """
    from modules.updates.activity_ledger import record_winget_activity
    from modules.updates.winget_updater import UpdateOutcome, install_update

    results = []
    total = len(targets)
    for index, update in enumerate(targets):
        if is_cancelled():
            log("Cancelled.")
            break
        log(f"[{index + 1}/{total}] Updating {update.name} ({update.winget_id})...")
        try:
            outcome = install_update(
                update.winget_id,
                output_cb=log,
                is_cancelled=is_cancelled,
                target_version=update.available_version,
                before_version=update.installed_version,
                verify=verify,
                package_name=update.name,
            )
        except Exception as exc:
            outcome = UpdateOutcome(update.winget_id, "FAILED", str(exc))
        record_winget_activity(app, update, outcome, "store", log)
        log(f"  {outcome.state}: {outcome.detail}")
        results.append((update, outcome))
    return results

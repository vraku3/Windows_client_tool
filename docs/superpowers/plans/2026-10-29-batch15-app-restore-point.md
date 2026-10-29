# Updates batch 15 - optional restore point before app/Store batches

**Goal:** Give winget and Store batches the same explicit safety option the
Windows Update path already has, without slowing down the default workflow.

## Tasks

1. `restore_before.ensure_restore_point()` is a Qt-free helper: off is a
   no-op; on creates `Windows Client Tool - <purpose>` through
   `core.system_restore` and refuses the batch with a reason if Windows cannot
   create it.
2. The App Updates batch, the Store **Update Selected** batch and the shared
   `run_winget_stage` (Run All / unattended) call it before their first install.
3. Settings gains **Create a System Restore point before app and Store update
   batches**, stored as `updates.restore_point_before_app_updates` and off by
   default (winget has no rollback either way). Help text explains the cost.

## Constraints and review focus

- Off by default; a batch must not become slower for users who did not ask.
- A requested checkpoint that cannot be created stops the batch before any
  install; the message names the purpose and the setting to change.
- The existing Windows Update restore-point setting is unchanged.
- No widget work from workers.

## Verification

- `test_restore_before.py` covers off/success/refusal.
- Targeted updates/Store regressions plus module inventory pass; ruff clean.

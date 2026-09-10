# Driver Manager follow-ups ledger

Branch: feat/driver-manager-followups (plain branch off master, not a
separate worktree -- created in the main checkout).

Scope: address the final-review's parked finding (I4: two UI-thread-
blocking calls) and the Minor findings, per the user's "finish it all"
request. Explicitly OUT of scope, left as-is:
- CSV formula injection in _write_inventory_csv -- matches the pre-existing
  _do_export idiom across the whole app; fixing it would be a codebase-wide
  CSV-escaping change, not a driver-pane-scoped one.
- Consolidating list_restore_points with restore_module.py's own duplicate
  reader -- touches a different module entirely (System Restore
  management), was only a "consider" suggestion, not required.

BASE (before Batch A): 7ae6029

Batch A (I4): dispatched (agent a1d4e9506c3475cfd, sonnet -- moves
_show_restore_points and DriverDetailDialog's driver_store_size call onto
background Workers, with widget-lifetime guards). Touches driver_module.py,
driver_detail_dialog.py, their test files.

Batch B (Minors, dispatched after Batch A lands to avoid file conflicts):
1. Surface detect_duplicate_hardware_ids as a "Shared Hardware ID" flag
   (driver_reader.py's fetch_drivers, post-processing step) + explanation
   entry in driver_module.py's _FLAG_EXPLANATIONS.
2. Baseline save collision: confirm before silently overwriting an
   existing (or sanitized-name-colliding) baseline.
3. Unbounded QMessageBox for restore points -- cap at N with "...and M more".
4. Driverless device shows "WHQL Certified: No" instead of "N/A".
5. Promote inline QInputDialog imports to module level (matching the
   QMessageBox promotion already done in Task 9).
6. Remove redundant `import datetime as _dt` in driver_reader.py
   (datetime already imported at module top).
7. tests/test_driver_detail_dialog.py::test_dialog_shows_whql_yes_or_no
   does an unmocked real DriverStore read -- add the missing
   driver_store_size monkeypatch (its sibling test three lines above
   already does this).

Batch A: fix round 1 (hanging test + missing cancelled/error handlers) --
re-review confirmed all addressed, no new breakage. Batch A complete
(commits 7ae6029..7d9cda4).

Batch B: implementer DONE, commits 701c470 + b65906b. Report flagged that
Batch A's concurrent fix-round commit (7d9cda4) did a broad `git add -A`
that swept up 6 of Batch B's 7 in-progress edits (verified byte-identical
to what Batch B intended) -- Batch B's own commit carries the remainder.
Lesson for next time: don't run two implementers concurrently against the
SAME shared checkout (not separate worktrees) even after ordering commits
to avoid direct file conflicts -- a plain `git add -A`/`git commit -am`
sweeps the whole working tree, not just the files a task was told to
touch. Got lucky both agents were careful enough to verify byte-identical
overlap rather than silently corrupting each other's uncommitted work.
Dispatching review (agent aa0e11ea5b9e49162, sonnet) over ee7fb82..b65906b
(spans both Batch A's already-approved fix and Batch B, scoped explicitly
to Batch B's 7 items only).

Batch B: review (agent aa0e11ea5b9e49162, sonnet): Approved, 0
Critical/Important, 2 Minor (no test pins the "real inf_name, not WHQL"
case still showing "No" -- logic correct by inspection; a pre-existing
ruff style nit repeated in the new test -- both deferred). Reviewer
independently re-ran the full 5-file suite (102 passed) rather than
trusting the report, and hand-traced Item 1's flag through fetch_drivers'
full multi-pass dedup machinery.
Batch B: complete (commits ee7fb82..b65906b, review clean, 2 parked minors).

BOTH BATCHES COMPLETE. feat/driver-manager-followups is fully reviewed
clean (I4 fix + all 7 Minor findings from the final review), ready to
merge whenever -- holding off since the user is mid-design-conversation on
the larger vendor-driver-update feature.

# Cleanup Duplicates implementation plan

**Goal:** Implement the supplied Duplicates specification without changing Game Mode or committing.

**Architecture:** Qt-free enumeration, staged SHA-256 comparison, SQLite cache and guarded actions. A virtual tree model and a separate tab consume immutable scan snapshots. Existing cleanup refusal and measured deletion engines remain authoritative.

**Constraints:** Python 3.12, PyQt6, optional Pillow, no new native dependency, no colour literals, UTF-8 without BOM and LF. Scan only on explicit request. Never open placeholders or follow directory reparses. Keep one physical file per group. Cancellation inside hashing and guarded Worker callbacks throughout.

- [x] Add failing temp-directory tests for comparison methods, identity, cancellation, cache and action safety.
- [x] Implement `duplicates/models.py`, `enumerate.py`, `hash_cache.py`, `finder.py`: parallel traversal, snapshot identity, candidate stages, image distance and progress.
- [x] Implement `duplicates/actions.py`: validate selection, fresh comparison, refusal guards, measured permanent deletion, recoverable recycle, collision-safe moves and reports.
- [x] Implement tree model and `_duplicates_tab.py`: settings, filters, progress, selection, preview, actions and exports; integrate after Discover and add hidden imports.
- [x] Run requested pytest command outside repository, read-only Downloads benchmark, and document measured results in CLAUDE.md.

**Review focus:** Changed keepers/targets, path redirection and cloud files, hard links outside scan roots, cancelled action accounting, and late callbacks from a prior run. Tests must use injected actions and never recycle actual user files.

The TreeSize MFT path will be evaluated before enumeration implementation. No build or commit is requested.

Review notes: keep group aggregate calculations out of Qt's per-child filtering path
(the 100,000-row regression caught quadratic work). Use perf_counter for sub-15 ms
stage timing. Explicitly initialize COM on the action thread, force shell recycling
with FOFX_RECYCLEONDELETE, and record measured action history before Qt can suppress a
cancelled result. Treat Pillow decompression refusals and optional SQLite failures
as per-file/cache issues. Read-only Downloads timings and limitations are recorded
in CLAUDE.md. The Codex CLI could not resolve its home directory; review was performed
in this session, without substitute agents. The review skill's external lessons
file was absent and is outside the writable workspace; lessons are retained here.

Final validation: 48 tests passed in the requested Duplicates / Cleanup module /
frozen-colour selection (41 new parametrized cases across four files). A 100,000-row
model/filter test, Qt model tester, deleted-widget callbacks, cancelled action
accounting, protected paths, forced recoverable shell flags and definitive byte
verification are covered. No user files were recycled or deleted. Downloads was
only read; optional benchmark cache was stored in the temp directory. No commit.

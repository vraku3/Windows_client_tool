# Updates batch 20 - export and import update exclusions

**Goal:** Move a curated exclusion list between machines without hand-editing
config.json.

## Tasks

1. `core/blocklist.export_json()` writes `{version: 1, exclusions: [...]}` with
   the normalized pattern, reason, origin, dates and author.
2. `core.blocklist.import_json()` accepts that wrapper or a bare list, rejects
   malformed JSON/non-lists/all-invalid entries with a readable ValueError, and
   returns normalized entries.
3. The exclusions editor gains **Export...** and **Import...**. Import asks
   Yes = merge (same pattern case-insensitively replaces fields) / No = replace
   all / Cancel, then saves and records a timeline event.

## Constraints and review focus

- A malformed file is an error, never an empty successful import.
- Merge never duplicates a pattern case-insensitively; replace is explicit.
- Export contains only exclusions, no machine paths or user data beyond the
  existing author field.
- No network access.

## Verification

- `test_blocklist.py` covers round trip, bare lists, malformed/empty payloads.
- Existing exclusions UI tests plus module inventory pass; ruff clean.

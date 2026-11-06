# TreeSize MTP Implementation Plan

**Goal:** Scan connected phones and cameras through Shell COM into the existing NodeStore.
**Spec:** User request in this session, 2026-11-06.
**Architecture:** Qt-free BFS engine, a COM-initialized QRunnable, and existing TreeSize views. One device root contains all of its storage folders. No MTP writes.

- [ ] Add fake Shell COM regression tests for discovery, multiple storages, BFS sizes/dates, batching, cancellation and unreadable folders. Run them to confirm the feature is missing.
- [ ] Implement scan/mtp_scanner.py and ui/mtp_worker.py. Keep COM objects on their creating thread, cap error details at 100, and report partial results accurately.
- [ ] Integrate DriveList, target menu, scan routing and result capacity. Preserve shell paths in the address dropdown so Refresh scans the same device; disable Pause and local filesystem operations for MTP.
- [ ] Register frozen imports in root pyinstaller_common.py. Run focused engine/UI tests, existing TreeSize tests, main import and live device discovery. If a phone is connected, perform a read-only engine scan and compare worker totals.

Review focus: localized device names and dates; missing metadata; disconnected/unreadable folders; multiple storages; names that resemble local paths. Existing unrelated workspace changes must remain untouched. No build, deployment or commit is requested.

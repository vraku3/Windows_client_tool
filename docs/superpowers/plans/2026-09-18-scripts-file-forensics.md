# Scripts Tab + File Forensics Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new "Scripts" sidebar entry (a `CompositeModule`) hosting a
professional, redesigned rebuild of the user's `Find-FileCreator.ps1`:
search a folder for files, see who has each one open, guess who created
it, live-watch a folder for new files as they appear, check SHA256/
VirusTotal reputation, flag unsigned creators, and keep a persistent
history — all built on this app's existing process-forensics engine
rather than reimplementing any of it.

**Architecture:** A Qt-free `engine/` package (metadata, device-path
translation, locking-process detection via `core.procengine.findref`,
creator-time correlation via `core.procengine.ntquery`/`details`,
reputation via `core.virustotal_client`, and a native
`ReadDirectoryChangesW` folder watcher) plus one `BaseModule` (the UI),
hosted by a new `CompositeModule` hub. Same `scan/`+`store/`-style split
TreeSize and Monitor Control already use.

**Tech Stack:** PyQt6, pywin32 (`win32file`, `win32event`, `win32security`
via the existing `treesize.scan.owners.OwnerResolver`), the existing
`core.procengine` package, `core.virustotal_client`.

**Spec:** `docs/superpowers/specs/2026-09-18-scripts-file-forensics-design.md`

## Global Constraints

- The 5 modules already in this app that File Forensics reuses
  (`core.procengine.findref`, `core.procengine.ntquery`,
  `core.procengine.details`, `core.procengine.signatures`,
  `core.virustotal_client`, `core.procengine.actions`,
  `modules.treesize.scan.owners`) must not be modified — this plan only
  adds new files that call into them.
- Every `engine/` module is Qt-free — no `PyQt6` imports anywhere under
  `src/modules/file_forensics/engine/`.
- A refusal is never collapsed into "nothing found" — every engine
  function that can be refused (handle enumeration, module list, ACL
  read) reports that distinctly, matching `FindReport`'s own convention
  the rest of `procengine` already follows.
- The live watcher's cancellation must actually interrupt the blocking
  wait, not just flip `worker.is_cancelled` — the exact bug class this
  session already found and fixed twice (DISM, winget) elsewhere in this
  app. `FolderWatcher.stop()` must be called explicitly by the UI's
  cancel path, in addition to `worker.cancel()`.
- Sidebar count goes from 28 to 29 (a genuinely new entry, not a fold).

---

### Task 1: File metadata engine

**Files:**
- Create: `src/modules/file_forensics/__init__.py` (empty)
- Create: `src/modules/file_forensics/engine/__init__.py` (empty)
- Create: `src/modules/file_forensics/engine/file_metadata.py`
- Test: `tests/test_file_forensics_metadata.py`

**Interfaces:**
- Produces: `FileMetadata` dataclass (`path`, `size`, `created`,
  `modified`, `accessed`, `owner`, `read_only`), `read_metadata(path: str)
  -> FileMetadata`.

- [ ] **Step 1: Write the failing test**

```python
import os
import time

from modules.file_forensics.engine.file_metadata import read_metadata


def test_reads_real_metadata_for_a_real_file(tmp_path):
    f = tmp_path / "probe.txt"
    f.write_text("hello")

    meta = read_metadata(str(f))

    assert meta.path == str(f)
    assert meta.size == 5
    assert meta.read_only is False
    assert meta.created is not None
    assert meta.modified is not None


def test_a_missing_file_raises_file_not_found(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        read_metadata(str(tmp_path / "does_not_exist.txt"))


def test_owner_lookup_failure_is_an_empty_string_not_an_exception(tmp_path, monkeypatch):
    f = tmp_path / "probe2.txt"
    f.write_text("x")
    import modules.file_forensics.engine.file_metadata as fm

    class _BoomResolver:
        def for_path(self, path):
            raise OSError("no ACL")

    monkeypatch.setattr(fm, "_owner_resolver", _BoomResolver())
    meta = fm.read_metadata(str(f))
    assert meta.owner == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_metadata.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.file_forensics'`

- [ ] **Step 3: Write the implementation**

```python
"""File metadata for File Forensics -- size, times, owner, read-only.

Owner lookup reuses `treesize.scan.owners.OwnerResolver` rather than a
second win32security wrapper: it already handles pywin32 being absent,
caches the SID->name round trip, and never raises on an unreadable ACL.
"""
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from modules.treesize.scan.owners import OwnerResolver

_owner_resolver = OwnerResolver()


@dataclass(frozen=True)
class FileMetadata:
    path: str
    size: int
    created: datetime
    modified: datetime
    accessed: datetime
    owner: str
    read_only: bool


def read_metadata(path: str) -> FileMetadata:
    """Everything about one file. Raises FileNotFoundError if it's gone --
    a real possibility for a temp file between being listed and being
    inspected, and the caller (analysis.py) is the one that decides
    whether that's worth reporting or silently skipping."""
    st = os.stat(path)
    try:
        owner = _owner_resolver.for_path(path)
    except Exception:  # noqa: BLE001 -- an unreadable ACL is ordinary, per owners.py's own docstring
        owner = ""
    return FileMetadata(
        path=path,
        size=st.st_size,
        created=datetime.fromtimestamp(st.st_ctime),
        modified=datetime.fromtimestamp(st.st_mtime),
        accessed=datetime.fromtimestamp(st.st_atime),
        owner=owner,
        read_only=not os.access(path, os.W_OK),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_metadata.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modules/file_forensics/__init__.py src/modules/file_forensics/engine/__init__.py src/modules/file_forensics/engine/file_metadata.py tests/test_file_forensics_metadata.py
git commit -m "feat(file-forensics): add file metadata engine"
```

---

### Task 2: NT device-path translation

**Files:**
- Create: `src/modules/file_forensics/engine/device_paths.py`
- Test: `tests/test_file_forensics_device_paths.py`

**Interfaces:**
- Produces: `to_device_path(win_path: str) -> Optional[str]`

- [ ] **Step 1: Write the failing test**

```python
from modules.file_forensics.engine import device_paths


def test_translates_a_real_drive_letter_path(monkeypatch):
    monkeypatch.setattr(
        device_paths, "_query_dos_device",
        lambda drive: r"\Device\HarddiskVolume3" if drive == "C:" else None,
    )
    result = device_paths.to_device_path(r"C:\Users\me\file.txt")
    assert result == r"\Device\HarddiskVolume3\Users\me\file.txt"


def test_a_path_with_no_drive_letter_returns_none():
    assert device_paths.to_device_path(r"\\server\share\file.txt") is None


def test_a_drive_query_manager_cannot_answer_returns_none(monkeypatch):
    monkeypatch.setattr(device_paths, "_query_dos_device", lambda drive: None)
    assert device_paths.to_device_path(r"Z:\ghost\file.txt") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_device_paths.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
"""Drive-letter path -> NT device path, e.g. 'C:\\x' -> '\\Device\\
HarddiskVolume3\\x'.

`core.procengine.findref.find()` matches against handle names in the
kernel's OWN representation, never drive letters -- so searching for a
specific file means translating the search target once, rather than
reverse-translating every one of findref's results.
"""
import ctypes
from typing import Optional

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _query_dos_device(drive: str) -> Optional[str]:
    """'C:' -> '\\Device\\HarddiskVolume3', or None if it cannot be read."""
    buf = ctypes.create_unicode_buffer(260)
    length = _kernel32.QueryDosDeviceW(drive, buf, 260)
    if length == 0:
        return None
    return buf.value


def to_device_path(win_path: str) -> Optional[str]:
    if len(win_path) < 3 or win_path[1] != ":":
        return None  # UNC paths, or anything with no drive letter
    device = _query_dos_device(win_path[:2])
    if device is None:
        return None
    return device + win_path[2:]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_device_paths.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modules/file_forensics/engine/device_paths.py tests/test_file_forensics_device_paths.py
git commit -m "feat(file-forensics): add NT device-path translation"
```

---

### Task 3: Locking-process detection

**Files:**
- Create: `src/modules/file_forensics/engine/locking_processes.py`
- Test: `tests/test_file_forensics_locking.py`

**Interfaces:**
- Consumes: `core.procengine.findref.find`, `device_paths.to_device_path`
- Produces: `LockingProcess` dataclass (`pid`, `process`, `type_name`),
  `find_locking_processes(path: str) -> Tuple[List[LockingProcess], str]`
  (the second value is `FindReport.summary()`, always -- the honest
  disclosure travels with the answer, not just on request).

- [ ] **Step 1: Write the failing test**

```python
from modules.file_forensics.engine import locking_processes as lp


def test_translates_the_path_and_returns_matches(monkeypatch):
    monkeypatch.setattr(lp, "to_device_path",
                        lambda p: r"\Device\HarddiskVolume3\Users\me\f.txt")

    class _FakeMatch:
        pid = 1234
        process = "notepad"
        kind = "Handle"
        type_name = "File"
        detail = r"\Device\HarddiskVolume3\Users\me\f.txt"

    class _FakeReport:
        matches = [_FakeMatch()]
        def summary(self):
            return "1 matches in 250 processes"

    monkeypatch.setattr(lp, "find", lambda *a, **k: _FakeReport())

    procs, summary = lp.find_locking_processes(r"C:\Users\me\f.txt")

    assert len(procs) == 1
    assert procs[0].pid == 1234
    assert procs[0].process == "notepad"
    assert "1 matches" in summary


def test_a_path_that_cannot_be_translated_still_returns_a_report(monkeypatch):
    monkeypatch.setattr(lp, "to_device_path", lambda p: None)
    procs, summary = lp.find_locking_processes(r"\\server\share\f.txt")
    assert procs == []
    assert "could not be translated" in summary.lower()


def test_only_file_type_matches_are_kept(monkeypatch):
    monkeypatch.setattr(lp, "to_device_path", lambda p: r"\Device\HarddiskVolume3\f.txt")

    class _RegMatch:
        pid = 1
        process = "svchost"
        kind = "Handle"
        type_name = "Key"
        detail = "HKLM\\Software"

    class _FileMatch:
        pid = 2
        process = "notepad"
        kind = "Handle"
        type_name = "File"
        detail = r"\Device\HarddiskVolume3\f.txt"

    class _FakeReport:
        matches = [_RegMatch(), _FileMatch()]
        def summary(self):
            return "ok"

    monkeypatch.setattr(lp, "find", lambda *a, **k: _FakeReport())
    procs, _ = lp.find_locking_processes(r"C:\f.txt")
    assert len(procs) == 1
    assert procs[0].process == "notepad"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_locking.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
"""Which processes have a specific file open right now.

Wraps `core.procengine.findref.find()` -- the same native NT
handle-enumeration Process Explorer's own Ctrl+F uses -- scoped to one
exact file rather than a free-text search, and filtered to `File`-typed
handle matches only (a DLL/module match is a different, separate
question this app already answers elsewhere).
"""
from dataclasses import dataclass
from typing import List, Tuple

from core.procengine.findref import find
from .device_paths import to_device_path


@dataclass(frozen=True)
class LockingProcess:
    pid: int
    process: str
    type_name: str


def find_locking_processes(path: str) -> Tuple[List[LockingProcess], str]:
    """Returns (processes holding `path` open, an honest summary of what
    could be searched) -- the summary travels with the answer always, not
    only when something was refused, so a caller never has to remember to
    ask for it separately."""
    device_path = to_device_path(path)
    if device_path is None:
        return [], ("The file's path could not be translated to a form "
                    "the handle search understands (not on a local drive?).")

    report = find(device_path, handles=True, modules=False)
    procs = [
        LockingProcess(pid=m.pid, process=m.process, type_name=m.type_name)
        for m in report.matches
        if m.type_name == "File"
    ]
    return procs, report.summary()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_locking.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modules/file_forensics/engine/locking_processes.py tests/test_file_forensics_locking.py
git commit -m "feat(file-forensics): add locking-process detection via findref"
```

---

### Task 4: Creator-time correlation heuristic

**Files:**
- Create: `src/modules/file_forensics/engine/creator_heuristic.py`
- Test: `tests/test_file_forensics_creator.py`

**Interfaces:**
- Consumes: `core.procengine.ntquery.system_processes`,
  `core.procengine.details.resolve`
- Produces: `CreatorCandidate` dataclass (`pid`, `name`, `path`, `user`,
  `started_at`, `delta_seconds`), `find_creator_candidates(created_at:
  datetime, tolerance_seconds: float = 5.0) -> List[CreatorCandidate]`
  (sorted by `abs(delta_seconds)` ascending -- index 0 is the best guess).

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime, timedelta

from modules.file_forensics.engine import creator_heuristic as ch


class _FakeProc:
    def __init__(self, pid, name, create_time_filetime):
        self.pid = pid
        self.name = name
        self.create_time = create_time_filetime


def _filetime_for(dt: datetime) -> int:
    """Inverse of ch._filetime_to_datetime, for building test fixtures."""
    delta = dt - ch._FILETIME_EPOCH.replace(tzinfo=None)
    return int(delta.total_seconds() * 10_000_000)


def test_finds_a_process_started_close_to_file_creation(monkeypatch):
    created = datetime(2026, 1, 1, 12, 0, 0)
    close_proc = _FakeProc(111, "installer.exe", _filetime_for(created + timedelta(seconds=2)))
    far_proc = _FakeProc(222, "explorer.exe", _filetime_for(created - timedelta(minutes=10)))

    monkeypatch.setattr(ch, "system_processes", lambda: [close_proc, far_proc])
    monkeypatch.setattr(ch, "resolve", lambda pid: type(
        "D", (), {"path": f"C:\\{pid}.exe", "user": "TESTUSER"})())

    candidates = ch.find_creator_candidates(created, tolerance_seconds=5.0)

    assert len(candidates) == 1
    assert candidates[0].pid == 111
    assert candidates[0].name == "installer.exe"
    assert abs(candidates[0].delta_seconds - 2.0) < 0.01


def test_multiple_candidates_are_sorted_by_closeness(monkeypatch):
    created = datetime(2026, 1, 1, 12, 0, 0)
    a = _FakeProc(1, "a.exe", _filetime_for(created + timedelta(seconds=4)))
    b = _FakeProc(2, "b.exe", _filetime_for(created + timedelta(seconds=1)))

    monkeypatch.setattr(ch, "system_processes", lambda: [a, b])
    monkeypatch.setattr(ch, "resolve", lambda pid: type(
        "D", (), {"path": None, "user": None})())

    candidates = ch.find_creator_candidates(created, tolerance_seconds=5.0)

    assert [c.pid for c in candidates] == [2, 1]


def test_no_candidates_within_tolerance_returns_empty(monkeypatch):
    created = datetime(2026, 1, 1, 12, 0, 0)
    far = _FakeProc(1, "far.exe", _filetime_for(created - timedelta(hours=1)))
    monkeypatch.setattr(ch, "system_processes", lambda: [far])
    monkeypatch.setattr(ch, "resolve", lambda pid: type(
        "D", (), {"path": None, "user": None})())

    assert ch.find_creator_candidates(created, tolerance_seconds=5.0) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_creator.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
"""Guess which process created a file, by correlating the file's creation
time against every process's start time -- the same heuristic
Find-FileCreator.ps1 used manually, on this app's own fast process
engine instead of one-at-a-time Get-Process calls.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from core.procengine.details import resolve
from core.procengine.ntquery import system_processes

#: Same conversion procengine/columns.py already uses for display --
#: reused here for arithmetic, since Windows FILETIME is 100ns ticks
#: since 1601, not a Python timestamp.
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class CreatorCandidate:
    pid: int
    name: str
    path: Optional[str]
    user: Optional[str]
    started_at: datetime
    delta_seconds: float


def _filetime_to_datetime(value: int) -> Optional[datetime]:
    if not value:
        return None
    try:
        return (_FILETIME_EPOCH + timedelta(microseconds=value // 10)).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def find_creator_candidates(created_at: datetime,
                            tolerance_seconds: float = 5.0
                            ) -> List[CreatorCandidate]:
    candidates = []
    for proc in system_processes():
        started = _filetime_to_datetime(proc.create_time)
        if started is None:
            continue
        delta = (started - created_at).total_seconds()
        if abs(delta) > tolerance_seconds:
            continue
        details = resolve(proc.pid)
        candidates.append(CreatorCandidate(
            pid=proc.pid, name=proc.name, path=details.path,
            user=details.user, started_at=started, delta_seconds=delta,
        ))
    candidates.sort(key=lambda c: abs(c.delta_seconds))
    return candidates
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_creator.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modules/file_forensics/engine/creator_heuristic.py tests/test_file_forensics_creator.py
git commit -m "feat(file-forensics): add creator-time correlation heuristic"
```

---

### Task 5: Reputation lookup (SHA256 + VirusTotal) + signature check

**Files:**
- Create: `src/modules/file_forensics/engine/reputation.py`
- Test: `tests/test_file_forensics_reputation.py`

**Interfaces:**
- Consumes: `core.virustotal_client.compute_sha256`,
  `core.virustotal_client.VTClient`, `core.procengine.signatures.verify_signature`
- Produces: `check_signature(path: str) -> SignatureFacts` (thin re-export),
  `check_reputation(path: str, api_key: str) -> Optional[VTResult]`
  (`None` when there's no API key or the hash can't be computed --
  distinct from `VTResult(found=False, ...)`, which means VT itself
  answered "unknown").

- [ ] **Step 1: Write the failing test**

```python
from modules.file_forensics.engine import reputation as rep


def test_no_api_key_returns_none_without_calling_vt(monkeypatch):
    called = []
    monkeypatch.setattr(rep, "compute_sha256", lambda p: called.append(p) or "abc")
    result = rep.check_reputation("C:\\f.exe", api_key="")
    assert result is None
    assert called == []  # never even hashed -- no point


def test_a_hash_failure_returns_none(monkeypatch):
    monkeypatch.setattr(rep, "compute_sha256", lambda p: None)
    result = rep.check_reputation("C:\\f.exe", api_key="realkey")
    assert result is None


def test_a_real_key_and_hash_calls_vt_client(monkeypatch):
    monkeypatch.setattr(rep, "compute_sha256", lambda p: "deadbeef")

    class _FakeResult:
        found = True
        malicious = 0

    class _FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key
        def check(self, sha256):
            assert sha256 == "deadbeef"
            return _FakeResult()

    monkeypatch.setattr(rep, "VTClient", _FakeClient)
    result = rep.check_reputation("C:\\f.exe", api_key="realkey")
    assert result.found is True


def test_check_signature_delegates_to_procengine(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(rep, "verify_signature", lambda path: sentinel)
    assert rep.check_signature("C:\\f.exe") is sentinel
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_reputation.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
"""Reputation and signature checks for a file -- both already built
elsewhere in this app (VirusTotal for Process Explorer, Authenticode
verification for the process engine generally); this only wires them in
for a file path found by File Forensics rather than a running process.
"""
from typing import Optional

from core.procengine.signatures import SignatureFacts, verify_signature
from core.virustotal_client import VTClient, VTResult, compute_sha256


def check_signature(path: str) -> SignatureFacts:
    return verify_signature(path)


def check_reputation(path: str, api_key: str) -> Optional[VTResult]:
    """`None` means "we didn't ask" (no key, or couldn't hash) -- distinct
    from `VTResult(found=False)`, which means VT itself said unknown."""
    if not api_key:
        return None
    sha256 = compute_sha256(path)
    if not sha256:
        return None
    return VTClient(api_key=api_key).check(sha256)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_reputation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modules/file_forensics/engine/reputation.py tests/test_file_forensics_reputation.py
git commit -m "feat(file-forensics): add reputation and signature check wrappers"
```

---

### Task 6: Analysis orchestrator

**Files:**
- Create: `src/modules/file_forensics/engine/analysis.py`
- Test: `tests/test_file_forensics_analysis.py`

**Interfaces:**
- Consumes: `file_metadata.read_metadata`,
  `locking_processes.find_locking_processes`,
  `creator_heuristic.find_creator_candidates`,
  `reputation.check_signature`, `reputation.check_reputation`
- Produces: `FileAnalysis` dataclass (`metadata`, `locking_processes`,
  `locking_summary`, `creator_candidates`, `top_creator_signature`,
  `reputation`), `analyze(path: str, tolerance_seconds: float = 5.0,
  vt_api_key: str = "") -> FileAnalysis`

> **Post-review correction (applied during Task 6's own fix round,
> before Task 7 began):** the field was originally named
> `top_creator_signed: Optional[bool]`, storing only
> `SignatureFacts.signed`. The task reviewer found this collapses three
> distinct answers -- genuinely unsigned, signature present but invalid/
> tampered, and "could not verify" (a refusal) -- into the same `False`,
> directly conflicting with this plan's own global constraint that a
> refusal is never collapsed into a not-found-equivalent answer. Fixed by
> storing the whole `SignatureFacts` object instead of the pre-collapsed
> bool. Every code sample below (Task 6's own implementation and every
> later task's test fixtures) already reflects the corrected
> `top_creator_signature: Optional[SignatureFacts]` shape.

- [ ] **Step 1: Write the failing test**

```python
from datetime import datetime

from modules.file_forensics.engine import analysis


def test_orchestrates_every_engine_piece(monkeypatch):
    class _Meta:
        created = datetime(2026, 1, 1)

    monkeypatch.setattr(analysis, "read_metadata", lambda p: _Meta())
    monkeypatch.setattr(analysis, "find_locking_processes",
                        lambda p: (["proc1"], "1 matches"))

    class _Candidate:
        path = "C:\\creator.exe"

    monkeypatch.setattr(analysis, "find_creator_candidates",
                        lambda created, tolerance_seconds: [_Candidate()])

    class _SigFacts:
        signed = False

    fake_sig = _SigFacts()
    monkeypatch.setattr(analysis, "check_signature", lambda path: fake_sig)
    monkeypatch.setattr(analysis, "check_reputation", lambda path, api_key: None)

    result = analysis.analyze("C:\\found.txt", vt_api_key="")

    assert result.metadata is not None
    assert result.locking_processes == ["proc1"]
    assert result.locking_summary == "1 matches"
    assert len(result.creator_candidates) == 1
    assert result.top_creator_signature is fake_sig
    assert result.reputation is None


def test_no_creator_candidates_means_no_signature_check(monkeypatch):
    class _Meta:
        created = datetime(2026, 1, 1)

    monkeypatch.setattr(analysis, "read_metadata", lambda p: _Meta())
    monkeypatch.setattr(analysis, "find_locking_processes", lambda p: ([], "ok"))
    monkeypatch.setattr(analysis, "find_creator_candidates",
                        lambda created, tolerance_seconds: [])

    called = []
    monkeypatch.setattr(analysis, "check_signature", lambda path: called.append(path))
    monkeypatch.setattr(analysis, "check_reputation", lambda path, api_key: None)

    result = analysis.analyze("C:\\found.txt")

    assert result.top_creator_signature is None
    assert called == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_analysis.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
"""One found file -> everything File Forensics knows about it.

The single seam the UI calls, whether the file came from a manual search
or a live-watch detection -- both paths must produce identically-shaped
results, and this is what guarantees that.
"""
from dataclasses import dataclass
from typing import List, Optional

from .creator_heuristic import CreatorCandidate, find_creator_candidates
from .file_metadata import FileMetadata, read_metadata
from .locking_processes import LockingProcess, find_locking_processes
from .reputation import SignatureFacts, check_reputation, check_signature


@dataclass(frozen=True)
class FileAnalysis:
    metadata: FileMetadata
    locking_processes: List[LockingProcess]
    locking_summary: str
    creator_candidates: List[CreatorCandidate]
    #: None when there was no creator candidate to check at all, OR when
    #: the top candidate's own process path could not be resolved. The
    #: full SignatureFacts is kept (not just .signed) so a caller can
    #: tell "genuinely unsigned" apart from "signature invalid/tampered"
    #: apart from "could not verify" -- collapsing those into one bool
    #: was the exact refusal-hiding bug this field's first draft had.
    top_creator_signature: Optional[SignatureFacts]
    reputation: object  # VTResult | None


def analyze(path: str, tolerance_seconds: float = 5.0,
            vt_api_key: str = "") -> FileAnalysis:
    metadata = read_metadata(path)
    locking, locking_summary = find_locking_processes(path)
    candidates = find_creator_candidates(metadata.created, tolerance_seconds)

    top_creator_signature = None
    reputation = None
    if candidates and candidates[0].path:
        top_creator_signature = check_signature(candidates[0].path)
        reputation = check_reputation(candidates[0].path, vt_api_key)

    return FileAnalysis(
        metadata=metadata,
        locking_processes=locking,
        locking_summary=locking_summary,
        creator_candidates=candidates,
        top_creator_signature=top_creator_signature,
        reputation=reputation,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_analysis.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modules/file_forensics/engine/analysis.py tests/test_file_forensics_analysis.py
git commit -m "feat(file-forensics): add analysis orchestrator"
```

---

### Task 7: Native folder watcher

**Files:**
- Create: `src/modules/file_forensics/engine/folder_watcher.py`
- Test: `tests/test_file_forensics_folder_watcher.py`

**Interfaces:**
- Produces: `FolderWatcher(path: str, on_created: Callable[[str], None],
  recursive: bool = False)` with `.run(worker)` (blocking, meant to be
  passed straight to a `Worker`) and `.stop()` (thread-safe, callable from
  any thread, actually interrupts `.run()`'s blocking wait).

- [ ] **Step 1: Write the failing test**

```python
import os
import threading
import time

import pytest

from modules.file_forensics.engine.folder_watcher import FolderWatcher


class _FakeWorker:
    is_cancelled = False


def test_detects_a_real_file_created_in_the_watched_folder(tmp_path):
    detected = []
    watcher = FolderWatcher(str(tmp_path), on_created=detected.append)

    def _create_soon():
        time.sleep(0.3)
        (tmp_path / "new_file.txt").write_text("hello")

    creator_thread = threading.Thread(target=_create_soon)
    run_thread = threading.Thread(target=watcher.run, args=(_FakeWorker(),))

    creator_thread.start()
    run_thread.start()
    creator_thread.join(timeout=5)
    time.sleep(0.5)  # let the watcher's event fire and callback run
    watcher.stop()
    run_thread.join(timeout=5)

    assert not run_thread.is_alive(), "watcher.run() did not exit after stop()"
    assert any("new_file.txt" in path for path in detected)


def test_stop_before_any_event_returns_promptly(tmp_path):
    watcher = FolderWatcher(str(tmp_path), on_created=lambda p: None)
    run_thread = threading.Thread(target=watcher.run, args=(_FakeWorker(),))
    run_thread.start()
    time.sleep(0.2)  # let it reach the wait

    started = time.monotonic()
    watcher.stop()
    run_thread.join(timeout=5)
    elapsed = time.monotonic() - started

    assert not run_thread.is_alive()
    assert elapsed < 2.0, f"stop() took {elapsed:.1f}s to take effect"


def test_a_nonexistent_folder_raises_immediately():
    watcher = FolderWatcher(r"Z:\this\does\not\exist\at\all", on_created=lambda p: None)
    with pytest.raises(OSError):
        watcher.run(_FakeWorker())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_folder_watcher.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
"""A native, cancellable "tell me the instant a new file appears in this
folder" watcher -- `ReadDirectoryChangesW` with overlapped I/O, not
`QFileSystemWatcher` + directory diffing, which can miss a fast
create/delete pair on a churning temp folder.

`worker.is_cancelled` alone cannot stop this: the blocking wait is a
native WaitForMultipleObjects call, which nothing about `Worker.cancel()`
touches. `stop()` sets a real Win32 event this class also waits on, the
same class of fix this session already applied to DISM and winget calls
that could not be cancelled mid-operation.
"""
import os
from typing import Callable

import pywintypes
import win32con
import win32event
import win32file

FILE_LIST_DIRECTORY = 0x0001
_BUFFER_SIZE = 64 * 1024


class FolderWatcher:
    def __init__(self, path: str, on_created: Callable[[str], None],
                recursive: bool = False):
        self._path = path
        self._on_created = on_created
        self._recursive = recursive
        self._stop_event = win32event.CreateEvent(None, True, False, None)

    def run(self, worker) -> None:
        handle = win32file.CreateFile(
            self._path, FILE_LIST_DIRECTORY,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE | win32con.FILE_SHARE_DELETE,
            None, win32con.OPEN_EXISTING,
            win32con.FILE_FLAG_BACKUP_SEMANTICS | win32con.FILE_FLAG_OVERLAPPED,
            None,
        )
        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
        buf = win32file.AllocateReadBuffer(_BUFFER_SIZE)
        try:
            while not worker.is_cancelled:
                win32file.ReadDirectoryChangesW(
                    handle, buf, self._recursive,
                    win32con.FILE_NOTIFY_CHANGE_FILE_NAME,
                    overlapped,
                )
                rc = win32event.WaitForMultipleObjects(
                    [overlapped.hEvent, self._stop_event], False, win32event.INFINITE)
                if rc == win32event.WAIT_OBJECT_0 + 1:
                    break  # stop() was called
                nbytes = win32file.GetOverlappedResult(handle, overlapped, False)
                for action, filename in win32file.FILE_NOTIFY_INFORMATION(buf, nbytes):
                    if action == 1:  # FILE_ACTION_ADDED
                        self._on_created(os.path.join(self._path, filename))
        finally:
            win32file.CloseHandle(handle)

    def stop(self) -> None:
        """Thread-safe -- call from the UI thread while run() is blocking
        on a worker thread."""
        win32event.SetEvent(self._stop_event)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_folder_watcher.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modules/file_forensics/engine/folder_watcher.py tests/test_file_forensics_folder_watcher.py
git commit -m "feat(file-forensics): add native cancellable folder watcher"
```

---

### Task 8: Persistent history log

**Files:**
- Create: `src/modules/file_forensics/history_log.py`
- Test: `tests/test_file_forensics_history_log.py`

**Interfaces:**
- Produces: `record(entry: dict) -> None`, `recent(limit: int = 50) ->
  List[dict]`, `search(query: str, limit: int = 50) -> List[dict]`

- [ ] **Step 1: Write the failing test**

```python
from modules.file_forensics import history_log


def test_record_then_recent_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(history_log, "_history_path",
                        lambda: str(tmp_path / "history.json"))

    history_log.record({"path": r"C:\a.txt", "creator": "installer.exe"})
    history_log.record({"path": r"C:\b.txt", "creator": "chrome.exe"})

    entries = history_log.recent(limit=10)
    assert len(entries) == 2
    assert entries[0]["path"] == r"C:\b.txt"  # newest first


def test_search_filters_by_any_field_substring(tmp_path, monkeypatch):
    monkeypatch.setattr(history_log, "_history_path",
                        lambda: str(tmp_path / "history.json"))
    history_log.record({"path": r"C:\setup_123.tmp", "creator": "installer.exe"})
    history_log.record({"path": r"C:\report.docx", "creator": "winword.exe"})

    results = history_log.search("installer")
    assert len(results) == 1
    assert results[0]["creator"] == "installer.exe"


def test_history_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(history_log, "_history_path",
                        lambda: str(tmp_path / "history.json"))
    monkeypatch.setattr(history_log, "_CAP", 5)
    for i in range(10):
        history_log.record({"path": f"C:\\{i}.txt", "creator": "x"})

    assert len(history_log.recent(limit=100)) == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_history_log.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
"""A local, append-only log of File Forensics findings -- every manual
search result and every live-watch detection. Same shape as
quick_fix_history.py: a capped JSON array under the app data dir.
"""
import json
import os
from datetime import datetime
from typing import List

#: Higher than quick_fix_history.py's 200 -- live-watch can generate many
#: entries on a churning folder, and losing recent forensic findings to a
#: tight cap would defeat the point of keeping history at all.
_CAP = 500


def _history_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "WindowsTweaker")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "file_forensics_history.json")


def _load() -> list:
    path = _history_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def record(entry: dict) -> None:
    entries = _load()
    entries.append({**entry, "at": datetime.now().isoformat(timespec="seconds")})
    with open(_history_path(), "w", encoding="utf-8") as f:
        json.dump(entries[-_CAP:], f, indent=2)


def recent(limit: int = 50) -> List[dict]:
    return list(reversed(_load()))[:limit]


def search(query: str, limit: int = 50) -> List[dict]:
    needle = query.lower()
    matches = [e for e in reversed(_load())
              if any(needle in str(v).lower() for v in e.values())]
    return matches[:limit]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_history_log.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modules/file_forensics/history_log.py tests/test_file_forensics_history_log.py
git commit -m "feat(file-forensics): add persistent history log"
```

---

### Task 9: FileForensicsModule -- search UI, results table, empty/error states

**Files:**
- Create: `src/modules/file_forensics/file_forensics_module.py`
- Test: `tests/test_file_forensics_module.py`

**Interfaces:**
- Consumes: `engine.analysis.analyze`, `core.base_module.BaseModule`,
  `core.worker.Worker`, `ui.empty_state.EmptyState`,
  `ui.error_banner.ErrorBanner`, `core.widget_life.widget_is_valid`
- Produces: `FileForensicsModule(BaseModule)` with a working manual
  Search that populates a color-coded results table.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from PyQt6.QtWidgets import QApplication


class _FakeApp:
    config = None
    thread_pool = None


@pytest.fixture
def module(qapp):
    from modules.file_forensics.file_forensics_module import FileForensicsModule
    m = FileForensicsModule()
    m.on_start(_FakeApp())
    m.create_widget()
    return m


def test_module_declares_itself_correctly(module):
    assert module.name == "File Forensics"
    assert module.requires_admin is False


def test_create_widget_builds_without_raising(module):
    assert module._widget is not None


def test_search_with_no_folder_shows_the_empty_state(module, monkeypatch):
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [])
    module._folder_edit.setText(r"C:\some\folder")
    module._on_search_clicked()
    assert module._results_stack.currentIndex() == 1  # empty page


def test_a_result_populates_the_table_and_shows_content_page(module, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    fake_analysis = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\found.txt", size=10,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="TESTUSER", read_only=False,
        ),
        locking_processes=[], locking_summary="ok",
        creator_candidates=[], top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [fake_analysis])
    module._folder_edit.setText(r"C:\some\folder")
    module._on_search_clicked()

    assert module._table.rowCount() == 1
    assert module._results_stack.currentIndex() == 0  # content page


def test_a_nonexistent_folder_shows_the_error_banner(module, monkeypatch):
    def _raise(*a, **k):
        raise FileNotFoundError("no such folder")
    monkeypatch.setattr(module, "_analyze_folder", _raise)
    module._folder_edit.setText(r"C:\does\not\exist")
    module._on_search_clicked()
    assert not module._error_banner.isHidden()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_module.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
"""File Forensics -- find a file, see who has it open, guess who created
it. A redesigned, professional rebuild of the user's own
Find-FileCreator.ps1, hosted as the first tab of the new Scripts hub.

Built entirely on this app's existing process-forensics engine
(core.procengine) rather than reimplementing any of it -- see
docs/superpowers/specs/2026-09-18-scripts-file-forensics-design.md.
"""
import fnmatch
import os
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.widget_life import widget_is_valid as _widget_valid
from core.worker import Worker
from ui.empty_state import EmptyState
from ui.error_banner import ErrorBanner

from .engine.analysis import FileAnalysis, analyze

_COLUMNS = ["Name", "Path", "Size", "Created", "Owner", "Locked By", "Likely Creator"]


class FileForensicsModule(BaseModule):
    name = "File Forensics"
    icon = "🔎"
    description = "Find a file, see who has it open, and who created it"
    group = ModuleGroup.TOOLS
    requires_admin = False

    def __init__(self):
        super().__init__()
        self._widget: Optional[QWidget] = None
        self._loaded = False

    def on_start(self, app) -> None:
        self.app = app

    def on_activate(self) -> None:
        pass

    def on_deactivate(self) -> None:
        self.cancel_all_workers()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def get_refresh_interval(self):
        return None

    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(8, 8, 8, 8)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        layout.addWidget(self._error_banner)

        layout.addLayout(self._build_toolbar())

        self._results_stack = QStackedWidget()
        self._table = self._build_table()
        self._results_stack.addWidget(self._table)
        self._empty = EmptyState(
            "🔎", "No results yet",
            "Enter a folder and click Search, or enable Live Watch.",
        )
        self._results_stack.addWidget(self._empty)
        self._results_stack.setCurrentIndex(1)
        layout.addWidget(self._results_stack, 1)

        return self._widget

    def _build_toolbar(self) -> QHBoxLayout:
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Folder:"))
        self._folder_edit = QLineEdit(os.environ.get("TEMP", ""))
        toolbar.addWidget(self._folder_edit, 1)
        toolbar.addWidget(QLabel("Name contains:"))
        self._filter_edit = QLineEdit()
        toolbar.addWidget(self._filter_edit)
        self._recurse_cb = QCheckBox("Recurse")
        self._recurse_cb.setChecked(True)
        toolbar.addWidget(self._recurse_cb)
        self._search_btn = QPushButton("Search")
        self._search_btn.clicked.connect(self._on_search_clicked)
        toolbar.addWidget(self._search_btn)
        return toolbar

    def _build_table(self) -> QTableWidget:
        table = QTableWidget(0, len(_COLUMNS))
        table.setHorizontalHeaderLabels(_COLUMNS)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        return table

    def _analyze_folder(self, folder: str, name_filter: str, recurse: bool
                        ) -> List[FileAnalysis]:
        """Walk `folder` for names containing `name_filter`, analyzing
        each match. Raises FileNotFoundError / NotADirectoryError for the
        UI to turn into an ErrorBanner -- never swallowed."""
        if not os.path.isdir(folder):
            raise FileNotFoundError(f"'{folder}' is not a folder.")
        results = []
        if recurse:
            walker = os.walk(folder)
        else:
            # os.listdir() returns subdirectories too; os.walk() already
            # separates them into _dirs, but the single-level case must
            # filter by hand or a subfolder gets analyzed as if it were a
            # file (os.stat() succeeds on a directory too).
            only_files = [f for f in os.listdir(folder)
                         if os.path.isfile(os.path.join(folder, f))]
            walker = [(folder, [], only_files)]
        for root, _dirs, files in walker:
            for filename in files:
                if name_filter and name_filter.lower() not in filename.lower():
                    continue
                path = os.path.join(root, filename)
                try:
                    results.append(analyze(path, vt_api_key=self._vt_api_key()))
                except (FileNotFoundError, PermissionError, OSError):
                    continue  # gone or unreadable between listing and analysis
        return results

    def _vt_api_key(self) -> str:
        if self.app and self.app.config:
            return self.app.config.get("virustotal.api_key", "") or ""
        return ""

    def _on_search_clicked(self) -> None:
        folder = self._folder_edit.text().strip()
        name_filter = self._filter_edit.text().strip()
        recurse = self._recurse_cb.isChecked()
        try:
            results = self._analyze_folder(folder, name_filter, recurse)
        except (FileNotFoundError, NotADirectoryError, PermissionError) as e:
            self._error_banner.set_error(str(e))
            return
        self._error_banner.clear()
        self._populate_table(results)

    def _populate_table(self, results: List[FileAnalysis]) -> None:
        self._table.setRowCount(0)
        for analysis in results:
            row = self._table.rowCount()
            self._table.insertRow(row)
            meta = analysis.metadata
            self._table.setItem(row, 0, QTableWidgetItem(os.path.basename(meta.path)))
            self._table.setItem(row, 1, QTableWidgetItem(meta.path))
            self._table.setItem(row, 2, QTableWidgetItem(str(meta.size)))
            self._table.setItem(row, 3, QTableWidgetItem(str(meta.created)))
            self._table.setItem(row, 4, QTableWidgetItem(meta.owner))
            locked_text = ", ".join(p.process for p in analysis.locking_processes)
            self._table.setItem(row, 5, QTableWidgetItem(locked_text))
            creator_text = (analysis.creator_candidates[0].name
                            if analysis.creator_candidates else "")
            self._table.setItem(row, 6, QTableWidgetItem(creator_text))
        if not _widget_valid(self._results_stack):
            return
        self._results_stack.setCurrentIndex(0 if results else 1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_module.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modules/file_forensics/file_forensics_module.py tests/test_file_forensics_module.py
git commit -m "feat(file-forensics): add search UI, results table, empty/error states"
```

---

### Task 10: Color coding, detail panel, and remediation actions

**Files:**
- Modify: `src/modules/file_forensics/file_forensics_module.py`
- Modify: `tests/test_file_forensics_module.py`

**Interfaces:**
- Consumes: `core.procengine.actions.end_process`
- Produces: row color coding, a detail panel below the table, Kill
  Locking Process / Reveal in Explorer / Copy Path actions.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_locked_file_row_is_highlighted(module, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    from modules.file_forensics.engine.locking_processes import LockingProcess
    import datetime

    locked = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\locked.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[LockingProcess(pid=1, process="notepad", type_name="File")],
        locking_summary="ok", creator_candidates=[], top_creator_signature=None,
        reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [locked])
    module._folder_edit.setText(r"C:\x")
    module._on_search_clicked()

    from PyQt6.QtGui import QColor
    item = module._table.item(0, 5)
    assert item.background().color() != QColor(0, 0, 0, 0)  # actually colored


def test_selecting_a_row_shows_its_detail(module, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    from modules.file_forensics.engine.locking_processes import LockingProcess
    import datetime

    locked = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\locked.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[LockingProcess(pid=1, process="notepad", type_name="File")],
        locking_summary="1 matches in 250 processes", creator_candidates=[],
        top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [locked])
    module._folder_edit.setText(r"C:\x")
    module._on_search_clicked()
    module._table.selectRow(0)
    module._on_row_selected()

    assert "notepad" in module._detail_label.text()
    assert "250 processes" in module._detail_label.text()


def test_kill_locking_process_asks_for_confirmation(module, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    from modules.file_forensics.engine.locking_processes import LockingProcess
    import datetime

    locked = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\locked.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[LockingProcess(pid=1, process="notepad", type_name="File")],
        locking_summary="ok", creator_candidates=[], top_creator_signature=None,
        reputation=None,
    )
    monkeypatch.setattr(module, "_analyze_folder", lambda *a, **k: [locked])
    module._folder_edit.setText(r"C:\x")
    module._on_search_clicked()
    module._table.selectRow(0)
    module._on_row_selected()

    from PyQt6.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No)
    killed = []
    monkeypatch.setattr("modules.file_forensics.file_forensics_module.end_process",
                        lambda pid: killed.append(pid))

    module._on_kill_locking_clicked()

    assert asked  # confirmation was shown
    assert killed == []  # user said No, nothing killed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_module.py -v -k "highlighted or detail or confirmation"`
Expected: FAIL — attributes/methods don't exist yet

- [ ] **Step 3: Write the implementation**

Add to `file_forensics_module.py`:

```python
# Add to imports:
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QMessageBox

from core.procengine.actions import end_process
from core.procengine.signatures import COULD_NOT_VERIFY, INVALID, NOT_SIGNED

# Add module-level color constants, near _COLUMNS:
_LOCKED_COLOR = QColor("#5c4a1a")     # amber -- in use
_CREATOR_HIGH_COLOR = QColor("#1a5c2a")  # green -- confident single match
_CREATOR_LOW_COLOR = QColor("#5c4a1a")   # amber -- ambiguous/multiple
```

Replace `create_widget`'s body from `self._results_stack = ...` onward to
add a detail panel and wire row selection:

```python
        self._results_stack = QStackedWidget()
        self._table = self._build_table()
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._results_stack.addWidget(self._table)
        self._empty = EmptyState(
            "🔎", "No results yet",
            "Enter a folder and click Search, or enable Live Watch.",
        )
        self._results_stack.addWidget(self._empty)
        self._results_stack.setCurrentIndex(1)
        layout.addWidget(self._results_stack, 1)

        self._detail_label = QLabel("Select a row to see details.")
        self._detail_label.setWordWrap(True)
        layout.addWidget(self._detail_label)

        actions = QHBoxLayout()
        self._kill_btn = QPushButton("Kill Locking Process")
        self._kill_btn.setEnabled(False)
        self._kill_btn.clicked.connect(self._on_kill_locking_clicked)
        actions.addWidget(self._kill_btn)
        self._reveal_btn = QPushButton("Reveal in Explorer")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._on_reveal_clicked)
        actions.addWidget(self._reveal_btn)
        self._copy_btn = QPushButton("Copy Path")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._on_copy_path_clicked)
        actions.addWidget(self._copy_btn)
        actions.addStretch()
        layout.addLayout(actions)

        self._current_results: List[FileAnalysis] = []
        return self._widget
```

Update `_populate_table` to keep the analyses and color rows:

```python
    def _populate_table(self, results: List[FileAnalysis]) -> None:
        self._current_results = results
        self._table.setRowCount(0)
        for analysis in results:
            row = self._table.rowCount()
            self._table.insertRow(row)
            meta = analysis.metadata
            self._table.setItem(row, 0, QTableWidgetItem(os.path.basename(meta.path)))
            self._table.setItem(row, 1, QTableWidgetItem(meta.path))
            self._table.setItem(row, 2, QTableWidgetItem(str(meta.size)))
            self._table.setItem(row, 3, QTableWidgetItem(str(meta.created)))
            self._table.setItem(row, 4, QTableWidgetItem(meta.owner))

            locked_text = ", ".join(p.process for p in analysis.locking_processes)
            locked_item = QTableWidgetItem(locked_text)
            if analysis.locking_processes:
                locked_item.setBackground(_LOCKED_COLOR)
            self._table.setItem(row, 5, locked_item)

            creator_text = (analysis.creator_candidates[0].name
                            if analysis.creator_candidates else "")
            creator_item = QTableWidgetItem(creator_text)
            if len(analysis.creator_candidates) == 1:
                creator_item.setBackground(_CREATOR_HIGH_COLOR)
            elif len(analysis.creator_candidates) > 1:
                creator_item.setBackground(_CREATOR_LOW_COLOR)
            self._table.setItem(row, 6, creator_item)
        if not _widget_valid(self._results_stack):
            return
        self._results_stack.setCurrentIndex(0 if results else 1)

    def _selected_analysis(self) -> Optional[FileAnalysis]:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._current_results):
            return None
        return self._current_results[row]

    def _on_row_selected(self) -> None:
        analysis = self._selected_analysis()
        has_lock = bool(analysis and analysis.locking_processes)
        self._kill_btn.setEnabled(has_lock)
        self._reveal_btn.setEnabled(analysis is not None)
        self._copy_btn.setEnabled(analysis is not None)
        if not analysis:
            self._detail_label.setText("Select a row to see details.")
            return

        lines = [f"Path: {analysis.metadata.path}", f"Owner: {analysis.metadata.owner}"]
        if analysis.locking_processes:
            lines.append("Locked by:")
            for p in analysis.locking_processes:
                lines.append(f"  {p.process} (PID {p.pid})")
        else:
            lines.append("Not currently open by any process.")
        lines.append(analysis.locking_summary)
        if analysis.creator_candidates:
            lines.append("Creator candidates:")
            for c in analysis.creator_candidates:
                lines.append(f"  {c.name} (PID {c.pid}, Δ{c.delta_seconds:+.1f}s)")
            sig = analysis.top_creator_signature
            # Three distinct answers, never collapsed into one -- see
            # analysis.py's own docstring on why this field carries the
            # whole SignatureFacts rather than just a bool.
            if sig is not None and sig.status == NOT_SIGNED:
                lines.append("⚠ Likely creator is UNSIGNED.")
            elif sig is not None and sig.status == INVALID:
                lines.append(f"⚠ Likely creator's signature is INVALID: {sig.reason or ''}".rstrip())
            elif sig is not None and sig.status == COULD_NOT_VERIFY:
                lines.append(f"Signature could not be verified: {sig.reason or ''}".rstrip())
        else:
            lines.append("No process started close enough to the file's creation time.")
        self._detail_label.setText("\n".join(lines))

    def _on_kill_locking_clicked(self) -> None:
        analysis = self._selected_analysis()
        if not analysis or not analysis.locking_processes:
            return
        proc = analysis.locking_processes[0]
        reply = QMessageBox.question(
            self._widget, "Kill Locking Process",
            f"Kill '{proc.process}' (PID {proc.pid})?\n\n"
            "This ends the process immediately and cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            end_process(proc.pid)
            self._on_search_clicked()  # refresh to show the file is now free

    def _on_reveal_clicked(self) -> None:
        analysis = self._selected_analysis()
        if analysis:
            import subprocess
            subprocess.Popen(["explorer", "/select,", analysis.metadata.path])

    def _on_copy_path_clicked(self) -> None:
        analysis = self._selected_analysis()
        if analysis:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(analysis.metadata.path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_module.py -v`
Expected: PASS (all tests from Task 9 and Task 10)

- [ ] **Step 5: Commit**

```bash
git add src/modules/file_forensics/file_forensics_module.py tests/test_file_forensics_module.py
git commit -m "feat(file-forensics): add color coding, detail panel, remediation actions"
```

---

### Task 11: Live watch, ignore patterns, and toast notification

**Files:**
- Modify: `src/modules/file_forensics/file_forensics_module.py`
- Modify: `tests/test_file_forensics_module.py`

**Interfaces:**
- Consumes: `engine.folder_watcher.FolderWatcher`, `core.events.NOTIFY_BALLOON`,
  `core.events.BalloonNotifyData`
- Produces: a Live Watch toggle that starts/stops a `FolderWatcher` on a
  `Worker`, an ignore-patterns field, a desktop toast on each detection
  while the app isn't the foreground window.

- [ ] **Step 1: Write the failing tests**

```python
def test_live_watch_toggle_starts_and_stops_a_watcher(module, monkeypatch):
    started = []
    stopped = []

    class _FakeWatcher:
        def __init__(self, path, on_created, recursive=False):
            started.append(path)
            self.on_created = on_created
        def run(self, worker):
            pass
        def stop(self):
            stopped.append(True)

    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.FolderWatcher", _FakeWatcher)
    module._folder_edit.setText(r"C:\watch\me")
    module._watch_cb.setChecked(True)
    module._on_watch_toggled(True)
    assert started == [r"C:\watch\me"]

    module._watch_cb.setChecked(False)
    module._on_watch_toggled(False)
    assert stopped == [True]


def test_ignore_pattern_skips_analysis(module, monkeypatch):
    module._ignore_edit.setText("*.tmp")
    analyzed = []
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.analyze",
        lambda path, **k: analyzed.append(path))

    module._on_watched_file_created(r"C:\watch\noise.tmp")
    assert analyzed == []

    module._on_watched_file_created(r"C:\watch\real.docx")
    assert analyzed == [r"C:\watch\real.docx"]


def test_a_watch_detection_is_recorded_to_history(module, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    fake = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\watch\real.docx", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="", read_only=False,
        ),
        locking_processes=[], locking_summary="ok",
        creator_candidates=[], top_creator_signature=None, reputation=None,
    )
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.analyze", lambda path, **k: fake)
    recorded = []
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.history_log.record",
        lambda entry: recorded.append(entry))

    module._on_watched_file_created(r"C:\watch\real.docx")

    assert len(recorded) == 1
    assert recorded[0]["path"] == r"C:\watch\real.docx"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_module.py -v -k "watch or ignore"`
Expected: FAIL — attributes/methods don't exist yet

- [ ] **Step 3: Write the implementation**

Add to imports:

```python
from core.events import NOTIFY_BALLOON, BalloonNotifyData
from . import history_log
from .engine.folder_watcher import FolderWatcher
```

Add to `_build_toolbar`, after the Search button:

```python
        self._watch_cb = QCheckBox("Live Watch")
        self._watch_cb.toggled.connect(self._on_watch_toggled)
        toolbar.addWidget(self._watch_cb)
        toolbar.addWidget(QLabel("Ignore:"))
        self._ignore_edit = QLineEdit("*.tmp;~$*")
        self._ignore_edit.setMaximumWidth(120)
        toolbar.addWidget(self._ignore_edit)
```

Add near `__init__`:

```python
        self._watcher: Optional[FolderWatcher] = None
        self._watch_worker: Optional[Worker] = None
```

Add new methods:

```python
    def _ignore_patterns(self) -> List[str]:
        raw = self._ignore_edit.text().strip()
        return [p.strip() for p in raw.split(";") if p.strip()]

    def _is_ignored(self, path: str) -> bool:
        name = os.path.basename(path)
        return any(fnmatch.fnmatch(name, pat) for pat in self._ignore_patterns())

    def _on_watch_toggled(self, checked: bool) -> None:
        if checked:
            folder = self._folder_edit.text().strip()
            self._watcher = FolderWatcher(
                folder, on_created=self._on_watched_file_created,
                recursive=self._recurse_cb.isChecked(),
            )
            self._watch_worker = Worker(self._watcher.run)
            self._workers.append(self._watch_worker)
            self.thread_pool.start(self._watch_worker)
        else:
            if self._watcher is not None:
                self._watcher.stop()  # must actually interrupt the wait, not just cancel()
            if self._watch_worker is not None:
                self._watch_worker.cancel()
            self._watcher = None
            self._watch_worker = None

    def _on_watched_file_created(self, path: str) -> None:
        if self._is_ignored(path):
            return
        try:
            result = analyze(path, vt_api_key=self._vt_api_key())
        except (FileNotFoundError, PermissionError, OSError):
            return  # gone or unreadable by the time we got to it
        creator = result.creator_candidates[0].name if result.creator_candidates else ""
        history_log.record({
            "path": result.metadata.path, "creator": creator,
            "locked_by": ", ".join(p.process for p in result.locking_processes),
        })
        self._current_results = self._current_results + [result]
        self._populate_table(self._current_results)
        self._maybe_notify(result, creator)

    def _maybe_notify(self, result, creator: str) -> None:
        from PyQt6.QtWidgets import QApplication
        if QApplication.activeWindow() is not None:
            return  # app is in the foreground -- the table update is enough
        if not self.app:
            return
        message = f"New file: {os.path.basename(result.metadata.path)}"
        if creator:
            message += f" (likely by {creator})"
        self.app.event_bus.publish(NOTIFY_BALLOON, BalloonNotifyData(
            title="File Forensics", message=message))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_module.py -v`
Expected: PASS (all tests from Tasks 9-11)

- [ ] **Step 5: Commit**

```bash
git add src/modules/file_forensics/file_forensics_module.py tests/test_file_forensics_module.py
git commit -m "feat(file-forensics): add live watch, ignore patterns, toast notification"
```

---

### Task 12: History tab and export

**Files:**
- Modify: `src/modules/file_forensics/file_forensics_module.py`
- Modify: `tests/test_file_forensics_module.py`

**Interfaces:**
- Produces: a `QTabWidget` splitting Search results from a searchable
  History panel, and Export (CSV/JSON) for both.

- [ ] **Step 1: Write the failing tests**

```python
def test_history_tab_shows_recorded_entries(module, monkeypatch):
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.history_log.recent",
        lambda limit=50: [{"path": r"C:\a.txt", "creator": "x.exe", "at": "2026-01-01T00:00:00"}])
    module._refresh_history()
    assert module._history_table.rowCount() == 1


def test_history_search_filters(module, monkeypatch):
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.history_log.search",
        lambda query, limit=50: [{"path": r"C:\found.txt", "creator": "y.exe", "at": "2026-01-01T00:00:00"}])
    module._history_search_edit.setText("found")
    module._on_history_search()
    assert module._history_table.rowCount() == 1
    assert module._history_table.item(0, 0).text() == r"C:\found.txt"


def test_export_writes_a_csv(module, tmp_path, monkeypatch):
    from modules.file_forensics.engine.file_metadata import FileMetadata
    from modules.file_forensics.engine.analysis import FileAnalysis
    import datetime

    fake = FileAnalysis(
        metadata=FileMetadata(
            path=r"C:\a.txt", size=1,
            created=datetime.datetime(2026, 1, 1), modified=datetime.datetime(2026, 1, 1),
            accessed=datetime.datetime(2026, 1, 1), owner="me", read_only=False,
        ),
        locking_processes=[], locking_summary="ok",
        creator_candidates=[], top_creator_signature=None, reputation=None,
    )
    module._current_results = [fake]
    out_path = str(tmp_path / "export.csv")
    monkeypatch.setattr(
        "modules.file_forensics.file_forensics_module.QFileDialog.getSaveFileName",
        lambda *a, **k: (out_path, "CSV"))

    module._on_export_clicked()

    with open(out_path, encoding="utf-8") as f:
        content = f.read()
    assert r"C:\a.txt" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_module.py -v -k "history or export"`
Expected: FAIL — attributes/methods don't exist yet

- [ ] **Step 3: Write the implementation**

Add to imports:

```python
import csv

from PyQt6.QtWidgets import QFileDialog, QTabWidget
```

Restructure `create_widget` to put the results stack + detail panel +
actions inside a "Search" tab, and add a "History" tab, inside an outer
`QTabWidget`:

```python
    def create_widget(self) -> QWidget:
        self._widget = QWidget()
        outer_layout = QVBoxLayout(self._widget)
        outer_layout.setContentsMargins(8, 8, 8, 8)

        self._error_banner = ErrorBanner()
        self._error_banner.hide()
        outer_layout.addWidget(self._error_banner)

        tabs = QTabWidget()
        tabs.addTab(self._build_search_tab(), "Search")
        tabs.addTab(self._build_history_tab(), "History")
        outer_layout.addWidget(tabs, 1)

        self._current_results: List[FileAnalysis] = []
        self._watcher: Optional[FolderWatcher] = None
        self._watch_worker: Optional[Worker] = None
        return self._widget

    def _build_search_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addLayout(self._build_toolbar())

        self._results_stack = QStackedWidget()
        self._table = self._build_table()
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._results_stack.addWidget(self._table)
        self._empty = EmptyState(
            "🔎", "No results yet",
            "Enter a folder and click Search, or enable Live Watch.",
        )
        self._results_stack.addWidget(self._empty)
        self._results_stack.setCurrentIndex(1)
        layout.addWidget(self._results_stack, 1)

        self._detail_label = QLabel("Select a row to see details.")
        self._detail_label.setWordWrap(True)
        layout.addWidget(self._detail_label)

        actions = QHBoxLayout()
        self._kill_btn = QPushButton("Kill Locking Process")
        self._kill_btn.setEnabled(False)
        self._kill_btn.clicked.connect(self._on_kill_locking_clicked)
        actions.addWidget(self._kill_btn)
        self._reveal_btn = QPushButton("Reveal in Explorer")
        self._reveal_btn.setEnabled(False)
        self._reveal_btn.clicked.connect(self._on_reveal_clicked)
        actions.addWidget(self._reveal_btn)
        self._copy_btn = QPushButton("Copy Path")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._on_copy_path_clicked)
        actions.addWidget(self._copy_btn)
        self._export_btn = QPushButton("Export CSV")
        self._export_btn.clicked.connect(self._on_export_clicked)
        actions.addWidget(self._export_btn)
        actions.addStretch()
        layout.addLayout(actions)
        return tab

    def _build_history_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search history:"))
        self._history_search_edit = QLineEdit()
        self._history_search_edit.textChanged.connect(self._on_history_search)
        search_row.addWidget(self._history_search_edit, 1)
        layout.addLayout(search_row)

        self._history_table = QTableWidget(0, 3)
        self._history_table.setHorizontalHeaderLabels(["Path", "Creator", "When"])
        self._history_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._history_table, 1)
        self._refresh_history()
        return tab
```

Add new methods:

```python
    def _refresh_history(self) -> None:
        self._fill_history_table(history_log.recent(limit=50))

    def _on_history_search(self) -> None:
        query = self._history_search_edit.text().strip()
        entries = history_log.search(query) if query else history_log.recent(limit=50)
        self._fill_history_table(entries)

    def _fill_history_table(self, entries: List[dict]) -> None:
        self._history_table.setRowCount(0)
        for entry in entries:
            row = self._history_table.rowCount()
            self._history_table.insertRow(row)
            self._history_table.setItem(row, 0, QTableWidgetItem(entry.get("path", "")))
            self._history_table.setItem(row, 1, QTableWidgetItem(entry.get("creator", "")))
            self._history_table.setItem(row, 2, QTableWidgetItem(entry.get("at", "")))

    def _on_export_clicked(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self._widget, "Export Results", "file_forensics_export.csv", "CSV Files (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Path", "Size", "Created", "Owner", "Locked By", "Likely Creator"])
            for analysis in self._current_results:
                meta = analysis.metadata
                locked = ", ".join(p.process for p in analysis.locking_processes)
                creator = (analysis.creator_candidates[0].name
                          if analysis.creator_candidates else "")
                writer.writerow([meta.path, meta.size, meta.created, meta.owner, locked, creator])
```

Also update `_on_watched_file_created` to call `self._refresh_history()`
after `history_log.record(...)`, so a live-watch hit shows up in the
History tab immediately, not only after the tab is next opened.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_file_forensics_module.py -v`
Expected: PASS (all tests from Tasks 9-12)

- [ ] **Step 5: Commit**

```bash
git add src/modules/file_forensics/file_forensics_module.py tests/test_file_forensics_module.py
git commit -m "feat(file-forensics): add history tab and CSV export"
```

---

### Task 13: Scripts composite hub and app wiring

**Files:**
- Create: `src/modules/scripts_hub/__init__.py` (empty)
- Create: `src/modules/scripts_hub/scripts_hub_module.py`
- Modify: `src/main.py`
- Modify: `pyinstaller_common.py`
- Modify: `tests/test_module_inventory.py`
- Test: `tests/test_scripts_hub_module.py` (new)

**Interfaces:**
- Produces: `ScriptsModule(CompositeModule)`, registered in `main.py`,
  sidebar count 28 -> 29.

- [ ] **Step 1: Write the failing test**

```python
import pytest


@pytest.fixture
def module(qapp):
    from modules.scripts_hub.scripts_hub_module import ScriptsModule
    return ScriptsModule()


def test_the_hub_has_one_child(module):
    assert len(module.children) == 1
    assert type(module.children[0]).__name__ == "FileForensicsModule"


def test_the_hub_builds_a_widget_with_one_tab(module, qapp):
    class FakeApp:
        backup = None
        config = None
        thread_pool = None

    module.on_start(FakeApp())
    widget = module.create_widget()
    assert widget is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scripts_hub_module.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write `scripts_hub_module.py`**

```python
"""Scripts -- a home for hand-rolled utility tools. File Forensics is the
first; adding a second script later is adding another child here, the
same way Startup & Boot grew from two children to three.
"""
from core.composite_module import CompositeModule
from core.module_groups import ModuleGroup


class ScriptsModule(CompositeModule):
    name = "Scripts"
    icon = "🧰"
    description = "Hand-rolled utility tools"
    group = ModuleGroup.TOOLS
    requires_admin = False

    def __init__(self):
        super().__init__()
        from modules.file_forensics.file_forensics_module import FileForensicsModule

        self.children = [FileForensicsModule()]
```

- [ ] **Step 4: Update `src/main.py`**

Read the current file first to confirm the exact "Batch C — Tools group"
import/registration block still matches (grep for `RestoreManagerModule`
to find it). Add, alongside the other Batch C imports:

```python
    from modules.scripts_hub.scripts_hub_module import ScriptsModule
```

Add, alongside the other Batch C registrations (after
`app.module_registry.register(RestoreManagerModule())`):

```python
    app.module_registry.register(ScriptsModule())
```

- [ ] **Step 5: Update `pyinstaller_common.py`**

Add to `HIDDEN_IMPORTS`, near the other module-specific entries (`grep -n
"HIDDEN_IMPORTS" pyinstaller_common.py` to find the exact insertion
point):

```python
    "modules.file_forensics.file_forensics_module",
```

(`ScriptsModule` imports `FileForensicsModule` lazily inside `__init__`,
per the `CompositeModule` convention documented in CLAUDE.md — miss this
and the frozen build runs fine until someone opens the Scripts tab.)

- [ ] **Step 6: Update `tests/test_module_inventory.py`**

Change `test_the_sidebar_is_28_entries` to `test_the_sidebar_is_29_entries`,
update its docstring to note the new, genuinely additive Scripts entry,
and change the assertion:

```python
def test_the_sidebar_is_29_entries(registered):
    """29, up from 28: the new Scripts hub (docs/superpowers/specs/
    2026-09-18-scripts-file-forensics-design.md) is a genuinely new
    sidebar entry, not a fold of existing ones -- 28 + 1 = 29."""
    assert len(registered) == 29
```

- [ ] **Step 7: Run the full inventory + smoke + new hub tests**

Run: `.venv/Scripts/python.exe -m pytest -q tests/test_module_inventory.py tests/test_module_smoke.py tests/test_scripts_hub_module.py`
Expected: PASS, sidebar count 29

- [ ] **Step 8: Commit**

```bash
git add src/modules/scripts_hub src/main.py pyinstaller_common.py tests/test_module_inventory.py tests/test_scripts_hub_module.py
git commit -m "feat: add Scripts composite hub hosting File Forensics"
```

---

### Task 14: Docs, full suite, and real-machine verification

**Files:**
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Test: full suite + a real run against `C:\Users\iorda\AppData\Local\Temp`

- [ ] **Step 1: Update CLAUDE.md's Composite Modules section**

Change "the five" to "the six" and add `Scripts` to the named list.
Add a `### Scripts / File Forensics` section (matching the style of the
other tool sections — Monitor Control, Driver Manager, etc.) summarizing:
the engine reuse table from the spec, the `ReadDirectoryChangesW`
cancellation requirement (`FolderWatcher.stop()` must be called
explicitly, not just `worker.cancel()`), and the NT device-path
translation gotcha (`findref.find()` matches device paths, not drive
letters).

- [ ] **Step 2: Update AGENTS.md's Composite Modules section**

Same change, matching AGENTS.md's own condensed style (see the
mgmt-network-consolidation round's equivalent edit for the pattern).

- [ ] **Step 3: Update README.md**

Add a "Scripts" bullet under whichever section lists TOOLS-group modules
(check the file for the exact existing list first), describing File
Forensics in one line.

- [ ] **Step 4: Update CHANGELOG.md**

Add an entry under `### Added` (or the file's equivalent current-release
heading) describing the new Scripts tab and File Forensics tool, matching
this repo's own changelog entry style (see recent entries for tone/format).

- [ ] **Step 5: Full suite**

```bash
rm -rf .pytest-tmp
.venv/Scripts/python.exe -m pytest -q --timeout=300
```
Expected: exit 0, sidebar count 29, no FAILED/ERROR beyond the documented
pre-existing `test_procengine_findref.py` real-machine-state flake.

- [ ] **Step 6: Real-machine verification**

Run the app from source (`python src/main.py`), open Scripts → File
Forensics, and Search against `C:\Users\iorda\AppData\Local\Temp` (the
user's own explicit request). Confirm: real files listed, at least one
shows real metadata (size/created/owner), and — if any temp file happens
to be open — a real locking process appears with a plausible creator
guess. This is the check that proves the tool works against reality, not
just mocks; do not skip it or claim done without it.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md AGENTS.md README.md CHANGELOG.md
git commit -m "docs: document the Scripts tab and File Forensics tool"
```

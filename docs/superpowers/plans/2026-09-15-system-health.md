# System Health Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new "System Health" sidebar module: read-only findings (pending servicing transaction, orphaned scheduled tasks, upgrade headroom) plus gated DISM servicing actions (ScanHealth, Component Cleanup moved from Large Items, a heavily-gated ResetBase), a JSON history log, and a new read-only `--unattended --stages health`.

**Architecture:** `findings.py`/`servicing.py`/`history.py` are pure Python (no Qt, no PyQt6 import), matching the `scan/`+`store/` split TreeSize/Monitor Control/GPResult already keep — everything worth unit-testing headless lives there. `system_health_module.py` is the only file that imports Qt, assembling a 2-tab `BaseModule` (Findings, Servicing) over that engine layer, plus the DISM buttons and driver-store-style confirm/typed-confirmation dialogs. `stage_runners.py`/`unattended_runner.py` get one new stage, additive only.

**Tech Stack:** Python 3.12, PyQt6, pytest, `subprocess` (DISM/schtasks), `winreg` not needed here.

**Spec:** `docs/superpowers/specs/2026-09-15-system-health-design.md`

## Global Constraints

- No Qt import anywhere in `findings.py`, `servicing.py`, or `history.py`.
- `ResetBase` is never selectable via any bulk/multi-select UI — a single dedicated button, gated behind (a) a clean same-session ScanHealth result, (b) a typed `RESETBASE` confirmation, (c) a forced (non-optional) restore point.
- A refused/failed read (schtasks, DISM) is reported as a Finding/result naming the refusal — never silently treated as "found nothing."
- `--unattended --stages health` calls `findings.all_findings()` only — never any `servicing.py` function.
- `requires_admin = True`, `read_only_unelevated = True` (Monitor Control's pattern, not Driver Manager's) — see spec §2.1's ruling.

---

## Task 1: `findings.py` — the 3 read-only checks

**Files:**
- Create: `src/modules/system_health/__init__.py` (empty)
- Create: `src/modules/system_health/findings.py`
- Test: `tests/test_system_health_findings.py` (new)

**Interfaces:**
- Produces: `Finding` (dataclass: `id: str, title: str, detail: str, severity: str`), `check_pending_servicing() -> Optional[Finding]`, `check_orphaned_scheduled_tasks() -> List[Finding]`, `check_upgrade_headroom() -> Finding`, `all_findings() -> List[Finding]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_system_health_findings.py`:

```python
"""System Health's 3 read-only findings. No Qt, no writes -- every
check here must be safe to run unelevated and from --unattended
--stages health.
"""
import os
import subprocess

import pytest

from modules.system_health.findings import (
    Finding, check_pending_servicing, check_orphaned_scheduled_tasks,
    check_upgrade_headroom, all_findings,
)


def test_pending_servicing_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("windir", str(tmp_path))  # no WinSxS\pending.xml here
    assert check_pending_servicing() is None


def test_pending_servicing_present_returns_a_warning(tmp_path, monkeypatch):
    winsxs = tmp_path / "WinSxS"
    winsxs.mkdir()
    (winsxs / "pending.xml").write_text("<root/>")
    monkeypatch.setenv("windir", str(tmp_path))

    finding = check_pending_servicing()

    assert finding is not None
    assert finding.severity == "warning"
    assert "pending.xml" in finding.detail or str(winsxs) in finding.detail


def test_orphaned_task_pointing_at_a_missing_program_is_flagged(tmp_path, monkeypatch):
    csv_output = '"RealTask","Ready","N/A"\r\n'
    xml_output = "<Task><Actions><Exec><Command>C:\\does\\not\\exist.exe</Command></Exec></Actions></Task>"

    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        class _R:
            pass
        r = _R()
        r.returncode = 0
        if "/xml" in cmd:
            r.stdout = xml_output
        else:
            r.stdout = csv_output
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    findings = check_orphaned_scheduled_tasks()

    assert len(findings) == 1
    assert findings[0].severity == "info"
    assert "RealTask" in findings[0].detail


def test_task_pointing_at_a_real_program_is_not_flagged(tmp_path, monkeypatch):
    real_exe = tmp_path / "real.exe"
    real_exe.write_text("x")
    csv_output = '"RealTask","Ready","N/A"\r\n'
    xml_output = f"<Task><Actions><Exec><Command>{real_exe}</Command></Exec></Actions></Task>"

    def fake_run(cmd, **kwargs):
        class _R:
            pass
        r = _R()
        r.returncode = 0
        r.stdout = xml_output if "/xml" in cmd else csv_output
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert check_orphaned_scheduled_tasks() == []


def test_task_pointing_at_a_bare_command_resolved_via_path_is_not_flagged(monkeypatch):
    csv_output = '"NotepadTask","Ready","N/A"\r\n'
    xml_output = "<Task><Actions><Exec><Command>notepad.exe</Command></Exec></Actions></Task>"

    def fake_run(cmd, **kwargs):
        class _R:
            pass
        r = _R()
        r.returncode = 0
        r.stdout = xml_output if "/xml" in cmd else csv_output
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("shutil.which", lambda name: r"C:\Windows\notepad.exe")

    assert check_orphaned_scheduled_tasks() == []


def test_a_refused_schtasks_call_is_reported_not_swallowed(monkeypatch):
    def fake_run(cmd, **kwargs):
        class _R:
            pass
        r = _R()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "Access is denied."
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    findings = check_orphaned_scheduled_tasks()

    assert len(findings) == 1
    assert findings[0].id == "orphaned_tasks_refused"
    assert "denied" in findings[0].detail.lower()


def test_a_failed_schtasks_call_is_reported_not_swallowed(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise OSError("schtasks not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    findings = check_orphaned_scheduled_tasks()

    assert len(findings) == 1
    assert findings[0].id == "orphaned_tasks_refused"


def test_upgrade_headroom_below_threshold_is_a_warning(monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda path: (100 * 1024**3, 90 * 1024**3, 10 * 1024**3))
    finding = check_upgrade_headroom()
    assert finding.severity == "warning"
    assert "10.0 GB" in finding.title


def test_upgrade_headroom_above_threshold_is_info(monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda path: (500 * 1024**3, 100 * 1024**3, 400 * 1024**3))
    finding = check_upgrade_headroom()
    assert finding.severity == "info"


def test_all_findings_combines_all_three_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("windir", str(tmp_path))  # no pending.xml
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    monkeypatch.setattr("shutil.disk_usage", lambda path: (500 * 1024**3, 100 * 1024**3, 400 * 1024**3))

    findings = all_findings()

    # No pending servicing, no orphaned tasks (empty schtasks output), one
    # headroom finding -- exactly 1 item.
    assert len(findings) == 1
    assert findings[0].id == "upgrade_headroom"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_system_health_findings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.system_health'`.

- [ ] **Step 3: Create the package and `findings.py`**

Create `src/modules/system_health/__init__.py` (empty file — just makes this a package).

Create `src/modules/system_health/findings.py` with the exact content from the spec's §2.2 code block (copy verbatim — it is complete, no placeholders).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_system_health_findings.py -v`
Expected: PASS, all 10 tests.

- [ ] **Step 5: Confirm this file imports no Qt**

Run: `grep -n "PyQt6" src/modules/system_health/findings.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add src/modules/system_health/__init__.py src/modules/system_health/findings.py tests/test_system_health_findings.py
git commit -m "feat(system-health): add the 3 read-only findings checks

Pending servicing transaction (WinSxS\\pending.xml existence), orphaned
scheduled tasks (Action path no longer exists on disk), and an upgrade-
headroom free-space check (a practical rule of thumb, explicitly not
represented as an official Microsoft number). No Qt import -- safe to
call unelevated and from --unattended --stages health. A refused
schtasks read is reported as a Finding, never silently swallowed into
an empty (\"nothing found\") result."
```

---

## Task 2: `servicing.py` — the DISM command wrappers

**Files:**
- Create: `src/modules/system_health/servicing.py`
- Test: `tests/test_system_health_servicing.py` (new)

**Interfaces:**
- Produces: `DismResult` (dataclass: `command: str, returncode: int, output: str`), `run_scan_health(timeout=600) -> DismResult`, `run_component_cleanup(timeout=1800) -> DismResult`, `run_reset_base(timeout=1800) -> DismResult`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_system_health_servicing.py`:

```python
"""DISM command wrappers -- build the exact documented command line and
return its real output/exit code. Never invoked against the real system
in tests; subprocess.run is always mocked.
"""
import subprocess

from modules.system_health.servicing import (
    DismResult, run_scan_health, run_component_cleanup, run_reset_base,
)


def _fake_run(returncode=0, stdout="ok", stderr=""):
    def fake(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        class _R:
            pass
        r = _R()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = stderr
        return r
    captured = {}
    return fake, captured


def test_run_scan_health_builds_the_documented_command(monkeypatch):
    fake, captured = _fake_run()
    monkeypatch.setattr(subprocess, "run", fake)

    result = run_scan_health()

    assert captured["cmd"] == ["dism", "/Online", "/Cleanup-Image", "/ScanHealth"]
    assert isinstance(result, DismResult)
    assert result.returncode == 0
    assert result.output == "ok"


def test_run_component_cleanup_builds_the_documented_command(monkeypatch):
    fake, captured = _fake_run(returncode=0, stdout="cleaned")
    monkeypatch.setattr(subprocess, "run", fake)

    result = run_component_cleanup()

    assert captured["cmd"] == ["dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup"]
    assert result.output == "cleaned"


def test_run_reset_base_builds_the_documented_command(monkeypatch):
    fake, captured = _fake_run(returncode=0, stdout="reset")
    monkeypatch.setattr(subprocess, "run", fake)

    result = run_reset_base()

    assert captured["cmd"] == [
        "dism", "/Online", "/Cleanup-Image", "/StartComponentCleanup", "/ResetBase"]
    assert result.output == "reset"


def test_dism_result_carries_the_real_nonzero_returncode(monkeypatch):
    fake, captured = _fake_run(returncode=87, stdout="", stderr="Error: 87")
    monkeypatch.setattr(subprocess, "run", fake)

    result = run_scan_health()

    assert result.returncode == 87
    assert "87" in result.output


def test_every_call_passes_create_no_window(monkeypatch):
    fake, captured = _fake_run()
    monkeypatch.setattr(subprocess, "run", fake)

    run_scan_health()

    assert captured["kwargs"].get("creationflags") == 0x08000000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_system_health_servicing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'modules.system_health.servicing'`.

- [ ] **Step 3: Create `servicing.py`**

Create `src/modules/system_health/servicing.py` with the exact content from the spec's §2.3 code block (copy verbatim).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_system_health_servicing.py -v`
Expected: PASS, all 5 tests.

- [ ] **Step 5: Confirm no Qt import**

Run: `grep -n "PyQt6" src/modules/system_health/servicing.py`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add src/modules/system_health/servicing.py tests/test_system_health_servicing.py
git commit -m "feat(system-health): add DISM servicing command wrappers

run_scan_health/run_component_cleanup/run_reset_base each build one
documented DISM command line and return its real exit code and output
as a DismResult. No gating logic here -- the UI layer is responsible
for every precondition before run_reset_base is ever called, so the
gating itself is testable independent of ever actually invoking DISM."
```

---

## Task 3: `history.py` — the JSON audit log

**Files:**
- Create: `src/modules/system_health/history.py`
- Test: `tests/test_system_health_history.py` (new)

**Interfaces:**
- Produces: `append_run(app_data_dir: str, *, action: str, command: str, returncode: int, findings_count: int = 0) -> None`, `load_history(app_data_dir: str) -> List[dict]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_system_health_history.py`:

```python
"""System Health's own history log -- mirrors updates/history_writer.py's
shape (a capped JSON array), a separate file since this module's entries
don't fit Update Center's own {ts, freed, updates, wg} shape.
"""
import json
import os

from modules.system_health.history import append_run, load_history, MAX_ENTRIES


def test_load_history_on_a_fresh_install_returns_empty_list(tmp_path):
    assert load_history(str(tmp_path)) == []


def test_append_run_then_load_round_trips(tmp_path):
    append_run(str(tmp_path), action="scan_health", command="dism /Online /Cleanup-Image /ScanHealth",
              returncode=0, findings_count=0)

    history = load_history(str(tmp_path))

    assert len(history) == 1
    assert history[0]["action"] == "scan_health"
    assert history[0]["returncode"] == 0
    assert "ts" in history[0]


def test_history_is_capped_at_max_entries(tmp_path):
    for i in range(MAX_ENTRIES + 10):
        append_run(str(tmp_path), action="findings_refresh", command="", returncode=0, findings_count=i)

    history = load_history(str(tmp_path))

    assert len(history) == MAX_ENTRIES
    # Oldest entries are dropped, newest kept.
    assert history[-1]["findings_count"] == MAX_ENTRIES + 9


def test_load_history_on_corrupt_json_returns_empty_list_not_a_crash(tmp_path):
    health_dir = os.path.join(str(tmp_path), "system_health")
    os.makedirs(health_dir)
    with open(os.path.join(health_dir, "history.json"), "w") as f:
        f.write("{not valid json")

    assert load_history(str(tmp_path)) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_system_health_history.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `history.py`**

Create `src/modules/system_health/history.py`:

```python
"""System Health's own run-history log. Mirrors
modules/updates/history_writer.py's shape (a capped JSON array at
{app_data_dir}/<subfolder>/history.json) -- a separate file rather than
extending Update Center's own history, since this module's entries
({ts, action, command, returncode, findings_count}) don't fit Update
Center's ({ts, freed, updates, wg}) shape, and mixing unrelated history
streams into one file is worse than two small, separately-typed ones.
"""
import json
import logging
import os
from datetime import datetime
from typing import List

logger = logging.getLogger(__name__)

MAX_ENTRIES = 200


def _history_path(app_data_dir: str) -> str:
    health_dir = os.path.join(app_data_dir, "system_health")
    os.makedirs(health_dir, exist_ok=True)
    return os.path.join(health_dir, "history.json")


def load_history(app_data_dir: str) -> List[dict]:
    path = _history_path(app_data_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        logger.warning("Could not read system_health history.json", exc_info=True)
        return []


def append_run(app_data_dir: str, *, action: str, command: str,
               returncode: int, findings_count: int = 0) -> None:
    history = load_history(app_data_dir)
    history.append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "action": action,
        "command": command,
        "returncode": returncode,
        "findings_count": findings_count,
    })
    history = history[-MAX_ENTRIES:]
    path = _history_path(app_data_dir)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception:
        logger.warning("Could not write system_health history.json", exc_info=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_system_health_history.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/modules/system_health/history.py tests/test_system_health_history.py
git commit -m "feat(system-health): add the JSON run-history log

Mirrors updates/history_writer.py's shape (a capped 200-entry JSON
array) in its own file/subfolder rather than extending Update Center's
own history -- the two modules' entries don't share a shape, and
System Health's own audit trail should survive independent of anything
happening in Updates."
```

---

## Task 4: `system_health_module.py` — module skeleton + Findings tab

**Files:**
- Create: `src/modules/system_health/system_health_module.py`
- Modify: `src/main.py`
- Test: `tests/test_system_health_module.py` (new)

**Interfaces:**
- Consumes: `findings.all_findings()` (Task 1), `history.append_run`/`load_history` (Task 3).
- Produces: `SystemHealthModule(BaseModule)` with `name/icon/description/requires_admin/read_only_unelevated/group` class attributes, `create_widget()` returning a `QTabWidget` with 2 tabs ("Findings" built in this task, "Servicing" a placeholder `QWidget` filled in by Task 5), `on_activate()` triggers a first Findings refresh.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_system_health_module.py`:

```python
"""SystemHealthModule's skeleton and Findings tab. Servicing tab's own
behavior (DISM buttons, ResetBase gating) is covered in Task 5's own
additions to this same file.
"""
import tempfile

import pytest


def _module(qapp):
    from app import App
    from modules.system_health.system_health_module import SystemHealthModule

    App.instance = None
    app = App(app_data_dir=tempfile.mkdtemp())
    module = SystemHealthModule()
    module.on_start(app)
    widget = module.create_widget()
    module._keep_alive = widget
    return module, app


def test_module_declares_the_right_admin_model():
    from modules.system_health.system_health_module import SystemHealthModule
    assert SystemHealthModule.requires_admin is True
    assert SystemHealthModule.read_only_unelevated is True


def test_create_widget_builds_two_tabs(qapp):
    module, app = _module(qapp)
    try:
        assert module._tabs.count() == 2
        assert module._tabs.tabText(0) == "Findings"
        assert module._tabs.tabText(1) == "Servicing"
    finally:
        app.shutdown()


def test_refresh_findings_populates_the_list(qapp, monkeypatch):
    from modules.system_health.findings import Finding

    module, app = _module(qapp)
    try:
        fake_findings = [Finding(id="x", title="Test Finding", detail="detail text", severity="info")]
        monkeypatch.setattr("modules.system_health.findings.all_findings", lambda: fake_findings)

        module._refresh_findings()
        # Worker runs on a thread pool -- settle it.
        from PyQt6.QtCore import QThreadPool
        import time
        QThreadPool.globalInstance().waitForDone(5000)
        deadline = time.time() + 1
        while time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert module._findings_list.count() == 1
        assert "Test Finding" in module._findings_list.item(0).text()
    finally:
        app.shutdown()


def test_on_activate_triggers_a_findings_refresh(qapp, monkeypatch):
    module, app = _module(qapp)
    try:
        calls = []
        monkeypatch.setattr(module, "_refresh_findings", lambda: calls.append(1))
        module.on_activate()
        assert calls == [1]
    finally:
        app.shutdown()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_system_health_module.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `system_health_module.py`'s skeleton and Findings tab**

Create `src/modules/system_health/system_health_module.py`:

```python
"""System Health — read-only findings plus gated DISM servicing actions.

requires_admin=True, read_only_unelevated=True: matches Monitor
Control's pattern (display/DDC reads need no elevation, only audio
writes do) rather than Driver Manager's (requires_admin=False) --
System Health is fundamentally about privileged servicing operations
that happen to have a genuinely useful unelevated read-only subset,
which is the closer fit.
"""
import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QListWidget, QListWidgetItem,
)

from core.base_module import BaseModule
from core.module_groups import ModuleGroup
from core.worker import Worker
from core.semantic import semantic

logger = logging.getLogger(__name__)


class SystemHealthModule(BaseModule):
    name = "System Health"
    icon = "🩺"
    description = "Servicing findings, WinSxS cleanup, and component-store health checks"
    requires_admin = True
    read_only_unelevated = True
    group = ModuleGroup.SYSTEM

    def __init__(self):
        super().__init__()
        self._last_scan_health_clean = False
        self._findings_worker = None

    def on_start(self, app) -> None:
        self.app = app

    def create_widget(self) -> QWidget:
        from PyQt6.QtWidgets import QTabWidget
        outer = QWidget()
        layout = QVBoxLayout(outer)
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._build_findings_tab(), "Findings")
        self._tabs.addTab(self._build_servicing_tab(), "Servicing")

        return outer

    # ── Findings tab ──────────────────────────────────────────────────

    def _build_findings_tab(self) -> QWidget:
        tab = QWidget()
        lay = QVBoxLayout(tab)

        toolbar = QHBoxLayout()
        self._refresh_findings_btn = QPushButton("🔄  Refresh Findings")
        self._refresh_findings_btn.clicked.connect(self._refresh_findings)
        self._findings_status_lbl = QLabel("Click Refresh Findings to check this machine")
        self._findings_status_lbl.setObjectName("muted")
        toolbar.addWidget(self._refresh_findings_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._findings_status_lbl)
        lay.addLayout(toolbar)

        self._findings_list = QListWidget()
        lay.addWidget(self._findings_list, 1)

        return tab

    def _refresh_findings(self) -> None:
        self._refresh_findings_btn.setEnabled(False)
        self._findings_status_lbl.setText("Checking...")

        def _run(_worker):
            from modules.system_health import findings
            return findings.all_findings()

        def _done(results):
            self._refresh_findings_btn.setEnabled(True)
            self._findings_list.clear()
            for finding in results:
                icon = "⚠️" if finding.severity == "warning" else "ℹ️"
                item = QListWidgetItem(f"{icon}  {finding.title}\n{finding.detail}")
                color = semantic("warning") if finding.severity == "warning" else semantic("info")
                item.setForeground(__import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(color))
                self._findings_list.addItem(item)
            self._findings_status_lbl.setText(
                f"{len(results)} finding(s)" if results else "No issues found")

        def _err(e: str):
            self._refresh_findings_btn.setEnabled(True)
            self._findings_status_lbl.setText(f"Error: {e}")

        self._findings_worker = Worker(_run)
        self._findings_worker.signals.result.connect(_done)
        self._findings_worker.signals.error.connect(_err)
        self.app.thread_pool.start(self._findings_worker)

    # ── Servicing tab (Task 5 fills this in) ────────────────────────────

    def _build_servicing_tab(self) -> QWidget:
        return QWidget()

    # ── Lifecycle ────────────────────────────────────────────────────

    def on_activate(self) -> None:
        self._refresh_findings()

    def on_stop(self) -> None:
        self.cancel_all_workers()

    def get_status_info(self) -> str:
        return "System Health"
```

Note the awkward `__import__("PyQt6.QtGui", fromlist=["QColor"]).QColor(color)` — this is a placeholder shape ONLY to avoid a forward-reference issue; replace it properly by adding `from PyQt6.QtGui import QColor` to the top-level imports and simplifying that line to `item.setForeground(QColor(color))`.

- [ ] **Step 4: Register the module in `main.py`**

In `src/main.py`, add the import near the other Batch B / System group imports:

```python
    from modules.system_health.system_health_module import SystemHealthModule
```

And the registration near `MonitorControlModule`'s:

```python
    app.module_registry.register(SystemHealthModule())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_system_health_module.py -v`
Expected: PASS, all 4 tests.

- [ ] **Step 6: Run the module smoke test to confirm registration doesn't break anything**

Run: `pytest tests/test_module_smoke.py -v -k SystemHealth`
Expected: PASS — this repo's existing smoke test (per CLAUDE.md's own documented pattern) constructs every registered module and confirms `on_start`+`create_widget` survive; a new module must pass this automatically once registered.

- [ ] **Step 7: Commit**

```bash
git add src/modules/system_health/system_health_module.py src/main.py tests/test_system_health_module.py
git commit -m "feat(system-health): add the module skeleton and Findings tab

New sidebar entry, requires_admin=True + read_only_unelevated=True
(Monitor Control's pattern). Findings tab runs the 3 read-only checks
on a Worker and renders them as a simple severity-colored list.
Servicing tab is a placeholder, filled in by the next task."
```

---

## Task 5: Servicing tab — DISM actions + gated ResetBase

**Files:**
- Modify: `src/modules/system_health/system_health_module.py`
- Modify: `src/modules/cleanup/tabs/_large_items_tab.py` (remove the DISM section, now moved here)
- Test: `tests/test_system_health_module.py` (extend)
- Test: `tests/test_cleanup_large_items_tab.py` (if it exists — check; update to remove now-stale DISM assertions) or wherever `_large_items_tab.py`'s DISM buttons are currently tested

**Interfaces:**
- Consumes: `servicing.run_scan_health/run_component_cleanup/run_reset_base` (Task 2), `history.append_run` (Task 3), `core.system_restore.create_restore_point` (existing).
- Produces: the Servicing tab's real UI — 3 buttons, ResetBase gated per the plan's own Global Constraints.

- [ ] **Step 1: Find and read the current DISM section in `_large_items_tab.py`**

Read `src/modules/cleanup/tabs/_large_items_tab.py` in full. It currently has (verified during spec-writing): `_analyze_btn`/`_dism_btn`, `_run_analyze`/`_run_dism` methods (running `/AnalyzeComponentStore` and `/StartComponentCleanup` respectively via a `Worker` on `get_long_op_pool()`), a `_dism_out` QTextEdit for output, and `_dism_thread_pool`. Confirm this still matches (line numbers may have drifted from other work this session) before editing.

- [ ] **Step 2: Write the failing tests for the Servicing tab**

Append to `tests/test_system_health_module.py`:

```python
def test_servicing_tab_has_three_buttons(qapp):
    module, app = _module(qapp)
    try:
        assert hasattr(module, "_scan_health_btn")
        assert hasattr(module, "_component_cleanup_btn")
        assert hasattr(module, "_reset_base_btn")
    finally:
        app.shutdown()


def test_reset_base_is_disabled_until_a_clean_scan_health(qapp):
    module, app = _module(qapp)
    try:
        assert module._reset_base_btn.isEnabled() is False
    finally:
        app.shutdown()


def test_scan_health_success_enables_reset_base(qapp, monkeypatch):
    from modules.system_health.servicing import DismResult

    module, app = _module(qapp)
    try:
        monkeypatch.setattr(
            "modules.system_health.servicing.run_scan_health",
            lambda: DismResult("dism /Online /Cleanup-Image /ScanHealth", 0, "No corruption"))
        module._run_scan_health()

        from PyQt6.QtCore import QThreadPool
        import time
        QThreadPool.globalInstance().waitForDone(5000)
        deadline = time.time() + 1
        while time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert module._last_scan_health_clean is True
        assert module._reset_base_btn.isEnabled() is True
    finally:
        app.shutdown()


def test_scan_health_failure_does_not_enable_reset_base(qapp, monkeypatch):
    from modules.system_health.servicing import DismResult

    module, app = _module(qapp)
    try:
        monkeypatch.setattr(
            "modules.system_health.servicing.run_scan_health",
            lambda: DismResult("dism /Online /Cleanup-Image /ScanHealth", 87, "Corruption found"))
        module._run_scan_health()

        from PyQt6.QtCore import QThreadPool
        import time
        QThreadPool.globalInstance().waitForDone(5000)
        deadline = time.time() + 1
        while time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert module._last_scan_health_clean is False
        assert module._reset_base_btn.isEnabled() is False
    finally:
        app.shutdown()


def test_reset_base_click_requires_typed_confirmation(qapp, monkeypatch):
    """The confirmation dialog's Ok button must stay disabled until the
    exact word RESETBASE is typed -- simulate this by driving the real
    dialog class directly rather than mocking QMessageBox.exec, since
    this needs a QDialog with a QLineEdit, not a plain QMessageBox."""
    from modules.system_health.system_health_module import _ResetBaseConfirmDialog

    dlg = _ResetBaseConfirmDialog()
    ok_button = dlg._ok_button
    assert ok_button.isEnabled() is False

    dlg._confirm_field.setText("wrong")
    assert ok_button.isEnabled() is False

    dlg._confirm_field.setText("RESETBASE")
    assert ok_button.isEnabled() is True


def test_reset_base_creates_a_restore_point_before_running_dism(qapp, monkeypatch):
    from modules.system_health.servicing import DismResult

    module, app = _module(qapp)
    try:
        module._last_scan_health_clean = True
        module._reset_base_btn.setEnabled(True)

        restore_calls = []
        monkeypatch.setattr(
            "core.system_restore.create_restore_point",
            lambda description, timeout=60: (restore_calls.append(description), (True, "ok"))[1])
        dism_calls = []
        monkeypatch.setattr(
            "modules.system_health.servicing.run_reset_base",
            lambda: (dism_calls.append(1), DismResult("dism ... /ResetBase", 0, "done"))[1])
        # Skip the interactive typed-confirmation dialog for this test --
        # call the internal method the dialog's Ok button would trigger.
        module._do_reset_base_confirmed()

        from PyQt6.QtCore import QThreadPool
        import time
        QThreadPool.globalInstance().waitForDone(5000)
        deadline = time.time() + 1
        while time.time() < deadline:
            qapp.processEvents()
            time.sleep(0.01)

        assert len(restore_calls) == 1
        assert len(dism_calls) == 1
    finally:
        app.shutdown()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_system_health_module.py -v -k "servicing or reset_base or scan_health"`
Expected: FAIL — none of these methods/classes exist yet.

- [ ] **Step 3: Implement the Servicing tab**

Replace `_build_servicing_tab`'s stub body in `system_health_module.py` and add the supporting methods/dialog class:

```python
class _ResetBaseConfirmDialog:
    """Typed-confirmation gate for Reset Base -- a checkbox is too easy
    to click through without reading; typing the literal word forces at
    least a moment's real attention."""

    def __init__(self, parent=None):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QDialogButtonBox
        self._dialog = QDialog(parent)
        self._dialog.setWindowTitle("Reset Base — Confirm")
        lay = QVBoxLayout(self._dialog)
        lay.addWidget(QLabel(
            "This runs:\n\n"
            "  dism /Online /Cleanup-Image /StartComponentCleanup /ResetBase\n\n"
            "This PERMANENTLY removes the ability to uninstall currently "
            "installed Windows updates. A restore point will be created "
            "automatically first. This cannot be undone by this app.\n\n"
            "Type RESETBASE to continue:"
        ))
        self._confirm_field = QLineEdit()
        lay.addWidget(self._confirm_field)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._ok_button = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setEnabled(False)
        self._confirm_field.textChanged.connect(
            lambda text: self._ok_button.setEnabled(text == "RESETBASE"))
        self._buttons.accepted.connect(self._dialog.accept)
        self._buttons.rejected.connect(self._dialog.reject)
        lay.addWidget(self._buttons)

    def exec(self) -> bool:
        from PyQt6.QtWidgets import QDialog
        return self._dialog.exec() == QDialog.DialogCode.Accepted
```

Add these methods to `SystemHealthModule`, and replace `_build_servicing_tab`:

```python
    def _build_servicing_tab(self) -> QWidget:
        from core.long_op_pool import get_long_op_pool
        tab = QWidget()
        lay = QVBoxLayout(tab)

        self._servicing_out = QLabel("")
        self._servicing_out.setWordWrap(True)
        self._servicing_out.setObjectName("muted")

        self._scan_health_btn = QPushButton("🔍  Check for Corruption (ScanHealth)")
        self._scan_health_btn.setToolTip(
            "Runs: dism /Online /Cleanup-Image /ScanHealth\n"
            "Checks the component store for corruption. Read-only, "
            "does not repair anything. Can take several minutes."
        )
        self._scan_health_btn.clicked.connect(self._run_scan_health)
        lay.addWidget(self._scan_health_btn)

        self._component_cleanup_btn = QPushButton("🗑️  Component Cleanup")
        self._component_cleanup_btn.setToolTip(
            "Runs: dism /Online /Cleanup-Image /StartComponentCleanup\n"
            "Removes superseded Windows components from WinSxS. "
            "Can reclaim 2–10 GB. Takes several minutes."
        )
        self._component_cleanup_btn.clicked.connect(self._run_component_cleanup)
        lay.addWidget(self._component_cleanup_btn)

        self._reset_base_btn = QPushButton("⚠️  Reset Base (permanent)")
        self._reset_base_btn.setEnabled(False)
        self._reset_base_btn.setToolTip(
            "Run Check for Corruption first, with a clean result, before "
            "Reset Base becomes available."
        )
        self._reset_base_btn.clicked.connect(self._on_reset_base_clicked)
        lay.addWidget(self._reset_base_btn)

        lay.addWidget(self._servicing_out)
        lay.addStretch()

        self._servicing_pool = get_long_op_pool()
        return tab

    def _run_scan_health(self) -> None:
        self._scan_health_btn.setEnabled(False)
        self._servicing_out.setText("Checking for corruption (can take several minutes)...")

        def _run(_worker):
            from modules.system_health import servicing
            return servicing.run_scan_health()

        def _done(result):
            self._scan_health_btn.setEnabled(True)
            self._last_scan_health_clean = (result.returncode == 0)
            self._reset_base_btn.setEnabled(self._last_scan_health_clean)
            self._servicing_out.setText(
                f"ScanHealth: {'no corruption found' if self._last_scan_health_clean else 'issue detected'} "
                f"(exit {result.returncode})\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="scan_health",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            self._scan_health_btn.setEnabled(True)
            self._last_scan_health_clean = False
            self._reset_base_btn.setEnabled(False)
            self._servicing_out.setText(f"Error: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._servicing_pool.start(w)

    def _run_component_cleanup(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        mb = QMessageBox(self._tabs)
        mb.setWindowTitle("Component Cleanup")
        mb.setIcon(QMessageBox.Icon.Information)
        mb.setText(
            "Runs: dism /Online /Cleanup-Image /StartComponentCleanup\n\n"
            "Removes superseded Windows components from WinSxS. Can "
            "reclaim 2–10 GB. Takes several minutes. Continue?"
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        mb.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if mb.exec() != QMessageBox.StandardButton.Ok:
            return

        self._component_cleanup_btn.setEnabled(False)
        self._servicing_out.setText("Running Component Cleanup (this can take several minutes)...")

        def _run(_worker):
            from modules.system_health import servicing
            return servicing.run_component_cleanup()

        def _done(result):
            self._component_cleanup_btn.setEnabled(True)
            self._servicing_out.setText(
                f"Component Cleanup finished (exit {result.returncode})\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="component_cleanup",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            self._component_cleanup_btn.setEnabled(True)
            self._servicing_out.setText(f"Error: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._servicing_pool.start(w)

    def _on_reset_base_clicked(self) -> None:
        if not self._last_scan_health_clean:
            return
        dlg = _ResetBaseConfirmDialog(self._tabs)
        if not dlg.exec():
            return
        self._do_reset_base_confirmed()

    def _do_reset_base_confirmed(self) -> None:
        self._reset_base_btn.setEnabled(False)
        self._servicing_out.setText("Creating a restore point before Reset Base...")

        def _run(_worker):
            from core.system_restore import create_restore_point
            from modules.system_health import servicing
            ok, msg = create_restore_point("Before System Health Reset Base")
            if not ok:
                raise RuntimeError(f"Could not create a restore point: {msg}")
            return servicing.run_reset_base()

        def _done(result):
            self._reset_base_btn.setEnabled(False)  # stays gated -- needs a fresh ScanHealth to re-enable
            self._last_scan_health_clean = False
            self._servicing_out.setText(
                f"Reset Base finished (exit {result.returncode})\n{result.output[:500]}")
            from modules.system_health import history
            history.append_run(self.app.app_data_dir, action="reset_base",
                               command=result.command, returncode=result.returncode)

        def _err(e: str):
            self._servicing_out.setText(f"Reset Base did not run: {e}")

        w = Worker(_run)
        w.signals.result.connect(_done)
        w.signals.error.connect(_err)
        self._servicing_pool.start(w)
```

- [ ] **Step 4: Remove the DISM section from `_large_items_tab.py`**

Delete `_analyze_btn`/`_dism_btn` construction, the `dism`/`dism_lay`/`dism_row`/`dism_desc`/`self._dism_out` block, `_run_analyze`/`_run_dism`, `self._analyze_worker`/`self._dism_worker`/`self._dism_thread_pool`, and their `_cancel_all()` references — read the file's current full content first (it has other content around this section, like the `_DriverStorePanel` and `_scan_tab`, that must stay). Leave everything else in this file untouched (the scan tab, the driver store panel).

- [ ] **Step 5: Find and update any existing test asserting on the removed DISM buttons**

Run: `grep -rln "_analyze_btn\|_dism_btn\|_run_analyze\|_run_dism" tests/*.py`
For each match, remove or migrate the assertion — if a test exists specifically for Large Items' DISM buttons, either delete it (with a comment explaining it moved to `tests/test_system_health_module.py`) or migrate it there, following whichever is the smaller, more honest change given what the test actually covers.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_system_health_module.py -v`
Expected: PASS, all tests in the file.

Run: `pytest tests/ -k "large_items or cleanup_module" -v` (or whatever the actual Large Items test file is named — find it via `grep -rl "_LargeItemsTab" tests/*.py`)
Expected: PASS, no failures from the removed DISM section.

- [ ] **Step 7: Run the FULL test suite**

Run: `pytest -q --timeout=300` (QT_QPA_PLATFORM=offscreen). Confirm exit code 0, no FAILURES, no crash (check exit code + grep for FAILED/ERROR, not the trailing summary line).

- [ ] **Step 8: Manual real-machine sanity check — READ-ONLY parts only**

This module runs real, slow, system-level DISM commands. Do NOT run Component Cleanup or Reset Base against the real machine as part of this task — those are genuinely slow (minutes) and Reset Base is irreversible. Instead:

```bash
python src/main.py
```

Navigate to System Health. Confirm: the Findings tab populates with real results (a pending.xml check, any real orphaned tasks on this machine, the real free-space number) without elevation. Confirm the Servicing tab's 3 buttons render with correct tooltips and Reset Base is disabled. This is a real, deliberate manual step for the READ-ONLY surface; the destructive actions remain unexercised by any automated or manual step in this plan, consistent with never running an irreversible operation as part of implementing this feature.

- [ ] **Step 9: Commit**

```bash
git add src/modules/system_health/system_health_module.py src/modules/cleanup/tabs/_large_items_tab.py tests/test_system_health_module.py
git commit -m "feat(system-health): add the Servicing tab (ScanHealth, Component Cleanup, gated Reset Base)

Component Cleanup is moved verbatim from Large Items -- a servicing
operation, not a file to select-and-delete. ScanHealth is new. Reset
Base is gated behind three independent things: a clean same-session
ScanHealth result, a typed RESETBASE confirmation (not a checkbox), and
a forced restore point created immediately before the DISM call -- and
stays gated again after running, requiring a fresh ScanHealth before it
can be used a second time."
```

---

## Task 6: `--unattended --stages health`

**Files:**
- Modify: `src/modules/updates/stage_runners.py`
- Modify: `src/modules/updates/unattended_runner.py`
- Test: existing stage_runners test file (find via `grep -rl "STAGE_RUNNERS\|run_dism_stage" tests/*.py`) — extend it

**Interfaces:**
- Produces: `run_health_stage(app, log: LogFn, is_cancelled: CancelFn) -> dict`, added to `STAGE_RUNNERS["health"]` and `STAGE_LABELS["health"]`; `"health"` added to `unattended_runner.VALID_STAGES`.

- [ ] **Step 1: Find the existing stage_runners test file**

Run: `grep -rl "STAGE_RUNNERS\|run_dism_stage\|run_cleanup_safe_stage" tests/*.py`
Read whichever file(s) it finds to learn the established test pattern for a stage runner before writing this task's own tests.

- [ ] **Step 2: Write the failing test**

Add to that same test file (or create `tests/test_system_health_stage.py` if none of the existing files is a natural fit — check first):

```python
def test_run_health_stage_calls_findings_not_servicing(monkeypatch):
    from modules.updates.stage_runners import run_health_stage

    calls = []
    monkeypatch.setattr(
        "modules.system_health.findings.all_findings",
        lambda: calls.append("findings") or [])
    # If run_health_stage ever imports/calls anything from servicing.py,
    # this makes it fail loudly rather than silently running real DISM.
    import modules.system_health.servicing as servicing_module
    def _forbidden(*a, **k):
        raise AssertionError("run_health_stage must never call servicing.py")
    monkeypatch.setattr(servicing_module, "run_scan_health", _forbidden)
    monkeypatch.setattr(servicing_module, "run_component_cleanup", _forbidden)
    monkeypatch.setattr(servicing_module, "run_reset_base", _forbidden)

    class _FakeApp:
        app_data_dir = "."

    import tempfile
    fake_app = _FakeApp()
    fake_app.app_data_dir = tempfile.mkdtemp()

    result = run_health_stage(fake_app, lambda msg: None, lambda: False)

    assert calls == ["findings"]
    assert isinstance(result, dict)


def test_health_is_a_valid_unattended_stage():
    from modules.updates.unattended_runner import VALID_STAGES
    assert "health" in VALID_STAGES


def test_health_stage_is_registered():
    from modules.updates.stage_runners import STAGE_RUNNERS, STAGE_LABELS
    assert "health" in STAGE_RUNNERS
    assert "health" in STAGE_LABELS
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest -k "run_health_stage or health_is_a_valid or health_stage_is_registered" -v`
Expected: FAIL — `run_health_stage` doesn't exist, `"health"` not in `VALID_STAGES`/`STAGE_RUNNERS`.

- [ ] **Step 4: Add `run_health_stage`**

In `src/modules/updates/stage_runners.py`, add (near `run_dism_stage`, before `STAGE_RUNNERS`):

```python
def run_health_stage(app, log: LogFn, is_cancelled: CancelFn) -> dict:
    """Read-only System Health findings only -- never ScanHealth,
    Component Cleanup, or Reset Base. Writes its own result to
    System Health's own history file (a separate stream from Update
    Center's, per that module's own design), not into this function's
    return dict, which stays a plain {findings_count} for STAGE_RUNNERS'
    homogeneous -> dict contract."""
    from modules.system_health import findings, history
    log("System Health: checking for pending servicing, orphaned tasks, and upgrade headroom...")
    results = findings.all_findings()
    for finding in results:
        log(f"  [{finding.severity}] {finding.title}")
    history.append_run(app.app_data_dir, action="unattended_findings",
                       command="", returncode=0, findings_count=len(results))
    return {"findings_count": len(results)}
```

Add to `STAGE_RUNNERS`:

```python
STAGE_RUNNERS = {
    "wu": run_wu_stage,
    "winget": run_winget_stage,
    "store": run_store_stage,
    "cleanup": run_cleanup_safe_stage,
    "dism": run_dism_stage,
    "health": run_health_stage,
}
```

And to `STAGE_LABELS`:

```python
STAGE_LABELS = {
    "wu": "Windows Update",
    "winget": "winget",
    "store": "Microsoft Store",
    "cleanup": "Cleanup (safe)",
    "dism": "DISM WinSxS cleanup",
    "health": "System Health findings",
}
```

- [ ] **Step 5: Add `"health"` to `unattended_runner.py`'s `VALID_STAGES`**

In `src/modules/updates/unattended_runner.py`, change:

```python
VALID_STAGES = ("wu", "winget", "store", "cleanup", "dism")
```

to:

```python
VALID_STAGES = ("wu", "winget", "store", "cleanup", "dism", "health")
```

Note: `normalize_stage_data` (also in `stage_runners.py`) is deliberately NOT modified to add a `"health"` case — it stays scoped to Update Center's own wu/winget/store/cleanup/dism report shape, per this module's own separate-history-stream design (Task 3). Running `--unattended --stages health` alone will still produce an Update Center HTML report showing zeros for the other stages (since `normalize_stage_data` defaults absent keys to empty), which is a known, accepted, non-breaking quirk of `unattended_runner.py`'s existing generic orchestration loop — not something this task's scope extends to fixing.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest -k "run_health_stage or health_is_a_valid or health_stage_is_registered" -v`
Expected: PASS, all 3 tests.

- [ ] **Step 7: Run the full existing stage_runners/unattended test suite**

Run whichever test file(s) Step 1 found, in full, to confirm no regression to the other 5 stages.

- [ ] **Step 8: Run the FULL test suite**

Run: `pytest -q --timeout=300` (QT_QPA_PLATFORM=offscreen). Confirm exit code 0, no FAILURES, no crash.

- [ ] **Step 9: Commit**

```bash
git add src/modules/updates/stage_runners.py src/modules/updates/unattended_runner.py tests/
git commit -m "feat(system-health): add the read-only --unattended --stages health stage

run_health_stage calls findings.all_findings() only, writes to System
Health's own separate history file, and never touches servicing.py --
pinned by a test that fails loudly if it ever does. normalize_stage_data
is deliberately untouched (stays scoped to Update Center's own report
shape); a health-only unattended run still produces an Update Center
report showing zeros for the other stages, a known non-breaking quirk
of the existing generic orchestration loop, not fixed here."
```

---

## Self-Review

**Spec coverage:** all 8 "IN" items from spec §1's table have a task: pending servicing (Task 1), orphaned tasks (Task 1), upgrade headroom (Task 1), moved StartComponentCleanup (Task 5), ScanHealth (Task 2/5), gated ResetBase (Task 2/5), command transparency (every dialog in Task 5 states its literal command), JSON audit log (Task 3), `--unattended --stages health` (Task 6). The "OUT" items (orphaned services, VSS floor, RestoreHealth, generic dry-run) correctly have no task.

**Placeholder scan:** Task 4 Step 3 contains one flagged, immediately-resolved rough edge (the `__import__`-based `QColor` construction, explicitly called out as needing a proper top-level import) rather than a silent TBD.

**Type consistency:** `Finding`/`DismResult` dataclass shapes are used identically across Tasks 1-2's own modules and Tasks 4-6's consumers. `run_health_stage`'s signature matches every other `STAGE_RUNNERS` entry exactly (`(app, log: LogFn, is_cancelled: CancelFn) -> dict`).

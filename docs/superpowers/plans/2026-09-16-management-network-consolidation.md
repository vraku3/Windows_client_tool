# Management + Network Tab Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the sidebar from 32 to 28 entries by re-hosting 5
existing, unchanged modules as children of two composite hubs — a new
one for Scheduled Tasks/Services/Windows Features, and folding Shared
Resources/Remote Tools into the existing Network Diagnostics composite.

**Architecture:** Pure re-hosting, following the exact `CompositeModule`
pattern already used by 4 other hubs in this app. Zero functional
changes to any of the 5 modules being moved.

**Tech Stack:** PyQt6, existing `CompositeModule`/`BaseModule` infrastructure.

**Spec:** `docs/superpowers/specs/2026-09-16-management-network-consolidation-design.md`

## Global Constraints

- The 5 modules being re-hosted (`TasksModule`, `ServicesModule`,
  `WindowsFeaturesModule`, `SharesModule`, `RemoteToolsModule`) must not
  change at all — no edits to their own files.
- Sidebar count must become exactly 28 (from 32).
- `CompositeModule`'s existing per-child elevation gating (a child
  needing admin it doesn't have becomes a disabled tab, per CLAUDE.md)
  needs no new code — it already handles mixed `requires_admin` values
  among children.

---

### Task 1: New "System Management" composite hub

**Files:**
- Create: `src/modules/system_management/__init__.py` (empty)
- Create: `src/modules/system_management/system_management_module.py`
- Modify: `src/main.py`
- Test: `tests/test_module_inventory.py`
- Test: `tests/test_system_management_module.py` (new)

**Interfaces:**
- Consumes: `TasksModule`, `ServicesModule`, `WindowsFeaturesModule`
  (unchanged, existing classes).
- Produces: `SystemManagementModule`, registered in `main.py` in place
  of the 3 modules it now hosts.

- [ ] **Step 1: Create the package**

```bash
mkdir -p src/modules/system_management
touch src/modules/system_management/__init__.py
```

- [ ] **Step 2: Write `system_management_module.py`**

```python
"""System Management — scheduled tasks, services, and Windows optional
features in one place. All three were separate ModuleGroup.MANAGE
sidebar entries with no shared code -- this hub is pure re-hosting, not
a rewrite. Each child stays an ordinary BaseModule that knows nothing
about being hosted (per core/composite_module.py's own documented
pattern), so it is unchanged and can still be tested alone.
"""
from core.composite_module import CompositeModule
from core.module_groups import ModuleGroup


class SystemManagementModule(CompositeModule):
    name = "System Management"
    icon = "🗂️"
    description = "Scheduled tasks, services, and Windows optional features"
    group = ModuleGroup.MANAGE
    requires_admin = False

    def __init__(self):
        super().__init__()
        from modules.scheduled_tasks.tasks_module import TasksModule
        from modules.services_manager.services_module import ServicesModule
        from modules.windows_features.features_module import WindowsFeaturesModule

        self.children = [TasksModule(), ServicesModule(), WindowsFeaturesModule()]
```

- [ ] **Step 3: Update `main.py`**

Remove these 3 imports:
```python
    from modules.scheduled_tasks.tasks_module import TasksModule
    from modules.windows_features.features_module import WindowsFeaturesModule
```
(keep note: `from modules.services_manager.services_module import ServicesModule`
is under the `# Track 2 — New Tool Modules` comment block, a different
location — remove it from there specifically.)

Remove these 3 registrations:
```python
    app.module_registry.register(TasksModule())
    app.module_registry.register(WindowsFeaturesModule())
    ...
    app.module_registry.register(ServicesModule())
```

Add, alongside the other composite imports/registrations:
```python
    from modules.system_management.system_management_module import SystemManagementModule
```
```python
    app.module_registry.register(SystemManagementModule())
```

Read the current `main.py` first — the exact insertion points must
match its real current structure (grouped comments like `# Batch B —
Manage group`, `# Track 2`), not be guessed from this snippet alone.

- [ ] **Step 4: Write `tests/test_system_management_module.py`**

Mirror `StartupBootModule`'s own test file's shape as closely as
possible — read it first (`tests/test_startup_boot_module.py` or
wherever it lives; find it with `grep -rl "StartupBootModule" tests/`)
and match its exact fixture/assertion style rather than inventing a new
one. At minimum:

```python
def test_the_hub_has_three_children(qapp):
    from modules.system_management.system_management_module import SystemManagementModule
    mod = SystemManagementModule()
    assert len(mod.children) == 3
    names = {type(c).__name__ for c in mod.children}
    assert names == {"TasksModule", "ServicesModule", "WindowsFeaturesModule"}


def test_the_hub_builds_a_widget_with_all_three_tabs(qapp):
    from modules.system_management.system_management_module import SystemManagementModule

    class FakeApp:
        backup = None
        config = None
        thread_pool = None

    mod = SystemManagementModule()
    mod.on_start(FakeApp())
    widget = mod.create_widget()
    assert widget is not None
```

(Adjust the `FakeApp` shape to match whatever `TasksModule`/
`ServicesModule`/`WindowsFeaturesModule`'s own `on_start`/`create_widget`
actually read off `app` — check each one's `on_start` body first rather
than guessing; add whatever attributes they need.)

- [ ] **Step 5: Update the sidebar-count test**

In `tests/test_module_inventory.py`, the count is currently 32 (from
the prior module-consolidation round). This task alone brings it to 30
(32 − 3 individual + 1 hub). Do NOT set it to 28 yet — that's the count
after Task 2 too. Update the assertion to `30` for now if this task is
reviewed/merged before Task 2, or leave both changes in the same PR if
executing both tasks before any review checkpoint (check the ledger/plan
execution order — if Task 2 runs immediately after in the same
worktree before any test run is checked in isolation, it's fine to only
assert the final `28` once both tasks are done; note this explicitly in
your commit message either way so the count's history is traceable).

- [ ] **Step 6: Run the tests**

Run: `python -m pytest -q tests/test_system_management_module.py tests/test_module_inventory.py --timeout=120`
Expected: PASS (module inventory test's exact expected count depends on
Step 5's choice above).

- [ ] **Step 7: Commit**

```bash
git add src/modules/system_management src/main.py tests/test_system_management_module.py tests/test_module_inventory.py
git commit -m "feat: add System Management hub hosting Scheduled Tasks/Services/Windows Features"
```

---

### Task 2: Fold Shared Resources + Remote Tools into Network Diagnostics

**Files:**
- Modify: `src/modules/network_diagnostics/network_module.py`
- Modify: `src/main.py`
- Test: `tests/test_module_inventory.py`
- Test: whatever existing Network Diagnostics test file covers its
  composite children (find with `grep -rl "NetworkDiagnosticsModule"
  tests/`)

**Interfaces:**
- Consumes: `SharesModule`, `RemoteToolsModule` (unchanged, existing classes).
- Produces: `NetworkDiagnosticsModule.children` gains 2 more entries.

- [ ] **Step 1: Read the current file**

Read `src/modules/network_diagnostics/network_module.py`'s
`NetworkDiagnosticsModule.__init__` in full to confirm its current
`self.children` list still matches what the spec assumes (4 entries:
`NetworkToolsModule`, `WifiAnalyzerModule`, `HostsEditorModule`,
`NetExtrasModule`) before editing.

- [ ] **Step 2: Add the two new children**

```python
    def __init__(self):
        super().__init__()
        from modules.hosts_editor.hosts_editor_module import HostsEditorModule
        from modules.network_extras.net_extras_module import NetExtrasModule
        from modules.wifi_analyzer.wifi_module import WifiAnalyzerModule
        from modules.shared_resources.shares_module import SharesModule
        from modules.remote_tools.remote_module import RemoteToolsModule

        self.children = [
            NetworkToolsModule(),
            WifiAnalyzerModule(),
            HostsEditorModule(),
            NetExtrasModule(),
            SharesModule(),
            RemoteToolsModule(),
        ]
```

- [ ] **Step 3: Update `main.py`**

Remove the `SharesModule`/`RemoteToolsModule` imports and
`app.module_registry.register(SharesModule())` /
`app.module_registry.register(RemoteToolsModule())` calls — read the
current file first to find their exact location (`# Batch C` comment
block per this app's existing grouping comments).

- [ ] **Step 4: Update or add composite-children tests**

Find the existing test file covering `NetworkDiagnosticsModule`'s
children (`grep -rl "NetworkDiagnosticsModule" tests/`) and add an
assertion that `SharesModule`/`RemoteToolsModule` (by class name) are
now among `mod.children`. If no such file exists, add a small test to
whichever file already tests this composite's widget-building (check
`test_module_inventory.py`'s own composite-list tests, e.g. lines near
"PerfMon" in `dashboard.children" for the existing pattern of asserting
a name is among a composite's children).

- [ ] **Step 5: Update the sidebar-count test**

`tests/test_module_inventory.py`: the count is now 28 (30 from Task 1,
minus 2 for these). Update `test_the_sidebar_is_N_entries`'s name,
docstring, and assertion to `28`. The docstring should explain the
running history (Task 1's hub took it to 30, this task takes it to 28)
the same way the existing docstring already explains the 33→32 story.

- [ ] **Step 6: Run the tests**

Run: `python -m pytest -q tests/test_module_inventory.py --timeout=120`
Plus whatever Network Diagnostics test file was touched in Step 4.
Expected: PASS, count is 28.

- [ ] **Step 7: grep sweep**

Run: `grep -rn "app.module_registry.register(TasksModule\|app.module_registry.register(ServicesModule\|app.module_registry.register(WindowsFeaturesModule\|app.module_registry.register(SharesModule\|app.module_registry.register(RemoteToolsModule" src/main.py`
Expected: no output — all 5 standalone registrations gone, only the 2
composite hosts remain.

- [ ] **Step 8: Commit**

```bash
git add src/modules/network_diagnostics/network_module.py src/main.py tests/
git commit -m "feat: fold Shared Resources and Remote Tools into Network Diagnostics"
```

---

### Task 3: Documentation + final verification

**Files:**
- Modify: `CLAUDE.md`
- Modify: `AGENTS.md`
- Test: full suite

- [ ] **Step 1: Update CLAUDE.md's Composite Modules section**

Add `System Management` to the list of composites (currently names
`Diagnose`, `Debloat`, `Startup & Boot` and `Network Diagnostics` as
"the four" — this becomes 5, update that count/wording). Note Network
Diagnostics now also hosts Shared Resources and Remote Tools.

- [ ] **Step 2: Update AGENTS.md's Composite Modules section**

Same change, matching AGENTS.md's own condensed style (see its existing
Composite Modules section, added in the prior module-consolidation
round).

- [ ] **Step 3: Full suite**

```bash
rm -rf .pytest-tmp
python -m pytest -q --timeout=300
```
Expected: exit 0, sidebar count 28, no FAILED/ERROR beyond the
documented pre-existing `.pytest-tmp` stale-lock or unrelated flakes.

- [ ] **Step 4: Manual sidebar-count sanity check**

```bash
python -c "import sys; sys.path.insert(0, 'src'); from app import App; from main import register_all_modules; app = App(); register_all_modules(app); print(len(app.module_registry.modules))"
```
Expected: `28`.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: update composite module list for the Management + Network consolidation"
```

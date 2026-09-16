# Management + Network Tab Consolidation

## §0. Context and rulings

This is a continuation of the tab-merging pattern established by the
Cleanup+QuickCleanup merge and the Performance Tuner / Quick Fix
consolidation ([[module-consolidation]] memory). Those two rounds were
picked from a broader brainstorm that also flagged two weaker
candidates — no functional duplication, just an unclaimed natural home
or an UX-tidiness win — deliberately deferred at the time. The user
asked to fix everything left in the backlog; this spec covers those two
candidates, both approved via a short design confirmation before this
document was written:

1. **New composite hub hosting Scheduled Tasks + Services + Windows
   Features** — three small (354–729 line), standalone `ModuleGroup.MANAGE`
   modules, all "a list of OS-managed things you can enable/disable,"
   with no shared code today.
2. **Fold Shared Resources + Remote Tools into the existing Network
   Diagnostics composite** — two small (238/312 line) `ModuleGroup.TOOLS`
   modules with no home of their own; Network Diagnostics already hosts
   4 similar network-admin tools as composite children (`NetworkToolsModule`,
   `WifiAnalyzerModule`, `HostsEditorModule`, `NetExtrasModule`).

**Ruling**: unlike the Performance Tuner/Quick Fix round, this is pure
re-hosting — zero functional changes to any of the 5 modules being
moved. `CompositeModule` already supports children with different
`requires_admin` values (a child needing elevation it doesn't have
becomes a disabled tab carrying the reason, per CLAUDE.md's Composite
Modules section) — `StartupBootModule` is the working precedent
(`requires_admin=False` at the host level, its own children self-gate).
Services (`requires_admin=True`) and Windows Features
(`requires_admin=True`) sitting beside Scheduled Tasks
(`requires_admin=False`) needs no special handling beyond what
`CompositeModule` already does.

## §1. Goals

- Reduce the sidebar from 32 to 28 entries.
- Zero functional change to any of the 5 modules being re-hosted — same
  classes, same behavior, only their registration/hosting changes.
- Follow the exact `CompositeModule` pattern already used by
  `DiagnoseModule`/`DebloatModule`/`StartupBootModule`/`NetworkDiagnosticsModule`.

## §2. Part A — New "System Management" hub

New `CompositeModule` at `src/modules/system_management/system_management_module.py`:

```python
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

`main.py` drops the 3 standalone registrations
(`TasksModule`/`ServicesModule`/`WindowsFeaturesModule`) and registers
`SystemManagementModule()` in their place. `TasksModule`,
`ServicesModule`, `WindowsFeaturesModule` themselves are UNCHANGED —
they already work as ordinary `BaseModule`s, which is exactly what a
composite child needs to be (per CLAUDE.md: "a child stays an ordinary
module that knows nothing about being hosted").

## §3. Part B — Fold Shared Resources + Remote Tools into Network Diagnostics

`src/modules/network_diagnostics/network_module.py`'s
`NetworkDiagnosticsModule.__init__` gains two more children:

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

`main.py` drops the 2 standalone registrations
(`SharesModule`/`RemoteToolsModule`). Both are `requires_admin=False`,
matching every existing child here — no elevation-mixing concern.
`SharesModule`/`RemoteToolsModule` themselves are UNCHANGED.

## §4. Testing

- `tests/test_module_inventory.py`: sidebar-count test → 28 (from 32),
  docstring updated with the reasoning (matches the existing pattern of
  explaining each count change in that test's own docstring).
- New/extended composite tests for `SystemManagementModule` mirroring
  `StartupBootModule`'s own test file's shape (construct the composite,
  confirm 3 children, confirm each child's widget builds under the
  composite's `create_widget()`).
- `NetworkDiagnosticsModule`'s existing test file (if any) gains an
  assertion that `SharesModule`/`RemoteToolsModule` are now among its
  children.
- `_all_composite_children`-style helper (referenced in
  `test_module_inventory.py`'s existing sidebar-count docstring) must
  still find `TasksModule`, `ServicesModule`, `WindowsFeaturesModule`,
  `SharesModule`, `RemoteToolsModule` as reachable, non-orphaned modules
  — moving to composite hosting must not make search/inventory checks
  blind to them.
- Full suite run before and after.

## §5. Documentation

- `CLAUDE.md`/`AGENTS.md`: add `SystemManagementModule` to the Composite
  Modules list (now 5 composites, not 4); note Network Diagnostics'
  2 new children.

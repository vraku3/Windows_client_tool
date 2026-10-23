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

    def wrap(self, tabs):
        from modules.diagnose.help_ui import hub
        return hub(tabs, "Tasks run programs on a schedule, services run in the background, and optional features add Windows components. Reading the inventory does not change the PC; actions can affect other apps and protected changes need administrator rights. Enable/Disable can be changed again, but task deletion and work lost during service interruption have no undo here.", {
            "Scheduled Tasks": "Inspect task commands, accounts and triggers. Changes ask first and protected tasks need administrator rights; Enable/Disable are reversible, while deletion has no built-in undo.",
            "Services": "Inspect background services and dependencies. Actions can interrupt work and generally need administrator rights; startup settings can be changed again, but the previous startup type is not saved.",
            "Windows Features": "Inspect or change optional Windows components. Changes ask first and need administrator rights, and may need a restart; the opposite action changes state again without restoring application data.",
        })

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

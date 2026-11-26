"""System Utilities composite hub: Environment Variables, Registry Explorer, System Report."""
from core.composite_module import CompositeModule
from core.module_groups import ModuleGroup
from modules.env_vars.env_vars_module import EnvVarsModule
from modules.registry_explorer.registry_module import RegistryExplorerModule
from modules.system_report.report_module import SystemReportModule


class UtilitiesHubModule(CompositeModule):
    name = "System Utilities"
    icon = "🧰"
    description = "Environment variables, Registry explorer, and full HTML/text system report."
    group = ModuleGroup.TOOLS

    def __init__(self) -> None:
        super().__init__()
        self.children = [
            EnvVarsModule(),
            RegistryExplorerModule(),
            SystemReportModule(),
        ]

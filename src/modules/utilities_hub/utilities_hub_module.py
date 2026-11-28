"""System Utilities composite hub: Software Inventory, System Management, Control Panel, Environment Variables, Registry Explorer, System Report."""
from core.composite_module import CompositeModule
from core.module_groups import ModuleGroup
from modules.control_panel.control_panel_module import ControlPanelModule
from modules.env_vars.env_vars_module import EnvVarsModule
from modules.registry_explorer.registry_module import RegistryExplorerModule
from modules.software_inventory.software_module import SoftwareModule
from modules.system_management.system_management_module import SystemManagementModule
from modules.system_report.report_module import SystemReportModule


class UtilitiesHubModule(CompositeModule):
    name = "System Utilities"
    icon = "🧰"
    description = (
        "Software inventory, system management (tasks, services, features), classic Control Panel, "
        "environment variables, Registry explorer, and full HTML/text system report."
    )
    group = ModuleGroup.TOOLS

    def __init__(self) -> None:
        super().__init__()
        self.children = [
            SoftwareModule(),
            SystemManagementModule(),
            ControlPanelModule(),
            EnvVarsModule(),
            RegistryExplorerModule(),
            SystemReportModule(),
        ]

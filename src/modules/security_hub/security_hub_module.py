"""Security & Policies composite hub: Security Dashboard, Firewall, Certificates, GPResult, Local Users."""
from core.composite_module import CompositeModule
from core.module_groups import ModuleGroup
from modules.certificate_viewer.cert_module import CertModule
from modules.firewall_rules.firewall_manager_module import FirewallManagerModule
from modules.gpresult.gpresult_module import GPResultModule
from modules.local_users.users_module import LocalUsersModule
from modules.security_dashboard.security_module import SecurityDashboardModule


class SecurityHubModule(CompositeModule):
    name = "Security & Policies"
    icon = "🛡️"
    description = "Security posture, Windows Firewall rules, certificates, Group Policy, and local accounts."
    group = ModuleGroup.MANAGE

    def __init__(self) -> None:
        super().__init__()
        self.children = [
            SecurityDashboardModule(),
            FirewallManagerModule(),
            CertModule(),
            GPResultModule(),
            LocalUsersModule(),
        ]

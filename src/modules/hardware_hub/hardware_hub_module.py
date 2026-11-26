"""Hardware & Tuning composite hub: Hardware Info, Thermal Control, Monitor Control, Stress Test."""
from core.composite_module import CompositeModule
from core.module_groups import ModuleGroup
from modules.hardware_inventory.hardware_module import HardwareModule
from modules.monitor_control.monitor_module import MonitorControlModule
from modules.stress_test.stress_module import StressModule
from modules.thermal_control.thermal_module import ThermalControlModule


class HardwareHubModule(CompositeModule):
    name = "Hardware & Tuning"
    icon = "🖥️"
    description = "Hardware inventory, PCIe/NVMe audit, fan curves, display DDC/CI, and stress testing."
    group = ModuleGroup.SYSTEM

    def __init__(self) -> None:
        super().__init__()
        self.children = [
            HardwareModule(),
            ThermalControlModule(),
            MonitorControlModule(),
            StressModule(),
        ]

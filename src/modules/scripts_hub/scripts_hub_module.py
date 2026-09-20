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

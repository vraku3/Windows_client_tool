"""Scripts -- a home for hand-rolled utility tools: the Script Library
(read-only admin checks, the user's own scripts, the support bundle) and File
Forensics. Adding another tool is adding another child here, the same way
Startup & Boot grew from two children to three.
"""
from core.composite_module import CompositeModule
from core.module_groups import ModuleGroup


class ScriptsModule(CompositeModule):
    name = "Scripts"
    icon = "🧰"
    description = "Admin script library, support bundle and file forensics"
    group = ModuleGroup.TOOLS
    requires_admin = False

    def __init__(self):
        super().__init__()
        from modules.file_forensics.file_forensics_module import FileForensicsModule
        from modules.scripts_hub.library_module import ScriptLibraryModule
        from modules.nav_pane.nav_pane_module import NavPaneModule

        self.children = [ScriptLibraryModule(), FileForensicsModule(), NavPaneModule()]

    def wrap(self, tabs):
        for index, child in enumerate(self.children):
            tabs.setTabToolTip(index, child.description)
        return tabs

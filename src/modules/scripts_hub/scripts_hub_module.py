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
        from PyQt6.QtWidgets import QWidget, QVBoxLayout
        from ui.module_intro import module_intro
        from ui.help.scripts_ui import explain_scripts
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(module_intro("Choose Script Library to read and run admin scripts, File Forensics to investigate files without changing them, or Explorer Navigation Pane to customize Explorer. Running scripts and applying Explorer settings can change this PC; read the selected tool?s explanation first."))
        layout.addWidget(tabs)
        explain_scripts(root)
        return root

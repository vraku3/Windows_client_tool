"""One row in App Buster's list, whatever kind of app it is.

Qt-free. The vocabulary follows O&O AppBuster's own manual
(manuals.oo-software.com/ooappbuster): six TYPES, four STATUS values, three
recommendation labels, three "available to" answers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

# ---- Type ------------------------------------------------------------------
WINDOWS = "Windows app"        # Store / UWP / MSIX package, removable
DESKTOP = "Desktop app"        # Win32 / MSI, removed through its own uninstaller
SYSTEM = "System app"          # part of Windows; informational only
FRAMEWORK = "Framework"        # a runtime other packages depend on
ORPHANED = "Orphaned"          # a leftover with nothing installed behind it
DEFECT = "Defect"              # installed, but its uninstall metadata is broken

TYPES = (WINDOWS, DESKTOP, SYSTEM, FRAMEWORK, ORPHANED, DEFECT)

# ---- Status ----------------------------------------------------------------
INSTALLED = "Installed"
INSTALLABLE = "Installable"            # on this PC (provisioned) but not for you
STAGED = "Staged (not installed)"      # files staged, registered for nobody
UNREMOVABLE = "Unremovable"            # system / framework / NonRemovable

# ---- Recommendation --------------------------------------------------------
KEEP = "keep"
OPTIONAL = "optional"
REMOVE = "remove"
RECOMMENDATION_RANK = {REMOVE: 0, OPTIONAL: 1, KEEP: 2}
RECOMMENDATION_TIP = {KEEP: "Keep application.",
                      OPTIONAL: "Keep or remove application.",
                      REMOVE: "Remove application."}

# ---- Available to ----------------------------------------------------------
FOR_YOU = "You"
FOR_SEVERAL = "Several users"
FOR_PC = "Whole computer"

# ---- Kinds of leftover (what a cleanup of an Orphaned/Defect row deletes) --
LEFTOVER_DATA_FOLDER = "data_folder"       # %LocalAppData%\Packages\<family>
LEFTOVER_UNINSTALL_ENTRY = "uninstall_entry"
LEFTOVER_MSI_REGISTRATION = "msi_registration"


@dataclass
class AppRecord:
    """Everything the list, the filters and the actions need about one app.

    A value that could not be read is None, never 0 or "": Storage None means
    "not measured", which is not the same as an app that takes no space.
    """
    key: str                    # stable identity across scans
    name: str
    type: str
    status: str = INSTALLED
    publisher: str = ""
    version: str = ""
    architecture: str = ""
    description: str = ""
    installed: Optional[datetime] = None
    files_bytes: Optional[int] = None
    data_bytes: Optional[int] = None
    available: str = FOR_YOU
    recommendation: str = KEEP
    update: str = ""            # newer version winget offers, "" when none
    winget_id: str = ""
    hidden: bool = False        # no Start-menu entry / SystemComponent
    removable: bool = True
    install_location: str = ""
    icon_path: str = ""
    # Windows-app identity
    package_name: str = ""
    full_name: str = ""
    family: str = ""
    publisher_id: str = ""
    signature: str = ""
    # Desktop-app identity
    registry_key: str = ""      # e.g. HKLM\SOFTWARE\...\Uninstall\{GUID}
    product_code: str = ""
    uninstall_string: str = ""
    modify_path: str = ""
    windows_installer: bool = False
    # Orphaned / defect detail
    leftover: str = ""
    leftover_paths: tuple = ()          # folders/keys a cleanup removes
    reason: str = ""                    # why it is Orphaned / Defect
    purpose: str = ""                   # orphaned: likely purpose
    vendor: str = ""                    # orphaned: presumed vendor / family
    confidence: str = ""                # orphaned: High / Medium / Unknown
    extra: Dict[str, str] = field(default_factory=dict)

    @property
    def storage(self) -> Optional[int]:
        if self.files_bytes is None and self.data_bytes is None:
            return None
        return (self.files_bytes or 0) + (self.data_bytes or 0)

    @property
    def is_windows(self) -> bool:
        return bool(self.full_name or self.family) and self.type != DESKTOP

    @property
    def can_uninstall(self) -> bool:
        return self.removable and self.status in (INSTALLED, STAGED) or self.type in (ORPHANED, DEFECT)

    @property
    def can_install(self) -> bool:
        return self.status in (INSTALLABLE, STAGED) and bool(self.family)

    @property
    def can_modify(self) -> bool:
        return self.type == DESKTOP and bool(self.modify_path)

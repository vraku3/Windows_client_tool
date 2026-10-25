"""Typed outcomes; an unreadable entry is never a missing entry."""
from dataclasses import dataclass
from enum import StrEnum


class Kind(StrEnum):
    MANAGED = "Made here"
    HANDMADE = "Made outside this app"
    SYSTEM = "Windows / OneDrive (read-only)"


class Health(StrEnum):
    OK = "OK"
    TARGET_MISSING = "Target missing"
    JUNCTION_BROKEN = "Junction broken"
    HIDDEN_ON_DESKTOP = "Desktop icon hiding missing"
    UNREADABLE = "Could not read"
    INCOMPLETE = "Incomplete operation"
    ORPHAN = "Partially removed by another program"


class Verification(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_LISTED = "NOT_LISTED"
    WRONG_NAME = "WRONG_NAME"
    COULD_NOT_CHECK = "COULD_NOT_CHECK"


@dataclass
class VerifyResult:
    status: Verification
    detail: str


@dataclass
class NavEntry:
    guid: str
    name: str = ""
    target: str = ""
    resolved_target: str = ""
    junction: str | None = None
    sort_order: int = 48
    icon: str = r"%SystemRoot%\system32\imageres.dll,-3"
    kind: Kind = Kind.SYSTEM
    health: Health = Health.OK
    detail: str = ""


CLSID = r"Software\Classes\CLSID"
EXPLORER = r"Software\Microsoft\Windows\CurrentVersion\Explorer"
NAMESPACE = EXPLORER + r"\Desktop\NameSpace"
HIDE = EXPLORER + r"\HideDesktopIcons\NewStartPanel"
ADVANCED = EXPLORER + r"\Advanced"
FOLDER_SHORTCUT = "{0AFACED1-E828-11D1-9187-B532F1E9575D}"
DEFAULT_ICON = r"%SystemRoot%\system32\imageres.dll,-3"


def values_for(guid, name, target, order, icon):
    """Exact registry recipe, including original value types."""
    root = CLSID + "\\" + guid
    return [(root, "", name, 1), (root, "LocalizedString", name, 1),
            (root, "System.IsPinnedToNameSpaceTree", 1, 4),
            (root, "SortOrderIndex", order, 4),
            (root + r"\DefaultIcon", "", icon, 2),
            (root + r"\InProcServer32", "", r"%SystemRoot%\system32\shell32.dll", 2),
            (root + r"\Instance", "CLSID", FOLDER_SHORTCUT, 1),
            (root + r"\Instance\InitPropertyBag", "Attributes", 0x11, 4),
            (root + r"\Instance\InitPropertyBag", "Target", target, 1),
            (root + r"\ShellFolder", "FolderValueFlags", 0x28, 4),
            (root + r"\ShellFolder", "Attributes", 0xF080004D, 4),
            (NAMESPACE + "\\" + guid, "", name, 1), (HIDE, guid, 1, 4)]

"""Where a package came from, and Windows' own word on whether it can go.

Qt-free. Both answers come from Get-AppxPackage's own fields rather than a
name list (measured on this machine, 2026-10-09, 154 packages unelevated):

* **`NonRemovable` is Windows' answer to "can this be uninstalled?"** -- the
  flag Settings > Apps greys Uninstall out on; `Remove-AppxPackage` refuses
  it with 0x80073CFA. 52 packages here carry it. The pane's older rule (a
  name set, or an install under SystemApps) agreed on all but three, and two
  of those three were the dangerous direction: `windows.immersivecontrolpanel`
  (the **Settings app**, installed under `C:\\Windows\\ImmersiveControlPanel`)
  and `Microsoft.SecHealthUI` (**Windows Security**, under
  `Program Files\\WindowsApps`) were shown "Yes, removable" and picked up by
  "Select Non-System". The third, `Microsoft.WindowsStore`, is NOT
  NonRemovable but stays protected by the name list -- so the rules are
  OR-ed, never one replacing the other.
* **`SignatureKind` says where a package came from**: 83 Store, 50 System,
  21 Developer here. Developer-signed means it did not come through the
  Store -- sideloaded, or dropped as an MSIX by another installer (Teams,
  Edge, OneDrive, VS Code, Notepad++'s shell extension, a Bitdefender context
  menu, a Gigabyte utility). That is the population an admin audits first,
  because the Store's own vetting never saw it. `Enterprise` (signed with an
  organisation's certificate) and `None` (unsigned, developer mode) were not
  present here but are the documented values.
"""
from typing import Tuple

SOURCE_STORE = "Store"
SOURCE_WINDOWS = "Windows"
SOURCE_SIDELOADED = "Sideloaded"
SOURCE_ENTERPRISE = "Enterprise"
SOURCE_UNSIGNED = "Unsigned"

_SOURCES = {
    "store": (SOURCE_STORE, "Signed by the Microsoft Store."),
    "system": (SOURCE_WINDOWS, "Signed as part of Windows itself."),
    "developer": (SOURCE_SIDELOADED,
                  "Developer-signed: installed outside the Store (sideloaded, "
                  "or deployed as an MSIX by another installer). The Store's "
                  "own vetting never saw it."),
    "enterprise": (SOURCE_ENTERPRISE,
                   "Signed with an organisation's own certificate "
                   "(line-of-business app)."),
    "none": (SOURCE_UNSIGNED, "Unsigned -- only installable in developer mode."),
}

NON_REMOVABLE_REASON = (
    "Windows marks this package NonRemovable: Settings will not uninstall it "
    "and Remove-AppxPackage refuses it (0x80073CFA).")


def package_source(signature_kind) -> Tuple[str, str]:
    """(label, explanation) for a SignatureKind value. Never guessed: an
    absent value is "", an unknown one is shown as Windows reported it."""
    key = str(signature_kind or "").strip().lower()
    if key in _SOURCES:
        return _SOURCES[key]
    if not key:
        return ("", "Windows did not report a signature kind for this package.")
    return (str(signature_kind), "Signature kind as reported by Windows.")

"""Researched Autodesk 2027 installer components and removal consequences."""
import re

# Ordered most specific first, including the alternate names in uninstall records.
ENTRIES = [
    # Product updates show as their own uninstall entries; removing one rolls the
    # product back to the previous release (Autodesk ships 2027.x as patches).
    (r"^(?:Autodesk )?AutoCAD 2027\.\d+ Update$", "Keep",
     "An AutoCAD 2027 product update (patch) installed on top of AutoCAD.",
     "Brings AutoCAD 2027 to this update's fixes and improvements.",
     "Removing it rolls AutoCAD back to the previous 2027 release and loses this update's fixes; "
     "it is removed together with AutoCAD anyway. Keep."),
    (r"^(?:Autodesk )?Inventor(?: Professional)? 2027\.\d+ Update$", "Keep",
     "An Inventor 2027 product update (patch) installed on top of Inventor.",
     "Brings Inventor 2027 to this update's fixes and improvements.",
     "Removing it rolls Inventor back to the previous 2027 release and loses this update's fixes; "
     "it is removed together with Inventor anyway. Keep."),
    (r"^(?:Autodesk )?AutoCAD 2027 - English$", "Optional",
     "AutoCAD, Autodesk's 2D/3D CAD program.", "Creates and edits 2D/3D CAD drawings.",
     "Removing it removes AutoCAD; drawings (.dwg) stay. Keep while used."),
    (r"^Autodesk Inventor Professional 2027 - English$", "Optional",
     "Inventor, Autodesk's 3D mechanical design program.", "Creates mechanical parts and assemblies.",
     "Removing it removes Inventor; your parts/assemblies stay."),
    (r"^ACAD Private$", "Keep",
     "An Installer package that supplies files AutoCAD needs to run.",
     "Autodesk forum guidance: remove only when no AutoCAD is installed.",
     "Keep while AutoCAD is installed; remove only by uninstalling AutoCAD itself."),
    (r"^Autodesk Inventor Private 2027 Language Pack - English$", "Keep",
     "The English resources Inventor runs with.", "Supplies Inventor's English resources.",
     "Keep while Inventor is installed."),
    (r"^Autodesk Inventor Private 2027$", "Keep",
     "Inventor's private shared components, the same kind of package as ACAD Private for Inventor.",
     "Supplies components Inventor needs to run.", "Keep while Inventor is installed."),
    (r"^Autodesk Inventor Content Center Libraries 2027(?: \(Desktop Content\))?$", "Keep",
     "Inventor's library of standard parts (bolts, nuts, profiles) used by Content Center.",
     "Provides standard parts for Content Center.",
     "Removing it breaks Place from Content Center and any assembly that references those parts must regenerate them. Keep while Inventor is used."),
    (r"^Autodesk Inventor Electrical Catalog Browser CA 2027$", "Optional",
     "The electrical component catalog browser used by Inventor's cable & harness/electrical workflows.",
     "Browses electrical content.", "Optional if you never use electrical content."),
    (r"^Autodesk Genuine Service$", "Keep",
     "Autodesk's licence-compliance service, installed with every Autodesk product.",
     "Checks Autodesk licence compliance.",
     "Autodesk's own guidance removes it only in a full clean uninstall; uninstalling it while Autodesk products are installed is refused or reinstalled. Keep while any Autodesk product is installed."),
    (r"^Autodesk Access$", "Optional",
     "Autodesk's installer/update manager (successor of the Autodesk Desktop App).",
     "It delivers product updates.", "Optional (updates then come from your Autodesk account)."),
    (r"^Autodesk (?:App Manager|Access / App Manager)$", "Optional",
     "Autodesk App Manager.", "Manages Autodesk App Store plug-ins.",
     "Optional if you install no App Store plug-ins."),
    (r"^Autodesk Featured Apps$", "Safe to remove",
     "An AutoCAD/Inventor add-in that promotes Autodesk App Store apps.",
     "Promotes Autodesk App Store apps.", "Safe to remove; nothing in your drawings depends on it."),
    (r"^Autodesk CER$", "Optional", "Customer Error Reporting.",
     "Sends crash reports to Autodesk.", "Optional; removing it only loses crash reporting."),
    (r"^Autodesk Interoperability Engine Manager$", "Keep",
     "The translators that import/export other CAD formats (used by Inventor/InfraWorks).",
     "Autodesk support treats a missing one as an error.", "Keep while Inventor is installed."),
    (r"^AutoCAD Open in Desktop$", "Optional",
     "A helper that opens drawings from Autodesk's web/cloud apps in desktop AutoCAD.",
     "Hands web/cloud drawings to desktop AutoCAD.", "Optional; removing it only stops that hand-off."),
    (r"^Autodesk AutoCAD MCP Server$", "Keep",
     "Installed by AutoCAD 2027 for the Autodesk Assistant's MCP (Model Context Protocol) integration.",
     "Lets AI assistants work with AutoCAD. Purpose not fully documented by Autodesk.",
     "Keep (part of AutoCAD 2027)."),
]


def verdict(name):
    return next((v for pattern, v, *_ in ENTRIES if re.search(pattern, name, re.I)), None)


def entry(name):
    """(verdict, what, does, what removing costs) for a researched Autodesk name, or None.

    For screens whose rows App Buster's scan does not list (product update
    patches are hidden there as children of their product)."""
    return next((tuple(rest) for pattern, *rest in ENTRIES if re.search(pattern, name or "", re.I)), None)

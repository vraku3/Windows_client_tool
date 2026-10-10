"""keep / optional / remove -- from a curated list, never from a name guess.

Qt-free. O&O AppBuster's rule, adopted as is: a recommendation is a
deliberate decision about a KNOWN package, matched on its package name
TOGETHER WITH its publisher id; everything not on the list is **keep**,
because "a wrong keep costs you some disk space; a wrong remove could break
something". System and framework apps are never recommended for removal --
they cannot be removed at all.

Microsoft's packages are matched on name + publisher id 8wekyb3d8bbwe, so a
sideloaded package that merely borrows a Microsoft name is not recommended
for anything. Third-party consumer apps ship under many publisher ids, so
those are matched on the vendor's own name prefix instead.
"""
from __future__ import annotations

from typing import Dict, Tuple

from . import model as m

MS = "8wekyb3d8bbwe"
WIN = "cw5n1h2txyewy"

#: (package name, publisher id) -> (label, why)
CURATED: Dict[Tuple[str, str], Tuple[str, str]] = {}


def _add(label: str, why: str, names: str, pid: str = MS) -> None:
    for name in names.split():
        CURATED[(name.lower(), pid)] = (label, why)


_add(m.REMOVE, "Ad-supported Bing content app; nothing depends on it.",
     "Microsoft.BingWeather Microsoft.BingNews Microsoft.BingFinance Microsoft.BingSports "
     "Microsoft.BingTravel Microsoft.BingFoodAndDrink Microsoft.BingHealthAndFitness "
     "Microsoft.BingTranslator")
_add(m.REMOVE, "Preinstalled extra with no dependencies.",
     "Microsoft.3DBuilder Microsoft.Microsoft3DViewer Microsoft.MSPaint Microsoft.MixedReality.Portal "
     "Microsoft.Getstarted Microsoft.Office.Sway Microsoft.MicrosoftOfficeHub Microsoft.M365Companions "
     "Microsoft.SkypeApp Microsoft.Messaging Microsoft.549981C3F5F50 Microsoft.ZuneVideo "
     "Microsoft.Windows.Podcasts Microsoft.WindowsFeedbackHub Microsoft.NetworkSpeedTest "
     "Microsoft.OneConnect Microsoft.People Microsoft.Wallet Microsoft.Print3D")
_add(m.REMOVE, "Game shipped with Windows.", "Microsoft.MicrosoftSolitaireCollection")
_add(m.REMOVE, "Video editor preinstalled by Windows.", "Clipchamp.Clipchamp", "yxz26nhyzhsrt")
_add(m.OPTIONAL, "Safe to remove if you do not use it.",
     "Microsoft.BingSearch Microsoft.Todos Microsoft.PowerAutomateDesktop Microsoft.OutlookForWindows "
     "Microsoft.windowscommunicationsapps Microsoft.YourPhone Microsoft.ZuneMusic Microsoft.Windows.Photos "
     "Microsoft.WindowsCamera Microsoft.WindowsAlarms Microsoft.WindowsSoundRecorder "
     "Microsoft.MicrosoftStickyNotes Microsoft.MicrosoftJournal Microsoft.Whiteboard "
     "Microsoft.Office.OneNote Microsoft.Windows.DevHome Microsoft.PCManager Microsoft.RemoteDesktop "
     "Microsoft.GetHelp Microsoft.GamingApp Microsoft.XboxApp Microsoft.XboxGameOverlay "
     "Microsoft.XboxGamingOverlay Microsoft.XboxSpeechToTextOverlay Microsoft.XboxConsoleCompanion "
     "Microsoft.Windows.Copilot Microsoft.Copilot Microsoft.WindowsNotepad Microsoft.Paint "
     "Microsoft.ScreenSketch Microsoft.WindowsCalculator Microsoft.WindowsTerminal Microsoft.Windows.DeskApp "
     "Microsoft.Windows.StudioDesign Microsoft.Windows.Paint.Cocreator Microsoft.Photos.Import "
     "MSTeams MicrosoftTeams")
_add(m.OPTIONAL, "Safe to remove if you do not use it.",
     "MicrosoftCorporationII.QuickAssist MicrosoftCorporationII.MicrosoftFamily", MS)
_add(m.OPTIONAL, "Widgets board; safe to remove if you do not use Widgets.",
     "MicrosoftWindows.Client.WebExperience Microsoft.StartExperiencesApp", WIN)
_add(m.OPTIONAL, "Phone and PC link component.", "MicrosoftWindows.CrossDevice", WIN)
# Deliberately KEEP even though removable: other apps or Windows rely on them.
_add(m.KEEP, "The Store installs and updates other apps.",
     "Microsoft.WindowsStore Microsoft.StorePurchaseApp Microsoft.DesktopAppInstaller")
_add(m.KEEP, "Xbox sign-in for games depends on it.",
     "Microsoft.XboxIdentityProvider Microsoft.Xbox.TCUI")
_add(m.KEEP, "Codec other apps use to open media.",
     "Microsoft.HEIFImageExtension Microsoft.HEVCVideoExtension Microsoft.VP9VideoExtensions "
     "Microsoft.WebMediaExtensions Microsoft.WebpImageExtension Microsoft.RawImageExtension "
     "Microsoft.AV1VideoExtension Microsoft.MPEG2VideoExtension Microsoft.AVCEncoderVideoExtension")
_add(m.KEEP, "Windows Security's own interface.", "Microsoft.SecHealthUI")

#: Vendor name prefixes for third-party consumer apps and OEM add-ons (these
#: publish under many ids). -> (label, why)
VENDOR_PREFIXES: Dict[str, Tuple[str, str]] = {
    "king.com.": (m.REMOVE, "Preinstalled casual game."),
    "ad2f1837.": (m.OPTIONAL, "HP add-on; some duplicate Windows functionality."),
    "dellinc.": (m.OPTIONAL, "Dell add-on; some duplicate Windows functionality."),
    "e046963f.": (m.OPTIONAL, "Lenovo add-on; some duplicate Windows functionality."),
    "amazon.com.amazon": (m.REMOVE, "Preinstalled shopping app."),
    "spotifyab.": (m.OPTIONAL, "Music app; often preinstalled."),
    "disney.": (m.REMOVE, "Preinstalled streaming promotion."),
    "facebook.": (m.REMOVE, "Preinstalled social app."),
    "bytedancepte.": (m.REMOVE, "Preinstalled social app (TikTok)."),
    "4df9e0f8.netflix": (m.REMOVE, "Preinstalled streaming promotion."),
    "amazonvideo.primevideo": (m.REMOVE, "Preinstalled streaming promotion."),
    "adobesystemsincorporated.adobephotoshopexpress": (m.REMOVE, "Preinstalled trial."),
    "a278ab0d.": (m.REMOVE, "Preinstalled game (Gameloft)."),
}


def recommend(rec: m.AppRecord) -> Tuple[str, str]:
    """(label, why) for one row."""
    if rec.type in (m.SYSTEM, m.FRAMEWORK):
        return m.KEEP, "Part of Windows or a runtime other apps need; it cannot be removed."
    if rec.type in (m.ORPHANED, m.DEFECT):
        return m.OPTIONAL, "Leftover or broken entry; review it before cleaning up."
    if rec.type != m.WINDOWS:
        return m.KEEP, "Not on the curated list."
    hit = CURATED.get((rec.package_name.lower(), rec.publisher_id.lower()))
    if hit:
        return hit
    low = rec.package_name.lower()
    for prefix, verdict in VENDOR_PREFIXES.items():
        if low.startswith(prefix):
            return verdict
    return m.KEEP, "Not on the curated list."


def apply(rows) -> None:
    for rec in rows:
        rec.recommendation, rec.extra["why"] = recommend(rec)

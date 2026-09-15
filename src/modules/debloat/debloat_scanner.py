"""debloat_scanner — detects installed bloatware apps and tweak states."""
import logging
from typing import Dict, Optional

from core.appx_service import installed_names, installed_names_or_none

logger = logging.getLogger(__name__)

KNOWN_PACKAGES = {
    # Bing Apps
    "Microsoft.BingWeather", "Microsoft.BingNews", "Microsoft.BingFinance",
    "Microsoft.BingSports", "Microsoft.BingTravel", "Microsoft.BingFoodAndDrink",
    "Microsoft.BingHealthAndFitness", "Microsoft.BingTranslator", "Microsoft.BingSearch",
    # System Utilities
    "Microsoft.WindowsCalculator", "Microsoft.WindowsCamera", "Microsoft.WindowsAlarms",
    "Microsoft.ScreenSketch", "Microsoft.WindowsNotepad", "Microsoft.Paint",
    "Microsoft.WindowsSoundRecorder", "MicrosoftCorporationII.QuickAssist",
    "Microsoft.MicrosoftStickyNotes", "Microsoft.WindowsTerminal",
    "Clipchamp.Clipchamp", "Microsoft.MicrosoftJournal",
    # Microsoft Communications
    "Microsoft.windowscommunicationsapps", "Microsoft.Messaging",
    "Microsoft.SkypeApp", "Microsoft.YourPhone", "Microsoft.549981C3F5F50",
    # Microsoft Office
    "Microsoft.MicrosoftOfficeHub", "Microsoft.Office.OneNote",
    "Microsoft.OutlookForWindows", "Microsoft.M365Companions",
    "Microsoft.PowerAutomateDesktop", "Microsoft.Todos", "Microsoft.Office.Sway",
    # Microsoft Media
    "Microsoft.ZuneVideo", "Microsoft.ZuneMusic", "Microsoft.Windows.Photos",
    "Microsoft.3DBuilder", "Microsoft.Microsoft3DViewer", "Microsoft.MSPaint",
    # Microsoft Gaming
    "Microsoft.XboxApp", "Microsoft.XboxGameOverlay", "Microsoft.GamingApp",
    "Microsoft.XboxIdentityProvider", "Microsoft.XboxSpeechToTextOverlay",
    "Microsoft.Xbox.TCUI", "Microsoft.XboxConsoleCompanion",
    # Microsoft Miscellaneous
    "Microsoft.WindowsStore", "MSTeams", "MicrosoftTeams",
    "Microsoft.MicrosoftSolitaireCollection", "Microsoft.Whiteboard",
    "Microsoft.Windows.DevHome", "MicrosoftCorporationII.MicrosoftFamily",
    "Microsoft.WindowsFeedbackHub", "Microsoft.GetHelp", "Microsoft.Getstarted",
    "Microsoft.PCManager", "Microsoft.MixedReality.Portal", "Microsoft.RemoteDesktop",
    "Microsoft.StartExperiencesApp", "Microsoft.NetworkSpeedTest",
    "LinkedInforWindows",
    # Third-Party Consumer
    "Amazon.com.Amazon", "Facebook", "Instagram", "Spotify", "Netflix",
    "Disney", "TikTok", "king.com.CandyCrushSaga", "king.com.CandyCrushSodaSaga",
    "king.com.BubbleWitch3Saga", "AdobeSystemsIncorporated.AdobePhotoshopExpress",
    "AutodeskSketchBook", "Duolingo-LearnLanguagesforFree", "PandoraMediaInc",
    "Plex", "TuneInRadio", "ACGMediaPlayer", "COOKINGFEVER",
    "DisneyMagicKingdoms", "FarmVille2CountryEscape", "Fitbit", "Flipboard",
    "HiddenCity", "HULULLC.HULUPLUS", "iHeartRadio", "MarchofEmpires",
    "NYTCrossword", "OneCalendar", "PhototasticCollage", "PicsArt-PhotoStudio",
    "PolarrPhotoEditorAcademicEdition", "PrimeVideo", "Royal Revolt", "Shazam",
    "SlingTV", "Twitter", "Viber", "WinZipUniversal", "Wunderlist",
    "Sidia.LiveWallpaper", "EclipseManager", "XING", "ActiproSoftwareLLC",
    "CyberLinkMediaSuiteEssentials", "Microsoft.OneConnect", "Asphalt8Airborne",
    # HP OEM
    "AD2F1837.HPAIExperienceCenter", "AD2F1837.HPConnectedMusic",
    "AD2F1837.HPConnectedPhotopoweredbySnapfish", "AD2F1837.HPDesktopSupportUtilities",
    "AD2F1837.HPEasyClean", "AD2F1837.HPFileViewer", "AD2F1837.HPJumpStarts",
    "AD2F1837.HPPCHardwareDiagnosticsWindows", "AD2F1837.HPPowerManager",
    "AD2F1837.HPPrinterControl", "AD2F1837.HPPrivacySettings", "AD2F1837.HPQuickDrop",
    "AD2F1837.HPQuickTouch", "AD2F1837.HPRegistration", "AD2F1837.HPSupportAssistant",
    "AD2F1837.HPSureShieldAI", "AD2F1837.HPSystemInformation", "AD2F1837.HPWelcome",
    "AD2F1837.HPWorkWell", "AD2F1837.myHP",
    # Dell OEM
    "DellInc.DellDigitalDelivery", "DellInc.DellMobileConnect",
    "DellInc.DellSupportAssistforPCs",
    # Lenovo OEM
    "E046963F.LenovoCompanion", "LenovoCompanyLimited.LenovoVantageService",
    # Windows 11 24H2/25H2 AI & shell features (added 2026-09; these are
    # exactly the packages a debloat catalog exists to remove, and were
    # silently undetectable — debloat.json listed them, this set did not)
    "Microsoft.AI.Landscape", "Microsoft.Windows.Copilot",
    "Microsoft.Windows.Recall", "Microsoft.Windows.Wcr",
    "Microsoft.SecHealthUI", "Microsoft.Windows.WebExperience",
    "Microsoft.Windows.DeskApp", "Microsoft.Photos.Import",
    "Microsoft.ScreenCapture", "Microsoft.Windows.Paint.Cocreator",
    "Microsoft.Windows.Podcasts", "Microsoft.Windows.StudioDesign",
}


def get_installed_packages() -> Dict[str, str]:
    """Return {package_name: display_name} for known installed Appx packages.

    Uses the shared `core.appx_service` enumeration (one query, cached, with
    the unelevated fallback) rather than its own PowerShell.
    """
    installed: Dict[str, str] = {}
    for name in installed_names():
        if name in KNOWN_PACKAGES:
            installed[name] = name
    return installed


def get_installed_packages_or_none() -> Optional[Dict[str, str]]:
    """Same as `get_installed_packages()`, but returns `None` when the
    underlying AppX enumeration could not be completed at all, instead of
    silently reporting that as "0 bloatware apps installed" -- a query that
    is refused, times out, or is killed after being wedged is a failed
    scan, not a clean one."""
    names = installed_names_or_none()
    if names is None:
        return None
    return {name: name for name in names if name in KNOWN_PACKAGES}


PROTECTED_APPS = {
    "Microsoft.WindowsStore",
    "Microsoft.WindowsTerminal",
    "Microsoft.GetHelp",
    "Microsoft.WindowsAlarms",
    "Microsoft.WindowsCalculator",
    "Microsoft.WindowsNotepad",
    "Microsoft.ScreenSketch",
}

PROTECTED_REASONS = {
    "Microsoft.WindowsStore": "Required for installing apps from Microsoft Store",
    "Microsoft.WindowsTerminal": "Recommended terminal — removal not advised",
    "Microsoft.GetHelp": "Built-in Windows help system",
    "Microsoft.WindowsAlarms": "System clock and alarms",
    "Microsoft.WindowsCalculator": "System calculator",
    "Microsoft.WindowsNotepad": "System text editor",
    "Microsoft.ScreenSketch": "Windows' screenshot/annotation tool — "
        "several other apps (Teams, OneNote) shell out to it",
}

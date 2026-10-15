"""What each known app IS, what it DOES, and what removing it costs. Qt-free data.

Three tables, consulted in order by `app_info.about()`:

* PACKAGES -- Windows (MSIX/AppX) packages by exact package name, lowercase.
* PACKAGE_PREFIXES -- families that ship many versions/architectures under
  one name stem (VCLibs, Windows App Runtime, Feature Experience Packs, ...).
* DESKTOP -- desktop (Win32/MSI) programs by a regex on their display name.

Every entry is `_e(what, does, remove, name="")`. For a System app or a
Framework, `remove` says why it cannot be removed and what would break; for
everything else it says what removing it actually costs. `name` replaces an
unreadable display name (a bare GUID, a package id) and is "" otherwise.

Facts only. Where Microsoft does not document what a component does, the entry
says so instead of guessing -- an invented purpose is worse than none.
"""
from __future__ import annotations

import re
from typing import Dict, List, NamedTuple, Pattern, Tuple


class About(NamedTuple):
    what: str
    does: str
    remove: str
    name: str = ""


def _e(what: str, does: str, remove: str, name: str = "") -> About:
    return About(what, does, remove, name)


_SHELL = ("It is part of Windows itself: the package is marked NonRemovable and lives under "
          "%SystemRoot%\\SystemApps, so the deployment service refuses to remove it. Forcing it "
          "out breaks the feature it backs, and the next feature update puts it back anyway.")
_FRAMEWORK = ("Frameworks are never removed by hand: Windows keeps one as long as any installed "
              "app declares it as a dependency, and removes it by itself once the last such app "
              "is gone. Removing it underneath those apps would stop them from starting.")
_CODEC = ("Removing it does not break Windows, but every app that relies on the system codec "
          "(Photos, Media Player, Explorer thumbnails, the browser's media pipeline, games that "
          "play cut-scenes) stops opening that format. It can be reinstalled from the Store.")
_ADK = ("It is one component of the Windows Assessment and Deployment Kit (ADK), Microsoft's "
        "toolkit for building, customising and deploying Windows images. ")
_ADK_REMOVE = ("Removing one ADK component on its own leaves the kit half-installed; remove the "
               "Windows Assessment and Deployment Kit as a whole (its own entry, or Settings > "
               "Apps) if you no longer build or service Windows images. Nothing in everyday "
               "Windows uses it.")
_VENDOR_HAL = ("Removing it while the vendor's control app is still installed makes that app "
               "lose the device (RGB stops responding, the device disappears from the app); "
               "remove the control app first, or reinstall it to get this back.")


PACKAGES: Dict[str, About] = {
    # ---- System apps (part of Windows) -------------------------------------------------------
    "1527c705-839a-4832-9118-54d4bd6a0c89": _e(
        "The Windows file picker (Microsoft.Windows.FilePicker) -- its package name is a GUID, "
        "which is why it shows up unnamed.",
        "Draws the Open / Save dialog that Store and WinUI apps show when they ask you to pick a "
        "file or folder.",
        _SHELL, name="File Picker"),
    "c5e2524a-ea46-4f67-841f-6a9465d9d515": _e(
        "The packaged part of File Explorer (Microsoft.Windows.FileExplorer); its package name "
        "is a GUID.",
        "Gives File Explorer a package identity so modern features (the WinUI command bar, "
        "Gallery, Home, tabs) can run inside it.",
        _SHELL, name="File Explorer (package)"),
    "e2a4f912-2574-4a75-9bb0-0d023378592b": _e(
        "The \"How do you want to open this file?\" dialog (Microsoft.Windows.AppResolverUX); "
        "its package name is a GUID.",
        "Appears when you open a file type with no default app, or choose Open with > Choose "
        "another app, and records the default you pick.",
        _SHELL, name="Open With dialog"),
    "f46d4000-fd22-4db4-ac8e-4e1ddde828fe": _e(
        "The dialog that suggests folders to add to a library "
        "(Microsoft.Windows.AddSuggestedFoldersToLibraryDialog).",
        "Shown when Windows offers to add folders to Pictures, Music, Videos or Documents "
        "libraries, for example after connecting a drive.",
        _SHELL),
    "microsoft.aad.brokerplugin": _e(
        "The Web Account Manager plug-in for work or school accounts (Microsoft Entra ID, "
        "formerly Azure AD).",
        "Holds and refreshes sign-in tokens so Office, Teams, OneDrive, Edge and Windows itself "
        "can sign you in once (single sign-on) instead of asking for a password in every app. "
        "It also runs the work/school sign-in window.",
        _SHELL + " Without it, work-account sign-in fails and Microsoft 365 apps loop on the "
        "sign-in prompt."),
    "microsoft.accountscontrol": _e(
        "The \"Email and accounts\" host.",
        "Runs the dialog apps open when they ask you to add or choose an account (Settings > "
        "Accounts > Email & accounts uses it too).",
        _SHELL),
    "microsoft.asynctextservice": _e(
        "A background text service shipped with Windows.",
        "Other inbox apps call into it for text processing; Microsoft does not document what "
        "exactly it does, and it has no window of its own.",
        _SHELL),
    "microsoft.bioenrollment": _e(
        "Windows Hello setup.",
        "Walks you through enrolling a face, fingerprint or PIN for Windows Hello (Settings > "
        "Accounts > Sign-in options opens it).",
        _SHELL + " Without it, Windows Hello can no longer be set up or re-enrolled."),
    "microsoft.creddialoghost": _e(
        "The modern credential prompt.",
        "Draws the \"Windows Security\" window that asks for a PIN, password or smart card -- "
        "for a network share, Remote Desktop, a UAC prompt for another account, or an app "
        "using the Windows credential API.",
        _SHELL),
    "microsoft.desktopappinstaller": _e(
        "App Installer -- which is also where winget (Windows Package Manager) lives.",
        "Installs .msix/.appx files when you double-click them, and provides the winget "
        "command line that this app uses for its update checks and for App Buster's Update "
        "action.",
        "On this PC it is registered as part of Windows. Removing it would take winget with it: "
        "app updates here, the Updates module and every script that calls winget would stop "
        "working."),
    "microsoft.ecapp": _e(
        "Eye Control, an accessibility feature.",
        "Lets you control the mouse, an on-screen keyboard and text-to-speech with your eyes, "
        "using a supported eye tracker (Settings > Accessibility > Eye control).",
        _SHELL),
    "microsoft.lockapp": _e(
        "The lock screen.",
        "Draws the screen you see before signing in or after Win+L: the background picture "
        "(including Windows Spotlight), clock, notifications and status.",
        _SHELL),
    "microsoft.microsoftedgedevtoolsclient": _e(
        "Edge DevTools client for the old EdgeHTML engine.",
        "Lets developers attach the Edge developer tools to EdgeHTML web views on this PC or a "
        "remote device. Today's Chromium Edge does not need it; it is a leftover kept for "
        "compatibility.",
        _SHELL),
    "microsoft.sechealthui": _e(
        "The Windows Security app -- the window, not the protection.",
        "Shows and configures Microsoft Defender Antivirus, the firewall, SmartScreen, device "
        "security (Core isolation, TPM, Secure Boot), account protection and family options. "
        "The protection itself runs in services (WinDefend, SecurityHealthService) whether or "
        "not this window exists.",
        "It is part of Windows. Removing it would leave protection running with no way to see "
        "its state, approve a blocked file or change a setting."),
    "microsoft.win32webviewhost": _e(
        "A host for the legacy EdgeHTML web view used by desktop apps.",
        "Lets older desktop programs embed the EdgeHTML-based WebView control. Modern apps use "
        "WebView2 (Chromium) instead.",
        _SHELL),
    "microsoft.windows.apprep.chxapp": _e(
        "Microsoft Defender SmartScreen's warning window.",
        "Shows \"Windows protected your PC\" when you start a downloaded program that is not "
        "yet known to Microsoft's reputation service, and lets you run it anyway.",
        _SHELL + " SmartScreen checks keep running; without this window you would lose the "
        "\"Run anyway\" choice."),
    "microsoft.windows.assignedaccesslockapp": _e(
        "The Assigned Access (kiosk mode) lock app.",
        "Takes over the lock screen when a kiosk account signs in, so a single-app kiosk "
        "cannot be escaped. Unused unless kiosk mode is configured.",
        _SHELL),
    "microsoft.windows.capturepicker": _e(
        "The screen-capture picker.",
        "Lists windows and screens to choose from when an app asks to capture one through the "
        "Windows.Graphics.Capture API (screen sharing in some meeting and streaming apps).",
        _SHELL),
    "microsoft.windows.cloudexperiencehost": _e(
        "Cloud Experience Host (\"Your account\").",
        "Runs the setup screens: the first-run setup (OOBE), \"Let's finish setting up your "
        "device\", Microsoft account sign-in and PIN setup, and joining a work account.",
        _SHELL + " Without it, account sign-in and the setup screens fail."),
    "microsoft.windows.contentdeliverymanager": _e(
        "Microsoft Content (Content Delivery Manager).",
        "Downloads Windows Spotlight lock-screen pictures, tips, Start and Settings "
        "suggestions -- and is what silently installs \"suggested\" apps on a fresh profile.",
        _SHELL + " You do not need to remove it to stop it: the Privacy and Taskbar & Start "
        "tweaks in this app turn off its suggestions and silent installs (they write its "
        "ContentDeliveryManager settings)."),
    "microsoft.windows.narratorquickstart": _e(
        "Narrator's welcome screen (Narrator Home).",
        "Opens when Narrator, the built-in screen reader, starts: a short tutorial and quick "
        "links to its settings.",
        _SHELL),
    "microsoft.windows.oobenetworkcaptiveportal": _e(
        "The captive-portal sign-in used during Windows setup.",
        "Shows the hotel or guest Wi-Fi sign-in page when Windows setup connects to a network "
        "that requires one.",
        _SHELL),
    "microsoft.windows.oobenetworkconnectionflow": _e(
        "The \"Let's connect you to a network\" step of Windows setup.",
        "Lists networks and connects to one during the first-run setup (OOBE).",
        _SHELL),
    "microsoft.windows.parentalcontrols": _e(
        "Microsoft Family Safety enforcement.",
        "Enforces screen-time limits, app and content filters and activity reports for child "
        "accounts managed through Microsoft Family. Idle when no child account uses this PC.",
        _SHELL),
    "microsoft.windows.peopleexperiencehost": _e(
        "A shell host for contact-related features.",
        "Originally drove the taskbar People bar (removed in Windows 11); it remains as a shell "
        "component that other parts of Windows call.",
        _SHELL),
    "microsoft.windows.pinningconfirmationdialog": _e(
        "The pin-confirmation dialog.",
        "Asks \"Do you want to pin this app?\" when a program requests a place on the taskbar "
        "or Start, so no app can pin itself without your consent.",
        _SHELL),
    "microsoft.windows.printqueueactioncenter": _e(
        "The modern print queue window.",
        "Shows the jobs waiting for a printer and lets you pause or cancel them (Settings > "
        "Printers > Open print queue).",
        _SHELL),
    "microsoft.windows.secureassessmentbrowser": _e(
        "Take a Test, the locked-down exam browser.",
        "Opens an exam page full screen and blocks other apps, the clipboard and notifications "
        "until the test ends. Used by schools; idle otherwise.",
        _SHELL),
    "microsoft.windows.shellexperiencehost": _e(
        "Windows Shell Experience Host.",
        "Draws the notification center, the calendar flyout, the network/volume/battery "
        "flyouts, and other pop-ups attached to the taskbar.",
        _SHELL + " Without it, notifications and taskbar flyouts stop working."),
    "microsoft.windows.startmenuexperiencehost": _e(
        "The Start menu.",
        "Draws Start: pinned apps, All apps, recommendations and the search entry point.",
        _SHELL + " Without it, Start does not open."),
    "microsoft.windows.xgpuejectdialog": _e(
        "The external-GPU eject dialog.",
        "Lists apps still using an external graphics card (a Thunderbolt eGPU) before you "
        "unplug it, so they can be closed safely.",
        _SHELL),
    "microsoft.xboxgamecallableui": _e(
        "Xbox in-game UI.",
        "Shows the Xbox sign-in, friends, invitations and achievement pop-ups that PC games "
        "built on Xbox services call up.",
        _SHELL),
    "microsoftwindows.client.coreai": _e(
        "Click to Do, the on-device AI action layer.",
        "On Copilot+ PCs, analyses what is on screen (text, images) with local AI models and "
        "offers actions such as summarise, copy text or search. On other PCs it is installed "
        "but idle.",
        _SHELL + " Its features can be turned off in the AI Features tweaks instead."),
    "microsoftwindows.undockeddevkit": _e(
        "The UDK package (\"undocked\" shell components).",
        "Carries parts of the Windows 11 shell -- taskbar and Start pieces -- that Microsoft "
        "updates separately from the rest of Windows.",
        _SHELL),
    "windows.cbspreview": _e(
        "Windows Barcode Preview.",
        "Shows the camera preview when an app scans a barcode or QR code through the Windows "
        "barcode-scanner API.",
        _SHELL),
    "windows.immersivecontrolpanel": _e(
        "The Settings app.",
        "Everything under Start > Settings: display, sound, Bluetooth, network, accounts, "
        "Windows Update, privacy and the rest.",
        _SHELL + " Without it, most of Windows can no longer be configured."),
    "windows.printdialog": _e(
        "The modern print dialog.",
        "Shows printer choice, preview and options when a Store or WinUI app prints.",
        _SHELL),
    # ---- Frameworks ---------------------------------------------------------------------------
    "microsoft.directxruntime": _e(
        "The DirectX runtime for Store games.",
        "Provides the older DirectX helper libraries (D3DX9/10/11, XAudio2 legacy and friends) "
        "that games packaged for the Store still link against. Modern DirectX 12 is part of "
        "Windows and does not come from here.",
        _FRAMEWORK),
    "microsoft.ui.xaml.cbs": _e(
        "WinUI 2 (Microsoft.UI.Xaml), the copy shipped with Windows itself.",
        "The control library inbox apps such as Settings, Notepad and Paint draw their "
        "buttons, menus and navigation with.",
        _FRAMEWORK),
    "microsoft.windows.augloop.cbs": _e(
        "Augmentation Loop runtime, shipped with Windows.",
        "The plumbing Microsoft uses to connect inbox apps to cloud-assisted features "
        "(writing suggestions, Copilot-style actions). It has no window of its own.",
        _FRAMEWORK),
    # ---- Windows apps: Microsoft ------------------------------------------------------------
    "microsoft.6365217ce6eb4": _e(
        "Microsoft Defender -- the consumer app that comes with a Microsoft 365 Personal or "
        "Family subscription. It is NOT the antivirus built into Windows.",
        "Shows the security status of every device in your family (Windows, Mac, phones), "
        "adds identity-theft monitoring in some regions and runs its own scans on phones. On "
        "Windows it reads Defender Antivirus' status; the protection is Windows' own.",
        "Safe to remove if you do not use the cross-device dashboard. Windows Security and "
        "Defender Antivirus keep protecting this PC exactly as before."),
    "microsoft.applicationcompatibilityenhancements": _e(
        "Windows Application Compatibility Enhancements.",
        "Delivers application-compatibility fixes (and, on Arm PCs, emulation improvements) "
        "through the Store, so they arrive without waiting for a Windows update.",
        "Removing it does not break Windows, but programs that depend on a fix it carries may "
        "misbehave again. Windows reinstalls it with the next servicing update."),
    "microsoft.av1videoextension": _e("AV1 Video Extension (codec).",
                                      "Lets Windows decode AV1 video -- the format YouTube, "
                                      "Netflix and modern cameras increasingly use -- with "
                                      "hardware acceleration where the GPU supports it.", _CODEC),
    "microsoft.avcencodervideoextension": _e("AVC (H.264) Encoder Video Extension.",
                                             "Lets apps ENCODE H.264 video through Windows' media "
                                             "stack: the Camera app, Clipchamp, Game Bar recording.",
                                             _CODEC),
    "microsoft.heifimageextension": _e("HEIF Image Extension (codec).",
                                       "Opens .heic/.heif photos -- the format iPhones and many "
                                       "Android phones save -- in Photos, Explorer thumbnails "
                                       "and any app using Windows' imaging stack.", _CODEC),
    "microsoft.hevcvideoextension": _e("HEVC (H.265) Video Extensions from the device manufacturer.",
                                       "Plays and records H.265 video (phone videos, 4K/HDR "
                                       "content). This variant is the free one an OEM licensed "
                                       "for this PC.", _CODEC),
    "microsoft.mpeg2videoextension": _e("MPEG-2 Video Extension (codec).",
                                        "Plays MPEG-2 video: DVD rips, TV recordings, some camera "
                                        "and broadcast files.", _CODEC),
    "microsoft.rawimageextension": _e("Raw Image Extension (codec).",
                                      "Opens camera RAW files (.CR3, .NEF, .ARW, .DNG...) in "
                                      "Photos and shows their thumbnails in Explorer.", _CODEC),
    "microsoft.vp9videoextensions": _e("VP9 Video Extensions (codec).",
                                       "Decodes VP9, the codec most of YouTube streams in, for "
                                       "apps that use Windows' media pipeline.", _CODEC),
    "microsoft.webmediaextensions": _e("Web Media Extensions.",
                                       "Adds Ogg, Vorbis and Theora support to Windows' media "
                                       "stack, for web-sourced audio and video files.", _CODEC),
    "microsoft.webpimageextension": _e("WebP Image Extension (codec).",
                                       "Opens .webp images -- the format most websites now "
                                       "serve -- in Photos and Explorer.", _CODEC),
    "microsoft.bingnews": _e("Microsoft News.",
                             "A news reader fed by Microsoft Start, with ads.",
                             "Safe to remove; nothing depends on it."),
    "microsoft.bingsearch": _e("Bing Search.",
                               "The package that lets Windows Search show web results from "
                               "Bing.", "Safe to remove if you do not want web results in "
                               "Start search; local search keeps working."),
    "microsoft.bingweather": _e("Microsoft Weather.", "Forecasts, radar and weather news.",
                                "Safe to remove; the Widgets board has its own weather card."),
    "microsoft.microsoftedge.stable": _e(
        "Microsoft Edge's package registration.",
        "Edge itself is a desktop program (listed separately); this package gives it an identity "
        "Windows uses for notifications, sharing and app defaults.",
        "Edge is part of Windows and is kept current by its own updater. Removing this package "
        "breaks Edge's integration (notifications, share targets) without removing the browser. "
        "WebView2, which many apps use, is a separate runtime and is not affected."),
    "microsoft.microsoftofficehub": _e(
        "The Microsoft 365 Copilot app (formerly \"Office\" / \"Microsoft 365\").",
        "A launcher and file list for Microsoft 365 web and desktop apps, with Copilot chat.",
        "Safe to remove. Installed Word, Excel, Outlook and the rest keep working; this is only "
        "the hub."),
    "microsoft.microsoftsolitairecollection": _e(
        "Microsoft Solitaire Collection.", "Klondike, Spider, FreeCell and others, with ads "
        "unless you subscribe.", "Safe to remove; reinstallable from the Store."),
    "microsoft.microsoftstickynotes": _e(
        "Sticky Notes.", "Desktop notes that sync with your Microsoft account (and Outlook / "
        "OneNote) when you sign in.",
        "Safe to remove. Notes synced to your account stay in the cloud; notes never synced are "
        "lost with the app's data."),
    "microsoft.office.actionsserver": _e(
        "A background package installed with Microsoft 365 apps.",
        "Microsoft does not document it; it ships with Office and is used by Office's own "
        "integration with Windows.",
        "Leave it while Microsoft 365 is installed -- the Office installer owns it and puts it "
        "back on its next update."),
    "microsoft.office.onenotevirtualprinter": _e(
        "The \"OneNote (Desktop)\" virtual printer.",
        "Adds a printer that sends whatever you print into a OneNote page.",
        "Safe to remove if you never print to OneNote; the Office installer may re-add it."),
    "microsoft.officepushnotificationutility": _e(
        "Office push-notification helper.", "Delivers notifications from Microsoft 365 "
        "services (comments, shares, reminders) to the Windows notification center.",
        "Removable, but Microsoft 365 reinstalls it on its next update."),
    "microsoft.onedrivesync": _e(
        "OneDrive's package identity.", "Gives the OneDrive desktop client a package identity "
        "so it can integrate with File Explorer (status icons, Files On-Demand, the sync "
        "provider in the navigation pane).",
        "Leave it while OneDrive is installed: removing it breaks Explorer integration. "
        "Uninstall OneDrive itself (desktop app) instead if you do not use it -- and note that "
        "Desktop, Documents and Pictures are redirected to OneDrive on this PC."),
    "microsoft.outlookforwindows": _e("The new Outlook for Windows.",
                                      "Mail and calendar app that replaced Mail and Calendar.",
                                      "Safe to remove if you use classic Outlook (Microsoft 365) "
                                      "or another mail app. Mail stays on the server."),
    "microsoft.paint": _e("Paint.", "Simple drawing and image editing, now with layers and AI "
                          "features (Cocreator, background removal) on some PCs.",
                          "Safe to remove; reinstallable from the Store."),
    "microsoft.powerautomatedesktop": _e(
        "Power Automate for desktop.", "Microsoft's robotic-process-automation tool: records "
        "and replays clicks and keystrokes as flows, with connectors to Microsoft 365.",
        "Safe to remove if you have no flows."),
    "microsoft.screensketch": _e(
        "Snipping Tool.", "Screenshots (also bound to the Print Screen key), screen "
        "recording, and text extraction from images.",
        "Removable, but Print Screen and Win+Shift+S stop opening anything. Most people keep "
        "it."),
    "microsoft.storepurchaseapp": _e(
        "Store Experience Host.", "Handles purchases, sign-in and licence checks for the "
        "Microsoft Store and for in-app purchases inside Store apps.",
        "Keep it: Store purchases and paid apps' licence checks need it."),
    "microsoft.todos": _e("Microsoft To Do.", "Task lists synced with your Microsoft account "
                          "and Outlook tasks.", "Safe to remove; tasks stay in your account."),
    "microsoft.visualstudiocode": _e(
        "Visual Studio Code's package identity.", "A small \"sparse\" package VS Code registers "
        "so it can appear in Windows 11's new right-click menu (Open with Code).",
        "Removing it only removes that menu entry; VS Code itself is the desktop app."),
    "microsoft.widgetsplatformruntime": _e(
        "Widgets Platform Runtime.", "Lets apps provide widgets to the Widgets board.",
        "Removable together with the Widgets board (Windows Web Experience Pack)."),
    "microsoft.windows.devhome": _e("Dev Home.", "A developer dashboard: GitHub widgets, "
                                    "machine configuration, Dev Drive setup. Microsoft retired "
                                    "it in 2025.", "Safe to remove."),
    "microsoft.windows.photos": _e(
        "Photos.", "The default image viewer and gallery, with basic editing and OneDrive / "
        "iCloud photo browsing.",
        "Removable, but images then open in whatever is set as the next default (or nothing). "
        "Keep it unless another viewer is set up."),
    "microsoft.windowsalarms": _e("Clock (Alarms & Clock).", "Alarms, timers, a stopwatch, "
                                  "world clock and Focus sessions.", "Safe to remove if unused."),
    "microsoft.windowscamera": _e("Camera.", "Takes photos and videos with the webcam; also "
                                  "scans QR codes and documents.", "Safe to remove; other apps "
                                  "use the webcam directly and keep working."),
    "microsoft.windowsnotepad": _e("Notepad.", "The plain-text editor, now with tabs, spell "
                                   "check and autosave.", "Removable, but .txt files lose their "
                                   "default app. Most people keep it."),
    "microsoft.windowsstore": _e(
        "Microsoft Store.", "Installs and UPDATES Store apps -- including the codecs, App "
        "Installer (winget) and many inbox apps listed here.",
        "Keep it. Without the Store, Store apps stop receiving updates and there is no "
        "supported way to put most of them back."),
    "microsoft.windowsterminal": _e(
        "Windows Terminal.", "The tabbed console host for PowerShell, Command Prompt, WSL and "
        "SSH; set as the default terminal on Windows 11.",
        "Removable; console programs then open in the old console host (conhost)."),
    "microsoft.winget.source": _e(
        "winget's package index.", "The local copy of the winget community repository that "
        "`winget search`, `winget upgrade` and this app's update checks query.",
        "Keep it: winget refreshes it automatically and removing it only forces a re-download "
        "the next time winget runs."),
    "microsoft.xboxidentityprovider": _e(
        "Xbox Identity Provider.", "Signs PC games into Xbox services (achievements, cloud "
        "saves, multiplayer).", "Keep it if you play PC Game Pass or Xbox-enabled games; they "
        "cannot sign in without it."),
    "microsoft.yourphone": _e("Phone Link.", "Mirrors an Android phone or iPhone: messages, "
                              "calls, notifications, photos.", "Safe to remove if you do not "
                              "link a phone."),
    "microsoft.zunemusic": _e("Media Player (formerly Groove Music).",
                              "Plays local music and video files.",
                              "Safe to remove if you use another player (VLC is installed "
                              "here)."),
    "microsoftcorporationii.quickassist": _e(
        "Quick Assist.", "Remote help: someone you trust views or controls your screen with a "
        "security code.",
        "Safe to remove if you never give or receive remote help. Note that scammers commonly "
        "ask victims to open it -- removing it is a reasonable hardening step on a relative's PC."),
    "microsoftwindows.client.webexperience": _e(
        "Windows Web Experience Pack -- the Widgets board.",
        "Draws the Widgets panel (Win+W and the weather on the taskbar): news, weather, "
        "sports, stocks.",
        "Safe to remove if you do not use Widgets; the taskbar weather disappears with it."),
    "microsoftwindows.crossdevice": _e(
        "Mobile devices (Cross Device Experience Host).",
        "Connects a phone to Windows: use it as a webcam, see it in Start's side panel, and "
        "continue browsing or apps across devices.",
        "Safe to remove if you do not connect a phone."),
    "microsoftwindows.voicesignaturegeneratorpack": _e(
        "Voice Signature Generator Pack.",
        "A voice component Windows installs with speech features; Microsoft does not document "
        "its exact role.",
        "Leave it: Windows reinstalls speech components it decides it needs."),
    "mstteams": _e("Microsoft Teams (the new Teams).", "Chat, meetings and calls for work, "
                   "school and personal accounts.", "Safe to remove if you do not use Teams; "
                   "Microsoft 365 may reinstall it."),
    "msteams": _e("Microsoft Teams (the new Teams).", "Chat, meetings and calls for work, "
                  "school and personal accounts.", "Safe to remove if you do not use Teams; "
                  "Microsoft 365 may reinstall it -- the Outlook Teams Meeting add-in stops "
                  "working without it."),
    "aimgr": _e(
        "Local AI Manager for Microsoft 365.",
        "Downloads and runs small on-device AI models that Microsoft 365 apps use for some "
        "Copilot features, so those features work without sending everything to the cloud.",
        "Removable; Microsoft 365 reinstalls it on its next update if your subscription "
        "includes those features."),
    "clipchamp.clipchamp": _e("Clipchamp.", "Microsoft's browser-based video editor, "
                              "preinstalled on Windows 11.", "Safe to remove."),
    # ---- Windows apps: third party ----------------------------------------------------------
    "5319275a.whatsappdesktop": _e(
        "WhatsApp (Store version).", "Messages and calls from your phone's WhatsApp account, "
        "linked to this PC as a companion device.",
        "Removing it signs this PC out of WhatsApp and deletes local chat history on this PC; "
        "chats stay on your phone. (App Buster removes the app; this app's Cleanup never "
        "touches WhatsApp's sign-in data.)"),
    "50497eliazammuto.moonlightuwp": _e(
        "Moonlight (UWP).", "Streams games from a PC running NVIDIA GameStream or Sunshine to "
        "this device.", "Safe to remove if you do not stream games."),
    "65d483df-b37e-4fcf-94de-8b795233db63": _e(
        "GIGABYTE Control Center's package component.",
        "A Store-packaged part of GIGABYTE Control Center (the motherboard's RGB, fan and "
        "update utility).",
        "Removing it alone breaks GIGABYTE Control Center; uninstall Control Center itself "
        "instead.", name="GIGABYTE Control Center (package)"),
    "bdcontextualmenu": _e(
        "Bitdefender's right-click menu entry.",
        "Adds \"Scan with Bitdefender\" to Windows 11's new context menu.",
        "Removing it only removes that menu entry; Bitdefender itself is the desktop app."),
    "notepadplusplus": _e(
        "Notepad++'s package identity.", "A sparse package so Notepad++ can appear in Windows "
        "11's new right-click menu (Edit with Notepad++).",
        "Removing it only removes that menu entry; Notepad++ itself is the desktop app."),
    "openai.codex": _e("ChatGPT desktop app (OpenAI).", "OpenAI's chat assistant, with a "
                       "Alt+Space launcher and Codex coding features.",
                       "Safe to remove; your chats stay in your OpenAI account."),
    "poncle.vampiresurvivors": _e("Vampire Survivors (Store/Game Pass version).",
                                  "Poncle's roguelite. The DLCs are separate packages.",
                                  "Removing it removes the game; Steam cloud or Xbox saves "
                                  "depend on the store you bought it from."),
}

#: Package-name prefixes (lowercase) for families shipped under many names.
PACKAGE_PREFIXES: List[Tuple[str, About]] = [
    ("microsoft.vclibs.", _e(
        "Visual C++ runtime for Store apps (VCLibs).",
        "The C/C++ runtime libraries that apps written in C++ need. The \"UWPDesktop\" "
        "variants serve desktop programs packaged for the Store; x86 and x64 copies exist "
        "side by side because 32-bit and 64-bit apps need their own.",
        _FRAMEWORK)),
    ("microsoft.net.native.", _e(
        ".NET Native framework/runtime for Store apps.",
        "Store apps written in C# for UWP were compiled ahead of time with .NET Native; this "
        "package is the runtime they load. It is unrelated to the .NET you install for "
        "desktop programs.",
        _FRAMEWORK)),
    ("microsoft.ui.xaml.", _e(
        "WinUI 2 (Microsoft.UI.Xaml) control library.",
        "Buttons, navigation views, menus and other controls that Store apps draw their "
        "interface with. Several versions coexist because each app pins the one it was built "
        "against.",
        _FRAMEWORK)),
    ("microsoft.windowsappruntime.cbs", _e(
        "Windows App SDK runtime, the copy shipped with Windows.",
        "WinUI 3 and the Windows App SDK APIs that Windows' own newer apps (and parts of the "
        "shell) are built on.",
        _FRAMEWORK)),
    ("microsoft.windowsappruntime.", _e(
        "Windows App SDK runtime (WinUI 3).",
        "The framework apps built with the Windows App SDK load: WinUI 3 controls, windowing, "
        "notifications, resource loading. Each version is a separate package; x86 and x64 "
        "coexist.",
        _FRAMEWORK)),
    ("microsoft.winappruntime.ddlm.", _e(
        "Windows App SDK \"Dynamic Dependency Lifetime Manager\" (DDLM).",
        "A tiny helper that keeps one Windows App SDK version registered while an UNPACKAGED "
        "desktop program uses it -- otherwise Windows would consider the framework unused and "
        "remove it.",
        "Removable, but a desktop program built on that Windows App SDK version may then fail "
        "to start until the runtime is reinstalled. Leave it unless you are sure nothing uses "
        "that version.")),
    ("microsoftcorporationii.winappruntime.main.", _e(
        "Windows App SDK \"Main\" package.",
        "Provides the Windows App SDK's system-wide services -- push notifications, background "
        "tasks, app lifecycle -- to apps built on it.",
        "Removable, but apps built on that Windows App SDK version lose notifications and "
        "background work. Leave it; it costs a few MB.")),
    ("microsoftcorporationii.winappruntime.singleton", _e(
        "Windows App SDK \"Singleton\" package.",
        "Hosts the one-per-PC services the Windows App SDK shares between apps (the push "
        "notification long-running process).",
        "Removable, but apps built on the Windows App SDK lose push notifications. Leave it.")),
    ("microsoft.ink.handwriting.", _e(
        "Handwriting recognition data for one language.",
        "Turns pen input into text in the handwriting panel, Ink Workspace and pen-enabled "
        "apps. One package per language and architecture.",
        _FRAMEWORK)),
    ("microsoft.aifabric.", _e(
        "Windows AI runtime (AI Fabric), shipped with Windows.",
        "The on-device AI plumbing Windows features call into (local models for Click to Do, "
        "Recall, image and text actions on Copilot+ PCs). Idle on PCs without an NPU.",
        _FRAMEWORK)),
    ("microsoftwindows.speech.", _e(
        "Speech pack for one language.",
        "Speech recognition (and, for some languages, natural voices) used by voice typing "
        "(Win+H), Voice access and Narrator.",
        "Removable; voice typing and Voice access stop working in that language until Windows "
        "downloads it again (Settings > Time & language > Speech).")),
    ("microsoftwindows.client.", _e(
        "Windows Feature Experience Pack.",
        "Carries parts of the Windows 11 shell -- taskbar, Start, File Explorer, the setup "
        "screens, input -- as packages, so Microsoft can update those features through Windows "
        "Update without a full feature update.",
        _SHELL)),
    ("microsoftwindows.", _e(
        "Windows Feature Experience Pack component.",
        "A piece of Windows delivered as a package so Microsoft can update it separately. "
        "Microsoft does not document what each of these numbered components contains.",
        _SHELL)),
    ("poncle.", _e(
        "Vampire Survivors downloadable content (DLC).",
        "Each Vampire Survivors DLC (Legacy of the Moonspell, Tides of the Foscari, Emergency "
        "Meeting, Operation Guns...) is its own Store package that the game loads.",
        "Removing it removes that DLC from the game; reinstall from the Store/Xbox app.")),
    ("king.com.", _e("A King game (Candy Crush and similar).",
                     "Casual free-to-play game, usually installed silently by Windows' "
                     "suggested-apps feature.", "Safe to remove.")),
]

_GAME_BNET = ("Installed and patched through Battle.net. Uninstalling here runs Blizzard's own "
              "uninstaller; your characters and progress are on Blizzard's servers, local "
              "settings and addons are lost.")

#: (regex on the desktop app's display name, About) -- first match wins.
DESKTOP_RAW: List[Tuple[str, About]] = [
    # ---- Runtimes -------------------------------------------------------------------------
    (r"^Microsoft Visual C\+\+ .*(Redistributable|Runtime)", _e(
        "Microsoft Visual C++ runtime (\"redistributable\").",
        "The C/C++ runtime DLLs programs built with that Visual Studio version need "
        "(msvcp*.dll, vcruntime*.dll). Each year is separate (2010, 2013, 2015-2022), x86 and "
        "x64 are separate, and the 2013/2022 entries come as a pair (\"Minimum\" + "
        "\"Additional\") of the same install.",
        "Do not remove. There is no reliable way to know which programs need which version, "
        "and removing one makes some program fail with \"VCRUNTIME140.dll was not found\" or "
        "similar. They are small. The 2015-2022 runtime is one shared version: keep the "
        "newest.")),
    (r"^Microsoft Edge WebView2 Runtime", _e(
        "Microsoft Edge WebView2 Runtime.",
        "The Chromium engine that desktop apps embed to show web content: Teams, Outlook, "
        "widgets, many installers and vendor utilities draw their whole interface with it.",
        "Do not remove: it is part of Windows 11 and dozens of apps stop rendering without "
        "it. Windows reinstalls it anyway.")),
    (r"^Microsoft Edge$", _e(
        "Microsoft Edge, the browser.",
        "Chromium-based browser, also used by Windows for some links and the help pages.",
        "Part of Windows; its uninstaller refuses in most regions. Set another browser as "
        "default instead (Settings > Apps > Default apps). WebView2 is separate and must stay.")),
    (r"^Microsoft GameInput", _e(
        "Microsoft GameInput.",
        "Microsoft's input API for games: controllers, keyboards and mice with low latency. "
        "Installed by games and by Xbox components that use it.",
        "Removable, but a game using GameInput may lose controller input; its installer "
        "usually puts it back.")),
    (r"^Microsoft \.NET|^Microsoft Windows Desktop Runtime|^Microsoft ASP\.NET", _e(
        ".NET runtime.",
        "The runtime that programs written in C#/.NET run on. Several versions coexist on "
        "purpose: each program needs its major version.",
        "Remove only versions out of support that nothing uses -- a program built for that "
        "version will refuse to start and ask you to install it again.")),
    (r"^Java|^Oracle Java|^Eclipse Temurin", _e(
        "Java runtime.", "Runs programs written in Java (Minecraft launchers, some vendor "
        "tools, development tools).", "Removable if nothing needs Java; old versions should go "
        "anyway, they are a security risk.")),
    # ---- Microsoft --------------------------------------------------------------------------
    (r"^Microsoft OneDrive", _e(
        "Microsoft OneDrive (desktop sync client).",
        "Syncs your OneDrive files and, on this PC, also backs up Desktop, Documents and "
        "Pictures -- those folders are REDIRECTED into OneDrive here.",
        "Do not uninstall without first turning off folder backup (OneDrive > Settings > "
        "Sync and backup > Manage backup): files that exist only online would no longer be "
        "reachable from Explorer.")),
    (r"^Microsoft 365|^Microsoft Office", _e(
        "Microsoft 365 (Office) apps.",
        "Word, Excel, PowerPoint, Outlook (classic), OneNote and friends, installed by "
        "Click-to-Run and updated by it.",
        "Removing it removes every Office app at once. Your Outlook data files (.pst/.ost) "
        "stay on disk; mail in an Exchange/Microsoft 365 mailbox stays on the server.")),
    (r"^Office 16 Click-to-Run", _e(
        "Office Click-to-Run component.", "Part of Microsoft 365's installer/update engine.",
        "Leave it: Microsoft 365 owns it and will not update (or uninstall cleanly) "
        "without it.")),
    (r"^Microsoft Teams Meeting Add-in", _e(
        "Teams Meeting add-in for Outlook.", "Adds the \"New Teams meeting\" button to "
        "classic Outlook's calendar.", "Removable if you do not schedule Teams meetings; Teams "
        "reinstalls it.")),
    (r"^Microsoft Teams \(PWA\)", _e(
        "Microsoft Teams installed as a web app from Chrome.",
        "A Chrome \"app window\" pointing at teams.microsoft.com; Chrome added this entry when "
        "you chose Install Teams in the browser.",
        "Safe to remove; it only removes the shortcut/app window, not your Teams account.")),
    (r"^Microsoft Visual Studio Code", _e(
        "Visual Studio Code (per-user install).", "Microsoft's code editor.",
        "Safe to remove; settings and extensions in %USERPROFILE%\\.vscode and "
        "%APPDATA%\\Code stay unless you delete them.")),
    (r"Application Compatibility (Fix )?Database", _e(
        "An application-compatibility shim database (.sdb).",
        "A set of compatibility fixes a vendor (or an administrator) registered with Windows, "
        "so specific programs run correctly -- e.g. AMD's driver package installs one for "
        "games.",
        "Removing it unregisters those fixes; the program they were for may misbehave again. "
        "Leave it unless you know what it was for.")),
    # ---- Windows ADK ------------------------------------------------------------------------
    (r"^Windows Performance Toolkit|^WPT|^WPTx64", _e(
        "Windows Performance Toolkit (part of the ADK).",
        _ADK + "WPT is Windows Performance Recorder (WPR) and Analyzer (WPA): ETW tracing for "
        "boot, CPU, disk and stutter investigations.", _ADK_REMOVE)),
    (r"^User State Migration Tool", _e(
        "User State Migration Tool (USMT), part of the ADK.",
        _ADK + "USMT's scanstate/loadstate move user accounts, files and settings between "
        "Windows installs.", _ADK_REMOVE)),
    (r"^Windows System Image Manager", _e(
        "Windows System Image Manager (WSIM), part of the ADK.",
        _ADK + "WSIM writes unattend.xml answer files for automated Windows setup.", _ADK_REMOVE)),
    (r"^Oscdimg", _e(
        "Oscdimg, part of the ADK.", _ADK + "Oscdimg builds bootable ISO images (Windows "
        "install media, WinPE).", _ADK_REMOVE)),
    (r"^Imaging (And Configuration )?Designer|^Imaging Tools Support|^Windows Deployment|"
     r"^Windows Setup Files|^Kits Configuration|^Toolkit Documentation|^Supply Chain Trust|"
     r"^BCD and Boot|^OA3Tool|^OACheck|^OATool|^UEV Tools|^Appman", _e(
        "A Windows ADK component.",
        _ADK + "This entry is one of its parts: imaging and configuration designers (ICD, "
        "provisioning packages), DISM headers and tools, setup files, BCD/boot tools, OEM "
        "Activation tools (OA3Tool), UE-V templates or the App-V sequencer, depending on the "
        "name.", _ADK_REMOVE)),
    # ---- Browsers ---------------------------------------------------------------------------
    (r"^Google Chrome", _e("Google Chrome.", "Web browser; also hosts installed web apps "
                           "(PWAs) such as the Teams entry here.",
                           "Removable. Bookmarks and passwords synced to your Google account "
                           "come back on reinstall; unsynced profile data is lost if you also "
                           "delete it.")),
    (r"^Brave", _e("Brave browser.", "Chromium-based browser with built-in ad and tracker "
                   "blocking.", "Removable; the profile stays in %LOCALAPPDATA%\\BraveSoftware "
                   "unless you delete it.")),
    (r"^Mozilla Firefox", _e("Mozilla Firefox.", "Web browser.", "Removable; the profile stays "
                             "unless you delete it.")),
    # ---- Security / VPN ---------------------------------------------------------------------
    (r"^Bitdefender Total Security|^Bitdefender Antivirus|^Bitdefender Internet", _e(
        "Bitdefender Total Security -- this PC's antivirus.",
        "Real-time malware protection, firewall, web protection and ransomware protection. "
        "While it is installed, Microsoft Defender Antivirus steps aside (passive mode).",
        "Removing it turns Microsoft Defender Antivirus back on automatically, so the PC is not "
        "left unprotected -- but use Bitdefender's own uninstaller and restart afterwards.")),
    (r"^Bitdefender Agent", _e("Bitdefender Agent.", "The service that installs, updates and "
                               "licenses Bitdefender products and talks to Bitdefender Central.",
                               "Leave it while any Bitdefender product is installed; it is "
                               "removed with them.")),
    (r"^Bitdefender VPN", _e("Bitdefender VPN.", "VPN client bundled with Bitdefender "
                             "(Hotspot Shield technology).", "Safe to remove if you use "
                             "another VPN (CyberGhost is installed here).")),
    (r"^CyberGhost", _e("CyberGhost VPN.", "VPN client: routes traffic through CyberGhost's "
                        "servers; installs its own network adapter.", "Safe to remove if unused; "
                        "its virtual adapter goes with it.")),
    (r"^KeePass", _e("KeePass Password Safe.", "Offline password manager; the database is the "
                     ".kdbx file you chose, not the program.", "Removing the program never "
                     "deletes your .kdbx database -- but keep a copy before changes.")),
    # ---- Gaming -----------------------------------------------------------------------------
    (r"^Battle\.net", _e("Battle.net.", "Blizzard's launcher: installs, patches and launches "
                         "World of Warcraft, Diablo, StarCraft and the rest.", "Removing it "
                         "leaves installed games on disk but they can no longer be updated or "
                         "launched normally.")),
    (r"^World of Warcraft|^Diablo|^StarCraft|^Overwatch|^Hearthstone", _e(
        "A Blizzard game.", "Installed through Battle.net.", _GAME_BNET)),
    (r"^Steam$", _e("Steam.", "Valve's store and launcher; owns every game in its library "
                    "folders.", "Uninstalling Steam DELETES the steamapps folder under its "
                    "install directory, i.e. every game installed there. Move the library first "
                    "if you want to keep them.")),
    (r"^Cyberpunk 2077", _e("Cyberpunk 2077 (CD PROJEKT RED).", "The game, or its bonus "
                            "content entry.", "Removing it deletes the game; saves in "
                            "Saved Games\\CD Projekt Red stay.")),
    (r"^CurseForge", _e("CurseForge (Overwolf).", "Addon and mod manager (World of Warcraft "
                        "addons, Minecraft mods), runs on Overwolf.", "Safe to remove; installed "
                        "addons stay in the game folders.")),
    (r"^ArdenWoW", _e("ArdenWoW Launcher.", "Launcher for a World of Warcraft private server.",
                      "Safe to remove if unused.")),
    (r"^Vampire Survivors", _e("Vampire Survivors (desktop version).", "Poncle's roguelite.",
                               "Removable; saves depend on the store you bought it from.")),
    (r"^Arrow a Row", _e("Arrow a Row.", "A small indie game.", "Safe to remove.")),
    (r"^Display Driver Uninstaller", _e(
        "Display Driver Uninstaller (DDU).", "Removes GPU drivers completely, normally run in "
        "Safe Mode before a clean driver reinstall.", "Safe to remove; it is only a tool. Do "
        "not run it on this PC casually -- it removes the AMD driver this PC deliberately "
        "keeps on its 2022 version.")),
    (r"^Geeks3D FurMark|^OCCT", _e(
        "GPU/CPU stress-test tool.", "Loads the hardware to its limit to test stability and "
        "cooling (FurMark: GPU; OCCT: CPU, GPU, memory, power supply).", "Safe to remove; "
        "it runs only when you start it.")),
    # ---- Hardware vendors -------------------------------------------------------------------
    (r"^PawnIO", _e(
        "PawnIO kernel driver.",
        "A signed kernel driver that lets programs read and write hardware sensors and fan "
        "controllers safely. THIS app's Thermal Control uses it (through LibreHardwareMonitor) "
        "to read CPU/motherboard temperatures and to run fan curves.",
        "Removing it disables CPU/motherboard temperatures and fan control in Thermal "
        "Control (GPU readings keep working). Reinstall with: winget install namazso.PawnIO.")),
    (r"^GIGABYTE Control Center|^GBT_|^GIGABYTE (Performance|Storage) Library|^Sidekick", _e(
        "GIGABYTE Control Center or one of its modules.",
        "GIGABYTE's utility for the motherboard: RGB lighting (RGB Fusion), fan and "
        "performance profiles, BIOS/driver updates. Each module (RGB sync, dynamic lighting, "
        "MB update, performance and storage libraries, Sidekick) installs as its own entry.",
        "Uninstall Control Center as a whole rather than one module; a missing module leaves "
        "its page broken. Removing it all stops RGB control (lighting falls back to the "
        "board's default) but changes nothing in the BIOS.")),
    (r"^ENE[ _]", _e(
        "ENE RGB/hardware access layer (HAL).",
        "ENE Technology makes the RGB and sensor controller chips on many motherboards, "
        "memory modules, SSDs and capture cards. These HAL entries are the drivers GIGABYTE "
        "Control Center (and similar apps) use to talk to each kind of device.",
        _VENDOR_HAL)),
    (r"^Patriot Viper", _e("Patriot Viper M.2 SSD RGB software.", "Controls the RGB lighting "
                           "on a Patriot Viper SSD.", "Safe to remove; the lighting falls back "
                           "to its default.")),
    (r"^WD .*Game Drive", _e("WD Game Drive software.", "RGB/dashboard utility for a WD P40 "
                             "external SSD.", "Safe to remove; the drive keeps working.")),
    (r"^Verbatim", _e("Verbatim SureFire gaming peripheral software.", "Configures a Verbatim "
                      "SureFire mouse/keyboard (RGB, buttons).", "Safe to remove if you do not "
                      "need to change the device's settings.")),
    (r"^AMD DVR", _e("AMD ReLive recording component.", "Instant replay and recording for AMD "
                     "Radeon Software.", "Leave it while AMD Software is installed; it is part "
                     "of the driver package.")),
    (r"^RyzenMasterSDK", _e("AMD Ryzen Master SDK.", "The driver/library that reads and tunes "
                            "Ryzen CPU settings; installed by Ryzen Master or by a "
                            "motherboard utility.", "Removable if no tuning tool uses it.")),
    (r"^VLIBillboard", _e(
        "VIA Labs USB billboard test tool.", "Installed by a USB-C hub/monitor driver: talks to "
        "the \"billboard\" device a USB-C dock exposes when DisplayPort Alt Mode fails.",
        "Safe to remove.")),
    (r"^Windows Driver Package - libusb", _e(
        "libusb-win32 driver package.", "A generic USB driver some tools install to talk to a "
        "device directly (firmware flashers, RGB controllers, programmers).",
        "Removing it detaches that driver from its device; the tool that installed it stops "
        "seeing the device.")),
    (r"^AppControl", _e("AppControl.", "A third-party utility; its publisher entry gives no "
                        "further detail.", "Check what it is before removing it.")),
    # ---- Development -------------------------------------------------------------------------
    (r"^Python .*Launcher|^Python Launcher", _e(
        "Python Launcher (py.exe).", "Picks the right Python version for `py` and for .py "
        "files you double-click.", "Remove with Python, not alone.")),
    (r"^Python \d", _e(
        "Python, or one of its components.", "The Python interpreter. The installer registers "
        "each part (core, standard library, pip, Tcl/Tk, docs...) as its own entry.",
        "Uninstall the main \"Python 3.x (64-bit)\" entry; the component entries go with it. "
        "THIS app's .venv (development) is built on this Python.")),
    (r"^Git$", _e("Git for Windows.", "Version control, with Git Bash and Git Credential "
                  "Manager.", "Removable; repositories on disk are untouched.")),
    (r"^GitHub CLI", _e("GitHub CLI (gh).", "Command-line access to GitHub: PRs, issues, "
                        "releases, auth.", "Safe to remove; sign-in is stored separately.")),
    (r"^Node\.js", _e("Node.js.", "JavaScript runtime and npm.", "Removable; global npm "
                      "packages and CLIs installed with npm stop working.")),
    (r"^Claude Code", _e("Claude Code (Anthropic).", "Anthropic's coding agent CLI.",
                         "Removable; settings in %USERPROFILE%\\.claude stay.")),
    (r"^opencode|^OpenCode", _e("OpenCode.", "An open-source AI coding agent (terminal and "
                                "desktop).", "Safe to remove.")),
    (r"^AutoHotkey", _e("AutoHotkey.", "Scripting for hotkeys and automation.", "Removable; "
                        ".ahk scripts stop running.")),
    (r"^MATLAB", _e("MATLAB (MathWorks).", "Numerical computing environment; large "
                    "(often 10+ GB with toolboxes).", "Removable; licence activation is tied to "
                    "your MathWorks account.")),
    (r"^Notepad\+\+", _e("Notepad++.", "Text and code editor.", "Safe to remove.")),
    # ---- Media / misc -----------------------------------------------------------------------
    (r"^VLC media player", _e("VLC media player.", "Plays almost any audio/video format with "
                              "its own codecs.", "Safe to remove.")),
    (r"^Stremio", _e("Stremio.", "Media center that streams from add-on sources.",
                     "Safe to remove.")),
    (r"^Deluge", _e("Deluge.", "BitTorrent client.", "Safe to remove; downloads stay.")),
]

DESKTOP: List[Tuple[Pattern[str], About]] = [(re.compile(p, re.IGNORECASE), a)
                                             for p, a in DESKTOP_RAW]

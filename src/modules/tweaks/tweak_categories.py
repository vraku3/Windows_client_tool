"""The 20 real tweak-category definition files, by category name.

Extracted from tweaks_module.py so it can be imported without pulling in
PyQt6 — this is the authoritative list several modules need (tweaks_module's
own tab-building, and debloat_presets.py's category lookup for saved custom
presets) without needing the UI layer that comes with tweaks_module.
"""

CATEGORY_FILES = {
    "Privacy":       "privacy.json",
    "Performance":   "performance.json",
    "Telemetry":     "telemetry.json",
    "UI Tweaks":     "ui_tweaks.json",
    "Services":      "services.json",
    "Gaming":        "gaming.json",
    "Security":      "security.json",
    "Network":        "network.json",
    "AI Features":   "ai_features.json",
    "Navigation Pane": "navigation.json",
    "Explorer":      "explorer.json",
    "Taskbar & Start": "taskbar_start.json",
    "Power":         "power.json",
    "Input":         "input.json",
    "Windows Update": "updates.json",
    "Defender & Firewall": "defender.json",
    "Browsers":      "browser.json",
    "Storage":       "storage.json",
    "Multimedia":    "multimedia.json",
    "Remote Access": "remote.json",
}

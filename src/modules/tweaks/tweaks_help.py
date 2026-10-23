"""Plain-language help derived from tweak definitions and the undo implementation."""

STATUS_HELP = {
    "applied": "Applied: the detectable settings match this tweak; this does not prove this app applied them.",
    "not_applied": "Not applied: the checked settings do not match, including a missing registry value at its Windows default.",
    "partial": "Partial: only some settings match, or the checks cannot establish that every step matches. Read the reason.",
    "not_applicable": "Not applicable: the target service, task, package or required Windows version is absent on this PC.",
    "unknown": "Unknown: no reliable verdict is available, for example because detection has not run, a read was refused or no detection rule exists.",
}

RISK_HELP = {
    "low": "Low risk: a lower-impact change in this catalog, but it can still remove a feature or app you use.",
    "medium": "Medium risk: changes Windows features or behaviour; check whether you depend on them before applying.",
    "high": "High risk: may affect security, connectivity or essential features; review every step before applying.",
}

INTRO = (
    "Review Windows settings, select tweaks or load a preset, then apply the changes with administrator rights. "
    "Undo uses recorded before-values where supported; commands and app removals cannot be fully undone by Revert. "
    "Selecting a checkbox or loading a preset only prepares a selection."
)

APPS_INTRO = (
    "Queue installed apps for removal or catalog apps for installation through winget, the Windows package manager. "
    "Apply asks you to confirm app changes; installers may request administrator rights. "
    "Revert does not restore removed apps or their data; reinstall them separately."
)

SELECTION_HELP = {
    "Select All": "Check every tweak in this category, including filtered-out rows; changes nothing until Apply Selected.",
    "Deselect All": "Clear all checkboxes in this category, including filtered-out rows; this does not undo applied tweaks.",
    "Select Applied": "Select rows detected as Applied and clear the others, including filtered-out rows; selection changes no Windows settings.",
    "Select Not Applied": "Select rows detected as Not applied and clear the others, including filtered-out rows; selection changes no Windows settings.",
}

CONTROL_HELP = {
    "_search_input": "Filter tweak rows by name, description or detection reason across categories. This only changes the view, not Windows or the checked selection.",
    "_risk_filter": "Filter rows by the catalog's risk rating; hidden rows remain checked. Low is lower impact, Medium can affect features, and High can affect security or essential functions.",
    "_status_filter": "Filter by detected state; hidden rows remain checked and can still be applied. Applicable excludes only Not applicable.\n" + "\n".join(STATUS_HELP.values()),
    "_preset_combo": "Choose a saved set of tweak selections, then press Load. Choosing a name alone does not change Windows or the current selection.",
    "_select_all_global_btn": "Check all tweaks in the current category, including hidden rows; Windows changes only when you apply.",
    "_deselect_all_global_btn": "Clear every selection in the current category, including hidden rows; this does not revert applied settings.",
    "_apply_btn": "Apply checked tweaks across all categories, including hidden rows, and queued app changes. App changes ask for confirmation; bulk tweaks run without per-tweak confirmation. Requires administrator rights; undo is limited to recorded reversible steps.",
}

PRESET_HELP = {
    "load_btn": "Replace the checked selections with this preset across categories; does not apply Windows changes or ask for confirmation.",
    "save_btn": "Ask for a name and save the current checked tweak selections as a preset file; changes no Windows settings.",
    "export_btn": "Choose a file and export the selected saved preset as JSON or ZIP for reuse; does not export current Windows settings or apply anything.",
    "import_btn": "Choose a preset JSON or ZIP file to save in the preset list; importing does not apply its settings. Load it to select its tweaks.",
}


def step_summary(step):
    """Describe the actual write, without guessing an undocumented effect."""
    kind = step.get("type")
    if kind in ("registry", "registry_delete"):
        target = step.get("key", "") + "\\" + (step.get("value") or "(default)")
        if kind == "registry_delete":
            return f"Deletes registry value {target}"
        return f"Sets {target} = {step.get('data')!r} ({step.get('kind', 'DWORD')})"
    if kind == "service":
        start = step.get("start_type", "manual")
        start = {2: "automatic", 3: "manual", 4: "disabled"}.get(start, start)
        return f"Sets service {step.get('name', '')} start type to {start}"
    if kind == "scheduled_task":
        return f"Disables scheduled task {step.get('task_name', '')}"
    if kind == "appx":
        return f"Removes installed app package {step.get('package', '')}"
    return "Runs " + kind + ": " + step.get("command", step.get("cmd", ""))


def undo_help(tweak):
    """Keep promises aligned with TweakEngine records and BackupService.revert_step."""
    steps = tweak.get("steps", [])
    parts = []
    if any(s["type"] in ("registry", "registry_delete", "service", "scheduled_task") for s in steps):
        parts.append("Revert can restore recorded registry values, service start types and scheduled-task states after an apply here; it needs a successful saved record and permission to write back.")
    if any(s["type"] == "service" for s in steps):
        parts.append("Revert restores the service start type, not its previous running/stopped state.")
    if any(s["type"] == "appx" for s in steps):
        parts.append("App removals cannot be undone by Revert: reinstall separately; previous app data and configuration are not restored.")
    if any(s["type"] == "command" or (s["type"] == "script" and not s.get("revert_command")) for s in steps):
        parts.append("Commands and scripts without an explicit undo command cannot be undone by Revert.")
    if any(s["type"] == "script" and s.get("revert_command") for s in steps):
        parts.append("Revert runs the script's supplied undo command; it does not restore a complete system snapshot.")
    return "\n".join(parts) or "No recorded undo steps are defined."


def row_help(tweak, status="unknown", reason=""):
    sections = [tweak.get("description", ""), RISK_HELP.get(tweak.get("risk", "low"), "Risk not specified."),
                "What changes:\n" + "\n".join(step_summary(s) for s in tweak.get("steps", [])),
                undo_help(tweak)]
    sections.append("Administrator rights: required to use the Tweaks module. " + (
        "This definition also requests administrator rights." if tweak.get("requires_admin", True)
        else "This definition itself does not request elevation."))
    sections.append(STATUS_HELP.get(status, STATUS_HELP["unknown"]) + ("\nReason: " + reason if reason else ""))
    return "\n\n".join(sections)

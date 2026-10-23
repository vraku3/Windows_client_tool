"""Plain English explanations shared by the Debloat panes; no Qt dependency."""
INTRO = {
    'Debloat': 'Inspect installed apps, remove unwanted packages, or apply Windows preference presets. Read the row explanations before applying: removals may need reinstalling and recorded settings can be recovered through Restore Manager; changes may require administrator rights.',
    'Apps': 'Compare installed Windows app packages with the Debloat catalog and choose which to remove. Apply asks first and needs administrator rights; a restore point is requested, but reinstalling a removed app may require its installer or Microsoft Store and does not recover its data.',
    'Privacy & Telemetry': 'Review Windows privacy, diagnostic reporting, service and network preferences. Applying checked tweaks asks first and needs administrator rights; Restore Manager can undo recorded steps, but not every command has an undo.',
    'AI & Navigation': 'Review preferences for Windows AI features and the desktop navigation experience. Applying asks first and needs administrator rights; Restore Manager can undo recorded steps, but a removed feature or app may need reinstalling.',
    'Run All': 'Run the checked presets in order, applying each preset\'s tweaks and then removing its apps. This asks first and needs administrator rights; one shared restore point records supported changes, while app removal and commands without an undo are not fully reversible.',
}
CONTROLS = {
    'Scan Apps': 'Read installed Windows app packages and compare them with the catalog. This does not remove apps or change Windows.',
    'Apply Selected': 'After confirmation, remove the checked installed app packages with administrator rights. Removed apps may need reinstalling; a restore point does not guarantee recovery of app data.',
    'Apply All Safe': 'Select the catalog entries marked safe, excluding protected apps, and ask before removing them. This label is a catalog recommendation, not a guarantee: removed features may be needed and app data may be lost.',
    'Export': 'Choose a file and export the displayed app inventory. This writes a report, without changing installed apps.',
    'View History': 'Open the locally recorded Debloat run history. Reading this list changes nothing on the PC.',
    'Protected Apps…': 'Show apps excluded from automatic removal because they provide common Windows tools. Explicitly selecting a protected app requires an additional confirmation.',
    'Select All': 'Check all visible selectable rows for a later apply operation. Checking does not change Windows.',
    'Select None': 'Clear checked rows without undoing any changes already applied.',
    'Show all catalogued apps': 'Include catalog entries that are not installed. This only changes the list; it does not install or remove anything.',
    'Light Debloat': 'Load the Light preset selection from the built-in preset file. This changes checkmarks only; apply is separate.',
    'Full Debloat': 'Load the broader Full preset selection from the built-in preset file. Review every checked app or tweak before applying; selecting this button changes no Windows settings.',
    'Privacy-Focused': 'Load the preset selection aimed at reducing diagnostic reporting and online integration. This changes checkmarks only; applying may disable features that depend on those services.',
    'Custom': 'Load your saved Custom selection, replacing the current checkmarks when it is available. This does not apply changes.',
    'Save Selection as Custom': 'Save the checked app selection in your local Custom preset. This overwrites the saved selection for later use, without removing apps.',
    'Save as Custom': 'Save checked tweaks into the local Custom preset, grouped by their categories. This changes the saved preset, without applying it.',
    'Apply Selected Tweaks': 'Ask for confirmation, then apply the checked registry, service, task or command steps with administrator rights. Restore Manager can undo backed-up steps; commands without an undo may require manual recovery.',
    'Revert Applied…': 'Open Restore Manager to review and restore recorded changes. Only backed-up settings and steps with an undo can be restored; this is not a per-app reinstall.',
    'Run All': 'Ask for confirmation, then run every checked preset in order using a shared restore point. Each can change settings and remove apps; overlapping presets can repeat changes, and not every removal or command can be undone.',
    'Stop': 'Request cancellation before further work starts. An operation already running may finish, and completed changes remain applied.',
    'Cancel': 'Request cancellation or close this confirmation. Completed operations are not automatically undone.',
    'Close': 'Close this window without making additional changes.',
    'Copy registry path': 'Copy this tweak\'s registry location to the clipboard. This does not edit the registry.',
    'Copy command': 'Copy this tweak\'s command to the clipboard without executing it.',
}
HEADERS = {
    '☐': 'Check this row for a later apply operation; selection alone changes no Windows settings.',
    'App Name': 'Friendly app name from the Debloat package catalog, matched against Windows package inventory.',
    'Tweak': 'Preference name from the built-in tweak definitions; hover the row for its description.',
    'Category': 'Catalog group used to organise related app packages or Windows preferences.',
    'Risk': 'Catalog estimate of how disruptive the tweak may be, not a guarantee of compatibility.',
    'Status': 'Last scan or detection result. Applied means all checked steps match; Partial means only some match; Not Applicable means the target does not exist here; Unknown means the read failed.',
}


def tweak_help(tweak):
    description = tweak.get('description') or tweak.get('name', 'Windows preference')
    costs = tweak.get('warning') or tweak.get('notes') or ''
    return (str(description) + (' ' + str(costs) if costs else '') +
            ' Applying runs this definition?s checked steps with administrator rights after confirmation. '
            'Restore Manager can undo recorded steps; commands without an undo and removed app data are not restored automatically.')

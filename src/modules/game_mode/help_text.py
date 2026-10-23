"""Game Mode and persistent optimisation explanations, independent of Qt."""
INTRO = 'Review recommendations for this PC and preview which programs and services Game Mode will stop. ON asks first and records changes before applying; OFF restores only those recorded changes, and stopping services or changing system settings needs administrator rights.'
PANES = {
    'Recommendations': 'Read findings about display settings, background apps and gaming configuration. Refresh changes nothing on the PC; suggested fixes may take you to other modules where administrator rights can be required.',
    'What Game Mode changes': 'Choose the services to stop, programs to close and temporary preferences for the next ON operation. Choices alone change nothing; ON asks first, may end unsaved programs after eight seconds and needs administrator rights for service changes, while OFF restores only its recorded state.',
    'Log': 'Read the Game Mode operation transcript, including skipped and failed actions. Nothing here changes the PC; OFF uses its saved recovery record rather than this displayed log, and system restore actions can need administrator rights.',
    'Optimise OS': 'Apply persistent Windows preferences independently of Game Mode ON/OFF. Apply and Revert show a preview and ask first; supported settings use a separate recovery record, administrator rights may be required, and deleted files cannot be recovered here.',
    'Settings (reversible)': 'Review current preferences, targets and compatibility costs before applying. Tailored uses this PC\'s profile, Maximum selects more settings, and Optional rows require your choice; administrator rights may be needed and Revert uses recorded previous settings, with capability re-adds requiring Windows Update.',
    'Free up space (one-way)': 'Inspect exactly which temporary files and caches are eligible for deletion, then tick the locations to clean. Free up space asks first and may need administrator rights; deleted files bypass the Recycle Bin and cannot be restored by Revert.',
    'Apps you may not use': 'Review retained Windows usage evidence to help decide which apps to investigate. Nothing is removed here and missing evidence means unknown; App Buster handles confirmed removal separately and may need administrator rights.',
}
CONTROLS = {
    'Game Mode ON': 'Preview and confirm closing checked programs, stopping checked services and applying temporary choices. Programs still open after eight seconds may be ended and lose unsaved work; service changes need administrator rights, and OFF restores only recorded actions.',
    'Game Mode OFF': 'Restore the recorded power plan, preferences, services, tasks and eligible apps from this Game Mode session. It cannot restore unsaved work, only reopens recorded apps, and keeps failed recovery entries for another attempt; service recovery needs administrator rights.',
    'Tailored (this PC)': 'Choose the persistent settings profile tailored to detected hardware and this PC. This only changes targets and checkmarks; Apply previews the actual changes before confirmation.',
    'Maximum': 'Choose the broader persistent settings profile. Optional settings still need an explicit tick; nothing changes until Apply is reviewed and confirmed.',
    'Apply': 'Read current state and show a preview before applying checked persistent preferences. These survive Game Mode OFF; administrator rights may be required, and only recorded changes can be reverted.',
    'Revert': 'Preview restoring the persistent preferences recorded by Optimise OS, then ask before applying. This does not recover deleted files; re-adding a removed Windows capability needs Windows Update and may fail offline.',
    'Refresh': 'Read current settings, background activity and available cleanup or usage evidence. This refresh does not apply changes or delete files.',
    'Free up space...': 'Rescan selected locations, preview eligible files and ask before deleting them or running selected component cleanup. Files are not sent to the Recycle Bin; this has no undo and some operations need administrator rights.',
    'OK': 'Confirm the exact operations listed in this preview. Persistent changes use the recorded undo where supported; file deletion has no undo and capability re-adds need Windows Update.',
}
FLAGS = {
    'plan': 'Select the CPU-aware power plan when Game Mode turns on. Dual-chiplet Ryzen X3D systems with the optimizer use Balanced; other CPUs use the performance plan. OFF restores the recorded previous plan.',
    'gamebar': 'Select changing Windows\' own Game Mode preference on ON. This does not install Xbox Game Bar; OFF restores the recorded registry state.',
    'vcache': 'Select starting the AMD 3D V-Cache optimizer service if present and stopped. Needs administrator rights; OFF restores the recorded service state.',
    'maintenance': 'Select pausing Windows Automatic Maintenance while ON. Maintenance work is delayed, not removed; OFF restores the recorded preference and may need administrator rights.',
    'tasks': 'Select disabling catalogued updater and maintenance tasks while ON. Updates and maintenance are delayed; OFF restores only recorded task enabled states, and protected tasks may be refused without administrator rights.',
    'visuals': 'Select disabling transparency and window animations on ON. This changes desktop appearance temporarily; OFF restores the recorded previous values.',
    'explorer': 'Select restarting the Windows desktop shell as the last ON step. This closes File Explorer folders and interrupts copies; OFF cannot reconstruct those windows or recover interrupted work.',
    'lower': 'Select lowering the priority of eligible apps left open while ON, so they compete less for CPU time. OFF restores recorded priorities only when the process still matches and its priority has not since changed.',
}
HEADERS = {
    '': 'Finding indicator from the recommendation engine, not an independent performance benchmark.',
    'Finding': 'Recommendation or observed condition from the engine\'s reads of this PC; hover the row for evidence.',
    'What to do': 'Suggested next step; reviewing it makes no changes.',
    'Stop': 'Tick to stop this service on the next confirmed ON operation. Startup type stays unchanged; OFF restores recorded changes.',
    'Service': 'Friendly service name from the Game Mode catalog, matched against Windows service state.',
    'Now': 'Latest observed state read by the engine; unknown or unreadable is not Off.',
    'Why': 'Catalog explanation, compatibility cost or observed reason for the proposed action.',
    'Close': 'Tick to close this program on ON. After eight seconds it can be ended, losing unsaved work; OFF may reopen the program, not its unsaved contents.',
    'Program': 'Running app group from process inventory, with helpers folded into their parent app.',
    'Handles': 'Number of open operating-system object references reported for this process group. This is a count, not memory usage or predicted frame rate.',
    'While playing': 'Planned treatment based on the current choices: close, lower priority, protect or leave running.',
    'Setting': 'Persistent preference from the optimisation catalog. Optional rows need your explicit tick; read-only rows cannot be applied here.',
    'Profile target': 'Value requested by the selected persistent profile, rather than a measured current value.',
    'Status': 'Engine comparison with the target and saved recovery record. Unreadable settings are not assumed disabled.',
    'Location': 'Filesystem path or app location reported by the scan; viewing it changes nothing.',
    'Present': 'Measured bytes found at this location, displayed in scaled units; this can exceed the amount eligible to delete.',
    'Newest file / status': 'Newest modification time found by the scan, or the reason a location could not be measured.',
    'Safety': 'Cleanup engine classification and exclusions. A lower-risk cache can still lose useful temporary data; deletion has no undo.',
    'Eligible': 'Bytes meeting the cleanup engine\'s age and exclusion rules, displayed in scaled units. A fresh scan is made before deletion.',
    'App': 'App identified by Windows usage and installation records, not a list of apps proven unnecessary.',
    'Kind': 'Type of app or usage evidence identified by the engine.',
    'State': 'Usage classification based on retained records; no usage record means unknown, not unused.',
    'Last seen': 'Latest retained usage timestamp reported by Windows, not guaranteed to be the last real launch.',
    'Source': 'Usage evidence source: UserAssist, Background Activity Moderator (BAM), or Prefetch. Records may be incomplete.',
}


def setting_help(spec, why):
    level = {'T': 'Tailored: included in the profile for this PC.',
             'M': 'Maximum: included in the broader profile.',
             'O': 'Optional: not selected automatically; tick it only if you want its trade-off.',
             'R': 'Read-only: this preference is observed here and belongs to Tweaks.'}.get(spec.level, spec.level)
    undo = ('Revert needs Windows Update / internet to re-add this capability; the payload is not saved locally.'
            if spec.kind == 'capability' else 'Revert uses the recorded previous setting; it cannot recover deleted user data.')
    if spec.read_only:
        undo = 'Observed only: this row has no Apply or Revert action here.'
    permission = ' Needs administrator rights.' if spec.admin else ''
    return f'{spec.label}: {spec.why} {why} {level} {undo}{permission}'

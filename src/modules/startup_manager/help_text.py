"""Plain-language explanations for this pane; no Qt dependencies."""
INTRO = ('Startup entries launch programs at sign-in, boot, or when Windows loads an extension. User entries '
 'can be switched here; some sources are read-only, and removing an entry has no undo button. Browsing '
 'needs no administrator rights; machine-wide changes may need them.')

CONTROLS = {'Enable': 'Allow the selected user startup entry to run automatically again. Registry and Startup '
           'Folder panes enable it immediately; Everything asks first. This does not start the program '
           'now; Disable reverses the switch.',
 'Disable': 'Ask before preventing this entry from launching automatically. The program stays installed '
            'and an Enable button can reverse this switch; machine-wide entries may require '
            'administrator rights.',
 'Remove': 'Ask before permanently deleting the selected startup registry value or Startup Folder item. '
           'The app cannot undo removal; this does not uninstall the target program.',
 'Add': 'Open a dialog to add a current-user Run registry entry. Accepting the dialog makes its command '
        'run on future sign-ins; remove the entry here to stop it.',
 'Open Location': 'Open the folder behind the selected command in Explorer. If the old source-specific '
                  'view cannot resolve a path, it opens the user Startup Folder; this does not launch '
                  'the target program.',
 'Open Startup Folder': 'Open your Startup Folder in Explorer. Windows launches items in that folder at '
                        'sign-in; merely opening it changes nothing.',
 'Copy Summary': 'Copy the current startup inventory summary to the clipboard, replacing its previous '
                 'text. No startup entries change.',
 'Change History': 'Show changes recorded by this tool in the Everything pane. This is an activity log, '
                   'not an undo command or a complete Windows audit trail.',
 'All': 'Show all startup entries collected by this scan. This only clears the category filter.',
 'Flagged': 'Show entries with findings from this tool. A finding is a reason to investigate, not proof '
            'of malware; no startup switches change.',
 'Not signed': 'Show files reported as unsigned by the signature check. Unsigned is not proof of '
               'malware; catalog-signed Windows files are recognised separately.',
 'Missing file': 'Show entries whose resolved executable was not found. A disconnected drive or an old '
                 'shortcut can cause this; the filter deletes nothing.',
 'Not Microsoft': 'Show entries not identified as Microsoft by the available publisher information. '
                  'This is a filter, not a trust verdict.',
 'Disabled': 'Show entries whose automatic launch is disabled. Viewing them does not enable or remove '
             'them.',
 'Recently added': "Show entries first seen after the saved initial scan. First seen is this tool's "
                   "observation, not the program's install date.",
 'Slowed boot': 'Show entries matched to Windows slow-start events. This uses recorded startup delays, '
                'not a benchmark of every installed program.'}

HEADERS = {'Name': 'Entry name from its registry value, shortcut, task, service, or extension registration.',
 'Command/Path': 'Command or path read from this startup source. It can include arguments; the target '
                 'is not executed by displaying it.',
 'Status': 'Whether this startup entry is enabled according to its source. Enabled is an '
           'automatic-launch setting, not evidence the program is running.',
 'Notes': 'Additional information reported by the source, such as the registry location or task '
          'details.',
 'State': 'Automatic-launch state reported for this entry; unknown means the scan could not determine '
          'it.',
 'Source': 'Where Windows registers this item, for example a Run registry key, Startup Folder or '
           'scheduled task.',
 'Scope': 'User or machine scope: whether the registration belongs to one account or all users.',
 'Publisher': 'Publisher found in the executable metadata or signature. Missing publisher information '
              'does not prove the file is malicious.',
 'Signature': "Result of checking the executable's embedded or Windows catalog signature. Unknown means "
              'the check could not decide; signed is not a guarantee of harmless behaviour.',
 'Boot impact': 'Largest matching delay in Windows Diagnostics-Performance startup events, displayed in '
                'seconds. Empty means no matching event, not zero cost.',
 'First seen': 'Date this tool first observed the entry in its saved inventory. First scan marks the '
               'baseline, not the install date.',
 'Findings': 'Reasons this tool flagged the entry. Read the row details before deciding to disable it.'}

TABS = {'Everything': 'A combined inventory of automatic launch locations, signatures and findings. Only '
               'supported user entries can be toggled here after confirmation; Enable reverses Disable, '
               'and machine entries may require admin rights.',
 'Registry': "Programs registered in your account's Run key start at sign-in. Enable/Disable are "
             'reversible here; Add and Remove change registry values, and removal has no undo. Your own '
             'key normally needs no administrator rights.',
 'Machine (HKLM) Run': 'Machine-wide Run registry entries apply to all users at sign-in. This pane is '
                       'read-only and does not change the PC; administrator rights may be needed to '
                       'read restricted registrations.',
 'Startup Folder': 'Shortcuts and files in your Startup Folder run at sign-in. Enable/Disable can be '
                   'reversed here; Remove deletes the folder item after confirmation and has no undo. '
                   'Your own folder normally needs no administrator rights.',
 'RunOnce': 'RunOnce registry entries request a one-time launch at a future sign-in. This pane reads '
            'the registrations without changing them; restricted machine keys may require admin rights.',
 'Scheduled Tasks': 'Tasks can launch programs at sign-in or boot. This startup inventory is read-only; '
                    'use System Management to change tasks with the required account permissions.',
 'Services': 'Services can start in the background without a user signing in. This startup inventory is '
             'read-only; service changes belong in System Management and generally need administrator '
             'rights.',
 'Browser Extensions': 'Extensions add features inside a browser. This pane reads available '
                       'registrations without changing them; use the browser to manage them, and '
                       'restricted profiles may not be readable.',
 'Shell Extensions': 'Shell extensions add handlers and menus to Windows Explorer. This pane reads '
                     'registrations without changing them; restricted registrations may need '
                     'administrator rights to read.',
 'Icon Overlays': 'Icon overlay handlers add small status marks to Explorer icons. This pane reads '
                  'registrations without changing them; an entry does not prove its overlay is '
                  'currently visible, and restricted keys may require admin rights.'}


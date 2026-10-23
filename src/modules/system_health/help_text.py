"""Plain-language explanations for this pane; no Qt dependencies."""
INTRO = ('Findings explain servicing and reliability evidence; they do not repair the PC. Servicing buttons '
 'require administrator rights and can remove Windows component files after confirmation. Reset Base '
 'permanently removes uninstall options for currently installed updates and cannot be undone by this '
 'app.')

CONTROLS = {'Refresh Findings': 'Read servicing, task, disk and event-log evidence again without repairing '
                     'anything. Some checks may be unavailable without administrator rights; allow time '
                     'for Windows queries.',
 'Copy finding': 'Copy the selected finding and its supporting evidence to the clipboard, replacing its '
                 'previous contents. No Windows changes.',
 'Open related tab': 'Navigate to the app pane related to this finding. Navigation alone does not apply '
                     "a repair; review the destination's actions before using them."}

HEADERS = {}

TABS = {'Findings': 'Read-only checks for pending servicing, orphaned tasks, upgrade headroom and other '
             'available evidence. Restricted checks may need administrator rights; Copy and navigation '
             'buttons do not repair anything.',
 'Servicing': 'Check the Windows component store or remove superseded components using DISM. '
              'Administrator rights and confirmations are required for cleanup; Reset Base additionally '
              'needs a clean scan, typed confirmation and a restore point, and has no undo.'}


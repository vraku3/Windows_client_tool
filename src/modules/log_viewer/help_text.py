"""Plain-language explanations for Log Viewer; no Qt or machine queries."""

INTRO = 'Read CMTrace and plain-text logs, filter messages and compare investigations. Viewing and filtering do not modify the original logs; exports and saved investigations write files you choose. Protected log files may require administrator rights.'
SOURCE = 'the parsed log records'
CONTROLS = {'refresh': 'Read the current information again and replace the displayed results. This does not '
            'change Windows settings; the read may take a moment.',
 'scan drives': 'Read disk and volume health information. This does not repair or erase a drive; '
                'some checks need administrator rights.',
 'copy details': 'Copy the selected record to the clipboard, replacing its previous contents. This '
                 'does not change the selected item.',
 'copy path': 'Copy the selected path to the clipboard. This does not change the file or registry '
              'key.',
 'copy to clipboard': 'Copy the displayed results to the clipboard, replacing its previous '
                      'contents. The source data is unchanged.',
 'copy as markdown': 'Copy the current report as Markdown text to the clipboard. This does not '
                     'change restore points.',
 'expand all': 'Show every available child row. This only changes the view and may take time for a '
               'large tree; Collapse hides them again.',
 'collapse all': 'Hide expanded child rows without deleting data. Expand opens them again.',
 'all': 'Show all records without this category restriction. This changes only the displayed '
        'results.',
 'enabled': 'Show records whose enabled flag is set. This filter does not enable anything.',
 'disabled': 'Show records whose enabled flag is clear. This filter does not disable anything.',
 'close': 'Close this window. This does not undo changes already applied.',
 'cancel': 'Cancel this dialog or request. Changes already completed are not automatically undone.',
 'ok': 'Accept the choices in this dialog and return to the pane. Review any change-specific '
       'controls before accepting.',
 'browse': 'Open a file or folder picker to choose the target. Choosing a location does not itself '
           'alter its contents.',
 'search': 'Search the selected data and show matching results. This reads information without '
           'changing it.',
 'go': 'Navigate to the entered location. This changes the view without changing the target.',
 'export csv': 'Choose a destination and write the displayed records as a CSV file for a '
               'spreadsheet. This leaves the source records unchanged.',
 'export html report': 'Choose a destination and write an HTML report. This does not change '
                       'hardware; review identifying information before sharing.',
 'generate report': 'Read system information and build the report preview. This may take a moment '
                    'and does not change Windows settings.',
 'save html': 'Choose a destination and save the generated HTML report. This writes a file without '
              'changing Windows settings.',
 'view detail': 'Open the selected record with its available properties. This is a read-only view.',
 'filter': 'Limit displayed records to text matching this field. Clear the text to show the other '
           'records again; no underlying data changes.',
 'search edit': 'Enter text to filter or search the displayed records. This does not edit the '
                'source data.',
 'scrollleftbutton': 'Scroll the tab strip left to reveal earlier tabs. This changes only the '
                     'view.',
 'scrollrightbutton': 'Scroll the tab strip right to reveal later tabs. This changes only the '
                      'view.',
 'qt_tableview_cornerbutton': 'Select table cells using the table selection mode. This does not '
                              'change the underlying records.',
 '×': 'Dismiss this message or clear this field, depending on its location. This does not undo '
      'completed operations.',
 'about': "Show this application's version and purpose. This does not change Windows settings.",
 'find box': 'Enter text to find in the current view. This changes only matching or selected '
             'results, not source data.',
 'find': 'Find a matching entry in the current results. This does not change the underlying '
         'records.',
 'options': "Open this pane's settings dialog. Viewer preferences can change; the dialog describes "
            'any operation that affects the PC.',
 'filter name or value': 'Limit displayed records to text matching this field. Clear the text to '
                         'show the other records again; no underlying data changes.',
 'dir combo': 'Filter rules by incoming or outgoing traffic. This changes only the view, not '
              'network access.',
 'action combo': 'Filter rules by their Allow or Block action. This does not enable, disable or '
                 'rewrite rules.',
 'profile combo': 'Choose a profile/category in this pane. Selection changes only the current view '
                  'until an Apply action is used.',
 'path combo': 'Enter or choose a scan target. The scan starts through the scan control and reads '
               'metadata without changing files.',
 'path bar': 'Enter a full registry hive and key path to navigate to. This does not create or '
             'modify a key.',
 'recent combo': 'Choose a recently visited registry path. This changes only navigation history '
                 'and the current view.',
 'component button': 'Choose which log components to include. This filters records without '
                     'modifying the original logs.',
 'exclude box': 'Enter text that matching messages must not contain. This filters the view without '
                'deleting source records.',
 'open button': 'Open the menu for choosing a log, folder or saved investigation. Source files are '
                'read rather than edited.',
 'preset button': 'Choose a saved set of log filters. This changes the view without changing '
                  'source records.',
 'source': 'Filter by the log source. Clear this field to show other sources again; files are not '
           'modified.',
 'thread': 'Filter by the thread identifier recorded in the log. This does not inspect or control '
           'a live thread.',
 'text to look for': 'Enter words to find in the displayed log messages. This changes only '
                     'matching results, not log files.',
 'a-': "Reduce this table's text size. This changes only presentation; A resets the zoom.",
 'a+': "Increase this table's text size. This changes only presentation; A resets the zoom.",
 'a': "Reset this table's text size to its normal zoom. No records are changed.",
 'open log': 'Choose a log to read. CMTrace records are detected automatically and other text is '
             'parsed best-effort; the original file is unchanged.',
 'previous': 'Jump to the previous matching message in the loaded log. This changes only the '
             'selection.',
 'next': 'Jump to the next matching message in the loaded log. This changes only the selection.',
 'error lookup': 'Open the error-code lookup for a Windows or servicing result code. A code '
                 'description provides context rather than proving the cause of this log entry.',
 'highlight rules': 'Edit message-highlighting rules used by this viewer. Colours affect the view '
                    'only, not the severity stored in the source log.',
 'clear range': 'Remove the time-range filter and show records outside the selected range again. '
                'This does not erase log entries.',
 'info': 'Include informational records in the view. Severity comes from the parsed log record; '
         'this does not change logging on the PC.',
 'warning': 'Include warning records in the view. A warning is a recorded condition to '
            'investigate, not proof of a current fault.',
 'error': 'Include error records in the view. Recorded errors can describe historical or recovered '
          'failures; this does not repair them.',
 'any time': 'Choose a time window for visible log messages. This filters loaded timestamps '
             'without changing the original log.',
 'component: all': 'Filter by the component that wrote each parsed log record. This changes only '
                   'the view.',
 'hide servicing boilerplate': "Hide repetitive servicing messages matched by this viewer's "
                               'preset. Hidden entries remain in the original log; turn the preset '
                               'off to show them again.',
 'save an evidence bundle': 'Choose a destination and save investigation evidence files. This '
                            'writes a bundle without modifying source logs; review sensitive '
                            'message text before sharing.',
 'save investigation to a file': 'Choose a destination and save this investigation for later '
                                 'reopening. This writes a file without editing source logs.',
 'open an investigation': 'Choose a saved investigation to reopen in this viewer. This changes the '
                          'current view without modifying the source logs.',
 'presets': 'Choose a prepared set of message filters. This changes only the viewer; hidden '
            'records remain in their source log.',
 'follow': 'Read newly appended records and follow the live log while enabled. This does not alter '
           'logging or the source file.',
 'regex': 'Treat search text as a regular expression, a pattern language for matching messages. '
          'This changes matching only; an invalid pattern is reported.',
 'fold continuations': 'Fold continuation lines into their parent message for display. This '
                       'changes only the view, not source log lines.',
 'include rolled (.lo_)': 'Include rotated CMTrace .lo_ files in the selected log set. This reads '
                          'more files without changing them.',
 'load earlier': 'Read an older chunk of the open log. This can take a moment and changes the '
                 'loaded window, not the original file.',
 'search earlier': 'Search earlier portions of the log beyond the currently loaded window. This '
                   'reads more data without modifying the log.',
 'newest': 'Load the newest available records from the open log. Older records remain in the '
           'source file.',
 'go to': "Jump to the entered record or time using the viewer's navigation dialog. This changes "
          'only the loaded view and selection.',
 'summary': 'Show counts and patterns calculated from the loaded records. This is not a complete '
            'health assessment or a repair.',
 'errors + context': 'Show errors with nearby messages so preceding and following activity remains '
                     'visible. This is a view filter; the original log is unchanged.',
 'message colours': 'Edit message-matching colour rules for this viewer. This changes presentation '
                    'without changing source severity or message text.',
 'issue ▶': 'Jump to the next issue identified among loaded records. This changes selection '
            'without attempting a repair.',
 '◀ issue': 'Jump to the previous issue identified among loaded records. This changes selection '
            'without attempting a repair.',
 'compare against another log': 'Choose another log and compare its parsed records with this one. '
                                'Both original files remain unchanged.',
 'open every log in a folder': 'Choose a folder and open its supported log files together. This '
                               'reads files without editing them; many logs can take time.',
 'open a folder and its subfolders': 'Choose a folder and discover supported logs recursively. '
                                     'This reads metadata and logs without editing them; large '
                                     'folder trees can take time.',
 'cbs (component store)': 'Read Windows Component Based Servicing activity from CBS.log. This does '
                          'not run Windows Update, DISM repair or component cleanup.',
 'cbs — newest rolled archive': 'Read the newest available archived CBS log; extraction writes a '
                                'temporary working copy. This does not run Windows Update, DISM '
                                'repair or component cleanup.',
 'dism': 'Read Deployment Image Servicing and Management activity. This does not run Windows '
         'Update, DISM repair or component cleanup.',
 'windows update (reportingevents)': 'Read Windows Update reporting events. This does not run '
                                     'Windows Update, DISM repair or component cleanup.',
 'windows update (windowsupdate.log)': 'Open the Windows Update text-log workflow, which may '
                                       'generate a readable log from Windows tracing data. This '
                                       'does not run Windows Update, DISM repair or component '
                                       'cleanup.',
 'errors only': 'Show parsed Error-level records. This does not run Windows Update, DISM repair or '
                'component cleanup.',
 'errors and warnings': 'Show parsed Error and Warning records. This does not run Windows Update, '
                        'DISM repair or component cleanup.',
 'failing result codes': 'Show records matched by the preset for failing result-code messages. '
                         'This does not run Windows Update, DISM repair or component cleanup.',
 'setup phase boundaries': 'Show records matched by the preset for Windows setup phase '
                           'transitions. This does not run Windows Update, DISM repair or '
                           'component cleanup.',
 'store damage': 'Show records matched by the preset for component-store damage messages. This '
                 'does not run Windows Update, DISM repair or component cleanup.'}
HEADERS = {'Time': 'Timestamp parsed from the log record. Plain-text parsing is best-effort and undated '
         'lines may have no timestamp.',
 'Severity': 'Parsed record level: CMTrace types 1, 2 and 3 mean Info, Warning and Error. It '
             'describes the recorded event, not current PC health.',
 'Component': 'Component name supplied by the log record, when available. This identifies the '
              'writer rather than an installed-program inventory.',
 'Thread': 'Thread identifier recorded by the log writer. It is not necessarily a currently '
           'running Windows thread.',
 'Message': 'Original message text parsed from the log. Filtering and highlighting do not modify '
            'this source text.',
 'Source': 'Source file or source identity retained by the investigation, useful when several logs '
           'are merged.',
 'Package': 'Servicing package identity extracted from the available log messages. This is not a '
            'fresh package-state query.'}
TABS = {'Personal': 'Current user MY store: certificates associated with this Windows account.',
 'Computer': 'Local machine MY store: certificates associated with the PC rather than only this '
             'user.',
 'Trusted Root': 'Machine root authorities Windows is configured to trust. This pane does not add '
                 'or remove trust.',
 'Intermediate CAs': 'Intermediate certificate authorities that help link a certificate to a '
                     'trusted root.',
 'Disallowed': "Certificates explicitly placed in Windows' disallowed store. Listing them does not "
               'make them trusted.',
 'Trusted Publishers': 'Publisher certificates in the machine store used in publisher-trust '
                       'decisions.',
 'Chart': 'Visual comparison of the selected scan totals; chart area follows the active measure.',
 'Details': 'Files and subfolders immediately under the selected folder, with independent Size and '
            'Allocated columns.',
 'Extensions': 'Scan totals grouped by filename extension; an extension is a naming convention, '
               'not content verification.',
 'File groups': "Scan totals grouped into file-type categories using the app's extension rules.",
 'Users': 'Records grouped by owner in TreeSize, or local accounts in Local Users & Groups; values '
          "come from the pane's data source.",
 'Age of Files': 'Scan totals grouped by file-age bands; age is not proof a file can be deleted.',
 'Top Files': 'Largest files in the current scan, ranked by the displayed measure.',
 'History': 'Saved scan history for comparison over time; older measurements may differ from '
            'current disk contents.',
 'Groups': 'Local Windows groups and their reported members. This view does not edit membership.',
 'Findings': 'App-generated observations based on the collected records. These are review hints, '
             'not automatic repairs.',
 'Asset Record': 'Hardware identity and asset facts reported by Windows, with snapshot comparison '
                 'where available.',
 'Firmware and Security': 'Firmware and security capability information reported by Windows; '
                          'missing facts are not proof of a disabled capability.'}


def normalized(text):
    text = str(text or '').replace('&', '').replace('…', '').replace('...', '')
    text = text.strip().removeprefix('🔄').strip().lower()
    return ' '.join(text.split())


def control_help(text, key=''):
    label = normalized(text)
    if label.startswith(('filter ', 'search ')):
        return CONTROLS['filter']
    if label in CONTROLS:
        return CONTROLS[label]
    key = normalized(key.replace('_', ' '))
    if key in CONTROLS:
        return CONTROLS[key]
    if any(word in key for word in ('search', 'filter', 'find text')):
        return CONTROLS['filter']
    if label.startswith(('export', 'save ')):
        return 'Choose a destination and save ' + str(text).replace('…', '') + ' data. This writes local files; the source records are unchanged.'
    if label.startswith(('expand', 'collapse', 'hide below', 'show ', 'sort ', 'hide ', 'fit ', 'autosize', '0 decimal', '1 decimal', '2 decimal')):
        return 'Change how the current results are displayed: ' + str(text) + '. This does not modify the underlying records; choose another view to change it again.'
    if label in ('b', 'kb', 'mb', 'gb', 'tb', 'auto'):
        return 'Display sizes using ' + str(text) + ' units (Auto chooses a readable unit). This changes formatting, not the measured byte counts or files.'
    if label.endswith(('\\', ':/')) or (len(label) == 2 and label.endswith(':')):
        return 'Scan this drive to read disk usage. A whole NTFS volume can use the fast MFT reader when elevated; other targets use a directory walk. Files are unchanged.'
    return str(text or key or 'Control') + ': choose or enter the value used by this pane. This field alone does not apply changes; review its accompanying action before proceeding.'


def header_help(label):
    label = str(label or '')
    return HEADERS.get(label, label + ': value shown from ' + SOURCE + '. A blank or unavailable value is not a measured zero.')


def tab_help(label):
    return TABS.get(str(label), str(label) + ': inspect this category of ' + SOURCE + '.')

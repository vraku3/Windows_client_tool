"""Plain-language explanations for TreeSize; no Qt or machine queries."""

INTRO = 'Explore which folders and file types use disk space. Size is file content length; Allocated is the space actually reserved on disk, which differs for compressed and sparse files. Folder scans work without administrator rights; elevated whole NTFS drive scans can read the Master File Table (MFT) directly, while other targets use a directory walk. Scanning does not remove files; file actions and scheduled scans have separate controls.'
SOURCE = 'the filesystem scan or saved scan'
CONTROLS = {'refresh': 'Scan again to replace the current results with current disk information. Files are '
            'unchanged; a full drive may take a while.',
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
 'size': 'Show logical file content lengths, summed for folders. This can be much larger than '
         'Allocated for sparse or compressed files; changing the mode does not alter files.',
 'allocated space': 'Show disk space reserved for file data, independently measured from logical '
                    'Size. Sparse and compressed files can use much less space; changing the mode '
                    'does not alter files.',
 'number of files': 'Show the count of files under each folder rather than bytes. This changes '
                    'only how the scan is displayed.',
 'percent': "Show each entry's share of the selected total as a percentage. This changes only the "
            'display mode.',
 'start as administrator': 'Request an elevated restart through Windows. Whole NTFS drives can '
                           'then use the fast Master File Table reader; folders, remote targets '
                           'and other filesystems use a directory walk.',
 'watch for file system changes': 'Watch the scanned target for filesystem notifications and '
                                  'update the displayed scan. This reads changes without modifying '
                                  'files; stop watching to stop live updates.',
 'run this scan daily': 'Create or replace the TreeSize scheduled task to scan this target daily. '
                        'This changes Task Scheduler; Remove the scheduled task removes that '
                        'automation.',
 'run this scan weekly': 'Create or replace the TreeSize scheduled task to scan this target '
                         'weekly. This changes Task Scheduler; Remove the scheduled task removes '
                         'that automation.',
 'remove the scheduled task': 'Remove the TreeSize scan task from Windows Task Scheduler. Existing '
                              'scan results and source files are not removed; scheduling again '
                              'recreates the automation.',
 'exclude selected folder': 'Exclude the selected folder from scan results. This does not delete '
                            'it; clear exclusions and rescan to include it again.',
 'exclude by pattern': 'Enter a name/path pattern to omit from this scan. This affects analysis '
                       'rather than deleting matching files.',
 'always exclude by pattern': 'Save an exclusion pattern for future scans. Matching files remain '
                              'on disk; clear the saved exclusions to include them again.',
 'clear all exclusions': 'Clear scan exclusion patterns. Files are unchanged; rescanning can '
                         'include previously excluded locations.',
 'find duplicate files': 'Open duplicate search to compare candidate files. Searching does not '
                         'remove files, and a result alone is not permission to delete a copy.',
 'find files': 'Open file search over scan information. Searching reads metadata and does not '
               'modify files.',
 'new scan': 'Choose a target and start a disk-usage scan. This reads file information without '
             'changing files; a full drive may take a while.',
 'select folder': 'Choose a folder for a directory-walk scan. The scan reads metadata and does not '
                  'delete or modify files.',
 'remote target': 'Open target selection for remote storage. Access depends on the target and '
                  'credentials; scanning reads information without deleting remote files.',
 'refresh selected folder': 'Rescan the selected folder and update its totals. This reads '
                            'information without modifying files.',
 'refresh all scans': 'Rescan the open targets to update their totals. This reads metadata without '
                      'modifying files and can take time.',
 'stop all scans': 'Request cancellation of active scans. Already collected results may be '
                   'incomplete; no source files are changed.',
 'close scan': 'Close this scan result in the viewer. This does not delete the scanned files.',
 '← back': 'Return from the File page to the scan view. This changes only the displayed page.',
 '◂': 'Navigate to the previously viewed folder. This does not change files.',
 '▸': 'Navigate forward in folder history. This does not change files.',
 '▴': 'Navigate to the parent folder. This does not change files.',
 '▶': 'Start scanning the selected target. This reads filesystem information without changing '
      'files.',
 'treemap': 'Show nested rectangles sized by the active disk-usage measure. This changes only the '
            'chart, not the files.',
 'pie': 'Show shares of disk usage as a pie chart. This changes only the chart.',
 'bar': 'Show disk usage as bars for easier comparison. This changes only the chart.',
 'reset settings': 'Reset TreeSize preferences to their defaults. This changes saved viewer '
                   'settings, not scanned files; there is no automatic undo of previous '
                   'preferences.',
 'import settings': 'Choose a settings file and load its TreeSize preferences. This changes viewer '
                    'configuration, not scanned file contents.',
 'export settings': 'Choose a destination and write TreeSize preferences to a settings file. This '
                    'does not change scanned files.',
 'select scan target': 'Choose a target and start a disk-usage scan. This reads file information '
                       'without changing files; a full drive may take a while.',
 'refresh scan': 'Scan again to replace the current results with current disk information. Files '
                 'are unchanged; a full drive may take a while.',
 'remove scan': 'Close this scan result in the viewer. This does not delete the scanned files.',
 'pause': 'Pause or resume collection of this scan. Already collected results remain visible; no '
          'source files change.',
 'stop': 'Request cancellation of the scan. The results may be incomplete; this does not change '
         'source files.',
 'schedule this scan': 'Open scheduling choices for this target. Daily and weekly choices create a '
                       'Windows scheduled task; removing the task stops future scheduled scans.',
 'empty recycle bin': 'Open the Recycle Bin emptying action, which asks before removing its '
                      'contents. Emptying removes recovery copies and cannot be undone here.',
 'remove obsolete software': "Open Windows' installed-programs interface. This button does not "
                             'uninstall anything by itself; uninstalling there changes the PC and '
                             'has no undo in TreeSize.',
 'configure windows system restore': 'Open Windows System Protection settings. This button only '
                                     'launches the settings; changes made there affect recovery '
                                     'and are not undone by TreeSize.',
 'map network drive': "Open Windows' network-drive mapping interface. Mapping associates a drive "
                      'letter with a share and may need credentials; this does not copy the remote '
                      'data.',
 'send by email': 'Prepare the scan report for an email workflow. Review paths and owner names '
                  'before sharing; the scan source is unchanged.',
 'create snapshot': 'Save current scan totals for later comparison. This writes scan history, not '
                    'a Windows restore point or file backup.',
 'compare with saved scan': 'Choose a saved scan and display differences from this scan. This '
                            'reads saved measurements without changing files.',
 'compare with snapshot': 'Choose a stored snapshot to compare totals over time. This does not '
                          'restore or modify files.',
 'compare with path': 'Scan another path for comparison with current results. This reads metadata '
                      'without changing either target.',
 'choose columns': 'Choose which detail columns are visible. Hidden columns and files are not '
                   'deleted.',
 'reset columns': 'Restore the default detail-column selection. This changes only the view.',
 'decimals': 'Choose how many decimal places sizes display. Rounding changes only formatted text, '
             'not the underlying byte values.',
 'select active view': 'Choose a scan view, such as Details, Chart or Extensions. This does not '
                       'change files.',
 'drive list': 'Show or hide the drive-capacity panel. This changes only the view.',
 'scan overview': 'Show or hide the scan-summary panel. This changes only the view.',
 'status bar': 'Show or hide scan progress and status information. This changes only the view.',
 'group scans': 'Group open scan results in the view. This changes only presentation, not source '
                'files.',
 'exclude': 'Open exclusion choices for this scan. Exclusions omit entries from analysis without '
            'deleting files.',
 'open treesize file search': 'Open file search over scan information. Searching reads metadata '
                              'and does not modify files.',
 'manage scheduled scans': 'Open Windows Task Scheduler to inspect scheduled scan tasks. Changes '
                           'made in that separate console affect automation and are not undone by '
                           'this view.',
 'find option (f6)': 'Type an option name to find a ribbon command. This searches the interface '
                     'without changing scan data.',
 'help contents': 'Open the TreeSize help information. This does not change files or Windows '
                  'settings.',
 'open': 'Open the selected item with its registered Windows application. Executable files can run '
         'programs; this is not a read-only preview.',
 'open containing folder': "Open File Explorer at the selected item's folder. This button does not "
                           'modify the item.',
 'copy name': 'Copy the selected file or folder name to the clipboard, replacing its previous '
              'contents. The item is unchanged.',
 'scan this folder': 'Read the selected folder to produce a disk-usage scan. No files are removed '
                     'or edited.',
 'exclude from scan': 'Exclude this name from the scan and refresh results. This does not delete '
                      'matching files.',
 'delete to recycle bin': 'Ask before sending the selected item to the Recycle Bin through '
                          'Windows. Restore it there while it is retained; this pane has no '
                          'automatic undo.',
 'delete permanently': 'Require confirmation before permanently deleting the selected item. '
                       'Protected paths may be refused; this bypasses recovery through the Recycle '
                       'Bin and has no undo here.',
 'move to': 'Choose a destination and confirm moving the selected item. Its path changes and '
            'applications may lose track of it; this pane has no automatic undo.',
 'secure erase': 'Open the confirmation flow for overwriting and removing the selected file. This '
                 'is irreversible here, and SSD remapping can prevent a guarantee that every '
                 'physical copy is overwritten.',
 'properties': 'Show properties retained in the scan for this item. These are scan-time values '
               'rather than a new live inspection.',
 '⇪': 'Open a scan target selection command. Scanning reads metadata without changing files.',
 '■': 'Request scan cancellation. Already collected results may be incomplete; files are '
      'unchanged.',
 '⟳': 'Scan again to replace the current results with current disk information. Files are '
      'unchanged; a full drive may take a while.',
 'chart': 'Visual comparison of the selected scan totals; chart area follows the active measure. '
          'This changes only the view; files are unchanged.',
 'details': 'Files and subfolders immediately under the selected folder, with independent Size and '
            'Allocated columns. This changes only the view; files are unchanged.',
 'extensions': 'Scan totals grouped by filename extension; an extension is a naming convention, '
               'not content verification. This changes only the view; files are unchanged.',
 'file groups': "Scan totals grouped into file-type categories using the app's extension rules. "
                'This changes only the view; files are unchanged.',
 'users': 'Records grouped by owner in TreeSize, or local accounts in Local Users & Groups; values '
          "come from the pane's data source. This changes only the view; files are unchanged.",
 'age of files': 'Scan totals grouped by file-age bands; age is not proof a file can be deleted. '
                 'This changes only the view; files are unchanged.',
 'top files': 'Largest files in the current scan, ranked by the displayed measure. This changes '
              'only the view; files are unchanged.',
 'current user': 'Scan the resolved Current user location to measure disk use. This reads file '
                 'metadata without cleaning or deleting its contents; access restrictions can make '
                 'a scan incomplete.',
 'desktop': 'Scan the resolved Desktop location to measure disk use. This reads file metadata '
            'without cleaning or deleting its contents; access restrictions can make a scan '
            'incomplete.',
 'documents': 'Scan the resolved Documents location to measure disk use. This reads file metadata '
              'without cleaning or deleting its contents; access restrictions can make a scan '
              'incomplete.',
 'downloads': 'Scan the resolved Downloads location to measure disk use. This reads file metadata '
              'without cleaning or deleting its contents; access restrictions can make a scan '
              'incomplete.',
 'user temp': 'Scan the resolved User Temp location to measure disk use. This reads file metadata '
              'without cleaning or deleting its contents; access restrictions can make a scan '
              'incomplete.',
 'windows temp': 'Scan the resolved Windows Temp location to measure disk use. This reads file '
                 'metadata without cleaning or deleting its contents; access restrictions can make '
                 'a scan incomplete.',
 'windows update cache': 'Scan the resolved Windows Update cache location to measure disk use. '
                         'This reads file metadata without cleaning or deleting its contents; '
                         'access restrictions can make a scan incomplete.',
 'installer cache': 'Scan the resolved Installer cache location to measure disk use. This reads '
                    'file metadata without cleaning or deleting its contents; access restrictions '
                    'can make a scan incomplete.',
 'package cache': 'Scan the resolved Package Cache location to measure disk use. This reads file '
                  'metadata without cleaning or deleting its contents; access restrictions can '
                  'make a scan incomplete.',
 'system log files': 'Scan the resolved System log files location to measure disk use. This reads '
                     'file metadata without cleaning or deleting its contents; access restrictions '
                     'can make a scan incomplete.',
 'windows logs': 'Scan the resolved Windows Logs location to measure disk use. This reads file '
                 'metadata without cleaning or deleting its contents; access restrictions can make '
                 'a scan incomplete.',
 'error reports': 'Scan the resolved Error reports location to measure disk use. This reads file '
                  'metadata without cleaning or deleting its contents; access restrictions can '
                  'make a scan incomplete.',
 'minidump': 'Scan the resolved Minidump location to measure disk use. This reads file metadata '
             'without cleaning or deleting its contents; access restrictions can make a scan '
             'incomplete.',
 'crash dumps': 'Scan the resolved Crash dumps location to measure disk use. This reads file '
                'metadata without cleaning or deleting its contents; access restrictions can make '
                'a scan incomplete.',
 'temp  caches': 'Open the menu of Temp & caches scan targets. Selecting a target scans it rather '
                 'than cleaning or deleting its contents.',
 'logs  dumps': 'Open the menu of Logs & dumps scan targets. Selecting a target scans it rather '
                'than cleaning or deleting its contents.'}
HEADERS = {'Size': 'Logical length of file contents, summed for folders, in the selected byte unit. Read by '
         'the MFT or directory-walk engine; independent of Allocated.',
 'Allocated': 'Disk space reserved for file data, summed for folders, in the selected byte unit. '
              'Read independently of Size; compression and sparse ranges can reduce it.',
 'Files': 'Number of files counted in the scanned subtree, not a byte measurement. Exclusions and '
          'unreadable locations can make totals incomplete.',
 'Folders': 'Number of subfolders counted by the scan. Excluded or unreadable locations are not a '
            'complete inventory.',
 '% of Parent': "Percentage of the parent folder's total represented by this row, calculated from "
                'scanned data.',
 '% of Total': 'Percentage of the scan total represented by this row, calculated from scanned '
               'data.',
 'Free': 'Available drive capacity reported by Windows, in the displayed byte unit. This is not '
         'derived by subtracting the scanned folders.',
 '% Free': 'Available capacity divided by total drive capacity, as a percentage reported from '
           'Windows volume information.',
 'Total Size': 'Total drive capacity reported by Windows, in the displayed byte unit; not a sum of '
               'scanned files.',
 'Owner': 'File owner identity from available scan/security metadata. A missing owner means it was '
          'not resolved, not that the file has none.',
 'Last Accessed': 'Last-access timestamp reported by the filesystem. Windows may defer or disable '
                  'updates, so this is not proof of recent use.',
 'Last Modified': 'Last-write timestamp from filesystem metadata. This does not identify who '
                  'changed the file.',
 'Created': 'Creation timestamp from filesystem metadata. Copying or restoring files can affect '
            'it; it does not identify the original author.',
 'Change': 'Difference between compared scan totals, in the displayed byte unit. Positive means '
           "growth; comparisons depend on the scans' coverage.",
 'Nodes': 'Count of file and directory entries retained in the scan data. This is a count rather '
          'than disk space.',
 'Age': 'Age band computed from the file timestamp recorded by the scan. It does not prove the '
        'file is no longer needed.',
 'Name': 'File, folder or group label from the scan. Expanding a directory reads its retained '
         'children; the name does not prove its contents.',
 'Path': 'Location retained in the scan. A live file may have moved or been removed since the '
         'scan.',
 'Extension': 'Filename suffix used to group scanned files, such as .log. This is not an '
              'inspection of file contents.',
 'File group': "Category assigned using the app's filename-extension rules. This is not a verified "
               'content type.',
 'When': 'Time of the saved scan history entry. It describes that measurement rather than current '
         'disk state.',
 'Target': 'Path or storage target recorded for the saved scan.',
 'Recoverable': 'Candidate duplicate file bytes shown by duplicate analysis. This is not '
                'guaranteed space reclaimed; hard links, allocation and chosen keepers affect the '
                'result.'}
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

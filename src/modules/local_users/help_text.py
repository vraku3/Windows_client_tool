"""Plain-language explanations for Local Users & Groups; no Qt or machine queries."""

INTRO = 'Inspect local Windows accounts, group membership and account findings. Nothing here changes accounts or passwords; Copy details only writes to the clipboard. Some account information may require administrator rights, and a missing last logon is not proof an account was never used.'
SOURCE = 'Windows local account and group information'
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
 'administrators': 'Show accounts belonging to the local Administrators group. Membership grants '
                   'broad control of the PC; this filter does not change membership.',
 'locked out': 'Show accounts reported locked out after sign-in failures. This does not unlock '
               'them.',
 'password never expires': 'Show accounts whose password-expiry flag is disabled. This does not '
                           'establish whether the password is strong.',
 'password not required': 'Show accounts whose flags permit a password not to be required. This '
                          'flag does not prove the account currently has a blank password.',
 'never logged on': 'Show accounts with no recorded last logon in the returned local account data. '
                    'Other sign-in evidence may exist; no account is modified.',
 'stale >90 days': 'Show accounts with a recorded last logon more than 90 days ago. This is a '
                   'review filter, not proof the account can be removed.'}
HEADERS = {'Days Idle': 'Days since the recorded local last logon, calculated by this pane. Missing logon '
              'data is kept distinct from a numeric age.',
 'Password Age (days)': 'Age of the password in days, derived from Windows account information. '
                        'This does not reveal password strength.',
 'Last Logon': 'Last logon time available in the returned local account information. It may not '
               'include every form of sign-in.',
 'Flags': 'Windows account flags such as disabled or password-not-required. A flag alone does not '
          'reveal the current password.',
 'Members': 'Number of members reported for the local group. Nested groups can grant access to '
            'additional people.',
 'Member list': 'Names of members returned by Windows for this local group; nested group contents '
                'are not necessarily expanded.',
 'Enabled': 'Whether Windows reports the local account as enabled. This does not establish whether '
            'it is currently signed in.',
 'Groups': 'Local group memberships returned by Windows. Group membership can grant permissions '
           'even when the account is not an administrator.',
 'Severity': 'Review priority assigned by this app to an account finding. This is not a Windows '
             'event severity or proof of compromise.'}
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

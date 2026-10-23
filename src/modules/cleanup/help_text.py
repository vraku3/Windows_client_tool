'Plain-language cleanup explanations; no Qt dependencies.'

CONTROLS = {'Close': 'Close this window; this does not undo completed operations.',
 'Add folder': 'Choose another folder to search for duplicate files. No files are changed.',
 'Add drive': 'Choose a fixed drive to search for duplicates. A whole-drive scan can take a long '
              'time; nothing is deleted by scanning.',
 'Remove root': 'Remove the highlighted search location from this scan; its files stay on disk.',
 'Filters and cache': 'Show scan limits, filename filters and hash-cache settings. These affect '
                      'the next scan, not existing files.',
 'Clear cache': 'Remove locally saved duplicate-file hash results. Files being compared are kept; '
                'hashes are calculated again on a later scan.',
 'Scan': 'Read the chosen folders to look for duplicate candidates. Scanning does not delete files '
         'or select every copy for removal.',
 'Stop': 'Request cancellation of the scan or action. Work already completed is kept; completed '
         'deletions cannot be undone here.',
 'Auto-select': 'Choose copies for removal using a keeper rule; review the selection before taking '
                'an action. This button does not remove files.',
 'Move to Recycle Bin': 'After confirmation, send checked copies to the Windows Recycle Bin, '
                        'keeping another copy. This credits zero freed bytes; restore from the '
                        'Recycle Bin while the files remain there.',
 'More actions': 'Show file actions, including moving copies and permanent deletion. Opening this '
                 'menu changes nothing.',
 'Move to folder...': 'Choose a destination for checked copies and review the confirmation. '
                      'Same-volume moves free zero bytes; the app has no automatic undo.',
 'Delete permanently...': 'After confirmation, permanently remove checked copies after rechecking '
                          'the keeper and file content. Removed data has no undo here; only '
                          'measured deleted bytes count as freed.',
 'Open file': 'Open the highlighted file in its associated application; that application may '
              'change the file.',
 'Open containing folder': 'Open File Explorer at the highlighted file. This action does not '
                           'delete it.',
 'Copy path': 'Copy the highlighted file path to the clipboard, replacing its current content.',
 'Exclude this folder': 'Add this location to Cleanup exclusions so future scans omit it. No files '
                        'are removed; the rule can be removed in Exclusions.',
 'Export CSV': 'Choose where to save the displayed results as a comma-separated text file. This '
               'writes a report, not changes to scanned files.',
 'Export HTML': 'Choose where to save an offline report for a browser. It includes file paths; '
                'scanned files are unchanged.',
 'Expand all': 'Expand all result groups to show their files; no files are changed.',
 'Collapse to browsers': 'Collapse cache entries to their browser groups; selection and files are '
                         'unchanged.',
 'Skip Windows, Program Files, ProgramData and AppData': 'Omit these system and application '
                                                         'folders from duplicate scanning. This '
                                                         'changes scan coverage only.',
 'Skip hidden and system files (zero-byte files always skipped)': 'Omit files carrying Windows '
                                                                  'hidden or system attributes. '
                                                                  'Empty files are always omitted; '
                                                                  'no files are changed.',
 'Use local hash cache': 'Reuse file hashes saved against file metadata to reduce repeated '
                         'reading. Removal still rechecks content and keeper metadata.',
 'Clear selection': 'Uncheck all duplicate copies; no files are removed.',
 'Keep newest': 'Keep newest: retain copies matching this rule and check other eligible copies for '
                'removal. Review the checked paths; applying the selection itself deletes nothing.',
 'Keep oldest': 'Keep oldest: retain copies matching this rule and check other eligible copies for '
                'removal. Review the checked paths; applying the selection itself deletes nothing.',
 'Keep shortest path': 'Keep shortest path: retain copies matching this rule and check other '
                       'eligible copies for removal. Review the checked paths; applying the '
                       'selection itself deletes nothing.',
 'Keep longest path': 'Keep longest path: retain copies matching this rule and check other '
                      'eligible copies for removal. Review the checked paths; applying the '
                      'selection itself deletes nothing.',
 'Keep first in preferred folder...': 'Keep first in preferred folder: retain copies matching this '
                                      'rule and check other eligible copies for removal. Review '
                                      'the checked paths; applying the selection itself deletes '
                                      'nothing.',
 'Keep all files in folder...': 'Keep all files in folder: retain copies matching this rule and '
                                'check other eligible copies for removal. Review the checked '
                                'paths; applying the selection itself deletes nothing.',
 'Quick Cleanup': 'Review measured cleanup candidates and choose a preset or category to clean. '
                  'Cleaning permanently removes selected data after the applicable confirmation; '
                  'caches may rebuild, but logs and history cannot be recreated. Protected '
                  'locations may need administrator rights.',
 'System Junk': 'Review temporary Windows files and caches found by the scanners. Cleaning deletes '
                'selected data; Windows can rebuild caches, but deleted diagnostics and temporary '
                'work have no undo here. System locations may require administrator rights.',
 'Browser Caches': 'Review browser cache entries and their research notes before selecting them. '
                   'Cleaning permanently removes selected data: downloaded cache content may be '
                   'fetched again, while deleted history and cookies are not restored here. Close '
                   'browsers to release locked files; restricted locations may need administrator '
                   'rights.',
 'App & Game Caches': "Review application and game cache candidates, including each item's purpose "
                      'and regeneration notes. Cleaning deletes selected data permanently; apps '
                      'may rebuild caches, with slower first launches or shader recompilation. '
                      'Restricted folders require administrator rights.',
 'Windows Update': 'Review downloaded update cache candidates; scanning does not install or '
                   'uninstall updates. Cleaning permanently removes selected cache data that '
                   'Windows may download again; deleted logs have no undo here. System changes '
                   'need administrator rights.',
 'Logs & Reports': 'Review diagnostic records and crash reports from the scanners. Cleaning '
                   'permanently removes selected diagnostic evidence; future events create new '
                   'records but do not recreate deleted history. Restricted locations require '
                   'administrator rights.',
 'Large Items': 'Review large files, driver packages and Windows Disk Cleanup options before '
                'acting. Removing personal files loses their contents; driver and system cleanup '
                'use their own confirmations and have no app undo. System tool operations require '
                'administrator rights.',
 'Dev Tools': 'Review development cache candidates and their individual explanations. Cleaning '
              'permanently removes selected data; tools may download dependencies or rebuild '
              'caches again, while deleted logs and recovery data cannot be recreated. Restricted '
              'folders require administrator rights.',
 'Discover': 'Find cache-named locations outside the enabled catalog for investigation. Nothing '
             'here deletes files or changes your PC; a cache-like name does not establish what a '
             'folder contains. Access depends on your current permissions.',
 'Duplicates': 'Search chosen folders for matching or similar files, then review copies to retain. '
               'Removal asks first; Recycle Bin moves free zero bytes and can be restored while '
               'retained, but permanent deletion has no app undo. Scanning needs only access to '
               'the chosen folders.',
 'History': 'Review locally recorded cleanup outcomes and measured deleted bytes. Reading history '
            'changes no system settings; exports write report files and do not recover deleted '
            'content. Administrator rights are not needed to read your local history.',
 '×': 'Dismiss this message; completed operations and current settings remain unchanged.'}

HEADERS = {'Path': 'Full path recorded for the cleanup item; this identifies what the operation attempted to '
         'process.',
 'Size': 'File or category size measured during scanning, displayed in byte units. Scanned size is '
         'an estimate of candidates, not a promise of freed space.',
 'What happened': 'Outcome recorded by the cleanup engine for this path, including skipped or '
                  'refused items.',
 'Reason': 'Reason the cleanup engine recorded for keeping, refusing or failing to delete this '
           'item.',
 'Category': 'Cleanup category assigned by the scanner catalog.',
 'Share': 'Percentage of the scanned total attributed to this category; this is not measured freed '
          'space.',
 'Location': 'Location found by the scan; inspect the full path before selecting it for removal.',
 'Safety': 'Review rating from scanner, catalog and research, using the most cautious result. A '
           'rating is not a backup or a guarantee against losing data.',
 'When': 'Timestamp recorded in local cleanup history.',
 'Freed': 'Measured bytes deleted by the cleanup engine, formatted in byte units. Recycling and '
          'same-volume moves count as zero.',
 'Reclaimable': 'Candidate size from the scan, in byte units; locked, excluded or protected files '
                'can reduce the actual deletion total.',
 'Set / file path': 'Duplicate candidate group or full file path from the filesystem scan.',
 'Folder': 'Parent directory read from the scanned path.',
 'Modified': 'Last-write timestamp read from filesystem metadata at scan time.',
 'Created': 'Creation timestamp reported by Windows filesystem metadata; it does not prove who '
            'created the content.',
 'Files': 'Number of distinct files in this duplicate candidate group, after physical file aliases '
          'are collapsed.',
 'Potential reclaimable': 'Estimated bytes recoverable while retaining a copy. Recycle Bin and '
                          'same-volume moves credit zero freed bytes.',
 'Hash': 'Content hash calculated by the duplicate engine; candidate methods may not have a '
         'full-file hash.',
 'Method / confidence': 'Comparison used by the duplicate engine. Similar names, sizes or images '
                        'do not prove identical content.',
 'Notes': 'Scan observations and review warnings from the duplicate engine.'}

FIELDS = {'_method': 'Choose exact-content, name, size or image-similarity matching. Candidate matches '
            'still need review; non-exact candidates require fresh content checks before removal.',
 '_threshold': 'Image similarity tolerance used by the scan. A larger tolerance can include images '
               'with more visual differences; it does not prove interchangeability.',
 '_minimum': 'Minimum file size in megabytes for the next duplicate scan; smaller files are '
             'omitted.',
 '_maximum': 'Maximum file size in megabytes for the next duplicate scan; Unlimited removes the '
             'upper limit.',
 '_include': 'Filename patterns to include in duplicate scanning. This filters the next scan '
             'without changing files.',
 '_exclude': 'Filename patterns to omit from duplicate scanning. This filters the next scan '
             'without changing files.',
 '_excluded_folders': 'Folders to omit from this duplicate scan. Their files are left untouched.',
 '_search': 'Filter the displayed results by text without changing files or the scan roots.'}

PANES = {'Quick Cleanup': 'Review measured cleanup candidates and choose a preset or category to clean. '
                  'Cleaning permanently removes selected data after the applicable confirmation; '
                  'caches may rebuild, but logs and history cannot be recreated. Protected '
                  'locations may need administrator rights.',
 'System Junk': 'Review temporary Windows files and caches found by the scanners. Cleaning deletes '
                'selected data; Windows can rebuild caches, but deleted diagnostics and temporary '
                'work have no undo here. System locations may require administrator rights.',
 'Browser Caches': 'Review browser cache entries and their research notes before selecting them. '
                   'Cleaning permanently removes selected data: downloaded cache content may be '
                   'fetched again, while deleted history and cookies are not restored here. Close '
                   'browsers to release locked files; restricted locations may need administrator '
                   'rights.',
 'App & Game Caches': "Review application and game cache candidates, including each item's purpose "
                      'and regeneration notes. Cleaning deletes selected data permanently; apps '
                      'may rebuild caches, with slower first launches or shader recompilation. '
                      'Restricted folders require administrator rights.',
 'Windows Update': 'Review downloaded update cache candidates; scanning does not install or '
                   'uninstall updates. Cleaning permanently removes selected cache data that '
                   'Windows may download again; deleted logs have no undo here. System changes '
                   'need administrator rights.',
 'Logs & Reports': 'Review diagnostic records and crash reports from the scanners. Cleaning '
                   'permanently removes selected diagnostic evidence; future events create new '
                   'records but do not recreate deleted history. Restricted locations require '
                   'administrator rights.',
 'Large Items': 'Review large files, driver packages and Windows Disk Cleanup options before '
                'acting. Removing personal files loses their contents; driver and system cleanup '
                'use their own confirmations and have no app undo. System tool operations require '
                'administrator rights.',
 'Dev Tools': 'Review development cache candidates and their individual explanations. Cleaning '
              'permanently removes selected data; tools may download dependencies or rebuild '
              'caches again, while deleted logs and recovery data cannot be recreated. Restricted '
              'folders require administrator rights.',
 'Discover': 'Find cache-named locations outside the enabled catalog for investigation. Nothing '
             'here deletes files or changes your PC; a cache-like name does not establish what a '
             'folder contains. Access depends on your current permissions.',
 'Duplicates': 'Search chosen folders for matching or similar files, then review copies to retain. '
               'Removal asks first; Recycle Bin moves free zero bytes and can be restored while '
               'retained, but permanent deletion has no app undo. Scanning needs only access to '
               'the chosen folders.',
 'History': 'Review locally recorded cleanup outcomes and measured deleted bytes. Reading history '
            'changes no system settings; exports write report files and do not recover deleted '
            'content. Administrator rights are not needed to read your local history.'}

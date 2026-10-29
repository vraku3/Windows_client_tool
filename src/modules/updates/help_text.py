"Update explanations grounded in the app's actual update paths."

CONTROLS = {'Refresh': 'Scan the update source again. This downloads update metadata, not installers, and can '
            'take a minute; no update is installed by Refresh.',
 'Update Selected': 'Start winget upgrades for the highlighted packages immediately, without an '
                    'extra app confirmation. Installers can change apps or need elevation; there '
                    'is no app rollback.',
 'Update All': 'Start winget upgrades for every listed package, including rows hidden by the text '
               'filter, without an extra app confirmation. Installed versions are checked when '
               'verification is enabled; no app rollback is provided.',
 'Package Details': 'Read package information using winget show. This does not install or change '
                    'the selected application.',
 'Add to Blocklist': 'Save a matching package or title pattern to exclude it from future app scans '
                     'and installs. Remove the pattern in Settings to include it again; this does '
                     'not uninstall anything.',
 'Export CSV': 'Write the app offers left by the current filters to a CSV file, including '
               'installed and available versions. This reads the scan results; it does not '
               'change or install anything.',
 'Export visible offers CSV...': 'Write the Windows Update offers left by the current filters to '
                                 'a CSV file, including category, severity, download bounds, '
                                 'downloaded state, driver flag and published security '
                                 'identifiers. This reads the last scan; it does not contact '
                                 'Windows Update or install anything.',
 'Download Selected': 'Download the selected Windows updates into the Windows Update cache without '
                      'installing them. No restart is requested; Install Selected can then run '
                      'from the downloaded bytes. A download alone changes no installed software.',
 'Download Visible': 'Download only the Windows updates the current filters leave visible, '
                     'without installing them. No restart is requested; Install Visible can then '
                     'run from the downloaded bytes.',
 'Driver Manager': 'Open Driver Manager, where a driver is approved and installed for a specific '
                   'device. Windows Update cannot reliably match one driver package to one device '
                   'from this list.',
 'Install Selected': 'Start downloading and installing the selected Windows updates immediately '
                     'without another app confirmation. Administrator rights are required; a '
                     'restart may be needed and there is no rollback here. Held AMD display '
                     'drivers are never installed.',
 'Hide Selected': "Change the selected Windows Update offer's hidden flag after confirmation. "
                  'With hidden offers selected the same button reads Unhide Selected. It does '
                  'not uninstall anything; use Show hidden updates to inspect hidden offers.',
 'Restart now': 'Ask Windows to restart in 60 seconds so a pending update can finish, after '
                'confirmation. This closes every app without saving it; run shutdown /a to '
                'cancel. It does not install another update by itself.',
 'History': 'Read Windows Update installation history from Windows; this does not install or '
            'remove updates.',
 'Open Settings': 'Open Windows Update in Windows Settings. This button itself does not install '
                  'anything.',
 'Show hidden updates': 'Include hidden offers in the Windows Update scan. Changing this view does '
                        'not unhide or install an update.',
 'Verify Store Updates': 'Record installed Store app versions, ask Windows to trigger Store '
                         'updates, then re-read the versions for up to 90 seconds. A version that '
                         'moved is reported; no change in that window is not proof the Store is '
                         'finished, and there is no app rollback.',
 'Open Store': 'Open Microsoft Store so you can manage apps there. Opening the Store does not '
               'itself confirm an update in this app.',
 'Verify versions after updating and report confirmed/unchanged': 'Save this setting immediately. '
                                                                  'Read installed versions after '
                                                                  'winget upgrades; disabling it '
                                                                  'reports unconfirmed outcomes '
                                                                  'rather than proving an update '
                                                                  'succeeded.',
 'Create a System Restore point before installing Windows updates': 'Save this setting '
                                                                    'immediately. Attempt a '
                                                                    'Windows System Restore '
                                                                    'checkpoint before '
                                                                    'installation; this is not a '
                                                                    'personal-file backup and '
                                                                    'depends on System Protection '
                                                                    'being available.',
 'Include driver updates from Windows Update': 'Save this setting immediately to include eligible '
                                               'driver offers in scans. This does not install them '
                                               'by itself and does not let an automated run install '
                                               'them; approve a driver per device in Driver Manager '
                                               'or with Install Selected. Held AMD display drivers '
                                               'remain excluded from installation.',
 'Let Run All and unattended runs install driver updates (not recommended)': 'Save this setting '
                                               'immediately. Off by default: driver offers are '
                                               'skipped by automated runs and reviewed per device '
                                               'in Driver Manager instead. Turning it on lets an '
                                               'unattended run install driver packages with no '
                                               'per-device approval.',
 'Save Blocklist': 'Save one exclusion pattern per line to the app configuration. Matching future '
                   'update offers are omitted; remove a pattern and save again to reverse the '
                   'exclusion.',
 'Windows Update': 'Include the Windows Update stage in the next run or saved schedule. Running it '
                   'installs eligible offers with administrator rights; held AMD display drivers '
                   'stay excluded and there is no app rollback.',
 'winget': 'Include application upgrades in the next run or saved schedule. Running the stage '
           'changes eligible installed apps without per-package prompts; there is no app rollback.',
 'Microsoft Store': 'Include the Store update trigger in the next run or saved schedule. Running '
                    'it asks Windows to update Store apps; completion does not prove every app '
                    'changed and there is no app rollback.',
 'Cleanup (safe)': "Include the app's automatic cleanup stage. It permanently removes eligible "
                   'cache data: apps can rebuild caches, while any deleted diagnostic history is '
                   'lost; locked or protected files may remain. Freed bytes are measured.',
 'DISM WinSxS': 'Include Windows component-store cleanup. Running it removes superseded system '
                'components with administrator rights; this is not Reset Base and there is no app '
                'undo.',
 'DISM WinSxS cleanup': 'Include Windows component-store cleanup. Running it removes superseded '
                        'system components with administrator rights; this is not Reset Base and '
                        'there is no app undo.',
 'Create / Update Task': 'Save the selected stages and register or replace the elevated recurring '
                         'maintenance task now. Future runs can install updates and permanently '
                         'delete cleanup data without per-run prompts. Remove Task stops future '
                         'scheduled runs.',
 'Remove Task': 'Remove the recurring maintenance task from Task Scheduler now. This stops future '
                'scheduled launches, not a run already in progress; Create / Update Task can '
                'register it again.',
 'Run Now': 'Ask Task Scheduler to start the saved maintenance task immediately, using its saved '
            'stages. It can install updates and delete cleanup data without another app '
            'confirmation; completed changes have no app rollback.',
 'Refresh Task Health': "Read the task's registered executable and last-run result from Task "
                        'Scheduler. This does not start or change the task.',
 'Update task to this copy': 'Replace the maintenance task registration so future runs use this '
                             'app copy and the selected schedule. It needs administrator rights '
                             'and does not undo past maintenance.',
 'Save': 'Register or replace the legacy winget auto-update task using this time and frequency. It '
         'silently upgrades applications on future runs; Remove deletes the schedule, not '
         'installed updates.',
 'Remove': 'Delete the legacy winget auto-update scheduled task now. Already installed updates '
           'remain; Save can recreate the schedule.',
 'Run All': 'Start the checked maintenance stages in sequence immediately, without another '
            'confirmation. Stages can install software or permanently remove caches and superseded '
            'components with administrator rights; there is no combined rollback.',
 'Stop': 'Request stopping before the next package, update or stage. A download or installer '
         'already in flight is allowed to finish; completed changes remain and there is no '
         'rollback here.',
 'Generate Maintenance Report': 'Write an HTML maintenance report from the run results and open it '
                                'in your browser. This does not run maintenance again.',
 'App Updates': 'Review application upgrades offered by winget, the Windows package manager. '
                'Update buttons start installers without another app confirmation and can require '
                'administrator rights; this app has no rollback. Verification reads installed '
                'versions rather than treating a successful command as proof.',
 'Windows Updates': 'Review Windows Update offers, including download bounds and restart '
                    'requirements. Installation asks first and needs administrator rights; Windows '
                    'determines whether an update can later be uninstalled. Held AMD display '
                    'drivers are never installed.',
 'Settings': 'Configure update exclusions, verification and recurring maintenance. General toggles '
             'save immediately; schedule buttons change Task Scheduler with administrator rights '
             'and can authorize future unattended installs and deletion. Removing a task stops '
             'future launches, not completed changes.',
 '×': 'Dismiss this message; completed operations and current settings remain unchanged.'}

HEADERS = {'Name': 'Application name returned by the shared winget update scan.',
 'ID': 'Exact package identifier returned by winget; used to select the installer.',
 'Id': 'Package identifier from the msstore subset of the shared winget scan.',
 'Installed version': 'Installed version reported by winget; Unknown means winget could not '
                      'identify a version.',
 'Installed': 'Installed version reported by winget for this Store package.',
 'Available': 'Version offered by the update source; availability alone does not prove '
              'installation will succeed.',
 'Source': 'winget repository that supplied this offer, such as winget or msstore.',
 'Result': 'Outcome returned by the update operation and, when enabled, its verification. '
           'Unconfirmed does not mean updated.',
 'KB': 'Microsoft Knowledge Base identifier returned by the Windows Update Agent, when present.',
 'Title': 'Update title returned by the Windows Update Agent.',
 'Classification': 'Update category returned by the Windows Update Agent, such as security or '
                   'drivers.',
 'Severity': 'Microsoft security severity from IUpdate.MsrcSeverity. A dash means the field could '
             'not be read or is absent.',
 'Download': 'Windows Update Agent download-size bounds, in byte units. The maximum is an upper '
             'bound, not the actual network transfer.',
 'Restart': 'Restart behavior reported by IUpdate.InstallationBehavior, not a prediction that a '
            'restart has already occurred.',
 'Released': 'Release timestamp reported by the Windows Update Agent.'}

FIELDS = {'_filter_edit': 'Filter displayed winget offers by name, identifier or source. This does not '
                 'change the blocklist or limit Update All.',
 '_sched_days': 'Days between unattended maintenance launches. Create / Update Task saves this '
                'interval; changing it alone does not change the task.',
 '_sched_time': 'Local time for unattended maintenance. Create / Update Task saves this time; '
                'changing it alone does not launch maintenance.',
 '_legacy_time': 'Local launch time for the legacy winget task. Save registers this schedule.',
 '_legacy_freq': 'Daily or weekly frequency for the legacy winget task. Save registers the '
                 'schedule; this control alone changes no apps.',
 '_block_edit': 'One exclusion pattern per line; * matches any text and plain text matches within '
                'names or identifiers. Save Blocklist applies these rules to future scans and '
                'installs.',
 '_task_health_lbl': 'Last task outcome read from Task Scheduler. A registered task is not proof '
                     'that its last maintenance run succeeded.',
 '_status_lbl': 'Current scan or operation state reported by this tab; a finished scan does not '
                'mean updates were installed.'}

PANES = {'App Updates': 'Review application upgrades offered by winget, the Windows package manager. '
                'Update buttons start installers without another app confirmation and can require '
                'administrator rights; this app has no rollback. Verification reads installed '
                'versions rather than treating a successful command as proof.',
 'Windows Updates': 'Review Windows Update offers, including download bounds and restart '
                    'requirements. Installation starts immediately without another app '
                    'confirmation and needs administrator rights; Windows determines whether an '
                    'update can later be uninstalled. Held AMD display drivers are never '
                    'installed.',
 'Microsoft Store': 'Review Store offers from the same winget scan used by App Updates. Verify '
                    'Store Updates asks Windows to trigger Store updates with administrator '
                    'rights; it does not prove each app finished updating and provides no '
                    'rollback.',
 'Run All': 'Run selected update and cleanup stages in sequence with administrator rights. The run '
            'asks first, but individual stages can install apps or permanently remove cache and '
            'superseded component data; caches may rebuild and deleted history cannot be '
            'recreated. There is no combined undo.',
 'Settings': 'Configure update exclusions, verification and recurring maintenance. General toggles '
             'save immediately; schedule buttons change Task Scheduler with administrator rights '
             'and can authorize future unattended installs and deletion. Removing a task stops '
             'future launches, not completed changes.'}

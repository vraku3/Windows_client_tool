"""Plain-language explanations for this pane; no Qt dependencies."""
INTRO = ('Scheduled tasks run programs in response to a time or event, sometimes as the powerful SYSTEM '
 'account. Enable/Disable can be reversed here; Run executes the task, and Delete has no built-in undo, '
 'so export XML first. Reading and changing tasks is limited by your account permissions; protected '
 'tasks need administrator rights.')

CONTROLS = {'Enable': "Ask before enabling the selected task's triggers. Disable reverses this setting; protected "
           'tasks need administrator rights. This does not run the task immediately.',
 'Disable': 'Ask before disabling future automatic runs of this task. Enable reverses the switch; it '
            'does not stop a task that is already running. Protected tasks need administrator rights.',
 'Run Now': 'Ask before launching this task now under its configured account, possibly SYSTEM. Its '
            'program can change files or settings, and this app cannot undo its effects; account '
            'permissions apply.',
 'Delete': 'Ask before deleting the task registration. There is no undo or import button here; export '
           'its XML first for possible manual re-creation in Task Scheduler. Protected tasks need '
           'administrator rights.',
 'Export XML': "Choose where to save the selected task's XML definition. This writes a file without "
               'changing the task; credentials are not backed up, and restoring requires Task Scheduler '
               'or another tool.',
 'Task Scheduler': 'Open the Windows Task Scheduler console. Opening it changes nothing; actions taken '
                   'there can change tasks and may need administrator rights.',
 'All': 'Show every task in the selected folder scope. This changes only the view.',
 'Failed': 'Show tasks whose last result code indicates an error or termination. This is the last '
           'recorded outcome, not proof every run fails; no task changes.',
 'Never ran': 'Show tasks with the Task Scheduler result code for not having run yet. The filter does '
              'not launch them.',
 'Running': 'Show tasks currently reported as running. Viewing them does not stop or restart them.',
 'Disabled': 'Show tasks with disabled triggers. Viewing them does not enable them.',
 'Third-party': 'Show tasks outside the Microsoft task-folder branch. Folder location is a heuristic, '
                'not a verified publisher or security verdict.',
 'SYSTEM': "Show tasks whose XML account resolves to SYSTEM, Windows' highly privileged local account. "
           'Filtering does not change their accounts.',
 'Logon/boot': 'Show tasks with enabled sign-in or system-start triggers in their XML. This only '
               'filters the view.',
 'Elevated': 'Show tasks requesting the highest available privileges in their XML. This is a task '
             'setting, not evidence that a run is currently elevated.',
 'Missing exe': 'Show tasks whose first program could not be found on disk or the search path. This '
                'does not remove the task; examine its commands first.'}

HEADERS = {'Name': 'Task name returned by the Windows Task Scheduler service.',
 'Folder': 'Task Scheduler folder containing this task; this is not a filesystem folder.',
 'State': 'Current task state reported by Task Scheduler, such as Ready, Running or Disabled.',
 'Last Run': 'Last run time reported by Task Scheduler; missing or sentinel dates can mean it has never '
             'run.',
 'Last Result': 'LastTaskResult from Task Scheduler, translated where known. It can be a task status '
                "code or the program's exit code; it does not always indicate an error.",
 'Next Run': 'Next scheduled time reported by Task Scheduler. Event-driven tasks may have no '
             'predictable next time.',
 'Runs as': 'Account or group from the task XML. SYSTEM has extensive local privileges; an unresolved '
            'security identifier is shown unchanged.',
 'Triggers': 'Enabled conditions that start the task, parsed from its XML definition.',
 'Author': 'Author string stored in the task definition. This is self-declared metadata, not a verified '
           'signature.',
 'Task Folders': "Task Scheduler's folder hierarchy. Select a folder to read its tasks; All tasks "
                 'includes the full tree.'}

TABS = {'Details': "Read-only explanation of the task's commands, account, triggers and last result. It uses "
            'the task definition and Windows-reported data; no task settings change.',
 'XML definition': 'Read-only task definition returned by Task Scheduler. XML describes actions and '
                   'permissions; viewing it does not execute or edit the task.'}


TASKS = {'\\Microsoft\\Windows\\Defrag\\ScheduledDefrag': 'Windows scheduled drive optimization. Disabling this '
                                                  'task prevents its scheduled optimization runs; it '
                                                  'does not disable all storage maintenance.',
 '\\Microsoft\\Windows\\SystemRestore\\SR': 'Windows System Restore task. Disabling it can prevent '
                                            'restore-point creation triggered through this task; it '
                                            'does not delete existing points.',
 '\\Microsoft\\Windows\\Servicing\\StartComponentCleanup': 'Windows component-store maintenance removes '
                                                           'superseded components. Disabling this task '
                                                           'prevents its scheduled cleanup; manual '
                                                           'servicing is separate.',
 '\\Microsoft\\Windows\\Windows Defender\\Windows Defender Scheduled Scan': 'Microsoft Defender '
                                                                            'scheduled malware scan. '
                                                                            'Disabling it prevents '
                                                                            'scans launched by this '
                                                                            'task; it does not by '
                                                                            'itself describe real-time '
                                                                            'protection status.',
 '\\Microsoft\\Windows\\Time Synchronization\\SynchronizeTime': 'Windows time-synchronization task. '
                                                                'Disabling it prevents synchronization '
                                                                'initiated by this task; other clock '
                                                                'synchronization mechanisms may still '
                                                                'operate.',
 '\\Microsoft\\Windows\\Chkdsk\\ProactiveScan': 'Windows proactive filesystem scan task. Disabling it '
                                                'prevents scans initiated by this task; it does not '
                                                'establish whether a disk is healthy.',
 '\\Microsoft\\Windows\\Registry\\RegIdleBackup': 'Windows registry maintenance backup task. What is '
                                                  'actually backed up depends on the Windows version '
                                                  'and configuration; the task name alone does not '
                                                  'guarantee a usable backup.'}


def task_help(path, description=""):
    """Full paths avoid assigning a Microsoft purpose to a vendor namesake."""
    known = next((text for key, text in TASKS.items() if key.casefold() == str(path).casefold()), "")
    return str(path) + "\n" + (known or ("Task description: " + description if description else
        "No curated purpose is available. Read this task's own commands, account and triggers before running or disabling it."))

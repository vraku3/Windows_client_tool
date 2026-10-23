"""Plain-language explanations for this pane; no Qt dependencies."""
INTRO = ('Windows boot traces show startup durations and the apps, drivers or services Windows flagged as slow. '
 'Refresh is read-only; the timeout button changes the boot menu after confirmation, with no automatic '
 'restore. Detailed traces and timeout changes may need administrator rights; System-log estimates are '
 'shown when detailed traces cannot be read.')

CONTROLS = {'Refresh Analysis': 'Read recent boot events and startup configuration again. No settings change; '
                     'reading detailed boot traces may require administrator rights and can take a '
                     'minute.',
 'Reduce Boot Timeout to 3s': 'Ask before changing the Windows boot-menu timeout to three seconds with '
                              'bcdedit. Needs administrator rights; this changes menu waiting time, not '
                              'driver or program startup speed, and has no undo button here.',
 'Fast Startup Info': 'Show information about Fast Startup and where to change it in Windows. This '
                      'button does not change the setting; changing it in Control Panel requires '
                      'administrator rights.',
 'Copy Boot Summary': 'Replace the clipboard with the current boot trend and top recorded slow items. '
                      'This does not change boot configuration.'}

HEADERS = {'Boot logged': 'Timestamp recorded by the Windows boot event; not the moment the power button was '
                'pressed.',
 'Type': 'Boot type decoded from Diagnostics-Performance event 100, such as a full boot or a Fast '
         'Startup resume.',
 'Boot time': 'Total boot duration from Windows Diagnostics-Performance event 100. Displayed in seconds '
              '(s) or minutes; Windows records the underlying value in milliseconds.',
 'Main path': 'Time for the main startup phase in event 100, displayed as a duration. This is a Windows '
              'trace measurement, not firmware/BIOS time.',
 'Post-boot': 'Post-startup settling duration recorded in event 100, displayed as a duration. '
              'Background apps can contribute after the desktop appears.',
 'Startup apps': 'Number of startup applications recorded in Windows event 100; not a complete '
                 'inventory of every automatic launch location.',
 'Degraded': 'Whether Windows marked this boot as slower than its baseline; the extra duration comes '
             'from event 100, not an independent benchmark.',
 'Kind': 'App, driver or service classification derived from the Diagnostics-Performance slow-event ID.',
 'Name': 'Name recorded by Windows for the startup component flagged as slow.',
 'Publisher': 'Company string recorded in the Windows slow-start event; not an independent signature '
              'check.',
 'Events': 'Number of matching slow-start events in the loaded trace window, not the total number of '
           'program launches.',
 'Worst': 'Largest recorded duration for this component among the loaded slow-start events, displayed '
          'in seconds or minutes.',
 'Average': 'Mean recorded duration for this component across loaded slow-start events, displayed in '
            'seconds or minutes. These events cover flagged starts only.',
 'Last seen': 'Date of the newest matching Windows slow-start event in this scan.',
 'Path': 'Executable or component path carried by the Windows slow-start event; it is not executed by '
         'this table.',
 'Time to ready (approx.)': 'Approximate elapsed seconds derived from System-log boot events when '
                            'detailed traces were unavailable. It is not directly comparable to event '
                            "100's full boot duration.",
 'Prior shutdown': 'Whether the fallback System-log evidence reports a clean or unexpected previous '
                   'shutdown. Unknown means the records did not establish it.'}

TABS = {}


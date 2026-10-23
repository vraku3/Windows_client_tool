"""Plain-language explanations for this pane; no Qt dependencies."""
INTRO = ('Power settings control sleep and hibernation; boot settings control how Windows starts. Changes here '
 'apply immediately without an extra confirmation, and Advanced Startup restarts immediately; '
 'administrator rights are required. Opposite switches reverse some settings, but the app does not '
 'automatically restore previous values.')

CONTROLS = {'Set Active Plan': 'Immediately activate the selected power plan with powercfg, without confirmation. '
                    'Needs administrator rights in this pane; choose another plan to change it again.',
 'Fast Startup': "Immediately write Windows' Fast Startup registry setting without confirmation. It "
                 'reuses saved kernel state after shutdown when hibernation is available; toggle again '
                 'to reverse the setting. Needs administrator rights.',
 'Hibernate': 'Immediately enable or disable hibernation with powercfg without confirmation. Disabling '
              'removes the hibernation file and prevents Fast Startup; toggle again to change '
              'availability. Needs administrator rights.',
 'Apply': 'Immediately set BOTH plugged-in and battery sleep timeouts to the selected minutes using '
          'powercfg. Zero means never sleep automatically; no confirmation. Needs administrator rights; '
          'apply another value to change it again.',
 'Enable Safe Mode': 'Immediately set the current boot entry to minimal Safe Mode, without '
                     'confirmation. This persists across restarts until Disable Safe Mode removes it; '
                     'needs administrator rights and may limit network/sign-in options. A BCD export is '
                     'attempted first; the app has no import button.',
 'Disable Safe Mode': "Immediately remove the current boot entry's Safe Mode flag, without "
                      'confirmation. The next boot uses its normal configuration; needs administrator '
                      'rights. A BCD export is attempted first; the app has no import button.',
 'Advanced Startup': 'Immediately force a restart into Windows recovery options using shutdown /r /o /f '
                     '/t 0. There is no confirmation or countdown; open apps can close and unsaved work '
                     'can be lost. This cannot be undone once shutdown proceeds.',
 'Set Timeout': 'Immediately set the boot-menu wait in seconds with bcdedit, without confirmation. Zero '
                'removes the selection delay; needs administrator rights. A BCD export is attempted '
                'first, but restoring it is manual; set another value here to change the timeout again.'}

HEADERS = {'Property': 'Boot configuration property read from bcdedit /enum. Section rows separate boot entries; '
             'the table itself is read-only.',
 'Value': 'Value reported by bcdedit for the property on the left. Identifiers in braces refer to '
          'boot-configuration entries, not files.'}

TABS = {'Power': 'Change the active power plan, hibernation, Fast Startup and sleep timeouts. These controls '
          'apply without confirmation and require administrator rights; use the controls again to '
          'change values, as previous values are not automatically restored.',
 'Boot': 'Read boot entries and change timeout or Safe Mode. Changes require administrator rights and '
         'apply without confirmation; Advanced Startup forces an immediate restart, and BCD backups '
         'require manual restoration.'}


'Repair explanations describe actual commands rather than optimistic labels.'

ACTIONS = {'cleanmgr': 'Open Windows Disk Cleanup for C:. Windows presents its own deletion choices; removed '
             'cache data can rebuild but deleted logs and files are not restored here.',
 'perf_reset': 'Run lodctr /r to rebuild performance-counter registrations from the system backup. '
               'This changes counter settings and needs elevation; there is no app undo.',
 'icon_cache': 'Delete icon-cache files and restart Explorer. Windows rebuilds icons; removed '
               'cache files have no undo and Explorer windows close.',
 'thumb_cache': 'Delete thumbnail-cache files and restart Explorer. Windows regenerates previews '
                'when folders are opened; removed cache files have no undo and Explorer windows '
                'close.',
 'explorer': 'Stop and relaunch Windows Explorer. Open folder windows close and file copies can be '
             'interrupted; the taskbar returns when Explorer starts.',
 'prefetch_clear': 'Delete .pf launch-trace files from Windows Prefetch. Windows creates new '
                   'traces as programs run; old traces are lost and initial launches may be '
                   'slower.',
 'recent_clear': 'Delete .lnk shortcuts in Windows Recent and attempt to remove thumbnail caches. '
                 'Original documents remain; deleted recent shortcuts cannot be restored here and '
                 'previews regenerate as folders are opened.',
 'flush_dns': 'Clear the DNS resolver cache, which stores recent name-to-address answers. Later '
              'connections query DNS again; there is no app restore for discarded cache entries.',
 'winsock': 'Reset the Winsock catalog used for network connections. A reboot is required and '
            'network software may need repair; there is no app undo.',
 'tcpip': 'Reset the Windows TCP/IP networking configuration. Connections can drop and a reboot is '
          'required; custom network configuration may need manual restoration.',
 'ip_renew': 'Release and renew DHCP network addressing using ipconfig. This can disconnect '
             'network sessions immediately and depends on a reachable DHCP server; there is no app '
             'undo.',
 'network_reset': 'Run both Winsock and TCP/IP resets. Connections can drop and a reboot is '
                  'required; custom network settings may need manual restoration.',
 'wu_reset': 'Stop update services, remove update caches and restart services. Windows can '
             'download cache data again; deleted local update records and cache contents have no '
             'undo here.',
 'wu_dlls': 'Run registration commands for the Windows Update DLL list. This changes COM '
            'registrations with administrator rights and provides no app undo.',
 'wu_scan': 'Ask Windows Update to search and print available offers. This does not install '
            'updates or change the driver hold policy and may take a minute.',
 'print_queue': 'Stop the Print Spooler, delete queued job files and restart it. Cancelled '
                'documents must be submitted again; deleted queue data has no undo.',
 'print_spooler_restart': 'Stop and restart the Print Spooler service. Printing is interrupted '
                          'while the service restarts; this does not deliberately clear queued job '
                          'files.',
 'clear_event_logs': 'Clear the System, Application and Security event logs. New events accumulate '
                     'afterwards, but deleted diagnostic and audit records cannot be recreated or '
                     'undone here.',
 'compact_winsxs': 'Run DISM /StartComponentCleanup to remove superseded Windows component data. '
                   'This can take 10-30 minutes, requires elevation and has no app undo; it does '
                   'not run Reset Base.',
 'wu_deep_clean': 'Run DISM /StartComponentCleanup /SuppressDefaultActions on the Windows '
                  'component store. Superseded component data is removed with no app undo; it can '
                  'take 10-20 minutes and does not run Reset Base.',
 'clear_clipboard': 'Replace the current Windows clipboard with empty content using clip. This '
                    'action provides no undo and does not promise to clear clipboard history or '
                    'synced copies.',
 'reset_search': 'Stop and restart the Windows Search service. Search may be briefly unavailable; '
                 'this implementation does not delete or rebuild the search database.',
 'clear_font_cache': 'Stop and restart the FontCache service. Applications may briefly redraw '
                     'text; this implementation does not delete font files or cache files.',
 'resize_hibernation': 'Ask powercfg to resize the hibernation file to 50% of RAM. Requires '
                       'elevation and an existing hibernation file; there is no app restore of the '
                       'previous size.'}

CONTROLS = {'View History': "Open this app's recorded repair outcomes without running a repair or changing "
                 'Windows settings.',
 'Close': 'Close the history window; recorded outcomes are kept.',
 'Undo Outlook/Teams change': 'Prepare a review of the saved Outlook/Teams browser-link setting '
                              'change, then ask before restoring it. This restores only the '
                              'settings recorded by this app.'}

INTRO = ('Run individual repair commands and inspect their output and local history. Repairs can restart '
 'services, interrupt networking or permanently delete caches and logs; caches may rebuild, but '
 'deleted records have no undo here. This module needs administrator rights; each Run tooltip '
 'explains whether confirmation appears.')

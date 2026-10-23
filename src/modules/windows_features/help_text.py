"""Plain-language explanations for this pane; no Qt dependencies."""
INTRO = ('Optional Windows features add operating-system components such as virtualization or legacy '
 'compatibility. Enable/Disable ask first and require administrator rights; downloads or a restart may '
 'be needed. The opposite action can change the feature state again, but it does not restore '
 'application data or previous dependent-feature settings.')

CONTROLS = {'Enable': 'Ask before enabling the selected Windows optional feature using DISM. Needs administrator '
           'rights and may need source files, a download or a restart; Disable can change its state '
           'again but is not a full rollback.',
 'Disable': 'Ask before disabling the selected Windows optional feature using DISM. Apps depending on '
            'it may stop working; needs administrator rights and may require restart. Enable can '
            'request it again, but does not restore application state.',
 'All states': 'Filter features by their Windows-reported state. This only changes the displayed list, '
               'not which features are enabled.'}

HEADERS = {'Feature Name': 'Internal optional-feature name returned by DISM /Online /Get-Features, not an '
                 'installed third-party app name.',
 'State': 'Feature state reported by DISM, including enabled, disabled or pending restart. A pending '
          'state may not be effective until Windows restarts.'}

TABS = {}


FEATURES = {'Microsoft-Hyper-V-All': 'Hyper-V virtualization components for running virtual machines. Disabling '
                          'can stop workloads that depend on Hyper-V; a restart may be required.',
 'VirtualMachinePlatform': 'Virtual Machine Platform supplies virtualization components used by '
                           'workloads such as WSL 2. Disabling it can prevent those workloads from '
                           'starting.',
 'Microsoft-Windows-Subsystem-Linux': 'Windows Subsystem for Linux supports running Linux environments '
                                      'on Windows. Disabling it can prevent WSL distributions from '
                                      'launching; this pane does not back up their data.',
 'Containers-DisposableClientVM': 'Windows Sandbox provides a disposable isolated Windows environment. '
                                  'Disabling it removes access to Sandbox; its temporary sessions are '
                                  'not persistent backups.',
 'IIS-WebServerRole': 'Internet Information Services web-server components. Disabling can make websites '
                      'hosted on this PC unavailable; enabling alone does not configure a secure '
                      'website.',
 'TelnetClient': 'Client for the legacy Telnet remote-terminal protocol. Telnet connections do not '
                 'encrypt their session; enabling the client does not create a Telnet server.',
 'TFTP': 'Client for Trivial File Transfer Protocol, a simple transfer protocol without built-in '
         'encryption or authentication. Enabling the client does not create a file server.',
 'ServicesForNFS-ClientOnly': 'Client for Network File System shares. Disabling can prevent access to '
                              'NFS shares used by this PC.',
 'DirectPlay': 'Legacy DirectPlay compatibility components used by some older games. Disabling can '
               'prevent games depending on them from running correctly.'}

"""Plain-language explanations for this pane; no Qt dependencies."""
INTRO = ('Services are background programs managed by Windows, often running before you sign in. Start/Stop '
 'affect the current session; Enable/Disable change future startup and can be changed again here, but '
 'lost work is not restored. Reading normally needs no elevation; changes generally need administrator '
 'rights.')

CONTROLS = {'Start': 'Start the selected service now without a confirmation dialog. Usually needs administrator '
          'rights; this does not change its startup type and Stop can request it to stop again.',
 'Stop': 'Request stopping the selected service immediately; there is no confirmation before the first stop attempt. Dependent programs may fail or lose work; '
         'administrator rights are usually required, and starting the service again does not restore '
         'lost work.',
 'Restart': 'Immediately request stopping and starting the service, without confirmation. Connections and in-progress work may be '
            'interrupted; usually needs administrator rights and cannot undo that interruption.',
 'Enable': "Set the selected service's startup type to Automatic immediately, without a confirmation "
           'dialog. Usually needs administrator rights; this does not start it now or restore its '
           'previous Manual/delayed setting.',
 'Disable': "Immediately set the service's startup type to Disabled, without confirmation. It will not be allowed to start "
            'until changed; usually needs administrator rights. Enable sets Automatic rather than '
            'restoring the old type.',
 'All': 'Filter by current Windows service state. Changing this view does not start or stop services.',
 'All Groups': "Filter using this tool's name-based Network, System, Application or Other grouping. "
               'This is a convenience grouping, not a Windows dependency classification.',
 'Refresh Details': 'Read configuration, dependencies and recovery settings for the selected service '
                    'again. This does not change it; restricted reads may require administrator rights.'}

HEADERS = {'Display Name': 'Friendly service name reported by Windows Win32_Service. Several services can have '
                 'similar display names.',
 'Name': 'Internal service identifier from Win32_Service, used for service-control commands and '
         'registry configuration.',
 'Status': 'Current service state reported by Win32_Service. Stopped can be normal for a service that '
           'starts only on demand.',
 'Start Type': 'Startup policy reported by Win32_Service: Automatic, Manual (on demand), or Disabled. '
               'Trigger and delayed-start details appear in the details panel.',
 'Impact': "This tool's High/Medium/Low estimate from service names and descriptions. It is a "
           'heuristic, not a measurement of CPU use or proof a service is dispensable.'}

TABS = {'Service List': 'Inventory of Windows services and their current states. The toolbar can change '
                 'services with administrator rights; startup changes can be changed again, but '
                 'interrupted work cannot be restored.',
 'Details': 'Configuration, account, dependencies and recovery information for the selected service. '
            'These details are read-only and may need administrator rights for restricted services; '
            'double-click a known dependency to navigate to it.'}


SERVICES = {'RpcSs': 'Remote Procedure Call lets Windows components communicate. Stopping or disabling it can '
          'break much of Windows and many applications.',
 'RpcEptMapper': 'RPC Endpoint Mapper helps programs locate Windows remote-procedure-call services. '
                 'Stopping it can prevent dependent Windows services and apps from communicating.',
 'DcomLaunch': 'DCOM Server Process Launcher starts COM and DCOM components on request. Disabling it '
               'can prevent Windows components and applications from starting.',
 'EventLog': 'Windows Event Log records system, security and application events. Stopping it can '
             'prevent event collection and break services that depend on logging.',
 'Schedule': 'Task Scheduler launches registered tasks when their triggers occur. Stopping it prevents '
             'scheduled maintenance and other task-driven work from running normally.',
 'PlugPlay': 'Plug and Play detects devices and manages hardware configuration changes. Disabling it '
             'can prevent Windows from recognising or configuring devices correctly.',
 'Power': 'Power manages power policy and related notifications. Stopping it can break normal '
          'power-management behaviour and dependent services.',
 'Dhcp': 'DHCP Client obtains automatic network addresses and configuration. Stopping it can prevent '
         'automatic address renewal and disrupt connectivity on networks that use DHCP.',
 'Dnscache': 'DNS Client caches name lookups and registers the computer name where supported. Stopping '
             'it prevents caching and registration; dependent services can fail.',
 'LanmanWorkstation': 'Workstation creates client connections to Windows SMB file shares. Stopping it '
                      'prevents access to remote SMB shares and breaks dependent services.',
 'LanmanServer': 'Server provides SMB file and printer sharing from this PC. Stopping it makes these '
                 'shared resources unavailable to other computers.',
 'MpsSvc': 'Windows Defender Firewall manages host firewall policy. Stopping it weakens or disrupts '
           'firewall functionality and can affect networking features.',
 'BFE': 'Base Filtering Engine manages filtering used by the firewall and IPsec. Stopping it reduces '
        'network protection and can break services that rely on filtering.',
 'WinDefend': 'Microsoft Defender Antivirus provides malware protection when Defender is active. '
              'Stopping or disabling it can remove that protection; Windows may block the change.',
 'wscsvc': 'Security Center monitors and reports security-product status. Stopping it can prevent '
           'security alerts and status reporting; it does not itself replace antivirus protection.',
 'SecurityHealthService': 'Windows Security Service supports Windows Security health reporting. '
                          'Stopping it can impair security-status reporting and the Windows Security '
                          'experience.',
 'wuauserv': 'Windows Update detects, downloads and installs Windows updates. Disabling it prevents '
             'normal Windows Update operation and can delay security fixes.',
 'BITS': 'Background Intelligent Transfer Service transfers files in the background for Windows and '
         'supported applications. Stopping it can interrupt update and app downloads.',
 'CryptSvc': 'Cryptographic Services supports certificate, catalog and signature-related Windows '
             'operations. Disabling it can break update installation and signature validation.',
 'TrustedInstaller': 'Windows Modules Installer installs, changes and removes Windows updates and '
                     'optional components. Disabling it prevents normal servicing operations.',
 'Winmgmt': 'Windows Management Instrumentation exposes system-management information. Stopping it can '
            'break diagnostics, monitoring and software that queries WMI.',
 'Spooler': 'Print Spooler queues print jobs and manages printers. Stopping it prevents normal printing '
            'and can affect printer discovery.',
 'Audiosrv': 'Windows Audio manages audio for applications. Stopping it disables normal audio playback '
             'and recording.',
 'AudioEndpointBuilder': 'Windows Audio Endpoint Builder manages audio devices exposed to applications. '
                         'Stopping it can make audio endpoints unavailable.',
 'WlanSvc': 'WLAN AutoConfig manages Wi-Fi discovery and connections. Stopping it can disconnect Wi-Fi '
            'or prevent automatic wireless connections.',
 'W32Time': 'Windows Time synchronizes the system clock. Disabling it stops automatic time '
            'synchronization, which can affect domain authentication when clocks drift.',
 'ProfSvc': 'User Profile Service loads and unloads user profiles. Disabling it can prevent users from '
            'signing in or loading their settings correctly.',
 'SamSs': 'Security Accounts Manager supports local account security information. Disabling it can '
          'break local account management and authentication-dependent operations.',
 'Netlogon': 'Netlogon maintains the secure channel used for domain authentication. Stopping it can '
             'disrupt domain logons and domain-resource access.',
 'WinRM': 'Windows Remote Management handles WS-Management requests for remote administration. Stopping '
          'it blocks management tools using that channel, including configured PowerShell remoting.',
 'WSearch': 'Windows Search builds the content index used for fast searches. Stopping it reduces '
            'indexed-search availability and can make searches slower or less complete.',
 'SysMain': 'SysMain uses activity patterns to improve application startup and responsiveness. '
            'Disabling it changes this optimization; it is not a guaranteed performance improvement.',
 'VSS': 'Volume Shadow Copy coordinates snapshots used by backup tools and restore operations. '
        'Disabling it can prevent snapshot-based backups and restores.'}


def service_help(service):
    """Use only known names or the service's own Windows description."""
    name = str(service.get("Name") or "")
    known = next((text for key, text in SERVICES.items() if key.casefold() == name.casefold()), "")
    own = str(service.get("Description") or "").strip()
    facts = "\n".join(f"{key}: {service.get(key) or 'not reported'}"
                      for key in ("Name", "Status", "Start Type", "Path"))
    return (known or ("Windows description: " + own if own else
                     "No purpose description was reported for this service; inspect its path and dependencies before changing it.")) + "\n" + facts

AUDIT_HELP = {'any': 'Show every service matching the other filters. No service configuration changes.',
 'unquoted': 'Show unquoted service executable paths containing spaces. This is a configuration concern '
             'to inspect; it does not prove the path is exploitable or that malware is present.',
 'crashed': 'Show services matched to failure events in the last 30 days. Missing or refused log '
            'evidence can leave no matches; this is not proof no crashes occurred.',
 'autostopped': 'Automatic but currently stopped. Trigger-start, delayed start or a completed one-time '
                'job can make this normal; it is not automatically a fault.',
 'thirdparty': 'Binary path is outside the Windows folder according to this tool. This is a path '
               'heuristic, not verified publisher information.',
 'account': 'Runs under an account other than the built-in service accounts. Password expiry or account '
            'lockout can stop such a service from signing in.',
 'disabled': 'Startup type is Disabled, so Windows will refuse a new start until the type is changed. '
             "This is not necessarily the service's current running state.",
 'delayed': 'Automatic delayed start is set in the service registry configuration. Windows starts it '
            'after the initial automatic services, rather than immediately at boot.',
 'triggerstart': 'Registry configuration contains service triggers, such as device or network events. A '
                 'trigger-start service can normally remain stopped until needed.'}

DETAIL_HELP = {'_detail_name_value': 'Internal identifier from Win32_Service; this is the name used by '
                       'service-control commands.',
 '_detail_disp_value': 'Friendly name reported by Win32_Service; this can differ from the internal '
                       'service identifier.',
 '_detail_type_value': 'Service type read from sc.exe qc, for example a standalone process or a shared '
                       'host. It is not the startup policy.',
 '_detail_start_type_value': 'Startup type read from sc.exe qc. Automatic starts at boot, Manual starts '
                             'on demand, and Disabled blocks a new start; triggers and delayed start '
                             'add further conditions.',
 '_detail_error_value': 'Error-control policy reported by sc.exe qc: how Windows handles a failure '
                        'during startup. This is configuration, not the latest runtime error.',
 '_detail_account_value': 'Logon account reported by Win32_Service. LocalSystem has extensive local '
                          'privileges; a custom account can fail if its password expires or it is '
                          'locked out.',
 '_detail_path_value': 'Binary path and arguments reported by sc.exe qc. It can name a shared service '
                       'host rather than a dedicated executable; this field is read-only.',
 '_detail_load_group_value': 'Load-order group from sc.exe qc. Windows uses groups to order startup; no '
                             'group means none was reported, not that there are no dependencies.',
 '_detail_status_value': 'Current service state reported by Win32_Service. Stopped can be normal for an '
                         'on-demand or trigger-start service.',
 '_detail_pid_value': 'Process ID reported by Win32_Service. Several services may share one process; an '
                      'empty ID can mean it is not running.',
 '_detail_impact_value': 'High/Medium/Low estimate based on names and descriptions, not measured '
                         'resource use. A Low estimate does not guarantee stopping the service is '
                         'harmless.',
 '_detail_desc_value': 'Purpose description supplied by Windows or the service vendor. This is '
                       'metadata, not an independent assessment of whether the service is needed.',
 '_detail_audit_value': 'Findings from configured recovery actions, dependency startup states and '
                        'recorded service failures. These checks do not prove the service is fault-free '
                        'or repair anything.',
 '_detail_deps_list': 'Services this service needs. A Disabled dependency blocks startup; double-click '
                      'a known service to navigate without changing it.',
 '_detail_rby_list': 'Services that depend on this one, read using sc.exe enumdepend. Stopping a '
                     'dependency can interrupt them; double-click only navigates.'}

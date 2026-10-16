"""Event logs and Network checks. Read-only."""
from ._base import CHK, CMD, L, T, maker, need_admin

E = maker("Event logs")
N = maker("Network")

_EVENTS = (r"""function Show($filter, $max = 50) {
  $e = Get-WinEvent -FilterHashtable $filter -MaxEvents $max -ErrorAction SilentlyContinue
  if (-not $e) { 'No matching events.'; return }
  $e | ForEach-Object { $tag = switch ($_.Level) { 1 {'[BAD] '} 2 {'[BAD] '} 3 {'[WARN]'} default {'[INFO]'} }
    '{0} {1:yyyy-MM-dd HH:mm:ss}  {2,5}  {3,-38} {4}' -f $tag, $_.TimeCreated, $_.Id, $_.ProviderName, (($_.Message -split "`r?`n")[0]) } }
""")

SCRIPTS = [
    # ---- Event logs --------------------------------------------------------------------------
    E("sys_errors_24h", "System errors in the last 24 hours",
      "Critical and error events from the System log in the last day, newest first, one line "
      "each.",
      _EVENTS + "Show @{LogName='System'; Level=1,2; StartTime=(Get-Date).AddDays(-1)} 100"),
    E("app_errors_24h", "Application errors in the last 24 hours",
      "Critical and error events from the Application log in the last day.",
      _EVENTS + "Show @{LogName='Application'; Level=1,2; StartTime=(Get-Date).AddDays(-1)} 100"),
    E("sys_warnings_7d", "System warnings in the last 7 days",
      "Warnings from the System log in the last week -- disk, NTFS, DNS client, time "
      "service problems often start as warnings.",
      _EVENTS + "Show @{LogName='System'; Level=3; StartTime=(Get-Date).AddDays(-7)} 150"),
    E("top_error_sources", "Most frequent error sources (7 days)",
      "Errors grouped by source and event id, most frequent first -- one noisy driver "
      "stands out immediately instead of being 400 lines in a list.",
      T(r"""Get-WinEvent -FilterHashtable @{LogName='System','Application'; Level=1,2; StartTime=(Get-Date).AddDays(-7)} -ErrorAction SilentlyContinue |
  Group-Object LogName, ProviderName, Id | Sort-Object Count -Descending | Select-Object -First 25 Count, @{n='Log, source, id';e={$_.Name}}, @{n='Last';e={($_.Group | Select-Object -First 1).TimeCreated}}""")),
    E("app_crashes", "Application crashes and hangs (30 days)",
      "Every program that crashed (Application Error 1000) or stopped responding "
      "(Application Hang 1002), grouped by program with the faulting module.",
      T(r"""Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000,1002; StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue | ForEach-Object {
  [pscustomobject]@{ Kind=$(if ($_.Id -eq 1000) {'crash'} else {'hang'}); App=$_.Properties[0].Value; Module=$(if ($_.Id -eq 1000) { $_.Properties[3].Value } else { '' }); Time=$_.TimeCreated } } |
  Group-Object Kind, App, Module | Sort-Object Count -Descending | Select-Object Count, Name, @{n='Last';e={($_.Group | Sort-Object Time -Descending | Select-Object -First 1).Time}}""")),
    E("bsod_history", "Blue screens (bugchecks)",
      "Every bugcheck Windows recorded (event 1001 BugCheck) with its stop code and "
      "parameters, plus the minidumps on disk.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WER-SystemErrorReporting'; Id=1001} -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No blue screen recorded in the System log.' } else { $e | ForEach-Object { "[BAD]  $($_.TimeCreated)  $(($_.Message -split "`r?`n")[0])" } }
''
'Minidumps:'
Get-ChildItem "$env:SystemRoot\Minidump" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | ForEach-Object { '{0}  {1,8:N0} KB  {2}' -f $_.LastWriteTime, ($_.Length/1KB), $_.Name }"""),
    E("unexpected_shutdowns", "Unexpected shutdowns and power loss",
      "Kernel-Power 41 (rebooted without a clean shutdown) and EventLog 6008 (previous "
      "shutdown was unexpected), with the bugcheck code 41 recorded -- 0 means power loss or a "
      "hard reset, not a blue screen.",
      r"""Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,6008} -MaxEvents 30 -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_.Id -eq 41) { "[BAD]  $($_.TimeCreated)  Kernel-Power 41 -- bugcheck code $($_.Properties[0].Value) $(if ($_.Properties[0].Value -eq 0) {'(power loss / hard reset / hang)'})" }
  else { "[WARN] $($_.TimeCreated)  6008 -- $(($_.Message -split "`r?`n")[0])" } }"""),
    E("disk_errors", "Disk, NTFS and storage controller errors",
      "Bad blocks (disk 7), controller resets (storahci/stornvme 129), NTFS corruption (55, "
      "98) and delayed write failures (140) -- the events that come before a disk dies.",
      _EVENTS + "Show @{LogName='System'; ProviderName='disk','Ntfs','storahci','stornvme','volmgr','Microsoft-Windows-Ntfs','iaStorAC'; Level=1,2,3; StartTime=(Get-Date).AddDays(-30)} 100"),
    E("driver_load_failures", "Drivers and services that failed to start",
      "Service Control Manager errors 7000/7001/7009/7023/7031/7034 -- services that failed "
      "or crashed -- and driver load failures, in the last 7 days.",
      _EVENTS + "Show @{LogName='System'; ProviderName='Service Control Manager'; Id=7000,7001,7009,7011,7023,7024,7031,7034,7043; StartTime=(Get-Date).AddDays(-7)} 100"),
    E("wu_events", "Windows Update installs and failures",
      "Update installs (19), failures (20) and restarts required (21/22) from the Windows "
      "Update client, in the last 30 days.",
      _EVENTS + "Show @{LogName='System'; ProviderName='Microsoft-Windows-WindowsUpdateClient'; StartTime=(Get-Date).AddDays(-30)} 100"),
    E("whea", "Hardware errors (WHEA)",
      "Machine Check and PCIe errors Windows logged through WHEA-Logger: corrected errors "
      "are early warnings of failing RAM, CPU or a PCIe link; fatal ones crashed the PC.",
      _EVENTS + "Show @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'} 100"),
    E("gpu_resets", "GPU driver timeouts (TDR)",
      "Display driver stopped responding and recovered (Display 4101) and related LiveKernel "
      "reports -- the 'screen went black for a second' events.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Display'; Id=4101} -MaxEvents 30 -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No display driver timeouts (4101) recorded.' } else { $e | ForEach-Object { "[WARN] $($_.TimeCreated)  $(($_.Message -split "`r?`n")[0])" } }
Get-ChildItem "$env:SystemRoot\LiveKernelReports" -Recurse -Filter *.dmp -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 15 | ForEach-Object { "[INFO] LiveKernelReport $($_.LastWriteTime)  $($_.Directory.Name)\$($_.Name)" }"""),
    E("event_log_sizes", "Event log sizes and retention",
      "Every non-empty event log with its size, maximum size and record count -- which logs "
      "are full and overwriting history.",
      T("Get-WinEvent -ListLog * -ErrorAction SilentlyContinue | Where-Object RecordCount -gt 0 | Sort-Object FileSize -Descending | Select-Object -First 40 LogName, @{n='MB';e={[math]::Round($_.FileSize/1MB,1)}}, @{n='MaxMB';e={[math]::Round($_.MaximumSizeInBytes/1MB)}}, RecordCount, LogMode, IsEnabled"),
      note="a few logs (Security) are only listed elevated"),
    E("logon_history", "Sign-in history",
      "Interactive, remote and unlock sign-ins (4624 types 2, 7, 10, 11) and failures (4625) "
      "in the last 7 days, from the Security log.",
      need_admin("the Security log is only readable elevated") +
      r"""$t = @{'2'='interactive';'7'='unlock';'10'='remote (RDP)';'11'='cached'}
Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4624,4625; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 3000 -ErrorAction SilentlyContinue | ForEach-Object {
  $d = @{}; ([xml]$_.ToXml()).Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }
  if ($t.ContainsKey([string]$d.LogonType)) {
    '{0} {1:yyyy-MM-dd HH:mm}  {2,-14} {3}\{4}  from {5}' -f $(if ($_.Id -eq 4625) {'[WARN]'} else {'[INFO]'}), $_.TimeCreated, $t[[string]$d.LogonType], $d.TargetDomainName, $d.TargetUserName, $d.IpAddress } } | Select-Object -First 200""",
      admin=True),
    E("powershell_activity", "PowerShell scripts run recently",
      "The last 40 script blocks PowerShell logged (4104) with their first line -- what ran, "
      "as which process. Needs script block logging (on here).",
      r"""Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-PowerShell/Operational'; Id=4104} -MaxEvents 200 -ErrorAction SilentlyContinue |
  Where-Object { $_.Properties[0].Value -eq 1 } | Select-Object -First 40 | ForEach-Object {
  $tag = if ($_.Level -eq 3) {'[WARN]'} else {'[INFO]'}
  '{0} {1:yyyy-MM-dd HH:mm:ss}  {2}' -f $tag, $_.TimeCreated, ((($_.Properties[2].Value) -replace '\s+',' ').Trim().Substring(0, [Math]::Min(150, (($_.Properties[2].Value) -replace '\s+',' ').Trim().Length))) }"""),
    # ---- Network -----------------------------------------------------------------------------
    N("adapters_ip", "Adapters and IP addresses",
      "Every connected adapter with its IPv4/IPv6 addresses, gateway and DNS servers.",
      T("Get-NetIPConfiguration | Where-Object { $_.NetAdapter.Status -eq 'Up' } | Select-Object InterfaceAlias, @{n='IPv4';e={($_.IPv4Address.IPAddress) -join ', '}}, @{n='IPv6';e={($_.IPv6Address.IPAddress) -join ', '}}, @{n='Gateway';e={($_.IPv4DefaultGateway.NextHop) -join ', '}}, @{n='DNS';e={($_.DNSServer.ServerAddresses) -join ', '}}")),
    N("connectivity", "Internet connectivity test",
      "Gateway ping, DNS resolution, an HTTPS request and Microsoft's own connectivity probe "
      "(the one the network icon uses) -- which layer is broken.",
      CHK + r"""$gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1).NextHop
if ($gw) { Chk (Test-Connection $gw -Count 2 -Quiet) 'Default gateway' $gw } else { Chk $false 'Default gateway' 'no default route' }
try { $r = Resolve-DnsName www.microsoft.com -ErrorAction Stop | Where-Object IPAddress | Select-Object -First 1; Chk $true 'DNS' "www.microsoft.com -> $($r.IPAddress)" } catch { Chk $false 'DNS' $_.Exception.Message }
try { $w = Invoke-RestMethod 'http://www.msftconnecttest.com/connecttest.txt' -TimeoutSec 8; Chk ($w -eq 'Microsoft Connect Test') 'NCSI probe' "$w" } catch { Chk $false 'NCSI probe' $_.Exception.Message }
try { $null = [Net.Sockets.TcpClient]::new('www.microsoft.com', 443); Chk $true 'HTTPS port 443' 'reachable' } catch { Chk $false 'HTTPS port 443' $_.Exception.Message }""",
      timeout=90),
    N("public_ip", "Public IP address",
      "The address the internet sees for this PC (asked of two independent services), and "
      "whether it differs from the local one -- behind NAT or a VPN.",
      r"""foreach ($u in 'https://api.ipify.org','https://ifconfig.me/ip') { try { '{0,-28} {1}' -f $u, (Invoke-RestMethod $u -TimeoutSec 8) } catch { "ERROR: $u -- $($_.Exception.Message)" } }""",
      timeout=60),
    N("dns_servers", "DNS servers per adapter",
      "Which DNS servers each adapter uses, IPv4 and IPv6. A rogue or stale DNS server here "
      "redirects everything.",
      T("Get-DnsClientServerAddress | Where-Object ServerAddresses | Select-Object InterfaceAlias, AddressFamily, @{n='Servers';e={$_.ServerAddresses -join ', '}}")),
    N("dns_resolution_test", "DNS resolution test",
      "Resolves a set of well-known names through the configured DNS and directly through "
      "1.1.1.1, timing both -- slow or failing DNS is the most common 'internet is slow'.",
      r"""foreach ($n in 'www.microsoft.com','www.google.com','login.microsoftonline.com','github.com') {
  foreach ($s in $null, '1.1.1.1') { $t = Measure-Command { try { $r = if ($s) { Resolve-DnsName $n -Server $s -DnsOnly -ErrorAction Stop } else { Resolve-DnsName $n -DnsOnly -ErrorAction Stop }; $ok = $true } catch { $ok = $false } }
    '{0} {1,-28} via {2,-10} {3,6:N0} ms' -f $(if ($ok) {'[OK]  '} else {'[BAD] '}), $n, $(if ($s) {$s} else {'system'}), $t.TotalMilliseconds } }""",
      timeout=90),
    N("hosts_file", "Hosts file entries",
      "Active entries in the hosts file (comments skipped). An entry for a Microsoft, "
      "antivirus or bank domain here is a classic malware redirect.",
      r"""$p = "$env:SystemRoot\System32\drivers\etc\hosts"
$lines = Get-Content $p -ErrorAction SilentlyContinue | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') }
if (-not $lines) { '[OK]   No active entries -- the hosts file overrides nothing.' } else { $lines | ForEach-Object { "[WARN] $_" } }"""),
    N("proxy", "Proxy settings",
      "The WinHTTP proxy (services, Windows Update) and this user's WinINet proxy and PAC "
      "script (browsers, most apps). A stale proxy breaks updates while browsing works.",
      r"""netsh winhttp show proxy
''
$i = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
"User proxy enabled : $([bool]$i.ProxyEnable)"
"User proxy server  : $($i.ProxyServer)"
"Bypass list        : $($i.ProxyOverride)"
"PAC script         : $($i.AutoConfigURL)" """),
    N("wifi_current", "Current Wi-Fi connection",
      "SSID, BSSID, signal, radio type (802.11ax...), channel, band and link rates of the "
      "current wireless connection.",
      "netsh wlan show interfaces", kind=CMD),
    N("wifi_drivers", "Wi-Fi driver capabilities",
      "What the wireless driver supports: radio types, WPA3, hosted network, bands.",
      "netsh wlan show drivers", kind=CMD),
    N("firewall_blocked_inbound", "Inbound firewall rules that ALLOW from anywhere",
      "Enabled inbound allow rules with no remote-address restriction, by program and port "
      "-- what is reachable from the network if a port is listening.",
      T(r"""Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True -ErrorAction SilentlyContinue | ForEach-Object {
  $a = $_ | Get-NetFirewallAddressFilter; $p = $_ | Get-NetFirewallPortFilter; $app = $_ | Get-NetFirewallApplicationFilter
  if ($a.RemoteAddress -contains 'Any' -and $p.Protocol -in 'TCP','UDP') { [pscustomobject]@{ Rule=$_.DisplayName; Profile=$_.Profile; Proto=$p.Protocol; Port=($p.LocalPort -join ','); Program=$app.Program } } } | Sort-Object Profile, Rule"""),
      timeout=240),
    N("smb_client", "SMB client: mapped drives and connections",
      "Mapped network drives and open SMB connections with the dialect in use (SMB 1 here is "
      "a finding).",
      r"""$m = Get-SmbMapping -ErrorAction SilentlyContinue
if ($m) { $m | Select-Object LocalPath, RemotePath, Status | Format-Table -AutoSize | Out-String -Width 220 } else { '[INFO] No mapped network drives.' }
$c = Get-SmbConnection -ErrorAction SilentlyContinue
if ($c) { $c | Select-Object ServerName, ShareName, UserName, Dialect | Format-Table -AutoSize | Out-String -Width 220 } else { '[INFO] No open SMB connections.' }"""),
    N("smb1_status", "SMBv1 status",
      "Whether the SMB 1.0 client and server are installed or enabled. SMBv1 is what "
      "WannaCry spread over; nothing modern needs it.",
      CHK + r"""$s = Get-SmbServerConfiguration -ErrorAction SilentlyContinue
if ($s) { Chk (-not $s.EnableSMB1Protocol) 'SMB1 server protocol' "EnableSMB1Protocol=$($s.EnableSMB1Protocol)" }
# Get-WindowsOptionalFeature THROWS for a standard user, whatever -ErrorAction says.
$isAdm = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$f = if ($isAdm) { Get-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -ErrorAction SilentlyContinue }
if ($f) { Chk ($f.State -ne 'Enabled') 'SMB1 feature' $f.State } elseif ($isAdm) { Chk $true 'SMB1 feature' 'not part of this Windows build' } else { Chk $null 'SMB1 feature' 'needs administrator to read' }""",
      note="the optional-feature state needs elevation", admin=True),
    N("network_profile", "Network location (Public / Private / Domain)",
      "The category Windows assigned each connected network, which decides which firewall "
      "profile applies. A home network left Public blocks file sharing and casting.",
      T("Get-NetConnectionProfile | Select-Object InterfaceAlias, Name, NetworkCategory, IPv4Connectivity, IPv6Connectivity")),
    N("arp_neighbors", "Neighbours on the local network (ARP)",
      "IP and MAC addresses this PC has talked to on the local network, reachable or stale.",
      T("Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.State -ne 'Unreachable' -and $_.IPAddress -notmatch '^(224|239|255)\\.' } | Sort-Object InterfaceAlias, IPAddress | Select-Object InterfaceAlias, IPAddress, LinkLayerAddress, State")),
    N("established", "Established connections by program",
      "Every open TCP connection to a remote address, with the owning program -- 'what is "
      "this PC talking to right now'.",
      T(r"""$p = @{}; Get-Process | ForEach-Object { $p[$_.Id] = $_.ProcessName }
Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | Where-Object { $_.RemoteAddress -notin '127.0.0.1','::1' } | Sort-Object OwningProcess |
  Select-Object @{n='Process';e={$p[[int]$_.OwningProcess]}}, OwningProcess, LocalPort, RemoteAddress, RemotePort"""),
      note="process names of system processes may be blank"),
    N("trace_route", "Route to the internet (traceroute)",
      "The routers between this PC and 1.1.1.1, with latency per hop -- where the delay or "
      "the break is.",
      "tracert -d -w 800 -h 20 1.1.1.1", kind=CMD, timeout=180),
    N("ping_quality", "Latency and packet loss",
      "20 pings each to the gateway and to 1.1.1.1 with min/avg/max and loss -- jitter and "
      "loss show a bad Wi-Fi link or ISP problem.",
      r"""$gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1).NextHop
foreach ($t in @($gw, '1.1.1.1') | Where-Object { $_ }) {
  $r = Test-Connection $t -Count 20 -ErrorAction SilentlyContinue
  $ok = @($r).Count; $loss = 100 - 5 * $ok
  if ($ok) { $m = $r | Measure-Object ResponseTime -Minimum -Maximum -Average; '{0} {1,-16} loss {2,3}%  min {3} ms  avg {4:N1} ms  max {5} ms' -f $(if ($loss -gt 0) {'[WARN]'} else {'[OK]  '}), $t, $loss, $m.Minimum, $m.Average, $m.Maximum }
  else { "[BAD]  $t -- no replies" } }""", timeout=120),
    N("vpn_connections", "VPN connections",
      "Windows' own VPN profiles (Settings > VPN) with their server and tunnel type, and any "
      "VPN adapters from third-party clients.",
      r"""Get-VpnConnection -ErrorAction SilentlyContinue | Select-Object Name, ServerAddress, TunnelType, ConnectionStatus, SplitTunneling | Format-Table -AutoSize | Out-String -Width 220
Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceDescription -match 'VPN|WireGuard|TAP|Wintun|OpenVPN|Tunnel' } | Select-Object Name, InterfaceDescription, Status | Format-Table -AutoSize | Out-String -Width 220"""),
    N("ipv6_status", "IPv6 status",
      "Whether IPv6 is bound per adapter, any global IPv6 address, and the DisabledComponents "
      "policy (disabling IPv6 by registry breaks some Windows features).",
      r"""Get-NetAdapterBinding -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue | Select-Object Name, Enabled | Format-Table -AutoSize | Out-String -Width 220
Get-NetIPAddress -AddressFamily IPv6 -ErrorAction SilentlyContinue | Where-Object { $_.PrefixOrigin -ne 'WellKnown' -and $_.IPAddress -notlike 'fe80*' } | Select-Object InterfaceAlias, IPAddress | Format-Table -AutoSize | Out-String -Width 220
$d = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' -ErrorAction SilentlyContinue).DisabledComponents
"DisabledComponents: $(if ($null -eq $d) { 'not set (IPv6 fully on)' } else { '0x{0:X}' -f $d })" """),
    N("mtu", "MTU per interface",
      "The MTU each interface uses. A VPN or PPPoE link with a too-large MTU causes pages that "
      "half-load.",
      T("Get-NetIPInterface -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object ConnectionState -eq 'Connected' | Select-Object InterfaceAlias, NlMtu, InterfaceMetric, Dhcp")),
    N("tcp_settings", "TCP global settings",
      "Receive window auto-tuning, RSS, ECN and congestion provider -- auto-tuning disabled by "
      "an old 'speed tweak' caps download speed.",
      "netsh int tcp show global", kind=CMD),
    N("net_shares_remote", "Network discovery and file sharing",
      "Whether network discovery and file and printer sharing firewall rules are enabled "
      "for the current profile.",
      T("Get-NetFirewallRule -Group '@FirewallAPI.dll,-32752','@FirewallAPI.dll,-28502' -ErrorAction SilentlyContinue | Group-Object Group, Profile, Enabled | Select-Object Count, Name")),
    N("dhcp_lease", "DHCP lease details",
      "For each DHCP adapter: the DHCP server, lease obtained and expiry -- 'my IP keeps "
      "changing' and duplicate-address problems.",
      T("Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'DHCPEnabled=TRUE AND IPEnabled=TRUE' | Select-Object Description, DHCPServer, DHCPLeaseObtained, DHCPLeaseExpires, @{n='IP';e={$_.IPAddress -join ', '}}")),
    N("winsock_catalog", "Winsock providers (LSP)",
      "Layered service providers in the Winsock catalog. Anything not from Microsoft here "
      "intercepts all network traffic -- old adware and some VPNs do this.",
      r"""netsh winsock show catalog | Select-String 'Description:' | ForEach-Object { $d = $_.Line.Split(':',2)[1].Trim(); if ($d -match 'MSAFD|Hyper-V RAW|RSVP') { "[OK]   $d" } else { "[WARN] $d" } } | Sort-Object -Unique"""),
    N("listening_firewall", "Listening ports and whether the firewall lets them in",
      "Each listening TCP port with the program and whether any enabled inbound allow rule "
      "matches its program -- exposure at a glance.",
      r"""$allow = Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True -ErrorAction SilentlyContinue | Get-NetFirewallApplicationFilter | ForEach-Object Program | Where-Object { $_ -and $_ -ne 'Any' } | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_).ToLower() }
$procs = @{}; Get-Process | ForEach-Object { $procs[$_.Id] = $_ }
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalAddress -notin '127.0.0.1','::1' } | Sort-Object LocalPort -Unique | ForEach-Object {
  $p = $procs[[int]$_.OwningProcess]; $path = if ($p -and $p.Path) { $p.Path.ToLower() } else { '' }
  $tag = if ($path -and $allow -contains $path) { '[WARN] allowed by a rule' } else { '[INFO] no program rule' }
  '{0,-26} {1,-15} {2,6}  {3}' -f $tag, $_.LocalAddress, $_.LocalPort, $(if ($p) { $p.ProcessName } else { "pid $($_.OwningProcess)" }) }""",
      timeout=180),
]

"""Event logs and Network checks. Read-only."""
from ._base import CHK, CMD, T, maker, need_admin

E = maker("Event logs")
N = maker("Network")

_DEEP_NETWORK_SCRIPTS = [
    N("client_tcp_ephemeral_pressure", "TCP dynamic port range pressure",
      "Compares IPv4/IPv6 dynamic port ranges with unique occupied local ports and top socket owners.",
      CHK + r"""try {
  $connections = @(Get-NetTCPConnection -ErrorAction Stop)
  if (-not $connections.Count) { Chk $null 'TCP sockets' 'no connections returned' }
  foreach ($family in 'ipv4','ipv6') {
    $output = & "$env:SystemRoot\System32\netsh.exe" int $family show dynamicport tcp 2>&1; $code = $LASTEXITCODE
    "== $family dynamic range"; $output
    if ($code -ne 0) { Chk $null $family "netsh exited $code"; continue }
    # netsh field labels are localized; only accept exactly two numeric value lines.
    $values = @($output | ForEach-Object { if ("$_" -match ':\s*(\d+)\s*$') { [int]$Matches[1] } })
    if ($values.Count -ne 2 -or $values[1] -le 0 -or $values[0]+$values[1]-1 -gt 65535) { Chk $null $family 'range output could not be parsed safely'; continue }
    $start = $values[0]; $end = $start+$values[1]-1
    $sockets = @($connections | Where-Object { ($_.LocalAddress.Contains(':') -eq ($family -eq 'ipv6')) -and $_.LocalPort -ge $start -and $_.LocalPort -le $end })
    $ports = @($sockets.LocalPort | Sort-Object -Unique).Count; $pct = 100*$ports/$values[1]
    Chk ($pct -lt 80) $family ("{0}/{1} unique local ports observed ({2:N1}%); 80% is a review threshold, not proof of exhaustion" -f $ports,$values[1],$pct)
    $sockets | Group-Object State, OwningProcess | Sort-Object Count -Descending | Select-Object -First 15 Count, Name | Format-Table -AutoSize | Out-String -Width 220
    $excluded = & "$env:SystemRoot\System32\netsh.exe" int $family show excludedportrange protocol=tcp 2>&1; $excludedCode = $LASTEXITCODE
    $excluded; if ($excludedCode -ne 0) { Chk $null 'Excluded ranges' "netsh exited $excludedCode" }
  }
  '[INFO] Ports may be reused across addresses and remote tuples. Exclusions, bound sockets and allocation failures need separate review; this is observed occupancy, not remaining capacity.'
} catch { Chk $null 'TCP port pressure' $_.Exception.Message }""",
      tags=('diagnostic', 'network', 'triage', 'health')),
    N("client_tls_endpoint_handshake", "TLS handshake and certificate validation",
      "Edit endpointHost and endpointPort in a copy to test TCP, SNI, TLS negotiation and normal certificate validation.",
      CHK + r"""$endpointHost = 'www.microsoft.com'; $endpointPort = 443
$tcp = $null; $ssl = $null
try {
  "[INFO] Direct TLS handshake to ${endpointHost}:$endpointPort; no HTTP proxy or STARTTLS negotiation. Edit variables in a copy for your endpoint."
  $tcp = [Net.Sockets.TcpClient]::new()
  $connect = $tcp.ConnectAsync($endpointHost,$endpointPort)
  if (-not $connect.Wait(10000)) { throw 'TCP connect timed out after 10 seconds' }
  $connect.GetAwaiter().GetResult()
  $ssl = [Net.Security.SslStream]::new($tcp.GetStream(),$false)
  $auth = $ssl.AuthenticateAsClientAsync($endpointHost)
  if (-not $auth.Wait(15000)) { throw 'TLS handshake timed out after 15 seconds' }
  $auth.GetAwaiter().GetResult()
  Chk $ssl.IsAuthenticated 'TLS authenticated' "protocol=$($ssl.SslProtocol); cipher=$($ssl.CipherAlgorithm) $($ssl.CipherStrength) bits; name/chain validation used platform defaults"
  if ($ssl.RemoteCertificate) {
    $cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new($ssl.RemoteCertificate)
    try { $cert | Select-Object Subject, Issuer, NotBefore, NotAfter, Thumbprint | Format-List | Out-String -Width 220 }
    finally { $cert.Dispose() }
  } else { Chk $null 'Peer certificate' 'no certificate returned' }
  '[INFO] Protocol selection uses this PowerShell/.NET host defaults. This does not test every protocol, HTTP, revocation availability or application authentication.'
} catch { Chk $false 'TLS handshake failed' $_.Exception.GetBaseException().Message }
finally { if ($ssl) { $ssl.Dispose() }; if ($tcp) { $tcp.Dispose() } }""",
      timeout=45, tags=('diagnostic', 'network', 'security', 'health')),
    N("client_smb_multichannel_rss", "SMB multichannel and RSS evidence",
      "Correlates SMB client settings, selected channels, client network interfaces and RSS adapter settings.",
      need_admin("complete SMB multichannel and adapter settings require elevation") + r"""foreach ($cmd in 'Get-SmbClientConfiguration','Get-SmbMultichannelConnection','Get-SmbClientNetworkInterface','Get-NetAdapterRss') {
  try {
    "== $cmd"
    $rows = @(& $cmd -ErrorAction Stop)
    if (-not $rows.Count) { '[INFO] No entries returned; active SMB connections or RSS-capable adapters may be absent.'; continue }
    switch ($cmd) {
      'Get-SmbClientConfiguration' { $rows | Select-Object EnableMultiChannel, ConnectionCountPerRssNetworkInterface, MaximumConnectionCountPerServer | Format-List | Out-String -Width 220 }
      'Get-SmbMultichannelConnection' { $rows | Select-Object ServerName, Selected, ClientIPAddress, ServerIPAddress, ClientInterfaceIndex, ServerInterfaceIndex, ClientRssCapable, ClientRdmaCapable | Format-Table -AutoSize | Out-String -Width 220 }
      'Get-SmbClientNetworkInterface' { $rows | Format-Table -AutoSize | Out-String -Width 220 }
      'Get-NetAdapterRss' { $rows | Select-Object Name, Enabled, NumberOfReceiveQueues, Profile, BaseProcessorNumber, MaxProcessors | Format-Table -AutoSize | Out-String -Width 220 }
    }
  } catch { "NOT AVAILABLE: ${cmd}: $($_.Exception.Message)" }
}
'[INFO] RSS capability alone does not prove multichannel use; inspect selected live channels. No adapter settings were changed.'""",
      admin=True, tags=('diagnostic', 'network', 'performance')),
    N("client_tcp_retransmit_delta", "TCP connections and retransmission rate (10 seconds)",
      "Shows netstat endpoints plus IPv4/IPv6 retransmission deltas from native TCP statistics.",
      CHK + r"""try {
  $netstat = & "$env:SystemRoot\System32\netstat.exe" -ano -p tcp 2>&1; $code = $LASTEXITCODE
  if ($code -ne 0) { Chk $null 'Netstat' "exit $code" }; if ($netstat) { $netstat } else { Chk $null 'Netstat' 'no output returned' }
  $ip = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties()
  $first = @{}; foreach ($family in 4,6) {
    try { $first[$family] = if ($family -eq 4) { $ip.GetTcpIPv4Statistics() } else { $ip.GetTcpIPv6Statistics() } }
    catch { Chk $null "IPv$family initial counters" $_.Exception.Message }
  }
  $clock = [Diagnostics.Stopwatch]::StartNew(); Start-Sleep -Seconds 10
  foreach ($family in 4,6) {
    if (-not $first.ContainsKey($family)) { continue }
    try {
      $last = if ($family -eq 4) { $ip.GetTcpIPv4Statistics() } else { $ip.GetTcpIPv6Statistics() }
      $delta = $last.SegmentsResent-$first[$family].SegmentsResent; $sent = $last.SegmentsSent-$first[$family].SegmentsSent
      if ($delta -lt 0 -or $sent -lt 0) { Chk $null "IPv$family TCP" 'counters reset'; continue }
      Chk ($delta -eq 0) "IPv$family retransmissions" ("{0} resent, {1} sent over {2:N1}s; {3:N2} retransmits/s (host-wide, not attributable to individual sockets)" -f $delta,$sent,$clock.Elapsed.TotalSeconds,($delta/$clock.Elapsed.TotalSeconds))
    } catch { Chk $null "IPv$family counters" $_.Exception.Message }
  }
} catch { Chk $null 'TCP statistics' $_.Exception.Message }""",
      tags=('diagnostic', 'network', 'performance', 'health')),
    N("client_dns_cache_efficiency_evidence", "DNS client cache and efficiency evidence",
      "Shows cache composition and TTLs, explicitly distinguishing inventory from unavailable client hit/miss telemetry.",
      need_admin("DNS client cache may silently return no data unelevated") + CHK + r"""try {
  $cache = @(Get-DnsClientCache -ErrorAction Stop)
  Chk $null 'DNS cache snapshot' "$($cache.Count) returned entries; an empty cache is not evidence of lookup failure"
  $cache | Group-Object Status, Type | Select-Object Count, Name | Format-Table -AutoSize | Out-String -Width 220
  $cache | Sort-Object TimeToLive | Select-Object -First 30 Entry, RecordName, RecordType, Status, TimeToLive, Data | Format-Table -AutoSize -Wrap | Out-String -Width 220
  Chk $null 'Client cache hit/miss ratio' 'NOT AVAILABLE from Get-DnsClientCache: cache entries have no query or hit counters. Windows DNS Server cache counters do not measure the client cache. Existing DNS ETW traces are needed for a measured workload ratio; no trace was started.'
} catch { Chk $null 'DNS client cache' $_.Exception.Message }""",
      admin=True, tags=('diagnostic', 'network', 'performance')),
]

_EVENTS = (r"""function Show($filter, $max = 50) {
  $e = Get-WinEvent -FilterHashtable $filter -MaxEvents $max -ErrorAction SilentlyContinue
  if (-not $e) { 'No matching events.'; return }
  $e | ForEach-Object { $tag = switch ($_.Level) { 1 {'[BAD] '} 2 {'[BAD] '} 3 {'[WARN]'} default {'[INFO]'} }
    '{0} {1:yyyy-MM-dd HH:mm:ss}  {2,5}  {3,-38} {4}' -f $tag, $_.TimeCreated, $_.Id, $_.ProviderName, (($_.Message -split "`r?`n")[0]) } }
""")

SCRIPTS = [
    N("shared_ipsec_active_policy", "Effective IPsec rules and security associations",
      "Effective connection security rules and live main/quick-mode associations; no policy changes.",
      need_admin("IPsec effective policy and security associations require elevation") + r"""foreach ($cmd in 'Get-NetIPsecRule','Get-NetIPsecMainModeSA','Get-NetIPsecQuickModeSA') {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { "NOT AVAILABLE: $cmd is not installed."; continue }
  try {
    "== $cmd"
    $rows = if ($cmd -eq 'Get-NetIPsecRule') { @(& $cmd -PolicyStore ActiveStore -ErrorAction Stop | Where-Object Enabled -eq 'True') } else { @(& $cmd -ErrorAction Stop) }
    if (-not $rows.Count) { '[INFO] No entries returned; no active association can be normal when traffic is idle.' }
    if ($cmd -eq 'Get-NetIPsecRule') { $rows | Select-Object DisplayName, Profile, Mode, InboundSecurity, OutboundSecurity, PolicyStoreSourceType | Format-Table -AutoSize -Wrap | Out-String -Width 220 }
    else { $rows | Select-Object Name, LocalEndpoint, RemoteEndpoint, LocalPort, RemotePort, Protocol, CipherAlgorithm, HashAlgorithm | Format-Table -AutoSize -Wrap | Out-String -Width 220 }
  } catch { "ERROR: ${cmd}: $($_.Exception.Message)" }
}""", admin=True, tags=('diagnostic', 'network', 'security')),
    N("shared_wlan_saved_profiles", "Saved WLAN profile names (no passwords)",
      "Lists saved WLAN profile names using netsh; never requests clear-text keys or exports profiles.",
      r"""try {
  $tool = Join-Path $env:SystemRoot 'System32\netsh.exe'
  $output = & $tool wlan show profiles 2>&1; $code = $LASTEXITCODE
  $output
  if ($code -ne 0) { "NOT AVAILABLE: WLAN profile query exited $code; WLAN service or hardware may be absent." }
  else { '[INFO] Profiles visible to this user; profile presence does not imply a current connection.' }
} catch { "ERROR: Saved WLAN profiles: $($_.Exception.Message)" }""",
      tags=('diagnostic', 'network', 'inventory')),
    N("shared_smb_inbound_open_files", "Inbound SMB sessions and open files",
      "Shows remote clients holding local files open, with session and file IDs; never closes sessions or handles.",
      need_admin("server-side SMB sessions and open files require elevation") + r"""foreach ($cmd in 'Get-SmbSession','Get-SmbOpenFile') {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { "NOT AVAILABLE: $cmd is absent."; continue }
  try {
    "== $cmd"
    $rows = @(& $cmd -ErrorAction Stop)
    if (-not $rows.Count) { '[INFO] No inbound SMB records returned.' }
    if ($cmd -eq 'Get-SmbSession') { $rows | Select-Object SessionId, ClientComputerName, ClientUserName, NumOpens, Dialect, SecondsIdle | Format-Table -AutoSize -Wrap | Out-String -Width 220 }
    else { $rows | Select-Object FileId, SessionId, ClientComputerName, ClientUserName, Path, Permissions, Locks | Format-Table -AutoSize -Wrap | Out-String -Width 220 }
  } catch { "ERROR: ${cmd}: $($_.Exception.Message)" }
}
'[INFO] These are connections into this machine, not files opened by this machine on another server.'""",
      admin=True, tags=('diagnostic', 'network', 'storage', 'triage')),
    N("shared_firewall_recent_records", "Firewall log: last 50 lines per active log path",
      "Tails distinct configured log paths for profiles with logging enabled, preserving raw fields and headers.",
      need_admin("firewall log files require elevation") + r"""try {
  $profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore -ErrorAction Stop)
  $active = @($profiles | Where-Object { $_.LogAllowed -eq 'True' -or $_.LogBlocked -eq 'True' })
  if (-not $active.Count) { '[INFO] No profile currently enables allowed or dropped packet logging.'; return }
  $active | Select-Object Name, LogAllowed, LogBlocked, LogFileName | Format-Table -AutoSize -Wrap | Out-String -Width 220
  foreach ($path in @($active | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_.LogFileName) } | Where-Object { $_ } | Select-Object -Unique)) {
    try {
      "== $path"
      if (-not (Test-Path -LiteralPath $path -ErrorAction Stop)) { '[INFO] Configured log not present yet; logging can be enabled before a file is created.'; continue }
      Get-Content -LiteralPath $path -Tail 50 -ErrorAction Stop
    } catch { "ERROR: Log read: $($_.Exception.Message)" }
  }
} catch { "ERROR: Firewall logging configuration: $($_.Exception.Message)" }""",
      admin=True, tags=('diagnostic', 'network', 'security', 'triage')),
    N("shared_udp_endpoint_owners", "UDP endpoints and owning processes",
      "IPv4 and IPv6 UDP bindings with PID and process name, including loopback; binding alone does not prove exposure.",
      r"""try {
  $names = @{}; Get-Process -ErrorAction Stop | ForEach-Object { $names[$_.Id] = $_.ProcessName }
  $endpoints = @(Get-NetUDPEndpoint -ErrorAction Stop)
  if (-not $endpoints.Count) { '[INFO] No UDP endpoints returned.'; return }
  $endpoints | Sort-Object LocalPort, LocalAddress | Select-Object LocalAddress, LocalPort, OwningProcess, @{n='Process';e={if ($names.ContainsKey([int]$_.OwningProcess)) { $names[[int]$_.OwningProcess] } else { '(exited or unresolved)' }}} | Format-Table -AutoSize | Out-String -Width 220
  '[INFO] UDP has no TCP-style listening state; firewall, interface and routing restrictions still apply.'
} catch { "ERROR: UDP endpoint inventory: $($_.Exception.Message)" }""",
      tags=('diagnostic', 'network', 'security')),
    N("adapter_error_counters", "Adapter errors and discarded packets",
      "Checks receive/transmit error and discard counters since the adapter counters last reset; totals are not current error rates.",
      CHK + r"""try {
  $stats = @(Get-NetAdapterStatistics -ErrorAction Stop)
  if (-not $stats.Count) { Chk $null 'Adapter counters' 'no adapters returned'; return }
  foreach ($s in $stats) {
    $values = @($s.ReceivedPacketErrors, $s.OutboundPacketErrors, $s.ReceivedDiscardedPackets, $s.OutboundDiscardedPackets)
    $detail = "RX errors=$($s.ReceivedPacketErrors); TX errors=$($s.OutboundPacketErrors); RX discards=$($s.ReceivedDiscardedPackets); TX discards=$($s.OutboundDiscardedPackets); cumulative counters"
    if (@($values | Where-Object { $null -eq $_ }).Count) { Chk $null $s.Name 'driver did not report all four counters' }
    else { Chk (-not @($values | Where-Object { $_ -gt 0 }).Count) $s.Name $detail }
  }
} catch { Chk $null 'Adapter counters' $_.Exception.Message }""",
      tags=('network', 'performance', 'health')),
    N("tcp_state_process_summary", "TCP connections grouped by state and process",
      "Checks TCP socket counts by state and owning process, including CloseWait and TimeWait; counts alone do not indicate a fault.",
      CHK + r"""try {
  $connections = @(Get-NetTCPConnection -ErrorAction Stop)
  $names = @{}
  Get-Process -ErrorAction Stop | ForEach-Object { $names[$_.Id] = $_.ProcessName }
  Chk $null 'TCP snapshot' "$($connections.Count) sockets; process exits can leave unresolved PIDs; TimeWait PID 0 is normal"
  $connections | Group-Object State, OwningProcess | Sort-Object Count -Descending | ForEach-Object {
    $socket = $_.Group[0]
    $name = $names[[int]$socket.OwningProcess]
    if (-not $name) { $name = '(unresolved / kernel)' }
    [pscustomobject]@{State=$socket.State; PID=$socket.OwningProcess; Process=$name; Count=$_.Count}
  } | Format-Table -AutoSize | Out-String -Width 220
} catch { Chk $null 'TCP connections' $_.Exception.Message }""",
      tags=('network', 'diagnostic', 'triage')),
    N("network_disconnects_7d", "Recent network disconnect events",
      "Checks NetworkProfile disconnects (10001) and WLAN disconnects (8003) in the last seven days, showing the newest 100 per log.",
      CHK + r"""foreach ($query in @(
  @{LogName='Microsoft-Windows-NetworkProfile/Operational'; Id=10001},
  @{LogName='Microsoft-Windows-WLAN-AutoConfig/Operational'; Id=8003}
)) {
  $log = $query.LogName
  try {
    $metadata = Get-WinEvent -ListLog $log -ErrorAction Stop
    if (-not $metadata.IsEnabled) { Chk $null $log 'channel disabled; history may be incomplete' }
    $query.StartTime = (Get-Date).AddDays(-7)
    $events = @(Get-WinEvent -FilterHashtable $query -MaxEvents 100 -ErrorAction Stop)
    Chk $null $log "$($events.Count) newest disconnect event(s), capped at 100; sleep and intentional disconnects are normal"
    $events | Select-Object TimeCreated, Id, Message | Format-Table -Wrap | Out-String -Width 220
  } catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { Chk $null $log 'no retained disconnect events in seven days' }
    else { Chk $null $log $_.Exception.Message }
  }
}""", tags=('network', 'diagnostic', 'triage')),
    N("default_route_metrics", "Default routes and combined interface metrics",
      "Checks IPv4 and IPv6 default routes with route plus interface metrics, exposing competing gateways and VPN route priority.",
      CHK + r"""try {
  $interfaces = @(Get-NetIPInterface -ErrorAction Stop)
  $routes = @(Get-NetRoute -ErrorAction Stop | Where-Object { $_.DestinationPrefix -in '0.0.0.0/0','::/0' })
  Chk ([bool]$routes.Count) 'Default routes' "$($routes.Count) route(s); more-specific routes take precedence over defaults"
  $routes | ForEach-Object {
    $route = $_
    $iface = $interfaces | Where-Object { $_.InterfaceIndex -eq $route.InterfaceIndex -and $_.AddressFamily -eq $route.AddressFamily } | Select-Object -First 1
    $metric = if ($iface) { [long]$route.RouteMetric + [long]$iface.InterfaceMetric } else { $null }
    [pscustomobject]@{Family=$route.AddressFamily; Adapter=$route.InterfaceAlias; NextHop=$route.NextHop; RouteMetric=$route.RouteMetric; InterfaceMetric=$iface.InterfaceMetric; TotalMetric=$metric; State=$iface.ConnectionState}
  } | Sort-Object Family, TotalMetric | Format-Table -AutoSize | Out-String -Width 220
} catch { Chk $null 'Default routes' $_.Exception.Message }""",
      tags=('network', 'diagnostic', 'health')),
    N("dns_nrpt_effective", "Effective DNS namespace policies (NRPT)",
      "Checks effective Name Resolution Policy Table namespaces, DNS servers and DNSSEC requirements used by split DNS and DirectAccess.",
      CHK + r"""try {
  $rules = @(Get-DnsClientNrptPolicy -Effective -ErrorAction Stop)
  Chk $null 'Effective NRPT' "$($rules.Count) namespace policy entry/entries; none is normal on unmanaged clients"
  $rules | Select-Object Namespace, NameServers, DirectAccessDnsServers, DnsSecValidationRequired, DnsSecQueryIPsecRequired | Format-List | Out-String -Width 220
} catch { Chk $null 'Effective NRPT' $_.Exception.Message }""",
      tags=('network', 'security', 'diagnostic')),
    N("dns_suffix_registration", "DNS suffix search and registration settings",
      "Checks global suffix search order and per-interface suffixes, dynamic DNS registration and connection-specific suffix registration.",
      CHK + r"""try {
  Get-DnsClientGlobalSetting -ErrorAction Stop | Select-Object SuffixSearchList, UseDevolution, DevolutionLevel | Format-List | Out-String -Width 220
  $clients = @(Get-DnsClient -ErrorAction Stop)
  Chk $null 'DNS client registration' "$($clients.Count) interface(s); suffix and registration requirements depend on the network"
  $clients | Select-Object InterfaceAlias, InterfaceIndex, ConnectionSpecificSuffix, RegisterThisConnectionsAddress, UseSuffixWhenRegistering | Format-Table -AutoSize -Wrap | Out-String -Width 220
} catch { Chk $null 'DNS suffix and registration settings' $_.Exception.Message }""",
      tags=('network', 'diagnostic', 'inventory')),
    # ---- Event logs --------------------------------------------------------------------------
    E("sys_errors_24h", "System errors in the last 24 hours",
      "Critical and error events from the System log in the last day, newest first, one line "
      "each.",
      _EVENTS + "Show @{LogName='System'; Level=1,2; StartTime=(Get-Date).AddDays(-1)} 100",
      tags=('diagnostic', 'triage', 'health')),
    E("app_errors_24h", "Application errors in the last 24 hours",
      "Critical and error events from the Application log in the last day.",
      _EVENTS + "Show @{LogName='Application'; Level=1,2; StartTime=(Get-Date).AddDays(-1)} 100",
      tags=('diagnostic', 'triage', 'health')),
    E("sys_warnings_7d", "System warnings in the last 7 days",
      "Warnings from the System log in the last week -- disk, NTFS, DNS client, time "
      "service problems often start as warnings.",
      _EVENTS + "Show @{LogName='System'; Level=3; StartTime=(Get-Date).AddDays(-7)} 150",
      tags=('diagnostic', 'triage', 'health')),
    E("top_error_sources", "Most frequent error sources (7 days)",
      "Errors grouped by source and event id, most frequent first -- one noisy driver "
      "stands out immediately instead of being 400 lines in a list.",
      T(r"""Get-WinEvent -FilterHashtable @{LogName='System','Application'; Level=1,2; StartTime=(Get-Date).AddDays(-7)} -ErrorAction SilentlyContinue |
  Group-Object LogName, ProviderName, Id | Sort-Object Count -Descending | Select-Object -First 25 Count, @{n='Log, source, id';e={$_.Name}}, @{n='Last';e={($_.Group | Select-Object -First 1).TimeCreated}}"""),
      tags=('diagnostic', 'triage')),
    E("app_crashes", "Application crashes and hangs (30 days)",
      "Every program that crashed (Application Error 1000) or stopped responding "
      "(Application Hang 1002), grouped by program with the faulting module.",
      T(r"""Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000,1002; StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue | ForEach-Object {
  [pscustomobject]@{ Kind=$(if ($_.Id -eq 1000) {'crash'} else {'hang'}); App=$_.Properties[0].Value; Module=$(if ($_.Id -eq 1000) { $_.Properties[3].Value } else { '' }); Time=$_.TimeCreated } } |
  Group-Object Kind, App, Module | Sort-Object Count -Descending | Select-Object Count, Name, @{n='Last';e={($_.Group | Sort-Object Time -Descending | Select-Object -First 1).Time}}"""),
      tags=('diagnostic', 'triage')),
    E("bsod_history", "Blue screens (bugchecks)",
      "Every bugcheck Windows recorded (event 1001 BugCheck) with its stop code and "
      "parameters, plus the minidumps on disk.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-WER-SystemErrorReporting'; Id=1001} -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No blue screen recorded in the System log.' } else { $e | ForEach-Object { "[BAD]  $($_.TimeCreated)  $(($_.Message -split "`r?`n")[0])" } }
''
'Minidumps:'
Get-ChildItem "$env:SystemRoot\Minidump" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | ForEach-Object { '{0}  {1,8:N0} KB  {2}' -f $_.LastWriteTime, ($_.Length/1KB), $_.Name }""",
      tags=('diagnostic', 'triage', 'health')),
    E("unexpected_shutdowns", "Unexpected shutdowns and power loss",
      "Kernel-Power 41 (rebooted without a clean shutdown) and EventLog 6008 (previous "
      "shutdown was unexpected), with the bugcheck code 41 recorded -- 0 means power loss or a "
      "hard reset, not a blue screen.",
      r"""Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,6008} -MaxEvents 30 -ErrorAction SilentlyContinue | ForEach-Object {
  if ($_.Id -eq 41) { "[BAD]  $($_.TimeCreated)  Kernel-Power 41 -- bugcheck code $($_.Properties[0].Value) $(if ($_.Properties[0].Value -eq 0) {'(power loss / hard reset / hang)'})" }
  else { "[WARN] $($_.TimeCreated)  6008 -- $(($_.Message -split "`r?`n")[0])" } }""",
      tags=('diagnostic', 'triage', 'health')),
    E("disk_errors", "Disk, NTFS and storage controller errors",
      "Bad blocks (disk 7), controller resets (storahci/stornvme 129), NTFS corruption (55, "
      "98) and delayed write failures (140) -- the events that come before a disk dies.",
      _EVENTS + "Show @{LogName='System'; ProviderName='disk','Ntfs','storahci','stornvme','volmgr','Microsoft-Windows-Ntfs','iaStorAC'; Level=1,2,3; StartTime=(Get-Date).AddDays(-30)} 100",
      tags=('diagnostic', 'triage', 'storage', 'health')),
    E("driver_load_failures", "Drivers and services that failed to start",
      "Service Control Manager errors 7000/7001/7009/7023/7031/7034 -- services that failed "
      "or crashed -- and driver load failures, in the last 7 days.",
      _EVENTS + "Show @{LogName='System'; ProviderName='Service Control Manager'; Id=7000,7001,7009,7011,7023,7024,7031,7034,7043; StartTime=(Get-Date).AddDays(-7)} 100",
      tags=('diagnostic', 'triage', 'health')),
    E("wu_events", "Windows Update installs and failures",
      "Update installs (19), failures (20) and restarts required (21/22) from the Windows "
      "Update client, in the last 30 days.",
      _EVENTS + "Show @{LogName='System'; ProviderName='Microsoft-Windows-WindowsUpdateClient'; StartTime=(Get-Date).AddDays(-30)} 100",
      tags=('diagnostic', 'triage', 'health')),
    E("whea", "Hardware errors (WHEA)",
      "Machine Check and PCIe errors Windows logged through WHEA-Logger: corrected errors "
      "are early warnings of failing RAM, CPU or a PCIe link; fatal ones crashed the PC.",
      _EVENTS + "Show @{LogName='System'; ProviderName='Microsoft-Windows-WHEA-Logger'} 100",
      tags=('diagnostic', 'triage', 'health')),
    E("gpu_resets", "GPU driver timeouts (TDR)",
      "Display driver stopped responding and recovered (Display 4101) and related LiveKernel "
      "reports -- the 'screen went black for a second' events.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Display'; Id=4101} -MaxEvents 30 -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No display driver timeouts (4101) recorded.' } else { $e | ForEach-Object { "[WARN] $($_.TimeCreated)  $(($_.Message -split "`r?`n")[0])" } }
Get-ChildItem "$env:SystemRoot\LiveKernelReports" -Recurse -Filter *.dmp -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 15 | ForEach-Object { "[INFO] LiveKernelReport $($_.LastWriteTime)  $($_.Directory.Name)\$($_.Name)" }""",
      tags=('diagnostic', 'triage', 'health')),
    E("event_log_sizes", "Event log sizes and retention",
      "Every non-empty event log with its size, maximum size and record count -- which logs "
      "are full and overwriting history.",
      T("Get-WinEvent -ListLog * -ErrorAction SilentlyContinue | Where-Object RecordCount -gt 0 | Sort-Object FileSize -Descending | Select-Object -First 40 LogName, @{n='MB';e={[math]::Round($_.FileSize/1MB,1)}}, @{n='MaxMB';e={[math]::Round($_.MaximumSizeInBytes/1MB)}}, RecordCount, LogMode, IsEnabled"),
      note="a few logs (Security) are only listed elevated",
      tags=('diagnostic', 'triage')),
    E("logon_history", "Sign-in history",
      "Interactive, remote and unlock sign-ins (4624 types 2, 7, 10, 11) and failures (4625) "
      "in the last 7 days, from the Security log.",
      need_admin("the Security log is only readable elevated") +
      r"""$t = @{'2'='interactive';'7'='unlock';'10'='remote (RDP)';'11'='cached'}
Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4624,4625; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 3000 -ErrorAction SilentlyContinue | ForEach-Object {
  $d = @{}; ([xml]$_.ToXml()).Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }
  if ($t.ContainsKey([string]$d.LogonType)) {
    '{0} {1:yyyy-MM-dd HH:mm}  {2,-14} {3}\{4}  from {5}' -f $(if ($_.Id -eq 4625) {'[WARN]'} else {'[INFO]'}), $_.TimeCreated, $t[[string]$d.LogonType], $d.TargetDomainName, $d.TargetUserName, $d.IpAddress } } | Select-Object -First 200""",
      admin=True,
      tags=('diagnostic', 'triage', 'health')),
    E("powershell_activity", "PowerShell scripts run recently",
      "The last 40 script blocks PowerShell logged (4104) with their first line -- what ran, "
      "as which process. Needs script block logging (on here).",
      r"""Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-PowerShell/Operational'; Id=4104} -MaxEvents 200 -ErrorAction SilentlyContinue |
  Where-Object { $_.Properties[0].Value -eq 1 } | Select-Object -First 40 | ForEach-Object {
  $tag = if ($_.Level -eq 3) {'[WARN]'} else {'[INFO]'}
  '{0} {1:yyyy-MM-dd HH:mm:ss}  {2}' -f $tag, $_.TimeCreated, ((($_.Properties[2].Value) -replace '\s+',' ').Trim().Substring(0, [Math]::Min(150, (($_.Properties[2].Value) -replace '\s+',' ').Trim().Length))) }""",
      tags=('diagnostic', 'triage', 'health')),
    # ---- Network -----------------------------------------------------------------------------
    N("adapters_ip", "Adapters and IP addresses",
      "Every connected adapter with its IPv4/IPv6 addresses, gateway and DNS servers.",
      CHK + r"""$configs = @(Get-NetIPConfiguration -ErrorAction Stop | Where-Object { $_.NetAdapter.Status -eq 'Up' })
$configs | Select-Object InterfaceAlias, @{n='IPv4';e={($_.IPv4Address.IPAddress) -join ', '}}, @{n='IPv6';e={($_.IPv6Address.IPAddress) -join ', '}}, @{n='Gateway';e={($_.IPv4DefaultGateway.NextHop) -join ', '}}, @{n='DNS';e={($_.DNSServer.ServerAddresses) -join ', '}} | Format-Table -AutoSize | Out-String -Width 220
if (-not $configs.Count) { Chk $null 'APIPA on active adapters' 'no UP adapters'; return }
$apipa = @($configs | ForEach-Object { $c = $_; $_.IPv4Address | Where-Object IPAddress -like '169.254.*' | ForEach-Object { "$($c.InterfaceAlias)=$($_.IPAddress)" } })
Chk (-not $apipa.Count) 'No APIPA on active adapters' $(if ($apipa.Count) { $apipa -join '; ' } else { 'no 169.254.x.x addresses on UP adapters' })""",
      tags=('network', 'diagnostic', 'health')),
    N("connectivity", "Internet connectivity test",
      "Gateway ping, DNS resolution, an HTTPS request and Microsoft's own connectivity probe "
      "(the one the network icon uses) -- which layer is broken.",
      CHK + r"""$gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1).NextHop
if ($gw) { Chk (Test-Connection $gw -Count 2 -Quiet) 'Default gateway' $gw } else { Chk $false 'Default gateway' 'no default route' }
try { $r = Resolve-DnsName www.microsoft.com -ErrorAction Stop | Where-Object IPAddress | Select-Object -First 1; Chk ([bool]$r) 'DNS' "www.microsoft.com -> $($r.IPAddress)" } catch { Chk $false 'DNS' $_.Exception.Message }
try { $w = Invoke-RestMethod 'http://www.msftconnecttest.com/connecttest.txt' -TimeoutSec 8; Chk ($w -eq 'Microsoft Connect Test') 'NCSI probe' "$w" } catch { Chk $false 'NCSI probe' $_.Exception.Message }
try { $null = [Net.Sockets.TcpClient]::new('www.microsoft.com', 443); Chk $true 'HTTPS port 443' 'reachable' } catch { Chk $false 'HTTPS port 443' $_.Exception.Message }""",
      timeout=90,
      tags=('network', 'diagnostic', 'health')),
    N("public_ip", "Public IP address",
      "The address the internet sees for this PC (asked of two independent services), and "
      "whether it differs from the local one -- behind NAT or a VPN.",
      r"""foreach ($u in 'https://api.ipify.org','https://ifconfig.me/ip') { try { '{0,-28} {1}' -f $u, (Invoke-RestMethod $u -TimeoutSec 8) } catch { "ERROR: $u -- $($_.Exception.Message)" } }""",
      timeout=60,
      tags=('network', 'diagnostic')),
    N("dns_servers", "DNS servers per adapter",
      "Which DNS servers each adapter uses, IPv4 and IPv6. A rogue or stale DNS server here "
      "redirects everything.",
      T("Get-DnsClientServerAddress | Where-Object ServerAddresses | Select-Object InterfaceAlias, AddressFamily, @{n='Servers';e={$_.ServerAddresses -join ', '}}"),
      tags=('network', 'diagnostic')),
    N("dns_resolution_test", "DNS resolution test",
      "Resolves a set of well-known names through the configured DNS and directly through "
      "1.1.1.1, timing both -- slow or failing DNS is the most common 'internet is slow'.",
      r"""foreach ($n in 'www.microsoft.com','www.google.com','login.microsoftonline.com','github.com') {
  foreach ($s in $null, '1.1.1.1') { $t = Measure-Command { try { $r = if ($s) { Resolve-DnsName $n -Server $s -DnsOnly -ErrorAction Stop } else { Resolve-DnsName $n -DnsOnly -ErrorAction Stop }; $ok = $true } catch { $ok = $false } }
    '{0} {1,-28} via {2,-10} {3,6:N0} ms' -f $(if ($ok) {'[OK]  '} else {'[BAD] '}), $n, $(if ($s) {$s} else {'system'}), $t.TotalMilliseconds } }""",
      timeout=90,
      tags=('network', 'diagnostic', 'health')),
    N("hosts_file", "Hosts file entries",
      "Active entries in the hosts file (comments skipped). An entry for a Microsoft, "
      "antivirus or bank domain here is a classic malware redirect.",
      r"""$p = "$env:SystemRoot\System32\drivers\etc\hosts"
$lines = Get-Content $p -ErrorAction SilentlyContinue | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') }
if (-not $lines) { '[OK]   No active entries -- the hosts file overrides nothing.' } else { $lines | ForEach-Object { "[WARN] $_" } }""",
      tags=('network', 'diagnostic', 'health')),
    N("proxy", "Proxy settings",
      "The WinHTTP proxy (services, Windows Update) and this user's WinINet proxy and PAC "
      "script (browsers, most apps). A stale proxy breaks updates while browsing works.",
      r"""netsh winhttp show proxy
''
$i = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
"User proxy enabled : $([bool]$i.ProxyEnable)"
"User proxy server  : $($i.ProxyServer)"
"Bypass list        : $($i.ProxyOverride)"
"PAC script         : $($i.AutoConfigURL)" """,
      tags=('network', 'diagnostic')),
    N("wifi_current", "Current Wi-Fi connection",
      "SSID, BSSID, signal, radio type (802.11ax...), channel, band and link rates of the "
      "current wireless connection.",
      "netsh wlan show interfaces", kind=CMD,
      tags=('network', 'diagnostic')),
    N("wifi_drivers", "Wi-Fi driver capabilities",
      "What the wireless driver supports: radio types, WPA3, hosted network, bands.",
      "netsh wlan show drivers", kind=CMD,
      tags=('network', 'diagnostic')),
    N("firewall_blocked_inbound", "Inbound firewall rules that ALLOW from anywhere",
      "Enabled inbound allow rules with no remote-address restriction, by program and port "
      "-- what is reachable from the network if a port is listening.",
      T(r"""Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True -ErrorAction SilentlyContinue | ForEach-Object {
  $a = $_ | Get-NetFirewallAddressFilter; $p = $_ | Get-NetFirewallPortFilter; $app = $_ | Get-NetFirewallApplicationFilter
  if ($a.RemoteAddress -contains 'Any' -and $p.Protocol -in 'TCP','UDP') { [pscustomobject]@{ Rule=$_.DisplayName; Profile=$_.Profile; Proto=$p.Protocol; Port=($p.LocalPort -join ','); Program=$app.Program } } } | Sort-Object Profile, Rule"""),
      timeout=240,
      tags=('network', 'security', 'diagnostic', 'compliance')),
    N("smb_client", "SMB client: mapped drives and connections",
      "Mapped network drives and open SMB connections with the dialect in use (SMB 1 here is "
      "a finding).",
      r"""$m = Get-SmbMapping -ErrorAction SilentlyContinue
if ($m) { $m | Select-Object LocalPath, RemotePath, Status | Format-Table -AutoSize | Out-String -Width 220 } else { '[INFO] No mapped network drives.' }
$c = Get-SmbConnection -ErrorAction SilentlyContinue
if ($c) { $c | Select-Object ServerName, ShareName, UserName, Dialect | Format-Table -AutoSize | Out-String -Width 220 } else { '[INFO] No open SMB connections.' }""",
      tags=('network', 'diagnostic')),
    N("smb1_status", "SMBv1 status",
      "Whether the SMB 1.0 client and server are installed or enabled. SMBv1 is what "
      "WannaCry spread over; nothing modern needs it.",
      CHK + r"""$s = Get-SmbServerConfiguration -ErrorAction SilentlyContinue
if ($s) { Chk (-not $s.EnableSMB1Protocol) 'SMB1 server protocol' "EnableSMB1Protocol=$($s.EnableSMB1Protocol)" }
# Get-WindowsOptionalFeature THROWS for a standard user, whatever -ErrorAction says.
$isAdm = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$f = if ($isAdm) { Get-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -ErrorAction SilentlyContinue }
if ($f) { Chk ($f.State -ne 'Enabled') 'SMB1 feature' $f.State } elseif ($isAdm) { Chk $true 'SMB1 feature' 'not part of this Windows build' } else { Chk $null 'SMB1 feature' 'needs administrator to read' }""",
      note="the optional-feature state needs elevation", admin=True,
      tags=('network', 'security', 'diagnostic', 'health')),
    N("network_profile", "Network location (Public / Private / Domain)",
      "The category Windows assigned each connected network, which decides which firewall "
      "profile applies. A home network left Public blocks file sharing and casting.",
      T("Get-NetConnectionProfile | Select-Object InterfaceAlias, Name, NetworkCategory, IPv4Connectivity, IPv6Connectivity"),
      tags=('network', 'diagnostic')),
    N("arp_neighbors", "Neighbours on the local network (ARP)",
      "IP and MAC addresses this PC has talked to on the local network, reachable or stale.",
      T("Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.State -ne 'Unreachable' -and $_.IPAddress -notmatch '^(224|239|255)\\.' } | Sort-Object InterfaceAlias, IPAddress | Select-Object InterfaceAlias, IPAddress, LinkLayerAddress, State"),
      tags=('network', 'diagnostic')),
    N("established", "Established connections by program",
      "Every open TCP connection to a remote address, with the owning program -- 'what is "
      "this PC talking to right now'.",
      T(r"""$p = @{}; Get-Process | ForEach-Object { $p[$_.Id] = $_.ProcessName }
Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | Where-Object { $_.RemoteAddress -notin '127.0.0.1','::1' } | Sort-Object OwningProcess |
  Select-Object @{n='Process';e={$p[[int]$_.OwningProcess]}}, OwningProcess, LocalPort, RemoteAddress, RemotePort"""),
      note="process names of system processes may be blank",
      tags=('network', 'diagnostic')),
    N("trace_route", "Route to the internet (traceroute)",
      "The routers between this PC and 1.1.1.1, with latency per hop -- where the delay or "
      "the break is.",
      "tracert -d -w 800 -h 20 1.1.1.1", kind=CMD, timeout=180,
      tags=('network', 'diagnostic')),
    N("ping_quality", "Latency and packet loss",
      "20 pings each to the gateway and to 1.1.1.1 with min/avg/max and loss -- jitter and "
      "loss show a bad Wi-Fi link or ISP problem.",
      r"""$gw = (Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1).NextHop
foreach ($t in @($gw, '1.1.1.1') | Where-Object { $_ }) {
  $r = Test-Connection $t -Count 20 -ErrorAction SilentlyContinue
  $ok = @($r).Count; $loss = 100 - 5 * $ok
  if ($ok) { $m = $r | Measure-Object ResponseTime -Minimum -Maximum -Average; '{0} {1,-16} loss {2,3}%  min {3} ms  avg {4:N1} ms  max {5} ms' -f $(if ($loss -gt 0) {'[WARN]'} else {'[OK]  '}), $t, $loss, $m.Minimum, $m.Average, $m.Maximum }
  else { "[BAD]  $t -- no replies" } }""", timeout=120,
      tags=('network', 'diagnostic', 'health')),
    N("vpn_connections", "VPN connections",
      "Windows' own VPN profiles (Settings > VPN) with their server and tunnel type, and any "
      "VPN adapters from third-party clients.",
      r"""Get-VpnConnection -ErrorAction SilentlyContinue | Select-Object Name, ServerAddress, TunnelType, ConnectionStatus, SplitTunneling | Format-Table -AutoSize | Out-String -Width 220
Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.InterfaceDescription -match 'VPN|WireGuard|TAP|Wintun|OpenVPN|Tunnel' } | Select-Object Name, InterfaceDescription, Status | Format-Table -AutoSize | Out-String -Width 220""",
      tags=('network', 'diagnostic')),
    N("ipv6_status", "IPv6 status",
      "Whether IPv6 is bound per adapter, any global IPv6 address, and the DisabledComponents "
      "policy (disabling IPv6 by registry breaks some Windows features).",
      r"""Get-NetAdapterBinding -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue | Select-Object Name, Enabled | Format-Table -AutoSize | Out-String -Width 220
Get-NetIPAddress -AddressFamily IPv6 -ErrorAction SilentlyContinue | Where-Object { $_.PrefixOrigin -ne 'WellKnown' -and $_.IPAddress -notlike 'fe80*' } | Select-Object InterfaceAlias, IPAddress | Format-Table -AutoSize | Out-String -Width 220
$d = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' -ErrorAction SilentlyContinue).DisabledComponents
"DisabledComponents: $(if ($null -eq $d) { 'not set (IPv6 fully on)' } else { '0x{0:X}' -f $d })" """,
      tags=('network', 'diagnostic')),
    N("mtu", "MTU per interface",
      "The MTU each interface uses. A VPN or PPPoE link with a too-large MTU causes pages that "
      "half-load.",
      T("Get-NetIPInterface -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object ConnectionState -eq 'Connected' | Select-Object InterfaceAlias, NlMtu, InterfaceMetric, Dhcp"),
      tags=('network', 'diagnostic')),
    N("tcp_settings", "TCP global settings",
      "Receive window auto-tuning, RSS, ECN and congestion provider -- auto-tuning disabled by "
      "an old 'speed tweak' caps download speed.",
      "netsh int tcp show global", kind=CMD,
      tags=('network', 'diagnostic')),
    N("net_shares_remote", "Network discovery and file sharing",
      "Whether network discovery and file and printer sharing firewall rules are enabled "
      "for the current profile.",
      T("Get-NetFirewallRule -Group '@FirewallAPI.dll,-32752','@FirewallAPI.dll,-28502' -ErrorAction SilentlyContinue | Group-Object Group, Profile, Enabled | Select-Object Count, Name"),
      tags=('network', 'security', 'diagnostic', 'compliance')),
    N("dhcp_lease", "DHCP lease details",
      "For each DHCP adapter: the DHCP server, lease obtained and expiry -- 'my IP keeps "
      "changing' and duplicate-address problems.",
      T("Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'DHCPEnabled=TRUE AND IPEnabled=TRUE' | Select-Object Description, DHCPServer, DHCPLeaseObtained, DHCPLeaseExpires, @{n='IP';e={$_.IPAddress -join ', '}}"),
      tags=('network', 'diagnostic')),
    N("winsock_catalog", "Winsock providers (LSP)",
      "Layered service providers in the Winsock catalog. Anything not from Microsoft here "
      "intercepts all network traffic -- old adware and some VPNs do this.",
      r"""netsh winsock show catalog | Select-String 'Description:' | ForEach-Object { $d = $_.Line.Split(':',2)[1].Trim(); if ($d -match 'MSAFD|Hyper-V RAW|RSVP') { "[OK]   $d" } else { "[WARN] $d" } } | Sort-Object -Unique""",
      tags=('network', 'diagnostic', 'health')),
    N("listening_firewall", "Listening ports and whether the firewall lets them in",
      "Each listening TCP port with the program and whether any enabled inbound allow rule "
      "matches its program -- exposure at a glance.",
      r"""$allow = Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True -ErrorAction SilentlyContinue | Get-NetFirewallApplicationFilter | ForEach-Object Program | Where-Object { $_ -and $_ -ne 'Any' } | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_).ToLower() }
$procs = @{}; Get-Process | ForEach-Object { $procs[$_.Id] = $_ }
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalAddress -notin '127.0.0.1','::1' } | Sort-Object LocalPort -Unique | ForEach-Object {
  $p = $procs[[int]$_.OwningProcess]; $path = if ($p -and $p.Path) { $p.Path.ToLower() } else { '' }
  $tag = if ($path -and $allow -contains $path) { '[WARN] allowed by a rule' } else { '[INFO] no program rule' }
  '{0,-26} {1,-15} {2,6}  {3}' -f $tag, $_.LocalAddress, $_.LocalPort, $(if ($p) { $p.ProcessName } else { "pid $($_.OwningProcess)" }) }""",
      timeout=180,
      tags=('network', 'security', 'diagnostic', 'health')),
]

SCRIPTS += _DEEP_NETWORK_SCRIPTS

_TS_NETWORK_EVENTS = CHK + r"""function TsNetworkEvents($filter, $max=300) {
  try {
    $meta=Get-WinEvent -ListLog $filter.LogName -ErrorAction Stop
    if (-not $meta.IsEnabled) { Chk $null $filter.LogName 'channel disabled; retained history incomplete' | Out-Host }
    $events=@(Get-WinEvent -FilterHashtable $filter -MaxEvents $max -ErrorAction Stop)
    if (-not $events.Count) { Chk $null $filter.LogName 'no retained matching events' | Out-Host }
    if ($events.Count -eq $max) { Chk $null $filter.LogName "newest $max records only" | Out-Host }
    $events
  } catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { Chk $null $filter.LogName 'no retained matching events' | Out-Host }
    else { Chk $null $filter.LogName "read unavailable/refused: $($_.Exception.Message)" | Out-Host }
  }
}
function TsNetworkData($event) { $d=@{}; ([xml]$event.ToXml()).Event.EventData.Data | ForEach-Object { if ($_.Name) { $d[$_.Name]=$_.'#text' } }; return $d }
"""

SCRIPTS += [
    N("ts-dns-resolution-timeline", "DNS timeline by configured resolver",
      "Times five targets per DNS server and compares address sets, with NRPT context for split DNS.",
      CHK + r"""try {
  $configs=@(Get-DnsClientServerAddress -ErrorAction Stop | Where-Object ServerAddresses)
  if (-not $configs.Count) { throw 'no configured resolver addresses returned' }
  $configs | Select-Object InterfaceAlias,ServerAddresses | Format-Table -AutoSize | Out-String -Width 220
  $servers=@($configs.ServerAddresses | Sort-Object -Unique)
  if ($servers.Count -gt 4) { Chk $null 'Resolver scope' 'first four unique servers only to bound runtime'; $servers=@($servers | Select-Object -First 4) }
} catch { Chk $null 'DNS configuration' $_.Exception.Message; return }
try {
  $nrpt=@(Get-DnsClientNrptPolicy -Effective -ErrorAction Stop)
  if (-not $nrpt.Count) { Chk $null 'NRPT' 'no effective namespace policies returned' }
  $nrpt | Select-Object Namespace,NameServers,DirectAccessDnsServers | Format-List | Out-String -Width 220
} catch { Chk $null 'NRPT unavailable' $_.Exception.Message }
$rows=@(); $budget=[Diagnostics.Stopwatch]::StartNew()
foreach ($target in 'www.microsoft.com','login.microsoftonline.com','www.google.com','www.cloudflare.com','www.bing.com') {
  foreach ($server in $servers) {
    if ($budget.Elapsed.TotalSeconds -gt 75) { Chk $null "$target via $server" 'skipped: overall DNS measurement budget reached'; continue }
    $watch=[Diagnostics.Stopwatch]::StartNew()
    try {
      $answer=@(Resolve-DnsName $target -Type A -Server $server -DnsOnly -NoHostsFile -QuickTimeout -ErrorAction Stop | Where-Object IPAddress)
      if (-not $answer.Count) { throw 'no A records returned' }
      $addresses=($answer.IPAddress | Sort-Object -Unique) -join ','
      $rows += [pscustomobject]@{Time=(Get-Date); Target=$target; Server=$server; Milliseconds=[math]::Round($watch.Elapsed.TotalMilliseconds); Answers=$addresses}
      Chk ($watch.Elapsed.TotalMilliseconds -lt 1000) "$target via $server" "$addresses; $([math]::Round($watch.Elapsed.TotalMilliseconds)) ms"
    } catch { Chk $null "$target via $server" "resolution failed or refused after $([math]::Round($watch.Elapsed.TotalMilliseconds)) ms: $($_.Exception.Message)" }
  }
  $sets=@($rows | Where-Object Target -eq $target | Select-Object -ExpandProperty Answers -Unique)
  Chk $null "$target answer comparison" $(if ($sets.Count -gt 1) { 'different address sets: possible split DNS or normal CDN/geographic rotation; compare intended namespace policy' } elseif ($sets.Count -eq 1) { 'successful replies agree; failed/skipped servers remain unknown' } else { 'no comparable successful answers' })
}
$rows | Format-Table -Wrap | Out-String -Width 220
'[INFO] Explicit-server DNS queries bypass normal resolver selection; these are query timings, not guaranteed uncached wire latency. Public targets cannot prove an internal namespace is healthy.'""",
      timeout=120, tags=('diagnostic', 'network', 'performance', 'triage', 'health')),
    N("ts-tcp-health-rates", "TCP retransmissions, resets and failures",
      "Correlates netstat -s with IPv4/IPv6 TCP counter deltas and socket states over 15 seconds.",
      CHK + r"""try {
  $native=@(& "$env:SystemRoot\System32\netstat.exe" -s -p tcp 2>&1); $exit=$LASTEXITCODE
  $native; if ($exit -ne 0 -or -not $native.Count) { Chk $null 'netstat -s' "exit=$exit; output lines=$($native.Count)" }
} catch { Chk $null 'netstat -s' $_.Exception.Message }
'[INFO] Typed TCP statistics below use the same OS counters without parsing localized netstat labels. ResetConnections counts established resets, not every RST packet.'
$ip=[Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties(); $first=@{}
foreach ($family in 4,6) {
  try { $first[$family]=if ($family -eq 4) { $ip.GetTcpIPv4Statistics() } else { $ip.GetTcpIPv6Statistics() } }
  catch { Chk $null "IPv$family baseline" $_.Exception.Message }
}
$watch=[Diagnostics.Stopwatch]::StartNew(); Start-Sleep -Seconds 15
foreach ($family in 4,6) {
  if (-not $first.ContainsKey($family)) { continue }
  try {
    $last=if ($family -eq 4) { $ip.GetTcpIPv4Statistics() } else { $ip.GetTcpIPv6Statistics() }; $d=@{}
    foreach ($field in 'SegmentsSent','SegmentsResent','ResetConnections','FailedConnectionAttempts','ConnectionsInitiated','ConnectionsAccepted') {
      $d[$field]=[long]$last.$field-[long]$first[$family].$field
      if ($d[$field] -lt 0) { throw "counter reset: $field" }
    }
    $seconds=$watch.Elapsed.TotalSeconds
    $ratio=if ($d.SegmentsSent -gt 0) { 100*$d.SegmentsResent/$d.SegmentsSent } else { $null }
    $d | Format-Table -AutoSize | Out-String -Width 220
    Chk $(if ($null -eq $ratio) { $null } else { $ratio -lt 1 }) "IPv$family retransmissions" ("{0} retransmits; {1:N2}/s; ratio={2}% (blank means no sent segments); 1% is a review threshold" -f $d.SegmentsResent,($d.SegmentsResent/$seconds),$ratio)
    Chk ($d.FailedConnectionAttempts -eq 0) "IPv$family failures" ("{0} failures ({1:N2}/s), {2} resets ({3:N2}/s); initiated={4}, accepted={5}; failures are host-wide, not attributable to sockets" -f $d.FailedConnectionAttempts,($d.FailedConnectionAttempts/$seconds),$d.ResetConnections,($d.ResetConnections/$seconds),$d.ConnectionsInitiated,$d.ConnectionsAccepted)
  } catch { Chk $null "IPv$family deltas" $_.Exception.Message }
}
try {
  $connections=@(Get-NetTCPConnection -ErrorAction Stop)
  if (-not $connections.Count) { Chk $null 'Sockets' 'no rows returned' }
  $connections | Group-Object State | Select-Object Count,Name | Format-Table -AutoSize | Out-String -Width 220
} catch { Chk $null 'Socket context' $_.Exception.Message }""",
      timeout=120, tags=('diagnostic', 'network', 'performance', 'triage', 'health')),
    N("ts-adapter-reset-history", "Adapter resets with driver context (7 days)",
      "Joins NDIS/TCPIP and adapter-provider events with current adapter, driver and error-counter evidence.",
      _TS_NETWORK_EVENTS + r"""try {
  $adapters=@(Get-NetAdapter -IncludeHidden -ErrorAction Stop)
  if (-not $adapters.Count) { Chk $null 'Adapter inventory' 'no adapters returned' }
  $adapters | Select-Object Name,InterfaceDescription,InterfaceGuid,Status,DriverVersion,DriverDate | Format-Table -Wrap | Out-String -Width 220
} catch { Chk $null 'Adapter inventory' $_.Exception.Message }
$events=@(TsNetworkEvents @{LogName='System';StartTime=(Get-Date).AddDays(-7)} 5000 | Where-Object { $_.ProviderName -match 'NDIS|Tcpip|Netwtw|e1[drex]|e2[fn]|rt640|rtwl|NetAdapter|mlx|bnxt' })
if (-not $events.Count) { Chk $null 'Reset evidence' 'no matching provider events in bounded System sample; older history may exist' }
foreach ($e in $events | Select-Object -First 100) {
  $d=TsNetworkData $e
  [pscustomobject]@{Time=$e.TimeCreated; Provider=$e.ProviderName; ID=$e.Id; Level=$e.LevelDisplayName; AdapterData=($d | Out-String).Trim(); Message=$e.Message} | Format-List | Out-String -Width 220
}
foreach ($log in 'Microsoft-Windows-NDIS/Operational','Microsoft-Windows-TCPIP/Operational') {
  TsNetworkEvents @{LogName=$log;StartTime=(Get-Date).AddDays(-7)} 50 | Select-Object TimeCreated,Id,Message | Format-List | Out-String -Width 220
}
try {
  $stats=@(Get-NetAdapterStatistics -ErrorAction Stop)
  if (-not $stats.Count) { Chk $null 'Adapter error counters' 'no rows returned' }
  $stats | Select-Object Name,ReceivedPacketErrors,OutboundPacketErrors,ReceivedDiscardedPackets,OutboundDiscardedPackets | Format-Table -AutoSize | Out-String -Width 220
} catch { Chk $null 'Adapter error counters' $_.Exception.Message }
Chk $null 'Interpretation' 'not every provider event is a reset; review message and adapter GUID, then align driver changes, sleep/resume and incident time. Driver-specific providers and disabled logs can leave gaps.'""",
      tags=('diagnostic', 'network', 'hardware', 'triage')),
    N("ts-wifi-disconnect-timeline", "Wi-Fi disconnect and reconnect timeline",
      "Correlates WLAN connect/disconnect events, session lengths and native WLAN reason-code descriptions.",
      _TS_NETWORK_EVENTS + r"""try {
  if (-not ('TsWlanReason' -as [type])) {
    Add-Type -TypeDefinition 'using System; using System.Text; using System.Runtime.InteropServices; public static class TsWlanReason { [DllImport("wlanapi.dll", CharSet=CharSet.Unicode)] public static extern uint WlanReasonCodeToString(uint code, uint size, StringBuilder text, IntPtr reserved); }' -ErrorAction Stop
  }
} catch { Chk $null 'Reason decoder' $_.Exception.Message }
$events=@(TsNetworkEvents @{LogName='Microsoft-Windows-WLAN-AutoConfig/Operational';Id=8001,8002,8003,11000,11001,11002;StartTime=(Get-Date).AddDays(-7)} 500)
$sessions=@{}
foreach ($e in $events | Sort-Object TimeCreated) {
  $d=TsNetworkData $e; $key=[string]$d.InterfaceGuid
  if (-not $key) { $key=[string]$d.InterfaceDescription }
  $duration=$null
  if ($e.Id -eq 8001 -and $key) { $sessions[$key]=$e.TimeCreated }
  if ($e.Id -eq 8003 -and $key -and $sessions.ContainsKey($key)) { $duration=($e.TimeCreated-$sessions[$key]).TotalSeconds; $sessions.Remove($key) }
  $reason='NOT AVAILABLE: numeric reason absent'; $code=[uint32]0
  if ($d.ReasonCode -and [uint32]::TryParse([string]$d.ReasonCode,[ref]$code)) {
    try {
      $text=[Text.StringBuilder]::new(1024); $status=[TsWlanReason]::WlanReasonCodeToString($code,1024,$text,[IntPtr]::Zero)
      $reason=if ($status -eq 0 -and $text.Length) { $text.ToString() } else { "Unknown WLAN code $code (decoder status $status)" }
    } catch { $reason="NOT AVAILABLE: decoder: $($_.Exception.Message)" }
  } elseif ($d.Reason) { $reason=[string]$d.Reason }
  [pscustomobject]@{Time=$e.TimeCreated; ID=$e.Id; Interface=$key; SSID=$d.SSID; Profile=$d.ProfileName; ConnectedSeconds=$duration; ReasonCode=$d.ReasonCode; Reason=$reason} | Format-List | Out-String -Width 220
}
Chk $null 'Wi-Fi interpretation' 'blank duration means no preceding connection in retained sample. WLAN reason codes differ from IEEE 802.11 reason codes; intentional roaming/sleep can disconnect normally. Inspect repeated short sessions.'""",
      tags=('diagnostic', 'network', 'triage')),
    N("ts-vpn-tunnel-health", "VPN tunnel traffic, duration and MTU",
      "Joins user/machine VPN profiles to interfaces, byte counters, MTU and retained RasClient connection events.",
      _TS_NETWORK_EVENTS + r"""$profiles=@()
foreach ($allUsers in $false,$true) {
  try {
    $p=if ($allUsers) { @(Get-VpnConnection -AllUserConnection -ErrorAction Stop) } else { @(Get-VpnConnection -ErrorAction Stop) }
    if (-not $p.Count) { Chk $null "VPN profiles allUsers=$allUsers" 'none returned in this scope' }
    $profiles += $p
  } catch { Chk $null "VPN profiles allUsers=$allUsers" $_.Exception.Message }
}
$history=@(TsNetworkEvents @{LogName='Application';ProviderName='RasClient';Id=20225,20226;StartTime=(Get-Date).AddDays(-30)} 300)
foreach ($p in $profiles) {
  "== $($p.Name); server=$($p.ServerAddress); status=$($p.ConnectionStatus); tunnel=$($p.TunnelType); split=$($p.SplitTunneling)"
  if ($p.ConnectionStatus -ne 'Connected') { continue }
  try {
    $interfaces=@(Get-NetIPInterface -InterfaceAlias $p.Name -ErrorAction Stop)
    if (-not $interfaces.Count) { throw 'no matching IP interface (profile name may differ)' }
    $interfaces | Select-Object InterfaceAlias,InterfaceIndex,AddressFamily,NlMtu,ConnectionState | Format-Table -AutoSize | Out-String -Width 220
    $nic=@([Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | Where-Object Name -eq $p.Name)
    if (-not $nic.Count) { throw 'no managed interface statistics for this profile name' }
    foreach ($n in $nic) { $s=$n.GetIPStatistics(); "[INFO] $($n.Name): received=$($s.BytesReceived) B sent=$($s.BytesSent) B; cumulative since interface counters reset" }
  } catch { Chk $null "$($p.Name) interface/statistics" $_.Exception.Message }
  $match=@($history | Where-Object { $d=TsNetworkData $_; @($d.Values) -contains $p.Name } | Sort-Object TimeCreated -Descending)
  if ($match.Count -and $match[0].Id -eq 20225) { Chk $null 'Observed tunnel duration' "$([math]::Round(((Get-Date)-$match[0].TimeCreated).TotalMinutes,1)) minutes since newest matching RasClient success; event-based estimate, not live RAS duration" }
  else { Chk $null 'Tunnel duration' 'no unbroken matching connection event in retained history' }
}
try {
  $other=@(Get-NetAdapter -IncludeHidden -ErrorAction Stop | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -match 'VPN|WireGuard|TAP|Wintun|OpenVPN|Tunnel' })
  if (-not $other.Count) { Chk $null 'Third-party adapters' 'no active adapters matched name heuristic; vendor clients may use other names' }
  $other | Select-Object Name,InterfaceDescription,ifIndex,Status | Format-Table -AutoSize | Out-String -Width 220
} catch { Chk $null 'Third-party adapter context' $_.Exception.Message }
Chk $null 'VPN assessment' 'traffic counters and MTU do not prove remote reachability; test a known internal endpoint separately. No tunnel was connected or reset.'""",
      admin=True, note='machine VPN profiles and some adapter reads may require elevation', tags=('diagnostic', 'network', 'triage', 'health')),
    N("ts-smb-session-security", "SMB sessions: dialect, signing and encryption",
      "Joins active outbound SMB connections, mapped paths and client policy without inferring negotiated security from policy.",
      CHK + r"""try {
  $config=Get-SmbClientConfiguration -ErrorAction Stop
  if (-not $config) { throw 'client configuration empty' }
  $config | Select-Object RequireSecuritySignature,EnableSecuritySignature,EnableInsecureGuestLogons,RequireEncryption | Format-List | Out-String -Width 220
} catch { Chk $null 'SMB client policy' $_.Exception.Message }
$maps=@()
try { $maps=@(Get-SmbMapping -ErrorAction Stop); if (-not $maps.Count) { Chk $null 'Mapped drives' 'no mappings in this logon context' } }
catch { Chk $null 'Mapped drives' $_.Exception.Message }
try {
  $connections=@(Get-SmbConnection -ErrorAction Stop)
  if (-not $connections.Count) { Chk $null 'SMB connections' 'none returned in this logon context; elevation can use a different mapping context'; return }
  foreach ($c in $connections) {
    $signed=if ($null -ne $c.Signed) { [bool]$c.Signed } else { $null }
    $encrypted=if ($null -ne $c.Encrypted) { [bool]$c.Encrypted } else { $null }
    $paths=@($maps | Where-Object { $_.RemotePath -eq "\\$($c.ServerName)\$($c.ShareName)" })
    [pscustomobject]@{Server=$c.ServerName; Share=$c.ShareName; Dialect=$c.Dialect; Signed=$signed; Encrypted=$encrypted; Opens=$c.NumOpens; LocalPaths=($paths.LocalPath -join ',')} | Format-List | Out-String -Width 220
    Chk $(if ($c.Dialect) { "$($c.Dialect)" -notmatch '^1(\.|$)' } else { $null }) "$($c.ServerName) dialect" "$($c.Dialect); SMB1 warrants urgent review"
    Chk $signed 'Negotiated signing' $(if ($null -eq $signed) { 'NOT AVAILABLE: Signed property not exposed by this build; policy is not negotiated state' } else { "$signed; interpret with negotiated encryption and security requirements" })
    Chk $null 'Negotiated encryption' $(if ($null -eq $encrypted) { 'NOT AVAILABLE: Encrypted property not exposed' } else { "$encrypted; whether required depends on data/network policy" })
  }
} catch { Chk $null 'SMB connection read' $_.Exception.Message }""",
      tags=('diagnostic', 'network', 'storage', 'security', 'triage')),
]
# Round 2: independent network evidence with explicit coverage limits.
SCRIPTS += [
    N('ts2-tls-chain-validator', 'TLS chains for busiest live HTTPS destinations',
      'Checks up to ten connected port-443 destinations with bounded TLS handshakes, hostname context and online revocation validation.',
      CHK + r"""try {
  $connections=@(Get-NetTCPConnection -State Established -RemotePort 443 -ErrorAction Stop)
  if (-not $connections.Count) { throw 'no established TCP port-443 destinations; QUIC and explicit proxies are outside scope' }
  $destinations=@($connections | Group-Object RemoteAddress | Sort-Object Count -Descending | Select-Object -First 10)
} catch { Chk $null 'HTTPS destination inventory (netstat-equivalent TCP table)' $_.Exception.Message; return }
$cache=@()
try { $cache=@(Get-DnsClientCache -ErrorAction Stop); if (-not $cache.Count) { Chk $null 'Hostname context' 'DNS cache empty' } }
catch { Chk $null 'DNS cache hostname context' $_.Exception.Message }
try {
  if (-not ('Ts2TlsProbe' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Net;
using System.Net.Sockets;
using System.Net.Security;
using System.Security.Authentication;
using System.Security.Cryptography.X509Certificates;
using System.Collections.Generic;
using System.Threading.Tasks;
public static class Ts2TlsProbe {
  public sealed class Result { public string Endpoint, Target, Policy, Error; public string[] Chain; public bool Authenticated; }
  public static Result Read(string ip,string target) {
    var result=new Result { Endpoint=ip, Target=target, Chain=new string[0] };
    using(var tcp=new TcpClient()) {
      SslStream ssl=null;
      try {
        if(!tcp.ConnectAsync(IPAddress.Parse(ip),443).Wait(2000)) throw new TimeoutException("TCP connect exceeded 2 seconds");
        ssl=new SslStream(tcp.GetStream(),false,(sender,cert,chain,errors)=> {
          result.Policy=errors.ToString(); var lines=new List<string>();
          if(chain!=null) {
            foreach(var status in chain.ChainStatus) lines.Add("ChainStatus="+status.Status+" "+status.StatusInformation.Trim());
            foreach(var element in chain.ChainElements) {
              var c=element.Certificate;
              lines.Add("Subject="+c.Subject+"; Issuer="+c.Issuer+"; Valid="+c.NotBefore.ToUniversalTime().ToString("o")+".."+c.NotAfter.ToUniversalTime().ToString("o")+"; Signature="+c.SignatureAlgorithm.FriendlyName+" ("+c.SignatureAlgorithm.Value+"); Thumbprint="+c.Thumbprint);
            }
          }
          result.Chain=lines.ToArray();
          return errors==SslPolicyErrors.None;
        });
        var auth=ssl.AuthenticateAsClientAsync(target,new X509CertificateCollection(),SslProtocols.None,true);
        if(!auth.Wait(6000)) throw new TimeoutException("TLS/online revocation exceeded 6 seconds; outcome unknown");
        result.Authenticated=ssl.IsAuthenticated;
      } catch(Exception ex) { result.Error=ex.GetBaseException().Message; }
      finally { if(ssl!=null) ssl.Dispose(); }
    }
    return result;
  }
}
'@ -ErrorAction Stop
  }
} catch { Chk $null 'TLS probe initialization' $_.Exception.Message; return }
foreach ($d in $destinations) {
  $ip=$d.Name
  $names=@($cache | Where-Object { [string]$_.Data -eq $ip -and ($_.Entry -or $_.RecordName) } | ForEach-Object { if ($_.Entry) { $_.Entry } else { $_.RecordName } } | Sort-Object -Unique)
  $target=if ($names.Count) { [string]$names[0] } else { $ip }
  "== Endpoint=$ip connections=$($d.Count) owning PIDs=$($d.Group.OwningProcess -join ',') target/SNI=$target DNS-cache candidates=$($names -join ',')"
  try {
    $r=[Ts2TlsProbe]::Read($ip,$target)
    $r.Chain
    $ok=if ($r.Authenticated -and $names.Count -eq 1) { $true } elseif ($r.Policy -and $r.Policy -ne 'None') { $false } else { $null }
    Chk $ok 'Observed TLS validation' "authenticated=$($r.Authenticated); policy=$($r.Policy); error=$($r.Error); online revocation requested using caller trust/proxy context"
    if (-not $r.Chain.Count) { Chk $null 'Certificate chain' 'NOT AVAILABLE: handshake did not deliver a chain before failure/timeout' }
    if (@($r.Chain | Where-Object { $_ -match 'Signature=(md5|sha1)' }).Count) { Chk $false 'Legacy certificate signature' 'MD5/SHA1 observed; inspect affected chain element and applicable trust policy' }
    if ($names.Count -ne 1) { Chk $null 'Server identity' 'NOT AVAILABLE: no unique DNS-cache hostname; IP/SNI mismatch may be expected. Cached DNS is not evidence of the original client SNI.' }
  } catch { Chk $null $ip $_.Exception.Message }
}
Chk $null 'Pinning and revocation limits' 'Application pinning policy is NOT AVAILABLE from a socket/certificate. Compare thumbprints/public-key pins with application configuration and error logs; do not bypass certificate validation. RevocationStatusUnknown/OfflineRevocation is unavailable evidence, not a revoked certificate. A valid chain here does not reproduce application trust stores, client certificates, TLS interception or proxy routing.'
Chk $null 'Scope / next step' 'Top ten by simultaneous connection count, not traffic. Fresh direct TCP/TLS probes send no HTTP request and honor validation failures. Review expiry/NotTimeValid, weak signatures, UntrustedRoot/PartialChain and Revoked statuses. No trust store, protocol policy or proxy settings changed.'""",
      timeout=120, admin=True, note='DNS cache and some socket ownership evidence can require elevation', tags=('diagnostic', 'network', 'security', 'health', 'triage')),
    N('ts2-winsock-provider-catalog', 'Winsock LSP / namespace provider trust inventory',
      'Combines native Winsock catalog output with 32/64-bit provider registry entries and DLL signature/publisher evidence.',
      CHK + r"""try {
  $native=@(& "$env:SystemRoot\System32\netsh.exe" winsock show catalog 2>&1); $code=$LASTEXITCODE
  $native
  if ($code -ne 0 -or -not $native.Count) { Chk $null 'Native Winsock catalog' "NOT AVAILABLE: exit=$code lines=$($native.Count)" }
} catch { Chk $null 'Winsock catalog' $_.Exception.Message }
$base='HKLM:\SYSTEM\CurrentControlSet\Services\WinSock2\Parameters'; $paths=@{}
foreach ($catalog in 'Protocol_Catalog9','NameSpace_Catalog5') {
  foreach ($entries in 'Catalog_Entries','Catalog_Entries64') {
    $root="$base\$catalog\$entries"
    try {
      if (-not (Test-Path -LiteralPath $root -ErrorAction Stop)) { "[INFO] $root -- catalog view absent"; continue }
      $keys=@(Get-ChildItem -LiteralPath $root -ErrorAction Stop)
      if (-not $keys.Count) { Chk $null $root 'catalog entries empty' }
      foreach ($key in $keys) {
        $v=Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction Stop
        $dll=$null
        if ($catalog -eq 'NameSpace_Catalog5') {
          $dll=$v.LibraryPath
          if ($dll -is [byte[]]) { $dll=[Text.Encoding]::Unicode.GetString($dll).TrimEnd([char]0) }
          "[INFO] NSP entry=$($key.Name); enabled=$($v.Enabled); display=$($v.DisplayString); provider=$($v.ProviderId)"
        } elseif ($v.PackedCatalogItem -is [byte[]] -and $v.PackedCatalogItem.Length -ge 260) {
          $dll=[Text.Encoding]::ASCII.GetString($v.PackedCatalogItem,0,260).Split([char]0)[0]
          "[INFO] LSP/protocol entry=$($key.Name); DLL candidate=$dll"
        }
        if (-not $dll -or $dll -notmatch '\.dll$') { Chk $null $key.Name 'NOT AVAILABLE: provider path not decoded; catalog registry encoding is implementation-specific; inspect native catalog above'; continue }
        $path=[Environment]::ExpandEnvironmentVariables([string]$dll)
        # A 32-bit catalog can name System32; resolve its actual redirected DLL in a 64-bit host.
        if ($entries -eq 'Catalog_Entries' -and [Environment]::Is64BitOperatingSystem -and [Environment]::Is64BitProcess -and $path.StartsWith("$env:SystemRoot\System32\",[StringComparison]::OrdinalIgnoreCase)) { $path="$env:SystemRoot\SysWOW64\"+$path.Substring(("$env:SystemRoot\System32\").Length) }
        $paths[$path]=$true
      }
    } catch { Chk $null $root "catalog read incomplete/refused: $($_.Exception.Message)" }
  }
}
if (-not $paths.Count) { Chk $null 'Provider binaries' 'NOT AVAILABLE: no decoded provider DLL paths' }
foreach ($path in $paths.Keys) {
  try {
    $file=Get-Item -LiteralPath $path -ErrorAction Stop
    $sig=Get-AuthenticodeSignature -LiteralPath $path -ErrorAction Stop
    $subject=$sig.SignerCertificate.Subject
    $ms=$sig.Status -eq 'Valid' -and $subject -match '(?:^|,\s*)O=Microsoft Corporation(?:,|$)'
    [pscustomobject]@{Path=$path; Company=$file.VersionInfo.CompanyName; Version=$file.VersionInfo.FileVersion; Signature=$sig.Status; Signer=$subject} | Format-List | Out-String -Width 220
    Chk $ms 'Provider trust review' 'non-Microsoft or unverifiable DLL warrants provenance review; VPN, endpoint security and name-resolution products can install legitimate providers. CompanyName alone is untrusted metadata.'
  } catch { Chk $null $path "binary/signature read unavailable: $($_.Exception.Message)" }
}
Chk $null 'Next step' 'Match native catalog GUID, chain length/order and namespace type to decoded registry entries, then verify DLL signer and approved software inventory. Catalog presence does not prove interception or malware. Modern filtering may use WFP rather than LSP. No Winsock reset/catalog change performed.'""",
      timeout=120, tags=('diagnostic', 'network', 'security', 'inventory', 'triage')),
    N('ts2-network-stack-tuning', 'Network stack tuning audit against client baselines',
      'Reads global TCP/offload state, transport templates and adapter RSS/RSC settings with version-qualified baseline guidance.',
      CHK + r"""try {
  $os=Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
  if (-not $os) { throw 'OS inventory empty' }
  "[INFO] OS=$($os.Caption) build=$($os.BuildNumber); defaults depend on build, transport template, NIC capabilities and policy. Baselines below are review guidance, not a factory-default attestation."
} catch { Chk $null 'OS baseline context' $_.Exception.Message }
foreach ($query in @(@('interface','tcp','show','global'),@('interface','tcp','show','supplemental'),@('interface','tcp','show','heuristics'),@('interface','tcp','show','chimney'),@('interface','tcp','show','netdma'))) {
  try {
    "== netsh $($query -join ' ')"
    $out=@(& "$env:SystemRoot\System32\netsh.exe" @query 2>&1); $exit=$LASTEXITCODE; $out
    if ($exit -ne 0 -or -not $out.Count) { Chk $null 'Legacy/current netsh query' "NOT AVAILABLE or unsupported on this build: exit=$exit; no default inferred" }
  } catch { Chk $null 'TCP native evidence' $_.Exception.Message }
}
try {
  $templates=@(Get-NetTCPSetting -ErrorAction Stop)
  if (-not $templates.Count) { throw 'no TCP templates returned' }
  foreach ($t in $templates) {
    $t | Select-Object SettingName,AutoTuningLevelLocal,AutoTuningLevelGroupPolicy,AutoTuningLevelEffective,CongestionProvider,EcnCapability,Timestamps,ScalingHeuristics | Format-List | Out-String -Width 220
    $ok=if ($null -eq $t.AutoTuningLevelLocal) { $null } else { [string]$t.AutoTuningLevelLocal -eq 'Normal' }
    Chk $ok "$($t.SettingName) autotuning" 'Normal is the usual Windows client baseline; effective policy can override local. Window scaling is negotiated per connection, not proven by autotuning alone.'
    Chk $null 'ECN / timestamps / congestion provider' 'Common client Internet baseline: ECN disabled, timestamps disabled; congestion provider varies (CUBIC on many modern builds, CTCP on older templates). Review current values with the exact build and workload baseline.'
  }
} catch { Chk $null 'TCP settings' $_.Exception.Message }
try {
  $global=Get-NetOffloadGlobalSetting -ErrorAction Stop
  if (-not $global) { throw 'offload settings empty' }
  $global | Format-List | Out-String -Width 220
  foreach ($field in 'ReceiveSideScaling','ReceiveSegmentCoalescing','Chimney','NetworkDirect') {
    $v=$global.PSObject.Properties[$field]
    if (-not $v -or $null -eq $v.Value) { Chk $null $field 'NOT AVAILABLE: property not exposed on this build'; continue }
    $baseline=switch ($field) { 'ReceiveSideScaling' {'Enabled'} 'ReceiveSegmentCoalescing' {'Enabled'} 'Chimney' {'Disabled'} default {$null} }
    $ok=if ($baseline) { [string]$v.Value -eq $baseline } else {$null}
    Chk $ok $field "current=$($v.Value); typical baseline=$baseline; support varies by NIC/build"
  }
} catch { Chk $null 'Global offloads' $_.Exception.Message }
foreach ($cmd in 'Get-NetAdapterRss','Get-NetAdapterRsc') {
  try {
    $rows=@(& $cmd -ErrorAction Stop)
    if (-not $rows.Count) { throw 'no adapter capability/settings rows returned' }
    "== $cmd"; $rows | Format-List | Out-String -Width 220
  } catch { Chk $null $cmd $_.Exception.Message }
}
Chk $null 'Legacy features and next step' 'TCP Chimney and NetDMA are deprecated/removed on modern clients; unsupported query is NOT AVAILABLE, never interpreted as Enabled or Disabled. Compare configuration and operational state of RSS/RSC per NIC. Changes from a typical baseline may be deliberate; correlate throughput/retransmits before proposing changes. No stack setting changed.'""",
      tags=('diagnostic', 'network', 'performance', 'baseline', 'triage')),
    N('ts2-proxy-pac-forensics', 'Proxy / PAC / WPAD settings and authentication evidence',
      'Decodes connection-setting blobs, machine/user policies and WinHTTP proxy state, then searches retained WinHTTP events for auth failures.',
      _TS_NETWORK_EVENTS + r"""foreach ($query in @(@('winhttp','show','proxy'),@('winhttp','show','advproxy'))) {
  try {
    "== netsh $($query -join ' ')"
    $out=@(& "$env:SystemRoot\System32\netsh.exe" @query 2>&1); $exit=$LASTEXITCODE; $out
    if ($exit -ne 0 -or -not $out.Count) { Chk $null 'WinHTTP proxy' "NOT AVAILABLE: exit=$exit; advproxy may not exist on older builds" }
  } catch { Chk $null 'WinHTTP proxy' $_.Exception.Message }
}
foreach ($scope in 'HKCU:\Software','HKLM:\SOFTWARE') {
  foreach ($suffix in 'Microsoft\Windows\CurrentVersion\Internet Settings','Policies\Microsoft\Windows\CurrentVersion\Internet Settings') {
    $root="$scope\$suffix"
    try {
      if (-not (Test-Path -LiteralPath $root -ErrorAction Stop)) { "[INFO] $root absent; no settings at this scope"; continue }
      $p=Get-ItemProperty -LiteralPath $root -ErrorAction Stop
      "== $root"; $p | Select-Object ProxySettingsPerUser,ProxyEnable,ProxyServer,ProxyOverride,AutoConfigURL,AutoDetect | Format-List | Out-String -Width 220
      foreach ($url in @($p.AutoConfigURL) | Where-Object { $_ }) {
        $uri=$null
        $valid=[Uri]::TryCreate([string]$url,[UriKind]::Absolute,[ref]$uri)
        Chk $valid 'PAC URL syntax' "$url; PAC content is not fetched or executed"
      }
      $conn="$root\Connections"
      if (-not (Test-Path -LiteralPath $conn -ErrorAction Stop)) { Chk $null $conn 'no per-connection blobs'; continue }
      $values=Get-ItemProperty -LiteralPath $conn -ErrorAction Stop
      foreach ($property in $values.PSObject.Properties | Where-Object { $_.Value -is [byte[]] }) {
        $b=[byte[]]$property.Value
        try {
          if ($b.Length -lt 16) { throw 'blob shorter than settings header' }
          $flags=[BitConverter]::ToUInt32($b,8); $offset=12; $parts=@()
          for ($i=0;$i -lt 3;$i++) {
            if ($offset+4 -gt $b.Length) { throw 'truncated string length' }
            $len=[BitConverter]::ToUInt32($b,$offset); $offset+=4
            if ($len -gt $b.Length-$offset) { throw 'string extends past blob' }
            $parts += [Text.Encoding]::Default.GetString($b,$offset,[int]$len); $offset += [int]$len
          }
          [pscustomobject]@{Scope=$root; Connection=$property.Name; Flags=('0x{0:X}' -f $flags); Direct=[bool]($flags -band 1); Proxy=[bool]($flags -band 2); PAC=[bool]($flags -band 4); WPADAutoDetect=[bool]($flags -band 8); Server=$parts[0]; Bypass=$parts[1]; PAC_URL=$parts[2]} | Format-List | Out-String -Width 220
          Chk $null 'Connection blob interpretation' 'documented flags shown from common DefaultConnectionSettings layout; SavedLegacySettings is a backup, not necessarily active. Configured WPAD auto-detect does not prove successful discovery.'
        } catch { Chk $null $property.Name "NOT AVAILABLE: unsupported/truncated connection blob: $($_.Exception.Message)" }
      }
    } catch { Chk $null $root $_.Exception.Message }
  }
}
try {
  $svc=Get-Service WinHttpAutoProxySvc -ErrorAction Stop
  "[INFO] WPAD service=$($svc.Status) startup=$($svc.StartType); trigger-start stopped state alone is normal."
} catch { Chk $null 'WPAD service' $_.Exception.Message }
$events=@(TsNetworkEvents @{LogName='Microsoft-Windows-WinHttp/Operational';StartTime=(Get-Date).AddDays(-7)} 500)
$auth=@($events | Where-Object { $_.ToXml() -match '\b407\b|\b12015\b|0x80072eef|proxy.*auth|login.*fail' })
foreach ($e in $auth) { "== $($e.TimeCreated) event=$($e.Id)"; $e.Message; $e.ToXml() }
if ($auth.Count) { Chk $false 'Proxy authentication leads' "$($auth.Count) retained matches: HTTP 407=proxy authentication required; 12015 / 0x80072EEF=ERROR_WINHTTP_LOGIN_FAILURE. A 407 can be a normal challenge; inspect retries and final outcome." }
else { Chk $null 'Proxy authentication history' 'no matching evidence in bounded retained WinHTTP events; channel may be absent/disabled or tracing never enabled. ETL traces are not started or converted.' }
Chk $null 'Next step / scope' 'Compare WinHTTP (services), WinINet per-connection flags, PAC URL and machine/user policy. Browser-managed proxy settings can differ. HKCU is the executing user; elevated alternate credentials change scope. WPAD discovery success and PAC routing are NOT AVAILABLE without an approved client trace; no PAC execution or configuration changes performed.'""",
      admin=True, note='machine policies and complete WinHTTP event reads may require elevation', timeout=120, tags=('diagnostic', 'network', 'security', 'triage')),
]

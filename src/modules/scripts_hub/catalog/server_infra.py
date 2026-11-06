"""Server infrastructure: DNS, DHCP, file server, storage, IIS, Hyper-V,
failover clustering, RDS, WSUS, certificate services and print server.
Read-only, each guarded with NOT AVAILABLE when its role/module is absent.
"""
from ._base import CHK, EV, L, T, maker, need_admin, need_cmd


def _guarded(category, requires, cmdlet, what):
    make = maker(category, server=True, requires=requires)

    def S(id, title, desc, body, **kw):
        return make(id, title, desc, need_cmd(cmdlet, what) + body, **kw)
    return S


DN = _guarded("DNS Server", "DnsServer module", "Get-DnsServerZone", "the DnsServer module (DNS Server role or RSAT: DNS)")
DH = _guarded("DHCP Server", "DhcpServer module", "Get-DhcpServerv4Scope", "the DhcpServer module (DHCP Server role or RSAT: DHCP)")
FS = _guarded("File server", "SMB server cmdlets", "Get-SmbShare", "the SMB server cmdlets")
FR = _guarded("File server", "FSRM / DFS modules", "Get-FsrmQuota", "the FileServerResourceManager module (FSRM)")
DF = _guarded("File server", "DFSN / DFSR modules", "Get-DfsnRoot", "the DFSN module (DFS Namespaces)")
SG = _guarded("Storage & free space", "Storage cmdlets", "Get-Volume", "the Storage module")
DD = _guarded("Storage & free space", "Data Deduplication", "Get-DedupVolume", "Data Deduplication")
IS = _guarded("IIS", "IIS (WebAdministration)", "Get-Website", "the WebAdministration module (IIS)")
HV = _guarded("Hyper-V", "Hyper-V module", "Get-VM", "the Hyper-V module")
CL = _guarded("Failover Cluster", "FailoverClusters module", "Get-Cluster", "the FailoverClusters module")
RD = _guarded("Remote Desktop Services", "RemoteDesktop module", "Get-RDServer", "the RemoteDesktop module (RDS)")
WS = _guarded("WSUS", "UpdateServices module", "Get-WsusServer", "the UpdateServices module (WSUS)")
CA = _guarded("Certificate Services", "AD CS (certutil -CA)", "certutil.exe", "certutil")
PS = _guarded("Print server", "PrintManagement module", "Get-Printer", "the PrintManagement module")

_HAS_SHARES = ("if (-not (Get-SmbShare | Where-Object { -not $_.Special })) { '[INFO] This computer "
               "shares no folders besides the administrative ones.'; return }\n")

_IS_CA = ("$ca = certutil.exe -cainfo name 2>&1\nif ($LASTEXITCODE -ne 0) { 'NOT AVAILABLE: this "
          "server is not a certificate authority.'; return }\n")

# Local role checks must also work without the deployment-management module.
_RDLOCAL = maker("Remote Desktop Services", server=True)
_CA_LOCAL = need_admin("CA configuration reads require elevation") + r"""try {
  $root = 'HKLM:\SYSTEM\CurrentControlSet\Services\CertSvc\Configuration'
  if (-not (Test-Path -LiteralPath $root -ErrorAction Stop)) { 'NOT AVAILABLE: no local AD CS role.'; return }
  $active = (Get-ItemProperty -LiteralPath $root -ErrorAction Stop).Active
  if (-not $active) { 'NOT AVAILABLE: no active CA.'; return }
  $caKey = Join-Path $root $active
  $caConfig = "$env:COMPUTERNAME\$active"
} catch { "ERROR: CA configuration unreadable: $($_.Exception.Message)"; return }
"""

# Preserve disabled/missing/refused/empty distinctions rather than treating all
# four cases as a clean event log. No log enablement or diagnostic generation.
_EVENT_DETAIL = CHK + r"""function Read-RoleEvents($log, $ids, $days = 7) {
  try {
    $meta = Get-WinEvent -ListLog $log -ErrorAction Stop
    if (-not $meta.IsEnabled) { Chk $null $log 'log disabled; history unavailable'; return }
    $filter = @{LogName=$log; StartTime=(Get-Date).AddDays(-$days)}
    if ($ids) { $filter.Id = $ids } else { $filter.Level = 1,2,3 }
    $events = @(Get-WinEvent -FilterHashtable $filter -MaxEvents 200 -ErrorAction Stop)
    Chk $null $log "$($events.Count) matching records, newest 200 at most; review details"
    $events | Select-Object TimeCreated, Id, ProviderName, Message | Format-List | Out-String -Width 240
  } catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { Chk $null $log "no matching events in $days days; absence does not establish health" }
    else { "ERROR: Log missing or unreadable ${log}: $($_.Exception.Message)" }
  }
}
"""

SCRIPTS = [
    CL("cluster-csv-health-verdicts", "CSV state and capacity health",
       "Checks each shared volume for online state, reported faults and less than 10% free capacity before clustered workloads run out of space.",
       CHK + r"""try {
  $volumes = @(Get-ClusterSharedVolume -ErrorAction Stop)
  if (-not $volumes.Count) { '[INFO] No Cluster Shared Volumes configured.' }
  foreach ($v in $volumes) {
    Chk ($v.State -eq 'Online') $v.Name "state=$($v.State); owner=$($v.OwnerNode)"
    foreach ($info in $v.SharedVolumeInfo) {
      $p = $info.Partition
      if (-not $p -or $p.Size -le 0) { Chk $null $v.Name 'partition size unavailable'; continue }
      $free = 100.0*$p.FreeSpace/$p.Size
      Chk ($free -ge 10 -and -not $info.FaultState) $info.FriendlyVolumeName ("free={0:N1}% ({1:N1} GB); fault={2}; redirected={3}" -f $free, ($p.FreeSpace/1GB), $info.FaultState, $info.RedirectedAccess)
    }
  }
} catch { "ERROR: Cannot read CSV health: $($_.Exception.Message)" }""",
       admin=True, note="cluster information may require elevation", tags=('diagnostic', 'health', 'storage')),
    HV("hyperv-integration-upgrade-review", "Running VMs needing integration review",
       "Highlights running guests whose host-reported integration state is not Up to date; versions alone are not comparable across guest operating systems.",
       CHK + r"""try {
  $vms = @(Get-VM -ErrorAction Stop | Where-Object State -eq 'Running')
  if (-not $vms.Count) { '[INFO] No running VMs.' }
  foreach ($vm in $vms) {
    $state = [string]$vm.IntegrationServicesState
    $ok = if ([string]::IsNullOrWhiteSpace($state)) { $null } elseif ($state -eq 'Up to date') { $true } elseif ($state -eq 'Update required') { $false } else { $null }
    Chk $ok $vm.Name "version=$($vm.IntegrationServicesVersion); reported state=$state; unknown states need guest-specific review"
  }
} catch { "ERROR: Cannot read VM integration state: $($_.Exception.Message)" }""",
       admin=True, note="Hyper-V management may require elevation", tags=('diagnostic', 'health')),
    IS("iis-explicit-recycle-schedules", "IIS explicit recycling schedules and triggers",
       "Lists scheduled recycle times and request/memory triggers to explain unexpected app pool restarts beyond the default interval.",
       r"""try {
  Get-ChildItem IIS:\AppPools -ErrorAction Stop | ForEach-Object {
    $r = $_.recycling; $p = $r.periodicRestart
    [pscustomobject]@{ Pool=$_.Name; Interval=$p.time; Schedule=($p.schedule.Collection | ForEach-Object { $_.value }) -join ', '; Requests=$p.requests; PrivateMemoryKB=$p.privateMemory; VirtualMemoryKB=$p.memory; DisallowOverlap=$r.disallowOverlappingRotation; LogRecycleEvents=$r.logEventOnRecycle }
  } | Format-Table -AutoSize -Wrap | Out-String -Width 220
} catch { "ERROR: Cannot read recycling schedules: $($_.Exception.Message)" }""",
       admin=True, note="IIS configuration reads may require elevation", tags=('inventory', 'performance', 'triage')),
    DN("dns-zone-transfer-targets", "DNS zone transfer destinations and notifications",
       "Shows explicit secondary and notification destinations, flagging unrestricted primary-zone transfers for security review.",
       CHK + r"""try {
  $zones = @(Get-DnsServerZone -ErrorAction Stop | Where-Object { $_.ZoneType -eq 'Primary' -and -not $_.IsAutoCreated })
  foreach ($z in $zones) {
    $mode = [string]$z.SecureSecondaries
    $ok = if ($mode -in 'NoTransfer','TransferToZoneNameServer','TransferToSecureServers') { $true } elseif ($mode -eq 'TransferAnyServer') { $false } else { $null }
    Chk $ok $z.ZoneName "mode=$mode; secondaries=$($z.SecondaryServers -join ', '); notify=$($z.Notify); notification targets=$($z.NotifyServers -join ', ')"
  }
  if (-not $zones.Count) { '[INFO] No applicable primary zones.' }
} catch { "ERROR: Cannot read transfer destinations: $($_.Exception.Message)" }""",
       tags=('diagnostic', 'security', 'network')),
    WS("wsus-sync-history-detail", "WSUS sync progress and last synchronization detail",
       "Reports current synchronization progress and the last attempt with errors and update totals to distinguish an active sync from a stalled or failed one.",
       CHK + r"""try {
  $w = Get-WsusServer -ErrorAction Stop; $s = $w.GetSubscription()
  "Current sync status: $($s.GetSynchronizationStatus())"
  $s.GetSynchronizationProgress() | Format-List | Out-String -Width 220
  $last = $s.GetLastSynchronizationInfo()
  if (-not $last -or $last.StartTime -le [datetime]'1900-01-01') { Chk $null 'Last WSUS sync' 'no completed synchronization information'; return }
  $ok = if ([string]$last.Result -eq 'Succeeded') { $true } elseif ([string]$last.Result -eq 'Failed') { $false } else { $null }
  Chk $ok 'Last WSUS sync result' "$($last.Result); started=$($last.StartTime); ended=$($last.EndTime)"
  $last | Format-List | Out-String -Width 220
} catch { "ERROR: Cannot read WSUS synchronization: $($_.Exception.Message)" }""",
       timeout=180, tags=('diagnostic', 'health', 'triage')),
    CA("ca-signing-certificate-expiry", "CA signing certificate expiry and renewal history",
       "Checks certificates referenced by the local CA configuration, including renewal history, for expiry within 60 days.",
       need_admin("local CA configuration and certificate store require elevation") + CHK + r"""try {
  $base = 'HKLM:\SYSTEM\CurrentControlSet\Services\CertSvc\Configuration'
  if (-not (Test-Path -LiteralPath $base -ErrorAction Stop)) { 'NOT AVAILABLE: no local AD CS configuration.'; return }
  $name = (Get-ItemProperty -LiteralPath $base -ErrorAction Stop).Active
  if (-not $name) { 'NOT AVAILABLE: no active certificate authority configured.'; return }
  $hashes = @((Get-ItemProperty -LiteralPath (Join-Path $base $name) -ErrorAction Stop).CACertHash)
  if (-not $hashes.Count) { Chk $null $name 'no CA certificate hashes configured'; return }
  foreach ($hash in $hashes) {
    $thumb = ($hash -replace '\s','')
    try {
      $cert = Get-Item -LiteralPath "Cert:\LocalMachine\My\$thumb" -ErrorAction Stop
      $days = [math]::Floor(($cert.NotAfter-(Get-Date)).TotalDays)
      Chk ($days -gt 60 -and $cert.NotBefore -le (Get-Date)) $name "$($cert.Subject); expires=$($cert.NotAfter); days=$days; thumbprint=$thumb; includes historical renewal certificates"
    } catch { Chk $null $name "certificate ${thumb} unreadable or absent: $($_.Exception.Message)" }
  }
} catch { "ERROR: Cannot inspect CA certificate configuration: $($_.Exception.Message)" }""",
       requires="local AD CS role", admin=True, tags=('diagnostic', 'health', 'security')),
    # ---- DNS Server (12) ---------------------------------------------------------------------
    DN("dns_zone_records", "Record counts per zone",
       "How many records each zone holds, by type.",
       r"""Get-DnsServerZone | Where-Object { -not $_.IsAutoCreated } | ForEach-Object { $z = $_.ZoneName; Get-DnsServerResourceRecord -ZoneName $z -ErrorAction SilentlyContinue | Group-Object RecordType | ForEach-Object { '{0,-40} {1,-6} {2,6}' -f $z, $_.Name, $_.Count } }""", tags=('inventory', 'network')),
    DN("dns_stale_records", "Stale dynamic records (older than 30 days)",
       "Dynamic A records whose timestamp is older than 30 days -- what scavenging would remove.",
       r"""Get-DnsServerZone | Where-Object { $_.ZoneType -eq 'Primary' -and -not $_.IsAutoCreated -and -not $_.IsReverseLookupZone } | ForEach-Object { $z = $_.ZoneName
  Get-DnsServerResourceRecord -ZoneName $z -RRType A -ErrorAction SilentlyContinue | Where-Object { $_.Timestamp -and $_.Timestamp -lt (Get-Date).AddDays(-30) } | ForEach-Object { '{0,-30} {1,-30} {2:yyyy-MM-dd} {3}' -f $z, $_.HostName, $_.Timestamp, $_.RecordData.IPv4Address } }""", tags=('diagnostic', 'network')),
    DN("dns_duplicate_ips", "Several names for one IP",
       "A records sharing an IP address -- stale records left behind by DHCP changes.",
       r"""Get-DnsServerZone | Where-Object { $_.ZoneType -eq 'Primary' -and -not $_.IsReverseLookupZone -and -not $_.IsAutoCreated } | ForEach-Object { $z = $_.ZoneName; Get-DnsServerResourceRecord -ZoneName $z -RRType A -ErrorAction SilentlyContinue | Where-Object HostName -ne '@' | ForEach-Object { [pscustomobject]@{ IP=$_.RecordData.IPv4Address.IPAddressToString; Name="$($_.HostName).$z" } } } |
  Group-Object IP | Where-Object Count -gt 1 | ForEach-Object { '[WARN] {0,-16} {1}' -f $_.Name, (($_.Group.Name) -join ', ') }""", tags=('diagnostic', 'health', 'network', 'triage')),
    DN("dns_reverse_zones", "Reverse lookup zones",
       "Reverse zones present -- missing ones make PTR lookups (and some logging) fail.",
       T("Get-DnsServerZone | Where-Object IsReverseLookupZone | Select-Object ZoneName, ZoneType, IsDsIntegrated, DynamicUpdate"), tags=('inventory', 'network')),
    DN("dns_zone_transfers", "Zone transfer settings",
       "Who may request a zone transfer per zone -- 'to any server' leaks the whole zone.",
       r"""Get-DnsServerZone | Where-Object { $_.ZoneType -eq 'Primary' -and -not $_.IsAutoCreated } | ForEach-Object { '{0} {1,-40} {2}' -f $(if ($_.SecureSecondaries -eq 'TransferAnyServer') {'[WARN]'} else {'[OK]  '}), $_.ZoneName, $_.SecureSecondaries }""", tags=('diagnostic', 'health', 'security', 'network')),
    DN("dns_dynamic_update", "Dynamic update per zone",
       "Whether each zone accepts secure, non-secure or no dynamic updates -- non-secure lets "
       "anyone register any name.",
       r"""Get-DnsServerZone | Where-Object { -not $_.IsAutoCreated } | ForEach-Object { '{0} {1,-40} {2}' -f $(if ($_.DynamicUpdate -eq 'NonsecureAndSecure') {'[WARN]'} else {'[OK]  '}), $_.ZoneName, $_.DynamicUpdate }""", tags=('diagnostic', 'health', 'security', 'network')),
    DN("dns_server_settings", "DNS server settings",
       "Listening addresses, round robin, cache locking, socket pool and the global query "
       "block list.",
       L("Get-DnsServerSetting -All | Select-Object ListeningIPAddress, RoundRobin, LocalNetPriority, EnableDnsSec, NameCheckFlag, BindSecondaries; Get-DnsServerCache | Select-Object LockingPercent, MaxTTL; Get-DnsServerGlobalQueryBlockList"), tags=('inventory', 'network', 'baseline')),
    DN("dns_conditional", "Conditional forwarders",
       "Conditional forwarder zones and their master servers.",
       T("Get-DnsServerZone | Where-Object ZoneType -eq 'Forwarder' | Select-Object ZoneName, IsDsIntegrated, @{n='Masters';e={$_.MasterServers -join ', '}}"), tags=('inventory', 'network')),
    DN("dns_dnssec", "DNSSEC signed zones",
       "Zones signed with DNSSEC and their key status.",
       r"""$z = Get-DnsServerZone | Where-Object IsSigned
if (-not $z) { '[INFO] No DNSSEC-signed zone.'; return }
$z | ForEach-Object { "== $($_.ZoneName)"; Get-DnsServerSigningKey -ZoneName $_.ZoneName -ErrorAction SilentlyContinue | Select-Object KeyType, CryptoAlgorithm, KeyLength, ActiveKey | Format-Table -AutoSize | Out-String -Width 220 }""", tags=('inventory', 'security', 'network')),
    DN("dns_test_self", "DNS server answers itself",
       "Resolves each primary zone's SOA against this server -- a zone loaded but not "
       "answering shows here.",
       r"""Get-DnsServerZone | Where-Object { $_.ZoneType -eq 'Primary' -and -not $_.IsAutoCreated -and -not $_.IsReverseLookupZone } | ForEach-Object { try { $null = Resolve-DnsName $_.ZoneName -Type SOA -Server 127.0.0.1 -DnsOnly -ErrorAction Stop; "[OK]   $($_.ZoneName)" } catch { "[BAD]  $($_.ZoneName) -- $($_.Exception.Message)" } }""", tags=('diagnostic', 'health', 'network')),
    DN("dns_debug_logging", "DNS debug logging configuration",
       "Whether the DNS debug log is on, its file and size -- needed to see who queries what.",
       L("Get-DnsServerDiagnostics | Select-Object Queries, Answers, Notifications, Update, LogFilePath, MaxMBFileSize, EnableLoggingToFile"), tags=('inventory', 'network')),
    DN("dns_events_summary", "DNS Server events (30 days)",
       "The DNS Server log grouped by event over 30 days.",
       EV + "EvSum @{LogName='DNS Server'} 30", tags=('diagnostic', 'health', 'network')),
    # ---- DHCP Server (10) --------------------------------------------------------------------
    DH("dhcp_scope_exhaustion", "Scopes close to exhaustion",
       "Scopes over 80% used, worst first.",
       r"""Get-DhcpServerv4Scope | ForEach-Object { $s = Get-DhcpServerv4ScopeStatistics -ScopeId $_.ScopeId; if ($s.PercentageInUse -ge 80) { '[WARN] {0,-16} {1,-30} {2:N1}% used, {3} free' -f $_.ScopeId, $_.Name, $s.PercentageInUse, $s.Free } }
'(nothing above = every scope under 80%)'""", tags=('diagnostic', 'health', 'network')),
    DH("dhcp_lease_durations", "Lease durations",
       "Lease length per scope -- very long leases exhaust guest/Wi-Fi scopes.",
       T("Get-DhcpServerv4Scope | Select-Object ScopeId, Name, LeaseDuration, State"), tags=('inventory', 'network')),
    DH("dhcp_exclusions", "Exclusion ranges",
       "Excluded address ranges per scope.",
       T("Get-DhcpServerv4Scope | ForEach-Object { Get-DhcpServerv4ExclusionRange -ScopeId $_.ScopeId } | Select-Object ScopeId, StartRange, EndRange"), tags=('inventory', 'network')),
    DH("dhcp_bad_addresses", "Declined (BAD_ADDRESS) leases",
       "Leases marked BAD_ADDRESS -- an IP conflict with a statically configured device.",
       r"""$b = Get-DhcpServerv4Scope | ForEach-Object { Get-DhcpServerv4Lease -ScopeId $_.ScopeId -BadLeases -ErrorAction SilentlyContinue }
if (-not $b) { '[OK]   No BAD_ADDRESS leases.'; return }
$b | ForEach-Object { "[WARN] $($_.IPAddress) in $($_.ScopeId)" }""", tags=('diagnostic', 'health', 'network', 'triage')),
    DH("dhcp_server_settings", "DHCP server settings",
       "Conflict detection attempts, audit logging, database path and backup interval.",
       L("Get-DhcpServerSetting; Get-DhcpServerAuditLog; Get-DhcpServerDatabase"), tags=('inventory', 'network', 'baseline')),
    DH("dhcp_dns_update", "DHCP dynamic DNS update settings",
       "Whether DHCP registers clients in DNS, the credentials it uses, and stale record "
       "cleanup on lease expiry.",
       L("Get-DhcpServerv4DnsSetting; Get-DhcpServerDnsCredential -ErrorAction SilentlyContinue"), tags=('inventory', 'network')),
    DH("dhcp_policies", "DHCP policies",
       "Server and scope policies (vendor class / MAC based option assignment).",
       T("Get-DhcpServerv4Policy -ErrorAction SilentlyContinue | Select-Object Name, ScopeId, Enabled, ProcessingOrder, Condition"), tags=('inventory', 'network')),
    DH("dhcp_v6", "DHCPv6 scopes",
       "IPv6 scopes and their state.",
       T("Get-DhcpServerv6Scope -ErrorAction SilentlyContinue | Select-Object Prefix, Name, State, PreferredLifetime"), tags=('inventory', 'network')),
    DH("dhcp_statistics", "DHCP server statistics",
       "Discovers, offers, requests, acks and naks since the service started -- naks spike "
       "when clients move between subnets.",
       L("Get-DhcpServerv4Statistics"), tags=('inventory', 'performance', 'network')),
    DH("dhcp_audit_today", "Today's DHCP audit log",
       "Today's DHCP audit log summarised by event (new lease, renewal, NAK, conflict...).",
       r"""$p = (Get-DhcpServerAuditLog).Path; $f = Join-Path $p ("DhcpSrvLog-{0}.log" -f (Get-Date).DayOfWeek.ToString().Substring(0,3))
if (-not (Test-Path $f)) { "[INFO] No audit log at $f"; return }
$names = @{'10'='new lease';'11'='renewed';'12'='released';'13'='IP in use (conflict)';'14'='scope full';'15'='denied (NAK)';'16'='deleted';'17'='expired'}
Get-Content $f | Where-Object { $_ -match '^\d+,' } | ForEach-Object { ($_ -split ',')[0] } | Group-Object | Sort-Object Count -Descending | ForEach-Object { '{0,6}  {1,3} {2}' -f $_.Count, $_.Name, $names[$_.Name] }""", tags=('inventory', 'network')),
    # ---- File server (18) --------------------------------------------------------------------
    FS("fs_shares_detail", "Shares with path, users and caching",
       "Every share with its path, current users, folder enumeration mode and offline caching.",
       T("Get-SmbShare | Select-Object Name, Path, CurrentUsers, FolderEnumerationMode, CachingMode, EncryptData, Description"),
       admin=True, tags=('inventory',)),
    FS("fs_share_ntfs", "Share and NTFS permissions",
       "For each non-admin share, the share ACL and the top-level NTFS ACL -- effective access "
       "is the more restrictive of the two.",
       _HAS_SHARES + r"""Get-SmbShare | Where-Object { -not $_.Special -and $_.Path } | ForEach-Object { "== $($_.Name)  ($($_.Path))"
  Get-SmbShareAccess -Name $_.Name | ForEach-Object { "   share: $($_.AccountName) $($_.AccessControlType) $($_.AccessRight)" }
  (Get-Acl $_.Path -ErrorAction SilentlyContinue).Access | ForEach-Object { "   ntfs : $($_.IdentityReference) $($_.AccessControlType) $($_.FileSystemRights)$(if ($_.IsInherited) {' (inherited)'})" } }""",
       admin=True, tags=('inventory', 'security')),
    FS("fs_everyone_access", "Shares open to Everyone / Authenticated Users",
       "Shares where Everyone, Authenticated Users or Domain Users have Change or Full.",
       r"""Get-SmbShare | Where-Object { -not $_.Special } | ForEach-Object { $n = $_.Name; Get-SmbShareAccess -Name $n | Where-Object { $_.AccountName -match 'Everyone|Authenticated Users|Domain Users' -and $_.AccessRight -in 'Full','Change' } | ForEach-Object { "[WARN] $n -- $($_.AccountName): $($_.AccessRight)" } }
'(nothing above = none)'""", tags=('diagnostic', 'health', 'security')),
    FS("fs_open_files_by_user", "Open files grouped by user",
       "How many files each remote user has open on this server.",
       r"""$f = Get-SmbOpenFile -ErrorAction SilentlyContinue
if (-not $f) { '[INFO] No files are open over the network.'; return }
$f | Group-Object ClientUserName | Sort-Object Count -Descending | ForEach-Object { '{0,6}  {1}' -f $_.Count, $_.Name }""",
       admin=True, tags=('inventory',)),
    FS("fs_sessions_dialect", "SMB sessions by dialect",
       "Connected sessions grouped by SMB dialect -- anything below 3.0 has no encryption.",
       r"""$s = Get-SmbSession -ErrorAction SilentlyContinue
if (-not $s) { '[INFO] No SMB sessions.'; return }
$s | Group-Object Dialect | ForEach-Object { '{0} dialect {1,-6} {2} session(s)' -f $(if ([double]$_.Name -lt 3) {'[WARN]'} else {'[OK]  '}), $_.Name, $_.Count }""",
       admin=True, tags=('diagnostic', 'health', 'security')),
    FS("fs_smb1_access_audit", "SMB1 clients (audit)",
       "Whether SMB1 access auditing is on and which clients connected with SMB1 (event 3000) "
       "-- the list to fix before turning SMB1 off.",
       r""""AuditSmb1Access: $((Get-SmbServerConfiguration).AuditSmb1Access)"
""" + EV + "EvList @{LogName='Microsoft-Windows-SMBServer/Audit'; Id=3000} 30 60", tags=('diagnostic', 'health', 'security')),
    FS("fs_smb_server_events", "SMB server errors (7 days)",
       "Errors and warnings from the SMB server operational and security logs.",
       EV + r"""EvSum @{LogName='Microsoft-Windows-SMBServer/Operational'; Level=1,2,3} 7
EvSum @{LogName='Microsoft-Windows-SMBServer/Security'; Level=1,2,3} 7""", tags=('diagnostic', 'health')),
    FS("fs_shadow_schedule", "Shadow copies for shares (previous versions)",
       "Shadow copy count per volume -- what 'Previous versions' can restore.",
       need_admin("Win32_Volume and Win32_ShadowCopy return NOTHING for a standard user "
                "(measured 2026-10-16)") + r"""Get-CimInstance Win32_ShadowCopy -ErrorAction SilentlyContinue | Group-Object VolumeName | ForEach-Object { $v = Get-CimInstance Win32_Volume | Where-Object DeviceID -eq $_.Name; '{0,-4} {1,3} copies, newest {2}' -f $v.DriveLetter, $_.Count, ($_.Group | Sort-Object InstallDate -Descending | Select-Object -First 1).InstallDate }""",
       admin=True, tags=('inventory', 'storage')),
    FS("fs_access_based_enum", "Access-based enumeration",
       "Shares where users only see folders they can open (ABE) and where they see everything.",
       _HAS_SHARES + T("Get-SmbShare | Where-Object { -not $_.Special } | Select-Object Name, FolderEnumerationMode"), tags=('inventory', 'security')),
    FS("fs_large_folders_shares", "Largest folders in each share",
       "The ten biggest top-level folders inside every non-admin share.",
       _HAS_SHARES + r"""Get-SmbShare | Where-Object { -not $_.Special -and $_.Path -and (Test-Path $_.Path) } | ForEach-Object { "== $($_.Name)"; Get-ChildItem $_.Path -Directory -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{ Folder=$_.Name; GB=[math]::Round(((Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum)/1GB,2) } } | Sort-Object GB -Descending | Select-Object -First 10 | Format-Table -AutoSize | Out-String -Width 220 }""",
       timeout=1800, tags=('inventory', 'storage')),
    FS("fs_old_files", "Files untouched for 3 years in shares",
       "Count and size of files not modified in three years, per share -- archive candidates.",
       _HAS_SHARES + r"""$cut = (Get-Date).AddYears(-3)
Get-SmbShare | Where-Object { -not $_.Special -and $_.Path -and (Test-Path $_.Path) } | ForEach-Object { $m = Get-ChildItem $_.Path -Recurse -File -ErrorAction SilentlyContinue | Where-Object LastWriteTime -lt $cut | Measure-Object Length -Sum; '{0,-25} {1,8} files  {2,8:N2} GB' -f $_.Name, $m.Count, ($m.Sum/1GB) }""",
       timeout=1800, tags=('inventory', 'storage')),
    FR("fsrm_quota_usage", "FSRM quotas near their limit",
       "Quotas over 85% used.",
       r"""Get-FsrmQuota | Where-Object { $_.Size -and $_.Usage / $_.Size -ge 0.85 } | ForEach-Object { '[WARN] {0}  {1:N0}% of {2:N1} GB' -f $_.Path, (100 * $_.Usage / $_.Size), ($_.Size/1GB) }
'(nothing above = all quotas under 85%)'""", tags=('diagnostic', 'health', 'storage')),
    FR("fsrm_templates", "FSRM quota and screen templates",
       "Quota templates and file screen templates defined on the server.",
       r"""Get-FsrmQuotaTemplate | Select-Object Name, @{n='GB';e={[math]::Round($_.Size/1GB,1)}}, SoftLimit | Format-Table -AutoSize | Out-String -Width 220
Get-FsrmFileScreenTemplate | Select-Object Name, Active, @{n='Groups';e={$_.IncludeGroup -join ', '}} | Format-Table -AutoSize | Out-String -Width 220""", tags=('inventory', 'storage')),
    FR("fsrm_storage_reports", "FSRM storage reports",
       "Configured storage report jobs and their schedules.",
       T("Get-FsrmStorageReport -ErrorAction SilentlyContinue | Select-Object Name, Namespace, ReportType, Schedule"), tags=('inventory', 'storage')),
    DF("dfsn_targets_state", "DFS namespace targets offline",
       "Namespace folder targets that are offline.",
       r"""Get-DfsnRoot -ErrorAction SilentlyContinue | ForEach-Object { Get-DfsnFolder -Path "$($_.Path)\*" -ErrorAction SilentlyContinue | ForEach-Object { Get-DfsnFolderTarget -Path $_.Path -ErrorAction SilentlyContinue | Where-Object State -ne 'Online' | ForEach-Object { "[WARN] $($_.Path) -> $($_.TargetPath) [$($_.State)]" } } }
'(nothing above = all online)'""", tags=('diagnostic', 'health')),
    DF("dfsn_root_servers", "DFS namespace servers",
       "Root targets (namespace servers) per namespace.",
       T("Get-DfsnRoot -ErrorAction SilentlyContinue | ForEach-Object { Get-DfsnRootTarget -Path $_.Path } | Select-Object Path, TargetPath, State, ReferralPriorityClass"), tags=('inventory',)),
    DF("dfsr_members", "DFS Replication members and folders",
       "Replicated folders, their members and content paths.",
       need_cmd("Get-DfsrMembership", "the DFSR module") + T("Get-DfsrMembership -ErrorAction SilentlyContinue | Select-Object GroupName, FolderName, ComputerName, ContentPath, ReadOnly, StagingPathQuotaInMB"), tags=('inventory',)),
    DF("dfsr_events", "DFS Replication errors (7 days)",
       "DFS Replication log grouped -- staging quota, conflicts, dirty shutdown (2213).",
       EV + "EvSum @{LogName='DFS Replication'; Level=1,2,3} 7", tags=('diagnostic', 'health')),
    # ---- Storage & free space (12) -----------------------------------------------------------
    SG("st_volumes_low", "Volumes below 15% free",
       "Every volume (including mount points) with less than 15% free space.",
       need_admin("Win32_Volume and Win32_ShadowCopy return NOTHING for a standard user "
                "(measured 2026-10-16)") + r"""Get-CimInstance Win32_Volume -Filter 'DriveType=3' | Where-Object Capacity | ForEach-Object { $pct = 100 * $_.FreeSpace / $_.Capacity; if ($pct -lt 15) { '[WARN] {0,-30} {1,6:N1}% free  ({2:N1} of {3:N1} GB)' -f $(if ($_.Name) {$_.Name} else {$_.DeviceID}), $pct, ($_.FreeSpace/1GB), ($_.Capacity/1GB) } }
'(nothing above = every volume has at least 15% free)'""",
       requires="", tags=('diagnostic', 'health', 'storage')),
    SG("st_mount_points", "Mount points and volumes without letters",
       "Volumes mounted into folders or with no drive letter -- easy to miss in a free-space "
       "check.",
       need_admin("Win32_Volume and Win32_ShadowCopy return NOTHING for a standard user "
                "(measured 2026-10-16)") + T("Get-CimInstance Win32_Volume -Filter 'DriveType=3' | Where-Object { -not $_.DriveLetter } | Select-Object Name, Label, FileSystem, @{n='FreeGB';e={[math]::Round($_.FreeSpace/1GB,1)}}, @{n='SizeGB';e={[math]::Round($_.Capacity/1GB,1)}}"),
       requires="", tags=('inventory', 'storage')),
    SG("st_storage_pools", "Storage Spaces pools and virtual disks",
       "Storage pools, their health and the virtual disks on them with resiliency.",
       r"""Get-StoragePool -IsPrimordial $false -ErrorAction SilentlyContinue | Select-Object FriendlyName, HealthStatus, OperationalStatus, @{n='SizeGB';e={[math]::Round($_.Size/1GB)}}, @{n='AllocatedGB';e={[math]::Round($_.AllocatedSize/1GB)}} | Format-Table -AutoSize | Out-String -Width 220
Get-VirtualDisk -ErrorAction SilentlyContinue | Select-Object FriendlyName, ResiliencySettingName, HealthStatus, OperationalStatus, @{n='SizeGB';e={[math]::Round($_.Size/1GB)}} | Format-Table -AutoSize | Out-String -Width 220
if (-not (Get-StoragePool -IsPrimordial $false -ErrorAction SilentlyContinue)) { '[INFO] No Storage Spaces pool.' }""",
       requires="", tags=('inventory', 'storage')),
    SG("st_iscsi", "iSCSI sessions and targets",
       "iSCSI initiator sessions, their targets and connection state.",
       r"""$s = Get-IscsiSession -ErrorAction SilentlyContinue
if (-not $s) { '[INFO] No iSCSI sessions.'; return }
$s | Select-Object TargetNodeAddress, IsConnected, IsPersistent, NumberOfConnections | Format-Table -AutoSize | Out-String -Width 220""",
       requires="", tags=('inventory', 'storage', 'network')),
    SG("st_mpio", "MPIO paths",
       "Multipath I/O disks and their path counts and policy.",
       need_cmd("mpclaim.exe", "MPIO (Multipath I/O feature)") + "mpclaim.exe -s -d", tags=('inventory', 'storage')),
    SG("st_disk_latency", "Disk latency per volume (10 s)",
       "Average read/write latency per logical disk over ten seconds -- over 20 ms sustained "
       "is a storage problem.",
       r"""$c = Get-Counter '\LogicalDisk(*)\Avg. Disk sec/Read','\LogicalDisk(*)\Avg. Disk sec/Write','\LogicalDisk(*)\Current Disk Queue Length' -SampleInterval 1 -MaxSamples 10 -ErrorAction SilentlyContinue
$c.CounterSamples | Where-Object InstanceName -ne '_total' | Group-Object Path | ForEach-Object { $a = ($_.Group | Measure-Object CookedValue -Average).Average; $ms = $_.Name -match 'sec/'; '{0} {1,-60} {2,10:N2}{3}' -f $(if ($ms -and $a -gt 0.02) {'[WARN]'} else {'[OK]  '}), ($_.Name -replace '^\\\\[^\\]+',''), $(if ($ms) { $a * 1000 } else { $a }), $(if ($ms) {' ms'} else {''}) }""",
       requires="", timeout=60, tags=('diagnostic', 'health', 'performance', 'storage')),
    SG("st_growth_folders", "Folders that grew most this week",
       "Top-level folders of each fixed drive with files written in the last 7 days, by the "
       "size of those new files -- where the disk is filling from.",
       r"""Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object { $d = $_.DeviceID + '\'; "== $d"
  Get-ChildItem $d -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object { $s = (Get-ChildItem $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object LastWriteTime -gt (Get-Date).AddDays(-7) | Measure-Object Length -Sum).Sum
    if ($s -gt 100MB) { '{0,9:N2} GB  {1}' -f ($s/1GB), $_.FullName } } }""",
       requires="", timeout=1800, tags=('inventory', 'storage')),
    SG("st_ntfs_cluster", "NTFS volume details",
       "Cluster size, MFT size and reserved zones per NTFS volume (fsutil fsinfo ntfsinfo).",
       r"""Get-Volume | Where-Object { $_.DriveLetter -and $_.FileSystem -eq 'NTFS' } | ForEach-Object { "== $($_.DriveLetter):"; fsutil fsinfo ntfsinfo "$($_.DriveLetter):" 2>&1 | Select-String 'Bytes Per Cluster|Mft Valid Data Length|Total Clusters|Free Clusters|NTFS Version' | ForEach-Object { '   ' + $_.Line.Trim() } }""",
       requires="", admin=True, tags=('inventory', 'storage')),
    DD("st_dedup_volumes", "Data Deduplication savings",
       "Deduplicated volumes with saved space and optimisation rate.",
       T("Get-DedupVolume | Select-Object Volume, Enabled, UsageType, @{n='SavedGB';e={[math]::Round($_.SavedSpace/1GB,1)}}, SavingsRate"), tags=('inventory', 'storage')),
    DD("st_dedup_jobs", "Data Deduplication jobs and status",
       "Last optimisation, garbage collection and scrubbing per volume.",
       L("Get-DedupStatus | Select-Object Volume, LastOptimizationTime, LastOptimizationResultMessage, LastGarbageCollectionTime, LastScrubbingTime, InPolicyFilesCount"), tags=('inventory', 'storage')),
    SG("st_volume_health", "Volume health and repair status",
       "Volumes reporting anything but Healthy, and the last chkdsk / spot-fix results from the "
       "log.",
       r"""Get-Volume | Where-Object { $_.DriveLetter -and $_.HealthStatus -ne 'Healthy' } | ForEach-Object { "[WARN] $($_.DriveLetter): $($_.HealthStatus) $($_.OperationalStatus)" }
""" + EV + "EvList @{LogName='Application'; ProviderName='Chkdsk','Wininit'} 90 20",
       requires="", tags=('diagnostic', 'health', 'storage')),
    SG("st_free_space_report", "Free space report (all fixed disks)",
       "Size, used, free and percentage for every fixed logical disk -- the classic Wintel "
       "free-space report.",
       T("Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID, VolumeName, FileSystem, @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}}, @{n='UsedGB';e={[math]::Round(($_.Size-$_.FreeSpace)/1GB,1)}}, @{n='FreeGB';e={[math]::Round($_.FreeSpace/1GB,1)}}, @{n='Free%';e={[math]::Round(100*$_.FreeSpace/$_.Size,1)}}"),
       requires="", tags=('inventory', 'storage')),
    # ---- IIS (10) ----------------------------------------------------------------------------
    IS("iis_apps", "Applications and virtual directories",
       "Every web application and virtual directory with its pool and physical path.",
       T("Get-WebApplication | Select-Object @{n='Site';e={$_.GetParentElement().Attributes['name'].Value}}, Path, applicationPool, PhysicalPath; Get-WebVirtualDirectory | Select-Object Path, PhysicalPath"), tags=('inventory',)),
    IS("iis_pool_recycling", "App pool recycling and limits",
       "Recycling interval, idle timeout, memory limits and rapid-fail protection per pool.",
       T("Get-ChildItem IIS:\\AppPools | Select-Object Name, @{n='Recycle';e={$_.recycling.periodicRestart.time}}, @{n='IdleTimeout';e={$_.processModel.idleTimeout}}, @{n='PrivateMemKB';e={$_.recycling.periodicRestart.privateMemory}}, @{n='RapidFail';e={$_.failure.rapidFailProtection}}"), tags=('inventory', 'performance')),
    IS("iis_auth", "Authentication per site",
       "Anonymous, Windows and Basic authentication state per site -- Basic over HTTP sends "
       "passwords in clear.",
       r"""Get-Website | ForEach-Object { $s = $_.Name; foreach ($a in 'anonymousAuthentication','windowsAuthentication','basicAuthentication') { $e = (Get-WebConfigurationProperty -Filter "/system.webServer/security/authentication/$a" -Name enabled -PSPath IIS:\ -Location $s -ErrorAction SilentlyContinue).Value; '{0,-30} {1,-26} {2}' -f $s, $a, $e } }""", tags=('inventory', 'security')),
    IS("iis_directory_browsing", "Directory browsing and detailed errors",
       "Sites with directory browsing on or detailed errors sent to remote clients -- both leak "
       "information.",
       r"""Get-Website | ForEach-Object { $s = $_.Name
  $db = (Get-WebConfigurationProperty -Filter /system.webServer/directoryBrowse -Name enabled -PSPath IIS:\ -Location $s).Value
  $er = (Get-WebConfigurationProperty -Filter /system.webServer/httpErrors -Name errorMode -PSPath IIS:\ -Location $s).Value
  '{0} {1,-30} directoryBrowse={2} errorMode={3}' -f $(if ($db -or $er -eq 'Detailed') {'[WARN]'} else {'[OK]  '}), $s, $db, $er }""", tags=('diagnostic', 'health', 'security')),
    IS("iis_request_filtering", "Request limits",
       "Maximum allowed content length and URL length per site.",
       r"""Get-Website | ForEach-Object { $s = $_.Name; $l = Get-WebConfigurationProperty -Filter /system.webServer/security/requestFiltering/requestLimits -Name * -PSPath IIS:\ -Location $s -ErrorAction SilentlyContinue; '{0,-30} maxAllowedContentLength={1} maxUrl={2} maxQueryString={3}' -f $s, $l.maxAllowedContentLength, $l.maxUrl, $l.maxQueryString }""", tags=('inventory', 'security', 'performance')),
    IS("iis_log_settings", "IIS log settings",
       "Log format, directory, rollover and fields per site.",
       T("Get-Website | Select-Object Name, @{n='Dir';e={$_.logFile.directory}}, @{n='Format';e={$_.logFile.logFormat}}, @{n='Period';e={$_.logFile.period}}, @{n='Enabled';e={$_.logFile.enabled}}"), tags=('inventory',)),
    IS("iis_log_status_codes", "Status codes in today's logs",
       "Response status codes counted per site in the newest log -- 4xx/5xx spikes.",
       r"""Get-Website | ForEach-Object { $dir = [Environment]::ExpandEnvironmentVariables($_.logFile.directory) + "\W3SVC$($_.id)"; $f = Get-ChildItem $dir -Filter *.log -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $f) { return }; $fields = (Get-Content $f.FullName -TotalCount 10 | Where-Object { $_ -like '#Fields:*' } | Select-Object -Last 1) -replace '^#Fields: ','' -split ' '; $i = [array]::IndexOf($fields, 'sc-status')
  "== $($_.Name) ($($f.Name))"; Get-Content $f.FullName | Where-Object { $_ -notlike '#*' } | ForEach-Object { ($_ -split ' ')[$i] } | Group-Object | Sort-Object Count -Descending | ForEach-Object { '   {0} x{1}' -f $_.Name, $_.Count } }""",
       timeout=600, tags=('inventory',)),
    IS("iis_top_urls", "Busiest URLs and clients today",
       "Top requested URLs and top client IPs in the newest log of each site.",
       r"""Get-Website | ForEach-Object { $dir = [Environment]::ExpandEnvironmentVariables($_.logFile.directory) + "\W3SVC$($_.id)"; $f = Get-ChildItem $dir -Filter *.log -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $f) { return }; $fields = (Get-Content $f.FullName -TotalCount 10 | Where-Object { $_ -like '#Fields:*' } | Select-Object -Last 1) -replace '^#Fields: ','' -split ' '
  $u = [array]::IndexOf($fields, 'cs-uri-stem'); $c = [array]::IndexOf($fields, 'c-ip'); $rows = Get-Content $f.FullName | Where-Object { $_ -notlike '#*' } | ForEach-Object { , ($_ -split ' ') }
  "== $($_.Name) top URLs"; $rows | ForEach-Object { $_[$u] } | Group-Object | Sort-Object Count -Descending | Select-Object -First 10 | ForEach-Object { '   {0,7} {1}' -f $_.Count, $_.Name }
  "== $($_.Name) top clients"; $rows | ForEach-Object { $_[$c] } | Group-Object | Sort-Object Count -Descending | Select-Object -First 10 | ForEach-Object { '   {0,7} {1}' -f $_.Count, $_.Name } }""",
       timeout=600, tags=('inventory', 'performance')),
    IS("iis_modules", "Installed IIS modules",
       "Global native modules loaded by IIS -- third-party modules run in every request.",
       T("Get-WebGlobalModule | Select-Object Name, Image"), tags=('inventory',)),
    IS("iis_events", "IIS / W3SVC / WAS errors (7 days)",
       "Errors from W3SVC, WAS (app pool crashes and rapid-fail) and ASP.NET.",
       EV + "EvSum @{LogName='System','Application'; ProviderName='WAS','W3SVC','Microsoft-Windows-WAS','ASP.NET 4.0.30319.0','IIS-W3SVC-WP'; Level=1,2,3} 7", tags=('diagnostic', 'health')),
    # ---- Hyper-V (12) ------------------------------------------------------------------------
    HV("hv_vm_resources", "VM CPU and memory configuration",
       "vCPUs, startup/min/max memory and dynamic memory per VM.",
       T("Get-VM | Select-Object Name, ProcessorCount, @{n='StartupGB';e={[math]::Round($_.MemoryStartup/1GB,1)}}, DynamicMemoryEnabled, @{n='MinGB';e={[math]::Round($_.MemoryMinimum/1GB,1)}}, @{n='MaxGB';e={[math]::Round($_.MemoryMaximum/1GB,1)}}"), tags=('inventory', 'performance')),
    HV("hv_vm_network", "VM network adapters",
       "Each VM's network adapters, switch, IPs, MAC and VLAN.",
       T("Get-VM | Get-VMNetworkAdapter | Select-Object VMName, Name, SwitchName, MacAddress, @{n='IPs';e={$_.IPAddresses -join ', '}}, @{n='VLAN';e={(Get-VMNetworkAdapterVlan -VMNetworkAdapter $_).AccessVlanId}}"), tags=('inventory', 'network')),
    HV("hv_vm_autostart", "VM automatic start and stop actions",
       "What each VM does when the host starts and shuts down -- 'save' needs disk space equal "
       "to its memory.",
       T("Get-VM | Select-Object Name, AutomaticStartAction, AutomaticStartDelay, AutomaticStopAction"), tags=('inventory',)),
    HV("hv_vm_versions", "VM configuration versions",
       "VM configuration version per VM -- old versions miss features after a host upgrade.",
       T("Get-VM | Select-Object Name, Version, Generation, State"), tags=('inventory',)),
    HV("hv_vm_secureboot", "VM Secure Boot and vTPM",
       "Secure Boot template and virtual TPM per generation-2 VM.",
       T("Get-VM | Where-Object Generation -eq 2 | ForEach-Object { $f = Get-VMFirmware $_; [pscustomobject]@{ VM=$_.Name; SecureBoot=$f.SecureBoot; Template=$f.SecureBootTemplate; vTPM=(Get-VMSecurity $_).TpmEnabled } }"), tags=('inventory', 'security')),
    HV("hv_vhd_fragmentation", "Dynamic disks vs their maximum",
       "Dynamically expanding VHDX files: current size against maximum -- overcommitted "
       "storage fills the host volume.",
       r"""$t = 0; Get-VM | Get-VMHardDiskDrive | ForEach-Object { $v = Get-VHD $_.Path -ErrorAction SilentlyContinue; if ($v.VhdType -eq 'Dynamic') { $t += $v.Size; '{0,-25} {1,8:N1} of {2,6:N0} GB  {3}' -f $_.VMName, ($v.FileSize/1GB), ($v.Size/1GB), $_.Path } }
'Total promised to dynamic disks: {0:N0} GB' -f ($t/1GB)""", tags=('inventory', 'performance', 'storage')),
    HV("hv_host_numa", "Host NUMA and memory",
       "NUMA nodes and free memory per node -- a VM spanning nodes runs slower.",
       T("Get-VMHostNumaNode | Select-Object NodeId, @{n='Processors';e={$_.ProcessorsAvailability.Count}}, @{n='MemTotalGB';e={[math]::Round($_.MemoryTotal/1KB,1)}}, @{n='MemAvailGB';e={[math]::Round($_.MemoryAvailable/1KB,1)}}"), tags=('inventory', 'performance')),
    HV("hv_events", "Hyper-V errors (7 days)",
       "Hyper-V VMMS and worker errors grouped.",
       EV + "EvSum @{LogName='Microsoft-Windows-Hyper-V-VMMS-Admin','Microsoft-Windows-Hyper-V-Worker-Admin'; Level=1,2,3} 7", tags=('diagnostic', 'health')),
    HV("hv_vm_uptime", "VM uptime and heartbeat",
       "Uptime and heartbeat status per running VM.",
       T("Get-VM | Where-Object State -eq 'Running' | Select-Object Name, Uptime, Heartbeat, Status"), tags=('inventory',)),
    HV("hv_live_migration", "Live migration settings",
       "Whether live migration is enabled, the authentication protocol, simultaneous "
       "migrations and networks.",
       L("Get-VMHost | Select-Object VirtualMachineMigrationEnabled, VirtualMachineMigrationAuthenticationType, VirtualMachineMigrationPerformanceOption, MaximumVirtualMachineMigrations; Get-VMMigrationNetwork"), tags=('inventory', 'network')),
    HV("hv_dvd_iso", "ISO images attached to VMs",
       "VMs with an ISO still mounted -- it blocks live migration if the path is local.",
       r"""$d = Get-VM | Get-VMDvdDrive | Where-Object Path
if (-not $d) { '[OK]   No ISO attached.'; return }
$d | ForEach-Object { "[INFO] $($_.VMName): $($_.Path)" }""", tags=('diagnostic', 'health', 'storage')),
    HV("hv_vm_integration_version", "Integration services version",
       "Integration services state reported by each VM.",
       T("Get-VM | Select-Object Name, IntegrationServicesVersion, IntegrationServicesState"), tags=('inventory',)),
    # ---- Failover Cluster (8) ----------------------------------------------------------------
    CL("cl_resource_types_owner", "Cluster resources and owners",
       "Every resource with type, owner group and node.",
       T("Get-ClusterResource | Select-Object Name, ResourceType, OwnerGroup, OwnerNode, State"), tags=('inventory',)),
    CL("cl_events", "Cluster errors (7 days)",
       "Failover Clustering operational errors grouped.",
       EV + "EvSum @{LogName='Microsoft-Windows-FailoverClustering/Operational'; Level=1,2,3} 7", tags=('diagnostic', 'health')),
    CL("cl_preferred_owners", "Preferred owners per role",
       "Preferred owner nodes for each clustered role.",
       r"""Get-ClusterGroup | ForEach-Object { '{0,-40} {1}' -f $_.Name, ((Get-ClusterOwnerNode -Group $_.Name).OwnerNodes.Name -join ', ') }""", tags=('inventory',)),
    CL("cl_csv_redirected", "CSV redirected I/O",
       "Cluster Shared Volumes in redirected access -- slow and a sign of a storage path "
       "problem.",
       T("Get-ClusterSharedVolumeState | Select-Object Name, Node, StateInfo, FileSystemRedirectedIOReason, BlockRedirectedIOReason"), tags=('inventory', 'performance', 'storage')),
    CL("cl_parameters", "Cluster parameters",
       "Heartbeat thresholds, quarantine and dynamic quorum settings.",
       L("Get-Cluster | Select-Object Name, SameSubnetThreshold, CrossSubnetThreshold, SameSubnetDelay, QuarantineThreshold, DynamicQuorum, WitnessDynamicWeight"), tags=('inventory',)),
    CL("cl_node_uptime", "Cluster node uptime",
       "Each node's last boot.",
       r"""Get-ClusterNode | ForEach-Object { try { $o = Get-CimInstance Win32_OperatingSystem -ComputerName $_.Name -ErrorAction Stop; '{0,-20} up since {1}' -f $_.Name, $o.LastBootUpTime } catch { "[BAD]  $($_.Name) -- $($_.Exception.Message)" } }""", tags=('diagnostic', 'health')),
    CL("cl_validation_report", "Last cluster validation report",
       "The newest cluster validation report on disk and its date.",
       r"""$r = Get-ChildItem "$env:SystemRoot\Cluster\Reports" -Filter 'Validation Report*' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 3
if (-not $r) { '[WARN] No validation report on this node.'; return }
$r | ForEach-Object { "[INFO] $($_.LastWriteTime)  $($_.FullName)" }""", tags=('diagnostic', 'health')),
    CL("cl_disks", "Cluster disks",
       "Physical disk resources with owner and state.",
       T("Get-ClusterResource | Where-Object ResourceType -eq 'Physical Disk' | Select-Object Name, OwnerGroup, OwnerNode, State"), tags=('inventory', 'storage')),
    # ---- Remote Desktop Services (6) ---------------------------------------------------------
    RD("rds_servers", "RDS deployment servers",
       "Servers in the deployment and their roles.",
       T("Get-RDServer | Select-Object Server, @{n='Roles';e={$_.Roles -join ', '}}"), tags=('inventory',)),
    RD("rds_collections", "Session collections",
       "Session collections and their size.",
       T("Get-RDSessionCollection | Select-Object CollectionName, Size, ResourceType, CollectionDescription"), tags=('inventory',)),
    RD("rds_remoteapps", "Published RemoteApps",
       "RemoteApp programs per collection.",
       T("Get-RDSessionCollection | ForEach-Object { Get-RDRemoteApp -CollectionName $_.CollectionName } | Select-Object CollectionName, DisplayName, FilePath, ShowInWebAccess"), tags=('inventory',)),
    RD("rds_licensing", "RDS licensing configuration",
       "Licensing mode and license servers -- the 120-day grace period ending shows here.",
       L("Get-RDLicenseConfiguration"), tags=('inventory',)),
    RD("rds_sessions_idle", "Idle and disconnected sessions",
       "Disconnected sessions and sessions idle for over an hour.",
       T("Get-RDUserSession | Where-Object { $_.SessionState -eq 'STATE_DISCONNECTED' -or $_.IdleTime -gt 3600000 } | Select-Object UserName, HostServer, SessionState, @{n='IdleMin';e={[math]::Round($_.IdleTime/60000)}}"), tags=('inventory',)),
    RD("rds_events", "Remote Desktop errors (7 days)",
       "TerminalServices errors grouped.",
       EV + "EvSum @{LogName='Microsoft-Windows-TerminalServices-LocalSessionManager/Operational','Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational'; Level=1,2,3} 7", tags=('diagnostic', 'health')),
    # ---- WSUS (6) ----------------------------------------------------------------------------
    WS("wsus_computers_stale", "WSUS computers not reporting",
       "Computers that have not reported to WSUS in 30 days.",
       r"""$w = Get-WsusServer; $w.GetComputerTargets() | Where-Object { $_.LastReportedStatusTime -lt (Get-Date).AddDays(-30) } | Sort-Object LastReportedStatusTime | ForEach-Object { '[WARN] {0,-40} last report {1}' -f $_.FullDomainName, $_.LastReportedStatusTime }""", tags=('diagnostic', 'health')),
    WS("wsus_computers_failed", "WSUS computers with update failures",
       "Computers reporting failed installs.",
       r"""$w = Get-WsusServer; $s = New-Object Microsoft.UpdateServices.Administration.ComputerTargetScope; $s.IncludedInstallationStates = 'Failed'
$w.GetComputerTargets($s) | ForEach-Object { "[BAD]  $($_.FullDomainName)" }""", tags=('diagnostic', 'health')),
    WS("wsus_groups", "WSUS computer groups",
       "Computer groups and their member counts.",
       r"""(Get-WsusServer).GetComputerTargetGroups() | ForEach-Object { '{0,-30} {1,5} computers' -f $_.Name, $_.GetComputerTargets().Count }""", tags=('inventory',)),
    WS("wsus_unapproved_critical", "Unapproved critical and security updates",
       "Critical and security updates still not approved.",
       r"""Get-WsusUpdate -Classification Critical -Approval Unapproved -Status Any -ErrorAction SilentlyContinue | Select-Object -First 50 | ForEach-Object { "[WARN] $($_.Update.Title)" }
Get-WsusUpdate -Classification Security -Approval Unapproved -Status Any -ErrorAction SilentlyContinue | Select-Object -First 50 | ForEach-Object { "[WARN] $($_.Update.Title)" }""",
       timeout=600, tags=('diagnostic', 'health', 'security')),
    WS("wsus_content_size", "WSUS content and database",
       "The WSUS content folder size and database location.",
       r"""$c = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Update Services\Server\Setup').ContentDir
'Content: {0}  {1:N1} GB' -f $c, ((Get-ChildItem $c -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum/1GB)
'Database: ' + (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Update Services\Server\Setup').SqlServerName""",
       timeout=900, tags=('inventory', 'storage')),
    WS("wsus_products", "WSUS products and classifications",
       "The products and classifications WSUS synchronises.",
       r"""$s = (Get-WsusServer).GetSubscription(); 'Classifications:'; $s.GetUpdateClassifications() | ForEach-Object { "   $($_.Title)" }; 'Products:'; $s.GetUpdateCategories() | ForEach-Object { "   $($_.Title)" }""", tags=('inventory',)),
    # ---- Certificate Services (6) ------------------------------------------------------------
    CA("ca_config", "Certificate authority configuration",
       "CA name, type, validity and CRL settings.",
       _IS_CA + "certutil.exe -cainfo", tags=('inventory', 'security', 'baseline')),
    CA("ca_expiring", "Certificates issued that expire within 30 days",
       "Issued certificates (by this CA) expiring in the next 30 days.",
       _IS_CA + r"""$d = (Get-Date).AddDays(30).ToString('MM/dd/yyyy')
certutil.exe -view -restrict "NotAfter<=$d,NotAfter>=now,Disposition=20" -out "RequestID,RequesterName,CommonName,NotAfter,CertificateTemplate" csv""",
       timeout=600, tags=('diagnostic', 'security')),
    CA("ca_failed_requests", "Failed and pending requests",
       "Certificate requests that failed or wait for approval.",
       _IS_CA + "certutil.exe -view -restrict \"Disposition=30\" -out \"RequestID,RequesterName,StatusCode,Disposition\" csv\ncertutil.exe -view -restrict \"Disposition=9\" -out \"RequestID,RequesterName\" csv",
       timeout=600, tags=('inventory', 'security')),
    CA("ca_crl", "CRL publication inspection",
       "CRL distribution points and when the current CRL expires -- an expired CRL makes "
       "every certificate fail revocation checks.",
       _IS_CA + "certutil.exe -getreg CA\\CRLPublicationURLs\n$ca = certutil.exe -CAInfo 2>&1\n$ca | Select-String 'CRL' | ForEach-Object { $_.Line }", tags=('diagnostic', 'security')),
    CA("ca_templates_risky", "Templates allowing requester-supplied names",
       "Published templates where the requester supplies the subject (ESC1 risk) -- domain "
       "admin via a certificate.",
       need_cmd("Get-ADObject", "the ActiveDirectory module") + r"""$cfg = (Get-ADRootDSE).configurationNamingContext
Get-ADObject -SearchBase "CN=Certificate Templates,CN=Public Key Services,CN=Services,$cfg" -Filter * -Properties msPKI-Certificate-Name-Flag, pKIExtendedKeyUsage | Where-Object { $_.'msPKI-Certificate-Name-Flag' -band 1 } | ForEach-Object { "[WARN] $($_.Name) -- ENROLLEE_SUPPLIES_SUBJECT; EKUs: $($_.pKIExtendedKeyUsage -join ', ')" }""", tags=('diagnostic', 'health', 'security')),
    CA("ca_service", "CA service and database",
       "Certificate Services service state and database size.",
       _IS_CA + r"""Get-Service CertSvc | Select-Object Status, StartType | Format-List | Out-String
$db = (certutil.exe -getreg CA\DBDirectory 2>&1 | Select-String 'DBDirectory' ) -replace '^.*= ',''
"Database folder: $db" """, tags=('inventory', 'security')),
    # ---- Print server (5) --------------------------------------------------------------------
    PS("ps_printers_shared", "Shared printers",
       "Printers this server shares, with driver, port and published-in-AD flag.",
       r"""$sp = Get-Service Spooler -ErrorAction SilentlyContinue
if ($sp.Status -ne 'Running') { "[WARN] Print Spooler is $($sp.Status) -- printers cannot be read."; return }
Get-Printer | Where-Object Shared | Select-Object Name, ShareName, DriverName, PortName, Published | Format-Table -AutoSize | Out-String -Width 220""", tags=('diagnostic', 'health')),
    PS("ps_queue_errors", "Print queues in error",
       "Printers in an error, offline or paper-jam state.",
       r"""$sp = Get-Service Spooler -ErrorAction SilentlyContinue
if ($sp.Status -ne 'Running') { "[WARN] Print Spooler is $($sp.Status) -- printers cannot be read."; return }
Get-Printer | Where-Object { $_.PrinterStatus -ne 'Normal' } | ForEach-Object { "[WARN] $($_.Name) -- $($_.PrinterStatus)" }
'(nothing above = every queue is normal)'""", tags=('diagnostic', 'health')),
    PS("ps_drivers_versions", "Print driver versions and isolation",
       "Drivers with version, type 3/4 and driver isolation mode.",
       r"""$sp = Get-Service Spooler -ErrorAction SilentlyContinue
if ($sp.Status -ne 'Running') { "[WARN] Print Spooler is $($sp.Status) -- drivers cannot be read."; return }
Get-PrinterDriver | Select-Object Name, MajorVersion, @{n='Version';e={$_.DriverVersion}}, PrinterEnvironment, IsPackageAware | Format-Table -AutoSize | Out-String -Width 220""", tags=('diagnostic', 'health')),
    PS("ps_spooler_dc", "Print Spooler on domain controllers",
       "Whether the spooler runs on this server -- on a DC it should be disabled "
       "(PrintNightmare / printer bug coercion).",
       CHK + r"""$dc = (Get-CimInstance Win32_ComputerSystem).DomainRole -ge 4
$s = Get-Service Spooler
Chk $(if ($dc) { $s.Status -ne 'Running' } else { $null }) 'Spooler on a domain controller' $(if ($dc) { "$($s.Status), $($s.StartType)" } else { 'this is not a domain controller' })""", tags=('diagnostic', 'health', 'security')),
    PS("ps_events", "Print service errors (7 days)",
       "PrintService admin log grouped.",
       EV + "EvSum @{LogName='Microsoft-Windows-PrintService/Admin'; Level=1,2,3} 7", tags=('diagnostic', 'health')),
]

# Additional daily operations checks. These never invoke cleanup, repair,
# synchronization, drain, or enrollment actions.
SCRIPTS += [
    CA("ca-published-crl-aia-probes", "Published HTTP CRL freshness and AIA reachability",
       "Probes configured HTTP publication files, decodes CRL update times and checks AIA certificate validity in memory.",
       _CA_LOCAL + CHK + r"""# Minimal DER TLV reader: only reads the signed CRL's update timestamps.
function Read-DerNode([byte[]]$bytes, [int]$offset) {
  if ($offset+2 -gt $bytes.Length) { throw 'Truncated DER header' }
  $tag = [int]$bytes[$offset]; $pos = $offset+1; $length = [int]$bytes[$pos]; $pos++
  if ($length -band 128) {
    $n = $length -band 127
    if ($n -lt 1 -or $n -gt 4 -or $pos+$n -gt $bytes.Length) { throw 'Unsupported DER length' }
    $length = 0L
    for ($j=0; $j -lt $n; $j++) { $length = $length*256+$bytes[$pos]; $pos++ }
  }
  if ($pos+$length -gt $bytes.Length) { throw 'DER value outside content' }
  [pscustomobject]@{Tag=$tag; Start=$pos; End=($pos+$length); Length=$length}
}
function Read-DerTime($bytes, $node) {
  $text = [Text.Encoding]::ASCII.GetString($bytes,$node.Start,$node.Length)
  $format = if ($node.Tag -eq 23) { 'yyMMddHHmmss\Z' } elseif ($node.Tag -eq 24) { 'yyyyMMddHHmmss\Z' } else { throw 'CRL timestamp missing' }
  # RFC 5280 UTCTime maps 50..99 to 1950..1999, 00..49 to 2000..2049.
  if ($node.Tag -eq 23) { $year = [int]$text.Substring(0,2); $text = $(if ($year -ge 50) {'19'} else {'20'})+$text; $format = 'yyyyMMddHHmmss\Z' }
  [datetime]::ParseExact($text,$format,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::AssumeUniversal).ToUniversalTime()
}
try {
  $config = Get-ItemProperty -LiteralPath $caKey -ErrorAction Stop
  $folder = Join-Path $env:SystemRoot 'System32\CertSrv\CertEnroll'
  $localFiles = @(Get-ChildItem -LiteralPath $folder -File -ErrorAction Stop)
  $urls = @{}
  foreach ($property in 'CRLPublicationURLs','CACertPublicationURLs') {
    foreach ($entry in @($config.$property)) {
      $raw = [string]$entry -replace '^\d+:',''
      if ($raw -notmatch '^https?://') { "[INFO] $property endpoint not probed (non-HTTP): $raw"; continue }
      $raw = $raw.Replace('%1',[Net.Dns]::GetHostEntry($env:COMPUTERNAME).HostName).Replace('%2',$env:COMPUTERNAME)
      $slash = $raw.LastIndexOf('/'); $directory = $raw.Substring(0,$slash+1); $leaf = $raw.Substring($slash+1)
      if ($directory -match '%\d') { Chk $null $raw 'unresolved directory substitution; use PKI publication configuration to resolve'; continue }
      if ($leaf -match '%\d') {
        $pattern = $leaf -replace '%\d+','*'
        $matches = @($localFiles | Where-Object { $_.Name -like $pattern })
        if (-not $matches.Count) { Chk $null $raw 'no local publication filename matches placeholders'; continue }
        foreach ($file in $matches) { $urls[$directory+[uri]::EscapeDataString($file.Name)] = $property }
      } else { $urls[$raw] = $property }
    }
  }
  if (-not $urls.Count) { Chk $null 'Publication probes' 'no concrete HTTP URLs resolved'; return }
  foreach ($url in $urls.Keys) {
    try {
      $request = [Net.HttpWebRequest]::Create($url); $request.Method = 'GET'; $request.Timeout = 15000; $request.ReadWriteTimeout = 15000
      $response = $request.GetResponse(); $buffer = New-Object IO.MemoryStream
      try {
        $stream = $response.GetResponseStream(); $chunk = New-Object byte[] 8192
        while (($read = $stream.Read($chunk,0,$chunk.Length)) -gt 0) {
          if ($buffer.Length+$read -gt 32MB) { throw 'Response exceeds 32 MB probe limit; freshness unknown' }
          $buffer.Write($chunk,0,$read)
        }
        $bytes = $buffer.ToArray()
      } finally { $response.Dispose(); $buffer.Dispose() }
      if (-not $bytes.Length) { throw 'Empty HTTP response' }
      if ($urls[$url] -eq 'CRLPublicationURLs') {
        $outer = Read-DerNode $bytes 0; $tbs = Read-DerNode $bytes $outer.Start
        if ($outer.Tag -ne 48 -or $tbs.Tag -ne 48) { throw 'Expected DER CRL sequence' }
        $node = Read-DerNode $bytes $tbs.Start
        if ($node.Tag -eq 2) { $node = Read-DerNode $bytes $node.End }
        # signature algorithm, issuer, thisUpdate, nextUpdate
        $issuer = Read-DerNode $bytes $node.End; $issued = Read-DerNode $bytes $issuer.End
        $next = Read-DerNode $bytes $issued.End
        $thisUpdate = Read-DerTime $bytes $issued; $nextUpdate = Read-DerTime $bytes $next
        $now = [datetime]::UtcNow
        Chk ($thisUpdate -le $now -and $nextUpdate -gt $now.AddHours(24)) $url "thisUpdate UTC=$thisUpdate; nextUpdate UTC=$nextUpdate; warning within 24 hours; signature not validated"
      } else {
        $cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList (,$bytes)
        try { Chk ($cert.NotBefore -le (Get-Date) -and $cert.NotAfter -gt (Get-Date)) $url "AIA subject=$($cert.Subject); expires=$($cert.NotAfter); chain and expected issuer identity not verified" }
        finally { $cert.Dispose() }
      }
    } catch { Chk $null $url "probe/decode failed: $($_.Exception.Message)" }
  }
  '[INFO] Probes run from this CA without authentication. LDAP/file endpoints and client network paths need separate checks; nothing is cached to disk.'
} catch { "ERROR: Cannot inspect publication endpoints: $($_.Exception.Message)" }""",
       admin=True, requires="local AD CS role and HTTP CDP/AIA publication", timeout=600, tags=('diagnostic', 'health', 'security', 'network')),
    CA("ca-permissions-enrollment-endpoints", "CA permissions and enrollment web endpoints",
       "Shows the CA security descriptor and IIS enrollment applications with their authentication settings.",
       _CA_LOCAL + CHK + r"""try {
  $bytes = (Get-ItemProperty -LiteralPath $caKey -ErrorAction Stop).Security
  if (-not $bytes) { Chk $null 'CA permissions' 'Security descriptor missing or empty' }
  else {
    $sd = New-Object Security.AccessControl.RawSecurityDescriptor -ArgumentList $bytes,0
    "[INFO] CA security SDDL: $($sd.GetSddlForm([Security.AccessControl.AccessControlSections]::All))"
    $sd.DiscretionaryAcl | Select-Object SecurityIdentifier, AceType, AccessMask, AceFlags | Format-Table -AutoSize | Out-String -Width 220
    '[INFO] CA rights: 1 Manage CA, 2 Issue/manage certificates, 256 Read, 512 Request certificates; SDDL is not an effective-access calculation.'
  }
} catch { "ERROR: Cannot read CA permissions: $($_.Exception.Message)" }
if (-not (Get-Command Get-WebApplication -ErrorAction SilentlyContinue)) { Chk $null 'Enrollment endpoints' 'WebAdministration unavailable; enrollment may be hosted on another server'; return }
try {
  $apps = @(Get-WebApplication -ErrorAction Stop | Where-Object { $_.Path -match '(?i)certsrv|ces|cep' })
  if (-not $apps.Count) { Chk $null 'Enrollment endpoints' 'no local Web Enrollment/CES/CEP applications returned' }
  foreach ($app in $apps) {
    "[INFO] $($app.ItemXPath); pool=$($app.ApplicationPool); path=$($app.Path)"
    $pool = Get-WebAppPoolState -Name $app.ApplicationPool -ErrorAction Stop
    Chk ($pool.Value -eq 'Started') $app.Path "application pool=$($pool.Value); does not prove successful enrollment"
    foreach ($auth in 'anonymousAuthentication','windowsAuthentication','basicAuthentication') {
      Get-WebConfigurationProperty -PSPath $app.PSPath -Filter "system.webServer/security/authentication/$auth" -Name enabled -ErrorAction Stop | ForEach-Object { "[INFO] ${auth}: $($_.Value)" }
    }
  }
  Get-WebBinding -Protocol https -ErrorAction Stop | Select-Object ItemXPath, BindingInformation, CertificateHash, CertificateStoreName | Format-Table -AutoSize | Out-String -Width 220
} catch { "ERROR: Enrollment endpoint configuration unreadable: $($_.Exception.Message)" }""",
       admin=True, requires="local AD CS role; optional IIS WebAdministration", tags=('inventory', 'security', 'baseline')),
    CA("ca-esc1-published-template-assessment", "Published template ESC1 enrollment risk assessment",
       "Checks publication, authentication policies, subject supply, approval gates and broad template/CA enrollment grants.",
       _CA_LOCAL + need_cmd("Get-ADObject", "ActiveDirectory module and directory read permissions") + CHK + r"""try {
  $dse = Get-ADRootDSE -ErrorAction Stop
  $pki = "CN=Public Key Services,CN=Services,$($dse.configurationNamingContext)"
  $cas = @(Get-ADObject -SearchBase "CN=Enrollment Services,$pki" -LDAPFilter '(objectClass=pKIEnrollmentService)' -Properties certificateTemplates,dNSHostName -ErrorAction Stop | Where-Object Name -eq $active)
  if ($cas.Count -ne 1) { Chk $null 'ESC1' 'local enterprise CA publication object not uniquely found; standalone CAs not covered'; return }
  $published = @($cas[0].certificateTemplates)
  if (-not $published.Count) { Chk $null 'ESC1' 'no published template names returned'; return }
  $config = Get-ItemProperty -LiteralPath $caKey -ErrorAction Stop
  if (-not $config.Security) { throw 'CA enrollment security descriptor unavailable' }
  $caSD = New-Object Security.AccessControl.RawSecurityDescriptor -ArgumentList $config.Security,0
  $policyRoot = Join-Path $caKey 'PolicyModules'
  $policyName = (Get-ItemProperty -LiteralPath $policyRoot -ErrorAction Stop).Active
  if (-not $policyName) { throw 'Active policy module unknown' }
  $policy = Get-ItemProperty -LiteralPath (Join-Path $policyRoot $policyName) -ErrorAction Stop
  $disposition = $policy.RequestDisposition
  $caPending = if ($null -eq $disposition) { $null } else { ($disposition -band 255) -eq 1 }
  $domainSID = (Get-ADDomain -ErrorAction Stop).DomainSID.Value
  $broad = @('S-1-1-0','S-1-5-11',"$domainSID-513","$domainSID-515")
  $authOids = @('1.3.6.1.5.5.7.3.2','1.3.6.1.4.1.311.20.2.2','1.3.6.1.5.2.3.4','2.5.29.37.0')
  $enroll = [guid]'0e10c968-78fb-11d2-90d4-00c04f79dc55'
  $templates = @(Get-ADObject -SearchBase "CN=Certificate Templates,$pki" -LDAPFilter '(objectClass=pKICertificateTemplate)' -Properties 'msPKI-Certificate-Name-Flag','msPKI-Enrollment-Flag','msPKI-RA-Signature',pKIExtendedKeyUsage,'msPKI-Certificate-Application-Policy' -ErrorAction Stop)
  if (-not $templates.Count) { Chk $null 'ESC1' 'no template objects returned'; return }
  foreach ($t in $templates | Where-Object { $_.Name -in $published }) {
    try {
      $subject = ($t.'msPKI-Certificate-Name-Flag' -band 1) -ne 0
      $approval = ($t.'msPKI-Enrollment-Flag' -band 2) -ne 0
      $signatures = [int]$t.'msPKI-RA-Signature'
      $eku = @($t.pKIExtendedKeyUsage); $appPolicy = @($t.'msPKI-Certificate-Application-Policy')
      $auth = (-not $eku.Count -or @($eku | Where-Object { $_ -in $authOids }).Count -gt 0)
      if ($appPolicy.Count) { $auth = $auth -and @($appPolicy | Where-Object { $_ -in $authOids }).Count -gt 0 }
      $acl = Get-Acl -LiteralPath "AD:\$($t.DistinguishedName)" -ErrorAction Stop
      $rules = @($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
      if (-not $rules.Count) { throw 'Template DACL has no returned ACEs; effective enrollment unknown' }
      $grants = @($rules | Where-Object {
        $_.AccessControlType -eq 'Allow' -and $_.IdentityReference.Value -in $broad -and
        (($_.ActiveDirectoryRights -band [DirectoryServices.ActiveDirectoryRights]::ExtendedRight) -ne 0) -and
        ($_.ObjectType -eq [guid]::Empty -or $_.ObjectType -eq $enroll)
      })
      $caGrants = @($caSD.DiscretionaryAcl | Where-Object { $_.AceType -eq 'AccessAllowed' -and $_.SecurityIdentifier.Value -in $broad -and ($_.AccessMask -band 512) -ne 0 })
      $denies = @($rules | Where-Object AccessControlType -eq 'Deny').Count + @($caSD.DiscretionaryAcl | Where-Object { [string]$_.AceType -match 'Denied' }).Count
      $gates = $subject -and $auth -and -not $approval -and $signatures -eq 0
      $candidate = $gates -and $grants.Count -gt 0 -and $caGrants.Count -gt 0 -and $caPending -eq $false
      # Denies/group intersections need a principal-specific access check. Never
      # convert an incomplete ACL read into a safe-template verdict.
      $ok = if (-not $gates -or $caPending -eq $true) { $true } elseif ($denies -or $null -eq $caPending -or -not $candidate) { $null } else { $false }
      Chk $ok $t.Name "published on $active; supplied subject=$subject; authentication=$auth; manager approval=$approval; RA signatures=$signatures; CA pending=$caPending; broad template grants=$($grants.Count); broad CA enroll grants=$($caGrants.Count); deny ACEs=$denies"
      "[INFO] EKU=$($eku -join ', '); application policies=$($appPolicy -join ', '); template enroll SIDs=$($grants.IdentityReference.Value -join ', '); CA enroll SIDs=$($caGrants.SecurityIdentifier.Value -join ', ')"
    } catch { Chk $null $t.Name "assessment incomplete: $($_.Exception.Message)" }
  }
  '[INFO] WARN means ESC1 candidate, not proven exploitation. Full effective access needs a specific principal, transitive groups, read rights, deny ACEs and CA policy-module behavior; narrow custom enrollment groups are not ruled safe.'
} catch { "ERROR: Cannot assess template risk: $($_.Exception.Message)" }""",
       admin=True, requires="local enterprise AD CS role; ActiveDirectory module and AD provider", timeout=300, tags=('diagnostic', 'security', 'compliance')),
    CA("ca-pending-request-age-detail", "Pending CA requests with submission times",
       "Shows request age evidence, requester and template for requests awaiting approval.",
       _CA_LOCAL + CHK + r"""try {
  $output = @(& certutil.exe -config $caConfig -view -restrict 'Disposition=9' -out 'RequestID,RequesterName,SubmittedWhen,CommonName,CertificateTemplate,DispositionMessage' csv 2>&1)
  if ($LASTEXITCODE -ne 0) { throw "certutil query exited $LASTEXITCODE : $($output -join ' ')" }
  if (-not $output.Count) { Chk $null 'Pending requests' 'query returned empty output' }
  else { $output; Chk $null 'Pending request review' 'review SubmittedWhen for backlog age; native output may report zero rows, and is localized' }
} catch { "ERROR: Pending requests refused or unreadable: $($_.Exception.Message)" }""",
       admin=True, requires="local AD CS role and CA database read rights", timeout=300, tags=('diagnostic', 'triage', 'security')),
    CA("ca-database-size-row-count", "CA database footprint and request row count",
       "Measures local CA database files and counts request rows through the read-only CA view interface.",
       _CA_LOCAL + CHK + r"""try {
  $config = Get-ItemProperty -LiteralPath $caKey -ErrorAction Stop
  $directory = [Environment]::ExpandEnvironmentVariables($config.DBDirectory)
  if (-not $directory) { throw 'DBDirectory missing or empty' }
  $files = @(Get-ChildItem -LiteralPath $directory -File -ErrorAction Stop)
  if (-not $files.Count) { Chk $null 'CA database files' 'directory returned no files' }
  $files | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize | Out-String -Width 220
  $view = New-Object -ComObject CertificateAuthority.View
  try {
    $view.OpenConnection($caConfig)
    $index = $view.GetColumnIndex($false,'RequestID'); $view.SetResultColumnCount(1); $view.SetResultColumn($index)
    $rows = $view.OpenView(); $count = 0L; $clock = [Diagnostics.Stopwatch]::StartNew(); $complete = $true
    try {
      while ($rows.Next() -ne -1) {
        $count++
        if ($clock.Elapsed.TotalSeconds -gt 60) { $complete = $false; break }
      }
    } finally { if ($rows) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($rows) } }
    Chk $null 'CA request rows' "count=$count; complete=$complete; stopped after 60 seconds if incomplete; enumeration is not a transactional snapshot"
  } finally { if ($view) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($view) } }
} catch { "ERROR: Cannot inspect CA database: $($_.Exception.Message)" }""",
       admin=True, requires="local AD CS role and CA database read rights", timeout=180, tags=('inventory', 'storage', 'performance')),
    _RDLOCAL("rds-license-availability-grace", "RDS license server availability and grace days",
       "Reads session-host licensing mode, configured servers, grace days and reachable CAL inventory.",
       need_admin("RDS licensing WMI reads require elevation") + CHK + r"""try {
  $role = Get-Item 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\RCM\Licensing Core' -ErrorAction Stop
  $setting = Get-CimInstance -Namespace root/cimv2/TerminalServices -ClassName Win32_TerminalServiceSetting -ErrorAction Stop
  if (-not $setting) { 'NOT AVAILABLE: no Terminal Services settings returned.'; return }
  "[INFO] Licensing mode=$($setting.LicensingType); policy source=$($setting.PolicySourceLicensingType)"
  $grace = Invoke-CimMethod -InputObject $setting -MethodName GetGracePeriodDays -ErrorAction Stop
  if ($grace.ReturnValue -ne 0 -or $null -eq $grace.DaysLeft) { Chk $null 'Grace period' "method result=$($grace.ReturnValue); days unavailable" }
  else { Chk $null 'Grace period' "$($grace.DaysLeft) days left; zero is normal on a correctly licensed host" }
  $result = Invoke-CimMethod -InputObject $setting -MethodName GetSpecifiedLicenseServerList -ErrorAction Stop
  if ($result.ReturnValue -ne 0) { throw "License server list method returned $($result.ReturnValue)" }
  $servers = @($result.SpecifiedLSList | Where-Object { $_ })
  Chk ($servers.Count -gt 0) 'Specified license servers' "$($servers -join ', '); inventory reads do not prove CAL compatibility or successful checkout"
  foreach ($server in $servers) {
    try {
      $packs = @(Get-CimInstance -ComputerName $server -Namespace root/cimv2 -ClassName Win32_TSLicenseKeyPack -ErrorAction Stop)
      if (-not $packs.Count) { Chk $null $server 'no CAL packs returned'; continue }
      $packs | Select-Object ProductVersion, TypeAndModel, TotalLicenses, IssuedLicenses, AvailableLicenses, ExpirationDate | Format-Table -AutoSize | Out-String -Width 220
      Chk $null $server 'license inventory readable; per-user CAL reporting and version compatibility need review'
    } catch { Chk $null $server "license inventory refused/unavailable: $($_.Exception.Message)" }
  }
} catch { "ERROR: RDS licensing role missing or unreadable: $($_.Exception.Message)" }""",
       admin=True, requires="RDS Session Host; licensing WMI access on license servers", timeout=300, tags=('diagnostic', 'health', 'triage')),
    _RDLOCAL("rds-fslogix-container-failures", "FSLogix profile containers and recent failures",
       "Reads profile paths, SMB reachability, current attachments and recent profile log errors.",
       need_cmd("Get-SmbConnection", "SMB client cmdlets") + need_admin("FSLogix session configuration requires elevation") + CHK + r"""try {
  $key = 'HKLM:\SOFTWARE\FSLogix\Profiles'
  if (-not (Test-Path -LiteralPath $key -ErrorAction Stop)) { 'NOT AVAILABLE: FSLogix Profiles not configured.'; return }
  $config = Get-ItemProperty -LiteralPath $key -ErrorAction Stop
  Chk ($config.Enabled -eq 1) 'FSLogix Profiles enabled' "Enabled=$($config.Enabled); VHDLocations=$($config.VHDLocations -join '; '); CCDLocations=$($config.CCDLocations -join '; ')"
  foreach ($path in @($config.VHDLocations | Where-Object { $_ })) {
    try { Chk (Test-Path -LiteralPath ([Environment]::ExpandEnvironmentVariables($path)) -ErrorAction Stop) $path 'path probe uses this admin identity, not the affected user' }
    catch { Chk $null $path $_.Exception.Message }
  }
  if (-not $config.VHDLocations -and -not $config.CCDLocations) { Chk $null 'Container locations' 'no explicit locations returned' }
  $sessions = Join-Path $key 'Sessions'
  if (Test-Path -LiteralPath $sessions -ErrorAction Stop) {
    $items = @(Get-ChildItem -LiteralPath $sessions -ErrorAction Stop)
    if (-not $items.Count) { '[INFO] No FSLogix session records.' }
    foreach ($item in $items) { Get-ItemProperty -LiteralPath $item.PSPath -ErrorAction Stop | Select-Object PSChildName, VHDPath, ProfilePath, Status, Reason, ErrorCode | Format-List | Out-String -Width 220 }
  } else { '[INFO] No FSLogix session registry key; no attachment evidence.' }
  $logDir = if ($config.LogDir) { [Environment]::ExpandEnvironmentVariables($config.LogDir) } else { "$env:ProgramData\FSLogix\Logs" }
  $logs = @(Get-ChildItem -LiteralPath (Join-Path $logDir 'Profile') -Filter *.log -File -ErrorAction Stop | Sort-Object LastWriteTime -Descending | Select-Object -First 2)
  if (-not $logs.Count) { Chk $null 'FSLogix logs' 'no profile logs returned' }
  foreach ($log in $logs) {
    "== $($log.FullName); modified=$($log.LastWriteTime)"
    $hits = @(Get-Content -LiteralPath $log.FullName -Tail 5000 -ErrorAction Stop | Select-String -Pattern '\[ERROR\]|\[WARN\]|LoadProfile failed|Attach.*fail')
    Chk $null 'Profile log sample' "$($hits.Count) error/warning lines in last 5,000 lines; not a full container validation"
    $hits | Select-Object -Last 40 | ForEach-Object { $_.Line }
  }
} catch { "ERROR: Cannot inspect FSLogix: $($_.Exception.Message)" }""",
       admin=True, requires="FSLogix Profiles installation/configuration", tags=('diagnostic', 'health', 'storage')),
    _RDLOCAL("rds-gateway-bound-certificate", "RD Gateway local certificate binding and expiry",
       "Reads the gateway certificate hash and checks the exact bound certificate in the machine store.",
       need_admin("RD Gateway configuration requires elevation") + CHK + r"""try {
  $key = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Terminal Server Gateway\Config\Core'
  if (-not (Test-Path -LiteralPath $key -ErrorAction Stop)) { 'NOT AVAILABLE: no local RD Gateway configuration.'; return }
  $config = Get-ItemProperty -LiteralPath $key -ErrorAction Stop
  $hash = $config.SSLCertificateSHA1Hash
  $thumb = if ($hash -is [byte[]]) { ($hash | ForEach-Object { $_.ToString('X2') }) -join '' } else { ([string]$hash -replace '\s','') }
  if (-not $thumb) { Chk $null 'Gateway binding' 'SSLCertificateSHA1Hash empty or absent'; return }
  $cert = Get-Item -LiteralPath "Cert:\LocalMachine\My\$thumb" -ErrorAction Stop
  $days = [math]::Floor(($cert.NotAfter-(Get-Date)).TotalDays)
  Chk ($cert.NotBefore -le (Get-Date) -and $days -gt 30 -and $cert.HasPrivateKey) 'RD Gateway certificate' "thumbprint=$thumb; subject=$($cert.Subject); expires=$($cert.NotAfter); days=$days; private key=$($cert.HasPrivateKey)"
  $cert | Select-Object DnsNameList, EnhancedKeyUsageList | Format-List | Out-String -Width 220
  '[INFO] Checks configured local binding only; TLS negotiation, chain trust, name matching and gateway service key access remain unverified.'
} catch { "ERROR: Cannot inspect gateway certificate: $($_.Exception.Message)" }""",
       admin=True, requires="local RD Gateway role", tags=('diagnostic', 'health', 'security')),
    RD("rds-broker-database-connectivity", "RDS broker HA database read connectivity",
       "Tests a SELECT against the configured broker HA database without exposing connection credentials.",
       need_cmd("Get-RDConnectionBrokerHighAvailability", "RemoteDesktop HA management cmdlets") + CHK + r"""try {
  $ha = Get-RDConnectionBrokerHighAvailability -ErrorAction Stop
  if (-not $ha -or -not $ha.DatabaseConnectionString) { Chk $null 'Broker database' 'no HA connection string; standalone WID deployment is not covered'; return }
  "[INFO] Active broker=$($ha.ActiveManagementServer); client access name=$($ha.ClientAccessName)"
  $connection = New-Object System.Data.Odbc.OdbcConnection $ha.DatabaseConnectionString
  try {
    $connection.ConnectionTimeout = 10
    $connection.Open(); $cmd = $connection.CreateCommand(); $cmd.CommandTimeout = 15
    $cmd.CommandText = "SELECT DB_NAME() AS DatabaseName, DATABASEPROPERTYEX(DB_NAME(),'Status') AS DatabaseStatus"
    $reader = $cmd.ExecuteReader()
    try {
      if ($reader.Read()) { Chk ([string]$reader['DatabaseStatus'] -eq 'ONLINE') 'Broker database' "name=$($reader['DatabaseName']); status=$($reader['DatabaseStatus']); read performed as current admin" }
      else { Chk $null 'Broker database' 'query returned no rows' }
    } finally { $reader.Dispose(); $cmd.Dispose() }
  } finally { $connection.Dispose() }
  '[INFO] This does not verify broker service-identity permissions, HA replication or a session placement transaction.'
} catch { Chk $null 'Broker database' "configuration/connection/query refused or failed ($($_.Exception.GetType().Name)); connection strings and SQL exception text withheld to protect credentials" }""",
       requires="RDS Connection Broker HA and configured ODBC driver", timeout=90, tags=('diagnostic', 'health', 'network')),
    _RDLOCAL("rds-per-session-cpu-memory-sample", "Session-host CPU and memory by session",
       "Samples process CPU deltas and sums working sets per session to locate resource pressure.",
       need_admin("complete process counters require elevation") + CHK + r"""try {
  if (-not (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\RCM\Licensing Core' -ErrorAction Stop)) { 'NOT AVAILABLE: no RDS Session Host licensing configuration.'; return }
  $cores = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).NumberOfLogicalProcessors
  if ($cores -le 0) { throw 'Logical processor count unavailable' }
  $before = @{}; $clock = [Diagnostics.Stopwatch]::StartNew()
  foreach ($p in Get-CimInstance Win32_Process -ErrorAction Stop) {
    $before["$($p.ProcessId)|$($p.CreationDate)"] = @{CPU=([decimal]$p.KernelModeTime+[decimal]$p.UserModeTime); At=$clock.Elapsed.TotalSeconds}
  }
  Start-Sleep -Seconds 5
  $rows = @(foreach ($p in Get-CimInstance Win32_Process -ErrorAction Stop) {
    if ($p.ProcessId -eq 0) { continue }
    $old = $before["$($p.ProcessId)|$($p.CreationDate)"]; $pct = $null
    if ($old) {
      $dt = $clock.Elapsed.TotalSeconds-$old.At; $delta = ([decimal]$p.KernelModeTime+[decimal]$p.UserModeTime)-$old.CPU
      if ($dt -gt 0 -and $delta -ge 0) { $pct = 100*$delta/10000000/$dt/$cores }
    }
    [pscustomobject]@{Session=$p.SessionId; PID=$p.ProcessId; CPU=$pct; WorkingSet=$p.WorkingSetSize}
  })
  if (-not $rows.Count) { Chk $null 'Session sample' 'no processes returned'; return }
  $rows | Group-Object Session | ForEach-Object {
    [pscustomobject]@{Session=$_.Name; Processes=$_.Count; CPUPercent=[math]::Round(($_.Group | Measure-Object CPU -Sum).Sum,2); UnsampledCPU=@($_.Group | Where-Object { $null -eq $_.CPU }).Count; WorkingSetMB=[math]::Round(($_.Group | Measure-Object WorkingSet -Sum).Sum/1MB,1)}
  } | Sort-Object CPUPercent -Descending | Format-Table -AutoSize | Out-String -Width 220
  '[INFO] CPU is percent of total host capacity. Working sets double-count shared pages. Includes session 0 services; exited processes are excluded.'
} catch { "ERROR: Cannot sample session resources: $($_.Exception.Message)" }""",
       admin=True, requires="local RDS Session Host", timeout=60, tags=('diagnostic', 'performance', 'triage')),
    WS("wsus-stack-database-health", "WSUS IIS, service and database health",
       "Checks the local WSUS stack and reads SUSDB file sizes through an integrated SQL connection.",
       need_admin("local WSUS and IIS configuration require elevation") + CHK + r"""try {
  $setup = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Update Services\Server\Setup' -ErrorAction Stop
  foreach ($name in 'WsusService','W3SVC','WAS') {
    try { $svc = Get-Service $name -ErrorAction Stop; Chk ($svc.Status -eq 'Running') $name $svc.Status }
    catch { Chk $null $name $_.Exception.Message }
  }
  if (Get-Command Get-WebAppPoolState -ErrorAction SilentlyContinue) {
    try { $pool = Get-WebAppPoolState WsusPool -ErrorAction Stop; Chk ($pool.Value -eq 'Started') 'WsusPool' $pool.Value }
    catch { Chk $null 'WsusPool' $_.Exception.Message }
  } else { Chk $null 'WsusPool' 'WebAdministration module unavailable' }
  $instance = [string]$setup.SqlServerName
  if (-not $instance) { throw 'SqlServerName is empty; database endpoint unknown' }
  if ($instance -match 'MICROSOFT##WID') {
    $svc = Get-Service 'MSSQL$MICROSOFT##WID' -ErrorAction Stop
    Chk ($svc.Status -eq 'Running') 'WID service' $svc.Status
    $instance = 'np:\\.\pipe\MICROSOFT##WID\tsql\query'
  } elseif ($instance -match 'MICROSOFT##SSEE') { $instance = 'np:\\.\pipe\MSSQL$MICROSOFT##SSEE\sql\query' }
  $builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
  $builder.DataSource = $instance; $builder.InitialCatalog = 'SUSDB'; $builder.IntegratedSecurity = $true; $builder.ConnectTimeout = 10
  $connection = New-Object System.Data.SqlClient.SqlConnection $builder.ConnectionString
  try {
    $connection.Open(); $command = $connection.CreateCommand(); $command.CommandTimeout = 20
    $command.CommandText = 'SELECT name,type_desc,CAST(size AS bigint)*8192 AS AllocatedBytes,physical_name FROM sys.database_files'
    $reader = $command.ExecuteReader(); $count = 0
    try { while ($reader.Read()) { $count++; "[INFO] $($reader['name']) $($reader['type_desc']): $($reader['AllocatedBytes']) bytes; $($reader['physical_name'])" } }
    finally { $reader.Dispose(); $command.Dispose() }
    if (-not $count) { Chk $null 'SUSDB' 'query returned no file rows' } else { Chk $true 'SUSDB connectivity' 'SELECT succeeded; allocated file size is not used space' }
  } finally { $connection.Dispose() }
} catch { "ERROR: WSUS stack/database read failed: $($_.Exception.Message)" }""",
       admin=True, requires="local WSUS role; SQL integrated read access; optional WebAdministration", tags=('diagnostic', 'health', 'storage')),
    WS("wsus-content-download-failures", "WSUS content download failure evidence",
       "Shows download errors and the newest WSUS log error lines without retrying downloads.",
       _EVENT_DETAIL + r"""Read-RoleEvents 'Application' @(364) 14
try {
  $setup = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Update Services\Server\Setup' -ErrorAction Stop
  if (-not $setup.TargetDir) { throw 'WSUS TargetDir is empty' }
  $path = Join-Path $setup.TargetDir 'LogFiles\SoftwareDistribution.log'
  $lines = @(Get-Content -LiteralPath $path -Tail 10000 -ErrorAction Stop)
  if (-not $lines.Count) { Chk $null 'WSUS log' 'file empty'; return }
  $hits = @($lines | Select-String -Pattern 'ContentSyncAgent|download.*fail|WebException|BITS.*error' | Select-Object -Last 50)
  Chk $null 'Download log evidence' "$($hits.Count) matches in last 10,000 lines; current file only, timestamps may be old"
  $hits | ForEach-Object { $_.Line }
} catch { "ERROR: Cannot read WSUS download log: $($_.Exception.Message)" }""",
       admin=True, requires="local WSUS role and readable Application/WSUS logs", tags=('diagnostic', 'triage', 'network')),
    WS("wsus-approved-client-compliance", "WSUS client compliance for approved installs",
       "Counts applicable approved-install states per client, retaining unknown and stale-report status.",
       CHK + r"""try {
  $w = Get-WsusServer -ErrorAction Stop
  $clients = @($w.GetComputerTargets())
  if (-not $clients.Count) { Chk $null 'WSUS clients' 'no computer targets returned'; return }
  $scope = New-Object Microsoft.UpdateServices.Administration.UpdateScope
  $scope.ApprovedStates = [Microsoft.UpdateServices.Administration.ApprovedStates]::LatestRevisionApproved
  foreach ($client in $clients) {
    try {
      $rows = @($client.GetUpdateInstallationInfoPerUpdate($scope) | Where-Object { [string]$_.UpdateApprovalAction -eq 'Install' })
      $bad = @($rows | Where-Object { [string]$_.UpdateInstallationState -in 'NotInstalled','Downloaded','Failed','InstalledPendingReboot' })
      $unknown = @($rows | Where-Object { [string]$_.UpdateInstallationState -notin 'Installed','NotApplicable','NotInstalled','Downloaded','Failed','InstalledPendingReboot' })
      $fresh = $client.LastReportedStatusTime -ge (Get-Date).AddDays(-7)
      $ok = if (-not $rows.Count -or $unknown.Count -or -not $fresh) { $null } else { $bad.Count -eq 0 }
      Chk $ok $client.FullDomainName "approved install rows=$($rows.Count); outstanding=$($bad.Count); unknown=$($unknown.Count); reported=$($client.LastReportedStatusTime); stale after 7 days"
      $rows | Group-Object UpdateInstallationState | Select-Object Name, Count | Format-Table -AutoSize | Out-String -Width 220
    } catch { Chk $null $client.FullDomainName $_.Exception.Message }
  }
} catch { "ERROR: Cannot read WSUS compliance: $($_.Exception.Message)" }""",
       timeout=900, tags=('diagnostic', 'health', 'compliance')),
    WS("wsus-superseded-cleanup-review", "WSUS superseded cleanup review candidates",
       "Lists old superseded revisions with approvals and needed counts; never declines or deletes updates.",
       CHK + r"""try {
  $w = Get-WsusServer -ErrorAction Stop
  $scope = New-Object Microsoft.UpdateServices.Administration.UpdateScope
  $scope.IsSuperseded = $true
  $updates = @($w.GetUpdates($scope) | Where-Object { -not $_.IsDeclined -and $_.ArrivalDate -lt (Get-Date).AddDays(-90) })
  if (-not $updates.Count) { '[INFO] No non-declined superseded updates older than 90 days returned.' }
  $computerScope = New-Object Microsoft.UpdateServices.Administration.ComputerTargetScope
  foreach ($u in $updates) {
    try {
      $summary = $u.GetSummary($computerScope)
      $approvals = @($u.GetUpdateApprovals())
      Chk $null $u.Title "arrived=$($u.ArrivalDate); approvals=$($approvals.Count); needed=$($summary.NotInstalledCount); failed=$($summary.FailedCount); unknown=$($summary.UnknownCount); revision=$($u.Id.RevisionNumber)"
    } catch { "ERROR: Update $($u.Id.UpdateId): $($_.Exception.Message)" }
  }
  '[INFO] Candidates require replacement applicability, approval and retention review; superseded alone does not mean safe to remove.'
} catch { "ERROR: Cannot enumerate superseded updates: $($_.Exception.Message)" }""",
       timeout=900, tags=('inventory', 'triage', 'storage')),
    WS("wsus-sync-error-history", "WSUS synchronization errors across recent attempts",
       "Shows error text for the newest 20 synchronization attempts and upstream connection settings.",
       CHK + r"""try {
  $w = Get-WsusServer -ErrorAction Stop; $subscription = $w.GetSubscription()
  $config = $w.GetConfiguration()
  "[INFO] Microsoft Update=$($config.SyncFromMicrosoftUpdate); upstream=$($config.UpstreamWsusServerName):$($config.UpstreamWsusServerPortNumber); SSL=$($config.UpstreamWsusServerUseSsl)"
  $history = @($subscription.GetSynchronizationHistory() | Sort-Object StartTime -Descending | Select-Object -First 20)
  if (-not $history.Count) { Chk $null 'WSUS sync history' 'no synchronization attempts returned'; return }
  foreach ($h in $history) {
    $ok = if ([string]$h.Result -eq 'Succeeded') { $true } elseif ([string]$h.Result -eq 'Failed') { $false } else { $null }
    Chk $ok 'Synchronization attempt' "start=$($h.StartTime); end=$($h.EndTime); result=$($h.Result)"
    $h | Format-List * | Out-String -Width 240
  }
} catch { "ERROR: Cannot read synchronization history: $($_.Exception.Message)" }""",
       timeout=300, tags=('diagnostic', 'health', 'network')),
    SG("storage-vss-writer-provider-health", "VSS writer and provider health",
       "Reads writer states, provider registrations and recent VSS errors without starting a snapshot.",
       need_cmd("vssadmin.exe", "VSS administrative tools") + need_admin("VSS writer enumeration requires elevation") + _EVENT_DETAIL + r"""foreach ($kind in 'writers','providers') {
  try {
    $output = @(& vssadmin.exe list $kind 2>&1); $code = $LASTEXITCODE
    if ($code -ne 0) { throw "vssadmin exited $code : $($output -join ' ')" }
    $output
    if (-not $output.Count) { Chk $null "VSS $kind" 'empty command output' }
    else { Chk $null "VSS $kind" 'review native state and last-error fields above; localized text is not interpreted as a health verdict' }
  } catch { "ERROR: Cannot read VSS ${kind}: $($_.Exception.Message)" }
}
Read-RoleEvents 'Application' @(8193,12289,12293) 7""",
       admin=True, requires="VSS tools", tags=('diagnostic', 'health', 'storage')),
    SG("storage-thin-pool-headroom", "Thin virtual disk promises versus pool headroom",
       "Compares thin virtual capacity and physical pool allocation; SAN backing capacity remains external.",
       need_cmd("Get-StoragePool", "Storage Spaces cmdlets") + CHK + r"""try {
  $pools = @(Get-StoragePool -IsPrimordial $false -ErrorAction Stop)
  if (-not $pools.Count) { Chk $null 'Thin provisioning' 'no non-primordial pools; SAN thin capacity must be checked on the array'; return }
  foreach ($pool in $pools) {
    try {
      $disks = @($pool | Get-VirtualDisk -ErrorAction Stop)
      $thin = @($disks | Where-Object ProvisioningType -eq 'Thin')
      if (-not $thin.Count) { "[INFO] $($pool.FriendlyName): no thin virtual disks returned."; continue }
      if ($pool.Size -le 0 -or $null -eq $pool.AllocatedSize) { Chk $null $pool.FriendlyName 'pool capacity unavailable'; continue }
      $free = $pool.Size-$pool.AllocatedSize; $pct = 100*$free/$pool.Size
      Chk ($pct -ge 15) $pool.FriendlyName ("physical free={0:N1} GB ({1:N1}%); 15% triage threshold" -f ($free/1GB),$pct)
      $thin | Select-Object FriendlyName, Size, FootprintOnPool, HealthStatus, OperationalStatus | Format-Table -AutoSize | Out-String -Width 220
      '[INFO] Size is logical promised bytes; FootprintOnPool includes resiliency overhead. Do not subtract logical size directly from physical pool size.'
    } catch { "ERROR: Pool $($pool.FriendlyName): $($_.Exception.Message)" }
  }
} catch { "ERROR: Cannot read thin pool capacity: $($_.Exception.Message)" }""",
       admin=True, requires="Storage Spaces pools and Storage module", tags=('diagnostic', 'health', 'storage')),
    SG("storage-refs-integrity-evidence", "ReFS health and integrity evidence",
       "Shows ReFS volume state, root integrity settings and retained ReFS errors without running repairs.",
       _EVENT_DETAIL + r"""try {
  $volumes = @(Get-Volume -ErrorAction Stop | Where-Object FileSystem -eq 'ReFS')
  if (-not $volumes.Count) { 'NOT AVAILABLE: no ReFS volumes returned.'; return }
  foreach ($v in $volumes) {
    $ok = if (-not $v.HealthStatus) { $null } else { [string]$v.HealthStatus -eq 'Healthy' }
    Chk $ok $v.Path "health=$($v.HealthStatus); operational=$($v.OperationalStatus)"
    if (Get-Command Get-FileIntegrity -ErrorAction SilentlyContinue) {
      try { Get-FileIntegrity -FileName $v.Path -ErrorAction Stop | Format-List | Out-String -Width 220 }
      catch { "ERROR: Root integrity setting $($v.Path): $($_.Exception.Message)" }
    } else { Chk $null 'Integrity streams' 'Get-FileIntegrity unavailable' }
  }
  Read-RoleEvents 'Microsoft-Windows-ReFS/Operational' $null 14
  '[INFO] Root integrity policy is not a recursive integrity scan. Retained events do not prove all files have been scrubbed.'
} catch { "ERROR: Cannot inspect ReFS volumes: $($_.Exception.Message)" }""",
       admin=True, requires="ReFS volumes and Storage module", tags=('diagnostic', 'health', 'storage')),
    SG("storage-spaces-repair-job-progress", "Storage Spaces repair job progress",
       "Lists active storage jobs and unhealthy physical disks without starting or resuming work.",
       need_cmd("Get-StorageJob", "Storage Spaces cmdlets") + CHK + r"""try {
  $jobs = @(Get-StorageJob -ErrorAction Stop)
  if (-not $jobs.Count) { Chk $null 'Storage jobs' 'no active jobs; does not prove redundancy is healthy' }
  $jobs | Select-Object Name, JobState, PercentComplete, BytesProcessed, BytesTotal, ElapsedTime, ErrorCode, ErrorDescription | Format-List | Out-String -Width 220
  $disks = @(Get-PhysicalDisk -ErrorAction Stop)
  if (-not $disks.Count) { Chk $null 'Physical disks' 'no disks returned' }
  foreach ($d in $disks) { Chk $(if ($d.HealthStatus) { [string]$d.HealthStatus -eq 'Healthy' } else { $null }) $d.FriendlyName "serial=$($d.SerialNumber); health=$($d.HealthStatus); operational=$($d.OperationalStatus -join ', ')" }
} catch { "ERROR: Cannot inspect repair work: $($_.Exception.Message)" }""",
       admin=True, requires="Storage Spaces and Storage module", tags=('diagnostic', 'health', 'storage')),
    CL("cluster-node-departure-quorum-review", "Local node departure quorum preflight",
       "Models current votes without this node and lists owned roles; dynamic quorum and workload safety still need review.",
       CHK + r"""try {
  $cluster = Get-Cluster -ErrorAction Stop; $nodes = @(Get-ClusterNode -ErrorAction Stop)
  $local = @($nodes | Where-Object { ($_.Name -split '\.')[0] -ieq $env:COMPUTERNAME })
  if ($local.Count -ne 1) { Chk $null 'Node departure' 'local node not uniquely identified'; return }
  $quorum = Get-ClusterQuorum -ErrorAction Stop
  $nodes | Select-Object Name, State, NodeWeight, DynamicWeight | Format-Table -AutoSize | Out-String -Width 220
  $witness = $quorum.QuorumResource
  "[INFO] quorum=$($quorum.QuorumType); witness=$($witness.Name); witness state=$($witness.State); dynamic quorum=$($cluster.DynamicQuorum); witness vote=$($cluster.WitnessDynamicWeight)"
  if ([string]$quorum.QuorumType -notin 'NodeMajority','NodeAndDiskMajority','NodeAndFileShareMajority' -or @($nodes | Where-Object { $null -eq $_.DynamicWeight }).Count) {
    Chk $null 'Current-vote model' 'unsupported quorum type or unavailable vote data'
  } else {
    $total = [int](($nodes | Measure-Object DynamicWeight -Sum).Sum) + [int]$cluster.WitnessDynamicWeight
    $remaining = [int](($nodes | Where-Object { $_.State -eq 'Up' -and $_.Name -ne $local[0].Name } | Measure-Object DynamicWeight -Sum).Sum)
    if ($witness -and $witness.State -eq 'Online') { $remaining += [int]$cluster.WitnessDynamicWeight }
    Chk ($remaining -gt ($total/2)) 'Current-vote majority after local departure' "remaining reachable votes=$remaining of $total; conservative snapshot, no prediction of vote reassignment"
  }
  $owned = @(Get-ClusterGroup -ErrorAction Stop | Where-Object { $_.OwnerNode.Name -eq $local[0].Name })
  Chk ($owned.Count -eq 0) 'Roles still owned locally' "$($owned.Count); do not stop a node with undrained workloads"
  $owned | Select-Object Name, State, GroupType | Format-Table -AutoSize | Out-String -Width 220
  Chk $null 'Safe to leave' 'not established: check remaining workload capacity, possible owners, storage paths and witness reachability from surviving nodes'
} catch { "ERROR: Cannot assess node departure: $($_.Exception.Message)" }""",
       admin=True, tags=('diagnostic', 'health', 'triage')),
    CL("cluster-failover-dependency-history", "Cluster resource dependencies and failure history",
       "Prints dependency expressions and recent resource failures with full event detail.",
       need_cmd("Get-ClusterResourceDependency", "FailoverClusters dependency cmdlets") + _EVENT_DETAIL + r"""try {
  $resources = @(Get-ClusterResource -ErrorAction Stop)
  if (-not $resources.Count) { '[INFO] No cluster resources returned.' }
  foreach ($r in $resources) {
    try {
      "== $($r.Name); group=$($r.OwnerGroup); owner=$($r.OwnerNode); state=$($r.State)"
      $deps = @(Get-ClusterResourceDependency -Resource $r.Name -ErrorAction Stop)
      if (-not $deps.Count) { '[INFO] No dependency expression returned.' } else { $deps | Format-List | Out-String -Width 240 }
    } catch { "ERROR: Dependencies for $($r.Name): $($_.Exception.Message)" }
  }
} catch { "ERROR: Cannot enumerate cluster resources: $($_.Exception.Message)" }
Read-RoleEvents 'System' @(1069,1205,1254,1641) 14
Read-RoleEvents 'Microsoft-Windows-FailoverClustering/Operational' $null 14
'[INFO] Events are retained on this node only; correlate other nodes for a complete failover timeline.'""",
       admin=True, tags=('diagnostic', 'triage')),
    CL("cluster-csv-latency-redirection-detail", "CSV latency counters and redirected I/O reasons",
       "Reads per-node redirection reasons and available CSV latency counters without changing CSV ownership.",
       need_cmd("Get-ClusterSharedVolumeState", "CSV state cmdlets") + CHK + r"""try {
  $states = @(Get-ClusterSharedVolumeState -ErrorAction Stop)
  if (-not $states.Count) { Chk $null 'CSV state' 'no CSV state records returned'; return }
  foreach ($s in $states) {
    Chk $null "$($s.Name) / $($s.Node)" "state=$($s.StateInfo); file redirected reason=$($s.FileSystemRedirectedIOReason); block redirected reason=$($s.BlockRedirectedIOReason)"
  }
  try {
    $sets = @(Get-Counter -ListSet '*Cluster*CSV*' -ErrorAction Stop)
    $paths = @($sets.PathsWithInstances | Where-Object { $_ -match '(?i)latency|avg\..*sec|redirected' })
    if (-not $paths.Count) { Chk $null 'CSV counters' 'no matching instances; counter names are localized'; return }
    $sample = Get-Counter -Counter $paths -MaxSamples 1 -ErrorAction Stop
    foreach ($c in $sample.CounterSamples) {
      if ($c.Status -ne 0) { Chk $null $c.Path "counter status=$($c.Status)" }
      else { "[INFO] $($c.Path) = $($c.CookedValue); counter type=$($c.CounterType)" }
    }
    '[INFO] Single sample; preserve native counter units. Redirected I/O can be expected during backup or maintenance.'
  } catch { "ERROR: CSV counters missing or unreadable: $($_.Exception.Message)" }
} catch { "ERROR: Cannot read CSV detail: $($_.Exception.Message)" }""",
       admin=True, tags=('diagnostic', 'performance', 'storage')),
]

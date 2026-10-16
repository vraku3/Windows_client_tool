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

SCRIPTS = [
    # ---- DNS Server (12) ---------------------------------------------------------------------
    DN("dns_zone_records", "Record counts per zone",
       "How many records each zone holds, by type.",
       r"""Get-DnsServerZone | Where-Object { -not $_.IsAutoCreated } | ForEach-Object { $z = $_.ZoneName; Get-DnsServerResourceRecord -ZoneName $z -ErrorAction SilentlyContinue | Group-Object RecordType | ForEach-Object { '{0,-40} {1,-6} {2,6}' -f $z, $_.Name, $_.Count } }"""),
    DN("dns_stale_records", "Stale dynamic records (older than 30 days)",
       "Dynamic A records whose timestamp is older than 30 days -- what scavenging would remove.",
       r"""Get-DnsServerZone | Where-Object { $_.ZoneType -eq 'Primary' -and -not $_.IsAutoCreated -and -not $_.IsReverseLookupZone } | ForEach-Object { $z = $_.ZoneName
  Get-DnsServerResourceRecord -ZoneName $z -RRType A -ErrorAction SilentlyContinue | Where-Object { $_.Timestamp -and $_.Timestamp -lt (Get-Date).AddDays(-30) } | ForEach-Object { '{0,-30} {1,-30} {2:yyyy-MM-dd} {3}' -f $z, $_.HostName, $_.Timestamp, $_.RecordData.IPv4Address } }"""),
    DN("dns_duplicate_ips", "Several names for one IP",
       "A records sharing an IP address -- stale records left behind by DHCP changes.",
       r"""Get-DnsServerZone | Where-Object { $_.ZoneType -eq 'Primary' -and -not $_.IsReverseLookupZone -and -not $_.IsAutoCreated } | ForEach-Object { $z = $_.ZoneName; Get-DnsServerResourceRecord -ZoneName $z -RRType A -ErrorAction SilentlyContinue | Where-Object HostName -ne '@' | ForEach-Object { [pscustomobject]@{ IP=$_.RecordData.IPv4Address.IPAddressToString; Name="$($_.HostName).$z" } } } |
  Group-Object IP | Where-Object Count -gt 1 | ForEach-Object { '[WARN] {0,-16} {1}' -f $_.Name, (($_.Group.Name) -join ', ') }"""),
    DN("dns_reverse_zones", "Reverse lookup zones",
       "Reverse zones present -- missing ones make PTR lookups (and some logging) fail.",
       T("Get-DnsServerZone | Where-Object IsReverseLookupZone | Select-Object ZoneName, ZoneType, IsDsIntegrated, DynamicUpdate")),
    DN("dns_zone_transfers", "Zone transfer settings",
       "Who may request a zone transfer per zone -- 'to any server' leaks the whole zone.",
       r"""Get-DnsServerZone | Where-Object { $_.ZoneType -eq 'Primary' -and -not $_.IsAutoCreated } | ForEach-Object { '{0} {1,-40} {2}' -f $(if ($_.SecureSecondaries -eq 'TransferAnyServer') {'[WARN]'} else {'[OK]  '}), $_.ZoneName, $_.SecureSecondaries }"""),
    DN("dns_dynamic_update", "Dynamic update per zone",
       "Whether each zone accepts secure, non-secure or no dynamic updates -- non-secure lets "
       "anyone register any name.",
       r"""Get-DnsServerZone | Where-Object { -not $_.IsAutoCreated } | ForEach-Object { '{0} {1,-40} {2}' -f $(if ($_.DynamicUpdate -eq 'NonsecureAndSecure') {'[WARN]'} else {'[OK]  '}), $_.ZoneName, $_.DynamicUpdate }"""),
    DN("dns_server_settings", "DNS server settings",
       "Listening addresses, round robin, cache locking, socket pool and the global query "
       "block list.",
       L("Get-DnsServerSetting -All | Select-Object ListeningIPAddress, RoundRobin, LocalNetPriority, EnableDnsSec, NameCheckFlag, BindSecondaries; Get-DnsServerCache | Select-Object LockingPercent, MaxTTL; Get-DnsServerGlobalQueryBlockList")),
    DN("dns_conditional", "Conditional forwarders",
       "Conditional forwarder zones and their master servers.",
       T("Get-DnsServerZone | Where-Object ZoneType -eq 'Forwarder' | Select-Object ZoneName, IsDsIntegrated, @{n='Masters';e={$_.MasterServers -join ', '}}")),
    DN("dns_dnssec", "DNSSEC signed zones",
       "Zones signed with DNSSEC and their key status.",
       r"""$z = Get-DnsServerZone | Where-Object IsSigned
if (-not $z) { '[INFO] No DNSSEC-signed zone.'; return }
$z | ForEach-Object { "== $($_.ZoneName)"; Get-DnsServerSigningKey -ZoneName $_.ZoneName -ErrorAction SilentlyContinue | Select-Object KeyType, CryptoAlgorithm, KeyLength, ActiveKey | Format-Table -AutoSize | Out-String -Width 220 }"""),
    DN("dns_test_self", "DNS server answers itself",
       "Resolves each primary zone's SOA against this server -- a zone loaded but not "
       "answering shows here.",
       r"""Get-DnsServerZone | Where-Object { $_.ZoneType -eq 'Primary' -and -not $_.IsAutoCreated -and -not $_.IsReverseLookupZone } | ForEach-Object { try { $null = Resolve-DnsName $_.ZoneName -Type SOA -Server 127.0.0.1 -DnsOnly -ErrorAction Stop; "[OK]   $($_.ZoneName)" } catch { "[BAD]  $($_.ZoneName) -- $($_.Exception.Message)" } }"""),
    DN("dns_debug_logging", "DNS debug logging configuration",
       "Whether the DNS debug log is on, its file and size -- needed to see who queries what.",
       L("Get-DnsServerDiagnostics | Select-Object Queries, Answers, Notifications, Update, LogFilePath, MaxMBFileSize, EnableLoggingToFile")),
    DN("dns_events_summary", "DNS Server events (30 days)",
       "The DNS Server log grouped by event over 30 days.",
       EV + "EvSum @{LogName='DNS Server'} 30"),
    # ---- DHCP Server (10) --------------------------------------------------------------------
    DH("dhcp_scope_exhaustion", "Scopes close to exhaustion",
       "Scopes over 80% used, worst first.",
       r"""Get-DhcpServerv4Scope | ForEach-Object { $s = Get-DhcpServerv4ScopeStatistics -ScopeId $_.ScopeId; if ($s.PercentageInUse -ge 80) { '[WARN] {0,-16} {1,-30} {2:N1}% used, {3} free' -f $_.ScopeId, $_.Name, $s.PercentageInUse, $s.Free } }
'(nothing above = every scope under 80%)'"""),
    DH("dhcp_lease_durations", "Lease durations",
       "Lease length per scope -- very long leases exhaust guest/Wi-Fi scopes.",
       T("Get-DhcpServerv4Scope | Select-Object ScopeId, Name, LeaseDuration, State")),
    DH("dhcp_exclusions", "Exclusion ranges",
       "Excluded address ranges per scope.",
       T("Get-DhcpServerv4Scope | ForEach-Object { Get-DhcpServerv4ExclusionRange -ScopeId $_.ScopeId } | Select-Object ScopeId, StartRange, EndRange")),
    DH("dhcp_bad_addresses", "Declined (BAD_ADDRESS) leases",
       "Leases marked BAD_ADDRESS -- an IP conflict with a statically configured device.",
       r"""$b = Get-DhcpServerv4Scope | ForEach-Object { Get-DhcpServerv4Lease -ScopeId $_.ScopeId -BadLeases -ErrorAction SilentlyContinue }
if (-not $b) { '[OK]   No BAD_ADDRESS leases.'; return }
$b | ForEach-Object { "[WARN] $($_.IPAddress) in $($_.ScopeId)" }"""),
    DH("dhcp_server_settings", "DHCP server settings",
       "Conflict detection attempts, audit logging, database path and backup interval.",
       L("Get-DhcpServerSetting; Get-DhcpServerAuditLog; Get-DhcpServerDatabase")),
    DH("dhcp_dns_update", "DHCP dynamic DNS update settings",
       "Whether DHCP registers clients in DNS, the credentials it uses, and stale record "
       "cleanup on lease expiry.",
       L("Get-DhcpServerv4DnsSetting; Get-DhcpServerDnsCredential -ErrorAction SilentlyContinue")),
    DH("dhcp_policies", "DHCP policies",
       "Server and scope policies (vendor class / MAC based option assignment).",
       T("Get-DhcpServerv4Policy -ErrorAction SilentlyContinue | Select-Object Name, ScopeId, Enabled, ProcessingOrder, Condition")),
    DH("dhcp_v6", "DHCPv6 scopes",
       "IPv6 scopes and their state.",
       T("Get-DhcpServerv6Scope -ErrorAction SilentlyContinue | Select-Object Prefix, Name, State, PreferredLifetime")),
    DH("dhcp_statistics", "DHCP server statistics",
       "Discovers, offers, requests, acks and naks since the service started -- naks spike "
       "when clients move between subnets.",
       L("Get-DhcpServerv4Statistics")),
    DH("dhcp_audit_today", "Today's DHCP audit log",
       "Today's DHCP audit log summarised by event (new lease, renewal, NAK, conflict...).",
       r"""$p = (Get-DhcpServerAuditLog).Path; $f = Join-Path $p ("DhcpSrvLog-{0}.log" -f (Get-Date).DayOfWeek.ToString().Substring(0,3))
if (-not (Test-Path $f)) { "[INFO] No audit log at $f"; return }
$names = @{'10'='new lease';'11'='renewed';'12'='released';'13'='IP in use (conflict)';'14'='scope full';'15'='denied (NAK)';'16'='deleted';'17'='expired'}
Get-Content $f | Where-Object { $_ -match '^\d+,' } | ForEach-Object { ($_ -split ',')[0] } | Group-Object | Sort-Object Count -Descending | ForEach-Object { '{0,6}  {1,3} {2}' -f $_.Count, $_.Name, $names[$_.Name] }"""),
    # ---- File server (18) --------------------------------------------------------------------
    FS("fs_shares_detail", "Shares with path, users and caching",
       "Every share with its path, current users, folder enumeration mode and offline caching.",
       T("Get-SmbShare | Select-Object Name, Path, CurrentUsers, FolderEnumerationMode, CachingMode, EncryptData, Description"),
       admin=True),
    FS("fs_share_ntfs", "Share and NTFS permissions",
       "For each non-admin share, the share ACL and the top-level NTFS ACL -- effective access "
       "is the more restrictive of the two.",
       _HAS_SHARES + r"""Get-SmbShare | Where-Object { -not $_.Special -and $_.Path } | ForEach-Object { "== $($_.Name)  ($($_.Path))"
  Get-SmbShareAccess -Name $_.Name | ForEach-Object { "   share: $($_.AccountName) $($_.AccessControlType) $($_.AccessRight)" }
  (Get-Acl $_.Path -ErrorAction SilentlyContinue).Access | ForEach-Object { "   ntfs : $($_.IdentityReference) $($_.AccessControlType) $($_.FileSystemRights)$(if ($_.IsInherited) {' (inherited)'})" } }""",
       admin=True),
    FS("fs_everyone_access", "Shares open to Everyone / Authenticated Users",
       "Shares where Everyone, Authenticated Users or Domain Users have Change or Full.",
       r"""Get-SmbShare | Where-Object { -not $_.Special } | ForEach-Object { $n = $_.Name; Get-SmbShareAccess -Name $n | Where-Object { $_.AccountName -match 'Everyone|Authenticated Users|Domain Users' -and $_.AccessRight -in 'Full','Change' } | ForEach-Object { "[WARN] $n -- $($_.AccountName): $($_.AccessRight)" } }
'(nothing above = none)'"""),
    FS("fs_open_files_by_user", "Open files grouped by user",
       "How many files each remote user has open on this server.",
       r"""$f = Get-SmbOpenFile -ErrorAction SilentlyContinue
if (-not $f) { '[INFO] No files are open over the network.'; return }
$f | Group-Object ClientUserName | Sort-Object Count -Descending | ForEach-Object { '{0,6}  {1}' -f $_.Count, $_.Name }""",
       admin=True),
    FS("fs_sessions_dialect", "SMB sessions by dialect",
       "Connected sessions grouped by SMB dialect -- anything below 3.0 has no encryption.",
       r"""$s = Get-SmbSession -ErrorAction SilentlyContinue
if (-not $s) { '[INFO] No SMB sessions.'; return }
$s | Group-Object Dialect | ForEach-Object { '{0} dialect {1,-6} {2} session(s)' -f $(if ([double]$_.Name -lt 3) {'[WARN]'} else {'[OK]  '}), $_.Name, $_.Count }""",
       admin=True),
    FS("fs_smb1_access_audit", "SMB1 clients (audit)",
       "Whether SMB1 access auditing is on and which clients connected with SMB1 (event 3000) "
       "-- the list to fix before turning SMB1 off.",
       r""""AuditSmb1Access: $((Get-SmbServerConfiguration).AuditSmb1Access)"
""" + EV + "EvList @{LogName='Microsoft-Windows-SMBServer/Audit'; Id=3000} 30 60"),
    FS("fs_smb_server_events", "SMB server errors (7 days)",
       "Errors and warnings from the SMB server operational and security logs.",
       EV + r"""EvSum @{LogName='Microsoft-Windows-SMBServer/Operational'; Level=1,2,3} 7
EvSum @{LogName='Microsoft-Windows-SMBServer/Security'; Level=1,2,3} 7"""),
    FS("fs_shadow_schedule", "Shadow copies for shares (previous versions)",
       "Shadow copy count per volume -- what 'Previous versions' can restore.",
       need_admin("Win32_Volume and Win32_ShadowCopy return NOTHING for a standard user "
                "(measured 2026-10-16)") + r"""Get-CimInstance Win32_ShadowCopy -ErrorAction SilentlyContinue | Group-Object VolumeName | ForEach-Object { $v = Get-CimInstance Win32_Volume | Where-Object DeviceID -eq $_.Name; '{0,-4} {1,3} copies, newest {2}' -f $v.DriveLetter, $_.Count, ($_.Group | Sort-Object InstallDate -Descending | Select-Object -First 1).InstallDate }""",
       admin=True),
    FS("fs_access_based_enum", "Access-based enumeration",
       "Shares where users only see folders they can open (ABE) and where they see everything.",
       _HAS_SHARES + T("Get-SmbShare | Where-Object { -not $_.Special } | Select-Object Name, FolderEnumerationMode")),
    FS("fs_large_folders_shares", "Largest folders in each share",
       "The ten biggest top-level folders inside every non-admin share.",
       _HAS_SHARES + r"""Get-SmbShare | Where-Object { -not $_.Special -and $_.Path -and (Test-Path $_.Path) } | ForEach-Object { "== $($_.Name)"; Get-ChildItem $_.Path -Directory -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{ Folder=$_.Name; GB=[math]::Round(((Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum)/1GB,2) } } | Sort-Object GB -Descending | Select-Object -First 10 | Format-Table -AutoSize | Out-String -Width 220 }""",
       timeout=1800),
    FS("fs_old_files", "Files untouched for 3 years in shares",
       "Count and size of files not modified in three years, per share -- archive candidates.",
       _HAS_SHARES + r"""$cut = (Get-Date).AddYears(-3)
Get-SmbShare | Where-Object { -not $_.Special -and $_.Path -and (Test-Path $_.Path) } | ForEach-Object { $m = Get-ChildItem $_.Path -Recurse -File -ErrorAction SilentlyContinue | Where-Object LastWriteTime -lt $cut | Measure-Object Length -Sum; '{0,-25} {1,8} files  {2,8:N2} GB' -f $_.Name, $m.Count, ($m.Sum/1GB) }""",
       timeout=1800),
    FR("fsrm_quota_usage", "FSRM quotas near their limit",
       "Quotas over 85% used.",
       r"""Get-FsrmQuota | Where-Object { $_.Size -and $_.Usage / $_.Size -ge 0.85 } | ForEach-Object { '[WARN] {0}  {1:N0}% of {2:N1} GB' -f $_.Path, (100 * $_.Usage / $_.Size), ($_.Size/1GB) }
'(nothing above = all quotas under 85%)'"""),
    FR("fsrm_templates", "FSRM quota and screen templates",
       "Quota templates and file screen templates defined on the server.",
       r"""Get-FsrmQuotaTemplate | Select-Object Name, @{n='GB';e={[math]::Round($_.Size/1GB,1)}}, SoftLimit | Format-Table -AutoSize | Out-String -Width 220
Get-FsrmFileScreenTemplate | Select-Object Name, Active, @{n='Groups';e={$_.IncludeGroup -join ', '}} | Format-Table -AutoSize | Out-String -Width 220"""),
    FR("fsrm_storage_reports", "FSRM storage reports",
       "Configured storage report jobs and their schedules.",
       T("Get-FsrmStorageReport -ErrorAction SilentlyContinue | Select-Object Name, Namespace, ReportType, Schedule")),
    DF("dfsn_targets_state", "DFS namespace targets offline",
       "Namespace folder targets that are offline.",
       r"""Get-DfsnRoot -ErrorAction SilentlyContinue | ForEach-Object { Get-DfsnFolder -Path "$($_.Path)\*" -ErrorAction SilentlyContinue | ForEach-Object { Get-DfsnFolderTarget -Path $_.Path -ErrorAction SilentlyContinue | Where-Object State -ne 'Online' | ForEach-Object { "[WARN] $($_.Path) -> $($_.TargetPath) [$($_.State)]" } } }
'(nothing above = all online)'"""),
    DF("dfsn_root_servers", "DFS namespace servers",
       "Root targets (namespace servers) per namespace.",
       T("Get-DfsnRoot -ErrorAction SilentlyContinue | ForEach-Object { Get-DfsnRootTarget -Path $_.Path } | Select-Object Path, TargetPath, State, ReferralPriorityClass")),
    DF("dfsr_members", "DFS Replication members and folders",
       "Replicated folders, their members and content paths.",
       need_cmd("Get-DfsrMembership", "the DFSR module") + T("Get-DfsrMembership -ErrorAction SilentlyContinue | Select-Object GroupName, FolderName, ComputerName, ContentPath, ReadOnly, StagingPathQuotaInMB")),
    DF("dfsr_events", "DFS Replication errors (7 days)",
       "DFS Replication log grouped -- staging quota, conflicts, dirty shutdown (2213).",
       EV + "EvSum @{LogName='DFS Replication'; Level=1,2,3} 7"),
    # ---- Storage & free space (12) -----------------------------------------------------------
    SG("st_volumes_low", "Volumes below 15% free",
       "Every volume (including mount points) with less than 15% free space.",
       need_admin("Win32_Volume and Win32_ShadowCopy return NOTHING for a standard user "
                "(measured 2026-10-16)") + r"""Get-CimInstance Win32_Volume -Filter 'DriveType=3' | Where-Object Capacity | ForEach-Object { $pct = 100 * $_.FreeSpace / $_.Capacity; if ($pct -lt 15) { '[WARN] {0,-30} {1,6:N1}% free  ({2:N1} of {3:N1} GB)' -f $(if ($_.Name) {$_.Name} else {$_.DeviceID}), $pct, ($_.FreeSpace/1GB), ($_.Capacity/1GB) } }
'(nothing above = every volume has at least 15% free)'""",
       requires=""),
    SG("st_mount_points", "Mount points and volumes without letters",
       "Volumes mounted into folders or with no drive letter -- easy to miss in a free-space "
       "check.",
       need_admin("Win32_Volume and Win32_ShadowCopy return NOTHING for a standard user "
                "(measured 2026-10-16)") + T("Get-CimInstance Win32_Volume -Filter 'DriveType=3' | Where-Object { -not $_.DriveLetter } | Select-Object Name, Label, FileSystem, @{n='FreeGB';e={[math]::Round($_.FreeSpace/1GB,1)}}, @{n='SizeGB';e={[math]::Round($_.Capacity/1GB,1)}}"),
       requires=""),
    SG("st_storage_pools", "Storage Spaces pools and virtual disks",
       "Storage pools, their health and the virtual disks on them with resiliency.",
       r"""Get-StoragePool -IsPrimordial $false -ErrorAction SilentlyContinue | Select-Object FriendlyName, HealthStatus, OperationalStatus, @{n='SizeGB';e={[math]::Round($_.Size/1GB)}}, @{n='AllocatedGB';e={[math]::Round($_.AllocatedSize/1GB)}} | Format-Table -AutoSize | Out-String -Width 220
Get-VirtualDisk -ErrorAction SilentlyContinue | Select-Object FriendlyName, ResiliencySettingName, HealthStatus, OperationalStatus, @{n='SizeGB';e={[math]::Round($_.Size/1GB)}} | Format-Table -AutoSize | Out-String -Width 220
if (-not (Get-StoragePool -IsPrimordial $false -ErrorAction SilentlyContinue)) { '[INFO] No Storage Spaces pool.' }""",
       requires=""),
    SG("st_iscsi", "iSCSI sessions and targets",
       "iSCSI initiator sessions, their targets and connection state.",
       r"""$s = Get-IscsiSession -ErrorAction SilentlyContinue
if (-not $s) { '[INFO] No iSCSI sessions.'; return }
$s | Select-Object TargetNodeAddress, IsConnected, IsPersistent, NumberOfConnections | Format-Table -AutoSize | Out-String -Width 220""",
       requires=""),
    SG("st_mpio", "MPIO paths",
       "Multipath I/O disks and their path counts and policy.",
       need_cmd("mpclaim.exe", "MPIO (Multipath I/O feature)") + "mpclaim.exe -s -d"),
    SG("st_disk_latency", "Disk latency per volume (10 s)",
       "Average read/write latency per logical disk over ten seconds -- over 20 ms sustained "
       "is a storage problem.",
       r"""$c = Get-Counter '\LogicalDisk(*)\Avg. Disk sec/Read','\LogicalDisk(*)\Avg. Disk sec/Write','\LogicalDisk(*)\Current Disk Queue Length' -SampleInterval 1 -MaxSamples 10 -ErrorAction SilentlyContinue
$c.CounterSamples | Where-Object InstanceName -ne '_total' | Group-Object Path | ForEach-Object { $a = ($_.Group | Measure-Object CookedValue -Average).Average; $ms = $_.Name -match 'sec/'; '{0} {1,-60} {2,10:N2}{3}' -f $(if ($ms -and $a -gt 0.02) {'[WARN]'} else {'[OK]  '}), ($_.Name -replace '^\\\\[^\\]+',''), $(if ($ms) { $a * 1000 } else { $a }), $(if ($ms) {' ms'} else {''}) }""",
       requires="", timeout=60),
    SG("st_growth_folders", "Folders that grew most this week",
       "Top-level folders of each fixed drive with files written in the last 7 days, by the "
       "size of those new files -- where the disk is filling from.",
       r"""Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object { $d = $_.DeviceID + '\'; "== $d"
  Get-ChildItem $d -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object { $s = (Get-ChildItem $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object LastWriteTime -gt (Get-Date).AddDays(-7) | Measure-Object Length -Sum).Sum
    if ($s -gt 100MB) { '{0,9:N2} GB  {1}' -f ($s/1GB), $_.FullName } } }""",
       requires="", timeout=1800),
    SG("st_ntfs_cluster", "NTFS volume details",
       "Cluster size, MFT size and reserved zones per NTFS volume (fsutil fsinfo ntfsinfo).",
       r"""Get-Volume | Where-Object { $_.DriveLetter -and $_.FileSystem -eq 'NTFS' } | ForEach-Object { "== $($_.DriveLetter):"; fsutil fsinfo ntfsinfo "$($_.DriveLetter):" 2>&1 | Select-String 'Bytes Per Cluster|Mft Valid Data Length|Total Clusters|Free Clusters|NTFS Version' | ForEach-Object { '   ' + $_.Line.Trim() } }""",
       requires="", admin=True),
    DD("st_dedup_volumes", "Data Deduplication savings",
       "Deduplicated volumes with saved space and optimisation rate.",
       T("Get-DedupVolume | Select-Object Volume, Enabled, UsageType, @{n='SavedGB';e={[math]::Round($_.SavedSpace/1GB,1)}}, SavingsRate")),
    DD("st_dedup_jobs", "Data Deduplication jobs and status",
       "Last optimisation, garbage collection and scrubbing per volume.",
       L("Get-DedupStatus | Select-Object Volume, LastOptimizationTime, LastOptimizationResultMessage, LastGarbageCollectionTime, LastScrubbingTime, InPolicyFilesCount")),
    SG("st_volume_health", "Volume health and repair status",
       "Volumes reporting anything but Healthy, and the last chkdsk / spot-fix results from the "
       "log.",
       r"""Get-Volume | Where-Object { $_.DriveLetter -and $_.HealthStatus -ne 'Healthy' } | ForEach-Object { "[WARN] $($_.DriveLetter): $($_.HealthStatus) $($_.OperationalStatus)" }
""" + EV + "EvList @{LogName='Application'; ProviderName='Chkdsk','Wininit'} 90 20",
       requires=""),
    SG("st_free_space_report", "Free space report (all fixed disks)",
       "Size, used, free and percentage for every fixed logical disk -- the classic Wintel "
       "free-space report.",
       T("Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID, VolumeName, FileSystem, @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}}, @{n='UsedGB';e={[math]::Round(($_.Size-$_.FreeSpace)/1GB,1)}}, @{n='FreeGB';e={[math]::Round($_.FreeSpace/1GB,1)}}, @{n='Free%';e={[math]::Round(100*$_.FreeSpace/$_.Size,1)}}"),
       requires=""),
    # ---- IIS (10) ----------------------------------------------------------------------------
    IS("iis_apps", "Applications and virtual directories",
       "Every web application and virtual directory with its pool and physical path.",
       T("Get-WebApplication | Select-Object @{n='Site';e={$_.GetParentElement().Attributes['name'].Value}}, Path, applicationPool, PhysicalPath; Get-WebVirtualDirectory | Select-Object Path, PhysicalPath")),
    IS("iis_pool_recycling", "App pool recycling and limits",
       "Recycling interval, idle timeout, memory limits and rapid-fail protection per pool.",
       T("Get-ChildItem IIS:\\AppPools | Select-Object Name, @{n='Recycle';e={$_.recycling.periodicRestart.time}}, @{n='IdleTimeout';e={$_.processModel.idleTimeout}}, @{n='PrivateMemKB';e={$_.recycling.periodicRestart.privateMemory}}, @{n='RapidFail';e={$_.failure.rapidFailProtection}}")),
    IS("iis_auth", "Authentication per site",
       "Anonymous, Windows and Basic authentication state per site -- Basic over HTTP sends "
       "passwords in clear.",
       r"""Get-Website | ForEach-Object { $s = $_.Name; foreach ($a in 'anonymousAuthentication','windowsAuthentication','basicAuthentication') { $e = (Get-WebConfigurationProperty -Filter "/system.webServer/security/authentication/$a" -Name enabled -PSPath IIS:\ -Location $s -ErrorAction SilentlyContinue).Value; '{0,-30} {1,-26} {2}' -f $s, $a, $e } }"""),
    IS("iis_directory_browsing", "Directory browsing and detailed errors",
       "Sites with directory browsing on or detailed errors sent to remote clients -- both leak "
       "information.",
       r"""Get-Website | ForEach-Object { $s = $_.Name
  $db = (Get-WebConfigurationProperty -Filter /system.webServer/directoryBrowse -Name enabled -PSPath IIS:\ -Location $s).Value
  $er = (Get-WebConfigurationProperty -Filter /system.webServer/httpErrors -Name errorMode -PSPath IIS:\ -Location $s).Value
  '{0} {1,-30} directoryBrowse={2} errorMode={3}' -f $(if ($db -or $er -eq 'Detailed') {'[WARN]'} else {'[OK]  '}), $s, $db, $er }"""),
    IS("iis_request_filtering", "Request limits",
       "Maximum allowed content length and URL length per site.",
       r"""Get-Website | ForEach-Object { $s = $_.Name; $l = Get-WebConfigurationProperty -Filter /system.webServer/security/requestFiltering/requestLimits -Name * -PSPath IIS:\ -Location $s -ErrorAction SilentlyContinue; '{0,-30} maxAllowedContentLength={1} maxUrl={2} maxQueryString={3}' -f $s, $l.maxAllowedContentLength, $l.maxUrl, $l.maxQueryString }"""),
    IS("iis_log_settings", "IIS log settings",
       "Log format, directory, rollover and fields per site.",
       T("Get-Website | Select-Object Name, @{n='Dir';e={$_.logFile.directory}}, @{n='Format';e={$_.logFile.logFormat}}, @{n='Period';e={$_.logFile.period}}, @{n='Enabled';e={$_.logFile.enabled}}")),
    IS("iis_log_status_codes", "Status codes in today's logs",
       "Response status codes counted per site in the newest log -- 4xx/5xx spikes.",
       r"""Get-Website | ForEach-Object { $dir = [Environment]::ExpandEnvironmentVariables($_.logFile.directory) + "\W3SVC$($_.id)"; $f = Get-ChildItem $dir -Filter *.log -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $f) { return }; $fields = (Get-Content $f.FullName -TotalCount 10 | Where-Object { $_ -like '#Fields:*' } | Select-Object -Last 1) -replace '^#Fields: ','' -split ' '; $i = [array]::IndexOf($fields, 'sc-status')
  "== $($_.Name) ($($f.Name))"; Get-Content $f.FullName | Where-Object { $_ -notlike '#*' } | ForEach-Object { ($_ -split ' ')[$i] } | Group-Object | Sort-Object Count -Descending | ForEach-Object { '   {0} x{1}' -f $_.Name, $_.Count } }""",
       timeout=600),
    IS("iis_top_urls", "Busiest URLs and clients today",
       "Top requested URLs and top client IPs in the newest log of each site.",
       r"""Get-Website | ForEach-Object { $dir = [Environment]::ExpandEnvironmentVariables($_.logFile.directory) + "\W3SVC$($_.id)"; $f = Get-ChildItem $dir -Filter *.log -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $f) { return }; $fields = (Get-Content $f.FullName -TotalCount 10 | Where-Object { $_ -like '#Fields:*' } | Select-Object -Last 1) -replace '^#Fields: ','' -split ' '
  $u = [array]::IndexOf($fields, 'cs-uri-stem'); $c = [array]::IndexOf($fields, 'c-ip'); $rows = Get-Content $f.FullName | Where-Object { $_ -notlike '#*' } | ForEach-Object { , ($_ -split ' ') }
  "== $($_.Name) top URLs"; $rows | ForEach-Object { $_[$u] } | Group-Object | Sort-Object Count -Descending | Select-Object -First 10 | ForEach-Object { '   {0,7} {1}' -f $_.Count, $_.Name }
  "== $($_.Name) top clients"; $rows | ForEach-Object { $_[$c] } | Group-Object | Sort-Object Count -Descending | Select-Object -First 10 | ForEach-Object { '   {0,7} {1}' -f $_.Count, $_.Name } }""",
       timeout=600),
    IS("iis_modules", "Installed IIS modules",
       "Global native modules loaded by IIS -- third-party modules run in every request.",
       T("Get-WebGlobalModule | Select-Object Name, Image")),
    IS("iis_events", "IIS / W3SVC / WAS errors (7 days)",
       "Errors from W3SVC, WAS (app pool crashes and rapid-fail) and ASP.NET.",
       EV + "EvSum @{LogName='System','Application'; ProviderName='WAS','W3SVC','Microsoft-Windows-WAS','ASP.NET 4.0.30319.0','IIS-W3SVC-WP'; Level=1,2,3} 7"),
    # ---- Hyper-V (12) ------------------------------------------------------------------------
    HV("hv_vm_resources", "VM CPU and memory configuration",
       "vCPUs, startup/min/max memory and dynamic memory per VM.",
       T("Get-VM | Select-Object Name, ProcessorCount, @{n='StartupGB';e={[math]::Round($_.MemoryStartup/1GB,1)}}, DynamicMemoryEnabled, @{n='MinGB';e={[math]::Round($_.MemoryMinimum/1GB,1)}}, @{n='MaxGB';e={[math]::Round($_.MemoryMaximum/1GB,1)}}")),
    HV("hv_vm_network", "VM network adapters",
       "Each VM's network adapters, switch, IPs, MAC and VLAN.",
       T("Get-VM | Get-VMNetworkAdapter | Select-Object VMName, Name, SwitchName, MacAddress, @{n='IPs';e={$_.IPAddresses -join ', '}}, @{n='VLAN';e={(Get-VMNetworkAdapterVlan -VMNetworkAdapter $_).AccessVlanId}}")),
    HV("hv_vm_autostart", "VM automatic start and stop actions",
       "What each VM does when the host starts and shuts down -- 'save' needs disk space equal "
       "to its memory.",
       T("Get-VM | Select-Object Name, AutomaticStartAction, AutomaticStartDelay, AutomaticStopAction")),
    HV("hv_vm_versions", "VM configuration versions",
       "VM configuration version per VM -- old versions miss features after a host upgrade.",
       T("Get-VM | Select-Object Name, Version, Generation, State")),
    HV("hv_vm_secureboot", "VM Secure Boot and vTPM",
       "Secure Boot template and virtual TPM per generation-2 VM.",
       T("Get-VM | Where-Object Generation -eq 2 | ForEach-Object { $f = Get-VMFirmware $_; [pscustomobject]@{ VM=$_.Name; SecureBoot=$f.SecureBoot; Template=$f.SecureBootTemplate; vTPM=(Get-VMSecurity $_).TpmEnabled } }")),
    HV("hv_vhd_fragmentation", "Dynamic disks vs their maximum",
       "Dynamically expanding VHDX files: current size against maximum -- overcommitted "
       "storage fills the host volume.",
       r"""$t = 0; Get-VM | Get-VMHardDiskDrive | ForEach-Object { $v = Get-VHD $_.Path -ErrorAction SilentlyContinue; if ($v.VhdType -eq 'Dynamic') { $t += $v.Size; '{0,-25} {1,8:N1} of {2,6:N0} GB  {3}' -f $_.VMName, ($v.FileSize/1GB), ($v.Size/1GB), $_.Path } }
'Total promised to dynamic disks: {0:N0} GB' -f ($t/1GB)"""),
    HV("hv_host_numa", "Host NUMA and memory",
       "NUMA nodes and free memory per node -- a VM spanning nodes runs slower.",
       T("Get-VMHostNumaNode | Select-Object NodeId, @{n='Processors';e={$_.ProcessorsAvailability.Count}}, @{n='MemTotalGB';e={[math]::Round($_.MemoryTotal/1KB,1)}}, @{n='MemAvailGB';e={[math]::Round($_.MemoryAvailable/1KB,1)}}")),
    HV("hv_events", "Hyper-V errors (7 days)",
       "Hyper-V VMMS and worker errors grouped.",
       EV + "EvSum @{LogName='Microsoft-Windows-Hyper-V-VMMS-Admin','Microsoft-Windows-Hyper-V-Worker-Admin'; Level=1,2,3} 7"),
    HV("hv_vm_uptime", "VM uptime and heartbeat",
       "Uptime and heartbeat status per running VM.",
       T("Get-VM | Where-Object State -eq 'Running' | Select-Object Name, Uptime, Heartbeat, Status")),
    HV("hv_live_migration", "Live migration settings",
       "Whether live migration is enabled, the authentication protocol, simultaneous "
       "migrations and networks.",
       L("Get-VMHost | Select-Object VirtualMachineMigrationEnabled, VirtualMachineMigrationAuthenticationType, VirtualMachineMigrationPerformanceOption, MaximumVirtualMachineMigrations; Get-VMMigrationNetwork")),
    HV("hv_dvd_iso", "ISO images attached to VMs",
       "VMs with an ISO still mounted -- it blocks live migration if the path is local.",
       r"""$d = Get-VM | Get-VMDvdDrive | Where-Object Path
if (-not $d) { '[OK]   No ISO attached.'; return }
$d | ForEach-Object { "[INFO] $($_.VMName): $($_.Path)" }"""),
    HV("hv_vm_integration_version", "Integration services version",
       "Integration services state reported by each VM.",
       T("Get-VM | Select-Object Name, IntegrationServicesVersion, IntegrationServicesState")),
    # ---- Failover Cluster (8) ----------------------------------------------------------------
    CL("cl_resource_types_owner", "Cluster resources and owners",
       "Every resource with type, owner group and node.",
       T("Get-ClusterResource | Select-Object Name, ResourceType, OwnerGroup, OwnerNode, State")),
    CL("cl_events", "Cluster errors (7 days)",
       "Failover Clustering operational errors grouped.",
       EV + "EvSum @{LogName='Microsoft-Windows-FailoverClustering/Operational'; Level=1,2,3} 7"),
    CL("cl_preferred_owners", "Preferred owners per role",
       "Preferred owner nodes for each clustered role.",
       r"""Get-ClusterGroup | ForEach-Object { '{0,-40} {1}' -f $_.Name, ((Get-ClusterOwnerNode -Group $_.Name).OwnerNodes.Name -join ', ') }"""),
    CL("cl_csv_redirected", "CSV redirected I/O",
       "Cluster Shared Volumes in redirected access -- slow and a sign of a storage path "
       "problem.",
       T("Get-ClusterSharedVolumeState | Select-Object Name, Node, StateInfo, FileSystemRedirectedIOReason, BlockRedirectedIOReason")),
    CL("cl_parameters", "Cluster parameters",
       "Heartbeat thresholds, quarantine and dynamic quorum settings.",
       L("Get-Cluster | Select-Object Name, SameSubnetThreshold, CrossSubnetThreshold, SameSubnetDelay, QuarantineThreshold, DynamicQuorum, WitnessDynamicWeight")),
    CL("cl_node_uptime", "Cluster node uptime",
       "Each node's last boot.",
       r"""Get-ClusterNode | ForEach-Object { try { $o = Get-CimInstance Win32_OperatingSystem -ComputerName $_.Name -ErrorAction Stop; '{0,-20} up since {1}' -f $_.Name, $o.LastBootUpTime } catch { "[BAD]  $($_.Name) -- $($_.Exception.Message)" } }"""),
    CL("cl_validation_report", "Last cluster validation report",
       "The newest cluster validation report on disk and its date.",
       r"""$r = Get-ChildItem "$env:SystemRoot\Cluster\Reports" -Filter 'Validation Report*' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 3
if (-not $r) { '[WARN] No validation report on this node.'; return }
$r | ForEach-Object { "[INFO] $($_.LastWriteTime)  $($_.FullName)" }"""),
    CL("cl_disks", "Cluster disks",
       "Physical disk resources with owner and state.",
       T("Get-ClusterResource | Where-Object ResourceType -eq 'Physical Disk' | Select-Object Name, OwnerGroup, OwnerNode, State")),
    # ---- Remote Desktop Services (6) ---------------------------------------------------------
    RD("rds_servers", "RDS deployment servers",
       "Servers in the deployment and their roles.",
       T("Get-RDServer | Select-Object Server, @{n='Roles';e={$_.Roles -join ', '}}")),
    RD("rds_collections", "Session collections",
       "Session collections and their size.",
       T("Get-RDSessionCollection | Select-Object CollectionName, Size, ResourceType, CollectionDescription")),
    RD("rds_remoteapps", "Published RemoteApps",
       "RemoteApp programs per collection.",
       T("Get-RDSessionCollection | ForEach-Object { Get-RDRemoteApp -CollectionName $_.CollectionName } | Select-Object CollectionName, DisplayName, FilePath, ShowInWebAccess")),
    RD("rds_licensing", "RDS licensing configuration",
       "Licensing mode and license servers -- the 120-day grace period ending shows here.",
       L("Get-RDLicenseConfiguration")),
    RD("rds_sessions_idle", "Idle and disconnected sessions",
       "Disconnected sessions and sessions idle for over an hour.",
       T("Get-RDUserSession | Where-Object { $_.SessionState -eq 'STATE_DISCONNECTED' -or $_.IdleTime -gt 3600000 } | Select-Object UserName, HostServer, SessionState, @{n='IdleMin';e={[math]::Round($_.IdleTime/60000)}}")),
    RD("rds_events", "Remote Desktop errors (7 days)",
       "TerminalServices errors grouped.",
       EV + "EvSum @{LogName='Microsoft-Windows-TerminalServices-LocalSessionManager/Operational','Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational'; Level=1,2,3} 7"),
    # ---- WSUS (6) ----------------------------------------------------------------------------
    WS("wsus_computers_stale", "WSUS computers not reporting",
       "Computers that have not reported to WSUS in 30 days.",
       r"""$w = Get-WsusServer; $w.GetComputerTargets() | Where-Object { $_.LastReportedStatusTime -lt (Get-Date).AddDays(-30) } | Sort-Object LastReportedStatusTime | ForEach-Object { '[WARN] {0,-40} last report {1}' -f $_.FullDomainName, $_.LastReportedStatusTime }"""),
    WS("wsus_computers_failed", "WSUS computers with update failures",
       "Computers reporting failed installs.",
       r"""$w = Get-WsusServer; $s = New-Object Microsoft.UpdateServices.Administration.ComputerTargetScope; $s.IncludedInstallationStates = 'Failed'
$w.GetComputerTargets($s) | ForEach-Object { "[BAD]  $($_.FullDomainName)" }"""),
    WS("wsus_groups", "WSUS computer groups",
       "Computer groups and their member counts.",
       r"""(Get-WsusServer).GetComputerTargetGroups() | ForEach-Object { '{0,-30} {1,5} computers' -f $_.Name, $_.GetComputerTargets().Count }"""),
    WS("wsus_unapproved_critical", "Unapproved critical and security updates",
       "Critical and security updates still not approved.",
       r"""Get-WsusUpdate -Classification Critical -Approval Unapproved -Status Any -ErrorAction SilentlyContinue | Select-Object -First 50 | ForEach-Object { "[WARN] $($_.Update.Title)" }
Get-WsusUpdate -Classification Security -Approval Unapproved -Status Any -ErrorAction SilentlyContinue | Select-Object -First 50 | ForEach-Object { "[WARN] $($_.Update.Title)" }""",
       timeout=600),
    WS("wsus_content_size", "WSUS content and database",
       "The WSUS content folder size and database location.",
       r"""$c = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Update Services\Server\Setup').ContentDir
'Content: {0}  {1:N1} GB' -f $c, ((Get-ChildItem $c -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum/1GB)
'Database: ' + (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Update Services\Server\Setup').SqlServerName""",
       timeout=900),
    WS("wsus_products", "WSUS products and classifications",
       "The products and classifications WSUS synchronises.",
       r"""$s = (Get-WsusServer).GetSubscription(); 'Classifications:'; $s.GetUpdateClassifications() | ForEach-Object { "   $($_.Title)" }; 'Products:'; $s.GetUpdateCategories() | ForEach-Object { "   $($_.Title)" }"""),
    # ---- Certificate Services (6) ------------------------------------------------------------
    CA("ca_config", "Certificate authority configuration",
       "CA name, type, validity and CRL settings.",
       _IS_CA + "certutil.exe -cainfo"),
    CA("ca_expiring", "Certificates issued that expire within 30 days",
       "Issued certificates (by this CA) expiring in the next 30 days.",
       _IS_CA + r"""$d = (Get-Date).AddDays(30).ToString('MM/dd/yyyy')
certutil.exe -view -restrict "NotAfter<=$d,NotAfter>=now,Disposition=20" -out "RequestID,RequesterName,CommonName,NotAfter,CertificateTemplate" csv""",
       timeout=600),
    CA("ca_failed_requests", "Failed and pending requests",
       "Certificate requests that failed or wait for approval.",
       _IS_CA + "certutil.exe -view -restrict \"Disposition=30\" -out \"RequestID,RequesterName,StatusCode,Disposition\" csv\ncertutil.exe -view -restrict \"Disposition=9\" -out \"RequestID,RequesterName\" csv",
       timeout=600),
    CA("ca_crl", "CRL publication",
       "CRL distribution points and when the current CRL expires -- an expired CRL makes "
       "every certificate fail revocation checks.",
       _IS_CA + "certutil.exe -getreg CA\\CRLPublicationURLs\ncertutil.exe -CRL 2>&1 | Select-Object -First 5"),
    CA("ca_templates_risky", "Templates allowing requester-supplied names",
       "Published templates where the requester supplies the subject (ESC1 risk) -- domain "
       "admin via a certificate.",
       need_cmd("Get-ADObject", "the ActiveDirectory module") + r"""$cfg = (Get-ADRootDSE).configurationNamingContext
Get-ADObject -SearchBase "CN=Certificate Templates,CN=Public Key Services,CN=Services,$cfg" -Filter * -Properties msPKI-Certificate-Name-Flag, pKIExtendedKeyUsage | Where-Object { $_.'msPKI-Certificate-Name-Flag' -band 1 } | ForEach-Object { "[WARN] $($_.Name) -- ENROLLEE_SUPPLIES_SUBJECT; EKUs: $($_.pKIExtendedKeyUsage -join ', ')" }"""),
    CA("ca_service", "CA service and database",
       "Certificate Services service state and database size.",
       _IS_CA + r"""Get-Service CertSvc | Select-Object Status, StartType | Format-List | Out-String
$db = (certutil.exe -getreg CA\DBDirectory 2>&1 | Select-String 'DBDirectory' ) -replace '^.*= ',''
"Database folder: $db" """),
    # ---- Print server (5) --------------------------------------------------------------------
    PS("ps_printers_shared", "Shared printers",
       "Printers this server shares, with driver, port and published-in-AD flag.",
       r"""$sp = Get-Service Spooler -ErrorAction SilentlyContinue
if ($sp.Status -ne 'Running') { "[WARN] Print Spooler is $($sp.Status) -- printers cannot be read."; return }
Get-Printer | Where-Object Shared | Select-Object Name, ShareName, DriverName, PortName, Published | Format-Table -AutoSize | Out-String -Width 220"""),
    PS("ps_queue_errors", "Print queues in error",
       "Printers in an error, offline or paper-jam state.",
       r"""$sp = Get-Service Spooler -ErrorAction SilentlyContinue
if ($sp.Status -ne 'Running') { "[WARN] Print Spooler is $($sp.Status) -- printers cannot be read."; return }
Get-Printer | Where-Object { $_.PrinterStatus -ne 'Normal' } | ForEach-Object { "[WARN] $($_.Name) -- $($_.PrinterStatus)" }
'(nothing above = every queue is normal)'"""),
    PS("ps_drivers_versions", "Print driver versions and isolation",
       "Drivers with version, type 3/4 and driver isolation mode.",
       r"""$sp = Get-Service Spooler -ErrorAction SilentlyContinue
if ($sp.Status -ne 'Running') { "[WARN] Print Spooler is $($sp.Status) -- drivers cannot be read."; return }
Get-PrinterDriver | Select-Object Name, MajorVersion, @{n='Version';e={$_.DriverVersion}}, PrinterEnvironment, IsPackageAware | Format-Table -AutoSize | Out-String -Width 220"""),
    PS("ps_spooler_dc", "Print Spooler on domain controllers",
       "Whether the spooler runs on this server -- on a DC it should be disabled "
       "(PrintNightmare / printer bug coercion).",
       CHK + r"""$dc = (Get-CimInstance Win32_ComputerSystem).DomainRole -ge 4
$s = Get-Service Spooler
Chk $(if ($dc) { $s.Status -ne 'Running' } else { $null }) 'Spooler on a domain controller' $(if ($dc) { "$($s.Status), $($s.StartType)" } else { 'this is not a domain controller' })"""),
    PS("ps_events", "Print service errors (7 days)",
       "PrintService admin log grouped.",
       EV + "EvSum @{LogName='Microsoft-Windows-PrintService/Admin'; Level=1,2,3} 7"),
]

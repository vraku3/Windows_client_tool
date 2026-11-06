"""Windows Server checks: Active Directory, DNS, DHCP, IIS, Hyper-V, file
server, failover clustering, roles and features. Read-only.

Every script starts with `need_cmd(...)`: on a PC without the role or its
RSAT module it prints "NOT AVAILABLE: ... is not installed" instead of an
error. They were syntax-checked with PowerShell's own parser on a Windows 11
client; they have NOT run against a real server role here, and `requires`
says so on their card.
"""
from ._base import CHK, L, T, maker, need_cmd, need_admin

_AD = "the ActiveDirectory module (RSAT: Active Directory tools, or a domain controller)"
_GPM = "the GroupPolicy module (RSAT: Group Policy Management)"
_DNS = "the DnsServer module (DNS Server role or RSAT: DNS)"
_DHCP = "the DhcpServer module (DHCP Server role or RSAT: DHCP)"
_IIS = "the WebAdministration module (IIS)"
_HV = "the Hyper-V module (Hyper-V role or management tools)"
_FS = "the FileServerResourceManager module (FSRM)"
_CL = "the FailoverClusters module (Failover Clustering)"
_SM = "the ServerManager module (Windows Server)"

AD = maker("Active Directory", server=True, requires="ActiveDirectory module")
DN = maker("DNS Server", server=True, requires="DnsServer module")
DH = maker("DHCP Server", server=True, requires="DhcpServer module")
IS = maker("IIS", server=True, requires="IIS (WebAdministration)")
HV = maker("Hyper-V", server=True, requires="Hyper-V module")
FS = maker("File server", server=True, requires="SMB server / FSRM / DFS modules")
CL = maker("Failover Cluster", server=True, requires="FailoverClusters module")
RF = maker("Roles & features", server=True, requires="ServerManager module")

_ADN = need_cmd("Get-ADDomain", _AD)
SY = maker("Roles & features", server=True)

# Local configuration reads also apply to member servers without management roles.
_LOCAL_CHECKS = [
    SY("server-time-source-config", "Windows Time sources and policy",
       "Shows the active time source, peers and effective configuration to diagnose clock drift and policy overrides.",
       need_cmd("w32tm.exe", "Windows Time tools") + r"""foreach ($query in 'source','peers','configuration','status') {
  "== $query"; & w32tm.exe /query "/$query"
  if ($LASTEXITCODE -ne 0) { "ERROR: Windows Time query $query exited $LASTEXITCODE" }
}""", tags=('diagnostic', 'network', 'baseline')),
    SY("server-pending-file-renames", "Pending file rename pairs",
       "Lists queued source/destination operations, including deletes, to explain files still awaiting a restart.",
       CHK + r"""try {
  $p = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -ErrorAction Stop
  foreach ($name in 'PendingFileRenameOperations','PendingFileRenameOperations2') {
    $pairs = @(if ($null -ne $p.$name) { $p.$name })
    Chk ($pairs.Count -eq 0) $name "$($pairs.Count) entries; presence alone does not prove a restart is required"
    for ($i=0; $i -lt $pairs.Count; $i+=2) {
      $dest = if ($i+1 -ge $pairs.Count) { '<unpaired entry>' } elseif ($pairs[$i+1] -eq '') { '<delete>' } else { $pairs[$i+1] }
      "$($pairs[$i]) -> $dest"
    }
  }
} catch { "ERROR: Cannot read pending renames: $($_.Exception.Message)" }""",
       tags=('diagnostic', 'triage')),
    SY("server-crash-dump-settings", "Crash dump and restart configuration",
       "Shows dump type, destination and automatic restart settings so support can assess crash evidence collection.",
       r"""try {
  Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' -ErrorAction Stop |
    Select-Object CrashDumpEnabled, DumpFile, MinidumpDir, DedicatedDumpFile, DumpFileSize, AutoReboot, Overwrite, AlwaysKeepMemoryDump, LogEvent |
    Format-List | Out-String -Width 220
  '[INFO] CrashDumpEnabled: 0 none, 1 complete, 2 kernel, 3 small, 7 automatic, 10 active. Configuration does not prove a dump can be written.'
} catch { "ERROR: Cannot read crash configuration: $($_.Exception.Message)" }""",
       tags=('inventory', 'triage', 'baseline')),
    SY("server-recovery-environment-settings", "Windows recovery environment and boot recovery",
       "Reads WinRE availability and current boot recovery settings to prepare for an unbootable server.",
       need_admin("boot recovery configuration requires elevation") + need_cmd("reagentc.exe", "Windows Recovery Environment tools") + r"""reagentc.exe /info
if ($LASTEXITCODE -ne 0) { "ERROR: reagentc exited $LASTEXITCODE" }
if (Get-Command bcdedit.exe -ErrorAction SilentlyContinue) {
  bcdedit.exe /enum '{current}'
  if ($LASTEXITCODE -ne 0) { "ERROR: bcdedit exited $LASTEXITCODE" }
}""", admin=True, tags=('inventory', 'baseline', 'triage')),
    SY("server-pagefile-ram-comparison", "Page file allocation versus physical RAM",
       "Compares configured and current page files with RAM and peak usage to support capacity and crash-dump planning.",
       r"""try {
  $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
  "RAM GB: $([math]::Round($cs.TotalPhysicalMemory/1GB,2)); automatic page file: $($cs.AutomaticManagedPagefile)"
  '== Configured limits (MB; zero means system managed)'
  Get-CimInstance Win32_PageFileSetting -ErrorAction Stop | Select-Object Name, InitialSize, MaximumSize | Format-Table -AutoSize | Out-String -Width 220
  '== Current allocation and peak use (MB)'
  Get-CimInstance Win32_PageFileUsage -ErrorAction Stop | Select-Object Name, AllocatedBaseSize, CurrentUsage, PeakUsage, TempPageFile | Format-Table -AutoSize | Out-String -Width 220
  '[INFO] There is no universal RAM multiplier; size for commit demand and the chosen dump type.'
} catch { "ERROR: Cannot read page file configuration: $($_.Exception.Message)" }""",
       tags=('inventory', 'performance', 'storage')),
    SY("server-smb-everyone-acl", "SMB Everyone access including read-only grants",
       "Finds Everyone allow entries by SID, including Read grants, without relying on localized group names.",
       need_cmd("Get-SmbShareAccess", "SMB server cmdlets") + need_admin("share permissions require elevation") + CHK + r"""try {
  $everyone = ([Security.Principal.SecurityIdentifier]'S-1-1-0').Translate([Security.Principal.NTAccount]).Value
  foreach ($share in Get-SmbShare -ErrorAction Stop) {
    try {
      $acl = @(Get-SmbShareAccess -Name $share.Name -ScopeName $share.ScopeName -ErrorAction Stop)
      $grants = @($acl | Where-Object { $_.AccountName -eq $everyone -and $_.AccessControlType -eq 'Allow' })
      Chk ($grants.Count -eq 0) $share.Name "Everyone allow rights: $(($grants.AccessRight) -join ', '); NTFS and deny entries still restrict effective access"
    } catch { "ERROR: Share $($share.Name): $($_.Exception.Message)" }
  }
} catch { "ERROR: Cannot enumerate shares: $($_.Exception.Message)" }""",
       requires="SMB server cmdlets", admin=True, tags=('diagnostic', 'security', 'compliance')),
]

SCRIPTS = _LOCAL_CHECKS + [
    # ---- Active Directory --------------------------------------------------------------------
    AD("ad_domain", "Domain and forest overview",
       "Domain and forest names, functional levels, NetBIOS name, PDC emulator and the "
       "number of domain controllers.",
       _ADN + L(r"""$d = Get-ADDomain; $f = Get-ADForest
[pscustomobject]@{ Domain=$d.DNSRoot; NetBIOS=$d.NetBIOSName; DomainMode=$d.DomainMode; Forest=$f.Name; ForestMode=$f.ForestMode
  PDC=$d.PDCEmulator; DCs=@(Get-ADDomainController -Filter *).Count; Sites=($f.Sites -join ', ') }"""), tags=('inventory', 'baseline')),
    AD("ad_fsmo", "FSMO role holders",
       "Which domain controller holds each of the five operations master roles.",
       _ADN + L(r"""$d = Get-ADDomain; $f = Get-ADForest
[pscustomobject]@{ SchemaMaster=$f.SchemaMaster; DomainNamingMaster=$f.DomainNamingMaster; PDCEmulator=$d.PDCEmulator; RIDMaster=$d.RIDMaster; InfrastructureMaster=$d.InfrastructureMaster }"""), tags=('inventory', 'baseline')),
    AD("ad_dcs", "Domain controllers",
       "Every domain controller with its site, OS, IP, global catalog and read-only flags.",
       _ADN + T("Get-ADDomainController -Filter * | Select-Object Name, Site, IPv4Address, OperatingSystem, IsGlobalCatalog, IsReadOnly"), tags=('inventory', 'baseline')),
    AD("ad_replication_failures", "Replication failures",
       "Replication failures reported for every domain controller, with the first failure "
       "time and count.",
       _ADN + r"""try { $f = @(Get-ADReplicationFailure -Target (Get-ADDomain -ErrorAction Stop).DNSRoot -Scope Domain -ErrorAction Stop) }
catch { "ERROR: $($_.Exception.Message)"; return }
if (-not $f.Count) { '[OK]   No replication failures reported.'; return }
$f | ForEach-Object { "[BAD]  $($_.Server) <- $($_.Partner)  failures=$($_.FailureCount) since $($_.FirstFailureTime)  last error $($_.LastError)" }""", tags=('diagnostic', 'health')),
    AD("ad_repl_summary", "Replication summary (repadmin)",
       "repadmin /replsummary: largest delta and failures per source and destination DC.",
       need_cmd("repadmin.exe", "repadmin (RSAT: Active Directory tools)") + "repadmin.exe /replsummary",
       timeout=300, tags=('diagnostic', 'triage')),
    AD("ad_dcdiag", "Domain controller health (dcdiag)",
       "dcdiag's standard tests against this DC, quiet mode (only failures printed).",
       need_cmd("dcdiag.exe", "dcdiag (a domain controller or RSAT)") + "dcdiag.exe /q", timeout=600, tags=('diagnostic', 'triage')),
    AD("ad_stale_computers", "Computers not seen for 90 days",
       "Enabled computer accounts whose last logon timestamp is older than 90 days -- stale "
       "objects to disable.",
       _ADN + T("Get-ADComputer -Filter 'Enabled -eq $true' -Properties LastLogonDate, OperatingSystem | Where-Object { $_.LastLogonDate -lt (Get-Date).AddDays(-90) } | Sort-Object LastLogonDate | Select-Object Name, OperatingSystem, LastLogonDate"), tags=('diagnostic',)),
    AD("ad_stale_users", "Users who have not signed in for 90 days",
       "Enabled user accounts with no logon in 90 days (lastLogonTimestamp replicates with up "
       "to 14 days of lag).",
       _ADN + T("Get-ADUser -Filter 'Enabled -eq $true' -Properties LastLogonDate | Where-Object { $_.LastLogonDate -lt (Get-Date).AddDays(-90) } | Sort-Object LastLogonDate | Select-Object SamAccountName, Name, LastLogonDate"), tags=('diagnostic', 'security')),
    AD("ad_locked_out", "Locked-out accounts",
       "Accounts currently locked out, with their last bad password time.",
       _ADN + r"""$l = Search-ADAccount -LockedOut -UsersOnly
if (-not $l) { '[OK]   No locked-out accounts.'; return }
$l | Get-ADUser -Properties LastBadPasswordAttempt, BadLogonCount | ForEach-Object { "[WARN] $($_.SamAccountName)  bad logons $($_.BadLogonCount), last $($_.LastBadPasswordAttempt)" }""", tags=('diagnostic', 'health', 'triage')),
    AD("ad_pwd_never_expires", "Passwords that never expire",
       "Enabled users with PasswordNeverExpires -- service accounts mostly, each one a risk to "
       "review.",
       _ADN + T("Get-ADUser -Filter 'Enabled -eq $true -and PasswordNeverExpires -eq $true' -Properties PasswordLastSet | Sort-Object PasswordLastSet | Select-Object SamAccountName, Name, PasswordLastSet"), tags=('inventory', 'security')),
    AD("ad_privileged_groups", "Privileged group membership",
       "Members (recursive) of Domain Admins, Enterprise Admins, Schema Admins, "
       "Administrators, Account/Server/Backup/Print Operators.",
       _ADN + r"""foreach ($g in 'Domain Admins','Enterprise Admins','Schema Admins','Administrators','Account Operators','Server Operators','Backup Operators','Print Operators','DnsAdmins','Group Policy Creator Owners') {
  try { $m = @(Get-ADGroupMember $g -Recursive -ErrorAction Stop) }
  catch { "== $g (not found or access denied: $($_.Exception.Message))"; continue }
  "== $g ($($m.Count))"; $m | ForEach-Object { "   $($_.SamAccountName) [$($_.objectClass)]" } }""", tags=('inventory', 'security')),
    AD("ad_krbtgt", "krbtgt password age",
       "When the krbtgt account's password was last changed. Older than 180 days, or never "
       "since the domain was built, is a Golden Ticket risk.",
       _ADN + CHK + r"""$k = Get-ADUser krbtgt -Properties PasswordLastSet
$age = ((Get-Date) - $k.PasswordLastSet).Days
Chk ($age -le 180) 'krbtgt password age' "$age days (last set $($k.PasswordLastSet))" """, tags=('diagnostic', 'health', 'security')),
    AD("ad_password_policy", "Domain password and lockout policy",
       "The default domain password policy and every fine-grained password policy.",
       _ADN + r"""Get-ADDefaultDomainPasswordPolicy | Format-List | Out-String -Width 220
Get-ADFineGrainedPasswordPolicy -Filter * -ErrorAction Stop | Select-Object Name, Precedence, MinPasswordLength, LockoutThreshold, AppliesTo | Format-Table -AutoSize | Out-String -Width 220""", tags=('inventory', 'security')),
    AD("ad_sites_subnets", "Sites and subnets",
       "AD sites, their subnets and site links -- clients in an unmapped subnet authenticate "
       "against a random DC.",
       _ADN + r"""Get-ADReplicationSite -Filter * | Select-Object Name | Format-Table -AutoSize | Out-String -Width 220
Get-ADReplicationSubnet -Filter * | Select-Object Name, Site | Format-Table -AutoSize | Out-String -Width 220
Get-ADReplicationSiteLink -Filter * | Select-Object Name, Cost, ReplicationFrequencyInMinutes, @{n='Sites';e={$_.SitesIncluded -join ', '}} | Format-Table -AutoSize | Out-String -Width 220""", tags=('inventory', 'network')),
    AD("ad_recycle_bin", "AD Recycle Bin",
       "Whether the Active Directory Recycle Bin optional feature is enabled -- without it a "
       "deleted user comes back without its groups.",
       _ADN + CHK + r"""$r = Get-ADOptionalFeature 'Recycle Bin Feature'
Chk ([bool]$r.EnabledScopes) 'AD Recycle Bin' $(if ($r.EnabledScopes) { 'enabled' } else { 'NOT enabled' })""", tags=('diagnostic', 'health')),
    AD("ad_admin_count_orphans", "AdminCount orphans",
       "Users with adminCount=1 who are no longer in a protected group -- they keep blocked "
       "inheritance and stay hard to manage.",
       _ADN + r"""$prot = @('Domain Admins','Enterprise Admins','Schema Admins','Administrators','Account Operators','Server Operators','Backup Operators','Print Operators') | ForEach-Object { try { Get-ADGroupMember $_ -Recursive -ErrorAction Stop } catch { } } | ForEach-Object SamAccountName
Get-ADUser -Filter 'adminCount -eq 1' | Where-Object { $_.SamAccountName -notin $prot -and $_.SamAccountName -ne 'krbtgt' } | ForEach-Object { "[WARN] $($_.SamAccountName)" }""", tags=('diagnostic', 'health', 'security')),
    AD("ad_dc_srv_records", "Domain controller DNS records",
       "Resolves the _ldap and _kerberos SRV records clients use to find a DC -- if these fail "
       "nothing can join or sign in. Runs on any domain-joined PC.",
       r"""$dom = (Get-CimInstance Win32_ComputerSystem).Domain
if (-not (Get-CimInstance Win32_ComputerSystem).PartOfDomain) { 'NOT AVAILABLE: this PC is not joined to a domain.'; return }
foreach ($r in "_ldap._tcp.dc._msdcs.$dom", "_kerberos._tcp.$dom", "_gc._tcp.$dom") { try { Resolve-DnsName $r -Type SRV -ErrorAction Stop | Where-Object Type -eq 'SRV' | ForEach-Object { "[OK]   $r -> $($_.NameTarget):$($_.Port)" } } catch { "[BAD]  $r -- $($_.Exception.Message)" } }""",
       requires="a domain-joined PC", tags=('diagnostic', 'health', 'network')),
    AD("ad_secure_channel", "Secure channel to the domain",
       "Whether this computer's trust with the domain is intact (Test-ComputerSecureChannel) "
       "and which DC it uses -- 'the trust relationship failed'.",
       r"""if (-not (Get-CimInstance Win32_ComputerSystem).PartOfDomain) { 'NOT AVAILABLE: this PC is not joined to a domain.'; return }
try { $ok = Test-ComputerSecureChannel -ErrorAction Stop; if ($ok) { '[OK]   Secure channel is intact.' } else { '[BAD]  Secure channel is BROKEN.' } } catch { "ERROR: $($_.Exception.Message)" }
nltest /sc_query:$((Get-CimInstance Win32_ComputerSystem).Domain)""",
       requires="a domain-joined PC", tags=('diagnostic', 'health', 'security')),
    AD("gpo_list", "Group Policy Objects",
       "Every GPO with its status, creation and modification time.",
       need_cmd("Get-GPO", _GPM) + T("Get-GPO -All | Sort-Object DisplayName | Select-Object DisplayName, GpoStatus, CreationTime, ModificationTime"),
       requires="GroupPolicy module", tags=('inventory', 'baseline')),
    AD("gpo_unlinked", "Unlinked GPOs",
       "GPOs not linked anywhere -- dead weight, or a policy someone forgot to link.",
       need_cmd("Get-GPO", _GPM) + r"""Get-GPO -All | ForEach-Object { [xml]$x = Get-GPOReport -Guid $_.Id -ReportType Xml; if (-not $x.GPO.LinksTo) { "[WARN] $($_.DisplayName)  (modified $($_.ModificationTime))" } }""",
       requires="GroupPolicy module", timeout=600, tags=('diagnostic', 'health')),
    # ---- DNS Server --------------------------------------------------------------------------
    DN("dns_zones", "DNS zones",
       "Every zone with type, AD-integration, dynamic update setting and record count.",
       need_cmd("Get-DnsServerZone", _DNS) + T("Get-DnsServerZone | Select-Object ZoneName, ZoneType, IsDsIntegrated, DynamicUpdate, IsReverseLookupZone, IsAutoCreated"), tags=('inventory', 'network')),
    DN("dns_forwarders", "DNS forwarders and recursion",
       "Forwarders, conditional forwarders, timeout and whether recursion is enabled.",
       need_cmd("Get-DnsServerForwarder", _DNS) + r"""Get-DnsServerForwarder | Format-List | Out-String -Width 220
Get-DnsServerZone | Where-Object ZoneType -eq 'Forwarder' | Select-Object ZoneName, MasterServers | Format-Table -AutoSize | Out-String -Width 220
"Recursion enabled: $((Get-DnsServerRecursion).Enable)" """, tags=('inventory', 'network')),
    DN("dns_scavenging", "DNS scavenging",
       "Server scavenging state and interval, and aging per zone -- stale records pile up "
       "without it.",
       need_cmd("Get-DnsServerScavenging", _DNS) + r"""Get-DnsServerScavenging | Format-List | Out-String -Width 220
Get-DnsServerZone | Where-Object { -not $_.IsAutoCreated -and $_.ZoneType -eq 'Primary' } | ForEach-Object { $a = Get-DnsServerZoneAging $_.ZoneName -ErrorAction Stop; '{0,-40} aging {1}' -f $_.ZoneName, $a.AgingEnabled }""", tags=('inventory', 'network')),
    DN("dns_statistics", "DNS server statistics",
       "Query and response totals, recursion and failure counters since the service started.",
       need_cmd("Get-DnsServerStatistics", _DNS) + L("(Get-DnsServerStatistics).Query2Statistics"), tags=('inventory', 'performance', 'network')),
    DN("dns_root_hints", "Root hints",
       "The root hints the server falls back to when no forwarder answers.",
       need_cmd("Get-DnsServerRootHint", _DNS) + T("Get-DnsServerRootHint | Select-Object @{n='NameServer';e={$_.NameServer.RecordData.NameServer}}, @{n='IP';e={($_.IPAddress.RecordData.IPv4Address) -join ', '}}"), tags=('inventory', 'network')),
    DN("dns_server_errors", "DNS Server errors (7 days)",
       "Errors and warnings from the DNS Server log.",
       need_cmd("Get-DnsServerZone", _DNS) + r"""try { $e = @(Get-WinEvent -FilterHashtable @{LogName='DNS Server'; Level=1,2,3; StartTime=(Get-Date).AddDays(-7)} -ErrorAction Stop) }
catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { '[OK]   No DNS Server errors in 7 days.' }
  else { "ERROR: $($_.Exception.Message)" }
  return
}
$e | Group-Object Id | Sort-Object Count -Descending | ForEach-Object { '[WARN] x{0,-5} event {1}: {2}' -f $_.Count, $_.Name, (($_.Group[0].Message -split "`r?`n")[0]) }""", tags=('diagnostic', 'health', 'network')),
    # ---- DHCP Server -------------------------------------------------------------------------
    DH("dhcp_scopes", "DHCP scopes and utilisation",
       "Every IPv4 scope with its range, state, and how full it is -- a scope at 95% hands out "
       "no more addresses soon.",
       need_cmd("Get-DhcpServerv4Scope", _DHCP) + r"""Get-DhcpServerv4Scope | ForEach-Object { $s = Get-DhcpServerv4ScopeStatistics -ScopeId $_.ScopeId
  '{0} {1,-16} {2,-30} {3}-{4}  {5,5:N1}% used ({6} free)  {7}' -f $(if ($s.PercentageInUse -ge 90) {'[WARN]'} else {'[OK]  '}), $_.ScopeId, $_.Name, $_.StartRange, $_.EndRange, $s.PercentageInUse, $s.Free, $_.State }""", tags=('diagnostic', 'health', 'network')),
    DH("dhcp_options", "DHCP server and scope options",
       "Router, DNS servers, domain name and other options set server-wide and per scope.",
       need_cmd("Get-DhcpServerv4OptionValue", _DHCP) + r"""'== Server options'; Get-DhcpServerv4OptionValue | Select-Object OptionId, Name, @{n='Value';e={$_.Value -join ', '}} | Format-Table -AutoSize | Out-String -Width 220
Get-DhcpServerv4Scope | ForEach-Object { "== Scope $($_.ScopeId)"; Get-DhcpServerv4OptionValue -ScopeId $_.ScopeId | Select-Object OptionId, Name, @{n='Value';e={$_.Value -join ', '}} | Format-Table -AutoSize | Out-String -Width 220 }""", tags=('inventory', 'network')),
    DH("dhcp_reservations", "DHCP reservations",
       "Every reservation per scope: IP, MAC and name.",
       need_cmd("Get-DhcpServerv4Reservation", _DHCP) + T("Get-DhcpServerv4Scope | ForEach-Object { Get-DhcpServerv4Reservation -ScopeId $_.ScopeId } | Select-Object ScopeId, IPAddress, ClientId, Name, Description"), tags=('inventory', 'network')),
    DH("dhcp_failover", "DHCP failover",
       "Failover relationships, their mode, partner and state.",
       need_cmd("Get-DhcpServerv4Failover", _DHCP) + r"""try { $f = @(Get-DhcpServerv4Failover -ErrorAction Stop) }
catch { "ERROR: $($_.Exception.Message)"; return }
if (-not $f.Count) { '[INFO] No DHCP failover relationship is configured.'; return }
$f | Select-Object Name, PartnerServer, Mode, State, @{n='Scopes';e={$_.ScopeId -join ', '}} | Format-Table -AutoSize | Out-String -Width 220""", tags=('inventory', 'network')),
    DH("dhcp_authorized", "DHCP servers authorised in AD",
       "DHCP servers authorised in Active Directory -- an unauthorised one on a domain "
       "network will not hand out addresses.",
       need_cmd("Get-DhcpServerInDC", _DHCP) + T("Get-DhcpServerInDC | Select-Object DnsName, IPAddress"), tags=('inventory', 'network')),
    DH("dhcp_leases_recent", "Most recent DHCP leases",
       "The 50 newest leases across scopes with host name and MAC.",
       need_cmd("Get-DhcpServerv4Lease", _DHCP) + T("Get-DhcpServerv4Scope | ForEach-Object { Get-DhcpServerv4Lease -ScopeId $_.ScopeId } | Sort-Object LeaseExpiryTime -Descending | Select-Object -First 50 IPAddress, ClientId, HostName, AddressState, LeaseExpiryTime"), tags=('inventory', 'network')),
    # ---- IIS ---------------------------------------------------------------------------------
    IS("iis_sites", "IIS sites and bindings",
       "Every site with its state, physical path and bindings (protocol, IP, port, host "
       "header).",
       need_cmd("Get-Website", _IIS) + T("Get-Website | Select-Object Name, State, PhysicalPath, @{n='Bindings';e={($_.Bindings.Collection | ForEach-Object { \"$($_.protocol) $($_.bindingInformation)\" }) -join '; '}}"), tags=('inventory',)),
    IS("iis_app_pools", "Application pools",
       "Every app pool with state, .NET version, pipeline mode and identity.",
       need_cmd("Get-Website", _IIS) + T("Get-ChildItem IIS:\\AppPools | Select-Object Name, State, managedRuntimeVersion, managedPipelineMode, @{n='Identity';e={$_.processModel.identityType}}"), tags=('inventory',)),
    IS("iis_certs", "HTTPS bindings and their certificates",
       "Each HTTPS binding with the certificate's subject and expiry -- the expiring "
       "certificate before it takes the site down.",
       need_cmd("Get-Website", _IIS) + r"""Get-ChildItem IIS:\SslBindings -ErrorAction Stop | ForEach-Object { $c = Get-ChildItem "Cert:\LocalMachine\$($_.Store)\$($_.Thumbprint)" -ErrorAction Stop
  $days = if ($c) { ($c.NotAfter - (Get-Date)).Days } else { $null }
  '{0} {1}:{2}  {3}  expires {4} ({5} days)' -f $(if ($days -lt 30) {'[WARN]'} else {'[OK]  '}), $_.IPAddress, $_.Port, $c.Subject, $c.NotAfter, $days }""", tags=('diagnostic', 'health', 'security')),
    IS("iis_errors", "IIS 500 errors in today's logs",
       "HTTP 5xx responses in today's W3C logs per site -- the server-side errors.",
       need_cmd("Get-Website", _IIS) + r"""Get-Website | ForEach-Object { $dir = [Environment]::ExpandEnvironmentVariables($_.logFile.directory) + "\W3SVC$($_.id)"
  $f = Get-ChildItem $dir -Filter *.log -ErrorAction Stop | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($f) { $n = (Select-String -Path $f.FullName -Pattern ' 5\d\d \d+ \d+ \d+$' -ErrorAction Stop).Count; '{0} {1,-30} {2} errors in {3}' -f $(if ($n) {'[WARN]'} else {'[OK]  '}), $_.Name, $n, $f.Name } }""", tags=('diagnostic', 'health')),
    IS("iis_worker_processes", "IIS worker processes",
       "Running w3wp.exe processes with their app pool and memory.",
       need_cmd("Get-Website", _IIS) + r"""Get-CimInstance Win32_Process -Filter "Name='w3wp.exe'" | ForEach-Object { '{0,6}  {1,8:N0} MB  {2}' -f $_.ProcessId, ($_.WorkingSetSize/1MB), (($_.CommandLine -replace '^.*-ap "([^"]+)".*$','$1')) }""", tags=('inventory', 'performance')),
    # ---- Hyper-V -----------------------------------------------------------------------------
    HV("hv_vms", "Virtual machines",
       "Every VM with state, CPU, memory, uptime, generation and version.",
       need_cmd("Get-VM", _HV) + T("Get-VM | Select-Object Name, State, CPUUsage, @{n='MemGB';e={[math]::Round($_.MemoryAssigned/1GB,1)}}, Uptime, Generation, Version"), tags=('inventory',)),
    HV("hv_checkpoints", "Checkpoints (snapshots)",
       "Every checkpoint with its age -- old checkpoints grow AVHDX files and slow the VM.",
       need_cmd("Get-VMSnapshot", _HV) + r"""$s = Get-VM | Get-VMSnapshot
if (-not $s) { '[OK]   No checkpoints.'; return }
$s | ForEach-Object { $age = ((Get-Date) - $_.CreationTime).Days; '{0} {1,-25} {2,-35} {3} days old' -f $(if ($age -gt 7) {'[WARN]'} else {'[INFO]'}), $_.VMName, $_.Name, $age }""", tags=('diagnostic', 'health')),
    HV("hv_switches", "Virtual switches",
       "Virtual switches, their type and the physical adapter they use.",
       need_cmd("Get-VMSwitch", _HV) + T("Get-VMSwitch | Select-Object Name, SwitchType, NetAdapterInterfaceDescription, AllowManagementOS"), tags=('inventory', 'network')),
    HV("hv_integration", "Integration services",
       "Integration services per VM that are enabled but not OK -- time sync, heartbeat, "
       "backup.",
       need_cmd("Get-VMIntegrationService", _HV) + r"""Get-VM | Get-VMIntegrationService | Where-Object Enabled | ForEach-Object { '{0} {1,-25} {2,-28} {3}' -f $(if ($_.PrimaryStatusDescription -eq 'OK') {'[OK]  '} else {'[WARN]'}), $_.VMName, $_.Name, $_.PrimaryStatusDescription }""", tags=('diagnostic', 'health')),
    HV("hv_disks", "VM disks",
       "Every virtual disk with its path, type and current size against maximum.",
       need_cmd("Get-VMHardDiskDrive", _HV) + T("Get-VM | Get-VMHardDiskDrive | ForEach-Object { $v = Get-VHD $_.Path -ErrorAction Stop; [pscustomobject]@{ VM=$_.VMName; Path=$_.Path; Type=$v.VhdType; SizeGB=[math]::Round($v.FileSize/1GB,1); MaxGB=[math]::Round($v.Size/1GB) } }"), tags=('inventory', 'storage')),
    HV("hv_host", "Hyper-V host resources",
       "Host logical processors, memory, default VM and disk paths, NUMA spanning and live "
       "migration settings.",
       need_cmd("Get-VMHost", _HV) + L("Get-VMHost | Select-Object ComputerName, LogicalProcessorCount, @{n='MemGB';e={[math]::Round($_.MemoryCapacity/1GB)}}, VirtualMachinePath, VirtualHardDiskPath, NumaSpanningEnabled, VirtualMachineMigrationEnabled"), tags=('inventory', 'performance')),
    HV("hv_replication", "Hyper-V replication health",
       "Replication state and health per replicated VM.",
       need_cmd("Get-VMReplication", _HV) + r"""try { $r = @(Get-VMReplication -ErrorAction Stop) }
catch { "ERROR: $($_.Exception.Message)"; return }
if (-not $r.Count) { '[INFO] No VM is replicated.'; return }
$r | ForEach-Object { '{0} {1,-25} {2,-12} {3}' -f $(if ($_.Health -eq 'Normal') {'[OK]  '} else {'[WARN]'}), $_.VMName, $_.State, $_.Health }""", tags=('diagnostic', 'health')),
    # ---- File server -------------------------------------------------------------------------
    FS("fs_sessions", "SMB sessions to this server",
       "Who is connected to this server's shares right now, from where and with which "
       "dialect.",
       need_cmd("Get-SmbSession", "the SMB server cmdlets") + r"""try { $s = @(Get-SmbSession -ErrorAction Stop) }
catch { "ERROR: $($_.Exception.Message)"; return }
if (-not $s.Count) { '[INFO] No one is connected to this PC''s shares.'; return }
$s | Select-Object ClientComputerName, ClientUserName, NumOpens, Dialect, SecondsExists | Format-Table -AutoSize | Out-String -Width 220""",
       requires="SMB server (any Windows with sharing on)", admin=True, tags=('diagnostic', 'triage')),
    FS("fs_open_files", "Files open over the network",
       "Files on this server's shares that remote users have open -- 'who has the "
       "spreadsheet locked'.",
       need_cmd("Get-SmbOpenFile", "the SMB server cmdlets") + r"""try { $f = @(Get-SmbOpenFile -ErrorAction Stop) }
catch { "ERROR: $($_.Exception.Message)"; return }
if (-not $f.Count) { '[INFO] No files are open over the network.'; return }
$f | Select-Object ClientUserName, ClientComputerName, Path | Format-Table -AutoSize | Out-String -Width 220""",
       requires="SMB server (any Windows with sharing on)", admin=True, tags=('diagnostic', 'triage')),
    FS("fs_server_config", "SMB server security settings",
       "SMB server configuration that matters for security: SMB1, signing, encryption, "
       "guest access and the null-session pipes.",
       need_cmd("Get-SmbServerConfiguration", "the SMB server cmdlets") + L("Get-SmbServerConfiguration | Select-Object EnableSMB1Protocol, EnableSMB2Protocol, RequireSecuritySignature, EnableSecuritySignature, EncryptData, RejectUnencryptedAccess, AnnounceServer, AutoShareServer, NullSessionPipes"),
       requires="SMB server (any Windows)", tags=('inventory', 'security')),
    FS("fsrm_quotas", "FSRM quotas",
       "File Server Resource Manager quotas with their limit and usage.",
       need_cmd("Get-FsrmQuota", _FS) + T("Get-FsrmQuota | Select-Object Path, @{n='LimitGB';e={[math]::Round($_.Size/1GB,1)}}, @{n='UsedGB';e={[math]::Round($_.Usage/1GB,1)}}, SoftLimit, Disabled"), tags=('inventory', 'storage')),
    FS("fsrm_screens", "FSRM file screens",
       "File screens and the file groups they block.",
       need_cmd("Get-FsrmFileScreen", _FS) + T("Get-FsrmFileScreen | Select-Object Path, Active, @{n='Blocks';e={$_.IncludeGroup -join ', '}}"), tags=('inventory', 'storage')),
    FS("dfsn_namespaces", "DFS namespaces",
       "DFS namespaces and their folder targets.",
       need_cmd("Get-DfsnRoot", "the DFSN module (DFS Namespaces)") + r"""Get-DfsnRoot -ErrorAction Stop | ForEach-Object { "== $($_.Path) ($($_.Type), $($_.State))"; Get-DfsnFolder -Path "$($_.Path)\*" -ErrorAction Stop | ForEach-Object { $f = $_; Get-DfsnFolderTarget -Path $f.Path -ErrorAction Stop | ForEach-Object { "   $($f.Path) -> $($_.TargetPath) [$($_.State)]" } } }""",
       requires="DFSN module", tags=('inventory',)),
    FS("dfsr_health", "DFS Replication groups and backlog",
       "DFS-R replication groups, their connections and the backlog count per connection.",
       need_cmd("Get-DfsReplicationGroup", "the DFSR module (DFS Replication)") + r"""Get-DfsReplicationGroup -ErrorAction Stop | ForEach-Object { $g = $_.GroupName; "== $g"
  $folders = @(Get-DfsReplicatedFolder -GroupName $g -ErrorAction Stop)
  Get-DfsrConnection -GroupName $g -ErrorAction Stop | ForEach-Object { $connection = $_
    foreach ($folder in $folders) {
      try {
        $records = @(Get-DfsrBacklog -GroupName $g -FolderName $folder.FolderName -SourceComputerName $connection.SourceComputerName -DestinationComputerName $connection.DestinationComputerName -Verbose -ErrorAction Stop 4>&1)
        $counts = @($records | Where-Object { $_ -is [System.Management.Automation.VerboseRecord] } | ForEach-Object {
          if ($_.Message -match '\bCount:\s*(\d+)') { [long]$Matches[1] }
        })
        if ($counts.Count -eq 1) {
          $b = $counts[0]
          '{0} {1} -> {2}  folder {3}  backlog {4}' -f $(if ($b -gt 100) {'[WARN]'} else {'[OK]  '}), $connection.SourceComputerName, $connection.DestinationComputerName, $folder.FolderName, $b
        } else {
          '[INFO] {0} -> {1} folder {2}: backlog count unknown (verbose count unavailable or unrecognised).' -f $connection.SourceComputerName, $connection.DestinationComputerName, $folder.FolderName
          $records | Where-Object { $_ -is [System.Management.Automation.VerboseRecord] } | ForEach-Object { $_.Message }
        }
      } catch { "ERROR: $g / $($folder.FolderName) -- $($_.Exception.Message)" }
    }
  }
}""",
       requires="DFSR module", timeout=600, tags=('diagnostic', 'health')),
    # ---- Failover Cluster --------------------------------------------------------------------
    CL("cl_nodes", "Cluster nodes",
       "Cluster name, nodes and their state.",
       need_cmd("Get-ClusterNode", _CL) + r""""Cluster: $((Get-Cluster).Name)"
Get-ClusterNode | Select-Object Name, State, NodeWeight, DynamicWeight | Format-Table -AutoSize | Out-String -Width 220""", tags=('inventory',)),
    CL("cl_groups", "Cluster roles (groups)",
       "Every clustered role with its owner node and state.",
       need_cmd("Get-ClusterGroup", _CL) + T("Get-ClusterGroup | Select-Object Name, OwnerNode, State, GroupType"), tags=('inventory',)),
    CL("cl_resources", "Cluster resources not online",
       "Resources that are offline or failed, with their role.",
       need_cmd("Get-ClusterResource", _CL) + r"""$r = Get-ClusterResource | Where-Object State -ne 'Online'
if (-not $r) { '[OK]   Every cluster resource is online.'; return }
$r | ForEach-Object { "[BAD]  $($_.Name) ($($_.ResourceType)) in $($_.OwnerGroup): $($_.State)" }""", tags=('diagnostic', 'health')),
    CL("cl_csv", "Cluster Shared Volumes",
       "CSVs with owner, state and free space.",
       need_cmd("Get-ClusterSharedVolume", _CL) + T("Get-ClusterSharedVolume | ForEach-Object { $p = $_.SharedVolumeInfo.Partition; [pscustomobject]@{ Name=$_.Name; Owner=$_.OwnerNode; State=$_.State; Path=$_.SharedVolumeInfo.FriendlyVolumeName; FreeGB=[math]::Round($p.FreeSpace/1GB); SizeGB=[math]::Round($p.Size/1GB) } }"), tags=('inventory', 'storage')),
    CL("cl_quorum", "Cluster quorum",
       "Quorum type and witness resource.",
       need_cmd("Get-ClusterQuorum", _CL) + L("Get-ClusterQuorum | Select-Object Cluster, QuorumType, QuorumResource"), tags=('inventory',)),
    CL("cl_networks", "Cluster networks",
       "Cluster networks, their role (cluster only / client and cluster) and state.",
       need_cmd("Get-ClusterNetwork", _CL) + T("Get-ClusterNetwork | Select-Object Name, Role, State, Address, AddressMask"), tags=('inventory', 'network')),
    # ---- Roles & features --------------------------------------------------------------------
    RF("rf_installed", "Installed roles and features",
       "Server roles, role services and features that are installed.",
       need_cmd("Get-WindowsFeature", _SM) + T("Get-WindowsFeature | Where-Object Installed | Select-Object Name, DisplayName, FeatureType"), tags=('inventory', 'baseline')),
    RF("rf_server_info", "Server identity",
       "Edition, installation type (Server Core or Desktop Experience), domain role and "
       "uptime.",
       r"""$os = Get-CimInstance Win32_OperatingSystem; $cs = Get-CimInstance Win32_ComputerSystem
if ($os.ProductType -eq 1) { 'NOT AVAILABLE: this is a Windows client, not a server.'; return }
$roles = @{0='Standalone workstation';1='Member workstation';2='Standalone server';3='Member server';4='Backup domain controller';5='Primary domain controller'}
[pscustomobject]@{ OS=$os.Caption; Build=$os.BuildNumber; InstallationType=(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').InstallationType
  DomainRole=$roles[[int]$cs.DomainRole]; LastBoot=$os.LastBootUpTime } | Format-List | Out-String -Width 220""",
       requires="Windows Server", tags=('inventory', 'baseline')),
    RF("rf_rds_sessions", "Remote Desktop Services sessions",
       "RDS user sessions on this session host, with state and idle time.",
       need_cmd("Get-RDUserSession", "the RemoteDesktop module (RDS)") + T("Get-RDUserSession -ErrorAction Stop | Select-Object UserName, HostServer, SessionState, CreateTime, IdleTime"),
       requires="RemoteDesktop module", tags=('inventory',)),
    RF("rf_wsus", "WSUS server status",
       "The WSUS server's update and computer counts and its last synchronisation.",
       need_cmd("Get-WsusServer", "the UpdateServices module (WSUS)") + r"""$w = Get-WsusServer
$s = $w.GetSubscription(); "Last sync: $($s.GetLastSynchronizationInfo().EndTime) -- $($s.GetLastSynchronizationInfo().Result)"
$st = $w.GetStatus(); "Computers: $($st.ComputerTargetCount)  needing updates: $($st.ComputerTargetsNeedingUpdatesCount)  with failures: $($st.ComputerTargetsWithUpdateErrorsCount)"
"Updates needed by computers: $($st.UpdatesNeededByComputersCount)  not approved: $($st.NotApprovedUpdatesCount)" """,
       requires="UpdateServices module", tags=('inventory',)),
    RF("rf_certificate_authority", "Certificate Authority",
       "If this server is a CA: its name, type and the templates it publishes.",
       need_cmd("certutil.exe", "certutil") + r"""$c = certutil.exe -cainfo 2>&1
if ($LASTEXITCODE -ne 0) { 'NOT AVAILABLE: this server is not a certificate authority.'; return }
$c | Select-Object -First 25
''; 'Published templates:'; certutil.exe -CATemplates 2>&1 | Select-Object -First 40""",
       requires="AD CS role", tags=('inventory',)),
    RF("rf_iis_installed", "Web server (IIS) features installed",
       "Which IIS role services are installed -- including risky ones like WebDAV, CGI or "
       "directory browsing.",
       need_cmd("Get-WindowsFeature", _SM) + T("Get-WindowsFeature Web-* | Where-Object Installed | Select-Object Name, DisplayName"), tags=('inventory',)),
]

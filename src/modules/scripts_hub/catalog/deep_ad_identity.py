"""Active Directory, Identity & Domain Infrastructure Deep Diagnostics. Read-only."""
from ._base import CHK, L, T, maker, need_cmd

AD = maker("Active Directory", server=True, requires="Active Directory module")
GPO = maker("Group Policy (domain)", server=True, requires="Group Policy module")
DNS = maker("DNS Server", server=True, requires="DNS Server module")
DNS_INBOX = maker("DNS Server", server=True, requires="")

SCRIPTS = [
    AD(
        "ad_fsmo",
        "FSMO Roles, Domain Controllers & GC Topology",
        "Discovers all 5 Flexible Single Master Operation (FSMO) role holders, "
        "Global Catalog servers, and domain controller operating system builds.",
        need_cmd("Get-ADDomain", "Active Directory module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: FSMO ROLES & DOMAIN CONTROLLER TOPOLOGY           " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Active Directory relies on 5 FSMO role holders: 2 Forest" -ForegroundColor Gray
Write-Host "[INFO] roles (Schema, Domain Naming) and 3 Domain roles (PDC Emulator, RID Master," -ForegroundColor Gray
Write-Host "[INFO] Infrastructure Master). The PDC Emulator handles password changes and time sync." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Forest & Domain FSMO Role Holders ===" -ForegroundColor Cyan
try {
    $forest = Get-ADForest -ErrorAction Stop
    $domain = Get-ADDomain -ErrorAction Stop

    Write-Host "Forest-level Roles:" -ForegroundColor Cyan
    Write-Host ("  [OK]   Schema Master         : {0}" -f $forest.SchemaMaster) -ForegroundColor Green
    Write-Host ("  [OK]   Domain Naming Master  : {0}" -f $forest.DomainNamingMaster) -ForegroundColor Green

    Write-Host "`nDomain-level Roles:" -ForegroundColor Cyan
    Write-Host ("  [OK]   PDC Emulator          : {0}" -f $domain.PDCEmulator) -ForegroundColor Green
    Write-Host ("  [OK]   RID Master            : {0}" -f $domain.RIDMaster) -ForegroundColor Green
    Write-Host ("  [OK]   Infrastructure Master : {0}" -f $domain.InfrastructureMaster) -ForegroundColor Green
} catch {
    Write-Host "[BAD]  Failed querying FSMO role holders: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host "`n=== Phase 2: Domain Controller Topology & Global Catalogs ===" -ForegroundColor Cyan
try {
    $dcs = Get-ADDomainController -Filter * -ErrorAction Stop
    Write-Host ("[INFO] Discovered {0} Domain Controller(s):" -f $dcs.Count) -ForegroundColor Cyan
    foreach ($dc in $dcs) {
        $gc = if ($dc.IsGlobalCatalog) { "GC  " } else { "    " }
        $rodc = if ($dc.IsReadOnly) { "RODC" } else { "RWDC" }
        Write-Host ("  [{0}] [{1}] {2,-24} Site: {3,-15} IP: {4,-15} OS: {5}" -f $gc, $rodc, $dc.HostName, $dc.Site, $dc.IPv4Address, $dc.OperatingSystem)
    }
} catch {
    Write-Host "[INFO] Domain controller enumeration: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[STATUS] Active Directory FSMO roles and DC topology enumerated." -ForegroundColor Green
Write-Host "[INFO] Verify that PDC Emulator is sync'd to a reliable NTP time source." -ForegroundColor Gray
""",
    ),
    AD(
        "dc_replication_matrix",
        "Domain Controller Replication Health & Partner Matrix",
        "Measures replication partner latency, consecutive sync failures, and warns of lingering object danger.",
        need_cmd("Get-ADDomain", "Active Directory module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: AD REPLICATION PARTNER HEALTH MATRIX               " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Domain Controllers replicate directory partitions" -ForegroundColor Gray
Write-Host "[INFO] (Schema, Configuration, Domain). Consecutive replication failures exceeding" -ForegroundColor Gray
Write-Host "[INFO] the Tombstone Lifetime (180 days) cause lingering objects and quarantine." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Active Directory Replication Partner Health ===" -ForegroundColor Cyan
$failCount = 0
try {
    $reps = Get-ADReplicationPartnerMetadata -Target (Get-ADDomain).PDCEmulator -ErrorAction Stop
    foreach ($r in $reps) {
        $consec = $r.ConsecutiveFailureCount
        $lastSuccess = $r.LastReplicationSuccess
        $partner = $r.Partner
        if ($consec -gt 0) {
            $failCount++
            Write-Host ("[BAD]  Replication Partner '{0}' has {1} consecutive failure(s)!" -f $partner, $consec) -ForegroundColor Red
            Write-Host ("       Last Success: {0} | Error: {1}" -f $lastSuccess, $r.LastReplicationResult) -ForegroundColor Red
        } else {
            Write-Host ("[OK]   Replication Partner '{0}' is HEALTHY (Last sync: {1})" -f $partner, $lastSuccess) -ForegroundColor Green
        }
    }
    if ($failCount -eq 0) {
        Write-Host "`n[OK]   All Active Directory replication partners synchronized with zero consecutive errors." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] AD Replication Partner query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($failCount -gt 0) {
    Write-Host ("[ACTION REQUIRED] Found {0} failing replication partnership(s)!" -f $failCount) -ForegroundColor Red
    Write-Host "[REMEDIATION] 1. Check network connectivity, DNS resolution, and RPC port 135/49152+ on failing partners."
    Write-Host "[REMEDIATION] 2. Run detailed replication status in command prompt: repadmin /showrepl"
    Write-Host "[REMEDIATION] 3. Test replication sync: repadmin /syncall /AdeP"
} else {
    Write-Host "[STATUS] Active Directory replication topology is completely healthy and converging." -ForegroundColor Green
}
""",
    ),
    AD(
        "kerberos_spn_audit",
        "Kerberos SPN Collisions, Delegation & Encryption Audit",
        "Searches for duplicate Service Principal Names (SPN), unconstrained delegation risks, and RC4 encryption types.",
        need_cmd("Get-ADDomain", "Active Directory module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: KERBEROS SPN COLLISIONS & DELEGATION RISKS        " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Service Principal Names (SPNs) associate a service with" -ForegroundColor Gray
Write-Host "[INFO] a logon account. Duplicate SPNs break Kerberos authentication across apps." -ForegroundColor Gray
Write-Host "[INFO] Unconstrained delegation allows a compromised server to impersonate any user." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Duplicate Service Principal Names (SPN) Check ===" -ForegroundColor Cyan
$duplicates = 0
try {
    $spns = Get-ADObject -Filter 'servicePrincipalName -like "*"' -Properties servicePrincipalName, sAMAccountName -ErrorAction Stop
    $spnMap = @{}
    foreach ($obj in $spns) {
        foreach ($s in $obj.servicePrincipalName) {
            if ($spnMap.ContainsKey($s)) {
                $duplicates++
                Write-Host ("[BAD]  Duplicate SPN collision: '{0}' on '{1}' and '{2}'" -f $s, $spnMap[$s], $obj.sAMAccountName) -ForegroundColor Red
            } else {
                $spnMap[$s] = $obj.sAMAccountName
            }
        }
    }
    if ($duplicates -eq 0) {
        Write-Host ("[OK]   Evaluated {0} unique SPNs. Zero duplicate collisions detected." -f $spnMap.Count) -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] SPN query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 2: Unconstrained Kerberos Delegation Exposure ===" -ForegroundColor Cyan
$unconstrainedCount = 0
try {
    $unconstrained = Get-ADAccount -Filter { TrustedForDelegation -eq $true -and PrimaryGroupID -ne 516 } -ErrorAction SilentlyContinue
    if ($unconstrained) {
        $unconstrainedCount = $unconstrained.Count
        Write-Host ("[WARN] Found {0} non-DC account(s) with Unconstrained Delegation enabled:" -f $unconstrainedCount) -ForegroundColor Yellow
        $unconstrained | ForEach-Object { Write-Host ("  [WARN] Account: {0} ({1})" -f $_.SamAccountName, $_.ObjectClass) -ForegroundColor Yellow }
    } else {
        Write-Host "[OK]   Zero non-DC accounts configured with dangerous Unconstrained Delegation." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] Delegation query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($duplicates -gt 0) {
    Write-Host ("[ACTION REQUIRED] Remove duplicate SPNs using setspn: setspn -D <SPN> <Account>" -f $duplicates) -ForegroundColor Red
}
if ($unconstrainedCount -gt 0) {
    Write-Host "[SECURITY RISK] Transition unconstrained delegation accounts to Resource-Based Constrained Delegation (RBCD)." -ForegroundColor Yellow
}
if ($duplicates -eq 0 -and $unconstrainedCount -eq 0) {
    Write-Host "[STATUS] Kerberos SPN configuration and delegation boundaries are secure." -ForegroundColor Green
}
""",
    ),
    AD(
        "stale_privileged_accounts",
        "Stale Privileged Accounts & AdminSDHolder Orphans",
        "Identifies domain administrative accounts with passwords that never expire or no logon in over 90 days.",
        need_cmd("Get-ADDomain", "Active Directory module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: PRIVILEGED ACCOUNTS & PASSWORD HYGIENE AUDIT      " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Tier-0 accounts (Domain Admins, Enterprise Admins) must" -ForegroundColor Gray
Write-Host "[INFO] follow strict hygiene: expiring passwords, multi-factor authentication," -ForegroundColor Gray
Write-Host "[INFO] and regular removal of dormant accounts to prevent lateral takeover." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Domain Admins & Enterprise Admins Password Hygiene ===" -ForegroundColor Cyan
$warnAdmins = 0
try {
    $da = Get-ADGroupMember -Identity "Domain Admins" -Recursive -ErrorAction Stop | Get-ADUser -Properties PasswordNeverExpires, LastLogonDate, Enabled
    $cutoff = (Get-Date).AddDays(-90)

    foreach ($u in $da) {
        $pne = $u.PasswordNeverExpires
        $lastLogon = $u.LastLogonDate
        $isInactive = (-not $lastLogon) -or ($lastLogon -lt $cutoff)
        
        if ($pne -or $isInactive) {
            $warnAdmins++
            Write-Host ("[WARN] Admin: {0,-20} Enabled: {1,-5} PassNeverExpires: {2,-5} LastLogon: {3}" -f (
                $u.SamAccountName, $u.Enabled, $pne, $lastLogon
            )) -ForegroundColor Yellow
        } else {
            Write-Host ("[OK]   Admin: {0,-20} Enabled: {1,-5} PassNeverExpires: {2,-5} LastLogon: {3}" -f (
                $u.SamAccountName, $u.Enabled, $pne, $lastLogon
            )) -ForegroundColor Green
        }
    }
} catch {
    Write-Host "[INFO] Privileged account query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($warnAdmins -gt 0) {
    Write-Host ("[ACTION REQUIRED] Found {0} Domain Admin(s) with dormant logins or non-expiring passwords." -f $warnAdmins) -ForegroundColor Yellow
    Write-Host "[REMEDIATION] 1. Disable dormant administrator accounts that have not logged in for 90+ days."
    Write-Host "[REMEDIATION] 2. Enforce regular password rotation for all privileged tier-0 identities."
} else {
    Write-Host "[STATUS] All active Domain Admins adhere to password rotation and activity standards." -ForegroundColor Green
}
""",
    ),
    GPO(
        "gpo_list",
        "Group Policy Inventory, WMI Filters & Status",
        "Enumerates all domain Group Policy Objects, modification dates, enabled status, and WMI filter attachments.",
        need_cmd("Get-GPO", "Group Policy module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: DOMAIN GROUP POLICY OBJECTS (GPO) INVENTORY       " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: GPOs enforce configuration baselines across domain machines." -ForegroundColor Gray
Write-Host "[INFO] Disabled user/computer components improve client logon times. WMI filters" -ForegroundColor Gray
Write-Host "[INFO] must evaluate efficiently to prevent Group Policy processing delays." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Domain Group Policy Objects Inventory ===" -ForegroundColor Cyan
try {
    $gpos = Get-GPO -All -ErrorAction Stop | Sort-Object DisplayName
    Write-Host ("[INFO] Total GPOs in Domain: {0}" -f $gpos.Count) -ForegroundColor Cyan
    foreach ($g in $gpos) {
        $status = $g.GpoStatus
        $wmi = if ($g.WmiFilter) { $g.WmiFilter.Name } else { "None" }
        $tag = if ($status -eq 'AllSettingsDisabled') { "[INFO]" } else { "[OK]  " }
        Write-Host ("{0} [{1,-20}] {2,-36} Modified: {3:yyyy-MM-dd} WMI: {4}" -f $tag, $status, $g.DisplayName, $g.ModificationTime, $wmi)
    }
} catch {
    Write-Host "[INFO] GPO query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[BEST PRACTICES]:"
Write-Host "  1. If a GPO only configures Computer settings, set GpoStatus to 'UserSettingsDisabled' to optimize processing."
Write-Host "  2. Clean up unlinked or completely disabled GPOs to maintain a tidy Active Directory environment."
""",
    ),
    GPO(
        "gpo_replication_sysvol",
        "SYSVOL & Group Policy DFS-R Consistency Diagnostic",
        "Validates Active Directory GPO versions against GPT.INI in SYSVOL to catch broken DFS-R replication.",
        need_cmd("Get-GPO", "Group Policy module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: GPO SYSVOL DFS-R CONSISTENCY DIAGNOSTIC           " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: GPOs have two halves: the Active Directory LDAP container" -ForegroundColor Gray
Write-Host "[INFO] and the SYSVOL filesystem folder (GPT.INI). If versions diverge, DFS-R" -ForegroundColor Gray
Write-Host "[INFO] replication is broken and workstations apply mismatched, corrupted policies." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Active Directory vs SYSVOL Version Consistency ===" -ForegroundColor Cyan
$mismatchCount = 0
try {
    $domain = (Get-ADDomain).DNSRoot
    $sysvolPolicies = "\\$domain\SYSVOL\$domain\Policies"
    $gpos = Get-GPO -All -ErrorAction Stop

    foreach ($g in $gpos) {
        $guid = $g.Id.ToString('B').ToUpper()
        $gptIni = Join-Path $sysvolPolicies "$guid\GPT.INI"
        if (Test-Path $gptIni) {
            $content = Get-Content $gptIni -Raw -ErrorAction SilentlyContinue
            if ($content -match 'Version=(\d+)') {
                $sysvolVer = [int]$matches[1]
                $adVer = $g.UserVersion + ($g.ComputerVersion * 65536)
                if ($sysvolVer -ne $adVer) {
                    $mismatchCount++
                    Write-Host ("[WARN] GPO Version Mismatch: '{0}' - AD Ver: {1}, SYSVOL Ver: {2}" -f $g.DisplayName, $adVer, $sysvolVer) -ForegroundColor Yellow
                } else {
                    Write-Host ("[OK]   GPO '{0}' - Version {1} synchronized." -f $g.DisplayName, $adVer) -ForegroundColor Green
                }
            }
        } else {
            $mismatchCount++
            Write-Host ("[BAD]  GPT.INI missing in SYSVOL for GPO: '{0}' ({1})" -f $g.DisplayName, $guid) -ForegroundColor Red
        }
    }
    if ($mismatchCount -eq 0) {
        Write-Host "`n[OK]   All domain GPOs match exact SYSVOL GPT versions." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] GPO consistency check: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($mismatchCount -gt 0) {
    Write-Host ("[ACTION REQUIRED] Found {0} GPO version mismatch(es) or missing GPT.INI files!" -f $mismatchCount) -ForegroundColor Red
    Write-Host "[REMEDIATION] 1. Inspect DFS-R event log on all DCs: Microsoft-Windows-DFSR/Operational."
    Write-Host "[REMEDIATION] 2. Verify SYSVOL share permissions and replication backlog via dfsrdiag."
} else {
    Write-Host "[STATUS] Active Directory and SYSVOL policy stores are in complete version lockstep." -ForegroundColor Green
}
""",
    ),
    DNS(
        "dns_zones",
        "DNS Server Zones, Scavenging & Aging Health",
        "Inspects DNS Server forward/reverse zones, scavenging enablement, refresh intervals, and dynamic update security.",
        need_cmd("Get-DnsServerZone", "DNS Server module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: DNS SERVER ZONES & AGING/SCAVENGING HEALTH         " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Active Directory Integrated DNS zones should enforce" -ForegroundColor Gray
Write-Host "[INFO] 'Secure Only' dynamic updates to prevent rogue IP overwrites. Zone aging" -ForegroundColor Gray
Write-Host "[INFO] and scavenging cleans up obsolete records from decommissioned hosts." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: DNS Server Hosted Zones & Security Configuration ===" -ForegroundColor Cyan
try {
    $zones = Get-DnsServerZone -ErrorAction Stop | Where-Object { -not $_.IsAutoCreated }
    Write-Host ("[INFO] Active DNS Zones ({0}):" -f $zones.Count) -ForegroundColor Cyan
    foreach ($z in $zones) {
        $sec = $z.DynamicUpdate
        $scav = if ($z.Aging) { "Aging/Scavenging ON" } else { "Aging OFF" }
        $tag = if ($sec -eq 'Secure') { "[OK]  " } elseif ($sec -eq 'None') { "[INFO]" } else { "[WARN]" }
        Write-Host ("{0} [{1,-16}] {2,-32} Type: {3,-12} {4}" -f $tag, $sec, $z.ZoneName, $z.ZoneType, $scav)
    }
} catch {
    Write-Host "[INFO] DNS server zone query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[DNS BEST PRACTICES]:"
Write-Host "  1. Active Directory zones should always have Dynamic Updates set to 'Secure' (SecureOnly)."
Write-Host "  2. Enable scavenging on one primary DNS server to purge records older than 7+7 days."
""",
    ),
    DNS_INBOX(
        "dns_srv_records_audit",
        "Active Directory Core DNS SRV Records Verification",
        "Queries critical Active Directory SRV locator records required for DC location and Kerberos authentication.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: AD CORE DNS SRV LOCATOR RECORDS AUDIT             " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Workstations discover Domain Controllers using DNS SRV" -ForegroundColor Gray
Write-Host "[INFO] records (_ldap._tcp.dc._msdcs, _kerberos._tcp). If these records fail to" -ForegroundColor Gray
Write-Host "[INFO] resolve, domain join, user logons, and GPO downloads fail across the fleet." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Core Domain Controller DNS SRV Locator Records ===" -ForegroundColor Cyan
$domain = $null
try {
    $domain = [System.DirectoryServices.ActiveDirectory.Domain]::GetComputerDomain().Name
} catch {}
if (-not $domain) {
    try { $domain = (Get-CimInstance Win32_ComputerSystem).Domain } catch {}
}

if (-not $domain -or $domain -eq "WORKGROUP") {
    Write-Host "[INFO] Computer is in WORKGROUP mode; skipping domain SRV record lookup." -ForegroundColor Gray
    Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
    Write-Host "[STATUS] Machine is not joined to an Active Directory domain." -ForegroundColor Gray
    return
}

Write-Host ("[INFO] Target Active Directory Domain: {0}" -f $domain) -ForegroundColor Cyan
$srvQueries = @(
    "_ldap._tcp.dc._msdcs.$domain",
    "_kerberos._tcp.$domain",
    "_kpasswd._tcp.$domain",
    "_ldap._tcp.pdc._msdcs.$domain"
)

$srvFailCount = 0
foreach ($q in $srvQueries) {
    try {
        $res = Resolve-DnsName -Name $q -Type SRV -ErrorAction Stop
        Write-Host ("[OK]   SRV Record resolved: {0} -> Target: {1}:{2}" -f $q, $res.NameTarget[0], $res.Port[0]) -ForegroundColor Green
    } catch {
        $srvFailCount++
        Write-Host ("[BAD]  Failed resolving required AD SRV record: {0} ({1})" -f $q, $_.Exception.Message) -ForegroundColor Red
    }
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($srvFailCount -gt 0) {
    Write-Host ("[ACTION REQUIRED] {0} critical Active Directory SRV locator records failed to resolve!" -f $srvFailCount) -ForegroundColor Red
    Write-Host "[REMEDIATION] 1. Verify workstation DNS server configuration points to Domain Controllers."
    Write-Host "[REMEDIATION] 2. On Domain Controllers, restart Netlogon service to re-register DNS SRV records."
} else {
    Write-Host "[STATUS] All essential Active Directory SRV locator records resolve successfully." -ForegroundColor Green
}
""",
    ),
    AD(
        "ad_krbtgt_password_age",
        "krbtgt Account Password Age & Golden Ticket Risk",
        "Audits the krbtgt account password age: warns if the password has not been rotated "
        "in over 180 days (critical Golden Ticket persistence vulnerability).",
        need_cmd("Get-ADDomain", "Active Directory module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: KRBTGT PASSWORD AGE & GOLDEN TICKET RISK          " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: The krbtgt account key encrypts all Kerberos TGT tickets." -ForegroundColor Gray
Write-Host "[INFO] If krbtgt key is stolen, attackers forge Golden Tickets granting eternal" -ForegroundColor Gray
Write-Host "[INFO] domain persistence. Microsoft recommends rotating krbtgt twice per year." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: krbtgt Key Distribution Account Password Age ===" -ForegroundColor Cyan
try {
    $krbtgt = Get-ADUser -Identity "krbtgt" -Properties PasswordLastSet, msDS-KrbTgtLink -ErrorAction Stop
    $lastSet = $krbtgt.PasswordLastSet
    $ageDays = if ($lastSet) { [math]::Round(((Get-Date) - $lastSet).TotalDays, 0) } else { 9999 }

    if ($ageDays -gt 365) {
        Write-Host ("[BAD]  krbtgt password last changed {0} days ago ({1:yyyy-MM-dd})! High Golden Ticket vulnerability." -f $ageDays, $lastSet) -ForegroundColor Red
    } elseif ($ageDays -gt 180) {
        Write-Host ("[WARN] krbtgt password last changed {0} days ago ({1:yyyy-MM-dd}). Routine 180-day rotation recommended." -f $ageDays, $lastSet) -ForegroundColor Yellow
    } else {
        Write-Host ("[OK]   krbtgt password rotated recently ({0} days ago on {1:yyyy-MM-dd})." -f $ageDays, $lastSet) -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] krbtgt query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[REMEDIATION GUIDANCE]:"
Write-Host "  To rotate krbtgt safely without dropping active sessions, use Microsoft's official krbtgt rotation script."
Write-Host "  Remember: krbtgt must be rotated TWICE with a replication wait interval (typically 10-24h) between rotations."
""",
    ),
    AD(
        "ad_recycle_bin_tombstone_audit",
        "Active Directory Recycle Bin & Tombstone Lifetime",
        "Verifies whether the AD Recycle Bin optional feature is enabled, inspects forest tombstone lifetime, "
        "and checks recently deleted directory objects.",
        need_cmd("Get-ADDomain", "Active Directory module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: AD RECYCLE BIN & TOMBSTONE LIFETIME AUDIT         " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Active Directory Recycle Bin allows instant recovery of" -ForegroundColor Gray
Write-Host "[INFO] deleted users, groups, and OUs with all group memberships intact, avoiding" -ForegroundColor Gray
Write-Host "[INFO] complicated authoritative backup restores using ntdsutil." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Active Directory Recycle Bin Status ===" -ForegroundColor Cyan
$binEnabled = $false
try {
    $bin = Get-ADOptionalFeature -Filter 'Name -like "Recycle Bin Feature"' -ErrorAction Stop
    $enabledScopes = $bin.EnabledScopes
    if ($enabledScopes) {
        $binEnabled = $true
        Write-Host "[OK]   Active Directory Recycle Bin is ENABLED for the forest." -ForegroundColor Green
    } else {
        Write-Host "[WARN] Active Directory Recycle Bin is NOT ENABLED! Accidental deletions require authoritative restore." -ForegroundColor Yellow
    }

    $tombstone = (Get-ADObject "CN=Directory Service,CN=Windows NT,CN=Services,$((Get-ADRootDSE).ConfigurationNamingContext)" -Properties tombstoneLifetime -ErrorAction SilentlyContinue).tombstoneLifetime
    $tombVal = if ($tombstone) { $tombstone } else { "180 (Default)" }
    Write-Host ("[INFO] Forest Tombstone Lifetime: {0} days" -f $tombVal)
} catch {
    Write-Host "[INFO] AD Recycle Bin query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if (-not $binEnabled) {
    Write-Host "[ACTION REQUIRED] Enable Active Directory Recycle Bin in Active Directory Administrative Center" -ForegroundColor Yellow
    Write-Host "[ACTION REQUIRED] or via PowerShell: enable-adoptionalfeature 'Recycle Bin Feature' -Scope ForestOrConfigurationSet." -ForegroundColor Yellow
} else {
    Write-Host "[STATUS] Active Directory Recycle Bin is active and protecting forest objects." -ForegroundColor Green
}
""",
    ),
    AD(
        "ad_trust_relationships_matrix",
        "Active Directory Domain & Forest Trust Relationships",
        "Audits all domain and forest trusts, direction (Inbound, Outbound, Bidirectional), "
        "trust type (Forest, External, Realm), and SID filtering enforcement.",
        need_cmd("Get-ADDomain", "Active Directory module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: AD DOMAIN & FOREST TRUST RELATIONSHIPS            " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Trusts allow authentication across domain boundaries." -ForegroundColor Gray
Write-Host "[INFO] SID Filtering must be enforced on external trusts to prevent SID History" -ForegroundColor Gray
Write-Host "[INFO] injection attacks where foreign users claim Domain Admin rights." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Domain & Forest Trust Inventory ===" -ForegroundColor Cyan
try {
    $trusts = Get-ADTrust -Filter * -ErrorAction Stop
    if ($trusts) {
        Write-Host ("[INFO] Configured Domain / Forest Trusts ({0}):" -f $trusts.Count) -ForegroundColor Cyan
        foreach ($t in $trusts) {
            $dir = $t.Direction
            $type = $t.TrustType
            $sidFilt = if ($t.SidFilteringQuarantined) { "Quarantined (Enforced)" } else { "Allowed (Not Quarantined)" }
            $tag = if ($t.SidFilteringQuarantined) { "[OK]  " } else { "[WARN]" }
            Write-Host ("{0} Trust: {1,-28} Direction: {2,-14} Type: {3,-10} SID Filtering: {4}" -f $tag, $t.Name, $dir, $type, $sidFilt)
        }
    } else {
        Write-Host "[OK]   Zero external or forest trusts configured (Isolated Domain Forest)." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] AD Trust query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[STATUS] Trust topology enumerated. Verify that external trusts have SID filtering enabled." -ForegroundColor Gray
""",
    ),
]


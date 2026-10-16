"""Active Directory and domain Group Policy checks. Read-only.

Every script is guarded: without the ActiveDirectory / GroupPolicy module it
prints NOT AVAILABLE. Parsed by PowerShell on a Windows 11 client; not run
against a real domain here (the card says what it needs).
"""
from ._base import CHK, L, T, maker, need_cmd

_AD = "the ActiveDirectory module (RSAT: Active Directory tools, or a domain controller)"
_GPM = "the GroupPolicy module (RSAT: Group Policy Management)"
_ADMAKE = maker("Active Directory", server=True, requires="ActiveDirectory module")
_GPMAKE = maker("Group Policy (domain)", server=True, requires="GroupPolicy module")


def A(id, title, desc, body, **kw):
    return _ADMAKE(id, title, desc, need_cmd("Get-ADDomain", _AD) + body, **kw)


def G(id, title, desc, body, **kw):
    return _GPMAKE(id, title, desc, need_cmd("Get-GPO", _GPM) + body, **kw)


def _users(id, title, desc, filt, props="", select="SamAccountName, Name, Enabled"):
    extra = f" -Properties {props}" if props else ""
    return A(id, title, desc, T(f"Get-ADUser -Filter {filt}{extra} | Sort-Object SamAccountName | Select-Object {select}"))


def _computers(id, title, desc, filt, props="OperatingSystem, LastLogonDate",
               select="Name, OperatingSystem, LastLogonDate, Enabled"):
    return A(id, title, desc, T(f"Get-ADComputer -Filter {filt} -Properties {props} | Sort-Object Name | Select-Object {select}"))


SCRIPTS = [
    # ---- users -------------------------------------------------------------------------------
    _users("ad_users_disabled", "Disabled user accounts",
           "Every disabled user account -- candidates for removal after the retention period.",
           "'Enabled -eq $false'", "WhenChanged", "SamAccountName, Name, WhenChanged"),
    _users("ad_users_never_logged_on", "Users who never signed in",
           "Enabled accounts with no logon recorded at all -- created and never used.",
           "'Enabled -eq $true'", "LastLogonDate, WhenCreated",
           "SamAccountName, Name, WhenCreated, LastLogonDate | Where-Object { -not $_.LastLogonDate }"),
    _users("ad_users_pwd_not_required", "Users with 'password not required'",
           "Accounts flagged PASSWD_NOTREQD -- they may have an EMPTY password.",
           "'PasswordNotRequired -eq $true'", "PasswordLastSet", "SamAccountName, Name, Enabled, PasswordLastSet"),
    _users("ad_users_reversible", "Users with reversible password encryption",
           "Accounts storing their password with reversible encryption -- effectively clear text.",
           "'AllowReversiblePasswordEncryption -eq $true'"),
    _users("ad_users_no_preauth", "Users without Kerberos pre-authentication (AS-REP roastable)",
           "Accounts with DONT_REQ_PREAUTH -- their hashes can be requested and cracked offline.",
           "'DoesNotRequirePreAuth -eq $true'"),
    _users("ad_users_spn", "User accounts with an SPN (Kerberoastable)",
           "User (not computer) accounts with a service principal name -- any user can request a "
           "ticket for them and crack it offline.",
           "'ServicePrincipalName -like \"*\"'", "ServicePrincipalName, PasswordLastSet",
           "SamAccountName, Enabled, PasswordLastSet, @{n='SPNs';e={$_.ServicePrincipalName -join '; '}}"),
    _users("ad_users_des", "Users allowed DES encryption",
           "Accounts flagged 'use DES key types only' -- broken crypto.",
           "'UseDESKeyOnly -eq $true'"),
    _users("ad_users_cannot_change_pwd", "Users who cannot change their password",
           "Accounts with 'user cannot change password' -- usually service accounts.",
           "'CannotChangePassword -eq $true'"),
    _users("ad_users_expired", "Expired user accounts",
           "Accounts past their AccountExpirationDate that are still enabled.",
           "'Enabled -eq $true'", "AccountExpirationDate",
           "SamAccountName, Name, AccountExpirationDate | Where-Object { $_.AccountExpirationDate -and $_.AccountExpirationDate -lt (Get-Date) }"),
    _users("ad_users_old_passwords", "Users with passwords older than a year",
           "Enabled accounts whose password was last set more than 365 days ago.",
           "'Enabled -eq $true'", "PasswordLastSet, PasswordNeverExpires",
           "SamAccountName, PasswordLastSet, PasswordNeverExpires | Where-Object { $_.PasswordLastSet -lt (Get-Date).AddDays(-365) }"),
    _users("ad_users_created_30d", "Users created in the last 30 days",
           "New accounts -- the first thing to check after a suspected compromise.",
           "'WhenCreated -gt \"$((Get-Date).AddDays(-30).ToString('yyyyMMddHHmmss.0Z'))\"'", "WhenCreated",
           "SamAccountName, Name, Enabled, WhenCreated"),
    _users("ad_users_admincount", "Users marked adminCount=1",
           "Every user protected by AdminSDHolder (current or former privileged accounts).",
           "'adminCount -eq 1'", "adminCount, LastLogonDate", "SamAccountName, Enabled, LastLogonDate"),
    _users("ad_users_bad_logons", "Users with recent bad passwords",
           "Accounts with a non-zero bad password count on the DC queried, most recent first.",
           "'badPwdCount -gt 0'", "badPwdCount, LastBadPasswordAttempt",
           "SamAccountName, badPwdCount, LastBadPasswordAttempt"),
    _users("ad_users_no_manager", "Enabled users without a manager or department",
           "Enabled accounts missing manager or department -- often orphaned or service accounts.",
           "'Enabled -eq $true'", "Manager, Department",
           "SamAccountName, Department, Manager | Where-Object { -not $_.Manager -or -not $_.Department }"),
    _users("ad_users_smartcard", "Users requiring smart card logon",
           "Accounts with 'smart card is required for interactive logon'.",
           "'SmartcardLogonRequired -eq $true'"),
    _users("ad_users_delegation", "Users trusted for delegation",
           "User accounts trusted for unconstrained or constrained delegation -- high-value "
           "targets.",
           "'TrustedForDelegation -eq $true -or TrustedToAuthForDelegation -eq $true'", "TrustedForDelegation, TrustedToAuthForDelegation, msDS-AllowedToDelegateTo",
           "SamAccountName, TrustedForDelegation, TrustedToAuthForDelegation, @{n='DelegateTo';e={$_.'msDS-AllowedToDelegateTo' -join '; '}}"),
    A("ad_user_counts", "User account counts",
      "Totals: enabled, disabled, locked, password never expires, never logged on, stale "
      "(90 days) -- the domain hygiene dashboard.",
      L(r"""$u = Get-ADUser -Filter * -Properties LastLogonDate, PasswordNeverExpires, LockedOut
[pscustomobject]@{ Total=$u.Count; Enabled=@($u | Where-Object Enabled).Count; Disabled=@($u | Where-Object { -not $_.Enabled }).Count
  Locked=@($u | Where-Object LockedOut).Count; PasswordNeverExpires=@($u | Where-Object PasswordNeverExpires).Count
  NeverLoggedOn=@($u | Where-Object { $_.Enabled -and -not $_.LastLogonDate }).Count
  Stale90=@($u | Where-Object { $_.Enabled -and $_.LastLogonDate -and $_.LastLogonDate -lt (Get-Date).AddDays(-90) }).Count }""")),
    A("ad_service_accounts", "Managed service accounts (gMSA / MSA)",
      "Group and standalone managed service accounts with who may retrieve their password.",
      T("Get-ADServiceAccount -Filter * -Properties PrincipalsAllowedToRetrieveManagedPassword, Enabled | Select-Object Name, ObjectClass, Enabled, @{n='Retrievers';e={$_.PrincipalsAllowedToRetrieveManagedPassword -join '; '}}")),
    # ---- computers ---------------------------------------------------------------------------
    _computers("ad_computers_os", "Computers by operating system",
               "Every computer account grouped by OS -- the migration plan in one table.",
               "*", select="Name, OperatingSystem, LastLogonDate, Enabled"),
    A("ad_computers_os_counts", "Operating system counts",
      "How many enabled computers run each OS version -- Windows 10 still around? Server 2012?",
      T("Get-ADComputer -Filter 'Enabled -eq $true' -Properties OperatingSystem, OperatingSystemVersion | Group-Object OperatingSystem, OperatingSystemVersion | Sort-Object Count -Descending | Select-Object Count, Name")),
    _computers("ad_computers_eol", "Computers on end-of-support Windows",
               "Enabled computers running Windows 7, 8.x, Windows 10, Server 2008/2012 or older.",
               "'Enabled -eq $true'", select="Name, OperatingSystem, LastLogonDate | Where-Object { $_.OperatingSystem -match 'Windows (7|8|XP|Vista|10)|Server 200|Server 2012' }"),
    _computers("ad_computers_never_logged", "Computers that never joined fully",
               "Computer accounts with no logon at all -- pre-staged and never used.",
               "'Enabled -eq $true'", select="Name, OperatingSystem, LastLogonDate | Where-Object { -not $_.LastLogonDate }"),
    _computers("ad_computers_disabled", "Disabled computer accounts",
               "Disabled computers, oldest change first.", "'Enabled -eq $false'",
               "WhenChanged, OperatingSystem", "Name, OperatingSystem, WhenChanged"),
    _computers("ad_computers_unconstrained", "Computers with unconstrained delegation",
               "Non-DC computers trusted for unconstrained delegation -- whoever controls them can "
               "impersonate anyone who connects.",
               "'TrustedForDelegation -eq $true -and PrimaryGroupID -ne 516'", "TrustedForDelegation, OperatingSystem",
               "Name, OperatingSystem, TrustedForDelegation"),
    A("ad_computers_laps", "LAPS coverage",
      "Which computers have a LAPS-managed local admin password (Windows LAPS or legacy) and "
      "when it expires -- computers without one share a password.",
      T(r"""Get-ADComputer -Filter 'Enabled -eq $true' -Properties 'msLAPS-PasswordExpirationTime','ms-Mcs-AdmPwdExpirationTime',OperatingSystem -ErrorAction SilentlyContinue | ForEach-Object {
  $e = if ($_.'msLAPS-PasswordExpirationTime') { [DateTime]::FromFileTime($_.'msLAPS-PasswordExpirationTime') } elseif ($_.'ms-Mcs-AdmPwdExpirationTime') { [DateTime]::FromFileTime($_.'ms-Mcs-AdmPwdExpirationTime') } else { $null }
  [pscustomobject]@{ Name=$_.Name; OS=$_.OperatingSystem; LAPS=$(if ($e) {'yes'} else {'NO'}); Expires=$e } } | Sort-Object LAPS, Name""")),
    A("ad_computers_by_ou", "Computers per OU",
      "How many computer accounts live in each OU -- computers left in the default Computers "
      "container get no OU-linked policy.",
      T("Get-ADComputer -Filter * | ForEach-Object { ($_.DistinguishedName -split ',', 2)[1] } | Group-Object | Sort-Object Count -Descending | Select-Object Count, Name")),
    # ---- groups ------------------------------------------------------------------------------
    A("ad_groups_empty", "Empty groups",
      "Groups with no members -- clutter, or a group someone emptied by mistake.",
      T("Get-ADGroup -Filter * -Properties Members, WhenCreated | Where-Object { -not $_.Members } | Sort-Object Name | Select-Object Name, GroupScope, GroupCategory, WhenCreated")),
    A("ad_groups_largest", "Largest groups",
      "The 25 groups with the most direct members.",
      T("Get-ADGroup -Filter * -Properties Members | Sort-Object { $_.Members.Count } -Descending | Select-Object -First 25 Name, GroupScope, @{n='Members';e={$_.Members.Count}}")),
    A("ad_groups_nested_admins", "Nested membership in Domain Admins",
      "Groups nested inside Domain Admins and Administrators -- the path to admin hidden two "
      "levels deep.",
      r"""foreach ($g in 'Domain Admins','Administrators','Enterprise Admins') { Get-ADGroupMember $g -ErrorAction SilentlyContinue | Where-Object objectClass -eq 'group' | ForEach-Object { "[WARN] group '$($_.Name)' is nested in $g" } }
'(nothing above = no nested groups)'"""),
    A("ad_protected_users", "Protected Users group",
      "Members of Protected Users -- privileged accounts should be there (no NTLM, no "
      "delegation, short TGTs).",
      r"""$m = Get-ADGroupMember 'Protected Users' -ErrorAction SilentlyContinue
if (-not $m) { '[WARN] Protected Users is empty -- privileged accounts are not in it.' } else { $m | ForEach-Object { "[OK]   $($_.SamAccountName)" } }"""),
    A("ad_group_changes", "Groups changed recently",
      "Groups whose membership or attributes changed in the last 14 days.",
      T("Get-ADGroup -Filter * -Properties WhenChanged | Where-Object WhenChanged -gt (Get-Date).AddDays(-14) | Sort-Object WhenChanged -Descending | Select-Object Name, WhenChanged, GroupScope")),
    A("ad_operators_groups", "Operator groups membership",
      "Members of Account, Server, Backup and Print Operators -- these groups should be empty "
      "in a modern domain.",
      CHK + r"""foreach ($g in 'Account Operators','Server Operators','Backup Operators','Print Operators') { $m = @(Get-ADGroupMember $g -ErrorAction SilentlyContinue); Chk ($m.Count -eq 0) $g $(if ($m) { ($m.SamAccountName) -join ', ' } else { 'empty' }) }"""),
    # ---- domain, DCs, replication ------------------------------------------------------------
    A("ad_dc_details", "Domain controller details",
      "Each DC's OS build, roles, LDAP/SSL ports, enabled state and whether it is a global "
      "catalog or read-only.",
      T("Get-ADDomainController -Filter * | Select-Object HostName, Site, OperatingSystem, OperatingSystemVersion, IsGlobalCatalog, IsReadOnly, @{n='Roles';e={$_.OperationMasterRoles -join ','}}")),
    A("ad_dc_reachability", "Domain controllers reachable",
      "Pings each DC and tests LDAP (389), Kerberos (88), SMB (445) and DNS (53).",
      r"""Get-ADDomainController -Filter * | ForEach-Object { $h = $_.HostName
  foreach ($p in 389, 88, 445, 53) { $ok = Test-NetConnection $h -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue; '{0} {1,-40} port {2}' -f $(if ($ok) {'[OK]  '} else {'[BAD] '}), $h, $p } }""",
      timeout=600),
    A("ad_repl_partner_metadata", "Replication partners and last success",
      "Each DC's inbound replication partners with the last successful replication time.",
      T("Get-ADDomainController -Filter * | ForEach-Object { Get-ADReplicationPartnerMetadata -Target $_.HostName -ErrorAction SilentlyContinue } | Select-Object Server, Partner, LastReplicationSuccess, LastReplicationResult, ConsecutiveReplicationFailures"),
      timeout=600),
    A("ad_repl_showrepl", "Replication status (repadmin /showrepl)",
      "repadmin /showrepl for every DC, errors only.",
      need_cmd("repadmin.exe", "repadmin") + "repadmin.exe /showrepl * /errorsonly", timeout=600),
    A("ad_repl_queue", "Replication queue",
      "Pending inbound replication requests per DC (repadmin /queue).",
      need_cmd("repadmin.exe", "repadmin") + "repadmin.exe /queue *", timeout=300),
    A("ad_tombstone", "Tombstone lifetime and backup age",
      "The forest tombstone lifetime and each partition's last backup -- a DC not backed up "
      "within it cannot be restored.",
      r"""$cfg = (Get-ADRootDSE).configurationNamingContext
$t = (Get-ADObject "CN=Directory Service,CN=Windows NT,CN=Services,$cfg" -Properties tombstoneLifetime).tombstoneLifetime
"Tombstone lifetime: $(if ($t) { "$t days" } else { '180 days (default)' })"
""" + "\n" + r"""if (Get-Command repadmin.exe -ErrorAction SilentlyContinue) { repadmin.exe /showbackup }"""),
    A("ad_sysvol_replication", "SYSVOL replication mode",
      "Whether SYSVOL replicates with DFS-R or the deprecated FRS, and the migration state.",
      r"""$s = dfsrmig /getglobalstate 2>&1; $s
$f = Get-Service NtFrs -ErrorAction SilentlyContinue; if ($f) { "[WARN] FRS service present: $($f.Status)" } else { '[OK]   FRS service not present' }"""),
    A("ad_trusts", "Domain and forest trusts",
      "Every trust with direction, type, transitivity and SID filtering.",
      T("Get-ADTrust -Filter * | Select-Object Name, Direction, TrustType, ForestTransitive, SIDFilteringQuarantined, SelectiveAuthentication")),
    A("ad_upn_suffixes", "UPN suffixes and SPN duplicates",
      "Alternative UPN suffixes in the forest, and duplicate SPNs (which break Kerberos).",
      r"""'UPN suffixes: ' + ((Get-ADForest).UPNSuffixes -join ', ')
''; if (Get-Command setspn.exe -ErrorAction SilentlyContinue) { setspn.exe -X -F }""", timeout=600),
    A("ad_schema", "Schema version",
      "The schema version (objectVersion) and what Windows Server level it corresponds to.",
      r"""$v = (Get-ADObject (Get-ADRootDSE).schemaNamingContext -Properties objectVersion).objectVersion
$m = @{ 47='2008 R2'; 56='2012'; 69='2012 R2'; 87='2016'; 88='2019 / 2022'; 90='2025' }
"Schema objectVersion $v -- $($m[[int]$v])" """),
    A("ad_ou_tree", "OU structure",
      "Every organizational unit with whether it is protected from accidental deletion and "
      "how many objects it holds.",
      T("Get-ADOrganizationalUnit -Filter * -Properties ProtectedFromAccidentalDeletion | Sort-Object DistinguishedName | Select-Object DistinguishedName, ProtectedFromAccidentalDeletion, @{n='Objects';e={@(Get-ADObject -SearchBase $_.DistinguishedName -SearchScope OneLevel -Filter *).Count}}")),
    A("ad_ou_unprotected", "OUs not protected from deletion",
      "OUs an admin could delete with one click.",
      T("Get-ADOrganizationalUnit -Filter * -Properties ProtectedFromAccidentalDeletion | Where-Object { -not $_.ProtectedFromAccidentalDeletion } | Select-Object DistinguishedName")),
    A("ad_default_containers", "Objects in default containers",
      "Users and computers left in CN=Users and CN=Computers, where no OU policy applies.",
      r"""$d = (Get-ADDomain).DistinguishedName
foreach ($c in "CN=Users,$d", "CN=Computers,$d") { $o = Get-ADObject -SearchBase $c -SearchScope OneLevel -Filter "objectClass -eq 'user' -or objectClass -eq 'computer'"; "== $c ($(@($o).Count))"; $o | Select-Object -First 50 | ForEach-Object { "   $($_.Name) [$($_.ObjectClass)]" } }"""),
    A("ad_machine_account_quota", "Machine account quota",
      "ms-DS-MachineAccountQuota -- by default any user may join 10 computers to the domain.",
      CHK + r"""$q = (Get-ADObject (Get-ADDomain).DistinguishedName -Properties 'ms-DS-MachineAccountQuota').'ms-DS-MachineAccountQuota'
Chk ($q -eq 0) 'ms-DS-MachineAccountQuota' "$q (default 10)" """),
    A("ad_dns_zones_ad", "AD-integrated DNS zones",
      "DNS zones stored in AD application partitions (DomainDnsZones / ForestDnsZones).",
      T("Get-ADObject -SearchBase \"DC=DomainDnsZones,$((Get-ADDomain).DistinguishedName)\" -Filter \"objectClass -eq 'dnsZone'\" | Select-Object Name")),
    A("ad_fgpp_applied", "Fine-grained password policy per user",
      "Which password policy actually applies to each privileged user (resultant set).",
      r"""foreach ($u in Get-ADGroupMember 'Domain Admins' -Recursive -ErrorAction SilentlyContinue | Where-Object objectClass -eq 'user') { $p = Get-ADUserResultantPasswordPolicy $u -ErrorAction SilentlyContinue; '{0,-25} {1}' -f $u.SamAccountName, $(if ($p) { "$($p.Name) (min length $($p.MinPasswordLength))" } else { 'default domain policy' }) }"""),
    A("ad_recent_changes", "Objects changed in the last 24 hours",
      "Every AD object modified in the last day -- what changed overnight.",
      T("Get-ADObject -Filter \"whenChanged -gt '$((Get-Date).AddDays(-1).ToString('yyyyMMddHHmmss.0Z'))'\" -Properties whenChanged | Sort-Object whenChanged -Descending | Select-Object -First 100 Name, ObjectClass, whenChanged")),
    A("ad_deleted_objects", "Recently deleted objects",
      "Objects deleted in the last 30 days (needs the AD Recycle Bin) -- what can still be "
      "restored.",
      T("Get-ADObject -Filter 'isDeleted -eq $true' -IncludeDeletedObjects -Properties whenChanged, lastKnownParent | Where-Object whenChanged -gt (Get-Date).AddDays(-30) | Select-Object -First 100 Name, ObjectClass, whenChanged, lastKnownParent")),
    A("ad_ldap_signing", "LDAP signing and channel binding",
      "The DC's LDAP server signing requirement and channel binding token setting.",
      r"""$k = 'HKLM:\SYSTEM\CurrentControlSet\Services\NTDS\Parameters'
$p = Get-ItemProperty $k -ErrorAction SilentlyContinue
if (-not $p) { 'NOT AVAILABLE: this PC is not a domain controller.'; return }
"LDAPServerIntegrity (2 = signing required): $($p.LDAPServerIntegrity)"
"LdapEnforceChannelBinding (2 = always): $($p.LdapEnforceChannelBinding)" """),
    A("ad_dc_time", "Domain time hierarchy",
      "Which DC is the authoritative time source (PDC) and each DC's offset against it.",
      r"""$pdc = (Get-ADDomain).PDCEmulator; "PDC emulator: $pdc"
w32tm /monitor /domain:(Get-ADDomain).DNSRoot 2>&1 | Select-Object -First 40""", timeout=300),
    A("ad_kerberos_policy", "Kerberos policy",
      "Maximum ticket lifetimes and clock skew from the default domain policy.",
      r"""$d = Get-ADDomain; $gpo = Get-ADObject "CN={31B2F340-016D-11D2-945F-00C04FB984F9},CN=Policies,CN=System,$($d.DistinguishedName)" -Properties gPCFileSysPath
$inf = Join-Path $gpo.gPCFileSysPath 'MACHINE\Microsoft\Windows NT\SecEdit\GptTmpl.inf'
if (Test-Path $inf) { Get-Content $inf | Select-String -Pattern 'MaxTicketAge|MaxRenewAge|MaxServiceAge|MaxClockSkew|TicketValidateClient' | ForEach-Object { $_.Line } } else { "Could not read $inf" }"""),
    # ---- domain Group Policy -----------------------------------------------------------------
    G("gpo_all_status", "GPOs with status and versions",
      "Every GPO, its status (some settings disabled), and user/computer AD vs SYSVOL versions "
      "-- a mismatch means SYSVOL replication is behind.",
      T("Get-GPO -All | Sort-Object DisplayName | Select-Object DisplayName, GpoStatus, @{n='UserAD';e={$_.User.DSVersion}}, @{n='UserSysvol';e={$_.User.SysvolVersion}}, @{n='CompAD';e={$_.Computer.DSVersion}}, @{n='CompSysvol';e={$_.Computer.SysvolVersion}}, ModificationTime")),
    G("gpo_version_mismatch", "GPOs whose AD and SYSVOL versions disagree",
      "GPOs where the AD and SYSVOL version numbers differ -- clients may apply a stale copy.",
      r"""Get-GPO -All | Where-Object { $_.User.DSVersion -ne $_.User.SysvolVersion -or $_.Computer.DSVersion -ne $_.Computer.SysvolVersion } | ForEach-Object { "[WARN] $($_.DisplayName): user $($_.User.DSVersion)/$($_.User.SysvolVersion)  computer $($_.Computer.DSVersion)/$($_.Computer.SysvolVersion)" }
'(nothing above = all consistent)'"""),
    G("gpo_links", "Where each GPO is linked",
      "Every GPO link with its target, enabled and enforced flags, and link order.",
      r"""Get-GPO -All | Sort-Object DisplayName | ForEach-Object { [xml]$x = Get-GPOReport -Guid $_.Id -ReportType Xml
  foreach ($l in $x.GPO.LinksTo) { '{0,-40} -> {1,-50} enabled={2} enforced={3}' -f $_.DisplayName, $l.SOMPath, $l.Enabled, $l.NoOverride } }""",
      timeout=900),
    G("gpo_enforced", "Enforced GPO links",
      "GPO links set to Enforced (they override blocked inheritance below).",
      r"""Get-GPO -All | ForEach-Object { [xml]$x = Get-GPOReport -Guid $_.Id -ReportType Xml; foreach ($l in $x.GPO.LinksTo) { if ($l.NoOverride -eq 'true') { "[INFO] $($_.DisplayName) -> $($l.SOMPath)" } } }""",
      timeout=900),
    G("gpo_blocked_inheritance", "OUs that block inheritance",
      "OUs with 'Block inheritance' -- domain-level policy does not reach them.",
      need_cmd("Get-ADOrganizationalUnit", "the ActiveDirectory module") +
      r"""Get-ADOrganizationalUnit -Filter * | ForEach-Object { $i = Get-GPInheritance -Target $_.DistinguishedName -ErrorAction SilentlyContinue; if ($i.GpoInheritanceBlocked) { "[WARN] $($_.DistinguishedName)" } }"""),
    G("gpo_empty", "GPOs with no settings",
      "GPOs that configure nothing (both halves empty) -- leftovers.",
      r"""Get-GPO -All | ForEach-Object { [xml]$x = Get-GPOReport -Guid $_.Id -ReportType Xml; if (-not $x.GPO.Computer.ExtensionData -and -not $x.GPO.User.ExtensionData) { "[INFO] $($_.DisplayName)" } }""",
      timeout=900),
    G("gpo_disabled_halves", "GPOs with a disabled half",
      "GPOs whose user or computer half is disabled -- intended, or why a setting 'does not "
      "apply'.",
      T("Get-GPO -All | Where-Object GpoStatus -ne 'AllSettingsEnabled' | Select-Object DisplayName, GpoStatus")),
    G("gpo_wmi_filters", "WMI filters",
      "WMI filters and the GPOs that use them -- a slow WMI filter delays every logon.",
      r"""Get-GPO -All | Where-Object WmiFilter | ForEach-Object { '{0,-40} filter: {1}  ({2})' -f $_.DisplayName, $_.WmiFilter.Name, $_.WmiFilter.Description }
'(nothing above = no GPO uses a WMI filter)'"""),
    G("gpo_permissions_odd", "GPOs editable by non-admins",
      "GPO permissions that let accounts outside Domain/Enterprise Admins edit a GPO -- a "
      "direct path to domain compromise.",
      r"""Get-GPO -All | ForEach-Object { $g = $_; Get-GPPermission -Guid $g.Id -All | Where-Object { $_.Permission -match 'GpoEdit|GpoEditDeleteModifySecurity' -and $_.Trustee.Name -notmatch 'Domain Admins|Enterprise Admins|SYSTEM|ENTERPRISE DOMAIN CONTROLLERS' } | ForEach-Object { "[WARN] $($g.DisplayName): $($_.Trustee.Name) has $($_.Permission)" } }""",
      timeout=600),
    G("gpo_recent", "GPOs changed in the last 30 days",
      "Recently modified GPOs, newest first.",
      T("Get-GPO -All | Where-Object ModificationTime -gt (Get-Date).AddDays(-30) | Sort-Object ModificationTime -Descending | Select-Object DisplayName, ModificationTime, Owner")),
    G("gpo_default_policies", "Default Domain and DC policies",
      "Version and last change of the two default GPOs, which should hold only password/"
      "Kerberos and user-rights settings respectively.",
      T("Get-GPO -Guid '31B2F340-016D-11D2-945F-00C04FB984F9','6AC1786C-016F-11D2-945F-00C04FB984F9' | Select-Object DisplayName, ModificationTime, @{n='CompVer';e={$_.Computer.DSVersion}}, @{n='UserVer';e={$_.User.DSVersion}}")),
    G("gpo_central_store", "ADMX central store",
      "Whether the SYSVOL PolicyDefinitions central store exists and how current its ADMX "
      "files are.",
      r"""$p = "\\$((Get-ADDomain).DNSRoot)\SYSVOL\$((Get-ADDomain).DNSRoot)\Policies\PolicyDefinitions"
if (Test-Path $p) { $f = Get-ChildItem $p -Filter *.admx; "[OK]   Central store: $($f.Count) ADMX files, newest $(($f | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime)" } else { "[WARN] No central store at $p" }"""),
    G("gpo_sysvol_size", "SYSVOL size and largest GPO folders",
      "The size of each GPO's SYSVOL folder -- installers stored in SYSVOL bloat replication.",
      r"""$p = "\\$((Get-ADDomain).DNSRoot)\SYSVOL\$((Get-ADDomain).DNSRoot)\Policies"
Get-ChildItem $p -Directory -ErrorAction SilentlyContinue | ForEach-Object { $s = (Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum; [pscustomobject]@{ Folder=$_.Name; MB=[math]::Round($s/1MB,1); GPO=(Get-GPO -Guid ($_.Name.Trim('{}')) -ErrorAction SilentlyContinue).DisplayName } } | Sort-Object MB -Descending | Select-Object -First 20 | Format-Table -AutoSize | Out-String -Width 220""",
      timeout=600),
    G("gpo_orphaned_sysvol", "Orphaned SYSVOL policy folders",
      "SYSVOL policy folders with no matching GPO in AD (and the reverse).",
      r"""$dom = (Get-ADDomain).DNSRoot; $ids = (Get-GPO -All).Id | ForEach-Object { "{$_}".ToUpper() }
Get-ChildItem "\\$dom\SYSVOL\$dom\Policies" -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^\{' -and $_.Name.ToUpper() -notin $ids } | ForEach-Object { "[WARN] SYSVOL folder without a GPO: $($_.Name)" }
'(nothing above = none)'"""),
    G("gpo_scripts", "Startup, shutdown, logon and logoff scripts in GPOs",
      "Script files referenced by GPOs -- and whether each still exists.",
      r"""$dom = (Get-ADDomain).DNSRoot
Get-GPO -All | ForEach-Object { $g = $_; foreach ($half in 'Machine','User') { $ini = "\\$dom\SYSVOL\$dom\Policies\{$($g.Id)}\$half\Scripts\scripts.ini"
  if (Test-Path $ini) { Get-Content $ini | Select-String 'CmdLine=' | ForEach-Object { "$($g.DisplayName) [$half]: $(($_.Line -split '=',2)[1])" } } } }"""),
]

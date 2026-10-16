"""Security, Users & groups and Remote access checks. Read-only.

Audit scripts print one [OK]/[WARN]/[INFO] line per check, with the value
they read, so the pane can colour them and a reader can see the evidence.
"""
from ._base import CHK, CMD, L, T, maker, need_admin

S = maker("Security")
U = maker("Users & groups")
R = maker("Remote access")

_REG = (r"function RegVal($path, $name) { (Get-ItemProperty -Path $path -Name $name -ErrorAction SilentlyContinue).$name }" + "\n")

SCRIPTS = [
    # ---- Security ----------------------------------------------------------------------------
    S("hardening_baseline", "Quick hardening baseline (20 checks)",
      "Twenty settings a hardening review looks at first -- UAC, SMBv1, LLMNR, NetBIOS, "
      "WDigest, LSA protection, Credential Guard, RDP NLA, AutoRun, PowerShell v2, firewall, "
      "Defender real-time, SmartScreen, Secure Boot, BitLocker on C: -- each with the value read.",
      CHK + _REG + r"""$sys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
Chk ((RegVal $sys EnableLUA) -ne 0) 'UAC enabled' "EnableLUA=$(RegVal $sys EnableLUA)"
Chk ((RegVal $sys ConsentPromptBehaviorAdmin) -in 1,2,5,$null) 'UAC prompts admins' "ConsentPromptBehaviorAdmin=$(RegVal $sys ConsentPromptBehaviorAdmin)"
$smb = Get-SmbServerConfiguration -ErrorAction SilentlyContinue
Chk ($smb -and -not $smb.EnableSMB1Protocol) 'SMBv1 server off' "EnableSMB1Protocol=$($smb.EnableSMB1Protocol)"
Chk ($smb -and $smb.RequireSecuritySignature) 'SMB signing required (server)' "RequireSecuritySignature=$($smb.RequireSecuritySignature)"
Chk ((RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient' EnableMulticast) -eq 0) 'LLMNR off' "EnableMulticast=$(RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\DNSClient' EnableMulticast) (absent = on)"
$nb = Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=TRUE' | Where-Object { $_.TcpipNetbiosOptions -ne 2 }
Chk (-not $nb) 'NetBIOS over TCP/IP off' "$(@($nb).Count) adapter(s) still allow it"
Chk ((RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest' UseLogonCredential) -ne 1) 'WDigest plaintext creds off' "UseLogonCredential=$(RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest' UseLogonCredential)"
Chk ((RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' RunAsPPL) -in 1,2) 'LSA protection (RunAsPPL)' "RunAsPPL=$(RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' RunAsPPL)"
$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction SilentlyContinue
Chk ($dg.SecurityServicesRunning -contains 1) 'Credential Guard running' "running services: $($dg.SecurityServicesRunning -join ',')"
Chk ($dg.SecurityServicesRunning -contains 2) 'Memory integrity (HVCI) running' "running services: $($dg.SecurityServicesRunning -join ',')"
Chk ((RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' UserAuthentication) -eq 1) 'RDP requires NLA' "UserAuthentication=$(RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' UserAuthentication)"
Chk ((RegVal 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer' NoDriveTypeAutoRun) -eq 255) 'AutoRun off on all drives' "NoDriveTypeAutoRun=$(RegVal 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer' NoDriveTypeAutoRun)"
$isAdm = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
# Get-WindowsOptionalFeature THROWS for a standard user, whatever -ErrorAction says.
$v2 = if ($isAdm) { Get-WindowsOptionalFeature -Online -FeatureName MicrosoftWindowsPowerShellV2Root -ErrorAction SilentlyContinue }
# Windows 11 24H2+ removed PowerShell 2.0 entirely: elevated, an absent feature is the answer.
if ($v2) { Chk ($v2.State -ne 'Enabled') 'PowerShell 2.0 removed' $v2.State } elseif ($isAdm) { Chk $true 'PowerShell 2.0 removed' 'not part of this Windows build' } else { Chk $null 'PowerShell 2.0 removed' 'needs administrator to read' }
$fw = Get-NetFirewallProfile -ErrorAction SilentlyContinue
Chk (-not ($fw | Where-Object { -not $_.Enabled })) 'Firewall on for every profile' (($fw | ForEach-Object { "$($_.Name)=$($_.Enabled)" }) -join ' ')
$av = Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction SilentlyContinue | Where-Object { (($_.productState -shr 8) -band 0xFF) -in 0x10,0x11 }
Chk ([bool]$av) 'An antivirus is active' (($av.displayName) -join ', ')
Chk ((RegVal 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer' SmartScreenEnabled) -ne 'Off') 'SmartScreen for apps' "SmartScreenEnabled=$(RegVal 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer' SmartScreenEnabled)"
try { $sb = Confirm-SecureBootUEFI -ErrorAction Stop; Chk $sb 'Secure Boot' $sb } catch { Chk $null 'Secure Boot' 'needs administrator / not UEFI' }
$bl = Get-BitLockerVolume -MountPoint $env:SystemDrive -ErrorAction SilentlyContinue
Chk $(if ($bl) { $bl.ProtectionStatus -eq 'On' } else { $null }) "BitLocker on $env:SystemDrive" $(if ($bl) { "$($bl.VolumeStatus), protection $($bl.ProtectionStatus)" } else { 'needs administrator to read' })
Chk ((RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' LmCompatibilityLevel) -ge 5) 'NTLMv2 only (LmCompatibilityLevel 5)' "LmCompatibilityLevel=$(RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' LmCompatibilityLevel) (absent = 3)" """,
      note="the PowerShell 2.0, Secure Boot and BitLocker checks need elevation", admin=True),
    S("defender_status", "Microsoft Defender status",
      "Real-time protection, tamper protection, signature age, last scans, cloud protection "
      "and whether Defender is in passive mode because another antivirus is installed.",
      L(r"""Get-MpComputerStatus | Select-Object AMRunningMode, RealTimeProtectionEnabled, IsTamperProtected, AntivirusSignatureVersion, AntivirusSignatureLastUpdated, QuickScanEndTime, FullScanEndTime, BehaviorMonitorEnabled, IoavProtectionEnabled, NISEnabled""")),
    S("defender_threats", "Defender detections history",
      "Threats Defender detected, when, where and what it did.",
      "$d = Get-MpThreatDetection -ErrorAction SilentlyContinue\n"
      "if (-not $d) { '[OK]   No threat detection recorded by Defender.'; return }\n"
      + T("$d | Sort-Object InitialDetectionTime -Descending | Select-Object InitialDetectionTime, ThreatID, @{n='Resources';e={$_.Resources -join '; '}}, ActionSuccess, @{n='Process';e={$_.ProcessName}}"),
      note="detection history is only fully readable elevated", admin=True),
    S("defender_prefs", "Defender preferences that weaken protection",
      "Exclusions, disabled features (real-time, behaviour, script scanning, IOAV), PUA "
      "protection and cloud level -- what an attacker or a 'performance guide' turns off.",
      CHK + r"""$p = Get-MpPreference -ErrorAction SilentlyContinue
if (-not $p) { 'ERROR: Defender preferences could not be read.'; return }
Chk (-not $p.DisableRealtimeMonitoring) 'Real-time monitoring' "Disable=$($p.DisableRealtimeMonitoring)"
Chk (-not $p.DisableBehaviorMonitoring) 'Behaviour monitoring' "Disable=$($p.DisableBehaviorMonitoring)"
Chk (-not $p.DisableScriptScanning) 'Script scanning' "Disable=$($p.DisableScriptScanning)"
Chk (-not $p.DisableIOAVProtection) 'Download scanning (IOAV)' "Disable=$($p.DisableIOAVProtection)"
Chk ($p.PUAProtection -eq 1) 'PUA protection' "PUAProtection=$($p.PUAProtection)"
Chk ($p.MAPSReporting -ge 1) 'Cloud protection (MAPS)' "MAPSReporting=$($p.MAPSReporting)"
foreach ($k in 'ExclusionPath','ExclusionProcess','ExclusionExtension','ExclusionIpAddress') { $v = @($p.$k) | Where-Object { $_ }
  if ($v -and $v[0] -like 'N/A*') { Chk $null $k 'needs administrator to read' } else { Chk (-not $v) $k $(if ($v) { $v -join '; ' } else { 'none' }) } }""",
      note="Defender redacts exclusions for a standard user", admin=True),
    S("asr_rules", "Attack Surface Reduction rules",
      "Which ASR rules are configured and in what mode (Block / Audit / Warn).",
      r"""$p = Get-MpPreference -ErrorAction SilentlyContinue
$ids = @($p.AttackSurfaceReductionRules_Ids); $acts = @($p.AttackSurfaceReductionRules_Actions)
if (-not $ids -or -not $ids[0]) { '[INFO] No ASR rule is configured (Windows default: all off).'; return }
$m = @{0='Disabled';1='Block';2='Audit';5='Not configured';6='Warn'}
for ($i = 0; $i -lt $ids.Count; $i++) { $a = [int]$acts[$i]; '{0} {1}  {2}' -f $(if ($a -eq 1) {'[OK]  '} elseif ($a -in 2,6) {'[INFO]'} else {'[WARN]'}), $ids[$i], $m[$a] }"""),
    S("firewall_rules_count", "Firewall rules summary",
      "How many rules exist per direction, action and profile, and how many are enabled.",
      T("Get-NetFirewallRule -ErrorAction SilentlyContinue | Group-Object Direction, Action, Enabled | Sort-Object Name | Select-Object Count, Name")),
    S("certs_untrusted_roots", "Non-Microsoft trusted root certificates",
      "Root certificates in the machine and user Trusted Root stores, minus the ones Windows "
      "ships with by thumbprint-independent issuer check. A root added by adware, a proxy or "
      "a 'security' product can intercept HTTPS.",
      T(r"""'Cert:\LocalMachine\Root','Cert:\CurrentUser\Root' | ForEach-Object { $s = $_; Get-ChildItem $s -ErrorAction SilentlyContinue | Where-Object { $_.Subject -notmatch 'Microsoft|VeriSign|DigiCert|GlobalSign|Entrust|Thawte|GeoTrust|Comodo|Sectigo|USERTrust|ISRG|Baltimore|QuoVadis|Starfield|Go Daddy|Amazon|Certum|IdenTrust|AAA Certificate|Symantec|Network Solutions|SECOM|Buypass|T-TeleSec|Actalis|SSL.com|Hongkong Post|Certainly|Atos|D-TRUST|Izenpe|emSign|HARICA|TWCA|Trustwave|SwissSign|Starfield|Equifax|Class 3 Public|Copyright \(c\) 1997' } |
  ForEach-Object { [pscustomobject]@{ Store=$s; Subject=$_.Subject; Expires=$_.NotAfter; Thumbprint=$_.Thumbprint } } }""")),
    S("code_integrity_blocks", "Blocked or unsigned drivers and binaries",
      "Code Integrity events: drivers or files blocked because they were unsigned, revoked or "
      "on Microsoft's vulnerable-driver blocklist, and Smart App Control blocks.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational'; Level=2,3} -MaxEvents 60 -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No code integrity blocks or warnings.'; return }
$e | ForEach-Object { '{0} {1:yyyy-MM-dd HH:mm}  {2,5}  {3}' -f $(if ($_.Level -eq 2) {'[BAD] '} else {'[WARN]'}), $_.TimeCreated, $_.Id, (($_.Message -split "`r?`n")[0]) }"""),
    S("smart_app_control", "Smart App Control and Windows Defender Application Control",
      "Smart App Control state (on / evaluation / off) and whether any WDAC policy is active.",
      r"""$s = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
"Smart App Control: $(switch ($s) { 0 {'off'} 1 {'ON (enforced)'} 2 {'evaluation'} default {'not reported'} })"
Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction SilentlyContinue | ForEach-Object { "Code integrity policy enforcement: $($_.CodeIntegrityPolicyEnforcementStatus) (0 off, 1 audit, 2 enforced)"; "User-mode CI: $($_.UsermodeCodeIntegrityPolicyEnforcementStatus)" }"""),
    S("uac_settings", "UAC settings in detail",
      "Every User Account Control value with what it means: admin and standard-user prompt "
      "behaviour, secure desktop, admin approval mode for the built-in Administrator.",
      _REG + r"""$k = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
$admin = @{0='elevate without prompting';1='credentials on secure desktop';2='consent on secure desktop';3='credentials';4='consent';5='consent for non-Windows binaries (default)'}
$user = @{0='deny';1='credentials on secure desktop';3='credentials (default)'}
"EnableLUA                  : $(RegVal $k EnableLUA)  (1 = UAC on)"
"ConsentPromptBehaviorAdmin : $(RegVal $k ConsentPromptBehaviorAdmin)  -> $($admin[[int](RegVal $k ConsentPromptBehaviorAdmin)])"
"ConsentPromptBehaviorUser  : $(RegVal $k ConsentPromptBehaviorUser)  -> $($user[[int](RegVal $k ConsentPromptBehaviorUser)])"
"PromptOnSecureDesktop      : $(RegVal $k PromptOnSecureDesktop)"
"FilterAdministratorToken   : $(RegVal $k FilterAdministratorToken)  (1 = built-in Administrator also gets UAC)"
"LocalAccountTokenFilterPolicy: $(RegVal $k LocalAccountTokenFilterPolicy)  (1 = remote admin by local accounts, pass-the-hash risk)" """),
    S("credential_manager", "Saved credentials (names only)",
      "What Windows Credential Manager holds for this user -- target names and types, never "
      "the secrets. Old saved credentials cause lockouts after a password change.",
      "cmdkey /list", kind=CMD),
    S("ssh_keys", "SSH keys and known hosts",
      "Public keys and the known_hosts list in your .ssh folder, and authorized_keys if this "
      "PC accepts SSH logins -- private key FILE NAMES only, never their contents.",
      r"""$d = Join-Path $env:USERPROFILE '.ssh'
if (-not (Test-Path $d)) { '[INFO] No .ssh folder.'; return }
Get-ChildItem $d -File | ForEach-Object { '{0,-30} {1,8} bytes  {2}' -f $_.Name, $_.Length, $_.LastWriteTime }
''
if (Test-Path "$d\known_hosts") { "known_hosts: $((Get-Content "$d\known_hosts" | Where-Object { $_.Trim() }).Count) host keys" }
foreach ($a in "$d\authorized_keys", "$env:ProgramData\ssh\administrators_authorized_keys") { if (Test-Path $a) { "[WARN] $a exists -- keys in it can log in to this PC" } }"""),
    S("autologon", "Automatic logon configured?",
      "Whether Windows signs in automatically, and if a plaintext DefaultPassword is stored "
      "in the registry (a serious finding).",
      CHK + _REG + r"""$k = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$on = (RegVal $k AutoAdminLogon) -eq '1'
Chk (-not $on) 'Automatic logon' "AutoAdminLogon=$(RegVal $k AutoAdminLogon) DefaultUserName=$(RegVal $k DefaultUserName)"
Chk ($null -eq (RegVal $k DefaultPassword)) 'No plaintext DefaultPassword' $(if ($null -ne (RegVal $k DefaultPassword)) { 'a password IS stored in clear text' } else { 'not present' })"""),
    S("lsa_packages", "LSA authentication and notification packages",
      "DLLs the Local Security Authority loads: authentication, security support providers "
      "and password notification packages. An unknown entry sees every password -- a known "
      "persistence trick.",
      _REG + r"""$k = 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa'
foreach ($n in 'Authentication Packages','Notification Packages','Security Packages') { '{0,-26} {1}' -f $n, ((RegVal $k $n) -join ', ') }
'{0,-26} {1}' -f 'OSConfig Security Packages', ((RegVal "$k\OSConfig" 'Security Packages') -join ', ')"""),
    S("screen_lock", "Screen lock and screensaver",
      "Whether the screen locks on its own: screensaver timeout and password, and the "
      "machine inactivity limit policy.",
      _REG + r"""$d = 'HKCU:\Control Panel\Desktop'
"Screensaver active      : $(RegVal $d ScreenSaveActive)"
"Screensaver timeout (s) : $(RegVal $d ScreenSaveTimeOut)"
"Password on resume      : $(RegVal $d ScreenSaverIsSecure)"
"InactivityTimeoutSecs   : $(RegVal 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' InactivityTimeoutSecs) (policy; empty = not set)"
powercfg /query SCHEME_CURRENT SUB_VIDEO VIDEOIDLE | Select-String 'Current (AC|DC)' | ForEach-Object { 'Display off ' + $_.Line.Trim() }"""),
    S("tls_protocols", "TLS/SSL protocols enabled",
      "SCHANNEL protocol settings: whether SSL 3.0, TLS 1.0 and 1.1 are explicitly disabled "
      "and TLS 1.2/1.3 enabled. Absent keys mean the OS default.",
      r"""foreach ($p in 'SSL 2.0','SSL 3.0','TLS 1.0','TLS 1.1','TLS 1.2','TLS 1.3') { foreach ($side in 'Client','Server') {
  $k = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\$p\$side"
  $e = (Get-ItemProperty $k -ErrorAction SilentlyContinue).Enabled
  $state = if ($null -eq $e) { 'OS default' } elseif ($e -eq 0) { 'DISABLED' } else { 'enabled' }
  $tag = if ($p -match 'SSL|1\.0|1\.1') { if ($state -eq 'DISABLED') {'[OK]  '} else {'[INFO]'} } else { if ($state -eq 'DISABLED') {'[WARN]'} else {'[OK]  '} }
  '{0} {1,-8} {2,-6} {3}' -f $tag, $p, $side, $state } }"""),
    S("unsigned_running", "Running programs without a valid signature",
      "Every running process whose executable is not validly Authenticode-signed (catalog "
      "signatures included), with its path. Most are harmless tools; unexpected ones in "
      "AppData or Temp are not.",
      r"""Get-Process | Where-Object Path | Select-Object -ExpandProperty Path -Unique | ForEach-Object {
  $s = Get-AuthenticodeSignature $_ -ErrorAction SilentlyContinue
  if ($s.Status -ne 'Valid') { $tag = if ($_ -match '\\(AppData|Temp|Users\\Public|ProgramData)\\') {'[WARN]'} else {'[INFO]'}; '{0} {1,-14} {2}' -f $tag, $s.Status, $_ } }""",
      note="other users' and system processes are not listed", timeout=240),
    S("open_shares_perms", "Share permissions",
      "Who has access to each non-administrative share, from the share ACL. 'Everyone: Full' "
      "on a share is the finding.",
      r"""$s = Get-SmbShare -ErrorAction SilentlyContinue | Where-Object { -not $_.Special }
if (-not $s) { '[OK]   No shares besides the administrative ones.'; return }
$s | ForEach-Object { $n = $_.Name; Get-SmbShareAccess -Name $n -ErrorAction SilentlyContinue | ForEach-Object { '{0} {1,-20} {2,-30} {3} {4}' -f $(if ($_.AccountName -match 'Everyone' -and $_.AccessRight -eq 'Full') {'[WARN]'} else {'[INFO]'}), $n, $_.AccountName, $_.AccessControlType, $_.AccessRight } }"""),
    S("audit_policy_all", "Audit policy (all subcategories)",
      "What Windows records in the Security log, subcategory by subcategory.",
      need_admin("auditpol refuses a standard user") + "auditpol /get /category:*", admin=True),
    S("browser_policies", "Browser policies in force",
      "Group Policy / registry policies applied to Edge and Chrome -- managed settings, "
      "forced extensions, homepage locks. Unexpected policies here are how adware hijacks a "
      "browser.",
      r"""foreach ($k in 'HKLM:\SOFTWARE\Policies\Microsoft\Edge','HKCU:\SOFTWARE\Policies\Microsoft\Edge','HKLM:\SOFTWARE\Policies\Google\Chrome','HKCU:\SOFTWARE\Policies\Google\Chrome') {
  if (Test-Path $k) { "== $k"; (Get-ItemProperty $k).PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { "[INFO] $($_.Name) = $($_.Value)" }
    Get-ChildItem $k -ErrorAction SilentlyContinue | ForEach-Object { "[WARN] subkey $($_.PSChildName): $(((Get-ItemProperty $_.PSPath).PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { $_.Value }) -join '; ')" } }
  else { "[OK]   $k -- no policy" } }"""),
    S("exploit_protection", "Exploit protection (system-wide)",
      "System-wide DEP, ASLR (bottom-up, high entropy, mandatory), CFG and SEHOP settings.",
      L("Get-ProcessMitigation -System | Select-Object @{n='DEP';e={$_.DEP.Enable}}, @{n='ASLR BottomUp';e={$_.ASLR.BottomUp}}, @{n='ASLR HighEntropy';e={$_.ASLR.HighEntropy}}, @{n='ASLR ForceRelocate';e={$_.ASLR.ForceRelocateImages}}, @{n='CFG';e={$_.CFG.Enable}}, @{n='SEHOP';e={$_.SEHOP.Enable}}")),
    S("windows_hello", "Sign-in options (PIN, Hello, password)",
      "Which Windows Hello credential providers this user has set up and whether "
      "passwordless sign-in is required.",
      r"""$ngc = Test-Path "$env:windir\ServiceProfiles\LocalService\AppData\Local\Microsoft\Ngc" -ErrorAction SilentlyContinue
"Windows Hello container present: $ngc"
$p = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device' -ErrorAction SilentlyContinue).DevicePasswordLessBuildVersion
"Require Windows Hello sign-in (passwordless): $(if ($p -eq 2) {'on'} elseif ($p -eq 0) {'off'} else {'not set'})"
Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers' -ErrorAction SilentlyContinue | ForEach-Object { (Get-ItemProperty $_.PSPath).'(default)' } | Where-Object { $_ } | Sort-Object | ForEach-Object { "provider: $_" }"""),
    S("rdp_brute_force", "Failed sign-in sources (brute force)",
      "Failed sign-ins (4625) in the last 7 days grouped by account and source address -- one "
      "address with hundreds of failures is password guessing.",
      need_admin("the Security log is only readable elevated") +
      T(r"""Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4625; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 5000 -ErrorAction SilentlyContinue | ForEach-Object {
  $d = @{}; ([xml]$_.ToXml()).Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }; [pscustomobject]@{ Account="$($d.TargetDomainName)\$($d.TargetUserName)"; Source=$d.IpAddress; Type=$d.LogonType } } |
  Group-Object Account, Source, Type | Sort-Object Count -Descending | Select-Object Count, Name"""),
      admin=True),
    # ---- Users & groups ----------------------------------------------------------------------
    U("local_users", "Local user accounts",
      "Every local account: enabled, last logon, password last set and whether it expires "
      "or is required. An enabled account nobody uses is an open door.",
      T("Get-LocalUser | Select-Object Name, Enabled, LastLogon, PasswordLastSet, PasswordExpires, PasswordRequired, UserMayChangePassword, Description")),
    U("local_groups", "Local groups and their members",
      "Every local group with its members -- Administrators, Remote Desktop Users, Hyper-V "
      "Administrators... membership in the wrong group is privilege.",
      r"""Get-LocalGroup | ForEach-Object { $g = $_; $m = @(Get-LocalGroupMember -Group $g -ErrorAction SilentlyContinue)
  if ($m.Count) { "== $($g.Name) ($($m.Count))"; $m | ForEach-Object { "   $($_.Name)  [$($_.ObjectClass), $($_.PrincipalSource)]" } } }"""),
    U("whoami_all", "Current user: groups and privileges",
      "This session's user, SID, group memberships and the privileges in its token -- what "
      "this process can actually do.",
      "whoami /all", kind=CMD),
    U("profiles", "User profiles on this PC",
      "Every user profile folder with its SID, last use and whether it is loaded -- orphaned "
      "profiles of departed users take space.",
      T(r"""Get-CimInstance Win32_UserProfile | Where-Object { -not $_.Special } | ForEach-Object {
  $n = try { (New-Object Security.Principal.SecurityIdentifier($_.SID)).Translate([Security.Principal.NTAccount]).Value } catch { '(unresolvable -- deleted account?)' }
  [pscustomobject]@{ Account=$n; Path=$_.LocalPath; LastUse=$_.LastUseTime; Loaded=$_.Loaded; SID=$_.SID } }""")),
    U("logged_on", "Who is signed in right now",
      "Interactive and remote sessions on this PC, with state and idle time.",
      "quser", kind=CMD),     # `query user` exits 1 even when it succeeds (measured); quser exits 0
    U("password_policy", "Local password and lockout policy",
      "Minimum length, age, history and the account lockout threshold -- 'net accounts'.",
      "net accounts", kind=CMD),
    U("admin_accounts_status", "Built-in Administrator and Guest",
      "Whether the built-in Administrator (RID 500) and Guest (RID 501) accounts are "
      "enabled, by SID -- they have their own names in other languages.",
      CHK + r"""Get-LocalUser | Where-Object { $_.SID.Value -match '-50[01]$' } | ForEach-Object { Chk (-not $_.Enabled) "$($_.Name) ($($_.SID.Value.Split('-')[-1]))" "Enabled=$($_.Enabled) LastLogon=$($_.LastLogon)" }"""),
    U("microsoft_accounts", "Microsoft and work accounts on this PC",
      "Accounts signed in to Windows itself: Microsoft accounts, Entra ID (work/school) "
      "joins and registrations -- dsregcmd's summary.",
      r"""dsregcmd /status | Select-String 'AzureAdJoined|EnterpriseJoined|DomainJoined|WorkplaceJoined|TenantName|DeviceId|AzureAdPrt |WamDefaultSet' | ForEach-Object { $_.Line.Trim() }"""),
    # ---- Remote access -----------------------------------------------------------------------
    R("rdp_status", "Remote Desktop status",
      "Whether Remote Desktop is enabled, its port, NLA requirement, who may connect, and "
      "whether the firewall lets it in.",
      CHK + _REG + r"""$on = (RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' fDenyTSConnections) -eq 0
Chk $null 'Remote Desktop' $(if ($on) {'ENABLED'} else {'disabled'})
"[INFO] Port -- $(RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' PortNumber)"
if ($on) { Chk ((RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' UserAuthentication) -eq 1) 'Network Level Authentication' 'required for RDP' }
Get-LocalGroupMember -SID S-1-5-32-555 -ErrorAction SilentlyContinue | ForEach-Object { "[INFO] Remote Desktop Users -- $($_.Name)" }
Get-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue | Where-Object Enabled -eq 'True' | ForEach-Object { "[INFO] firewall rule on -- $($_.DisplayName) ($($_.Profile))" }"""),
    R("winrm_status", "WinRM / PowerShell Remoting",
      "Whether the WinRM service runs, its listeners and TrustedHosts -- 'Enter-PSSession "
      "does not work' and 'who can remote into this PC'.",
      r"""$s = Get-Service WinRM -ErrorAction SilentlyContinue
"WinRM service: $($s.Status), start type $($s.StartType)"
if ($s.Status -eq 'Running') { winrm enumerate winrm/config/listener 2>&1; ''; "TrustedHosts: $((Get-Item WSMan:\localhost\Client\TrustedHosts -ErrorAction SilentlyContinue).Value)" }
else { '[INFO] Remoting is off -- nothing listens.' }""",
      note="listeners and TrustedHosts need elevation", admin=True),
    R("ssh_server", "OpenSSH server",
      "Whether the OpenSSH Server capability and sshd service are installed and running, "
      "the port and the default shell.",
      r"""$s = Get-Service sshd -ErrorAction SilentlyContinue
if (-not $s) { '[INFO] OpenSSH Server is not installed.'; return }
"sshd: $($s.Status), start type $($s.StartType)"
$cfg = "$env:ProgramData\ssh\sshd_config"
if (Test-Path $cfg) { Get-Content $cfg | Where-Object { $_ -match '^\s*(Port|PasswordAuthentication|PermitRootLogin|PubkeyAuthentication|AllowUsers|AllowGroups)\b' } }
"DefaultShell: $((Get-ItemProperty 'HKLM:\SOFTWARE\OpenSSH' -ErrorAction SilentlyContinue).DefaultShell)" """),
    R("remote_assistance", "Remote Assistance",
      "Whether Remote Assistance invitations are allowed and whether helpers may take control.",
      _REG + r"""$k = 'HKLM:\SYSTEM\CurrentControlSet\Control\Remote Assistance'
"fAllowToGetHelp      : $(RegVal $k fAllowToGetHelp) (1 = invitations allowed)"
"fAllowFullControl    : $(RegVal $k fAllowFullControl) (1 = helper can take control)" """),
    R("remote_tools_installed", "Remote-control software installed",
      "TeamViewer, AnyDesk, RustDesk, Chrome Remote Desktop, ScreenConnect, Splashtop, VNC, "
      "Quick Assist... installed programs and running services -- the first thing to check "
      "after a 'tech support' scam call.",
      r"""$pat = 'TeamViewer|AnyDesk|RustDesk|Chrome Remote Desktop|ScreenConnect|ConnectWise|Splashtop|VNC|LogMeIn|GoTo|Remote Utilities|Supremo|Zoho Assist|DWService|NinjaOne|Atera|Kaseya|Action1|MeshCentral|Parsec'
$apps = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match $pat }
$svc = Get-CimInstance Win32_Service | Where-Object { $_.DisplayName -match $pat -or $_.PathName -match $pat }
if (-not $apps -and -not $svc) { '[OK]   No known remote-control software found.'; return }
$apps | ForEach-Object { "[WARN] installed: $($_.DisplayName) $($_.DisplayVersion)" }
$svc | ForEach-Object { "[WARN] service: $($_.DisplayName) -- $($_.State), $($_.StartMode)" }"""),
    R("rdp_history", "Remote Desktop connections made FROM this PC",
      "Servers this user connected to with mstsc, from its MRU list and saved server "
      "entries (with the remembered username hint).",
      r"""$k = 'HKCU:\Software\Microsoft\Terminal Server Client'
(Get-ItemProperty "$k\Default" -ErrorAction SilentlyContinue).PSObject.Properties | Where-Object Name -like 'MRU*' | ForEach-Object { "recent: $($_.Value)" }
Get-ChildItem "$k\Servers" -ErrorAction SilentlyContinue | ForEach-Object { "saved: $($_.PSChildName)  user hint: $((Get-ItemProperty $_.PSPath).UsernameHint)" }"""),
    R("rdp_incoming_history", "Remote Desktop sessions INTO this PC",
      "Remote Desktop logons, disconnects and reconnects recorded by the Terminal Services "
      "session manager (21-25) in the last 30 days, with the source address.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-TerminalServices-LocalSessionManager/Operational'; Id=21,22,23,24,25; StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No Remote Desktop sessions in the last 30 days.'; return }
$e | ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1}  {2}' -f $_.TimeCreated, $_.Id, (($_.Message -split "`r?`n") -join ' ' -replace '\s+',' ') }"""),
]

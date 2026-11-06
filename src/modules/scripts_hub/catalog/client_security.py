"""Security, Users & groups and Remote access checks. Read-only.

Audit scripts print one [OK]/[WARN]/[INFO] line per check, with the value
they read, so the pane can colour them and a reader can see the evidence.
"""
from ._base import CHK, CMD, L, T, maker, need_admin

S = maker("Security")
U = maker("Users & groups")
R = maker("Remote access")

_PERSISTENCE_SCRIPTS = [
    S("client_wmi_permanent_subscriptions", "WMI permanent event persistence map",
      "Lists subscription filters, consumer payloads and bindings in root/subscription without executing consumers.",
      need_admin("complete permanent WMI subscription reads require elevation") + CHK + r"""foreach ($class in '__EventFilter','__EventConsumer','__FilterToConsumerBinding') {
  try {
    $rows = @(Get-CimInstance -Namespace root\subscription -ClassName $class -ErrorAction Stop)
    Chk $null $class "$($rows.Count) entries; legitimate management tools also use permanent subscriptions"
    $rows | Select-Object * -ExcludeProperty CimClass, CimInstanceProperties, CimSystemProperties | Format-List | Out-String -Width 220
  } catch { Chk $null $class "read failed: $($_.Exception.Message)" }
}
'[INFO] Review bindings alongside filter queries, executable paths and ScriptText. This inventory does not establish maliciousness or cover other namespaces.'""",
      admin=True, tags=('diagnostic', 'security', 'inventory', 'triage')),
    S("client_mde_onboarding_health", "Defender for Endpoint onboarding and Sense health",
      "Correlates local onboarding status, organization ID, Sense service and recent sensor events.",
      need_admin("MDE status and sensor event reads require elevation") + CHK + r"""try {
  $key = 'HKLM:\SOFTWARE\Microsoft\Windows Advanced Threat Protection\Status'
  if (Test-Path -LiteralPath $key -ErrorAction Stop) {
    $p = Get-ItemProperty -LiteralPath $key -ErrorAction Stop
    $ok = if ($null -eq $p.OnboardingState) { $null } else { $p.OnboardingState -eq 1 }
    Chk $ok 'MDE onboarding' "OnboardingState=$($p.OnboardingState); OrgId=$($p.OrgId); 1 means onboarded locally"
  } else { Chk $null 'MDE onboarding' 'status key absent; device may not be onboarded or licensed' }
} catch { Chk $null 'MDE onboarding' $_.Exception.Message }
try { $s = Get-Service Sense -ErrorAction Stop; Chk ($s.Status -eq 'Running') 'Sense sensor' "$($s.Status); $($s.StartType)" }
catch { Chk $null 'Sense sensor' $_.Exception.Message }
try {
  Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-SENSE/Operational'; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 20 -ErrorAction Stop | Select-Object TimeCreated, Id, LevelDisplayName, Message | Format-List | Out-String -Width 220
} catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { '[INFO] No SENSE events retained in seven days.' }
  else { Chk $null 'SENSE log' $_.Exception.Message }
}
Chk $null 'Cloud reporting' 'local onboarding and a running service do not prove recent telemetry receipt; verify device last-seen in the MDE portal'""",
      admin=True, tags=('diagnostic', 'security', 'health', 'fleet')),
    S("client_effective_user_rights", "Effective user rights assignments (merged export)",
      "Exports merged security policy to a temporary report and resolves privilege-assignment SIDs; never applies policy.",
      need_admin("secedit merged user-rights export requires elevation") + r"""$folder = Join-Path ([IO.Path]::GetTempPath()) ('WCT-rights-' + [guid]::NewGuid().ToString('N'))
try {
  [void][IO.Directory]::CreateDirectory($folder)
  $file = Join-Path $folder 'rights.inf'
  $output = & "$env:SystemRoot\System32\secedit.exe" /export /mergedpolicy /cfg $file /areas USER_RIGHTS /log (Join-Path $folder 'export.log') /quiet 2>&1
  $code = $LASTEXITCODE
  if ($code -ne 0) { "ERROR: secedit export exited $code"; $output; return }
  $lines = @(Get-Content -LiteralPath $file -ErrorAction Stop | Where-Object { $_ -match '^Se\w+\s*=' })
  if (-not $lines.Count) { 'NOT AVAILABLE: export contains no user-rights assignment lines.'; return }
  foreach ($line in $lines) {
    $right,$members = $line -split '=',2
    if (-not $members.Trim()) { "[INFO] $($right.Trim()) -- explicitly empty assignment"; continue }
    foreach ($member in ($members -split ',')) {
      $sid = $member.Trim().TrimStart('*'); $account = $sid
      if ($sid -like 'S-1-*') {
        try { $account = ([Security.Principal.SecurityIdentifier]::new($sid)).Translate([Security.Principal.NTAccount]).Value }
        catch { $account = "unresolved SID ($($_.Exception.Message))" }
      }
      '[INFO] {0} : {1} [{2}]' -f $right.Trim(), $account, $sid
    }
  }
  '[INFO] Merged assignment policy, not the privileges in existing logon tokens; group expansion and deny rights still apply.'
} catch { "ERROR: User-rights export: $($_.Exception.Message)" }
finally {
  if ([IO.Directory]::Exists($folder)) {
    try { [IO.Directory]::Delete($folder,$true) } catch { "ERROR: Temporary export cleanup failed: $($_.Exception.Message)" }
  }
}""", admin=True, tags=('diagnostic', 'security', 'compliance')),
    S("client_service_account_dependencies", "Service identity and dependency map",
      "Maps every non-SYSTEM service identity to required services and dependents for credential-change planning.",
      r"""try {
  $services = @(Get-CimInstance Win32_Service -ErrorAction Stop)
  if (-not $services.Count) { 'NOT AVAILABLE: service inventory returned no entries.'; return }
  $selected = @($services | Where-Object { $_.StartName -and $_.StartName -notmatch '^(LocalSystem|NT AUTHORITY\\SYSTEM)$' })
  if (-not $selected.Count) { '[INFO] No non-SYSTEM service identities returned.' }
  foreach ($s in $selected | Sort-Object StartName, Name) {
    try {
      $details = Get-Service -Name $s.Name -ErrorAction Stop
      [pscustomobject]@{Account=$s.StartName; Service=$s.Name; State=$s.State; StartMode=$s.StartMode; Requires=($details.ServicesDependedOn.Name -join ', '); Dependents=($details.DependentServices.Name -join ', '); Binary=$s.PathName} | Format-List | Out-String -Width 220
    } catch { "ERROR: Dependencies for $($s.Name): $($_.Exception.Message)" }
  }
  '[INFO] Includes built-in low-privilege and virtual accounts. Empty dependency fields mean none declared in SCM, not absence of application dependencies.'
} catch { "ERROR: Service identity map: $($_.Exception.Message)" }""",
      tags=('diagnostic', 'security', 'inventory')),
    S("client_credential_guard_intent_runtime", "Credential Guard: policy intent versus runtime",
      "Compares Device Guard runtime with LsaCfgFlags and VBS/HVCI configuration in policy and local keys.",
      CHK + r"""try {
  $dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction Stop
  if (-not $dg) { Chk $null 'Device Guard' 'no instance returned' }
  else {
    foreach ($id in 1,2) {
      $running = if ($null -eq $dg.SecurityServicesRunning) { $null } else { $dg.SecurityServicesRunning -contains $id }
      Chk $running $(if ($id -eq 1) { 'Credential Guard running' } else { 'HVCI running' }) "VBS status=$($dg.VirtualizationBasedSecurityStatus); configured=$($dg.SecurityServicesConfigured -join ','); running=$($dg.SecurityServicesRunning -join ',')"
    }
  }
} catch { Chk $null 'Device Guard runtime' $_.Exception.Message }
foreach ($path in 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeviceGuard','HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard','HKLM:\SYSTEM\CurrentControlSet\Control\Lsa','HKLM:\SYSTEM\CurrentControlSet\Control\DeviceGuard\Scenarios\HypervisorEnforcedCodeIntegrity') {
  try {
    $k = Get-Item -LiteralPath $path -ErrorAction Stop
    "== $path"
    foreach ($name in 'LsaCfgFlags','EnableVirtualizationBasedSecurity','RequirePlatformSecurityFeatures','Enabled','Locked') {
      if ($k.GetValueNames() -contains $name) { "[INFO] $name=$($k.GetValue($name))" } else { "[INFO] $name not configured here" }
    }
  } catch { Chk $null $path $_.Exception.Message }
}
'[INFO] LsaCfgFlags: 0 disabled, 1 enabled with UEFI lock, 2 enabled without lock. Missing values can inherit defaults; policy values alone do not prove runtime protection.'""",
      tags=('diagnostic', 'security', 'compliance', 'health')),
    S("client_applocker_local_effective_diff", "AppLocker local versus effective rules",
      "Compares rule IDs, content and enforcement modes between local configuration and effective merged policy.",
      need_admin("effective and local AppLocker policy reads require elevation") + CHK + r"""try {
  [xml]$local = Get-AppLockerPolicy -Local -Xml -ErrorAction Stop
  [xml]$effective = Get-AppLockerPolicy -Effective -Xml -ErrorAction Stop
  if (-not $local.AppLockerPolicy -or -not $effective.AppLockerPolicy) { Chk $null 'AppLocker comparison' 'one policy document is empty or invalid'; return }
  foreach ($type in 'Exe','Msi','Script','Dll','Appx') {
    $l = $local.AppLockerPolicy.RuleCollection | Where-Object Type -eq $type
    $e = $effective.AppLockerPolicy.RuleCollection | Where-Object Type -eq $type
    $lr = @($l.ChildNodes | Where-Object { $_.LocalName -like '*Rule' }); $er = @($e.ChildNodes | Where-Object { $_.LocalName -like '*Rule' })
    Chk $null $type "local mode=$($l.EnforcementMode), rules=$($lr.Count); effective mode=$($e.EnforcementMode), rules=$($er.Count); absent collections are unconfigured"
    foreach ($rule in $er) {
      $match = $lr | Where-Object Id -eq $rule.Id
      if (-not $match -or $match.OuterXml -ne $rule.OuterXml) { "[INFO] Effective added/changed rule: $($rule.OuterXml)" }
    }
    foreach ($rule in $lr) { if ($er.Id -notcontains $rule.Id) { "[INFO] Local rule absent effectively: $($rule.OuterXml)" } }
  }
  '[INFO] Local is the configured local policy; effective policy also merges domain policy. MDM application-control policy may require separate assessment.'
} catch { Chk $null 'AppLocker policy comparison' $_.Exception.Message }""",
      admin=True, tags=('diagnostic', 'security', 'compliance')),
    S("client_process_token_privileges", "Running process token privileges",
      "Reads primary-token privilege names and enabled flags; protected-process failures stay visible.",
      need_admin("other process token queries require elevation; protected processes can still refuse") + r"""try {
  Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
public static class WctTokenRead {
  [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint a, bool b, uint id);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
  [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr p,uint a,out IntPtr t);
  [DllImport("advapi32.dll", SetLastError=true)] static extern bool GetTokenInformation(IntPtr t,int c,IntPtr b,int n,out int needed);
  [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool LookupPrivilegeName(string s,IntPtr luid,StringBuilder name,ref int n);
  public static string[] Read(uint id) {
    IntPtr p=OpenProcess(0x1000,false,id), t=IntPtr.Zero, b=IntPtr.Zero;
    if(p==IntPtr.Zero) throw new Win32Exception();
    try {
      if(!OpenProcessToken(p,8,out t)) throw new Win32Exception();
      int n; GetTokenInformation(t,3,IntPtr.Zero,0,out n);
      if(n<=0) throw new Win32Exception();
      b=Marshal.AllocHGlobal(n);
      if(!GetTokenInformation(t,3,b,n,out n)) throw new Win32Exception();
      int count=Marshal.ReadInt32(b); var rows=new string[count];
      for(int i=0;i<count;i++) {
        IntPtr luid=IntPtr.Add(b,4+i*12); int len=256; var name=new StringBuilder(len);
        if(!LookupPrivilegeName(null,luid,name,ref len)) throw new Win32Exception();
        int flags=Marshal.ReadInt32(luid,8);
        rows[i]=name+" enabled="+((flags&2)!=0)+" attributes=0x"+flags.ToString("X");
      }
      return rows;
    } finally { if(b!=IntPtr.Zero) Marshal.FreeHGlobal(b); if(t!=IntPtr.Zero) CloseHandle(t); CloseHandle(p); }
  }
}
'@
  $processes = @(Get-Process -ErrorAction Stop)
  if (-not $processes.Count) { 'NOT AVAILABLE: process inventory empty.'; return }
  foreach ($p in $processes | Sort-Object Id) {
    try {
      $rows = @([WctTokenRead]::Read([uint32]$p.Id))
      "== $($p.ProcessName) PID=$($p.Id)"
      if (-not $rows.Count) { '[INFO] Token contains no privileges.' } else { $rows }
    } catch { "NOT AVAILABLE: $($p.ProcessName) PID=$($p.Id): $($_.Exception.Message)" }
  }
  '[INFO] Primary tokens only, not thread impersonation tokens; snapshots race with process exit. No privilege was enabled.'
} catch { "ERROR: Token inspection unavailable: $($_.Exception.Message)" }""",
      admin=True, tags=('diagnostic', 'security', 'inventory')),
]

_REG = (r"function RegVal($path, $name) { (Get-ItemProperty -Path $path -Name $name -ErrorAction SilentlyContinue).$name }" + "\n")

SCRIPTS = [
    S("shared_laps_policy", "LAPS policy and recent processing",
      "Windows and legacy LAPS policy plus recent operational events; never reads managed passwords.",
      CHK + need_admin("LAPS policy and operational history may require elevation") + r"""foreach ($path in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\LAPS','HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd') {
  try {
    if (-not (Test-Path -LiteralPath $path -ErrorAction Stop)) { Chk $null 'LAPS policy' "$path absent; no policy in this location"; continue }
    $p = Get-ItemProperty -LiteralPath $path -ErrorAction Stop
    "[INFO] $path"
    $p | Select-Object BackupDirectory, AdministratorAccountName, PasswordAgeDays, PasswordLength, PasswordComplexity, ADPasswordEncryptionEnabled, AdmPwdEnabled | Format-List | Out-String -Width 220
    Chk $null 'LAPS backup' 'policy presence does not prove successful password rotation or directory backup'
  } catch { Chk $null 'LAPS policy' $_.Exception.Message }
}
try {
  Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-LAPS/Operational'; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 20 -ErrorAction Stop |
    Select-Object TimeCreated, Id, LevelDisplayName, Message | Format-List | Out-String -Width 220
} catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { '[INFO] No LAPS events retained in seven days.' }
  else { "NOT AVAILABLE: LAPS operational history: $($_.Exception.Message)" }
}""", admin=True, tags=('diagnostic', 'security', 'compliance')),
    S("shared_loaded_driver_signatures", "Loaded driver signature verification",
      "Checks backing files of running system drivers, including catalog signatures; unavailable paths stay unknown.",
      need_admin("loaded driver file verification needs complete driver visibility") + CHK + r"""try { $drivers = @(Get-CimInstance Win32_SystemDriver -Filter "State='Running'" -ErrorAction Stop) }
catch { Chk $null 'Loaded drivers' $_.Exception.Message; return }
foreach ($driver in $drivers) {
  $path = [Environment]::ExpandEnvironmentVariables([string]$driver.PathName).Trim('"')
  $path = $path -replace '^\\\?\?\\',''
  if ($path -like '\SystemRoot\*') { $path = $env:SystemRoot + $path.Substring(11) }
  elseif ($path -match '^(?i)System32\\') { $path = Join-Path $env:SystemRoot $path }
  if ($path -notmatch '^[A-Za-z]:\\') { Chk $null $driver.Name "unresolved driver path: $path"; continue }
  try {
    $signature = Get-AuthenticodeSignature -LiteralPath $path -ErrorAction Stop
    $ok = if ($signature.Status -eq 'Valid') { $true } elseif ($signature.Status -eq 'NotSigned') { $false } else { $null }
    Chk $ok $driver.Name "$($signature.Status); $path; signer=$($signature.SignerCertificate.Subject)"
  } catch { Chk $null $driver.Name $_.Exception.Message }
}
'[INFO] File signatures reflect files on disk, not an attestation of loaded kernel memory.'""",
      admin=True, timeout=300, tags=('diagnostic', 'security', 'hardware', 'health')),
    S("shared_firewall_any_all_profiles", "Inbound Any-address rules on every profile",
      "Effective enabled allow rules for all profiles and any remote address, including protocol Any and rule origin.",
      need_admin("effective firewall policy and associated filters need elevation") + CHK + r"""try {
  $rules = @(Get-NetFirewallRule -PolicyStore ActiveStore -Direction Inbound -Action Allow -Enabled True -ErrorAction Stop |
    Where-Object { [int]$_.Profile -in 0,2147483647 -or ([int]$_.Profile -band 7) -eq 7 })
  $rows = @(foreach ($rule in $rules) {
    $address = $rule | Get-NetFirewallAddressFilter -ErrorAction Stop
    if ($address.RemoteAddress -contains 'Any') {
      $port = $rule | Get-NetFirewallPortFilter -ErrorAction Stop
      $app = $rule | Get-NetFirewallApplicationFilter -ErrorAction Stop
      [pscustomobject]@{Rule=$rule.DisplayName; Protocol=$port.Protocol; Ports=($port.LocalPort -join ','); Program=$app.Program; Source=$rule.PolicyStoreSource; SourceType=$rule.PolicyStoreSourceType}
    }
  })
  Chk (-not $rows.Count) 'Broad inbound rules' "$($rows.Count) effective rules; review service, interface and IPsec restrictions before concluding reachability"
  $rows | Format-Table -AutoSize -Wrap | Out-String -Width 220
} catch { Chk $null 'Broad inbound rules' $_.Exception.Message }""",
      admin=True, timeout=240, tags=('diagnostic', 'security', 'network', 'health')),
    S("shared_scriptblock_evidence", "PowerShell script block evidence (full text)",
      "Newest 30 retained 4104 records with block IDs, chunk numbers, paths and full logged text.",
      need_admin("script block logs can contain privileged session details") + r"""try {
  $events = @(Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-PowerShell/Operational'; Id=4104; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 30 -ErrorAction Stop)
  '[INFO] Newest 30 records at most; blocks can span records outside this sample. Logged text may contain sensitive data.'
  foreach ($event in $events) {
    $data = @{}; ([xml]$event.ToXml()).Event.EventData.Data | ForEach-Object { $data[$_.Name] = $_.'#text' }
    "== $($event.TimeCreated) record=$($event.RecordId) block=$($data.ScriptBlockId) chunk=$($data.MessageNumber)/$($data.MessageTotal) path=$($data.Path)"
    $data.ScriptBlockText
  }
} catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { '[INFO] No 4104 records retained in seven days; this does not prove no scripts ran.' }
  else { "NOT AVAILABLE: script block history: $($_.Exception.Message)" }
}""", admin=True, tags=('diagnostic', 'security', 'triage')),
    S("shared_vbs_requirements", "VBS requirements and security service evidence",
      "Reports required versus available VBS security properties and configured versus running service IDs.",
      CHK + r"""try {
  $dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction Stop
  if (-not $dg) { Chk $null 'VBS evidence' 'provider returned no instance'; return }
  $dg | Select-Object VirtualizationBasedSecurityStatus, RequiredSecurityProperties, AvailableSecurityProperties, SecurityServicesConfigured, SecurityServicesRunning, CodeIntegrityPolicyEnforcementStatus, UsermodeCodeIntegrityPolicyEnforcementStatus | Format-List | Out-String -Width 220
  '[INFO] Security services: 1 Credential Guard; 2 HVCI. Other numeric IDs are retained as reported.'
  foreach ($id in @($dg.SecurityServicesConfigured | Where-Object { $_ -ne 0 })) {
    $running = if ($null -eq $dg.SecurityServicesRunning) { $null } else { $dg.SecurityServicesRunning -contains $id }
    Chk $running "Configured security service $id" 'compare configured intent with running state; a restart or unmet prerequisite may explain a mismatch'
  }
  Chk $null 'VBS property IDs' '1 hypervisor; 2 Secure Boot; 3 DMA protection; 4 secure memory overwrite; 5 NX; 6 SMM mitigations; 7 mode-based execution control'
} catch { Chk $null 'VBS evidence unavailable' $_.Exception.Message }""",
      tags=('diagnostic', 'security', 'health', 'hardware')),
    S("machine_cert_expiry_60d", "Machine certificates expiring within 60 days",
      "Checks LocalMachine personal certificates for expiry within 60 days, including already expired certificates.",
      CHK + r"""try {
  $now = Get-Date
  $certs = @(Get-ChildItem Cert:\LocalMachine\My -ErrorAction Stop)
  $due = @($certs | Where-Object { $_.NotAfter -le $now.AddDays(60) })
  Chk (-not $due.Count) 'Machine certificate expiry' "$($due.Count) of $($certs.Count) certificate(s) expired or due within 60 days"
  $due | Sort-Object NotAfter | Select-Object Subject, NotAfter, @{n='DaysLeft';e={[math]::Floor(($_.NotAfter-$now).TotalDays)}}, HasPrivateKey, Thumbprint | Format-Table -AutoSize -Wrap | Out-String -Width 220
} catch { Chk $null 'Machine certificate expiry' $_.Exception.Message }""",
      tags=('security', 'health', 'inventory')),
    S("bitlocker_recovery_readiness", "BitLocker protection and recovery readiness",
      "Checks volume encryption, protection and recovery protector presence; remote recovery-key backup cannot be verified locally.",
      CHK + need_admin("BitLocker volume and key protector reads require elevation") + r"""try {
  $volumes = @(Get-BitLockerVolume -ErrorAction Stop)
  if (-not $volumes.Count) { Chk $null 'BitLocker' 'no volumes returned'; return }
  foreach ($v in $volumes) {
    Chk ($v.ProtectionStatus -eq 'On') "BitLocker $($v.MountPoint)" "status=$($v.VolumeStatus); protection=$($v.ProtectionStatus); encrypted=$($v.EncryptionPercentage)%; lock=$($v.LockStatus)"
    $recovery = @($v.KeyProtector | Where-Object { $_.KeyProtectorType -in 'RecoveryPassword','RecoveryKey' })
    if ($v.LockStatus -eq 'Locked' -or $null -eq $v.KeyProtector) {
      Chk $null "Recovery protector $($v.MountPoint)" 'volume locked or protector metadata unavailable'
    } else { Chk ([bool]$recovery.Count) "Recovery protector $($v.MountPoint)" "$($recovery.Count) recovery protector(s)" }
    $v.KeyProtector | Select-Object KeyProtectorType, KeyProtectorId | Format-Table -AutoSize | Out-String -Width 220
    Chk $null "Recovery key backup $($v.MountPoint)" 'not verified: confirm escrow in Entra ID, AD DS or your recovery-key records; protector presence is not proof of backup'
  }
} catch { Chk $null 'BitLocker' $_.Exception.Message }""",
      admin=True, tags=('security', 'storage', 'health')),
    S("execution_policy_scopes", "PowerShell execution policy by scope",
      "Checks execution policy precedence and reports all scopes; the Script Library process uses a Bypass launch override.",
      CHK + r"""try {
  Get-ExecutionPolicy -List -ErrorAction Stop | Format-Table -AutoSize | Out-String -Width 220
  $effective = Get-ExecutionPolicy -ErrorAction Stop
  Chk $null 'Effective policy in this process' "$effective; this runner requests Process=Bypass; execution policy is not a security boundary"
} catch { Chk $null 'Execution policies' $_.Exception.Message }""",
      tags=('security', 'diagnostic', 'baseline')),
    S("security_events_24h_summary", "Security event counts in the last 24 hours",
      "Checks retained failed logons (4625), lockouts (4740) and special-privilege logons (4672); privileged logons alone do not prove escalation.",
      CHK + need_admin("the Security log requires elevation") + r"""$since = (Get-Date).AddHours(-24)
foreach ($eventId in 4625,4740,4672) {
  try {
    $events = @(Get-WinEvent -FilterHashtable @{LogName='Security'; Id=$eventId; StartTime=$since} -MaxEvents 10001 -ErrorAction Stop)
    $count = $events.Count
    $label = switch ($eventId) { 4625 {'Failed logons'} 4740 {'Account lockouts'} 4672 {'Special privileges assigned at logon'} }
    $verdict = if ($eventId -eq 4672) { $null } else { $count -eq 0 }
    $detail = if ($count -gt 10000) { 'at least 10001; sample capped' } else { "$count retained event(s)" }
    Chk $verdict "$label ($eventId)" "$detail since $since"
    $events | Select-Object -First 5 TimeCreated, Id, Message | Format-Table -Wrap | Out-String -Width 220
  } catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') {
      Chk $null "Security event $eventId" 'no retained matches in 24h; audit policy and retention determine visibility'
    } else { Chk $null "Security event $eventId" $_.Exception.Message }
  }
}""", admin=True, tags=('security', 'triage', 'diagnostic')),
    S("applocker_effective_collections", "AppLocker effective rule collections",
      "Checks effective AppLocker collection enforcement modes, rule counts and Application Identity service status.",
      CHK + need_admin("effective AppLocker policy reads may require elevation") + r"""try {
  $policy = Get-AppLockerPolicy -Effective -Xml -ErrorAction Stop
  [xml]$xml = $policy
  $collections = @($xml.AppLockerPolicy.RuleCollection)
  if (-not $collections.Count) { Chk $null 'AppLocker' 'no effective rule collections returned' }
  foreach ($c in $collections) {
    $rules = @($c.ChildNodes | Where-Object { $_.LocalName -like '*Rule' })
    Chk $null "AppLocker $($c.Type)" "mode=$($c.EnforcementMode); rules=$($rules.Count); enforcement should match organizational policy"
  }
  $service = Get-Service AppIDSvc -ErrorAction Stop
  Chk $null 'Application Identity service' "status=$($service.Status); start type=$($service.StartType)"
} catch { Chk $null 'AppLocker policy' $_.Exception.Message }""",
      admin=True, tags=('security', 'compliance', 'inventory')),
    U("local_admin_sid_sources", "Local administrators: SIDs and account sources",
      "Checks direct Administrators membership by well-known group SID, with account source and built-in account classification; nested groups are not expanded.",
      CHK + r"""try {
  $members = @(Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop)
  Chk $null 'Direct local administrators' "$($members.Count) member(s); nested domain group membership is not expanded"
  $members | ForEach-Object {
    $sid = $_.SID.Value
    $origin = if ($sid -like 'S-1-5-32-*') { 'Built-in group' }
      elseif ($_.PrincipalSource -eq 'Local' -and $sid -match '-500$') { 'Built-in local Administrator' }
      elseif ($_.PrincipalSource) { [string]$_.PrincipalSource } else { 'Unknown / unresolved' }
    [pscustomobject]@{Name=$_.Name; SID=$sid; Type=$_.ObjectClass; Source=$origin}
  } | Format-Table -AutoSize -Wrap | Out-String -Width 220
} catch { Chk $null 'Local administrators' $_.Exception.Message }""",
      tags=('security', 'inventory', 'compliance')),
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
      note="the PowerShell 2.0, Secure Boot and BitLocker checks need elevation", admin=True,
      tags=('security', 'baseline', 'health')),
    S("defender_status", "Microsoft Defender status",
      "Real-time protection, tamper protection, signature age, last scans, cloud protection "
      "and whether Defender is in passive mode because another antivirus is installed.",
      CHK + r"""$d = Get-MpComputerStatus -ErrorAction Stop
if (-not $d) { Chk $null 'Defender real-time protection' 'status could not be read'; return }
Chk $d.RealTimeProtectionEnabled 'Defender real-time protection' "RealTimeProtectionEnabled=$($d.RealTimeProtectionEnabled); mode=$($d.AMRunningMode) (another antivirus may put Defender in passive mode)"
$d | Select-Object AMRunningMode, RealTimeProtectionEnabled, IsTamperProtected, AntivirusSignatureVersion, AntivirusSignatureLastUpdated, QuickScanEndTime, FullScanEndTime, BehaviorMonitorEnabled, IoavProtectionEnabled, NISEnabled | Format-List | Out-String -Width 220""",
      tags=('security', 'diagnostic', 'compliance', 'health')),
    S("defender_threats", "Defender detections history",
      "Threats Defender detected, when, where and what it did.",
      "$d = Get-MpThreatDetection -ErrorAction SilentlyContinue\n"
      "if (-not $d) { '[OK]   No threat detection recorded by Defender.'; return }\n"
      + T("$d | Sort-Object InitialDetectionTime -Descending | Select-Object InitialDetectionTime, ThreatID, @{n='Resources';e={$_.Resources -join '; '}}, ActionSuccess, @{n='Process';e={$_.ProcessName}}"),
      note="detection history is only fully readable elevated", admin=True,
      tags=('security', 'diagnostic', 'compliance', 'health')),
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
      note="Defender redacts exclusions for a standard user", admin=True,
      tags=('security', 'diagnostic', 'compliance', 'health')),
    S("asr_rules", "Attack Surface Reduction rules",
      "Which ASR rules are configured and in what mode (Block / Audit / Warn).",
      r"""$p = Get-MpPreference -ErrorAction SilentlyContinue
$ids = @($p.AttackSurfaceReductionRules_Ids); $acts = @($p.AttackSurfaceReductionRules_Actions)
if (-not $ids -or -not $ids[0]) { '[INFO] No ASR rule is configured (Windows default: all off).'; return }
$m = @{0='Disabled';1='Block';2='Audit';5='Not configured';6='Warn'}
for ($i = 0; $i -lt $ids.Count; $i++) { $a = [int]$acts[$i]; '{0} {1}  {2}' -f $(if ($a -eq 1) {'[OK]  '} elseif ($a -in 2,6) {'[INFO]'} else {'[WARN]'}), $ids[$i], $m[$a] }""",
      tags=('security', 'diagnostic', 'health')),
    S("firewall_rules_count", "Firewall rules summary",
      "How many rules exist per direction, action and profile, and how many are enabled.",
      CHK + r"""$profiles = @(Get-NetFirewallProfile -ErrorAction Stop)
if ($profiles.Count) { Chk (-not @($profiles | Where-Object { -not $_.Enabled }).Count) 'Firewall on for every profile' (($profiles | ForEach-Object { "$($_.Name)=$($_.Enabled)" }) -join '; ') } else { Chk $null 'Firewall profiles' 'no profiles returned' }
""" + T("Get-NetFirewallRule -ErrorAction SilentlyContinue | Group-Object Direction, Action, Enabled | Sort-Object Name | Select-Object Count, Name"),
      tags=('security', 'diagnostic', 'compliance', 'health')),
    S("certs_untrusted_roots", "Non-Microsoft trusted root certificates",
      "Root certificates in the machine and user Trusted Root stores, minus the ones Windows "
      "ships with by thumbprint-independent issuer check. A root added by adware, a proxy or "
      "a 'security' product can intercept HTTPS.",
      T(r"""'Cert:\LocalMachine\Root','Cert:\CurrentUser\Root' | ForEach-Object { $s = $_; Get-ChildItem $s -ErrorAction SilentlyContinue | Where-Object { $_.Subject -notmatch 'Microsoft|VeriSign|DigiCert|GlobalSign|Entrust|Thawte|GeoTrust|Comodo|Sectigo|USERTrust|ISRG|Baltimore|QuoVadis|Starfield|Go Daddy|Amazon|Certum|IdenTrust|AAA Certificate|Symantec|Network Solutions|SECOM|Buypass|T-TeleSec|Actalis|SSL.com|Hongkong Post|Certainly|Atos|D-TRUST|Izenpe|emSign|HARICA|TWCA|Trustwave|SwissSign|Starfield|Equifax|Class 3 Public|Copyright \(c\) 1997' } |
  ForEach-Object { [pscustomobject]@{ Store=$s; Subject=$_.Subject; Expires=$_.NotAfter; Thumbprint=$_.Thumbprint } } }"""),
      tags=('security', 'diagnostic')),
    S("code_integrity_blocks", "Blocked or unsigned drivers and binaries",
      "Code Integrity events: drivers or files blocked because they were unsigned, revoked or "
      "on Microsoft's vulnerable-driver blocklist, and Smart App Control blocks.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational'; Level=2,3} -MaxEvents 60 -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No code integrity blocks or warnings.'; return }
$e | ForEach-Object { '{0} {1:yyyy-MM-dd HH:mm}  {2,5}  {3}' -f $(if ($_.Level -eq 2) {'[BAD] '} else {'[WARN]'}), $_.TimeCreated, $_.Id, (($_.Message -split "`r?`n")[0]) }""",
      tags=('security', 'diagnostic', 'health')),
    S("smart_app_control", "Smart App Control and Windows Defender Application Control",
      "Smart App Control state (on / evaluation / off) and whether any WDAC policy is active.",
      r"""$s = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' -ErrorAction SilentlyContinue).VerifiedAndReputablePolicyState
"Smart App Control: $(switch ($s) { 0 {'off'} 1 {'ON (enforced)'} 2 {'evaluation'} default {'not reported'} })"
Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction SilentlyContinue | ForEach-Object { "Code integrity policy enforcement: $($_.CodeIntegrityPolicyEnforcementStatus) (0 off, 1 audit, 2 enforced)"; "User-mode CI: $($_.UsermodeCodeIntegrityPolicyEnforcementStatus)" }""",
      tags=('security', 'diagnostic')),
    S("uac_settings", "UAC settings in detail",
      "Every User Account Control value with what it means: admin and standard-user prompt "
      "behaviour, secure desktop, admin approval mode for the built-in Administrator.",
      CHK + _REG + r"""$k = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
$uac = Get-ItemProperty $k -ErrorAction Stop
Chk $(if ($null -eq $uac.EnableLUA) { $null } else { $uac.EnableLUA -eq 1 }) 'UAC enabled' "EnableLUA=$($uac.EnableLUA)"
Chk $(if ($null -eq $uac.ConsentPromptBehaviorAdmin) { $null } else { $uac.ConsentPromptBehaviorAdmin -in 1,2,3,4,5 }) 'UAC prompts administrators' "ConsentPromptBehaviorAdmin=$($uac.ConsentPromptBehaviorAdmin)"
Chk $(if ($null -eq $uac.PromptOnSecureDesktop) { $null } else { $uac.PromptOnSecureDesktop -eq 1 }) 'UAC secure desktop' "PromptOnSecureDesktop=$($uac.PromptOnSecureDesktop)"
$admin = @{0='elevate without prompting';1='credentials on secure desktop';2='consent on secure desktop';3='credentials';4='consent';5='consent for non-Windows binaries (default)'}
$user = @{0='deny';1='credentials on secure desktop';3='credentials (default)'}
"EnableLUA                  : $(RegVal $k EnableLUA)  (1 = UAC on)"
"ConsentPromptBehaviorAdmin : $(RegVal $k ConsentPromptBehaviorAdmin)  -> $($admin[[int](RegVal $k ConsentPromptBehaviorAdmin)])"
"ConsentPromptBehaviorUser  : $(RegVal $k ConsentPromptBehaviorUser)  -> $($user[[int](RegVal $k ConsentPromptBehaviorUser)])"
"PromptOnSecureDesktop      : $(RegVal $k PromptOnSecureDesktop)"
"FilterAdministratorToken   : $(RegVal $k FilterAdministratorToken)  (1 = built-in Administrator also gets UAC)"
"LocalAccountTokenFilterPolicy: $(RegVal $k LocalAccountTokenFilterPolicy)  (1 = remote admin by local accounts, pass-the-hash risk)" """,
      tags=('security', 'diagnostic', 'health')),
    S("credential_manager", "Saved credentials (names only)",
      "What Windows Credential Manager holds for this user -- target names and types, never "
      "the secrets. Old saved credentials cause lockouts after a password change.",
      "cmdkey /list", kind=CMD,
      tags=('security', 'diagnostic')),
    S("ssh_keys", "SSH keys and known hosts",
      "Public keys and the known_hosts list in your .ssh folder, and authorized_keys if this "
      "PC accepts SSH logins -- private key FILE NAMES only, never their contents.",
      r"""$d = Join-Path $env:USERPROFILE '.ssh'
if (-not (Test-Path $d)) { '[INFO] No .ssh folder.'; return }
Get-ChildItem $d -File | ForEach-Object { '{0,-30} {1,8} bytes  {2}' -f $_.Name, $_.Length, $_.LastWriteTime }
''
if (Test-Path "$d\known_hosts") { "known_hosts: $((Get-Content "$d\known_hosts" | Where-Object { $_.Trim() }).Count) host keys" }
foreach ($a in "$d\authorized_keys", "$env:ProgramData\ssh\administrators_authorized_keys") { if (Test-Path $a) { "[WARN] $a exists -- keys in it can log in to this PC" } }""",
      tags=('security', 'diagnostic', 'health')),
    S("autologon", "Automatic logon configured?",
      "Whether Windows signs in automatically, and if a plaintext DefaultPassword is stored "
      "in the registry (a serious finding).",
      CHK + _REG + r"""$k = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$on = (RegVal $k AutoAdminLogon) -eq '1'
Chk (-not $on) 'Automatic logon' "AutoAdminLogon=$(RegVal $k AutoAdminLogon) DefaultUserName=$(RegVal $k DefaultUserName)"
Chk ($null -eq (RegVal $k DefaultPassword)) 'No plaintext DefaultPassword' $(if ($null -ne (RegVal $k DefaultPassword)) { 'a password IS stored in clear text' } else { 'not present' })""",
      tags=('security', 'diagnostic', 'health')),
    S("lsa_packages", "LSA authentication and notification packages",
      "DLLs the Local Security Authority loads: authentication, security support providers "
      "and password notification packages. An unknown entry sees every password -- a known "
      "persistence trick.",
      _REG + r"""$k = 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa'
foreach ($n in 'Authentication Packages','Notification Packages','Security Packages') { '{0,-26} {1}' -f $n, ((RegVal $k $n) -join ', ') }
'{0,-26} {1}' -f 'OSConfig Security Packages', ((RegVal "$k\OSConfig" 'Security Packages') -join ', ')""",
      tags=('security', 'diagnostic')),
    S("screen_lock", "Screen lock and screensaver",
      "Whether the screen locks on its own: screensaver timeout and password, and the "
      "machine inactivity limit policy.",
      _REG + r"""$d = 'HKCU:\Control Panel\Desktop'
"Screensaver active      : $(RegVal $d ScreenSaveActive)"
"Screensaver timeout (s) : $(RegVal $d ScreenSaveTimeOut)"
"Password on resume      : $(RegVal $d ScreenSaverIsSecure)"
"InactivityTimeoutSecs   : $(RegVal 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' InactivityTimeoutSecs) (policy; empty = not set)"
powercfg /query SCHEME_CURRENT SUB_VIDEO VIDEOIDLE | Select-String 'Current (AC|DC)' | ForEach-Object { 'Display off ' + $_.Line.Trim() }""",
      tags=('security', 'diagnostic')),
    S("tls_protocols", "TLS/SSL protocols enabled",
      "SCHANNEL protocol settings: whether SSL 3.0, TLS 1.0 and 1.1 are explicitly disabled "
      "and TLS 1.2/1.3 enabled. Absent keys mean the OS default.",
      r"""foreach ($p in 'SSL 2.0','SSL 3.0','TLS 1.0','TLS 1.1','TLS 1.2','TLS 1.3') { foreach ($side in 'Client','Server') {
  $k = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\$p\$side"
  $e = (Get-ItemProperty $k -ErrorAction SilentlyContinue).Enabled
  $state = if ($null -eq $e) { 'OS default' } elseif ($e -eq 0) { 'DISABLED' } else { 'enabled' }
  $tag = if ($p -match 'SSL|1\.0|1\.1') { if ($state -eq 'DISABLED') {'[OK]  '} else {'[INFO]'} } else { if ($state -eq 'DISABLED') {'[WARN]'} else {'[OK]  '} }
  '{0} {1,-8} {2,-6} {3}' -f $tag, $p, $side, $state } }""",
      tags=('security', 'diagnostic', 'health')),
    S("unsigned_running", "Running programs without a valid signature",
      "Every running process whose executable is not validly Authenticode-signed (catalog "
      "signatures included), with its path. Most are harmless tools; unexpected ones in "
      "AppData or Temp are not.",
      r"""Get-Process | Where-Object Path | Select-Object -ExpandProperty Path -Unique | ForEach-Object {
  $s = Get-AuthenticodeSignature $_ -ErrorAction SilentlyContinue
  if ($s.Status -ne 'Valid') { $tag = if ($_ -match '\\(AppData|Temp|Users\\Public|ProgramData)\\') {'[WARN]'} else {'[INFO]'}; '{0} {1,-14} {2}' -f $tag, $s.Status, $_ } }""",
      note="other users' and system processes are not listed", timeout=240,
      tags=('security', 'diagnostic', 'health')),
    S("open_shares_perms", "Share permissions",
      "Who has access to each non-administrative share, from the share ACL. 'Everyone: Full' "
      "on a share is the finding.",
      r"""$s = Get-SmbShare -ErrorAction SilentlyContinue | Where-Object { -not $_.Special }
if (-not $s) { '[OK]   No shares besides the administrative ones.'; return }
$s | ForEach-Object { $n = $_.Name; Get-SmbShareAccess -Name $n -ErrorAction SilentlyContinue | ForEach-Object { '{0} {1,-20} {2,-30} {3} {4}' -f $(if ($_.AccountName -match 'Everyone' -and $_.AccessRight -eq 'Full') {'[WARN]'} else {'[INFO]'}), $n, $_.AccountName, $_.AccessControlType, $_.AccessRight } }""",
      tags=('security', 'diagnostic', 'health')),
    S("audit_policy_all", "Audit policy (all subcategories)",
      "What Windows records in the Security log, subcategory by subcategory.",
      need_admin("auditpol refuses a standard user") + "auditpol /get /category:*", admin=True,
      tags=('security', 'diagnostic', 'compliance')),
    S("browser_policies", "Browser policies in force",
      "Group Policy / registry policies applied to Edge and Chrome -- managed settings, "
      "forced extensions, homepage locks. Unexpected policies here are how adware hijacks a "
      "browser.",
      r"""foreach ($k in 'HKLM:\SOFTWARE\Policies\Microsoft\Edge','HKCU:\SOFTWARE\Policies\Microsoft\Edge','HKLM:\SOFTWARE\Policies\Google\Chrome','HKCU:\SOFTWARE\Policies\Google\Chrome') {
  if (Test-Path $k) { "== $k"; (Get-ItemProperty $k).PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { "[INFO] $($_.Name) = $($_.Value)" }
    Get-ChildItem $k -ErrorAction SilentlyContinue | ForEach-Object { "[WARN] subkey $($_.PSChildName): $(((Get-ItemProperty $_.PSPath).PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { $_.Value }) -join '; ')" } }
  else { "[OK]   $k -- no policy" } }""",
      tags=('security', 'diagnostic', 'health')),
    S("exploit_protection", "Exploit protection (system-wide)",
      "System-wide DEP, ASLR (bottom-up, high entropy, mandatory), CFG and SEHOP settings.",
      L("Get-ProcessMitigation -System | Select-Object @{n='DEP';e={$_.DEP.Enable}}, @{n='ASLR BottomUp';e={$_.ASLR.BottomUp}}, @{n='ASLR HighEntropy';e={$_.ASLR.HighEntropy}}, @{n='ASLR ForceRelocate';e={$_.ASLR.ForceRelocateImages}}, @{n='CFG';e={$_.CFG.Enable}}, @{n='SEHOP';e={$_.SEHOP.Enable}}"),
      tags=('security', 'diagnostic')),
    S("windows_hello", "Sign-in options (PIN, Hello, password)",
      "Which Windows Hello credential providers this user has set up and whether "
      "passwordless sign-in is required.",
      r"""$ngc = Test-Path "$env:windir\ServiceProfiles\LocalService\AppData\Local\Microsoft\Ngc" -ErrorAction SilentlyContinue
"Windows Hello container present: $ngc"
$p = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\PasswordLess\Device' -ErrorAction SilentlyContinue).DevicePasswordLessBuildVersion
"Require Windows Hello sign-in (passwordless): $(if ($p -eq 2) {'on'} elseif ($p -eq 0) {'off'} else {'not set'})"
Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers' -ErrorAction SilentlyContinue | ForEach-Object { (Get-ItemProperty $_.PSPath).'(default)' } | Where-Object { $_ } | Sort-Object | ForEach-Object { "provider: $_" }""",
      tags=('security', 'diagnostic')),
    S("rdp_brute_force", "Failed sign-in sources (brute force)",
      "Failed sign-ins (4625) in the last 7 days grouped by account and source address -- one "
      "address with hundreds of failures is password guessing.",
      need_admin("the Security log is only readable elevated") +
      T(r"""Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4625; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 5000 -ErrorAction SilentlyContinue | ForEach-Object {
  $d = @{}; ([xml]$_.ToXml()).Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }; [pscustomobject]@{ Account="$($d.TargetDomainName)\$($d.TargetUserName)"; Source=$d.IpAddress; Type=$d.LogonType } } |
  Group-Object Account, Source, Type | Sort-Object Count -Descending | Select-Object Count, Name"""),
      admin=True,
      tags=('security', 'diagnostic')),
    # ---- Users & groups ----------------------------------------------------------------------
    U("local_users", "Local user accounts",
      "Every local account: enabled, last logon, password last set and whether it expires "
      "or is required. An enabled account nobody uses is an open door.",
      CHK + r"""$users = @(Get-LocalUser -ErrorAction Stop)
$users | Select-Object Name, Enabled, LastLogon, PasswordLastSet, PasswordExpires, PasswordRequired, UserMayChangePassword, Description | Format-Table -AutoSize | Out-String -Width 220
# Service-account naming is only a heuristic: show candidates for review, never change policy.
$accounts = @(Get-CimInstance Win32_UserAccount -Filter 'LocalAccount=True' -ErrorAction Stop)
$serviceUsers = @(Get-CimInstance Win32_Service -ErrorAction Stop | ForEach-Object { if ($_.StartName -match '^(?:\.\\|[^\\]+\\)([^\\]+)$') { $Matches[1] } })
$review = @($accounts | Where-Object { -not $_.Disabled -and $_.PasswordExpires -eq $false -and $_.Name -notin $serviceUsers -and $_.Name -notmatch '^(svc[_-]|service[_-])' -and $_.SID -notmatch '-(501|503|504)$' })
Chk (-not $review.Count) 'Password expiry for enabled non-service accounts' "$($review.Count) account(s) with passwords that never expire; review against local policy (service names are heuristic)"
$review | ForEach-Object { "[WARN] $($_.Name) -- password never expires" }""",
      tags=('security', 'inventory', 'health')),
    U("local_groups", "Local groups and their members",
      "Every local group with its members -- Administrators, Remote Desktop Users, Hyper-V "
      "Administrators... membership in the wrong group is privilege.",
      r"""Get-LocalGroup | ForEach-Object { $g = $_; $m = @(Get-LocalGroupMember -Group $g -ErrorAction SilentlyContinue)
  if ($m.Count) { "== $($g.Name) ($($m.Count))"; $m | ForEach-Object { "   $($_.Name)  [$($_.ObjectClass), $($_.PrincipalSource)]" } } }""",
      tags=('security', 'inventory')),
    U("whoami_all", "Current user: groups and privileges",
      "This session's user, SID, group memberships and the privileges in its token -- what "
      "this process can actually do.",
      "whoami /all", kind=CMD,
      tags=('security', 'inventory')),
    U("profiles", "User profiles on this PC",
      "Every user profile folder with its SID, last use and whether it is loaded -- orphaned "
      "profiles of departed users take space.",
      T(r"""Get-CimInstance Win32_UserProfile | Where-Object { -not $_.Special } | ForEach-Object {
  $n = try { (New-Object Security.Principal.SecurityIdentifier($_.SID)).Translate([Security.Principal.NTAccount]).Value } catch { '(unresolvable -- deleted account?)' }
  [pscustomobject]@{ Account=$n; Path=$_.LocalPath; LastUse=$_.LastUseTime; Loaded=$_.Loaded; SID=$_.SID } }"""),
      tags=('security', 'inventory')),
    U("logged_on", "Who is signed in right now",
      "Interactive and remote sessions on this PC, with state and idle time.",
      "quser", kind=CMD,
      tags=('security', 'inventory')),     # `query user` exits 1 even when it succeeds (measured); quser exits 0
    U("password_policy", "Local password and lockout policy",
      "Minimum length, age, history and the account lockout threshold -- 'net accounts'.",
      "net accounts", kind=CMD,
      tags=('security', 'inventory', 'compliance')),
    U("admin_accounts_status", "Built-in Administrator and Guest",
      "Whether the built-in Administrator (RID 500) and Guest (RID 501) accounts are "
      "enabled, by SID -- they have their own names in other languages.",
      CHK + r"""Get-LocalUser | Where-Object { $_.SID.Value -match '-50[01]$' } | ForEach-Object { Chk (-not $_.Enabled) "$($_.Name) ($($_.SID.Value.Split('-')[-1]))" "Enabled=$($_.Enabled) LastLogon=$($_.LastLogon)" }""",
      tags=('security', 'inventory', 'health')),
    U("microsoft_accounts", "Microsoft and work accounts on this PC",
      "Accounts signed in to Windows itself: Microsoft accounts, Entra ID (work/school) "
      "joins and registrations -- dsregcmd's summary.",
      r"""dsregcmd /status | Select-String 'AzureAdJoined|EnterpriseJoined|DomainJoined|WorkplaceJoined|TenantName|DeviceId|AzureAdPrt |WamDefaultSet' | ForEach-Object { $_.Line.Trim() }""",
      tags=('security', 'inventory')),
    # ---- Remote access -----------------------------------------------------------------------
    R("rdp_status", "Remote Desktop status",
      "Whether Remote Desktop is enabled, its port, NLA requirement, who may connect, and "
      "whether the firewall lets it in.",
      CHK + _REG + r"""$on = (RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' fDenyTSConnections) -eq 0
Chk $null 'Remote Desktop' $(if ($on) {'ENABLED'} else {'disabled'})
"[INFO] Port -- $(RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' PortNumber)"
if ($on) { Chk ((RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' UserAuthentication) -eq 1) 'Network Level Authentication' 'required for RDP' }
Get-LocalGroupMember -SID S-1-5-32-555 -ErrorAction SilentlyContinue | ForEach-Object { "[INFO] Remote Desktop Users -- $($_.Name)" }
Get-NetFirewallRule -DisplayGroup 'Remote Desktop' -ErrorAction SilentlyContinue | Where-Object Enabled -eq 'True' | ForEach-Object { "[INFO] firewall rule on -- $($_.DisplayName) ($($_.Profile))" }""",
      tags=('security', 'network', 'diagnostic', 'health')),
    R("winrm_status", "WinRM / PowerShell Remoting",
      "Whether the WinRM service runs, its listeners and TrustedHosts -- 'Enter-PSSession "
      "does not work' and 'who can remote into this PC'.",
      r"""$s = Get-Service WinRM -ErrorAction SilentlyContinue
"WinRM service: $($s.Status), start type $($s.StartType)"
if ($s.Status -eq 'Running') { winrm enumerate winrm/config/listener 2>&1; ''; "TrustedHosts: $((Get-Item WSMan:\localhost\Client\TrustedHosts -ErrorAction SilentlyContinue).Value)" }
else { '[INFO] Remoting is off -- nothing listens.' }""",
      note="listeners and TrustedHosts need elevation", admin=True,
      tags=('security', 'network', 'diagnostic')),
    R("ssh_server", "OpenSSH server",
      "Whether the OpenSSH Server capability and sshd service are installed and running, "
      "the port and the default shell.",
      r"""$s = Get-Service sshd -ErrorAction SilentlyContinue
if (-not $s) { '[INFO] OpenSSH Server is not installed.'; return }
"sshd: $($s.Status), start type $($s.StartType)"
$cfg = "$env:ProgramData\ssh\sshd_config"
if (Test-Path $cfg) { Get-Content $cfg | Where-Object { $_ -match '^\s*(Port|PasswordAuthentication|PermitRootLogin|PubkeyAuthentication|AllowUsers|AllowGroups)\b' } }
"DefaultShell: $((Get-ItemProperty 'HKLM:\SOFTWARE\OpenSSH' -ErrorAction SilentlyContinue).DefaultShell)" """,
      tags=('security', 'network', 'diagnostic')),
    R("remote_assistance", "Remote Assistance",
      "Whether Remote Assistance invitations are allowed and whether helpers may take control.",
      _REG + r"""$k = 'HKLM:\SYSTEM\CurrentControlSet\Control\Remote Assistance'
"fAllowToGetHelp      : $(RegVal $k fAllowToGetHelp) (1 = invitations allowed)"
"fAllowFullControl    : $(RegVal $k fAllowFullControl) (1 = helper can take control)" """,
      tags=('security', 'network', 'diagnostic')),
    R("remote_tools_installed", "Remote-control software installed",
      "TeamViewer, AnyDesk, RustDesk, Chrome Remote Desktop, ScreenConnect, Splashtop, VNC, "
      "Quick Assist... installed programs and running services -- the first thing to check "
      "after a 'tech support' scam call.",
      r"""$pat = 'TeamViewer|AnyDesk|RustDesk|Chrome Remote Desktop|ScreenConnect|ConnectWise|Splashtop|VNC|LogMeIn|GoTo|Remote Utilities|Supremo|Zoho Assist|DWService|NinjaOne|Atera|Kaseya|Action1|MeshCentral|Parsec'
$apps = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match $pat }
$svc = Get-CimInstance Win32_Service | Where-Object { $_.DisplayName -match $pat -or $_.PathName -match $pat }
if (-not $apps -and -not $svc) { '[OK]   No known remote-control software found.'; return }
$apps | ForEach-Object { "[WARN] installed: $($_.DisplayName) $($_.DisplayVersion)" }
$svc | ForEach-Object { "[WARN] service: $($_.DisplayName) -- $($_.State), $($_.StartMode)" }""",
      tags=('security', 'network', 'diagnostic', 'health')),
    R("rdp_history", "Remote Desktop connections made FROM this PC",
      "Servers this user connected to with mstsc, from its MRU list and saved server "
      "entries (with the remembered username hint).",
      r"""$k = 'HKCU:\Software\Microsoft\Terminal Server Client'
(Get-ItemProperty "$k\Default" -ErrorAction SilentlyContinue).PSObject.Properties | Where-Object Name -like 'MRU*' | ForEach-Object { "recent: $($_.Value)" }
Get-ChildItem "$k\Servers" -ErrorAction SilentlyContinue | ForEach-Object { "saved: $($_.PSChildName)  user hint: $((Get-ItemProperty $_.PSPath).UsernameHint)" }""",
      tags=('security', 'network', 'diagnostic')),
    R("rdp_incoming_history", "Remote Desktop sessions INTO this PC",
      "Remote Desktop logons, disconnects and reconnects recorded by the Terminal Services "
      "session manager (21-25) in the last 30 days, with the source address.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-TerminalServices-LocalSessionManager/Operational'; Id=21,22,23,24,25; StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No Remote Desktop sessions in the last 30 days.'; return }
$e | ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1}  {2}' -f $_.TimeCreated, $_.Id, (($_.Message -split "`r?`n") -join ' ' -replace '\s+',' ') }""",
      tags=('security', 'network', 'diagnostic', 'health')),
]

SCRIPTS += _PERSISTENCE_SCRIPTS

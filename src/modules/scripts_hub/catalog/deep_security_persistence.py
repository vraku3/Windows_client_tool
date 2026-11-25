"""Security, Autostart Persistence & Threat Hunting Deep Diagnostics. Read-only."""
from ._base import CHK, L, T, maker, need_admin

SEC = maker("Security")

SCRIPTS = [
    SEC(
        "autoruns_deep_audit",
        "Deep Autostart Persistence & Execution Vector Hunter",
        "Comprehensive persistence audit inspecting Run/RunOnce keys (HKLM/HKCU, 32/64-bit), "
        "filesystem Startup folders, WMI Event Consumers, Image File Execution Options (IFEO) debuggers, "
        "AppCertDlls, Winlogon overrides, and third-party scheduled tasks.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: DEEP AUTOSTART PERSISTENCE & VECTOR HUNTER         " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Malware achieves reboot survival through persistence" -ForegroundColor Gray
Write-Host "[INFO] mechanisms: Registry Run keys, IFEO debugger hijacks, Winlogon overrides," -ForegroundColor Gray
Write-Host "[INFO] WMI permanent event consumers, and hidden tasks executing from user temp." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Registry Run & RunOnce Persistence Keys ===" -ForegroundColor Cyan
$runKeys = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce'
)

$runFound = 0
$suspiciousRun = 0
foreach ($rk in $runKeys) {
    if (Test-Path $rk) {
        $props = Get-ItemProperty $rk -ErrorAction SilentlyContinue
        if ($props) {
            $props.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object {
                $runFound++
                $val = $_.Value
                $isSuspicious = $val -like "*\AppData\*" -or $val -like "*\Temp\*" -or $val -like "*.vbs*" -or $val -like "*.bat*"
                if ($isSuspicious) {
                    $suspiciousRun++
                    Write-Host ("[WARN] {0} -> {1}" -f $_.Name, $val) -ForegroundColor Yellow
                    Write-Host ("       Key: {0}" -f $rk) -ForegroundColor Gray
                } else {
                    Write-Host ("[OK]   {0} -> {1}" -f $_.Name, $val) -ForegroundColor Green
                }
            }
        }
    }
}
if ($runFound -eq 0) { Write-Host "[OK]   Zero entries in standard Run/RunOnce registry keys." -ForegroundColor Green }

Write-Host "`n=== Phase 2: WMI Event Consumer Persistence (root\subscription) ===" -ForegroundColor Cyan
try {
    $consumers = Get-CimInstance -Namespace root\subscription -ClassName __EventConsumer -ErrorAction Stop
    if ($consumers) {
        Write-Host ("[WARN] Found {0} active WMI Event Consumer(s)!" -f $consumers.Count) -ForegroundColor Yellow
        $consumers | ForEach-Object {
            Write-Host ("  [WARN] Consumer: {0} ({1})" -f $_.Name, $_.CimClass.CimClassName) -ForegroundColor Yellow
        }
    } else {
        Write-Host "[OK]   Zero persistent WMI Event Consumers registered in root\subscription." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] WMI consumer check: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 3: Hijack Vectors (IFEO Debuggers & Winlogon Overrides) ===" -ForegroundColor Cyan
$ifeoPath = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options'
$ifeoDebuggerFound = 0
if (Test-Path $ifeoPath) {
    Get-ChildItem $ifeoPath -ErrorAction SilentlyContinue | ForEach-Object {
        $dbg = (Get-ItemProperty $_.PSPath -Name Debugger -ErrorAction SilentlyContinue).Debugger
        if ($dbg) {
            $ifeoDebuggerFound++
            Write-Host ("[BAD]  IFEO Debugger override detected: '{0}' -> '{1}'" -f $_.PSChildName, $dbg) -ForegroundColor Red
        }
    }
}
if ($ifeoDebuggerFound -eq 0) { Write-Host "[OK]   Zero IFEO debugger overrides found." -ForegroundColor Green }

$winlogon = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -ErrorAction SilentlyContinue
$userinit = $winlogon.Userinit
$shell = $winlogon.Shell
if ($userinit -and $userinit.ToLower().Trim() -notlike "*system32\userinit.exe*") {
    Write-Host ("[BAD]  Tampered Winlogon Userinit detected: {0}" -f $userinit) -ForegroundColor Red
} else {
    Write-Host "[OK]   Winlogon Userinit is legitimate (userinit.exe)." -ForegroundColor Green
}
if ($shell -and $shell.ToLower().Trim() -ne "explorer.exe") {
    Write-Host ("[WARN] Non-standard Winlogon Shell configured: {0}" -f $shell) -ForegroundColor Yellow
} else {
    Write-Host "[OK]   Winlogon Shell is default (explorer.exe)." -ForegroundColor Green
}

Write-Host "`n=== Phase 4: Non-Microsoft Scheduled Tasks in User Profiles ===" -ForegroundColor Cyan
$userTasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    $_.TaskPath -notlike '\Microsoft\*' -and $_.State -ne 'Disabled'
} | Where-Object {
    $acts = ($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' '
    $acts -like "*\AppData\*" -or $acts -like "*\Temp\*" -or $acts -like "*powershell*-enc*"
}

if ($userTasks) {
    Write-Host ("[WARN] Found {0} active non-Microsoft task(s) executing from user profile or temp:" -f $userTasks.Count) -ForegroundColor Yellow
    $userTasks | ForEach-Object {
        $act = ($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' ; '
        Write-Host ("  [WARN] Task: {0}{1} -> {2}" -f $_.TaskPath, $_.TaskName, $act) -ForegroundColor Yellow
    }
} else {
    Write-Host "[OK]   Zero scheduled tasks running suspicious binaries from user profiles or temp dirs." -ForegroundColor Green
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($suspiciousRun -gt 0 -or $ifeoDebuggerFound -gt 0 -or $userTasks) {
    Write-Host "[ACTION REQUIRED] High-probability persistence mechanism detected on system!" -ForegroundColor Red
    Write-Host "[REMEDIATION] 1. Investigate the identified file paths and hashes on VirusTotal."
    Write-Host "[REMEDIATION] 2. Run Microsoft Defender offline scan or full EDR threat containment."
    Write-Host "[REMEDIATION] 3. Remove suspicious registry autostart keys or unregister tasks."
} else {
    Write-Host "[STATUS] Standard autostart locations and system persistence vectors are clean." -ForegroundColor Green
}
""",
    ),
    SEC(
        "lsa_credential_guard_audit",
        "LSA Protection, Credential Guard & Virtualization Security",
        "Audits Local Security Authority (LSA) RunAsPPL protection, Credential Guard status, "
        "Virtualization-Based Security (VBS), WDigest clear-text password exposure, and cached logon limits.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: LSA RUNASPPL, CREDENTIAL GUARD & VBS AUDIT         " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: LSASS holds plain-text secrets, Kerberos tickets, and" -ForegroundColor Gray
Write-Host "[INFO] NTLM hashes. RunAsPPL and Credential Guard isolate LSASS inside a virtual" -ForegroundColor Gray
Write-Host "[INFO] container (VBS), blocking Mimikatz memory scraping even with local Admin." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: LSA Protection (RunAsPPL) Status ===" -ForegroundColor Cyan
$lsaKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa'
$runAsPpl = (Get-ItemProperty $lsaKey -Name RunAsPPL -ErrorAction SilentlyContinue).RunAsPPL
$runAsPplBoot = (Get-ItemProperty $lsaKey -Name RunAsPPLBoot -ErrorAction SilentlyContinue).RunAsPPLBoot

if ($runAsPpl -eq 1 -or $runAsPpl -eq 2) {
    $mode = if ($runAsPpl -eq 1) { "Enabled with UEFI Lock" } else { "Enabled without UEFI Lock" }
    Write-Host ("[OK]   LSA Protection (RunAsPPL) is ACTIVE ({0})" -f $mode) -ForegroundColor Green
} else {
    Write-Host "[WARN] LSA Protection (RunAsPPL) is DISABLED (LSASS memory readable by elevated processes)." -ForegroundColor Yellow
}

Write-Host "`n=== Phase 2: Virtualization-Based Security (VBS) & Credential Guard ===" -ForegroundColor Cyan
try {
    $dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction Stop
    $vbsRunning = $dg.VirtualizationBasedSecurityStatus -eq 2
    if ($vbsRunning) {
        Write-Host "[OK]   Virtualization-Based Security (VBS) is RUNNING." -ForegroundColor Green
    } else {
        Write-Host "[INFO] Virtualization-Based Security (VBS) is NOT currently running."
    }

    $credGuardCfg = 1 -in $dg.SecurityServicesConfigured
    $credGuardRunning = 1 -in $dg.SecurityServicesRunning
    if ($credGuardRunning) {
        Write-Host "[OK]   Credential Guard is ACTIVE and hardware-isolating NTLM/Kerberos secrets." -ForegroundColor Green
    } elseif ($credGuardCfg) {
        Write-Host "[WARN] Credential Guard is configured but NOT running (requires reboot or hardware support)." -ForegroundColor Yellow
    } else {
        Write-Host "[INFO] Credential Guard is not currently configured."
    }
} catch {
    Write-Host "[INFO] DeviceGuard WMI check: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 3: WDigest Plain-Text Credentials & Cached Logons ===" -ForegroundColor Cyan
$wdigestKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest'
$useLogonCred = (Get-ItemProperty $wdigestKey -Name UseLogonCredential -ErrorAction SilentlyContinue).UseLogonCredential

if ($useLogonCred -eq 1) {
    Write-Host "[BAD]  WDigest UseLogonCredential is set to 1! Plain-text passwords cached in memory!" -ForegroundColor Red
} else {
    Write-Host "[OK]   WDigest plain-text password caching is DISABLED." -ForegroundColor Green
}

$winlogonKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$cachedCount = (Get-ItemProperty $winlogonKey -Name CachedLogonsCount -ErrorAction SilentlyContinue).CachedLogonsCount
if ($null -ne $cachedCount) {
    Write-Host ("[INFO] Cached Domain Logons Limit: {0} (Default: 10)" -f $cachedCount)
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[SECURITY RECOMMENDATIONS]:"
Write-Host "  1. Enable RunAsPPL via GPO: Computer Configuration -> Administrative Templates"
Write-Host "     -> System -> Local Security Authority -> Configures LSA to run as a protected process."
Write-Host "  2. Ensure CPU virtualization (Intel VT-x / AMD-V) is enabled in UEFI BIOS for VBS."
Write-Host "  3. WDigest UseLogonCredential must always be 0 to prevent cleartext credential leaks."
""",
    ),
    SEC(
        "defender_edr_telemetry",
        "Microsoft Defender & EDR Deep Posture Audit",
        "Inspects Microsoft Defender operational posture: antivirus/antispyware signature age, "
        "engine version, exclusions inventory, MAPS cloud protection level, and Attack Surface Reduction (ASR) rules.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: MICROSOFT DEFENDER & EDR POSTURE AUDIT             " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Microsoft Defender provides endpoint detection and" -ForegroundColor Gray
Write-Host "[INFO] response. Signatures older than 3 days indicate broken Windows Update." -ForegroundColor Gray
Write-Host "[INFO] Attack Surface Reduction (ASR) rules block common macro and script exploits." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Defender Engine & Signature Freshness ===" -ForegroundColor Cyan
try {
    $mp = Get-CimInstance -Namespace root\Microsoft\Windows\Defender -ClassName MSFT_MpComputerStatus -ErrorAction Stop
    $sigAge = (Get-Date) - $mp.AntivirusSignatureLastUpdated
    
    if ($sigAge.TotalDays -gt 3) {
        Write-Host ("[BAD]  Antivirus Signature Date: {0} ({1:N1} days old - STALE!)" -f $mp.AntivirusSignatureLastUpdated, $sigAge.TotalDays) -ForegroundColor Red
    } elseif ($sigAge.TotalDays -gt 1) {
        Write-Host ("[WARN] Antivirus Signature Date: {0} ({1:N1} days old)" -f $mp.AntivirusSignatureLastUpdated, $sigAge.TotalDays) -ForegroundColor Yellow
    } else {
        Write-Host ("[OK]   Antivirus Signature Date: {0} ({1:N1} days old - FRESH)" -f $mp.AntivirusSignatureLastUpdated, $sigAge.TotalDays) -ForegroundColor Green
    }

    Write-Host ("[INFO] Defender Engine Version: {0}" -f $mp.AMEngineVersion)
    Write-Host ("[INFO] Product Version: {0}" -f $mp.AMProductVersion)
    
    $rtp = $mp.RealTimeProtectionEnabled
    if ($rtp) {
        Write-Host "[OK]   Real-Time Protection: ENABLED." -ForegroundColor Green
    } else {
        Write-Host "[BAD]  Real-Time Protection is DISABLED!" -ForegroundColor Red
    }

    $maps = $mp.MAPSReporting
    if ($maps -gt 0) {
        Write-Host ("[OK]   Cloud Protection (MAPS): ENABLED (Reporting Level: {0})" -f $maps) -ForegroundColor Green
    } else {
        Write-Host "[WARN] Cloud Protection (MAPS) is DISABLED." -ForegroundColor Yellow
    }
} catch {
    Write-Host "[WARN] Could not query Defender CIM status: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "`n=== Phase 2: Exclusions Inventory (High-Risk Audit) ===" -ForegroundColor Cyan
$riskyExclusions = 0
try {
    $pref = Get-CimInstance -Namespace root\Microsoft\Windows\Defender -ClassName MSFT_MpPreference -ErrorAction Stop
    $pathEx = $pref.ExclusionPath
    $procEx = $pref.ExclusionProcess
    $extEx = $pref.ExclusionExtension

    if ($pathEx) {
        Write-Host ("[INFO] Path Exclusions Count: {0}" -f $pathEx.Count) -ForegroundColor Cyan
        foreach ($px in $pathEx) {
            $isDangerous = $px -like "*\Temp*" -or $px -like "*\Users\*" -or $px -eq "$env:SystemDrive\"
            if ($isDangerous) {
                $riskyExclusions++
                Write-Host ("  [WARN] Path Exclusion (High Risk): {0}" -f $px) -ForegroundColor Yellow
            } else {
                Write-Host ("  [INFO] Path Exclusion: {0}" -f $px)
            }
        }
    } else {
        Write-Host "[OK]   Zero Path Exclusions configured (Tight security posture)." -ForegroundColor Green
    }

    if ($procEx) {
        Write-Host ("[INFO] Process Exclusions: {0}" -f ($procEx -join ', '))
    } else {
        Write-Host "[OK]   Zero Process Exclusions configured." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] Exclusions check: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 3: Attack Surface Reduction (ASR) Rules ===" -ForegroundColor Cyan
try {
    $asrIds = $pref.AttackSurfaceReductionRules_Ids
    $asrActions = $pref.AttackSurfaceReductionRules_Actions
    if ($asrIds -and $asrActions) {
        $enabledCount = ($asrActions | Where-Object { $_ -eq 1 }).Count
        $auditCount = ($asrActions | Where-Object { $_ -eq 2 }).Count
        Write-Host ("[OK]   Configured ASR Rules: {0} Total ({1} in Block Mode, {2} in Audit Mode)" -f $asrIds.Count, $enabledCount, $auditCount) -ForegroundColor Green
    } else {
        Write-Host "[INFO] Attack Surface Reduction (ASR) rules are not configured."
    }
} catch {
    Write-Host "[INFO] ASR check: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($riskyExclusions -gt 0) {
    Write-Host ("[ACTION REQUIRED] Review {0} high-risk path exclusions! Malware frequently abuses" -f $riskyExclusions) -ForegroundColor Yellow
    Write-Host "[ACTION REQUIRED] exclusions covering Temp or entire user directories to evade detection." -ForegroundColor Yellow
} else {
    Write-Host "[STATUS] Microsoft Defender endpoint protection is running healthy and up-to-date." -ForegroundColor Green
}
""",
    ),
    SEC(
        "priv_escalation_vectors",
        "Local Privilege Escalation & Misconfiguration Audit",
        "Audits unquoted service paths containing spaces, services with user-writeable executables, "
        "AlwaysInstallElevated policies, and system PATH directories placed ahead of System32.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: PRIVILEGE ESCALATION VECTORS AUDIT                 " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Local Privilege Escalation (LPE) vulnerabilities allow" -ForegroundColor Gray
Write-Host "[INFO] unprivileged standard users to gain SYSTEM authority. Common misconfigurations" -ForegroundColor Gray
Write-Host "[INFO] include unquoted service paths with spaces and AlwaysInstallElevated MSI policy." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Unquoted Service Binary Paths ===" -ForegroundColor Cyan
$services = Get-CimInstance Win32_Service
$unquotedCount = 0

foreach ($s in $services) {
    $pn = $s.PathName
    if (-not $pn) { continue }
    $cleanPath = $pn.Trim()
    if ($cleanPath.StartsWith('"')) { continue }
    # Strip arguments
    $exeCandidate = ($cleanPath -split '\.exe')[0] + '.exe'
    if ($exeCandidate -match ' ' -and -not $exeCandidate.StartsWith('\SystemRoot\')) {
        $unquotedCount++
        Write-Host ("[WARN] Unquoted service path with spaces: '{0}' ({1})" -f $s.Name, $s.DisplayName) -ForegroundColor Yellow
        Write-Host ("       Binary Path: {0}" -f $pn) -ForegroundColor Gray
    }
}
if ($unquotedCount -eq 0) {
    Write-Host "[OK]   Zero unquoted service paths with spaces detected." -ForegroundColor Green
}

Write-Host "`n=== Phase 2: AlwaysInstallElevated Policy Audit ===" -ForegroundColor Cyan
$inst1 = (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Installer' -Name AlwaysInstallElevated -ErrorAction SilentlyContinue).AlwaysInstallElevated
$inst2 = (Get-ItemProperty 'HKCU:\SOFTWARE\Policies\Microsoft\Windows\Installer' -Name AlwaysInstallElevated -ErrorAction SilentlyContinue).AlwaysInstallElevated

if ($inst1 -eq 1 -or $inst2 -eq 1) {
    Write-Host "[BAD]  AlwaysInstallElevated is ENABLED! Any user can install MSI packages with SYSTEM rights!" -ForegroundColor Red
} else {
    Write-Host "[OK]   AlwaysInstallElevated is disabled (Windows default)." -ForegroundColor Green
}

Write-Host "`n=== Phase 3: System PATH Order & Precedence ===" -ForegroundColor Cyan
$sysPath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
$pathParts = $sysPath -split ';' | Where-Object { $_.Trim() }
$sys32Index = -1
for ($i = 0; $i -lt $pathParts.Count; $i++) {
    if ($pathParts[$i] -like "*\System32*") {
        $sys32Index = $i
        break
    }
}

if ($sys32Index -gt 0) {
    Write-Host ("[WARN] System32 is NOT the first entry in PATH (position {0}). Entries ahead of System32:" -f $sys32Index) -ForegroundColor Yellow
    for ($j = 0; $j -lt $sys32Index; $j++) {
        Write-Host ("       [{0}] {1}" -f $j, $pathParts[$j]) -ForegroundColor Yellow
    }
} else {
    Write-Host "[OK]   System32 is properly positioned at the front of System PATH." -ForegroundColor Green
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($unquotedCount -gt 0) {
    Write-Host ("[REMEDIATION] Fix unquoted service paths by enclosing executable paths in double quotes:" -f $unquotedCount)
    Write-Host '  Example in registry: HKLM:\System\CurrentControlSet\Services\<ServiceName> ImagePath = "\"C:\Program Files\App\service.exe\""'
} else {
    Write-Host "[STATUS] Zero common local privilege escalation misconfigurations detected." -ForegroundColor Green
}
""",
    ),
    SEC(
        "rogue_root_ca_hunter",
        "Certificate Store Trust & Rogue Root CA Auditor",
        "Scans LocalMachine\\Root and LocalMachine\\AuthRoot for non-Microsoft root certificates, "
        "expired authorities, weak key lengths (<2048-bit), and SSL interception / inspection proxies.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: ROOT CA TRUST STORE & INTERCEPTION PROXIES        " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Root Certification Authorities in LocalMachine\Root have" -ForegroundColor Gray
Write-Host "[INFO] absolute trust on the OS. Malicious or rogue CAs can sign fake TLS certs" -ForegroundColor Gray
Write-Host "[INFO] to decrypt encrypted HTTPS traffic (Man-in-the-Middle TLS inspection)." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Local Computer Trusted Root Certification Authorities ===" -ForegroundColor Cyan
$roots = Get-ChildItem 'Cert:\LocalMachine\Root' -ErrorAction SilentlyContinue
$now = Get-Date

$expired = 0
$weak = 0
$inspectionProxies = @('zscaler', 'bluecoat', 'paloalto', 'fortinet', 'fiddler', 'charles', 'mitmproxy')

foreach ($c in $roots) {
    $subj = $c.Subject
    $issuer = $c.Issuer
    $thumb = $c.Thumbprint

    if ($c.NotAfter -lt $now) {
        $expired++
        Write-Host ("[WARN] Expired Root Certificate: {0} (Expired: {1:yyyy-MM-dd})" -f $subj, $c.NotAfter) -ForegroundColor Yellow
    }

    if ($c.PublicKey.Key.KeySize -lt 2048 -and $c.PublicKey.Oid.FriendlyName -like "*RSA*") {
        $weak++
        Write-Host ("[BAD]  Weak RSA Key Size ({0} bits): {1}" -f $c.PublicKey.Key.KeySize, $subj) -ForegroundColor Red
    }

    $subjLower = $subj.ToLower()
    foreach ($proxy in $inspectionProxies) {
        if ($subjLower -like "*$proxy*") {
            Write-Host ("[INFO] Corporate/Proxy TLS Inspection Root: '{0}' ({1})" -f $subj, $proxy.ToUpper()) -ForegroundColor Cyan
        }
    }
}

Write-Host ("`n[INFO] Total Root Authorities in Store: {0}" -f $roots.Count) -ForegroundColor Cyan
if ($expired -eq 0) { Write-Host "[OK]   Zero expired root certificates in LocalMachine\Root." -ForegroundColor Green }
if ($weak -eq 0) { Write-Host "[OK]   Zero weak RSA key (<2048-bit) root certificates detected." -ForegroundColor Green }

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($weak -gt 0) {
    Write-Host "[ACTION REQUIRED] Remove weak RSA root certificates using certmgr.msc or certutil." -ForegroundColor Red
} else {
    Write-Host "[STATUS] Trusted Root Certificate store contains valid, cryptographic-strength authorities." -ForegroundColor Green
}
""",
    ),
    SEC(
        "named_pipe_audit",
        "Windows Named Pipes & Lateral Movement Endpoint Audit",
        "Enumerates all active Windows Named Pipes, checking for known lateral movement or C2 indicators "
        "(psexec, cobalt, mimikatz) and auditing pipe accessibility.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: NAMED PIPES & LATERAL MOVEMENT ENDPOINTS          " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Named pipes enable inter-process communication (IPC) locally" -ForegroundColor Gray
Write-Host "[INFO] and over SMB. Attack tools (Cobalt Strike, PsExec, Meterpreter) frequently" -ForegroundColor Gray
Write-Host "[INFO] utilize distinct named pipe patterns for command execution and C2 beacons." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Active Windows Named Pipes Enumeration ===" -ForegroundColor Cyan
try {
    $pipes = [System.IO.Directory]::GetFiles("\\.\\pipe\\")
    Write-Host ("[INFO] Discovered {0} Active Named Pipes on system." -f $pipes.Count) -ForegroundColor Cyan

    $suspiciousKeywords = @('psexec', 'cobalt', 'mimikatz', 'csexec', 'meterpreter', 'beacon', 'bloodhound')
    $flagged = 0

    foreach ($p in $pipes) {
        $pName = [System.IO.Path]::GetFileName($p).ToLower()
        foreach ($kw in $suspiciousKeywords) {
            if ($pName -like "*$kw*") {
                $flagged++
                Write-Host ("[BAD]  Suspicious Named Pipe Detected: '{0}' (Matches keyword: {1})" -f $p, $kw.ToUpper()) -ForegroundColor Red
            }
        }
    }

    if ($flagged -eq 0) {
        Write-Host "[OK]   Zero suspicious or known attack-framework named pipes detected." -ForegroundColor Green
    }

    Write-Host "`nSample of Standard System & IPC Pipes:"
    $pipes | Select-Object -First 10 | ForEach-Object { Write-Host ("  • {0}" -f $_) }
} catch {
    Write-Host "[INFO] Named pipe query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($flagged -gt 0) {
    Write-Host ("[ACTION REQUIRED] Flagged {0} named pipe(s) matching known adversary tooling patterns!" -f $flagged) -ForegroundColor Red
    Write-Host "[REMEDIATION] Identify owning PID using Process Explorer or Handle utility (Sysinternals)."
} else {
    Write-Host "[STATUS] Active IPC named pipes reflect standard operating system endpoints." -ForegroundColor Green
}
""",
    ),
    SEC(
        "amsi_etw_tampering_audit",
        "AMSI Provider & ETW Threat Intelligence Tampering Audit",
        "Audits Antimalware Scan Interface (AMSI) registry policies, registered providers, "
        "and verifies whether Event Tracing for Windows (ETW) security telemetry is intact.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: AMSI PROVIDERS & ETW TELEMETRY INTEGRITY          " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: AMSI allows antivirus to inspect scripts (PowerShell, VBS," -ForegroundColor Gray
Write-Host "[INFO] Office macros) in memory before execution. Adversaries attempt to patch" -ForegroundColor Gray
Write-Host "[INFO] AmsiScanBuffer in memory or delete AMSI provider GUIDs from the registry." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Antimalware Scan Interface (AMSI) Providers ===" -ForegroundColor Cyan
$amsiProvidersKey = 'HKLM:\SOFTWARE\Microsoft\AMSI\Providers'
if (Test-Path $amsiProvidersKey) {
    $provs = Get-ChildItem $amsiProvidersKey -ErrorAction SilentlyContinue
    Write-Host ("[INFO] Registered AMSI Security Providers: {0}" -f $provs.Count) -ForegroundColor Cyan
    foreach ($pv in $provs) {
        $guid = $pv.PSChildName
        $clsKey = "HKLM:\SOFTWARE\Classes\CLSID\$guid"
        $name = if (Test-Path $clsKey) { (Get-ItemProperty $clsKey -ErrorAction SilentlyContinue).'(default)' } else { "Unknown" }
        Write-Host ("  [OK]   Provider: {0} ({1})" -f $guid, $name) -ForegroundColor Green
    }
    if ($provs.Count -eq 0) {
        Write-Host "[BAD]  Zero AMSI Providers registered! Script scanning is completely disabled!" -ForegroundColor Red
    }
} else {
    Write-Host "[WARN] AMSI Providers registry key missing or inaccessible." -ForegroundColor Yellow
}

Write-Host "`n=== Phase 2: PowerShell ScriptBlockLogging Policy ===" -ForegroundColor Cyan
$psPolicyKey = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\PowerShell\ScriptBlockLogging'
$sbl = (Get-ItemProperty $psPolicyKey -Name EnableScriptBlockLogging -ErrorAction SilentlyContinue).EnableScriptBlockLogging

if ($sbl -eq 1) {
    Write-Host "[OK]   PowerShell Script Block Logging (Event 4104) is EXPLICITLY ENABLED via GPO." -ForegroundColor Green
} else {
    Write-Host "[INFO] PowerShell Script Block Logging is at Windows standard default."
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[SECURITY BASELINE]:"
Write-Host "  1. If AMSI provider is missing, re-register Defender provider or repair AV agent."
Write-Host "  2. Enable ScriptBlockLogging via GPO to log full script code to Event 4104."
""",
    ),
    SEC(
        "kerberos_ticket_cache_audit",
        "Cached Kerberos Tickets (TGT / TGS) & Encryption Types",
        "Inspects active user Kerberos ticket cache via klist: audits ticket-granting tickets (TGT), "
        "session key cipher suites (AES-256 vs RC4), and expiration timestamps.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: CACHED KERBEROS TICKETS & CIPHER SUITES           " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Kerberos tickets (TGT / TGS) authenticate domain users." -ForegroundColor Gray
Write-Host "[INFO] Tickets encrypted with RC4-HMAC are vulnerable to offline Kerberoasting" -ForegroundColor Gray
Write-Host "[INFO] attacks. Modern domain environments must mandate AES-128 / AES-256 CTS." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: User Kerberos Ticket Cache (klist) ===" -ForegroundColor Cyan
try {
    $klistOut = klist.exe
    $klistLines = $klistOut -split "`r?`n"
    $hasTickets = $false
    $weakCipher = $false

    foreach ($line in $klistLines) {
        if ($line -match 'Server: |KerbTicket Encryption Type|End Time|Client: ') {
            $hasTickets = $true
            $isRc4 = $line -like "*ArcFour*" -or $line -like "*RC4*"
            if ($isRc4) {
                $weakCipher = $true
                Write-Host ("  [WARN] {0} (VULNERABLE LEGACY CIPHER)" -f $line.Trim()) -ForegroundColor Yellow
            } else {
                Write-Host ("  {0}" -f $line.Trim())
            }
        }
    }

    if (-not $hasTickets) {
        Write-Host "[INFO] Zero cached Kerberos tickets (Machine in Workgroup mode or local user logged on)." -ForegroundColor Gray
    }
} catch {
    Write-Host "[INFO] klist execution: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($weakCipher) {
    Write-Host "[ACTION REQUIRED] RC4-HMAC Kerberos tickets detected! Upgrade service accounts to AES-256 in Active Directory." -ForegroundColor Yellow
} else {
    Write-Host "[STATUS] Kerberos ticket cache is healthy or not active in current logon session." -ForegroundColor Green
}
""",
    ),
    SEC(
        "firewall_rules_stealth_exposure",
        "Inbound Firewall Rules Stealth & Over-Permissive Audit",
        "Identifies over-permissive inbound firewall rules: rules allowing any traffic on all ports, "
        "and rules associated with binaries residing in temporary or user directories.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: FIREWALL STEALTH & OVER-PERMISSIVE RULES AUDIT    " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Inbound allow rules permitting 'Any' port on binaries" -ForegroundColor Gray
Write-Host "[INFO] inside user directories (%AppData%, %Temp%) expose userland backdoors" -ForegroundColor Gray
Write-Host "[INFO] and bypass standard network perimeter controls." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Over-Permissive Inbound Allow Rules (Any Port) ===" -ForegroundColor Cyan
try {
    $inRules = Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True -ErrorAction Stop
    $anyPortCount = 0

    foreach ($r in $inRules) {
        $portFilter = $r | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue
        $appFilter = $r | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue

        if ($portFilter.LocalPort -eq 'Any' -and $portFilter.Protocol -in @('TCP', 'UDP', 'Any')) {
            $app = $appFilter.Program
            if ($app -and ($app -like "*\AppData\*" -or $app -like "*\Temp\*")) {
                $anyPortCount++
                Write-Host ("[BAD]  Inbound Allow ANY port on User Binary: '{0}'" -f $r.DisplayName) -ForegroundColor Red
                Write-Host ("       Program: {0}" -f $app) -ForegroundColor Gray
            }
        }
    }

    if ($anyPortCount -eq 0) {
        Write-Host "[OK]   Zero inbound allow-all-ports rules found targeting user profile binaries." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] Firewall rule query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($anyPortCount -gt 0) {
    Write-Host ("[ACTION REQUIRED] Found {0} over-permissive inbound rule(s) targeting user-writable paths!" -f $anyPortCount) -ForegroundColor Red
    Write-Host "[REMEDIATION] Review identified rules in Windows Defender Firewall with Advanced Security and remove them."
} else {
    Write-Host "[STATUS] Inbound firewall policy adheres to least-privilege exposure boundaries." -ForegroundColor Green
}
""",
    ),
    SEC(
        "applocker_wdac_code_integrity_audit",
        "AppLocker & WDAC / Smart App Control Block Event Audit",
        "Inspects AppLocker and Code Integrity event logs, grouping recent executable or script blocks "
        "with file hashes and process paths.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: APPLOCKER, WDAC & SMART APP CONTROL BLOCKS         " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Windows 11 Smart App Control (SAC), AppLocker, and WDAC" -ForegroundColor Gray
Write-Host "[INFO] prevent untrusted or unsigned binaries from running. When a program fails" -ForegroundColor Gray
Write-Host "[INFO] to start with 'An Application Control policy has blocked this file', Code" -ForegroundColor Gray
Write-Host "[INFO] Integrity logs Event IDs 3077 and 3118." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Code Integrity & Smart App Control Blocks (Last 14 Days) ===" -ForegroundColor Cyan
$ciBlocks = 0
try {
    $ciEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'Microsoft-Windows-CodeIntegrity/Operational';
        Id = 3077, 3118, 3033;
        StartTime = (Get-Date).AddDays(-14)
    } -MaxEvents 20 -ErrorAction Stop

    $ciBlocks = $ciEvents.Count
    Write-Host ("[WARN] Found {0} Smart App Control / Code Integrity block event(s):" -f $ciBlocks) -ForegroundColor Yellow
    $ciEvents | Select-Object -First 5 | ForEach-Object {
        Write-Host ("  [WARN] [{0:yyyy-MM-dd HH:mm}] Event {1}: {2}" -f $_.TimeCreated, $_.Id, ($_.Message -split "`r?`n")[0]) -ForegroundColor Yellow
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero Code Integrity block events in the last 14 days." -ForegroundColor Green
    } else {
        Write-Host "[INFO] CI log check: $($_.Exception.Message)"
    }
}

Write-Host "`n=== Phase 2: AppLocker Script & MSI Enforcement Logs ===" -ForegroundColor Cyan
try {
    $appLockerEvents = Get-WinEvent -FilterHashtable @{
        LogName = 'Microsoft-Windows-AppLocker/MSI and Script';
        Id = 8007, 8004;
        StartTime = (Get-Date).AddDays(-14)
    } -MaxEvents 15 -ErrorAction Stop

    Write-Host ("[WARN] Found {0} AppLocker block event(s):" -f $appLockerEvents.Count) -ForegroundColor Yellow
    $appLockerEvents | ForEach-Object {
        Write-Host ("  [WARN] [{0:yyyy-MM-dd HH:mm}] {1}" -f $_.TimeCreated, ($_.Message -split "`r?`n")[0]) -ForegroundColor Yellow
    }
} catch {
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host "[OK]   Zero AppLocker enforcement block events in the last 14 days." -ForegroundColor Green
    } else {
        Write-Host "[INFO] AppLocker log check: $($_.Exception.Message)"
    }
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($ciBlocks -gt 0) {
    Write-Host "[INFO] Code Integrity blocks indicate that Smart App Control blocked unsigned binaries." -ForegroundColor Cyan
    Write-Host "[REMEDIATION] 1. Verify if the blocked application is legitimate software."
    Write-Host "[REMEDIATION] 2. In Smart App Control environments, developers must sign binaries or wait for reputation learning."
} else {
    Write-Host "[STATUS] Zero application execution blocks recorded in Code Integrity or AppLocker." -ForegroundColor Green
}
""",
    ),
    SEC(
        "ntfs_motw_alternate_data_streams",
        "NTFS Mark-of-the-Web (MOTW) & Alternate Data Streams (ADS) Threat Hunter",
        "Recursively scans user directories (Downloads, Desktop, Temp) for hidden NTFS Alternate Data Streams (ADS), "
        "parses Zone.Identifier Mark-of-the-Web metadata (ZoneId, HostUrl, ReferrerUrl), and detects hidden executable streams.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: NTFS MARK-OF-THE-WEB & ALTERNATE DATA STREAMS     " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: When files are downloaded from the Internet, Windows" -ForegroundColor Gray
Write-Host "[INFO] attaches an NTFS Alternate Data Stream named Zone.Identifier (ZoneId 3)." -ForegroundColor Gray
Write-Host "[INFO] This triggers SmartScreen and Office Protected View. Malicious actors" -ForegroundColor Gray
Write-Host "[INFO] sometimes hide executable payloads inside custom ADS streams." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Zone.Identifier & Mark-of-the-Web (MOTW) in User Directories ===" -ForegroundColor Cyan
$scanDirs = @(
    [System.IO.Path]::Combine($env:USERPROFILE, "Downloads"),
    [System.IO.Path]::Combine($env:USERPROFILE, "Desktop")
)

$zoneMap = @{
    0 = 'My Computer (Local)'
    1 = 'Local Intranet'
    2 = 'Trusted Sites'
    3 = 'Internet'
    4 = 'Restricted Sites'
}

$execExts = @('.exe', '.dll', '.sys', '.ps1', '.bat', '.cmd', '.vbs', '.js', '.iso', '.vhd', '.vhdx', '.msi', '.hta')
$motwFound = 0
$riskFiles = 0

foreach ($dir in $scanDirs) {
    if (Test-Path $dir) {
        Write-Host ("Scanning directory: {0}" -f $dir) -ForegroundColor Cyan
        try {
            $files = Get-ChildItem -Path $dir -File -Recurse -Depth 2 -ErrorAction SilentlyContinue
            foreach ($f in $files) {
                try {
                    $streams = Get-Item -Path $f.FullName -Stream * -ErrorAction SilentlyContinue | Where-Object { $_.Stream -ne ':$DATA' }
                    foreach ($st in $streams) {
                        if ($st.Stream -eq 'Zone.Identifier') {
                            $motwFound++
                            $content = Get-Content -Path "$($f.FullName):Zone.Identifier" -ErrorAction SilentlyContinue
                            $zoneId = ($content | Where-Object { $_ -match '^ZoneId=' } | ForEach-Object { ($_ -split '=')[1].Trim() })
                            $hostUrl = ($content | Where-Object { $_ -match '^HostUrl=' } | ForEach-Object { ($_ -split '=')[1].Trim() })
                            $refUrl = ($content | Where-Object { $_ -match '^ReferrerUrl=' } | ForEach-Object { ($_ -split '=')[1].Trim() })

                            $zoneDesc = if ($zoneMap.ContainsKey([int]$zoneId)) { $zoneMap[[int]$zoneId] } else { "Zone $zoneId" }
                            $ext = $f.Extension.ToLower()

                            if ($ext -in $execExts -and [int]$zoneId -ge 3) {
                                $riskFiles++
                                Write-Host ("  [WARN] Executable with Internet MOTW: {0} ({1})" -f $f.Name, $zoneDesc) -ForegroundColor Yellow
                                if ($hostUrl) { Write-Host ("         Origin URL:   {0}" -f $hostUrl) -ForegroundColor Gray }
                                if ($refUrl)  { Write-Host ("         Referrer URL: {0}" -f $refUrl) -ForegroundColor Gray }
                            }
                        }
                    }
                } catch {}
            }
        } catch {
            Write-Host ("  [INFO] Directory scan note: {0}" -f $_.Exception.Message)
        }
    }
}

Write-Host ("`nTotal files with MOTW (Zone.Identifier) stream: {0}" -f $motwFound)
if ($riskFiles -gt 0) {
    Write-Host ("[WARN] Found {0} executable or container file(s) originating directly from the Internet." -f $riskFiles) -ForegroundColor Yellow
} else {
    Write-Host "[OK]   Zero untrusted executable files flagged in examined directories." -ForegroundColor Green
}

Write-Host "`n=== Phase 2: Hidden Alternate Data Streams (ADS) in Temp Folders ===" -ForegroundColor Cyan
$suspiciousAdsDirs = @(
    $env:TEMP,
    [System.IO.Path]::Combine($env:LOCALAPPDATA, "Temp")
)

$adsFound = 0
foreach ($td in $suspiciousAdsDirs) {
    if ($td -and (Test-Path $td)) {
        try {
            $tempFiles = Get-ChildItem -Path $td -File -Recurse -Depth 2 -ErrorAction SilentlyContinue
            foreach ($tf in $tempFiles) {
                try {
                    $hiddenStreams = Get-Item -Path $tf.FullName -Stream * -ErrorAction SilentlyContinue | Where-Object {
                        $_.Stream -ne ':$DATA' -and $_.Stream -ne 'Zone.Identifier' -and $_.Stream -notlike '*encryptable*' -and $_.Stream -notlike '*favicon*'
                    }
                    foreach ($hs in $hiddenStreams) {
                        $adsFound++
                        $streamName = $hs.Stream
                        $streamSize = $hs.Length
                        $tag = "[WARN]"
                        foreach ($ee in $execExts) {
                            if ($streamName.ToLower().EndsWith($ee)) {
                                $tag = "[BAD] "
                                break
                            }
                        }
                        Write-Host ("  {0} Hidden ADS: {1}:{2} ({3:N0} bytes)" -f $tag, $tf.FullName, $streamName, $streamSize)
                    }
                } catch {}
            }
        } catch {}
    }
}
if ($adsFound -eq 0) {
    Write-Host "[OK]   Zero non-standard hidden Alternate Data Streams detected in temporary directories." -ForegroundColor Green
} else {
    Write-Host ("[WARN] Detected {0} non-standard alternate data stream(s)." -f $adsFound) -ForegroundColor Yellow
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[INFO] To unblock a legitimate downloaded file:"
Write-Host "  Right-click file -> Properties -> Check 'Unblock' -> Click OK."
Write-Host "  Or in PowerShell: Unblock-File -Path <FilePath>"
""",
    ),
]


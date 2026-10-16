"""Updates, Services, Startup, Applications, Power, Printing, Group Policy,
plus more Troubleshooting and Inventory checks. Read-only."""
from ._base import CHK, CMD, L, T, maker, need_admin

UP = maker("Updates & servicing")
SV = maker("Services & processes")
ST = maker("Startup & tasks")
AP = maker("Applications")
PW = maker("Power")
PR = maker("Printing")
GP = maker("Group Policy")
TS = maker("Troubleshooting")
IN = maker("Inventory")

#: Get-Printer & co. with -ErrorAction SilentlyContinue print NOTHING when the
#: spooler is stopped -- measured here, where it is Disabled -- which reads as
#: "no printers". Every print check states the spooler first.
_SPOOLER = ("$sp = Get-Service Spooler -ErrorAction SilentlyContinue\n"
            "if (-not $sp -or $sp.Status -ne 'Running') { \"[WARN] The Print Spooler service is "
            "$(if ($sp) { \"$($sp.Status) ($($sp.StartType))\" } else { 'missing' }) -- printers, "
            "drivers, ports and queues cannot be read while it is not running. (Disabling it is "
            "a deliberate PrintNightmare hardening step on PCs that never print.)\"; return }\n")

_UNINSTALL = ("'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
              "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
              "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'")

SCRIPTS = [
    # ---- Updates & servicing -----------------------------------------------------------------
    UP("wu_history", "Windows Update history (last 60)",
      "Every update the Windows Update agent installed or failed to install, with the result "
      "code -- the same history Settings shows, including failures Settings hides.",
      r"""$s = New-Object -ComObject Microsoft.Update.Session; $q = $s.CreateUpdateSearcher()
$n = $q.GetTotalHistoryCount(); if (-not $n) { 'No update history.'; return }
$q.QueryHistory(0, [Math]::Min(60, $n)) | Where-Object Title | ForEach-Object {
  $r = switch ($_.ResultCode) { 2 {'[OK]  '} 3 {'[WARN]'} 4 {'[BAD] '} 5 {'[WARN]'} default {'[INFO]'} }
  '{0} {1:yyyy-MM-dd HH:mm}  {2}{3}' -f $r, $_.Date, $_.Title, $(if ($_.HResult) { '  (0x{0:X8})' -f $_.HResult } else { '' }) }""",
      timeout=180),
    UP("wu_pending", "Updates waiting to install",
      "Updates Windows Update has found but not installed yet (software and drivers), with "
      "size and whether they need a restart. Read-only search -- nothing is downloaded.",
      r"""$s = New-Object -ComObject Microsoft.Update.Session; $r = $s.CreateUpdateSearcher().Search("IsInstalled=0 and IsHidden=0")
if (-not $r.Updates.Count) { '[OK]   No pending updates.'; return }
foreach ($u in $r.Updates) { '[INFO] {0}  ({1:N0} MB max{2})' -f $u.Title, ($u.MaxDownloadSize/1MB), $(if ($u.InstallationBehavior.RebootBehavior) {', may need a restart'} else {''}) }""",
      timeout=300),
    UP("wu_settings", "Windows Update configuration",
      "Policy settings that change how updates arrive: WSUS server, deferrals, target "
      "release, automatic update behaviour and paused updates.",
      r"""foreach ($k in 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate','HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU','HKLM:\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings') {
  "== $k"; if (Test-Path $k) { (Get-ItemProperty $k).PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { "   $($_.Name) = $($_.Value)" } } else { '   (not set -- Windows defaults)' } }"""),
    UP("wu_services", "Update services health",
      "State of every service updates depend on (wuauserv, BITS, cryptsvc, TrustedInstaller, "
      "UsoSvc, DoSvc). A disabled one is why updates 'stopped'.",
      CHK + r"""foreach ($n in 'wuauserv','BITS','cryptsvc','TrustedInstaller','UsoSvc','DoSvc','WaaSMedicSvc') { $s = Get-Service $n -ErrorAction SilentlyContinue
  if ($s) { Chk ($s.StartType -ne 'Disabled') $n "$($s.Status), start type $($s.StartType)" } else { Chk $null $n 'not present' } }"""),
    UP("delivery_optimization", "Delivery Optimization",
      "Download mode (peers on LAN / internet) and how much this PC downloaded from and "
      "uploaded to peers this month.",
      L("Get-DeliveryOptimizationStatus -ErrorAction SilentlyContinue | Measure-Object FileSize -Sum | Select-Object Count, Sum; Get-DeliveryOptimizationPerfSnapThisMonth -ErrorAction SilentlyContinue")),
    UP("servicing_stack", "Servicing stack and last cumulative update",
      "The servicing stack version and the most recent cumulative update package installed, "
      "from CBS -- for 'is this machine patched'.",
      need_admin("Get-WindowsPackage needs elevation") +
      T("Get-WindowsPackage -Online | Where-Object { $_.PackageName -match 'RollupFix|ServicingStack' -and $_.PackageState -eq 'Installed' } | Sort-Object InstallTime -Descending | Select-Object -First 8 PackageName, InstallTime"),
      admin=True, timeout=300),
    UP("cbs_health", "Component store health (CheckHealth)",
      "DISM's quick, READ-ONLY check of whether the component store is flagged as corrupted "
      "-- seconds, unlike ScanHealth.",
      need_admin("DISM refuses a standard user") + "Dism.exe /Online /Cleanup-Image /CheckHealth",
      admin=True, timeout=300),
    UP("sfc_last", "Last System File Checker result",
      "What the last sfc /scannow found and fixed, read from CBS.log -- without running a new "
      "scan.",
      r"""$log = "$env:SystemRoot\Logs\CBS\CBS.log"
if (-not (Test-Path $log)) { '[INFO] CBS.log is not present (rolled into CbsPersist cabs).'; return }
$l = Select-String -Path $log -Pattern '\[SR\]' -ErrorAction SilentlyContinue | Select-Object -Last 40
if (-not $l) { '[INFO] No System File Checker run in the current CBS.log.'; return }
$l | ForEach-Object { $t = $_.Line; if ($t -match 'Cannot repair|corrupt') { "[WARN] $t" } else { "[INFO] $t" } }""",
      note="CBS.log may be unreadable unelevated"),
    UP("feature_update_readiness", "Windows 11 / feature update readiness",
      "The things a feature update checks: TPM 2.0, Secure Boot, UEFI, 64 GB disk, 4 GB RAM, "
      "and any upgrade safeguard hold the compatibility appraiser recorded.",
      CHK + r"""$os = Get-CimInstance Win32_OperatingSystem
Chk ($os.TotalVisibleMemorySize -ge 4000000) 'Memory >= 4 GB' ('{0:N1} GB' -f ($os.TotalVisibleMemorySize/1MB))
$c = Get-Volume -DriveLetter $env:SystemDrive[0]; Chk ($c.Size -ge 64GB) 'System disk >= 64 GB' ('{0:N0} GB' -f ($c.Size/1GB))
Chk ($env:firmware_type -eq 'UEFI' -or (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State')) 'UEFI firmware' "$env:firmware_type"
$spec = (Get-CimInstance -Namespace root\cimv2\Security\MicrosoftTpm -ClassName Win32_Tpm -ErrorAction SilentlyContinue).SpecVersion
Chk $(if ($spec) { $spec -like '2.0*' } else { $null }) 'TPM 2.0' $(if ($spec) { $spec } else { 'needs administrator to read' })
$gs = Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\TargetVersionUpgradeExperienceIndicators' -ErrorAction SilentlyContinue
foreach ($g in $gs) { $p = Get-ItemProperty $g.PSPath; if ($p.GatedBlockId -and $p.GatedBlockId -ne 'None') { "[WARN] Safeguard hold for $($g.PSChildName): $($p.GatedBlockId) $($p.GatedBlockReason)" } elseif ($p.RedReason -and $p.RedReason -ne 'None') { "[WARN] $($g.PSChildName): $($p.RedReason)" } else { "[OK]   $($g.PSChildName): no block recorded" } }""",
      note="the TPM version needs elevation", admin=True),
    UP("drivers_wu", "Driver updates offered by Windows Update",
      "Driver updates Windows Update has available for this PC (search only, nothing "
      "installed) -- Intel, Realtek and others distribute through it.",
      r"""$s = New-Object -ComObject Microsoft.Update.Session; $r = $s.CreateUpdateSearcher().Search("IsInstalled=0 and Type='Driver'")
if (-not $r.Updates.Count) { '[OK]   Windows Update offers no driver updates.'; return }
foreach ($u in $r.Updates) { "[INFO] $($u.Title)  (driver date $($u.DriverVerDate))" }""",
      timeout=300),
    UP("store_apps_updates", "Microsoft Store app update state",
      "Store apps with an update queued or failed, from the AppX deployment log -- why an app "
      "is stuck on an old version.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-AppXDeploymentServer/Operational'; Level=2; StartTime=(Get-Date).AddDays(-14)} -MaxEvents 40 -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No app deployment errors in 14 days.'; return }
$e | ForEach-Object { '[WARN] {0:yyyy-MM-dd HH:mm}  {1}' -f $_.TimeCreated, (($_.Message -split "`r?`n")[0]) }"""),
    UP("winget_upgrades", "Programs with updates available (winget)",
      "What winget would upgrade -- the version installed and the one available.",
      "winget upgrade --include-unknown --accept-source-agreements --disable-interactivity",
      kind=CMD, timeout=240),
    # ---- Services & processes ----------------------------------------------------------------
    SV("services_running", "Running services",
      "Every running service with its start type, account and executable.",
      T("Get-CimInstance Win32_Service -Filter \"State='Running'\" | Sort-Object Name | Select-Object Name, DisplayName, StartMode, StartName, @{n='PID';e={$_.ProcessId}}")),
    SV("services_nonms", "Services not made by Microsoft",
      "Services whose executable is not in the Windows folder or not signed by Microsoft -- "
      "third-party drivers' helpers, updaters, VPNs, and anything unexpected.",
      r"""Get-CimInstance Win32_Service | ForEach-Object {
  $path = ($_.PathName -replace '^"([^"]+)".*$','$1' -replace '^(\S+\.exe).*$','$1')
  if ($path -and $path -notmatch '\\Windows\\(System32|SysWOW64)\\svchost\.exe' -and (Test-Path $path)) {
    $sig = Get-AuthenticodeSignature $path -ErrorAction SilentlyContinue
    if ($sig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { '{0} {1,-40} {2,-9} {3,-10} {4}' -f $(if ($sig.Status -ne 'Valid') {'[WARN]'} else {'[INFO]'}), $_.Name, $_.State, $_.StartMode, $path } } } | Sort-Object""",
      timeout=300),
    SV("services_unquoted", "Unquoted service paths",
      "Services whose executable path has spaces and no quotes -- Windows tries <drive>\\Program.exe "
      "first, a classic privilege-escalation hole.",
      r"""$bad = Get-CimInstance Win32_Service | Where-Object { $_.PathName -and $_.PathName -notmatch '^"' -and ($_.PathName -split '\s+-|\s+/')[0] -match ' ' -and $_.PathName -notmatch '^[A-Za-z]:\\Windows\\' }
if (-not $bad) { '[OK]   No unquoted service paths with spaces.'; return }
$bad | ForEach-Object { "[WARN] $($_.Name): $($_.PathName)" }"""),
    SV("services_recovery", "Service recovery actions",
      "What Windows does when important services crash (restart / run program / reboot) -- "
      "sc qfailure for the services an admin cares about.",
      r"""foreach ($n in 'wuauserv','BITS','Dnscache','LanmanServer','LanmanWorkstation','EventLog','WinDefend','Spooler','W32Time') { "== $n"; sc.exe qfailure $n | Select-String 'RESET_PERIOD|FAILURE_ACTIONS|RESTART|REBOOT|RUN PROCESS' | ForEach-Object { '   ' + $_.Line.Trim() } }"""),
    SV("services_disabled", "Disabled services",
      "Services set to Disabled. 'Performance guides' disable things like the Windows Search, "
      "SysMain or the print spooler -- and some of those break features later.",
      T("Get-CimInstance Win32_Service -Filter \"StartMode='Disabled'\" | Sort-Object Name | Select-Object Name, DisplayName, PathName")),
    SV("process_tree", "Process tree",
      "Every process under its parent, with PID -- what launched what.",
      r"""$all = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name
$byParent = $all | Group-Object ParentProcessId -AsHashTable -AsString
$ids = @{}; $all | ForEach-Object { $ids[$_.ProcessId] = $true }
function Walk($p, $depth) { foreach ($c in @($byParent["$($p.ProcessId)"])) { if ($c -and $c.ProcessId -ne $p.ProcessId) { ('  ' * $depth) + "$($c.Name) [$($c.ProcessId)]"; if ($depth -lt 12) { Walk $c ($depth + 1) } } } }
$all | Where-Object { -not $ids[$_.ParentProcessId] -or $_.ParentProcessId -eq 0 } | ForEach-Object { "$($_.Name) [$($_.ProcessId)]"; Walk $_ 1 }"""),
    SV("process_cmdlines", "Process command lines",
      "Every process with its full command line -- PowerShell with -enc, rundll32 with a "
      "strange DLL, an EXE in Temp all show here.",
      r"""Get-CimInstance Win32_Process | Where-Object CommandLine | Sort-Object Name | ForEach-Object {
  $tag = if ($_.CommandLine -match '-enc(odedcommand)?\s|FromBase64|\\AppData\\Local\\Temp\\|mshta|regsvr32 /s /i:http') {'[WARN]'} else {'[INFO]'}
  '{0} {1,-28} {2,6}  {3}' -f $tag, $_.Name, $_.ProcessId, $_.CommandLine }""",
      note="command lines of system processes are blank unelevated"),
    SV("process_paths_odd", "Processes running from unusual folders",
      "Running programs whose executable is in Temp, Downloads, Public, the Recycle Bin or "
      "ProgramData root -- where malware drops itself.",
      r"""$odd = Get-Process | Where-Object { $_.Path -match '\\(Temp|Downloads|Users\\Public|\$Recycle\.Bin)\\|^[A-Za-z]:\\ProgramData\\[^\\]+\.exe$|\\AppData\\Roaming\\[^\\]+\.exe$' }
if (-not $odd) { '[OK]   No process runs from an unusual folder.'; return }
$odd | ForEach-Object { "[WARN] $($_.ProcessName) [$($_.Id)]  $($_.Path)" }"""),
    SV("dll_hijack_paths", "Writable folders on the system PATH",
      "System PATH folders a standard user can write to -- a DLL dropped there gets loaded "
      "by privileged programs.",
      r"""([Environment]::GetEnvironmentVariable('Path','Machine') -split ';') | Where-Object { $_ } | ForEach-Object {
  $p = [Environment]::ExpandEnvironmentVariables($_)
  if (Test-Path $p) { $acl = Get-Acl $p -ErrorAction SilentlyContinue
    $w = $acl.Access | Where-Object { $_.IdentityReference -match 'Users|Everyone|Authenticated Users' -and $_.FileSystemRights -match 'Write|Modify|FullControl' -and $_.AccessControlType -eq 'Allow' }
    if ($w) { "[WARN] $p  writable by $(($w.IdentityReference | Select-Object -Unique) -join ', ')" } else { "[OK]   $p" } } }"""),
    SV("service_dependencies", "Service dependencies of key services",
      "What the network, update and audio services depend on -- why stopping one service "
      "takes others down.",
      r"""foreach ($n in 'Dnscache','wuauserv','Audiosrv','LanmanWorkstation','Spooler','WinDefend','RpcSs') { $s = Get-Service $n -ErrorAction SilentlyContinue
  if ($s) { '{0,-18} depends on: {1}' -f $n, (($s.ServicesDependedOn.Name) -join ', '); '{0,-18} needed by : {1}' -f '', (($s.DependentServices.Name) -join ', ') } }"""),
    # ---- Startup & tasks ---------------------------------------------------------------------
    ST("startup_run_keys", "Programs that start at sign-in",
      "Run and RunOnce entries for the machine and this user, the Startup folders, and "
      "whether each is enabled in Task Manager's Startup tab.",
      r"""$appr = @{}
foreach ($a in 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32','HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder') {
  $p = Get-ItemProperty $a -ErrorAction SilentlyContinue; if ($p) { $p.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { $appr[$_.Name] = ($_.Value[0] % 2) -eq 0 } } }
foreach ($k in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run','HKCU:\Software\Microsoft\Windows\CurrentVersion\Run','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce','HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce') {
  $p = Get-ItemProperty $k -ErrorAction SilentlyContinue; if (-not $p) { continue }
  $p.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { $on = if ($appr.ContainsKey($_.Name)) { $appr[$_.Name] } else { $true }
    '{0} {1,-34} {2}   ({3})' -f $(if ($on) {'[INFO]'} else {'[OK]  '}), $_.Name, $_.Value, ($k -replace '^.*\\(Run\w*)$','$1') + $(if (-not $on) {', disabled'} else {''}) } }
foreach ($f in [Environment]::GetFolderPath('Startup'), [Environment]::GetFolderPath('CommonStartup')) { Get-ChildItem $f -ErrorAction SilentlyContinue | ForEach-Object { "[INFO] Startup folder: $($_.FullName)" } }"""),
    ST("tasks_recent", "Scheduled tasks created or changed recently",
      "Tasks whose definition file changed in the last 14 days -- installers and malware "
      "both register tasks.",
      need_admin("the task definition files are only readable elevated") +
      r"""Get-ChildItem "$env:SystemRoot\System32\Tasks" -Recurse -File -ErrorAction SilentlyContinue | Where-Object LastWriteTime -gt (Get-Date).AddDays(-14) | Sort-Object LastWriteTime -Descending | ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1}' -f $_.LastWriteTime, ($_.FullName -replace '^.*\\System32\\Tasks','') }""",
      admin=True),
    ST("tasks_failing", "Scheduled tasks that failed last time",
      "Enabled tasks whose last run result was not success, with the result code -- a broken "
      "backup or maintenance task fails silently for months.",
      r"""Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object State -ne 'Disabled' | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
  if ($i -and $i.LastRunTime -gt [datetime]'2000-01-01' -and $i.LastTaskResult -notin 0, 267009, 267011, 267014, 2147750687, 1) { '[WARN] {0,-70} last run {1:yyyy-MM-dd HH:mm}  result 0x{2:X}' -f ($_.TaskPath + $_.TaskName), $i.LastRunTime, $i.LastTaskResult } }""",
      timeout=300),
    ST("tasks_system_highest", "Tasks that run as SYSTEM or with highest privileges",
      "Non-Microsoft tasks running as SYSTEM or elevated -- a writable program path in one of "
      "these is a privilege escalation.",
      r"""Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -notlike '\Microsoft\*' -and ($_.Principal.UserId -match 'SYSTEM|S-1-5-18' -or $_.Principal.RunLevel -eq 'Highest') } | ForEach-Object {
  "[INFO] $($_.TaskPath)$($_.TaskName)  as $($_.Principal.UserId) ($($_.Principal.RunLevel))  -> $(($_.Actions | ForEach-Object { ($_.Execute + ' ' + $_.Arguments).Trim() }) -join ' ; ')" }""",
      note="tasks of other users may be missing"),
    ST("ifeo_debuggers", "Image File Execution Options hijacks",
      "IFEO Debugger entries -- they make Windows start another program INSTEAD of the one "
      "asked for (sethc.exe -> cmd.exe is a classic backdoor).",
      r"""$k = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options'
$hits = Get-ChildItem $k -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue; if ($p.Debugger) { "[WARN] $($_.PSChildName) -> Debugger: $($p.Debugger)" } }
if ($hits) { $hits } else { '[OK]   No IFEO debugger redirections.' }"""),
    ST("winlogon_entries", "Winlogon Shell and Userinit",
      "The programs Winlogon starts at sign-in. Anything besides explorer.exe and "
      "userinit.exe here is persistence.",
      CHK + r"""$w = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Chk ($w.Shell -eq 'explorer.exe') 'Shell' $w.Shell
Chk ($w.Userinit -match '^[A-Za-z]:\\Windows\\system32\\userinit\.exe,?$') 'Userinit' $w.Userinit
$u = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Winlogon' -ErrorAction SilentlyContinue).Shell
if ($u) { "[WARN] Per-user Shell override: $u" }"""),
    ST("appinit_dlls", "AppInit_DLLs and AppCert DLLs",
      "DLLs Windows injects into every process that loads user32 (AppInit) or creates a "
      "process (AppCertDlls) -- legacy injection points malware still uses.",
      CHK + r"""foreach ($k in 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Windows','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows NT\CurrentVersion\Windows') {
  $p = Get-ItemProperty $k -ErrorAction SilentlyContinue; Chk (-not $p.AppInit_DLLs) "AppInit_DLLs ($($k.Split('\')[2]))" "LoadAppInit_DLLs=$($p.LoadAppInit_DLLs) DLLs='$($p.AppInit_DLLs)'" }
$c = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\AppCertDlls' -ErrorAction SilentlyContinue
Chk (-not $c) 'AppCertDlls' $(if ($c) { ($c.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join '; ' } else { 'none' })"""),
    ST("startup_impact_list", "Startup apps and their measured impact",
      "Startup programs with the CPU and disk time Windows measured at the last boots "
      "(Task Manager's impact column), heaviest first.",
      T(r"""Get-CimInstance Win32_StartupCommand | Select-Object Name, Command, Location, User"""),
      note="entries of other users are not listed"),
    # ---- Applications ------------------------------------------------------------------------
    AP("apps_installed", "Installed programs",
      "Every installed desktop program (both registry views and per-user), with version, "
      "publisher and install date.",
      T(f"Get-ItemProperty {_UNINSTALL} -ErrorAction SilentlyContinue | Where-Object {{ $_.DisplayName -and -not $_.SystemComponent }} | Sort-Object DisplayName | Select-Object DisplayName, DisplayVersion, Publisher, InstallDate")),
    AP("apps_store", "Installed Store apps",
      "Microsoft Store / MSIX apps installed for this user, with version and publisher.",
      T("Get-AppxPackage | Where-Object { -not $_.IsFramework -and -not $_.NonRemovable } | Sort-Object Name | Select-Object Name, Version, @{n='Publisher';e={($_.Publisher -split ',')[0]}}, InstallLocation")),
    AP("apps_largest", "Largest installed programs",
      "Installed programs by the size their installer registered, biggest first (only "
      "programs that record a size).",
      T(f"Get-ItemProperty {_UNINSTALL} -ErrorAction SilentlyContinue | Where-Object {{ $_.DisplayName -and $_.EstimatedSize }} | Sort-Object EstimatedSize -Descending | Select-Object -First 30 DisplayName, @{{n='GB';e={{[math]::Round($_.EstimatedSize/1MB,2)}}}}, Publisher")),
    AP("apps_32bit", "32-bit programs installed",
      "Programs registered in the 32-bit (WOW6432Node) uninstall view.",
      T("Get-ItemProperty 'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' -ErrorAction SilentlyContinue | Where-Object DisplayName | Sort-Object DisplayName | Select-Object DisplayName, DisplayVersion, Publisher")),
    AP("apps_eol_runtimes", "Old runtimes still installed",
      "Java, Flash, Silverlight, Adobe Reader older versions, Python 2, old .NET Core and VC++ "
      "2005/2008 -- software past its end of support that keeps getting exploited.",
      f"$a = Get-ItemProperty {_UNINSTALL} -ErrorAction SilentlyContinue | Where-Object DisplayName\n" + r"""$hits = $a | Where-Object { $_.DisplayName -match 'Java ?[678]|Java\(TM\)|Flash Player|Silverlight|Python 2\.|\.NET Core [123]\.|Visual C\+\+ 200[58]|QuickTime|Adobe Reader [0-9]|Adobe Acrobat Reader DC .*(1[5-9]|20)\.' }
if (-not $hits) { '[OK]   No known end-of-support runtime found.'; return }
$hits | Sort-Object DisplayName | ForEach-Object { "[WARN] $($_.DisplayName) $($_.DisplayVersion)" }"""),
    AP("default_apps", "Default apps for common file types",
      "Which program opens web links, PDF, images, mail and music -- read from this user's "
      "UserChoice keys.",
      r"""foreach ($e in '.htm','.html','.pdf','.jpg','.png','.mp3','.mp4','.txt','.zip') { $c = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\$e\UserChoice" -ErrorAction SilentlyContinue).ProgId; '{0,-6} {1}' -f $e, $(if ($c) {$c} else {'(Windows default)'}) }
foreach ($p in 'http','https','mailto') { $c = (Get-ItemProperty "HKCU:\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\$p\UserChoice" -ErrorAction SilentlyContinue).ProgId; '{0,-6} {1}' -f $p, $(if ($c) {$c} else {'(Windows default)'}) }"""),
    # No "browser extensions" check: a script that walks browser profile folders is what
    # info-stealers do, and the antivirus here (Bitdefender) blocked it at launch and then
    # held its file open (2026-10-16). Browser POLICIES are checked under Security.
    AP("office_info", "Microsoft Office / 365 installation",
      "Click-to-Run version, update channel, platform and whether updates are enabled.",
      r"""$c = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration' -ErrorAction SilentlyContinue
if (-not $c) { '[INFO] No Click-to-Run Office installation.'; return }
[pscustomobject]@{ Version=$c.VersionToReport; Platform=$c.Platform; Channel=$c.CDNBaseUrl; UpdatesEnabled=$c.UpdatesEnabled; Products=$c.ProductReleaseIds; Culture=$c.ClientCulture } | Format-List | Out-String -Width 220"""),
    AP("app_crashes_by_app", "Which programs crash most",
      "Windows Error Reporting records for the last 30 days grouped by application -- the "
      "crashing program list behind Reliability Monitor.",
      T(r"""Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Windows Error Reporting'; Id=1001; StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue | ForEach-Object { [pscustomobject]@{ Event=$_.Properties[2].Value; App=$_.Properties[5].Value } } | Group-Object Event, App | Sort-Object Count -Descending | Select-Object -First 25 Count, Name""")),
    AP("file_associations_odd", "Script file types and what opens them",
      "What double-clicking .js, .vbs, .hta, .wsf, .bat and .ps1 does. Opening scripts in "
      "Notepad instead of running them blocks a whole class of email attachments.",
      r"""foreach ($e in '.js','.jse','.vbs','.vbe','.wsf','.hta','.bat','.cmd','.ps1','.scr') { $t = (cmd /c assoc $e 2>$null) -replace '^.*=',''; $o = if ($t) { (cmd /c ftype $t 2>$null) -replace '^.*=','' } else { '' }
  $runs = $o -match 'wscript|cscript|mshta|%1"? %\*$|cmd\.exe'
  '{0} {1,-5} {2,-14} {3}' -f $(if ($runs) {'[INFO]'} else {'[OK]  '}), $e, $t, $o }"""),
    # ---- Power -------------------------------------------------------------------------------
    PW("power_plans", "Power plans",
      "Every power plan with the active one marked.", "powercfg /list", kind=CMD),
    PW("sleep_states", "Available sleep states",
      "Which sleep states the firmware and drivers support (S3, Modern Standby S0, "
      "hibernate, hybrid sleep) and why the others are not available.",
      "powercfg /a", kind=CMD),
    PW("wake_sources", "What woke the PC last",
      "The device or timer that woke the PC from sleep last time, devices allowed to wake it, "
      "and wake timers armed now -- 'my PC turns itself on at night'.",
      r"""'Last wake:'; powercfg /lastwake
''; 'Devices allowed to wake the PC:'; powercfg /devicequery wake_armed
''; 'Wake timers:'
$isAdm = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdm) { powercfg /waketimers } else { 'NOT AVAILABLE: the wake-timer list needs administrator rights.' }""", admin=True),
    PW("power_requests", "What is keeping the PC awake",
      "Programs, drivers and services that currently block sleep or display off.",
      need_admin("powercfg /requests refuses a standard user") + "powercfg /requests", admin=True),
    PW("sleep_events", "Sleep and wake history (7 days)",
      "Each time the PC went to sleep and woke up (Kernel-Power 42, Power-Troubleshooter 1), "
      "with the wake source.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=42,107,1; StartTime=(Get-Date).AddDays(-7)} -ErrorAction SilentlyContinue | Where-Object { $_.ProviderName -match 'Kernel-Power|Power-Troubleshooter' }
if (-not $e) { '[INFO] No sleep or wake in the last 7 days (the PC stayed on, or was shut down instead).'; return }
$e | ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1}' -f $_.TimeCreated, ((($_.Message -split "`r?`n") | Where-Object { $_ } | Select-Object -First 2) -join ' / ') }"""),
    PW("hibernation", "Hibernation and Fast Startup",
      "Whether hibernation is enabled, the hiberfil size, and Fast Startup (which depends on "
      "it).",
      r"""$h = Get-Item "$env:SystemDrive\hiberfil.sys" -Force -ErrorAction SilentlyContinue
"hiberfil.sys: $(if ($h) { '{0:N1} GB' -f ($h.Length/1GB) } else { 'absent (hibernation off)' })"
"HibernateEnabled: $((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' -ErrorAction SilentlyContinue).HibernateEnabled)"
"Fast Startup (HiberbootEnabled): $((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -ErrorAction SilentlyContinue).HiberbootEnabled)" """),
    PW("usb_selective_suspend", "USB selective suspend and PCIe power saving",
      "Power-saving settings that cause USB devices to disconnect or Wi-Fi to drop, for the "
      "active plan on AC and battery.",
      r"""powercfg /query SCHEME_CURRENT 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 | Select-String 'Power Setting Name|Current (AC|DC)' | ForEach-Object { $_.Line.Trim() }
powercfg /query SCHEME_CURRENT 501a4d13-42af-4429-9fd1-a8218c268e20 ee12f906-d277-404b-b6da-e5fa1a576df5 | Select-String 'Power Setting Name|Current (AC|DC)' | ForEach-Object { $_.Line.Trim() }"""),
    # ---- Printing ----------------------------------------------------------------------------
    PR("printers", "Printers",
      "Every printer with its driver, port, default flag, sharing and status.",
      _SPOOLER + T("Get-Printer -ErrorAction SilentlyContinue | Select-Object Name, DriverName, PortName, Shared, Type, PrinterStatus, @{n='Default';e={(Get-CimInstance Win32_Printer -Filter \"Name='$($_.Name -replace \"'\",\"''\")'\").Default}}")),
    PR("print_jobs", "Jobs waiting in the print queues",
      "Print jobs stuck in any queue, with owner, size and state -- the stuck job that blocks "
      "everything behind it.",
      _SPOOLER + r"""$j = Get-Printer -ErrorAction SilentlyContinue | ForEach-Object { Get-PrintJob -PrinterName $_.Name -ErrorAction SilentlyContinue }
if (-not $j) { '[OK]   No jobs waiting.'; return }
$j | Select-Object PrinterName, Id, DocumentName, UserName, JobStatus, Size, SubmittedTime | Format-Table -AutoSize | Out-String -Width 220"""),
    PR("print_drivers", "Printer drivers",
      "Installed printer drivers with version, type and whether they are packaged "
      "(Point-and-Print hardening blocks unpackaged ones).",
      _SPOOLER + T("Get-PrinterDriver -ErrorAction SilentlyContinue | Select-Object Name, PrinterEnvironment, MajorVersion, IsPackageAware, Manufacturer")),
    PR("spooler_status", "Print Spooler and Point-and-Print",
      "Spooler service state and the Point-and-Print restrictions (PrintNightmare "
      "hardening): whether non-admins can install printer drivers.",
      CHK + r"""$s = Get-Service Spooler; "[INFO] Spooler -- $($s.Status), start type $($s.StartType)"
$k = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Printers\PointAndPrint'
$r = (Get-ItemProperty $k -ErrorAction SilentlyContinue).RestrictDriverInstallationToAdministrators
Chk ($r -ne 0) 'Driver install restricted to admins' "RestrictDriverInstallationToAdministrators=$(if ($null -eq $r) {'not set (secure default since 2021)'} else {$r})"
$p = Get-ItemProperty $k -ErrorAction SilentlyContinue
Chk ($p.NoWarningNoElevationOnInstall -ne 1) 'Point-and-Print warns and elevates' "NoWarningNoElevationOnInstall=$($p.NoWarningNoElevationOnInstall)" """),
    PR("printer_ports", "Printer ports",
      "TCP/IP, USB and WSD ports with their addresses -- a printer that 'moved' to a new IP.",
      _SPOOLER + T("Get-PrinterPort -ErrorAction SilentlyContinue | Select-Object Name, Description, PrinterHostAddress, PortNumber")),
    PR("print_errors", "Printing errors (7 days)",
      "Errors and warnings from the Print Service's own log -- driver crashes, failed jobs, "
      "blocked Point-and-Print installs.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-PrintService/Admin'; Level=1,2,3; StartTime=(Get-Date).AddDays(-7)} -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No printing errors in the last 7 days.'; return }
$e | ForEach-Object { '[WARN] {0:yyyy-MM-dd HH:mm}  {1}' -f $_.TimeCreated, (($_.Message -split "`r?`n")[0]) }"""),
    # ---- Group Policy ------------------------------------------------------------------------
    GP("gp_summary", "Applied Group Policy (summary)",
      "gpresult's summary for this user: domain, which GPOs applied and which were filtered "
      "out. On a workgroup PC this is local policy only.",
      "gpresult /r /scope user", kind=CMD, timeout=180),
    GP("gp_computer", "Applied Group Policy (computer)",
      "The computer half of gpresult. Refused unelevated -- gpresult says Access Denied.",
      need_admin("the computer scope of gpresult is refused for a standard user") +
      "gpresult /r /scope computer", admin=True, timeout=180),
    GP("gp_last_refresh", "Group Policy processing history",
      "When policy was last applied and how long it took, from the GroupPolicy operational "
      "log (8000/8001 start/end, 8002/8003 errors).",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-GroupPolicy/Operational'; Id=8000,8001,8002,8003,8004,8005,8006,8007} -MaxEvents 20 -ErrorAction SilentlyContinue
if (-not $e) { 'No Group Policy processing recorded.'; return }
$e | ForEach-Object { '{0} {1:yyyy-MM-dd HH:mm}  {2}' -f $(if ($_.Level -le 2) {'[BAD] '} else {'[INFO]'}), $_.TimeCreated, (($_.Message -split "`r?`n")[0]) }"""),
    GP("policies_registry", "Policy registry values in force",
      "Every value under the four Policies branches (machine and user) -- the raw form of "
      "every policy set on this PC, by GPO, local policy or a tweak tool.",
      r"""foreach ($r in 'HKLM:\SOFTWARE\Policies','HKCU:\SOFTWARE\Policies','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies') {
  "== $r"; Get-ChildItem $r -Recurse -ErrorAction SilentlyContinue | ForEach-Object { $k = $_; ($k | Get-ItemProperty -ErrorAction SilentlyContinue).PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { '   {0}\{1} = {2}' -f ($k.Name -replace '^HKEY_[A-Z_]+\\',''), $_.Name, ($_.Value -join ',') } } }"""),
    GP("local_pol_files", "Local Group Policy files",
      "Whether local machine and user Registry.pol files exist and when they changed -- "
      "evidence of local policy being set.",
      r"""foreach ($f in "$env:SystemRoot\System32\GroupPolicy\Machine\Registry.pol","$env:SystemRoot\System32\GroupPolicy\User\Registry.pol","$env:SystemRoot\System32\GroupPolicyUsers") { if (Test-Path $f) { $i = Get-Item $f -Force; '[INFO] {0}  {1}  {2} bytes' -f $f, $i.LastWriteTime, $i.Length } else { "[OK]   $f -- absent" } }"""),
    GP("mdm_enrollment", "MDM / Intune enrollment",
      "Whether this PC is enrolled in Intune or another MDM, the enrollment provider and "
      "the MDM URL -- 'who manages this PC'.",
      r"""$e = Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Enrollments' -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object { $_.ProviderID -or $_.UPN }
if (-not $e) { '[INFO] Not enrolled in any MDM.'; return }
$e | ForEach-Object { "[INFO] Provider $($_.ProviderID)  UPN $($_.UPN)  Discovery $($_.DiscoveryServiceFullURL)" }"""),
    # ---- more Troubleshooting ----------------------------------------------------------------
    TS("explorer_crashes", "Explorer and shell crashes",
      "Crashes of explorer.exe, StartMenuExperienceHost, ShellExperienceHost and SearchHost "
      "in the last 30 days, with the faulting module -- a shell extension usually.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='Application'; Id=1000; StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue | Where-Object { $_.Properties[0].Value -match '^(explorer|StartMenuExperienceHost|ShellExperienceHost|SearchHost|SearchApp|dwm)\.exe$' }
if (-not $e) { '[OK]   No shell crashes in 30 days.'; return }
$e | ForEach-Object { '[BAD]  {0:yyyy-MM-dd HH:mm}  {1}  module {2}' -f $_.TimeCreated, $_.Properties[0].Value, $_.Properties[3].Value }"""),
    TS("shell_extensions", "Third-party shell extensions",
      "Context-menu and shell extension handlers registered by non-Microsoft software -- the "
      "usual reason Explorer is slow or crashes on right-click.",
      r"""$a = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Shell Extensions\Approved' -ErrorAction SilentlyContinue
$a.PSObject.Properties | Where-Object { $_.Name -like '{*' } | ForEach-Object { $clsid = $_.Name
  $dll = (Get-ItemProperty "Registry::HKEY_CLASSES_ROOT\CLSID\$clsid\InprocServer32" -ErrorAction SilentlyContinue).'(default)'
  if ($dll -and $dll -notmatch '\\Windows\\(System32|SysWOW64)\\') { '{0,-40} {1}' -f $_.Value, $dll } } | Sort-Object -Unique"""),
    TS("search_index", "Windows Search index status",
      "Whether the Windows Search service runs, how many items are indexed and which "
      "locations are included -- 'search finds nothing'.",
      r"""$s = Get-Service WSearch -ErrorAction SilentlyContinue; "WSearch: $($s.Status), $($s.StartType)"
$c = Get-Counter '\Search Indexer(*)\Documents Filtered' -ErrorAction SilentlyContinue
if ($c) { $c.CounterSamples | ForEach-Object { "Documents filtered: $($_.CookedValue)" } }
(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows Search' -ErrorAction SilentlyContinue).DataDirectory | ForEach-Object { "Index location: $_" }"""),
    TS("dism_logs", "Recent DISM and CBS errors",
      "Error lines from the current DISM log -- why a repair or feature install failed.",
      r"""$l = "$env:SystemRoot\Logs\DISM\dism.log"
if (-not (Test-Path $l)) { '[INFO] No dism.log.'; return }
Select-String -Path $l -Pattern ', Error ' -ErrorAction SilentlyContinue | Select-Object -Last 30 | ForEach-Object { "[WARN] $($_.Line)" }""",
      note="dism.log may be unreadable unelevated"),
    TS("time_skew", "Clock skew against an internet time server",
      "How far this PC's clock is from time.windows.com -- more than a few seconds breaks "
      "sign-ins and certificates.",
      r"""w32tm /stripchart /computer:time.windows.com /samples:3 /dataonly 2>&1""", timeout=60),
    TS("profile_errors", "User profile errors",
      "User Profile Service warnings and errors (temporary profile, cannot load profile) in "
      "the last 30 days.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Microsoft-Windows-User Profiles Service'; Level=1,2,3; StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No user profile errors in 30 days.'; return }
$e | ForEach-Object { '[WARN] {0:yyyy-MM-dd HH:mm}  {1}  {2}' -f $_.TimeCreated, $_.Id, (($_.Message -split "`r?`n")[0]) }"""),
    TS("dns_client_errors", "DNS client errors",
      "Name resolution timeouts and failures logged by the DNS client in the last 7 days.",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-DNS-Client'; Level=2,3; StartTime=(Get-Date).AddDays(-7)} -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No DNS client errors in 7 days.'; return }
$e | Group-Object { ($_.Message -split "`r?`n")[0] } | Sort-Object Count -Descending | Select-Object -First 20 | ForEach-Object { '[WARN] x{0,-4} {1}' -f $_.Count, $_.Name }"""),
    TS("network_drops", "Network disconnects (7 days)",
      "Network adapter link down/up events and Wi-Fi disconnections with reason -- 'the "
      "internet keeps dropping'.",
      r"""$e = @(Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-WLAN-AutoConfig/Operational'; Id=8003; StartTime=(Get-Date).AddDays(-7)} -ErrorAction SilentlyContinue) + @(Get-WinEvent -FilterHashtable @{LogName='System'; Id=27,32,4202; StartTime=(Get-Date).AddDays(-7)} -ErrorAction SilentlyContinue)
if (-not $e) { '[OK]   No disconnects recorded in 7 days.'; return }
$e | Sort-Object TimeCreated -Descending | Select-Object -First 60 | ForEach-Object { '[WARN] {0:yyyy-MM-dd HH:mm}  {1,-40} {2}' -f $_.TimeCreated, $_.ProviderName, (($_.Message -split "`r?`n")[0]) }"""),
    TS("usb_disconnects", "USB device connect and disconnect history",
      "USB devices that arrived and were removed recently (Kernel-PnP 400/410/420) -- for a "
      "flaky hub or cable.",
      r"""Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Kernel-PnP/Configuration'; Id=400,410,420; StartTime=(Get-Date).AddDays(-3)} -MaxEvents 60 -ErrorAction SilentlyContinue | ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1}  {2}' -f $_.TimeCreated, $_.Id, $_.Properties[0].Value }"""),
    # ---- more Inventory ----------------------------------------------------------------------
    IN("serials", "Serial numbers and warranty identifiers",
      "System, board and disk serial numbers, and the UUID -- everything a warranty or asset "
      "form asks for.",
      L(r"""[pscustomobject]@{ System=(Get-CimInstance Win32_BIOS).SerialNumber; Board=(Get-CimInstance Win32_BaseBoard).SerialNumber
  UUID=(Get-CimInstance Win32_ComputerSystemProduct).UUID; Disks=((Get-PhysicalDisk | ForEach-Object { "$($_.FriendlyName): $($_.SerialNumber)" }) -join '; ') }""")),
    IN("hardware_ids", "Hardware IDs of every device",
      "PCI and USB hardware ids per device -- what you search for when Windows says 'unknown "
      "device'.",
      T("Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -match '^(PCI|USB|HID|ACPI)\\\\' } | Sort-Object Class | Select-Object Class, FriendlyName, @{n='Id';e={($_.InstanceId -split '\\\\')[1]}}")),
    IN("product_keys", "Windows product key source",
      "Whether a product key is embedded in the firmware (OEM) -- the key is shown only as "
      "its last 5 characters.",
      r"""$k = (Get-CimInstance SoftwareLicensingService).OA3xOriginalProductKey
if ($k) { "Firmware (OA3) key present: xxxxx-xxxxx-xxxxx-xxxxx-$($k.Substring($k.Length - 5))" } else { 'No key embedded in firmware (retail, volume or digital licence).' }
"Description: $((Get-CimInstance SoftwareLicensingService).OA3xOriginalProductKeyDescription)" """),
    IN("network_inventory", "Network identity",
      "Computer name, DNS suffix, domain/workgroup, MAC and IP of each connected adapter -- "
      "the line for an asset sheet.",
      T("Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object Status -eq 'Up' | ForEach-Object { [pscustomobject]@{ Computer=$env:COMPUTERNAME; Adapter=$_.Name; MAC=$_.MacAddress; IP=((Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress -join ', '); Speed=$_.LinkSpeed } }")),
    IN("installed_languages_fonts", "Fonts installed",
      "How many fonts are installed system-wide and per user, and the per-user ones by name "
      "(apps that install fonts only for one user confuse other accounts).",
      r"""$sys = (Get-ChildItem "$env:SystemRoot\Fonts" -File -ErrorAction SilentlyContinue).Count
$user = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\Windows\Fonts" -File -ErrorAction SilentlyContinue
"System fonts: $sys files"; "Per-user fonts: $(@($user).Count)"; $user | ForEach-Object { "   $($_.Name)" }"""),
]

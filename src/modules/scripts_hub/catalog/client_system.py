"""System, Hardware, Storage and Performance checks. Read-only."""
from ._base import CHK, CMD, L, T, maker, need_admin

S = maker("System")
H = maker("Hardware")
D = maker("Storage")
P = maker("Performance")

SCRIPTS = [
    # ---- System ------------------------------------------------------------------------------
    S("os_version", "Windows version, edition and build",
      "Edition, display version (24H2/25H2...), build and revision (UBR), install type and "
      "the date this installation was created. The UBR is what tells two '26100' machines "
      "apart: it moves with every cumulative update.",
      L(r"""$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
[pscustomobject]@{ Product=$cv.ProductName; Edition=$cv.EditionID; Version=$cv.DisplayVersion
  Build="$($cv.CurrentBuild).$($cv.UBR)"; InstallationType=$cv.InstallationType
  Installed=([datetime]'1970-01-01').AddSeconds($cv.InstallDate).ToLocalTime()
  RegisteredOwner=$cv.RegisteredOwner }""")),
    S("activation", "Windows activation status",
      "License status, channel (Retail / OEM / Volume) and partial product key of the Windows "
      "license, from the Software Licensing service -- what slmgr /dli shows, without its "
      "message box.",
      L(r"""Get-CimInstance SoftwareLicensingProduct -Filter "ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f' AND PartialProductKey IS NOT NULL" |
  Select-Object Name, Description, @{n='Status';e={ switch ($_.LicenseStatus) { 0 {'Unlicensed'} 1 {'Licensed'} 2 {'OOB grace'} 3 {'OOT grace'} 4 {'Non-genuine grace'} 5 {'Notification'} 6 {'Extended grace'} default {$_} } }}, PartialProductKey, ProductKeyChannel""")),
    S("env_machine", "System environment variables",
      "Machine-wide environment variables (HKLM), including PATH split one entry per line -- "
      "a missing or duplicated PATH entry is behind many 'command not found' problems.",
      r"""$e = [Environment]::GetEnvironmentVariables('Machine')
$e.Keys | Sort-Object | Where-Object { $_ -ne 'Path' } | ForEach-Object { '{0,-28} {1}' -f $_, $e[$_] }
''; 'PATH entries:'
$e['Path'] -split ';' | Where-Object { $_ } | ForEach-Object { if (Test-Path ([Environment]::ExpandEnvironmentVariables($_))) { "[OK]   $_" } else { "[WARN] $_   (does not exist)" } }"""),
    S("env_user", "User environment variables",
      "This user's environment variables (HKCU), with the user PATH checked entry by entry "
      "for folders that no longer exist.",
      r"""$e = [Environment]::GetEnvironmentVariables('User')
$e.Keys | Sort-Object | Where-Object { $_ -ne 'Path' } | ForEach-Object { '{0,-28} {1}' -f $_, $e[$_] }
''; 'User PATH entries:'
($e['Path'] -split ';') | Where-Object { $_ } | ForEach-Object { if (Test-Path ([Environment]::ExpandEnvironmentVariables($_))) { "[OK]   $_" } else { "[WARN] $_   (does not exist)" } }"""),
    S("pending_reboot", "Is a restart pending, and why",
      "The three signals Windows itself uses: Component Based Servicing RebootPending, "
      "Windows Update RebootRequired, and queued file REPLACEMENTS in "
      "PendingFileRenameOperations (queued deletions alone do not need a restart).",
      CHK + r"""$cbs = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
$wu = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
$ops = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue).PendingFileRenameOperations
$repl = 0; if ($ops) { for ($i = 0; $i -lt $ops.Count - 1; $i += 2) { if ($ops[$i + 1]) { $repl++ } } }
Chk (-not $cbs) 'Servicing (CBS)' $(if ($cbs) { 'an update or feature needs a restart' } else { 'nothing pending' })
Chk (-not $wu) 'Windows Update' $(if ($wu) { 'an installed update needs a restart' } else { 'nothing pending' })
Chk ($repl -eq 0) 'File replacements' "$repl queued replacement(s); queued deletions do not need a restart" """),
    S("time_sync", "Time and time synchronisation",
      "Time zone, the NTP source Windows Time uses and when it last synced. Kerberos, TLS and "
      "domain sign-in fail when the clock drifts more than five minutes.",
      r"""Get-TimeZone | Select-Object Id, DisplayName | Format-List | Out-String -Width 220
w32tm /query /status /verbose 2>&1 | Select-String 'Source|Last Successful|Leap|Stratum|Poll|Phase' | ForEach-Object { $_.Line.Trim() }""",
      note="w32tm may refuse some fields"),
    S("locale", "Language, region and keyboard",
      "Display language, system locale (what non-Unicode programs use -- the cause of "
      "garbled text in old apps), user region format and installed keyboard layouts.",
      L(r"""[pscustomobject]@{ SystemLocale=(Get-WinSystemLocale).Name; UICulture=(Get-UICulture).Name
  Culture=(Get-Culture).Name; HomeLocation=(Get-WinHomeLocation).HomeLocation
  Languages=((Get-WinUserLanguageList).LanguageTag -join ', ')
  Keyboards=((Get-WinUserLanguageList).InputMethodTips -join ', ') }""")),
    S("features_enabled", "Optional features that are enabled",
      "Windows optional features turned on (Hyper-V, WSL, .NET 3.5, SMB1...). SMB1 or "
      "Telnet enabled here is a hardening finding.",
      need_admin("Get-WindowsOptionalFeature needs elevation") +
      T("Get-WindowsOptionalFeature -Online | Where-Object State -eq 'Enabled' | Sort-Object FeatureName | Select-Object FeatureName, State"),
      admin=True, timeout=240),
    S("capabilities", "Installed Windows capabilities",
      "Features on Demand that are installed: RSAT tools, OpenSSH, language packs, legacy "
      "components like Internet Explorer mode or WordPad.",
      need_admin("Get-WindowsCapability needs elevation") +
      T("Get-WindowsCapability -Online | Where-Object State -eq 'Installed' | Sort-Object Name | Select-Object Name, State"),
      admin=True, timeout=240),
    S("dotnet_versions", ".NET Framework and .NET runtimes",
      ".NET Framework 4.x release (mapped to its version) plus every modern .NET runtime "
      "installed side by side. 'This app needs .NET X' starts here.",
      r"""$rel = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -ErrorAction SilentlyContinue).Release
$fw = switch ($rel) { { $_ -ge 533320 } { '4.8.1' ; break } { $_ -ge 528040 } { '4.8'; break } { $_ -ge 461808 } { '4.7.2'; break } { $_ -ge 394802 } { '4.6.2'; break } default { if ($rel) { "release $rel" } else { 'not found' } } }
".NET Framework: $fw (release $rel)"
''
if (Get-Command dotnet -ErrorAction SilentlyContinue) { 'Runtimes (dotnet --list-runtimes):'; dotnet --list-runtimes } else { 'dotnet host not on PATH -- modern .NET runtimes not found via PATH' }"""),
    S("powershell_info", "PowerShell versions and execution policy",
      "Windows PowerShell version, whether PowerShell 7 is installed, the effective execution "
      "policy per scope and whether script block / module logging is configured.",
      r""""Windows PowerShell: $($PSVersionTable.PSVersion)"
if (Get-Command pwsh -ErrorAction SilentlyContinue) { "PowerShell 7: $((Get-Command pwsh).Version)" } else { 'PowerShell 7: not installed' }
''
Get-ExecutionPolicy -List | Format-Table -AutoSize | Out-String -Width 220
$sb = (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\PowerShell\ScriptBlockLogging' -ErrorAction SilentlyContinue).EnableScriptBlockLogging
"Script block logging policy: $(if ($sb -eq 1) { 'ON' } else { 'not set by policy' })" """),
    S("wsl_status", "WSL distributions",
      "Whether the Windows Subsystem for Linux is installed and which distributions exist, "
      "with their state and WSL version.",
      r"""$env:WSL_UTF8 = '1'
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { '[INFO] WSL is not installed.'; return }
$out = wsl.exe --list --verbose 2>&1 | ForEach-Object { "$_" -replace "`0", '' } | Where-Object { $_.Trim() }
if ($LASTEXITCODE -ne 0) { "[INFO] No WSL distribution is installed ($(($out | Select-Object -First 1)))" } else { $out }"""),
    S("crash_settings", "Crash dump and recovery settings",
      "What Windows does on a blue screen: dump type (none / small / kernel / complete / "
      "automatic), dump file path, automatic restart and whether the page file is big enough "
      "for the dump.",
      L(r"""$c = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl'
[pscustomobject]@{ DumpType = switch ($c.CrashDumpEnabled) { 0 {'None'} 1 {'Complete'} 2 {'Kernel'} 3 {'Small (minidump)'} 7 {'Automatic'} default { $c.CrashDumpEnabled } }
  DumpFile=$c.DumpFile; MinidumpDir=$c.MinidumpDir; AutoReboot=[bool]$c.AutoReboot; Overwrite=[bool]$c.Overwrite
  PageFiles=((Get-CimInstance Win32_PageFileUsage | ForEach-Object { "$($_.Name) $($_.AllocatedBaseSize) MB" }) -join ', ') }""")),
    S("page_file", "Page file configuration and usage",
      "Whether Windows manages the page file, where it is, its size and peak usage since boot. "
      "A page file disabled or too small breaks crash dumps and some games.",
      CHK + r"""$cs = Get-CimInstance Win32_ComputerSystem
"Automatic management: $($cs.AutomaticManagedPagefile)"
Get-CimInstance Win32_PageFileUsage | Select-Object Name, AllocatedBaseSize, CurrentUsage, PeakUsage | Format-Table -AutoSize | Out-String -Width 220
if (-not (Get-CimInstance Win32_PageFileUsage)) { '[WARN] No page file -- crash dumps cannot be written' }"""),
    S("virtualization", "Virtualization and memory integrity",
      "Virtualization-based security, Credential Guard and memory integrity (HVCI) as "
      "configured and as actually RUNNING, plus whether the CPU's virtualization is on in "
      "firmware.",
      L(r"""$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction SilentlyContinue
$map = @{0='none';1='Credential Guard';2='Memory integrity (HVCI)';3='System Guard';4='SMM protection';7='Kernel-mode HW stack protection'}
[pscustomobject]@{
  VBS = switch ($dg.VirtualizationBasedSecurityStatus) { 0 {'off'} 1 {'enabled, not running'} 2 {'RUNNING'} default {'unknown'} }
  Configured = (($dg.SecurityServicesConfigured | ForEach-Object { $map[[int]$_] }) -join ', ')
  Running = (($dg.SecurityServicesRunning | ForEach-Object { $map[[int]$_] }) -join ', ')
  FirmwareVirtualization = (Get-CimInstance Win32_Processor | Select-Object -First 1).VirtualizationFirmwareEnabled
  HypervisorPresent = (Get-CimInstance Win32_ComputerSystem).HypervisorPresent }""")),
    S("secure_boot_tpm", "Secure Boot, TPM and firmware mode",
      "UEFI or legacy BIOS, Secure Boot on/off and the TPM's presence, version and readiness "
      "-- the Windows 11 and BitLocker prerequisites.",
      CHK + r"""$fw = $env:firmware_type; if (-not $fw) { $fw = if (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State') { 'UEFI' } else { 'Legacy BIOS' } }
Chk ($fw -eq 'UEFI') 'Firmware' $fw
try { $sb = Confirm-SecureBootUEFI -ErrorAction Stop; Chk $sb 'Secure Boot' $(if ($sb) {'on'} else {'off'}) } catch { Chk $null 'Secure Boot' $_.Exception.Message }
try { $t = Get-Tpm -ErrorAction Stop; Chk ($t.TpmPresent -and $t.TpmReady) 'TPM' "present=$($t.TpmPresent) ready=$($t.TpmReady) enabled=$($t.TpmEnabled)" } catch { Chk $null 'TPM' 'needs administrator to read' }
$spec = (Get-CimInstance -Namespace root\cimv2\Security\MicrosoftTpm -ClassName Win32_Tpm -ErrorAction SilentlyContinue).SpecVersion
if ($spec) { "[INFO] TPM spec version -- $spec" }""",
      note="the TPM and Secure Boot reads need elevation", admin=True),
    # ---- Hardware ----------------------------------------------------------------------------
    H("cpu_info", "Processor details",
      "Model, cores and logical processors, base clock, L2/L3 cache, socket and current load.",
      L("Get-CimInstance Win32_Processor | Select-Object Name, Manufacturer, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed, L2CacheSize, L3CacheSize, SocketDesignation, LoadPercentage, VirtualizationFirmwareEnabled")),
    H("memory_modules", "Memory modules (DIMMs)",
      "Every installed memory stick: slot, capacity, configured speed against rated speed "
      "(XMP/EXPO not applied shows up here), manufacturer and part number.",
      T("Get-CimInstance Win32_PhysicalMemory | Select-Object DeviceLocator, @{n='GB';e={[math]::Round($_.Capacity/1GB,1)}}, Speed, ConfiguredClockSpeed, Manufacturer, PartNumber")),
    H("memory_slots", "Memory slots used and maximum",
      "How many memory slots the board has, how many are used and the maximum capacity it "
      "supports -- 'can I add RAM?'.",
      L(r"""$a = Get-CimInstance Win32_PhysicalMemoryArray
$m = @(Get-CimInstance Win32_PhysicalMemory)
[pscustomobject]@{ Slots=$a.MemoryDevices; Used=$m.Count; InstalledGB=[math]::Round(($m | Measure-Object Capacity -Sum).Sum/1GB,1); MaxSupportedGB=[math]::Round($a.MaxCapacityEx/1MB,0) }""")),
    H("gpu_info", "Graphics adapters",
      "Every GPU with its driver version and date, video memory (as Windows reports it -- "
      "capped at 4 GB in this WMI class) and current resolution and refresh rate.",
      T("Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, @{n='DriverDate';e={$_.DriverDate.ToString('yyyy-MM-dd')}}, @{n='VRAM(GB)';e={[math]::Round($_.AdapterRAM/1GB,1)}}, CurrentHorizontalResolution, CurrentVerticalResolution, CurrentRefreshRate, Status")),
    H("motherboard_bios", "Motherboard and BIOS",
      "Board manufacturer, model and version, BIOS vendor, version and release date, and the "
      "system serial number (for warranty lookups).",
      L(r"""$b = Get-CimInstance Win32_BaseBoard; $bios = Get-CimInstance Win32_BIOS; $cs = Get-CimInstance Win32_ComputerSystemProduct
[pscustomobject]@{ Board="$($b.Manufacturer) $($b.Product) $($b.Version)"; BIOS="$($bios.Manufacturer) $($bios.SMBIOSBIOSVersion)"
  BIOSDate=$bios.ReleaseDate; Serial=$bios.SerialNumber; UUID=$cs.UUID; SMBIOS="$($bios.SMBIOSMajorVersion).$($bios.SMBIOSMinorVersion)" }""")),
    H("problem_devices", "Devices with problems",
      "Every device Device Manager shows with a yellow or red mark, with its error code "
      "explained (28 = no driver, 10 = cannot start, 43 = device reported a problem...).",
      r"""$codes = @{1='not configured';3='driver may be corrupted';10='cannot start';12='not enough resources';14='needs a restart';18='reinstall the drivers';19='registry configuration damaged';22='disabled';24='not present or not working';28='no driver installed';31='not working properly';32='driver disabled';37='driver failed to initialise';39='driver missing or corrupted';43='device reported a problem';45='not connected';52='driver signature could not be verified'}
$bad = Get-CimInstance Win32_PnPEntity | Where-Object { $_.ConfigManagerErrorCode -ne 0 }
if (-not $bad) { '[OK]   No device reports a problem.'; return }
$bad | ForEach-Object { $c = [int]$_.ConfigManagerErrorCode; "[WARN] $($_.Name) -- code $c ($($codes[$c]))  [$($_.PNPDeviceID)]" }"""),
    H("usb_devices", "USB devices connected",
      "USB devices present right now, with their vendor/product ids -- useful for 'what is "
      "this unknown device'.",
      T("Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object InstanceId -like 'USB*' | Sort-Object Class, FriendlyName | Select-Object Class, FriendlyName, Status, InstanceId")),
    H("monitors", "Monitors connected",
      "Each monitor's manufacturer, model, serial and year of manufacture, decoded from its "
      "EDID (WmiMonitorID).",
      T(r"""Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID -ErrorAction SilentlyContinue | ForEach-Object {
  [pscustomobject]@{ Manufacturer=-join ($_.ManufacturerName | Where-Object {$_} | ForEach-Object {[char]$_}); Model=-join ($_.UserFriendlyName | Where-Object {$_} | ForEach-Object {[char]$_})
    Serial=-join ($_.SerialNumberID | Where-Object {$_} | ForEach-Object {[char]$_}); Year=$_.YearOfManufacture; Active=$_.Active } }""")),
    H("audio_devices", "Sound devices",
      "Sound hardware Windows knows about, with status -- for 'no sound' tickets.",
      T("Get-CimInstance Win32_SoundDevice | Select-Object Name, Manufacturer, Status")),
    H("network_adapters_hw", "Network adapters (hardware)",
      "Physical network adapters with their driver, link speed, MAC address and media state.",
      T("Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Select-Object Name, InterfaceDescription, Status, LinkSpeed, MacAddress, DriverVersion, DriverDate")),
    H("battery", "Battery health",
      "Design capacity against full-charge capacity (wear), cycle count and current charge, "
      "for laptops. A desktop says it has no battery.",
      r"""$b = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue
if (-not $b) { '[INFO] No battery -- this is a desktop or the battery is not reported.'; return }
$s = Get-CimInstance -Namespace root\wmi -ClassName BatteryStaticData -ErrorAction SilentlyContinue | Select-Object -First 1
$f = Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity -ErrorAction SilentlyContinue | Select-Object -First 1
$c = Get-CimInstance -Namespace root\wmi -ClassName BatteryCycleCount -ErrorAction SilentlyContinue | Select-Object -First 1
"Charge: $($b.EstimatedChargeRemaining)%   Status: $($b.Status)"
if ($s -and $f -and $s.DesignedCapacity) { $h = [math]::Round(100 * $f.FullChargedCapacity / $s.DesignedCapacity, 1); $tag = if ($h -lt 70) {'[WARN]'} else {'[OK]  '}; "$tag Health: $h% of design capacity ($($f.FullChargedCapacity) of $($s.DesignedCapacity) mWh)" }
if ($c) { "Cycle count: $($c.CycleCount)" }""", note="the battery health classes need elevation", admin=True),
    H("bluetooth", "Bluetooth devices",
      "Paired and present Bluetooth devices and the radio itself, with status.",
      T("Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | Sort-Object FriendlyName | Select-Object FriendlyName, Status, Present")),
    H("cameras", "Cameras and imaging devices",
      "Webcams and scanners Windows sees, with status -- for 'the camera does not work in "
      "Teams'.",
      T("Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.Class -in 'Camera','Image' } | Select-Object Class, FriendlyName, Status")),
    # ---- Storage -----------------------------------------------------------------------------
    D("volumes", "Volumes and free space",
      "Every volume with file system, size, free space and health, with low space flagged.",
      r"""Get-Volume | Where-Object DriveLetter | Sort-Object DriveLetter | ForEach-Object {
  $pct = if ($_.Size) { [math]::Round(100 * $_.SizeRemaining / $_.Size, 1) } else { 0 }
  $tag = if ($pct -lt 10) { '[BAD] ' } elseif ($pct -lt 20) { '[WARN]' } else { '[OK]  ' }
  '{0} {1}:  {2,-6} {3,9:N1} GB free of {4,9:N1} GB ({5}%)  {6}  {7}' -f $tag, $_.DriveLetter, $_.FileSystem, ($_.SizeRemaining/1GB), ($_.Size/1GB), $pct, $_.HealthStatus, $_.FileSystemLabel }"""),
    D("physical_disks", "Physical disks and health",
      "Each disk's model, bus (NVMe/SATA/USB), media type, size, health and operational "
      "status, as Storage Management reports them.",
      T("Get-PhysicalDisk | Sort-Object DeviceId | Select-Object DeviceId, FriendlyName, BusType, MediaType, @{n='GB';e={[math]::Round($_.Size/1GB)}}, HealthStatus, OperationalStatus, FirmwareVersion")),
    D("disk_reliability", "Disk wear and error counters",
      "Temperature, wear (percentage used of rated endurance), power-on hours and read/write "
      "error counts per disk. Not every disk reports every counter; blanks mean 'not "
      "reported', never zero.",
      T("Get-PhysicalDisk | ForEach-Object { $r = $_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; [pscustomobject]@{ Disk=$_.FriendlyName; TempC=$r.Temperature; Wear=$r.Wear; PowerOnHours=$r.PowerOnHours; ReadErrors=$r.ReadErrorsTotal; WriteErrors=$r.WriteErrorsTotal } }"),
      note="some counters are only reported elevated", admin=True),
    D("partitions", "Partitions",
      "Every partition on every disk: type (EFI, MSR, recovery, basic), size, drive letter "
      "and offset -- what Disk Management shows, as text. Needs administrator: for a "
      "standard user Get-Partition returns NOTHING, with no error (measured 2026-10-16).",
      need_admin("Get-Partition returns an empty list for a standard user") + T("Get-Partition | Sort-Object DiskNumber, PartitionNumber | Select-Object DiskNumber, PartitionNumber, DriveLetter, Type, @{n='GB';e={[math]::Round($_.Size/1GB,2)}}, IsBoot, IsSystem, IsActive"), admin=True),
    D("disk_partition_style", "Disks: GPT or MBR",
      "Partition style per disk, boot and system flags. Windows 11 upgrades and UEFI boot "
      "need GPT.",
      T("Get-Disk | Sort-Object Number | Select-Object Number, FriendlyName, PartitionStyle, @{n='GB';e={[math]::Round($_.Size/1GB)}}, IsBoot, IsSystem, OperationalStatus")),
    D("bitlocker", "BitLocker status per volume",
      "Encryption state, method, protection on/off and key protector types for each volume.",
      need_admin("BitLocker status is only readable elevated") +
      T("Get-BitLockerVolume | Select-Object MountPoint, VolumeStatus, EncryptionMethod, EncryptionPercentage, ProtectionStatus, @{n='Protectors';e={($_.KeyProtector.KeyProtectorType) -join ', '}}"),
      admin=True),
    D("trim", "TRIM (delete notifications)",
      "Whether Windows sends TRIM to SSDs. 0 means ENABLED -- the value is a 'disable' flag.",
      "fsutil behavior query DisableDeleteNotify", kind=CMD),
    D("defrag_status", "Optimise-drives schedule and last run",
      "When each volume was last optimised (TRIM for SSDs, defrag for HDDs) and whether the "
      "scheduled task is enabled.",
      r"""Get-ScheduledTask -TaskPath '\Microsoft\Windows\Defrag\' -ErrorAction SilentlyContinue | ForEach-Object { "Scheduled task $($_.TaskName): $($_.State)"; ($_ | Get-ScheduledTaskInfo | Select-Object LastRunTime, NextRunTime | Format-List | Out-String).Trim() }"""),
    D("shadow_copies", "Volume shadow copies and restore storage",
      "Shadow storage per volume (how much space restore points may use) and the shadow "
      "copies that exist.",
      need_admin("vssadmin refuses a standard user") +
      r"""vssadmin list shadowstorage
vssadmin list shadows | Select-String 'Shadow Copy Volume|creation time|Original Volume' | ForEach-Object { $_.Line.Trim() }""",
      admin=True),
    D("restore_points", "System restore points",
      "Every restore point with its time and description, and whether System Protection is on.",
      need_admin("Get-ComputerRestorePoint returns nothing for a standard user") +
      T("Get-ComputerRestorePoint | Select-Object SequenceNumber, @{n='Created';e={$_.ConvertToDateTime($_.CreationTime)}}, Description, RestorePointType"),
      admin=True),
    D("large_folders_profile", "Largest folders in your profile",
      "The 15 biggest top-level folders under your user profile, measured -- where the space "
      "went. OneDrive files that are online-only are counted at their full size.",
      r"""Get-ChildItem $env:USERPROFILE -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
  $s = (Get-ChildItem $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
  [pscustomobject]@{ Folder=$_.Name; GB=[math]::Round($s/1GB,2) } } | Sort-Object GB -Descending | Select-Object -First 15 | Format-Table -AutoSize | Out-String -Width 220""",
      timeout=600),
    D("temp_size", "Temp folders size",
      "Size and file count of your %TEMP% and %SystemRoot%\\Temp -- the first place an admin "
      "looks when a disk fills.",
      r"""foreach ($p in $env:TEMP, "$env:SystemRoot\Temp") { $m = Get-ChildItem $p -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum
  '{0,-40} {1,10:N2} GB  {2,8} files' -f $p, ($m.Sum/1GB), $m.Count }""", timeout=300),
    D("component_store", "Component store (WinSxS) size",
      "What the WinSxS folder really uses, and whether a cleanup is recommended. Uses DISM "
      "/AnalyzeComponentStore -- READ-ONLY, but it holds the servicing lock for ~25 s.",
      need_admin("DISM refuses a standard user") +
      "Dism.exe /Online /Cleanup-Image /AnalyzeComponentStore", admin=True, timeout=600),
    D("onedrive_status", "OneDrive folders and Known Folder redirection",
      "Where Desktop, Documents and Pictures really are (OneDrive Backup redirects them) and "
      "which OneDrive accounts are set up.",
      r"""foreach ($f in 'Desktop','MyDocuments','MyPictures') { '{0,-12} {1}' -f $f, [Environment]::GetFolderPath($f) }
''
Get-ChildItem 'HKCU:\Software\Microsoft\OneDrive\Accounts' -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-ItemProperty $_.PSPath; if ($p.UserFolder) { "Account $($_.PSChildName): $($p.UserEmail)  -> $($p.UserFolder)" } }"""),
    # ---- Performance -------------------------------------------------------------------------
    P("top_cpu", "Top processes by CPU",
      "The 20 processes that used the most CPU time since they started, with their memory.",
      T("Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 Name, Id, @{n='CPU(s)';e={[math]::Round($_.CPU,1)}}, @{n='WS(MB)';e={[math]::Round($_.WorkingSet64/1MB)}}, Handles, StartTime")),
    P("top_memory", "Top processes by memory",
      "The 20 processes with the largest working set and private memory.",
      T("Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 20 Name, Id, @{n='WS(MB)';e={[math]::Round($_.WorkingSet64/1MB)}}, @{n='Private(MB)';e={[math]::Round($_.PrivateMemorySize64/1MB)}}, Handles")),
    P("cpu_now", "CPU usage right now (5-second sample)",
      "Total and per-core CPU use sampled over five seconds, plus the processor queue length "
      "-- a queue above ~2 per core means work is waiting.",
      r"""$c = Get-Counter '\Processor(_Total)\% Processor Time','\System\Processor Queue Length' -SampleInterval 1 -MaxSamples 5 -ErrorAction SilentlyContinue
$c.CounterSamples | Group-Object Path | ForEach-Object { '{0,-55} {1,8:N1}' -f $_.Name, ($_.Group | Measure-Object CookedValue -Average).Average }""",
      timeout=60),
    P("memory_now", "Memory pressure",
      "Physical memory in use, commit charge against the commit limit (what decides 'out of "
      "memory', not physical RAM alone), and the page fault rate.",
      r"""$os = Get-CimInstance Win32_OperatingSystem
$c = Get-Counter '\Memory\Committed Bytes','\Memory\Commit Limit','\Memory\Pages/sec','\Memory\Available MBytes' -ErrorAction SilentlyContinue
'Physical: {0:N1} GB used of {1:N1} GB' -f (($os.TotalVisibleMemorySize - $os.FreePhysicalMemory)/1MB), ($os.TotalVisibleMemorySize/1MB)
$c.CounterSamples | ForEach-Object { '{0,-40} {1,14:N0}' -f ($_.Path -replace '^\\\\[^\\]+',''), $_.CookedValue }"""),
    P("disk_now", "Disk activity (5-second sample)",
      "Read/write throughput, % idle time and average response time per physical disk. "
      "Response times above ~20 ms on an SSD (or 50 ms on an HDD) mean the disk is the "
      "bottleneck.",
      r"""$c = Get-Counter '\PhysicalDisk(*)\Disk Read Bytes/sec','\PhysicalDisk(*)\Disk Write Bytes/sec','\PhysicalDisk(*)\% Idle Time','\PhysicalDisk(*)\Avg. Disk sec/Transfer' -SampleInterval 1 -MaxSamples 5 -ErrorAction SilentlyContinue
$c.CounterSamples | Where-Object InstanceName -ne '_total' | Group-Object Path | ForEach-Object { '{0,-70} {1,14:N3}' -f ($_.Name -replace '^\\\\[^\\]+',''), ($_.Group | Measure-Object CookedValue -Average).Average }""",
      timeout=60),
    P("handles_threads", "Processes with the most handles",
      "Handle and thread counts per process. A process whose handle count keeps growing is "
      "leaking -- run this twice a few minutes apart.",
      T("Get-Process | Sort-Object HandleCount -Descending | Select-Object -First 20 Name, Id, HandleCount, @{n='Threads';e={$_.Threads.Count}}, @{n='WS(MB)';e={[math]::Round($_.WorkingSet64/1MB)}}")),
    P("startup_impact", "Boot and logon duration",
      "The last boot durations Windows measured (Diagnostics-Performance event 100) and the "
      "slowest components it blamed (events 101-110).",
      r"""$e = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Diagnostics-Performance/Operational'; Id=100} -MaxEvents 10 -ErrorAction SilentlyContinue
if (-not $e) { 'No boot performance records.'; return }
$e | ForEach-Object { $x = [xml]$_.ToXml(); $d = @{}; $x.Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }
  '{0}  boot {1,6:N1} s  (main path {2,6:N1} s, post-boot {3,6:N1} s)' -f $_.TimeCreated, ($d.BootTime/1000), ($d.MainPathBootTime/1000), ($d.BootPostBootTime/1000) }
''
'Components blamed for slow boots (most recent):'
Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Diagnostics-Performance/Operational'; Id=101,102,103,106,109} -MaxEvents 15 -ErrorAction SilentlyContinue | ForEach-Object { "[WARN] $($_.TimeCreated)  $($_.Message.Split([Environment]::NewLine)[0])" }"""),
    P("power_plan", "Power plan and processor limits",
      "The active power plan and its processor minimum/maximum state on AC and battery -- a "
      "maximum below 100% caps performance.",
      r"""powercfg /getactivescheme
powercfg /query SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX | Select-String 'Current (AC|DC) Power Setting Index' | ForEach-Object { 'Max processor state ' + $_.Line.Trim() }
powercfg /query SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMIN | Select-String 'Current (AC|DC) Power Setting Index' | ForEach-Object { 'Min processor state ' + $_.Line.Trim() }"""),
    P("gpu_load", "GPU engine usage (5-second sample)",
      "GPU utilisation per engine (3D, video encode/decode, copy) summed across processes -- "
      "what Task Manager's GPU graph shows.",
      r"""$c = Get-Counter '\GPU Engine(*)\Utilization Percentage' -SampleInterval 1 -MaxSamples 5 -ErrorAction SilentlyContinue
if (-not $c) { 'GPU counters are not available.'; return }
$c.CounterSamples | Group-Object { ($_.InstanceName -split '_engtype_')[-1] } | ForEach-Object { '{0,-20} {1,6:N1} %' -f $_.Name, (($_.Group | Measure-Object CookedValue -Sum).Sum / 5) } | Sort-Object""",
      timeout=60),
    P("network_throughput", "Network throughput (5-second sample)",
      "Bytes sent and received per second per adapter, sampled for five seconds.",
      r"""$c = Get-Counter '\Network Interface(*)\Bytes Received/sec','\Network Interface(*)\Bytes Sent/sec' -SampleInterval 1 -MaxSamples 5 -ErrorAction SilentlyContinue
$c.CounterSamples | Group-Object Path | ForEach-Object { '{0,-90} {1,12:N0} B/s' -f ($_.Name -replace '^\\\\[^\\]+',''), ($_.Group | Measure-Object CookedValue -Average).Average }""",
      timeout=60),
    P("reliability_index", "Reliability index (stability score)",
      "Windows' own 1-10 stability score over recent days, from the Reliability Monitor data.",
      T("Get-CimInstance Win32_ReliabilityStabilityMetrics -ErrorAction SilentlyContinue | Sort-Object TimeGenerated -Descending | Select-Object -First 14 @{n='Day';e={$_.TimeGenerated}}, @{n='Index';e={[math]::Round($_.SystemStabilityIndex,2)}}"),
      timeout=180),
    P("services_cpu", "Which services share each svchost",
      "Every svchost.exe process with the services inside it and its memory -- the way to "
      "turn 'svchost is using CPU' into a service name.",
      r"""$svc = Get-CimInstance Win32_Service -Filter "State='Running'" | Group-Object ProcessId -AsHashTable -AsString
Get-Process svchost -ErrorAction SilentlyContinue | Sort-Object WorkingSet64 -Descending | ForEach-Object {
  '{0,6}  {1,6:N0} MB  {2}' -f $_.Id, ($_.WorkingSet64/1MB), ((@($svc["$($_.Id)"]) | ForEach-Object { $_.Name }) -join ', ') }"""),
]

"""A hundred more Windows client checks across every client category. Read-only."""
from ._base import ADM, CHK, CMD, EV, L, REG, T, maker, need_admin

SY = maker("System")
HW = maker("Hardware")
DS = maker("Storage")
PF = maker("Performance")
EL = maker("Event logs")
NW = maker("Network")
SC = maker("Security")
US = maker("Users & groups")
UP = maker("Updates & servicing")
SV = maker("Services & processes")
ST = maker("Startup & tasks")
AP = maker("Applications")
PW = maker("Power")
PR = maker("Printing")
GP = maker("Group Policy")
RA = maker("Remote access")
IN = maker("Inventory")
TS = maker("Troubleshooting")

_UNINSTALL = ("'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
              "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
              "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'")

SCRIPTS = [
    # ---- System (12) -------------------------------------------------------------------------
    SY("upgrade_history", "Windows upgrade history",
       "Every feature update this installation went through (the 'Source OS' records Setup "
       "leaves), oldest first -- when it went from 23H2 to 24H2 and so on.",
       r"""$k = 'HKLM:\SYSTEM\Setup'
$rows = Get-ChildItem $k -ErrorAction SilentlyContinue | Where-Object PSChildName -like 'Source OS*' | ForEach-Object { $p = Get-ItemProperty $_.PSPath
  [pscustomobject]@{ Replaced=($_.PSChildName -replace '^Source OS \(Updated on (.*)\)$','$1'); Was="$($p.ProductName) $($p.DisplayVersion) (build $($p.CurrentBuild).$($p.UBR))" } }
if (-not $rows) { '[INFO] No feature update recorded -- this is the original installation.' } else { $rows | Format-Table -AutoSize | Out-String -Width 220 }
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'; "Now: $($cv.ProductName) $($cv.DisplayVersion) (build $($cv.CurrentBuild).$($cv.UBR))" """),
    SY("recovery_env", "Windows Recovery Environment",
       "Whether WinRE is enabled and where its image lives -- without it, 'Reset this PC' and "
       "automatic repair do not work.",
       need_admin("reagentc refuses a standard user") + "reagentc /info", admin=True),
    SY("boot_config", "Boot configuration (BCD)",
       "The boot manager and Windows loader entries: timeout, default, recovery sequence, "
       "safe-mode flags and test-signing left on.",
       need_admin("bcdedit refuses a standard user") + "bcdedit /enum", admin=True),
    SY("registry_size", "Registry size",
       "Current registry size against its maximum (Win32_Registry).",
       L("Get-CimInstance Win32_Registry | Select-Object CurrentSize, MaximumSize, Status")),
    SY("long_paths", "Long path support and 8.3 names",
       "Whether paths longer than 260 characters are allowed (LongPathsEnabled) and whether 8.3 "
       "short names are still created on the system volume.",
       REG + r"""'LongPathsEnabled: ' + (RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' LongPathsEnabled)
fsutil 8dot3name query $env:SystemDrive 2>&1""", note="fsutil may need elevation"),
    SY("wer_settings", "Windows Error Reporting settings",
       "Whether crash reporting is on, consent level, and local dump collection for crashing "
       "programs (LocalDumps).",
       REG + r"""RegShow 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting'
''; 'LocalDumps:'; RegShow 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps'"""),
    SY("storage_sense", "Storage Sense configuration",
       "Whether Storage Sense runs, how often, and what it deletes (Recycle Bin, Downloads, "
       "OneDrive content after N days).",
       REG + r"""RegShow 'HKCU:\Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy'"""),
    SY("clipboard_settings", "Clipboard history and cloud clipboard",
       "Whether clipboard history and cross-device clipboard sync are on (policy and user).",
       REG + r"""'History (user): ' + (RegVal 'HKCU:\Software\Microsoft\Clipboard' EnableClipboardHistory)
'Cloud sync (user): ' + (RegVal 'HKCU:\Software\Microsoft\Clipboard' CloudClipboardAutomaticUpload)
'Policy AllowClipboardHistory: ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' AllowClipboardHistory)
'Policy AllowCrossDeviceClipboard: ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' AllowCrossDeviceClipboard)"""),
    SY("dev_drive", "Dev Drive and ReFS volumes",
       "Dev Drive support and which volumes are Dev Drives or ReFS -- they skip some Defender "
       "scanning by design.",
       r"""fsutil devdrv query 2>&1
Get-Volume | Where-Object { $_.FileSystem -eq 'ReFS' } | Select-Object DriveLetter, FileSystemLabel, Size | Format-Table -AutoSize | Out-String -Width 220""",
       note="fsutil devdrv may need elevation"),
    SY("kernel_dma", "Kernel DMA protection",
       "Whether Kernel DMA Protection (against Thunderbolt/PCIe DMA attacks) is on, from the "
       "DMA guard policy and Device Guard data.",
       REG + r"""$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction SilentlyContinue
"Available security properties: $($dg.AvailableSecurityProperties -join ', ')  (7 = DMA protection)"
'Policy DeviceEnumerationPolicy: ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Kernel DMA Protection' DeviceEnumerationPolicy)"""),
    SY("system_uptime_history", "Uptime per boot (30 days)",
       "Every boot in the last 30 days and how long Windows stayed up before the next "
       "shutdown -- spotting a machine that is never restarted, or one that restarts daily.",
       r"""$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=6005,6006,6008; StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue | Sort-Object TimeCreated
$boot = $null
foreach ($x in $e) { if ($x.Id -eq 6005) { $boot = $x.TimeCreated } elseif ($boot) { $span = $x.TimeCreated - $boot; '{0:yyyy-MM-dd HH:mm} -> {1:yyyy-MM-dd HH:mm}  up {2:%d}d {2:hh}h {2:mm}m{3}' -f $boot, $x.TimeCreated, $span, $(if ($x.Id -eq 6008) {'  (UNEXPECTED end)'} else {''}); $boot = $null } }
if ($boot) { '{0:yyyy-MM-dd HH:mm} -> now               up {1:%d}d {1:hh}h {1:mm}m' -f $boot, ((Get-Date) - $boot) }"""),
    SY("os_install_media", "Installation type and servicing channel",
       "Whether this is a Home/Pro/Enterprise/Education install, a LTSC/IoT channel, and the "
       "composition edition. S mode is not read: reading the Code Integrity policy key from a "
       "script was blocked by Bitdefender as CMD:Heur.BZC.ZFV.Boxter (2026-10-18).",
       L(r"""$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$os = Get-CimInstance Win32_OperatingSystem
[pscustomobject]@{ EditionID=$cv.EditionID; CompositionEditionID=$cv.CompositionEditionID; ProductName=$cv.ProductName; Caption=$os.Caption; OperatingSystemSKU=$os.OperatingSystemSKU; DisplayVersion=$cv.DisplayVersion; Build="$($cv.CurrentBuild).$($cv.UBR)"; LCUVer=$cv.LCUVer; SMode='not read (see Settings > System > Activation)' }""")),
    # ---- Hardware (8) ------------------------------------------------------------------------
    HW("disk_predict_failure", "Disk failure prediction (SMART)",
       "The drive's own SMART 'predict failure' flag for each disk -- a True here means back up "
       "now.",
       need_admin("the SMART classes refuse a standard user") +
       r"""$p = Get-CimInstance -Namespace root\wmi -ClassName MSStorageDriver_FailurePredictStatus -ErrorAction SilentlyContinue
if (-not $p) { '[INFO] No disk reports SMART prediction through this interface (NVMe drives usually do not).'; return }
$p | ForEach-Object { '{0} {1}  PredictFailure={2} Reason={3}' -f $(if ($_.PredictFailure) {'[BAD] '} else {'[OK]  '}), $_.InstanceName, $_.PredictFailure, $_.Reason }""",
       admin=True),
    HW("storage_controllers", "Storage controllers and drivers",
       "SATA/NVMe/RAID controllers with their driver and version -- a vendor RAID driver vs "
       "Microsoft's inbox one explains many performance and crash issues.",
       T("Get-CimInstance Win32_PnPSignedDriver | Where-Object DeviceClass -in 'SCSIADAPTER','HDC' | Select-Object DeviceName, DriverProviderName, DriverVersion, @{n='Date';e={if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') }}}, InfName")),
    HW("chassis", "Chassis type",
       "Desktop, laptop, all-in-one, tablet... from SMBIOS, plus whether a lid / battery exist.",
       r"""$t = @{3='Desktop';4='Low-profile desktop';6='Mini tower';7='Tower';8='Portable';9='Laptop';10='Notebook';13='All-in-one';14='Sub-notebook';30='Tablet';31='Convertible';32='Detachable';35='Mini PC'}
Get-CimInstance Win32_SystemEnclosure | ForEach-Object { "Chassis: $(($_.ChassisTypes | ForEach-Object { $t[[int]$_] }) -join ', ')  Manufacturer: $($_.Manufacturer)  Serial: $($_.SerialNumber)" }"""),
    HW("input_devices", "Keyboards, mice and HID devices",
       "Keyboards, mice and other HID devices present, with status.",
       T("Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.Class -in 'Keyboard','Mouse','HIDClass' } | Sort-Object Class, FriendlyName | Select-Object Class, FriendlyName, Status")),
    HW("device_install_history", "Recent driver and device installs",
       "The last device installations from setupapi.dev.log -- which driver was installed when "
       "a device started misbehaving.",
       r"""$log = "$env:SystemRoot\INF\setupapi.dev.log"
if (-not (Test-Path $log)) { 'NOT AVAILABLE: setupapi.dev.log is not present.'; return }
Select-String -Path $log -Pattern '^>>>\s+\[(Device Install|Install Driver).*\]' -ErrorAction SilentlyContinue | Select-Object -Last 40 | ForEach-Object { $i = $_.LineNumber; $t = (Get-Content $log -TotalCount ($i + 1) | Select-Object -Last 1) -replace '^>>>\s+Section start\s+',''; '{0}  {1}' -f $t, ($_.Line -replace '^>>>\s+','') }""",
       timeout=180),
    HW("driverstore_count", "Driver store packages",
       "How many third-party driver packages the driver store holds, by class, with the oldest "
       "-- superseded packages pile up over years.",
       need_admin("pnputil prints its help text instead of the list for a standard user") +
       r"""$o = pnputil /enum-drivers
$cls = $o | Select-String 'Class Name:' | ForEach-Object { ($_.Line -split ':',2)[1].Trim() }
"Third-party driver packages: $($cls.Count)"; $cls | Group-Object | Sort-Object Count -Descending | ForEach-Object { '{0,5}  {1}' -f $_.Count, $_.Name }""",
       admin=True),
    HW("gpu_driver_age", "Graphics driver age",
       "Each GPU's driver date and how old it is.",
       CHK + r"""Get-CimInstance Win32_VideoController | ForEach-Object { $d = $_.DriverDate; $age = if ($d) { ((Get-Date) - $d).Days } else { $null }
  "[INFO] $($_.Name) -- driver $($_.DriverVersion), dated $(if ($d) { $d.ToString('yyyy-MM-dd') } else { '?' }) ($age days old)" }"""),
    HW("pci_devices", "PCI devices",
       "Every PCI device present with its class and vendor/device id.",
       T("Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object InstanceId -like 'PCI*' | Sort-Object Class | Select-Object Class, FriendlyName, @{n='VEN_DEV';e={($_.InstanceId -split '\\\\')[1] -replace '&SUBSYS.*',''}}, Status")),
    # ---- Storage (8) -------------------------------------------------------------------------
    DS("big_files_c", "Largest files on the system drive",
       "The 40 biggest files on the system drive (skipping the page, swap and hibernation "
       "files). Slow on a full disk.",
       r"""Get-ChildItem "$env:SystemDrive\" -Recurse -File -Force -ErrorAction SilentlyContinue 2>$null | Where-Object { $_.Name -notin 'pagefile.sys','swapfile.sys','hiberfil.sys' } | Sort-Object Length -Descending | Select-Object -First 40 | ForEach-Object { '{0,9:N2} GB  {1:yyyy-MM-dd}  {2}' -f ($_.Length/1GB), $_.LastWriteTime, $_.FullName }""",
       timeout=900),
    DS("recycle_bins", "Recycle Bin size per drive",
       "How much each drive's Recycle Bin holds, for every user (sizes of the $Recycle.Bin "
       "folders).",
       r"""Get-PSDrive -PSProvider FileSystem | Where-Object { Test-Path "$($_.Root)`$Recycle.Bin" } | ForEach-Object { $m = Get-ChildItem "$($_.Root)`$Recycle.Bin" -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum
  '{0}  {1,9:N2} GB  {2} files' -f $_.Root, ($m.Sum/1GB), $m.Count }""",
       note="other users' recycle bins are only counted elevated", timeout=300),
    DS("downloads_folder", "Downloads folder: size and oldest files",
       "Size of your Downloads folder and the 20 oldest, largest items in it.",
       r"""$d = (New-Object -ComObject Shell.Application).Namespace('shell:Downloads').Self.Path
$f = Get-ChildItem $d -Recurse -File -ErrorAction SilentlyContinue
'{0}: {1:N2} GB in {2} files' -f $d, (($f | Measure-Object Length -Sum).Sum/1GB), $f.Count
$f | Sort-Object LastWriteTime | Select-Object -First 20 | ForEach-Object { '{0:yyyy-MM-dd}  {1,9:N1} MB  {2}' -f $_.LastWriteTime, ($_.Length/1MB), $_.Name }""",
       timeout=300),
    DS("windows_old", "Windows.old and upgrade leftovers",
       "Size of Windows.old, $WINDOWS.~BT and $WINDOWS.~WS -- previous-installation files a "
       "feature update leaves for rollback.",
       r"""foreach ($p in 'Windows.old','$WINDOWS.~BT','$WINDOWS.~WS','ESD') { $f = Join-Path "$env:SystemDrive\" $p
  if (Test-Path $f) { $s = (Get-ChildItem $f -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum; '[INFO] {0,-14} {1,8:N2} GB' -f $p, ($s/1GB) } else { "[OK]   $p -- not present" } }""",
       timeout=600),
    DS("dirty_bit", "File system dirty bit",
       "Whether NTFS flagged a volume as dirty (chkdsk will run at next boot).",
       need_admin("fsutil dirty refuses a standard user") +
       r"""Get-Volume | Where-Object { $_.DriveLetter -and $_.FileSystem -eq 'NTFS' } | ForEach-Object { $r = fsutil dirty query "$($_.DriveLetter):" 2>&1; if ($r -match 'is Dirty') { "[WARN] $r" } else { "[OK]   $r" } }""",
       admin=True),
    DS("swap_files", "Page, swap and hibernation files",
       "Sizes of pagefile.sys, swapfile.sys (Store apps) and hiberfil.sys on each drive.",
       r"""$found = 0
Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Root -match '^[A-Z]:\\$' } | ForEach-Object { $root = $_.Root
  Get-ChildItem $root -Force -File -Filter *.sys -ErrorAction SilentlyContinue | Where-Object Name -in 'pagefile.sys','swapfile.sys','hiberfil.sys' | ForEach-Object { $found++; '{0,-4} {1,-13} {2,8:N2} GB' -f $root, $_.Name, ($_.Length/1GB) } }
if (-not $found) { '[INFO] No page, swap or hibernation file found at a drive root.' }"""),
    DS("do_cache", "Delivery Optimization cache",
       "The Delivery Optimization cache folder and its size.",
       r"""$c = Get-DeliveryOptimizationPerfSnap -ErrorAction SilentlyContinue
if ($c) { 'Cache size: {0:N2} GB  (files: {1})' -f ($c.CacheSizeBytes/1GB), $c.Files } else { '[INFO] Delivery Optimization did not report its cache.' }""",
       note="the cache figures need elevation", admin=True),
    DS("volume_free_trend", "Free space with low-space thresholds",
       "Free space per volume against two thresholds (10% / 15 GB) -- the check fleet "
       "monitoring runs.",
       r"""Get-Volume | Where-Object { $_.DriveLetter -and $_.Size } | ForEach-Object { $pct = 100 * $_.SizeRemaining / $_.Size; $gb = $_.SizeRemaining / 1GB
  $tag = if ($pct -lt 10 -or $gb -lt 15) { '[BAD] ' } elseif ($pct -lt 20) { '[WARN]' } else { '[OK]  ' }
  '{0} {1}:  {2,7:N1} GB free  ({3,4:N1}%)' -f $tag, $_.DriveLetter, $gb, $pct }"""),
    # ---- Performance (6) ---------------------------------------------------------------------
    PF("top_disk_io", "Processes doing the most disk I/O",
       "Disk bytes read and written per process over five seconds.",
       r"""$c = Get-Counter '\Process(*)\IO Data Bytes/sec' -SampleInterval 1 -MaxSamples 5 -ErrorAction SilentlyContinue
$c.CounterSamples | Where-Object { $_.InstanceName -notin '_total','idle' } | Group-Object InstanceName | ForEach-Object { [pscustomobject]@{ Process=$_.Name; MBps=[math]::Round((($_.Group | Measure-Object CookedValue -Average).Average)/1MB,2) } } | Sort-Object MBps -Descending | Select-Object -First 15 | Format-Table -AutoSize | Out-String -Width 220""",
       timeout=60),
    PF("pool_memory", "Kernel pool memory",
       "Paged and non-paged pool use -- a driver leaking non-paged pool is a classic cause of "
       "'out of memory' with free RAM.",
       r"""Get-Counter '\Memory\Pool Nonpaged Bytes','\Memory\Pool Paged Bytes','\Memory\System Cache Resident Bytes','\Memory\Modified Page List Bytes' | ForEach-Object { $_.CounterSamples } | ForEach-Object { '{0,-50} {1,10:N0} MB' -f ($_.Path -replace '^\\\\[^\\]+',''), ($_.CookedValue/1MB) }"""),
    PF("dpc_interrupts", "Interrupts and DPC time (5 s)",
       "% time spent in interrupts and deferred procedure calls -- high values mean a driver is "
       "hogging the CPU (audio crackles, stutter).",
       r"""$c = Get-Counter '\Processor(_Total)\% Interrupt Time','\Processor(_Total)\% DPC Time','\System\Context Switches/sec' -SampleInterval 1 -MaxSamples 5 -ErrorAction SilentlyContinue
$c.CounterSamples | Group-Object Path | ForEach-Object { $a = ($_.Group | Measure-Object CookedValue -Average).Average; '{0} {1,-50} {2,12:N2}' -f $(if ($_.Name -match 'Interrupt|DPC' -and $a -gt 5) {'[WARN]'} else {'[OK]  '}), ($_.Name -replace '^\\\\[^\\]+',''), $a }""",
       timeout=60),
    PF("memory_compression", "Memory compression",
       "The Memory Compression process size and whether memory compression is enabled.",
       r"""$p = Get-Process 'Memory Compression' -ErrorAction SilentlyContinue
"Memory Compression working set: $(if ($p) { '{0:N0} MB' -f ($p.WorkingSet64/1MB) } else { 'not visible (needs elevation)' })"
try { (Get-MMAgent -ErrorAction Stop | Format-List | Out-String).Trim() } catch { 'Get-MMAgent needs administrator rights.' }"""),
    PF("visual_settings", "Visual effects and GPU scheduling",
       "Visual effects mode, hardware-accelerated GPU scheduling and Game Mode.",
       REG + r"""'VisualFXSetting (0 auto,1 appearance,2 performance,3 custom): ' + (RegVal 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' VisualFXSetting)
'HwSchMode (2 = GPU scheduling on): ' + (RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' HwSchMode)
'Game Mode (AutoGameModeEnabled): ' + (RegVal 'HKCU:\Software\Microsoft\GameBar' AutoGameModeEnabled)"""),
    PF("thread_count_top", "Processes with the most threads",
       "Thread count per process -- runaway thread creation shows here first.",
       T("Get-Process | Sort-Object { $_.Threads.Count } -Descending | Select-Object -First 15 Name, Id, @{n='Threads';e={$_.Threads.Count}}, HandleCount")),
    # ---- Event logs (12) ---------------------------------------------------------------------
    EL("logon_logoff_winlogon", "Sign-in and sign-out times (Winlogon)",
       "Customer Experience Winlogon 7001 (logon) and 7002 (logoff) records -- when this PC "
       "was used, readable without admin.",
       EV + "EvList @{LogName='System'; ProviderName='Microsoft-Windows-Winlogon'; Id=7001,7002} 14 80"),
    EL("service_crashes", "Service crashes grouped",
       "Services that terminated unexpectedly (7031, 7034) in 30 days, grouped.",
       EV + "EvSum @{LogName='System'; ProviderName='Service Control Manager'; Id=7031,7034} 30"),
    EL("msi_installs", "Software installs and removals (MSI)",
       "MsiInstaller events: products installed (1033/11707), removed (1034/11724) and failed "
       "(11708) in 30 days.",
       EV + "EvList @{LogName='Application'; ProviderName='MsiInstaller'; Id=1033,1034,11707,11708,11724} 30 80"),
    EL("defender_events", "Defender events summary",
       "Defender's operational log grouped by event -- scans, detections, updates, config "
       "changes.",
       EV + "EvSum @{LogName='Microsoft-Windows-Windows Defender/Operational'} 7"),
    EL("time_changes", "System time changes",
       "When the clock was changed and by whom (Kernel-General 1) -- clock jumps break "
       "Kerberos and log timelines.",
       EV + "EvList @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-General'; Id=1} 30 40"),
    EL("gp_errors", "Group Policy errors",
       "Group Policy processing errors (1085, 1096, 1129, 1058, 1030) in 7 days.",
       EV + "EvList @{LogName='System'; ProviderName='Microsoft-Windows-GroupPolicy'; Level=1,2,3} 7 60"),
    EL("log_cleared_any", "Event logs that were cleared",
       "Any log cleared (System 104, Security 1102) in 90 days -- evidence being erased.",
       EV + r"""EvList @{LogName='System'; Id=104} 90 40
""" + "try { Get-WinEvent -FilterHashtable @{LogName='Security'; Id=1102} -MaxEvents 20 -ErrorAction Stop | ForEach-Object { \"[BAD]  $($_.TimeCreated)  Security log cleared\" } } catch { if ($_.Exception.Message -match 'No events') { '[OK]   Security log: never cleared in its retained history.' } else { 'NOTE: the Security log needs administrator rights.' } }"),
    EL("kernel_boot_errors", "Boot-time errors",
       "Errors and warnings logged in the first minutes after the last boot -- drivers and "
       "services that fail at startup.",
       r"""$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2,3; StartTime=$boot; EndTime=$boot.AddMinutes(10)} -ErrorAction SilentlyContinue | ForEach-Object { '{0} {1:HH:mm:ss} {2,5} {3,-34} {4}' -f $(if ($_.Level -le 2) {'[BAD] '} else {'[WARN]'}), $_.TimeCreated, $_.Id, $_.ProviderName, (($_.Message -split "`r?`n")[0]) }
"Boot at $boot" """),
    EL("app_errors_grouped", "Application log errors grouped (30 days)",
       "Every error source in the Application log over 30 days with counts.",
       EV + "EvSum @{LogName='Application'; Level=1,2} 30"),
    EL("system_errors_grouped", "System log errors grouped (30 days)",
       "Every error source in the System log over 30 days with counts.",
       EV + "EvSum @{LogName='System'; Level=1,2} 30"),
    EL("bits_errors", "Background transfer (BITS) errors",
       "BITS client errors -- downloads by Windows Update, Office and Store that failed.",
       EV + "EvList @{LogName='Microsoft-Windows-Bits-Client/Operational'; Level=2,3} 7 40"),
    EL("ntfs_events", "NTFS and volume events (90 days)",
       "NTFS self-healing, corruption and volume events over 90 days.",
       EV + "EvSum @{LogName='System'; ProviderName='Ntfs','Microsoft-Windows-Ntfs'} 90"),
    # ---- Network (12) ------------------------------------------------------------------------
    NW("wifi_survey", "Nearby Wi-Fi networks",
       "Every Wi-Fi network in range with BSSID, signal, channel and security -- channel "
       "congestion and a rogue access point with your SSID show here.",
       "netsh wlan show networks mode=bssid", kind=CMD),
    NW("adapter_advanced", "Adapter advanced properties",
       "Driver settings per physical adapter: speed/duplex, energy-efficient Ethernet, wake on "
       "LAN, jumbo frames, offloads.",
       T("Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Get-NetAdapterAdvancedProperty -ErrorAction SilentlyContinue | Select-Object Name, DisplayName, DisplayValue")),
    NW("port_proxy", "Port forwarding rules (portproxy)",
       "netsh interface portproxy rules -- silently forward a local port elsewhere; attackers "
       "use them to tunnel.",
       r"""$o = netsh interface portproxy show all
if (-not ($o | Where-Object { $_ -match '\d+\.\d+|\*' })) { '[OK]   No port forwarding rules.' } else { $o | ForEach-Object { "[WARN] $_" } }"""),
    NW("smb_client_config", "SMB client security settings",
       "Client-side SMB signing, encryption and insecure guest logons.",
       L("Get-SmbClientConfiguration | Select-Object RequireSecuritySignature, EnableSecuritySignature, EnableInsecureGuestLogons, EncryptionCiphers, EnableMultiChannel")),
    NW("netbios_cache", "NetBIOS name cache and names",
       "Local NetBIOS names and the remote name cache (nbtstat).",
       r"""nbtstat -n; nbtstat -c"""),
    NW("network_providers", "Network provider order",
       "The order Windows tries network providers (SMB, WebDAV, RDP, VirtualBox...) when "
       "opening a UNC path -- a slow provider first makes every \\\\server path slow.",
       REG + r"""(RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\NetworkProvider\Order' ProviderOrder) -split ','"""),
    NW("metered", "Metered connections",
       "Whether each network profile is metered (updates and sync are throttled on metered "
       "networks).",
       r"""$p = Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\NetworkList\DefaultMediaCost' -ErrorAction SilentlyContinue
(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\NetworkList\DefaultMediaCost' -ErrorAction SilentlyContinue).PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { '{0,-10} {1}' -f $_.Name, $(if ($_.Value -eq 2) {'metered by default'} else {'not metered'}) }"""),
    NW("firewall_logging", "Firewall logging",
       "Whether the firewall logs dropped and allowed connections, and where.",
       T("Get-NetFirewallProfile | Select-Object Name, LogBlocked, LogAllowed, LogFileName, LogMaxSizeKilobytes")),
    NW("firewall_log_drops", "Firewall log: most dropped",
       "Top source addresses and ports in the firewall's drop log, when logging is on.",
       r"""$f = [Environment]::ExpandEnvironmentVariables((Get-NetFirewallProfile -Name Public).LogFileName)
if (-not (Test-Path $f)) { "[INFO] No firewall log at $f -- logging of drops is off."; return }
$rows = Get-Content $f -Tail 20000 -ErrorAction SilentlyContinue | Where-Object { $_ -match ' DROP ' } | ForEach-Object { $p = $_ -split ' '; "$($p[2]) $($p[4]) -> $($p[5]):$($p[7])" }
$rows | Group-Object | Sort-Object Count -Descending | Select-Object -First 25 | ForEach-Object { '{0,6}  {1}' -f $_.Count, $_.Name }""",
       note="the firewall log is only readable elevated"),
    NW("hotspot_ics", "Mobile hotspot and Internet Connection Sharing",
       "Whether Internet Connection Sharing or the mobile hotspot is active -- this PC routing "
       "other devices' traffic.",
       r"""$s = Get-Service SharedAccess,icssvc -ErrorAction SilentlyContinue | Select-Object Name, Status, StartType
$s | Format-Table -AutoSize | Out-String -Width 220"""),
    NW("ncsi_settings", "Network connectivity indicator settings",
       "Where Windows checks for internet access (NCSI probe host) and whether active probing "
       "is disabled -- a corporate proxy breaking this shows 'No internet' with working "
       "internet.",
       REG + r"""RegShow 'HKLM:\SYSTEM\CurrentControlSet\Services\NlaSvc\Parameters\Internet'"""),
    NW("dns_suffix", "DNS suffixes and registration",
       "Primary DNS suffix, the search list and per-adapter registration settings.",
       r"""Get-DnsClientGlobalSetting | Format-List | Out-String -Width 220
Get-DnsClient | Where-Object { $_.ConnectionSpecificSuffix -or $_.RegisterThisConnectionsAddress } | Select-Object InterfaceAlias, ConnectionSpecificSuffix, RegisterThisConnectionsAddress | Format-Table -AutoSize | Out-String -Width 220"""),
    # ---- Security (14) -----------------------------------------------------------------------
    SC("bits_jobs", "BITS jobs (all users)",
       "Background Intelligent Transfer jobs -- legitimate updaters use them, and so does "
       "malware that wants to download and persist.",
       need_admin("other users' BITS jobs are only listed elevated") +
       r"""$j = Get-BitsTransfer -AllUsers -ErrorAction SilentlyContinue
if (-not $j) { '[OK]   No BITS jobs.'; return }
$j | ForEach-Object { '[INFO] {0,-30} {1,-12} {2}  -> {3}' -f $_.DisplayName, $_.JobState, $_.OwnerAccount, (($_.FileList | Select-Object -First 1).RemoteName) }""",
       admin=True),
    SC("accessibility_backdoor", "Accessibility tools integrity",
       "Signatures of sethc, utilman, osk, magnify, narrator and displayswitch -- replacing one "
       "with cmd.exe gives a SYSTEM shell at the sign-in screen.",
       r"""foreach ($n in 'sethc.exe','utilman.exe','osk.exe','Magnify.exe','Narrator.exe','DisplaySwitch.exe','AtBroker.exe') { $p = Join-Path "$env:SystemRoot\System32" $n
  if (-not (Test-Path $p)) { "[INFO] $n -- not present"; continue }
  $s = Get-AuthenticodeSignature $p; $v = (Get-Item $p).VersionInfo
  $ok = $s.Status -eq 'Valid' -and $v.OriginalFilename -notmatch '^cmd|^powershell|^explorer'
  '{0} {1,-18} signature {2,-10} original name {3}' -f $(if ($ok) {'[OK]  '} else {'[BAD] '}), $n, $s.Status, $v.OriginalFilename }"""),
    SC("cached_logons", "Cached domain logons",
       "How many domain logons Windows caches for offline sign-in (CachedLogonsCount) -- "
       "hashes an attacker can crack.",
       REG + CHK + r"""$c = RegVal 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' CachedLogonsCount
Chk ([int]$c -le 4) 'Cached logons' "CachedLogonsCount=$c (default 10)" """),
    SC("anonymous_restrictions", "Anonymous access restrictions",
       "RestrictAnonymous, RestrictAnonymousSAM and EveryoneIncludesAnonymous -- what an "
       "unauthenticated user can enumerate.",
       REG + CHK + r"""$k = 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa'
Chk ((RegVal $k RestrictAnonymousSAM) -eq 1) 'RestrictAnonymousSAM' "$(RegVal $k RestrictAnonymousSAM)"
Chk ((RegVal $k RestrictAnonymous) -ge 1) 'RestrictAnonymous' "$(RegVal $k RestrictAnonymous)"
Chk ((RegVal $k EveryoneIncludesAnonymous) -ne 1) 'EveryoneIncludesAnonymous off' "$(RegVal $k EveryoneIncludesAnonymous)" """),
    SC("ntlm_settings", "NTLM restrictions and auditing",
       "NTLM outgoing/incoming restrictions and audit settings (MSV1_0) -- the first step of "
       "an NTLM phase-out.",
       REG + r"""RegShow 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa\MSV1_0'"""),
    SC("kerberos_settings", "Kerberos encryption types",
       "Which Kerberos encryption types are allowed by policy -- RC4 still allowed is a "
       "Kerberoasting risk.",
       REG + r"""$v = RegVal 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\Kerberos\Parameters' SupportedEncryptionTypes
if ($null -eq $v) { '[INFO] SupportedEncryptionTypes not set by policy (OS defaults).' } else { $m = @{1='DES-CBC-CRC';2='DES-CBC-MD5';4='RC4';8='AES128';16='AES256'}; $on = $m.Keys | Where-Object { $v -band $_ } | ForEach-Object { $m[$_] }; '{0} {1}' -f $(if ($v -band 4) {'[WARN]'} else {'[OK]  '}), ($on -join ', ') }"""),
    SC("amsi_providers", "AMSI providers and PowerShell language mode",
       "Antimalware Scan Interface providers registered (which AV scans scripts) and the "
       "current PowerShell language mode.",
       r"""Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\AMSI\Providers' -ErrorAction SilentlyContinue | ForEach-Object { $c = $_.PSChildName; $n = (Get-ItemProperty "Registry::HKEY_CLASSES_ROOT\CLSID\$c" -ErrorAction SilentlyContinue).'(default)'; "[INFO] AMSI provider $c  $n" }
"Language mode: $($ExecutionContext.SessionState.LanguageMode)" """),
    SC("wsh_status", "Windows Script Host",
       "Whether Windows Script Host (wscript/cscript, .vbs/.js) is enabled -- disabling it "
       "blocks a common email-attachment attack.",
       REG + CHK + r"""$m = RegVal 'HKLM:\SOFTWARE\Microsoft\Windows Script Host\Settings' Enabled; $u = RegVal 'HKCU:\SOFTWARE\Microsoft\Windows Script Host\Settings' Enabled
Chk $(if ($m -eq 0 -or $u -eq 0) { $true } else { $null }) 'Windows Script Host disabled' "machine=$m user=$u (absent = enabled)" """),
    SC("office_macros", "Office macro and internet-content settings",
       "Per-application VBA warning level and 'block macros from the internet' for Word, Excel "
       "and PowerPoint (policy and user).",
       r"""foreach ($app in 'Word','Excel','PowerPoint') { foreach ($root in 'HKCU:\Software\Policies\Microsoft\Office\16.0','HKCU:\Software\Microsoft\Office\16.0') { $k = "$root\$app\Security"
  $p = Get-ItemProperty $k -ErrorAction SilentlyContinue; if ($p) { '{0,-10} {1,-48} VBAWarnings={2} blockcontentexecutionfrominternet={3}' -f $app, ($root -replace '^HKCU:\\Software\\',''), $p.VBAWarnings, $p.blockcontentexecutionfrominternet } } }
'(absent = Office defaults: macros from the internet are blocked since 2022)'"""),
    SC("mark_of_web", "Mark of the Web handling",
       "Whether Windows records the download zone on files (SaveZoneInformation) -- turning it "
       "off disables SmartScreen and Office's protected view for downloads.",
       REG + CHK + r"""$v = RegVal 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Policies\Attachments' SaveZoneInformation
Chk ($v -ne 1) 'Zone information kept on downloads' "SaveZoneInformation=$v (1 = DISABLED, absent = on)" """),
    SC("admin_shares", "Administrative shares",
       "Whether the hidden C$/ADMIN$ shares are created (AutoShareWks) and exist now.",
       REG + r"""'AutoShareWks: ' + (RegVal 'HKLM:\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters' AutoShareWks) + ' (absent = created)'
Get-SmbShare -Special $true -ErrorAction SilentlyContinue | Select-Object Name, Path | Format-Table -AutoSize | Out-String -Width 220"""),
    SC("remote_registry", "Remote Registry and remote management services",
       "Remote Registry, WinRM, RemoteAccess and SSH server service states -- every way in "
       "besides RDP.",
       CHK + r"""foreach ($n in 'RemoteRegistry','WinRM','RemoteAccess','sshd','TermService','SNMP') { $s = Get-Service $n -ErrorAction SilentlyContinue
  if (-not $s) { "[INFO] $n -- not installed"; continue }
  Chk ($s.Status -ne 'Running') $n "$($s.Status), $($s.StartType)" }"""),
    SC("defender_quarantine", "Defender quarantine",
       "Items Defender has in quarantine (MpCmdRun -Restore -ListAll, read-only listing).",
       need_admin("the quarantine is only readable elevated") +
       r"""$m = Get-ChildItem "$env:ProgramData\Microsoft\Windows Defender\Platform" -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
$exe = if ($m) { Join-Path $m.FullName 'MpCmdRun.exe' } else { "$env:ProgramFiles\Windows Defender\MpCmdRun.exe" }
& $exe -Restore -ListAll""", admin=True),
    SC("lsass_protection", "LSASS protection and credential dumping defences",
       "RunAsPPL, Credential Guard, the ASR rule that blocks LSASS credential theft and WDigest "
       "-- everything between an attacker and the passwords in memory.",
       REG + CHK + ADM + r"""$l = 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa'
Chk ((RegVal $l RunAsPPL) -in 1,2) 'LSA protection (RunAsPPL)' "$(RegVal $l RunAsPPL)"
$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction SilentlyContinue
Chk ($dg.SecurityServicesRunning -contains 1) 'Credential Guard running' "$($dg.SecurityServicesRunning -join ',')"
$p = Get-MpPreference -ErrorAction SilentlyContinue; $i = [array]::IndexOf(@($p.AttackSurfaceReductionRules_Ids), '9e6c4e1f-7d60-472f-ba1a-a39ef669e4b2')
Chk $(if ($i -ge 0) { @($p.AttackSurfaceReductionRules_Actions)[$i] -eq 1 } else { $false }) 'ASR: block credential stealing from LSASS' $(if ($i -ge 0) { "action $(@($p.AttackSurfaceReductionRules_Actions)[$i])" } else { 'not configured' })
Chk ((RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest' UseLogonCredential) -ne 1) 'WDigest off' "$(RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest' UseLogonCredential)" """),
    # ---- Users & groups (5) ------------------------------------------------------------------
    US("old_passwords", "Local accounts with old passwords",
       "Enabled local accounts whose password is over a year old, or was never set.",
       CHK + r"""Get-LocalUser | Where-Object Enabled | ForEach-Object { $age = if ($_.PasswordLastSet) { ((Get-Date) - $_.PasswordLastSet).Days } else { $null }
  Chk $(if ($null -eq $age) { $false } else { $age -lt 365 }) $_.Name $(if ($null -eq $age) { 'password never set' } else { "$age days old" }) }"""),
    US("accounts_created", "Local accounts created or changed (Security log)",
       "4720 created, 4722 enabled, 4724 password reset, 4726 deleted, 4732 added to a group -- "
       "90 days.",
       need_admin("the Security log is only readable elevated") + EV +
       "EvList @{LogName='Security'; Id=4720,4722,4724,4726,4732,4733} 90 80", admin=True),
    US("profile_sizes", "User profile sizes",
       "Disk space used by each profile folder.",
       r"""Get-CimInstance Win32_UserProfile | Where-Object { -not $_.Special -and (Test-Path $_.LocalPath) } | ForEach-Object { $s = (Get-ChildItem $_.LocalPath -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum; '{0,9:N2} GB  {1}' -f ($s/1GB), $_.LocalPath }""",
       note="other users' profiles are only measured elevated", timeout=900),
    US("admins_last_logon", "Administrators and their last logon",
       "Every member of the local Administrators group with its last logon (local accounts).",
       r"""Get-LocalGroupMember -SID S-1-5-32-544 | ForEach-Object { $u = if ($_.PrincipalSource -eq 'Local') { Get-LocalUser -SID $_.SID -ErrorAction SilentlyContinue }; '{0,-30} {1,-16} enabled={2}  last logon={3}' -f $_.Name, $_.PrincipalSource, $u.Enabled, $u.LastLogon }"""),
    US("sessions_detail", "Sessions and their processes",
       "Each logon session with the user and process count -- disconnected sessions keep "
       "using memory.",
       need_admin("Get-Process -IncludeUserName throws for a standard user") + r"""Get-Process -IncludeUserName -ErrorAction SilentlyContinue | Group-Object SessionId, UserName | Sort-Object Name | ForEach-Object { '{0,-40} {1,4} processes  {2,8:N0} MB' -f $_.Name, $_.Count, (($_.Group | Measure-Object WorkingSet64 -Sum).Sum/1MB) }
if (-not $?) { 'Process owners are only visible elevated.' }""",
       note="process owners are only shown elevated", admin=True),
    # ---- Updates & servicing (6) -------------------------------------------------------------
    UP("last_update_times", "Last update search and install",
       "When Windows Update last searched successfully and last installed something.",
       r"""$r = (New-Object -ComObject Microsoft.Update.AutoUpdate).Results
"Last search success : $($r.LastSearchSuccessDate)"
"Last install success: $($r.LastInstallationSuccessDate)"
$h = Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1
"Newest hotfix       : $($h.HotFixID) on $($h.InstalledOn)" """),
    UP("patch_age", "How current is this PC (patch age)",
       "Days since the newest installed update -- more than ~45 days means a missed Patch "
       "Tuesday.",
       CHK + r"""$h = Get-HotFix | Where-Object InstalledOn | Sort-Object InstalledOn -Descending | Select-Object -First 1
$age = ((Get-Date) - $h.InstalledOn).Days; Chk ($age -le 45) 'Newest update' "$($h.HotFixID), $age days ago" """),
    UP("store_autoupdate", "Microsoft Store automatic updates",
       "Whether Store apps update automatically (policy AutoDownload and the user setting).",
       REG + r"""'Policy AutoDownload (2 = off, 4 = on): ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore' AutoDownload)
'Policy DisableStoreApps: ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore' DisableStoreApps)"""),
    UP("driver_update_policy", "Drivers through Windows Update",
       "Whether driver updates are excluded from Windows Update and device metadata downloads "
       "are blocked.",
       REG + r"""'ExcludeWUDriversInQualityUpdate: ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate' ExcludeWUDriversInQualityUpdate)
'SearchOrderConfig (0 = never from WU): ' + (RegVal 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\DriverSearching' SearchOrderConfig)
'PreventDeviceMetadataFromNetwork: ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Device Metadata' PreventDeviceMetadataFromNetwork)"""),
    UP("pending_packages", "Packages pending install or removal",
       "Servicing packages waiting for a restart (Install Pending / Uninstall Pending).",
       need_admin("Get-WindowsPackage needs elevation") +
       r"""$p = Get-WindowsPackage -Online | Where-Object PackageState -match 'Pending'
if (-not $p) { '[OK]   No package is pending.'; return }
$p | ForEach-Object { "[WARN] $($_.PackageName) -- $($_.PackageState)" }""", admin=True, timeout=300),
    UP("update_errors_decoded", "Recent update failures decoded",
       "Failed updates from the WU history with the error code and its known meaning.",
       r"""$known = @{ '0x80070002'='files missing / interrupted download'; '0x800F0922'='not enough System Reserved space or VPN blocking'; '0x80073712'='component store corrupted (run DISM RestoreHealth)'; '0x8024402C'='proxy / DNS cannot reach WU'; '0x80070005'='access denied'; '0x800F081F'='source files not found'; '0x80240034'='download failed'; '0x8007000D'='corrupt data in update' }
$s = New-Object -ComObject Microsoft.Update.Session; $q = $s.CreateUpdateSearcher(); $n = $q.GetTotalHistoryCount()
$f = if ($n) { $q.QueryHistory(0, [Math]::Min(200, $n)) | Where-Object { $_.ResultCode -in 4,5 } } else { @() }
if (-not $f) { '[OK]   No failed update in recent history.'; return }
$f | ForEach-Object { $c = '0x{0:X8}' -f $_.HResult; '[BAD]  {0:yyyy-MM-dd}  {1}  {2}  {3}' -f $_.Date, $c, $known[$c], $_.Title }""",
       timeout=180),
    # ---- Services & processes (5) ------------------------------------------------------------
    SV("services_user_accounts", "Services running as a user account",
       "Services that log on as a named user (not LocalSystem/Network/Local Service) -- their "
       "passwords are stored as LSA secrets and break when the password changes.",
       r"""$s = Get-CimInstance Win32_Service | Where-Object { $_.StartName -and $_.StartName -notmatch '^(LocalSystem|NT AUTHORITY\\|NT SERVICE\\)' }
if (-not $s) { '[OK]   No service runs as a user account.'; return }
$s | ForEach-Object { "[INFO] $($_.Name) as $($_.StartName) ($($_.State))" }"""),
    SV("services_new", "Services installed recently (30 days)",
       "Service Control Manager 7045 records: every service and driver installed in 30 days.",
       EV + "EvList @{LogName='System'; ProviderName='Service Control Manager'; Id=7045} 30 80"),
    SV("processes_elevated", "Elevated processes",
       "Processes running with an elevated (High/System) token in your session.",
       need_admin("Get-Process -IncludeUserName throws for a standard user") + r"""Get-Process -IncludeUserName -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq (Get-Process -Id $PID).SessionId -and $_.UserName -match [regex]::Escape($env:USERNAME) } | ForEach-Object { try { $null = $_.Handle; '{0,-30} {1}' -f $_.ProcessName, $_.Id } catch { } } | Select-Object -First 50""",
       note="only meaningful elevated", admin=True),
    SV("service_failures_config", "Services with no recovery action",
       "Automatic services whose recovery is 'take no action' -- they stay down after a crash.",
       r"""$none = Get-CimInstance Win32_Service -Filter "StartMode='Auto'" | ForEach-Object { $q = sc.exe qfailure $_.Name 2>$null; if (-not ($q -match 'RESTART|REBOOT|RUN PROCESS')) { $_.Name } } | Sort-Object
if (-not $none) { '[OK]   Every automatic service has a recovery action.' } else { $none | ForEach-Object { "[INFO] $_ -- no recovery action" } }""",
       timeout=240),
    SV("process_modules_odd", "DLLs loaded from user folders",
       "DLLs loaded into running processes from Temp, AppData or Downloads -- DLL hijacking "
       "and side-loading leave this trace.",
       r"""Get-Process | ForEach-Object { $p = $_; try { $p.Modules | Where-Object { $_.FileName -match '\\(Temp|Downloads)\\|\\AppData\\(Local|Roaming)\\(?!Microsoft\\|Programs\\)' } | ForEach-Object { '[WARN] {0,-24} {1}' -f $p.ProcessName, $_.FileName } } catch { } } | Sort-Object -Unique | Select-Object -First 80""",
       note="other users' processes are skipped", timeout=300),
    # ---- Startup & tasks (6) -----------------------------------------------------------------
    ST("tasks_next_24h", "Tasks that will run in the next 24 hours",
       "Enabled scheduled tasks with a next run time in the next day.",
       r"""Get-ScheduledTask | Where-Object State -ne 'Disabled' | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue; if ($i.NextRunTime -and $i.NextRunTime -lt (Get-Date).AddDays(1)) { [pscustomobject]@{ Next=$i.NextRunTime; Task=$_.TaskPath + $_.TaskName } } } | Sort-Object Next | Format-Table -AutoSize | Out-String -Width 220""",
       timeout=300),
    ST("logon_scripts", "Logon scripts",
       "Scripts that run at sign-in: UserInitMprLogonScript, GPO logon scripts and the "
       "account's own logon script.",
       REG + r"""'UserInitMprLogonScript: ' + (RegVal 'HKCU:\Environment' UserInitMprLogonScript)
Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Group Policy\Scripts' -Recurse -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-ItemProperty $_.PSPath; if ($p.Script) { "GPO script: $($p.Script) $($p.Parameters)" } }"""),
    ST("startup_folders_all", "Startup folders for all users",
       "Shortcuts and files in every user's Startup folder and the common one.",
       r"""Get-ChildItem "$env:SystemDrive\Users\*\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\*" -Force -ErrorAction SilentlyContinue | ForEach-Object { "[INFO] $($_.FullName)" }
Get-ChildItem "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp\*" -Force -ErrorAction SilentlyContinue | ForEach-Object { "[INFO] $($_.FullName)" }""",
       note="other users' folders are only readable elevated"),
    ST("office_addins", "Office add-ins",
       "COM add-ins registered for Outlook, Word, Excel and PowerPoint with their load "
       "behaviour -- slow Outlook starts are usually one of these.",
       r"""foreach ($app in 'Outlook','Word','Excel','PowerPoint') { foreach ($root in 'HKCU:\Software\Microsoft\Office','HKLM:\SOFTWARE\Microsoft\Office','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Office') {
  Get-ChildItem "$root\$app\Addins" -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-ItemProperty $_.PSPath; '{0,-10} {1,-45} LoadBehavior={2}  {3}' -f $app, $_.PSChildName, $p.LoadBehavior, $p.FriendlyName } } }"""),
    ST("run_keys_all_users", "Run keys of every loaded user",
       "Run entries under every loaded user hive (HKEY_USERS) -- other accounts' autostarts.",
       r"""Get-ChildItem Registry::HKEY_USERS -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^S-1-5-21-[\d-]+$' } | ForEach-Object { $sid = $_.PSChildName
  $p = Get-ItemProperty "Registry::HKEY_USERS\$sid\Software\Microsoft\Windows\CurrentVersion\Run" -ErrorAction SilentlyContinue
  if ($p) { $p.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { '{0}  {1,-30} {2}' -f $sid, $_.Name, $_.Value } } }"""),
    ST("services_autostart_nonms", "Auto-start services from outside Windows",
       "Automatic services whose binary lives outside the Windows folder.",
       r"""Get-CimInstance Win32_Service -Filter "StartMode='Auto'" | Where-Object { $_.PathName -and $_.PathName -notmatch '\\Windows\\' } | Sort-Object Name | ForEach-Object { '{0,-40} {1,-8} {2}' -f $_.Name, $_.State, $_.PathName }"""),
    # ---- Applications (6) --------------------------------------------------------------------
    AP("apps_per_user", "Programs installed only for you",
       "Programs installed in your profile (HKCU) -- invisible to other users and to "
       "machine-wide inventory tools.",
       T("Get-ItemProperty 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' -ErrorAction SilentlyContinue | Where-Object DisplayName | Sort-Object DisplayName | Select-Object DisplayName, DisplayVersion, Publisher, InstallLocation")),
    AP("apps_by_publisher", "Installed programs by publisher",
       "How many programs each publisher has installed -- a quick read of what kind of machine "
       "this is.",
       f"Get-ItemProperty {_UNINSTALL} -ErrorAction SilentlyContinue | Where-Object DisplayName | Group-Object Publisher | Sort-Object Count -Descending | Select-Object -First 30 Count, Name | Format-Table -AutoSize | Out-String -Width 220"),
    AP("broken_uninstallers", "Programs whose uninstaller is missing",
       "Installed-program entries whose uninstall command points at a file that no longer "
       "exists -- they cannot be removed from Settings.",
       f"Get-ItemProperty {_UNINSTALL} -ErrorAction SilentlyContinue | Where-Object {{ $_.DisplayName -and $_.UninstallString -and $_.UninstallString -notmatch 'msiexec' }} | ForEach-Object {{\n"
       r"""  $exe = if ($_.UninstallString -match '^"([^"]+)"') { $Matches[1] } else { ($_.UninstallString -split ' (?=[/-])')[0] }
  if (-not (Test-Path $exe)) { "[WARN] $($_.DisplayName): $exe" } }"""),
    AP("java_installs", "Java installations",
       "Every Java runtime/JDK found (registry and common folders) and JAVA_HOME.",
       f"Get-ItemProperty {_UNINSTALL} -ErrorAction SilentlyContinue | Where-Object {{ $_.DisplayName -match 'Java|JDK|JRE|Temurin|Zulu|Corretto|OpenJDK' }} | ForEach-Object {{ \"$($_.DisplayName) $($_.DisplayVersion)\" }}\n"
       "\"JAVA_HOME: $env:JAVA_HOME\"\nif (Get-Command java -ErrorAction SilentlyContinue) { cmd /c 'java -version 2>&1' } else { 'java is not on PATH' }"),
    AP("dev_tools_versions", "Developer tool versions",
       "Versions of git, python, node, npm, dotnet, code, pwsh and docker found on PATH.",
       r"""foreach ($c in 'git --version','python --version','node --version','npm --version','dotnet --version','code --version','pwsh --version','docker --version','winget --version') { $exe = $c.Split(' ')[0]
  if (Get-Command $exe -ErrorAction SilentlyContinue) { '{0,-8} {1}' -f $exe, ((cmd /c "$c 2>&1") | Select-Object -First 1) } else { '{0,-8} not on PATH' -f $exe } }""",
       timeout=180),
    AP("exe_in_downloads", "Programs sitting in Downloads",
       "Executables, installers and scripts in your Downloads folder, newest first -- "
       "installers people ran once, and things that should not be there.",
       r"""$d = (New-Object -ComObject Shell.Application).Namespace('shell:Downloads').Self.Path
Get-ChildItem $d -Recurse -File -Include *.exe,*.msi,*.bat,*.cmd,*.ps1,*.vbs,*.js,*.hta,*.scr -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 50 | ForEach-Object { $s = Get-AuthenticodeSignature $_.FullName -ErrorAction SilentlyContinue
  '{0} {1:yyyy-MM-dd}  {2,-12} {3}' -f $(if ($s.Status -eq 'Valid') {'[INFO]'} else {'[WARN]'}), $_.LastWriteTime, $s.Status, $_.Name }"""),
    # ---- Power (3) ---------------------------------------------------------------------------
    PW("sleep_timeouts", "Sleep and display timeouts",
       "Display off, sleep and hibernate timeouts on AC and battery for the active plan.",
       r"""foreach ($s in @(@('SUB_VIDEO','VIDEOIDLE','Display off'), @('SUB_SLEEP','STANDBYIDLE','Sleep after'), @('SUB_SLEEP','HIBERNATEIDLE','Hibernate after'))) {
  $q = powercfg /query SCHEME_CURRENT $s[0] $s[1]; $ac = ($q | Select-String 'Current AC Power Setting Index: (0x\w+)').Matches.Groups[1].Value; $dc = ($q | Select-String 'Current DC Power Setting Index: (0x\w+)').Matches.Groups[1].Value
  '{0,-16} AC {1,6} min   battery {2,6} min' -f $s[2], ([Convert]::ToInt32($ac,16)/60), ([Convert]::ToInt32($dc,16)/60) }"""),
    PW("modern_standby", "Modern Standby",
       "Whether this PC uses Modern Standby (S0 low power idle) or classic S3 sleep -- they "
       "behave very differently with network and battery drain.",
       r"""$a = powercfg /a; if ($a -match 'Standby \(S0 Low Power Idle\) Network (Connected|Disconnected)') { "[INFO] Modern Standby: $($Matches[0])" } else { '[INFO] Classic sleep (no Modern Standby)' }
$a | Select-Object -First 8"""),
    PW("battery_saver", "Battery saver and power throttling",
       "Battery saver threshold and power throttling policy (background apps slowed on "
       "battery).",
       REG + r"""RegShow 'HKLM:\SYSTEM\CurrentControlSet\Control\Power\PowerThrottling'
powercfg /query SCHEME_CURRENT SUB_ENERGYSAVER ESBATTTHRESHOLD 2>&1 | Select-String 'Current (AC|DC)' | ForEach-Object { 'Battery saver threshold ' + $_.Line.Trim() }"""),
    # ---- Printing (2) ------------------------------------------------------------------------
    PR("default_printer", "Default printer per user",
       "This user's default printer and whether Windows manages it ('let Windows manage my "
       "default printer' changes it to the last one used).",
       REG + r"""'Device: ' + (RegVal 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Windows' Device)
'LegacyDefaultPrinterMode (1 = Windows does NOT manage it): ' + (RegVal 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Windows' LegacyDefaultPrinterMode)"""),
    PR("print_policies", "Printing policies",
       "Package Point-and-Print, approved servers and the RPC over named pipes / TCP settings "
       "of the print spooler.",
       REG + r"""RegShow 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Printers'
''; RegShow 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Printers\RPC'"""),
    # ---- Group Policy (3) --------------------------------------------------------------------
    GP("device_install_policy", "Device installation restrictions",
       "Policies that block installing devices (USB storage, by class or id) and removable "
       "storage access.",
       REG + r"""RegShow 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions'
''; RegShow 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices'"""),
    GP("user_rights", "User rights held by this session",
       "Privileges in the current token and whether each is enabled (whoami /priv).",
       "whoami /priv", kind=CMD),
    GP("applocker", "AppLocker and software restriction policies",
       "AppLocker rule collections in force and legacy Software Restriction Policies.",
       r"""try { $p = Get-AppLockerPolicy -Effective -ErrorAction Stop; $p.RuleCollections | ForEach-Object { '{0,-10} enforcement {1}, {2} rule(s)' -f $_.RuleCollectionType, $_.EnforcementMode, $_.Count } } catch { "AppLocker: $($_.Exception.Message)" }
if (Test-Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Safer\CodeIdentifiers') { '[INFO] Software Restriction Policies are configured.' } else { '[INFO] No Software Restriction Policies.' }"""),
    # ---- Remote access (3) -------------------------------------------------------------------
    RA("rdp_certificate", "Remote Desktop certificate",
       "The certificate RDP presents, its subject and expiry -- self-signed certificates "
       "trigger the 'identity cannot be verified' warning.",
       r"""$t = (Get-CimInstance -Namespace root\cimv2\TerminalServices -ClassName Win32_TSGeneralSetting -Filter "TerminalName='RDP-Tcp'" -ErrorAction SilentlyContinue).SSLCertificateSHA1Hash
if (-not $t) { 'NOT AVAILABLE: the RDP certificate could not be read (needs elevation, or RDP never started).'; return }
$c = Get-ChildItem "Cert:\LocalMachine\Remote Desktop\$t" -ErrorAction SilentlyContinue
"Thumbprint $t  Subject $($c.Subject)  Expires $($c.NotAfter)  Issuer $($c.Issuer)" """,
       admin=True),
    RA("rdp_shadow", "RDP shadowing and session limits",
       "Whether admins may shadow (watch or control) sessions, with or without consent, and the "
       "idle/disconnected session limits.",
       REG + r"""RegShow 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'"""),
    RA("remote_mgmt_rules", "Remote management firewall rules",
       "Firewall rule groups that open remote management: WinRM, WMI, Remote Event Log, Remote "
       "Service and Remote Scheduled Tasks management.",
       T("Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.DisplayGroup -match 'Windows Remote Management|Windows Management Instrumentation|Remote Event Log|Remote Service Management|Remote Scheduled Tasks|Remote Volume' } | Select-Object DisplayGroup, DisplayName, Enabled, Profile")),
    # ---- Inventory (4) -----------------------------------------------------------------------
    IN("one_page_inventory", "One-page inventory",
       "Computer, user, OS and build, model, serial, CPU, RAM, disks, IPs, last boot and "
       "newest update on one screen -- the asset-sheet line.",
       L(r"""$os = Get-CimInstance Win32_OperatingSystem; $cs = Get-CimInstance Win32_ComputerSystem; $cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
[pscustomobject]@{ Computer=$env:COMPUTERNAME; User="$env:USERDOMAIN\$env:USERNAME"; OS="$($cv.ProductName) $($cv.DisplayVersion) $($cv.CurrentBuild).$($cv.UBR)"
  Model="$($cs.Manufacturer) $($cs.Model)"; Serial=(Get-CimInstance Win32_BIOS).SerialNumber; CPU=(Get-CimInstance Win32_Processor | Select-Object -First 1).Name.Trim()
  RAM='{0:N0} GB' -f ($cs.TotalPhysicalMemory/1GB); Disks=((Get-PhysicalDisk | ForEach-Object { '{0} {1:N0} GB' -f $_.FriendlyName, ($_.Size/1GB) }) -join '; ')
  IPs=((Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }).IPAddress -join ', ')
  LastBoot=$os.LastBootUpTime; NewestUpdate=((Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1).InstalledOn) }""")),
    IN("software_count", "Software inventory counts",
       "Counts of desktop programs, Store apps, services, drivers and scheduled tasks.",
       f"$d = @(Get-ItemProperty {_UNINSTALL} -ErrorAction SilentlyContinue | Where-Object DisplayName).Count\n"
       r"""'Desktop programs : {0}' -f $d
'Store apps       : {0}' -f @(Get-AppxPackage | Where-Object { -not $_.IsFramework }).Count
'Services         : {0}' -f @(Get-Service).Count
'Signed drivers   : {0}' -f @(Get-CimInstance Win32_PnPSignedDriver).Count
'Scheduled tasks  : {0}' -f @(Get-ScheduledTask).Count"""),
    IN("disk_inventory", "Disk inventory",
       "Every physical disk with model, serial, firmware, bus, size and media type.",
       T("Get-PhysicalDisk | Select-Object FriendlyName, SerialNumber, FirmwareVersion, BusType, MediaType, @{n='GB';e={[math]::Round($_.Size/1GB)}}")),
    IN("license_office", "Office licences",
       "Office licences installed on this PC (product, status, last 5 of the key), via the "
       "Software Licensing service.",
       T("Get-CimInstance SoftwareLicensingProduct -Filter \"PartialProductKey IS NOT NULL\" | Where-Object { $_.Name -match 'Office' } | Select-Object Name, @{n='Status';e={ if ($_.LicenseStatus -eq 1) {'Licensed'} else { $_.LicenseStatus } }}, PartialProductKey")),
    # ---- Troubleshooting (5) -----------------------------------------------------------------
    TS("wer_reports", "Recent Windows Error Reporting reports",
       "The newest crash and hang reports Windows queued or archived, by program.",
       r"""foreach ($r in "$env:ProgramData\Microsoft\Windows\WER\ReportArchive", "$env:ProgramData\Microsoft\Windows\WER\ReportQueue", "$env:LOCALAPPDATA\Microsoft\Windows\WER\ReportArchive") {
  Get-ChildItem $r -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 15 | ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1}' -f $_.LastWriteTime, $_.Name } }"""),
    TS("upgrade_errors", "Feature update failures (Panther logs)",
       "Errors from the last Windows upgrade attempt's setuperr.log -- why a feature update "
       "rolled back.",
       r"""$logs = Get-ChildItem "$env:SystemDrive\`$WINDOWS.~BT\Sources\Panther\setuperr.log","$env:SystemRoot\Panther\setuperr.log","$env:SystemRoot\Panther\NewOs\Panther\setuperr.log" -ErrorAction SilentlyContinue
if (-not $logs) { '[OK]   No setuperr.log -- no failed upgrade recorded.'; return }
foreach ($l in $logs) { "== $($l.FullName) ($($l.LastWriteTime))"; Get-Content $l.FullName -Tail 25 | ForEach-Object { "[WARN] $_" } }""",
       note="the Panther logs may need elevation"),
    TS("explorer_restarts", "Explorer restarts and hangs",
       "Times Explorer restarted (Winlogon 6000/6001) or stopped responding.",
       EV + "EvList @{LogName='Application'; ProviderName='Application Hang'} 14 40"),
    TS("audio_troubleshoot", "Audio service and devices",
       "Audio services' state, the sound devices and audio endpoint errors -- 'no sound'.",
       CHK + r"""foreach ($n in 'Audiosrv','AudioEndpointBuilder') { $s = Get-Service $n; Chk ($s.Status -eq 'Running') $n "$($s.Status), $($s.StartType)" }
Get-PnpDevice -Class AudioEndpoint -PresentOnly -ErrorAction SilentlyContinue | Select-Object FriendlyName, Status | Format-Table -AutoSize | Out-String -Width 220"""),
    TS("camera_mic_privacy", "Camera and microphone privacy",
       "Whether apps may use the camera and microphone (system and per-user consent store), "
       "and which apps last used them.",
       r"""foreach ($c in 'webcam','microphone') { foreach ($root in 'HKLM:','HKCU:') { $v = (Get-ItemProperty "$root\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\$c" -ErrorAction SilentlyContinue).Value; '{0,-10} {1,-5} {2}' -f $c, $root, $v } }
''; 'Recently used by (non-packaged apps):'
foreach ($c in 'webcam','microphone') { Get-ChildItem "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\$c\NonPackaged" -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-ItemProperty $_.PSPath; if ($p.LastUsedTimeStop) { '{0,-10} {1:yyyy-MM-dd HH:mm}  {2}' -f $c, [DateTime]::FromFileTime($p.LastUsedTimeStop), ($_.PSChildName -replace '#','\') } } }"""),
]

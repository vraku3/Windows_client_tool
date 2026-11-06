"""A hundred more Windows client checks across every client category. Read-only."""
from ._base import ADM, CHK, CMD, EV, L, REG, T, maker, need_admin, need_cmd
from .client_system import _CLEANUP_SCAN, _DEEP_CLEANUP

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

# Walk one directory at a time: do not traverse junctions or hide partial totals.
_SIZE_WALK = r"""function MeasureTree($root) {
  $bytes = [decimal]0; $files = 0; $skipped = 0; $failed = 0
  $pending = [Collections.Generic.Stack[string]]::new(); $pending.Push($root)
  while ($pending.Count) {
    $dir = $pending.Pop()
    try {
      $item = Get-Item -LiteralPath $dir -Force -ErrorAction Stop
      if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { $skipped++; continue }
      $children = @(Get-ChildItem -LiteralPath $dir -Force -ErrorAction Stop)
      foreach ($child in $children) {
        if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { $skipped++; continue }
        if ($child.PSIsContainer) { $pending.Push($child.FullName) }
        else { $files++; $bytes += $child.Length }
      }
    } catch { $failed++; "ERROR: Partial size for ${dir}: $($_.Exception.Message)" | Out-Host }
  }
  [pscustomobject]@{Path=$root; GB=[math]::Round($bytes/1GB,3); Files=$files; SkippedReparsePoints=$skipped; FailedDirectories=$failed; Complete=($failed -eq 0 -and $skipped -eq 0)}
}
"""

_CLIENT_EVIDENCE_SCRIPTS = [
    HW("client_usb_setupapi_sections", "USB installation history from SetupAPI sections",
      "Shows the newest 30 USB device-install sections with timestamps and outcomes from the current SetupAPI log.",
      r"""try {
  $path = "$env:SystemRoot\INF\setupapi.dev.log"
  if (-not (Test-Path -LiteralPath $path -ErrorAction Stop)) { 'NOT AVAILABLE: SetupAPI device log absent.'; return }
  # Bounded tail avoids loading years of installation history into memory.
  $lines = @(Get-Content -LiteralPath $path -Tail 50000 -ErrorAction Stop)
  if (-not $lines.Count) { 'NOT AVAILABLE: SetupAPI log empty.'; return }
  $sections = [Collections.Generic.List[string]]::new(); $buffer = $null
  foreach ($line in $lines) {
    if ($line -match '^>>>\s+\[') {
      if ($null -ne $buffer) { $sections.Add(($buffer -join "`n") + "`n[INFO] Section incomplete in sampled tail") }
      if ($line -match '(?i)USB(STOR)?\\') { $buffer = [Collections.Generic.List[string]]::new() }
      else { $buffer = $null }
    }
    if ($null -ne $buffer) {
      $buffer.Add($line)
      if ($line -match '^<<<\s+\[Exit status:') { $sections.Add(($buffer -join "`n")); $buffer = $null }
    }
  }
  if ($null -ne $buffer) { $sections.Add(($buffer -join "`n") + "`n[INFO] Section still open or incomplete") }
  if (-not $sections.Count) { '[INFO] No USB installation sections in the newest 50000 lines; earlier history may exist.' }
  $sections | Select-Object -Last 30
  '[INFO] Installation evidence, not every insertion/removal or proof of user activity. Current log tail only; rotated logs excluded.'
} catch { "ERROR: SetupAPI history unavailable: $($_.Exception.Message)" }""",
      tags=('diagnostic', 'hardware', 'triage', 'inventory')),
    DS("client_vss_writer_provider_health", "VSS writers, providers and recent failures",
      "Reads live VSS writer states and providers plus recent VSS/Volsnap warnings; does not create a snapshot.",
      need_admin("vssadmin writer and provider queries require elevation") + CHK + r"""foreach ($kind in 'writers','providers') {
  try {
    "== VSS $kind"
    $out = @(& "$env:SystemRoot\System32\vssadmin.exe" list $kind 2>&1); $code = $LASTEXITCODE
    if ($out.Count) { $out } else { Chk $null "VSS $kind" 'no output returned' }
    if ($code -ne 0) { Chk $null "VSS $kind" "query exited $code" }
    else { Chk $null "VSS $kind" 'query completed; inspect each native State and Last error above (localized output is not guessed)' }
  } catch { Chk $null "VSS $kind" $_.Exception.Message }
}
foreach ($source in @(@{LogName='Application';ProviderName='VSS'},@{LogName='System';ProviderName='volsnap'})) {
  $source.Level = 2,3; $source.StartTime = (Get-Date).AddDays(-7)
  try {
    $events = @(Get-WinEvent -FilterHashtable $source -MaxEvents 30 -ErrorAction Stop)
    Chk $false $source.ProviderName "$($events.Count) newest warning/error events in seven days (limit 30)"
    $events | Select-Object TimeCreated, Id, Message | Format-List | Out-String -Width 220
  } catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { Chk $true $source.ProviderName 'no matching warning/error events retained in seven days; not a backup test' }
    else { Chk $null $source.ProviderName $_.Exception.Message }
  }
}""", admin=True, tags=('diagnostic', 'storage', 'health')),
    DS("client_temp_child_breakdown", "Temp storage by immediate child folder",
      "Breaks user and Windows Temp into top-level folders and loose files; reports refused paths and skips reparse points.",
      _SIZE_WALK + r"""foreach ($root in @($env:TEMP, "$env:SystemRoot\Temp") | Select-Object -Unique) {
  try {
    "== $root"
    $entries = @(Get-ChildItem -LiteralPath $root -Force -ErrorAction Stop)
    if (-not $entries.Count) { '[INFO] Temp directory is empty.'; continue }
    $loose = $entries | Where-Object { -not $_.PSIsContainer -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } | Measure-Object Length -Sum
    "[INFO] Loose files: $($loose.Count), $([math]::Round($loose.Sum/1MB,2)) MB"
    $rows = @(foreach ($entry in $entries | Where-Object PSIsContainer) { MeasureTree $entry.FullName })
    $rows | Sort-Object GB -Descending | Format-Table -AutoSize -Wrap | Out-String -Width 220
    $links = @($entries | Where-Object { -not $_.PSIsContainer -and ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) })
    if ($links.Count) { "[INFO] $($links.Count) top-level reparse files excluded." }
  } catch { "ERROR: Temp breakdown unavailable for ${root}: $($_.Exception.Message)" }
}
'[INFO] Logical file lengths, not allocated space; partial or skipped trees are not complete totals. No files are deleted.'""",
      note="Windows Temp may be partly unreadable without elevation", timeout=600,
      tags=('diagnostic', 'storage', 'triage')),
    US("client_profile_size_inventory", "User profile sizes with completeness markers",
      "Measures non-special profiles without following junctions; includes SID, last use, loaded state and partial-read evidence.",
      need_admin("other users' profile sizes require elevation; some folders can still refuse") + _SIZE_WALK + r"""try {
  $profiles = @(Get-CimInstance Win32_UserProfile -ErrorAction Stop | Where-Object { -not $_.Special })
  if (-not $profiles.Count) { 'NOT AVAILABLE: no non-special profiles returned.'; return }
  foreach ($profile in $profiles) {
    if (-not $profile.LocalPath) { "NOT AVAILABLE: profile path for $($profile.SID)"; continue }
    "== SID=$($profile.SID); loaded=$($profile.Loaded); last use=$($profile.LastUseTime)"
    MeasureTree $profile.LocalPath | Format-List | Out-String -Width 220
  }
  '[INFO] Logical sizes exclude reparse points, including some cloud placeholders. Failed directories and exclusions make totals partial; hard links can count more than once.'
} catch { "ERROR: Profile inventory: $($_.Exception.Message)" }""",
      admin=True, timeout=900, tags=('diagnostic', 'storage', 'inventory')),
    DS("client_backup_scope_recovery_evidence", "Backup scope and last local recovery evidence",
      "Reads File History scope, Windows Backup catalog versions and newest restore point; local evidence cannot prove recoverability.",
      need_admin("Windows Backup catalog and recovery point reads need elevation") + CHK + r"""$configRoot = "$env:LOCALAPPDATA\Microsoft\Windows\FileHistory\Configuration"
try {
  if (Test-Path -LiteralPath $configRoot -ErrorAction Stop) {
    $configs = @(Get-ChildItem -LiteralPath $configRoot -Filter 'Config*.xml' -File -ErrorAction Stop)
    if (-not $configs.Count) { Chk $null 'File History scope' 'no configuration XML files' }
    foreach ($file in $configs) {
      "== $($file.FullName) modified $($file.LastWriteTime)"
      Get-Content -LiteralPath $file.FullName -ErrorAction Stop
    }
  } else { Chk $null 'File History scope' 'no configuration for this user; not evidence that another backup product is absent' }
} catch { Chk $null 'File History scope' $_.Exception.Message }
try {
  $tool = Join-Path $env:SystemRoot 'System32\wbadmin.exe'
  if (-not (Test-Path -LiteralPath $tool -ErrorAction Stop)) { Chk $null 'Windows Backup catalog' 'wbadmin.exe absent' }
  else {
    $out = & $tool get versions 2>&1; $code = $LASTEXITCODE
    if ($out) { $out } else { Chk $null 'Windows Backup catalog' 'no output returned' }
    if ($code -ne 0) { Chk $null 'Windows Backup catalog' "query exited $code; see native output" }
    '[INFO] A catalog version identifier can be used with wbadmin get items to inspect that backup scope; no backup or recovery was started.'
  }
} catch { Chk $null 'Windows Backup catalog' $_.Exception.Message }
try {
  $point = Get-CimInstance -Namespace root\default -ClassName SystemRestore -ErrorAction Stop | Sort-Object SequenceNumber -Descending | Select-Object -First 1
  if ($point) { $point | Select-Object SequenceNumber, Description, CreationTime, RestorePointType | Format-List | Out-String -Width 220 }
  else { Chk $null 'Last restore point' 'none retained' }
} catch { Chk $null 'Last restore point' $_.Exception.Message }
Chk $null 'Recovery readiness' 'scope XML, catalog entries and restore points do not verify an independent usable backup. Third-party/cloud backups require their own console and a restore test.'""",
      admin=True, tags=('diagnostic', 'storage', 'inventory', 'health')),
]

_UNINSTALL = ("'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
              "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
              "'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'")

SCRIPTS = [
    AP("shared_browser_extension_manifests", "Browser extension manifests for this user",
       "Edge, Chrome and Firefox extension inventory across current-user profiles; retained packages may be disabled.",
       r"""$found = 0
foreach ($browser in @(@{Name='Edge';Root="$env:LOCALAPPDATA\Microsoft\Edge\User Data"}, @{Name='Chrome';Root="$env:LOCALAPPDATA\Google\Chrome\User Data"})) {
  try {
    if (-not (Test-Path -LiteralPath $browser.Root -ErrorAction Stop)) { "[INFO] $($browser.Name) user data absent."; continue }
    $profiles = @(Get-ChildItem -LiteralPath $browser.Root -Directory -ErrorAction Stop | Where-Object { $_.Name -eq 'Default' -or $_.Name -like 'Profile *' })
    foreach ($profile in $profiles) {
      $root = Join-Path $profile.FullName 'Extensions'
      if (-not (Test-Path -LiteralPath $root -ErrorAction Stop)) { continue }
      foreach ($id in (Get-ChildItem -LiteralPath $root -Directory -ErrorAction Stop)) {
        foreach ($version in (Get-ChildItem -LiteralPath $id.FullName -Directory -ErrorAction Stop)) {
          $manifest = Join-Path $version.FullName 'manifest.json'
          try {
            $m = Get-Content -LiteralPath $manifest -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
            $found++
            [pscustomobject]@{Browser=$browser.Name; Profile=$profile.Name; ID=$id.Name; Name=$m.name; Version=$m.version; Permissions=(@($m.permissions)+@($m.host_permissions) -join ', ')} | Format-List | Out-String -Width 220
          } catch { "ERROR: Extension manifest ${manifest}: $($_.Exception.Message)" }
        }
      }
    }
  } catch { "ERROR: $($browser.Name) inventory incomplete: $($_.Exception.Message)" }
}
$firefox = "$env:APPDATA\Mozilla\Firefox\Profiles"
try {
  if (Test-Path -LiteralPath $firefox -ErrorAction Stop) {
    foreach ($profile in (Get-ChildItem -LiteralPath $firefox -Directory -ErrorAction Stop)) {
      $file = Join-Path $profile.FullName 'extensions.json'
      if (-not (Test-Path -LiteralPath $file -ErrorAction Stop)) { continue }
      try {
        $json = Get-Content -LiteralPath $file -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $json.addons | Where-Object type -eq 'extension' | ForEach-Object {
          $found++
          [pscustomobject]@{Browser='Firefox'; Profile=$profile.Name; ID=$_.id; Name=$_.defaultLocale.name; Version=$_.version; Active=$_.active; UserDisabled=$_.userDisabled} | Format-List | Out-String -Width 220
        }
      } catch { "ERROR: Firefox inventory ${file}: $($_.Exception.Message)" }
    }
  }
} catch { "ERROR: Firefox profiles: $($_.Exception.Message)" }
"[INFO] $found extension package records; current user and default browser data locations only. Chromium names may be __MSG_ localization tokens; package presence does not prove enabled state."
""",
       timeout=180, tags=('inventory', 'security', 'diagnostic')),
    AP("shared_browser_native_hosts", "Browser native messaging host registrations",
       "Lists native messaging registration names and manifest paths in user and machine registry views; executes no hosts.",
       r"""$count = 0
foreach ($scope in 'HKCU:\SOFTWARE','HKLM:\SOFTWARE','HKCU:\SOFTWARE\WOW6432Node','HKLM:\SOFTWARE\WOW6432Node') {
  foreach ($vendor in 'Google\Chrome','Microsoft\Edge','Mozilla') {
    $root = "$scope\$vendor\NativeMessagingHosts"
    try {
      if (-not (Test-Path -LiteralPath $root -ErrorAction Stop)) { continue }
      foreach ($hostKey in (Get-ChildItem -LiteralPath $root -ErrorAction Stop)) {
        $key = Get-Item -LiteralPath $hostKey.PSPath -ErrorAction Stop
        $count++
        [pscustomobject]@{Registration=$hostKey.Name; Manifest=$key.GetValue('')} | Format-List | Out-String -Width 220
      }
    } catch { "ERROR: Native messaging registry ${root}: $($_.Exception.Message)" }
  }
}
"[INFO] $count registrations. These permit browser extensions to communicate with local programs; registration alone does not establish trust or active use."
""",
       tags=('inventory', 'security', 'diagnostic')),
    HW("shared_audio_endpoint_states", "Playback and recording endpoint configuration",
       "Registered render/capture endpoint names and state bits, including disabled, unplugged and absent devices.",
       r"""$count = 0
foreach ($flow in 'Render','Capture') {
  $root = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\$flow"
  try {
    if (-not (Test-Path -LiteralPath $root -ErrorAction Stop)) { "[INFO] $flow endpoints are not registered."; continue }
    foreach ($endpoint in (Get-ChildItem -LiteralPath $root -ErrorAction Stop)) {
      try {
        $p = Get-ItemProperty -LiteralPath $endpoint.PSPath -ErrorAction Stop
        $props = Get-ItemProperty -LiteralPath (Join-Path $endpoint.PSPath 'Properties') -ErrorAction Stop
        $state = $p.DeviceState
        $labels = @()
        if ($null -ne $state) {
          if ($state -band 1) { $labels += 'Active' }; if ($state -band 2) { $labels += 'Disabled' }
          if ($state -band 4) { $labels += 'Not present' }; if ($state -band 8) { $labels += 'Unplugged' }
        }
        $count++
        [pscustomobject]@{Flow=$flow; Name=$props.'{a45c254e-df1c-4efd-8020-67d146a850e0},14'; Endpoint=$endpoint.PSChildName; StateBits=$state; State=($labels -join ', ')} | Format-List | Out-String -Width 220
      } catch { "ERROR: Endpoint $($endpoint.PSChildName): $($_.Exception.Message)" }
    }
  } catch { "ERROR: $flow endpoints: $($_.Exception.Message)" }
}
"[INFO] $count endpoint registrations; cached registry state does not identify the default endpoint or a live audio stream."
""",
       tags=('diagnostic', 'hardware', 'inventory')),
    AP("shared_codec_filter_inventory", "Registered codecs and DirectShow filters",
       "Legacy Drivers32 codec registrations and DirectShow audio/video filter categories in both machine registry views.",
       r"""foreach ($view in 'HKLM:\SOFTWARE','HKLM:\SOFTWARE\WOW6432Node') {
  $codecs = "$view\Microsoft\Windows NT\CurrentVersion\Drivers32"
  try {
    if (Test-Path -LiteralPath $codecs -ErrorAction Stop) {
      "== $codecs"
      $p = Get-ItemProperty -LiteralPath $codecs -ErrorAction Stop
      $p.PSObject.Properties | Where-Object { $_.Name -match '^(vidc\.|msacm\.|wavemapper|midimapper)' } | ForEach-Object { "[INFO] $($_.Name) = $($_.Value)" }
    }
  } catch { "ERROR: Codec registrations: $($_.Exception.Message)" }
  foreach ($category in @(@{Name='Legacy filters';ID='{083863F1-70DE-11d0-BD40-00A0C911CE86}'}, @{Name='Audio compressors';ID='{33D9A761-90C8-11d0-BD43-00A0C911CE86}'}, @{Name='Video compressors';ID='{33D9A760-90C8-11d0-BD43-00A0C911CE86}'})) {
    $root = "$view\Classes\CLSID\$($category.ID)\Instance"
    try {
      if (-not (Test-Path -LiteralPath $root -ErrorAction Stop)) { continue }
      "== $view : $($category.Name)"
      Get-ChildItem -LiteralPath $root -ErrorAction Stop | ForEach-Object {
        $p = Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction Stop
        [pscustomobject]@{Name=$p.FriendlyName; CLSID=$p.CLSID; Registration=$_.PSChildName}
      } | Format-Table -AutoSize -Wrap | Out-String -Width 220
    } catch { "ERROR: Filter category $($category.Name): $($_.Exception.Message)" }
  }
}
'[INFO] Registration inventory only; does not load codecs, test playback or enumerate Media Foundation transforms.'""",
       tags=('inventory', 'diagnostic', 'hardware')),
    HW("shared_usb_arrival_removal_dates", "USB last arrival and removal evidence",
       "PnP-reported last arrival/removal properties for remembered USB instances; timestamps may be unavailable or cached.",
       need_cmd("Get-PnpDeviceProperty", "PnP device properties") + r"""try { $devices = @(Get-PnpDevice -ErrorAction Stop | Where-Object InstanceId -like 'USB\*') }
catch { "ERROR: USB inventory: $($_.Exception.Message)"; return }
if (-not $devices.Count) { '[INFO] No remembered USB device instances.'; return }
foreach ($device in $devices) {
  try {
    $props = @(Get-PnpDeviceProperty -InstanceId $device.InstanceId -ErrorAction Stop)
    $arrival = $props | Where-Object KeyName -eq 'DEVPKEY_Device_LastArrivalDate' | Select-Object -First 1
    $removal = $props | Where-Object KeyName -eq 'DEVPKEY_Device_LastRemovalDate' | Select-Object -First 1
    [pscustomobject]@{Name=$device.FriendlyName; Instance=$device.InstanceId; LastArrival=$arrival.Data; LastRemoval=$removal.Data} | Format-List | Out-String -Width 220
  } catch { "NOT AVAILABLE: properties for $($device.InstanceId): $($_.Exception.Message)" }
}
'[INFO] Blanks mean not reported. These are last-known PnP transitions, not a complete connection history or proof of user activity.'""",
       note="some protected device properties may require elevation", timeout=240,
       tags=('diagnostic', 'hardware', 'inventory', 'triage')),
    HW("usb_storage_history", "USB storage devices remembered by Windows",
       "Checks USBSTOR registry entries for previously installed USB storage models and instance "
       "identifiers. Entries do not establish connection times or current presence.",
       r"""$root = 'HKLM:\SYSTEM\CurrentControlSet\Enum\USBSTOR'
try {
  if (-not (Test-Path -LiteralPath $root -ErrorAction Stop)) { '[INFO] No USBSTOR history key exists.'; return }
  $rows = @(foreach ($model in (Get-ChildItem -LiteralPath $root -ErrorAction Stop)) {
    foreach ($instance in (Get-ChildItem -LiteralPath $model.PSPath -ErrorAction Stop)) {
      $p = Get-ItemProperty -LiteralPath $instance.PSPath -ErrorAction Stop
      [pscustomobject]@{ Model=$model.PSChildName; Instance=$instance.PSChildName; Name=$p.FriendlyName; Description=$p.DeviceDesc; Container=$p.ContainerID }
    }
  })
  if (-not $rows.Count) { '[INFO] No USB storage instances recorded.' }
  $rows | Format-Table -AutoSize -Wrap | Out-String -Width 220
} catch { "NOTE: USB storage history incomplete or refused: $($_.Exception.Message)" }""",
       tags=('diagnostic', 'hardware', 'inventory', 'triage'), keywords="USBSTOR removable history"),
    SV("search_index_files", "Windows Search service and index size",
       "Checks the Search service and database file sizes in its configured data directory. "
       "File size does not prove indexing is complete or the database is healthy.",
       need_admin("Search database folders can refuse standard-user reads") + CHK + r"""try {
  $service = Get-Service WSearch -ErrorAction Stop
  Chk ($service.Status -eq 'Running') 'Windows Search' "$($service.Status); start type $($service.StartType); a stopped service can be intentional"
  $key = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows Search' -ErrorAction Stop
  $root = if ($key.DataDirectory) { [Environment]::ExpandEnvironmentVariables($key.DataDirectory) } else { "$env:ProgramData\Microsoft\Search\Data" }
  "[INFO] Data directory: $root"
  $files = @(Get-ChildItem -LiteralPath $root -Recurse -File -Force -ErrorAction Stop | Where-Object { $_.Extension -in '.edb','.db' })
  if (-not $files.Count) { '[INFO] No .edb or .db files found; indexing may not have created a database.' }
  $files | Select-Object FullName, @{n='SizeMB';e={[math]::Round($_.Length/1MB,2)}}, LastWriteTime | Format-Table -AutoSize -Wrap | Out-String -Width 220
} catch { "NOTE: Search status or size could not be fully read: $($_.Exception.Message)" }""",
       admin=True, timeout=180, tags=('diagnostic', 'storage', 'performance'), keywords="WSearch index Windows.edb Windows.db"),
    SV("service_missing_executables", "Services with missing executable files",
       "Checks absolute local service executable paths for missing files. Ambiguous unquoted "
       "paths, remote paths and refused reads are reported without declaring the service orphaned.",
       CHK + r"""try { $services = @(Get-CimInstance Win32_Service -ErrorAction Stop) }
catch { "ERROR: service inventory failed: $($_.Exception.Message)"; return }
foreach ($service in $services) {
  $command = [Environment]::ExpandEnvironmentVariables([string]$service.PathName).Trim()
  $exe = $null
  if ($command -match '^"([^"]+)"') { $exe = $Matches[1] }
  elseif ($command -match '^(\S+\.exe)(?:\s|$)') { $exe = $Matches[1] }
  if (-not $exe -or $exe -notmatch '^[A-Za-z]:\\') { Chk $null $service.Name "path needs manual review: $command"; continue }
  try {
    $exists = Test-Path -LiteralPath $exe -PathType Leaf -ErrorAction Stop
    if (-not $exists) { Chk $false $service.Name "executable missing: $exe; state=$($service.State)" }
  } catch { Chk $null $service.Name "path read refused: $($_.Exception.Message)" }
}
'[INFO] Scan complete; only missing files and paths requiring review are listed. Service DLLs were not checked.'""",
       tags=('diagnostic', 'triage', 'health'), keywords="orphan service ImagePath missing binary"),
    ST("task_action_failures_7d", "Scheduled task action failures (7 days)",
       "Checks Task Scheduler failure events and completed actions with nonzero result codes "
       "over seven days. Disabled logging and read failures are reported explicitly.",
       CHK + r"""$log = 'Microsoft-Windows-TaskScheduler/Operational'
try {
  $info = Get-WinEvent -ListLog $log -ErrorAction Stop
  if (-not $info.IsEnabled) { 'NOTE: Task Scheduler operational logging is disabled; retained history is incomplete.' }
  $events = @(Get-WinEvent -FilterHashtable @{LogName=$log; Id=101,103,201,202,203; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 5000 -ErrorAction Stop)
} catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { '[INFO] No matching task records retained in seven days.' }
  else { "NOTE: task history unavailable or refused: $($_.Exception.Message)" }
  return
}
if ($events.Count -eq 5000) { 'NOTE: newest 5000 matching events only; older failures may be omitted.' }
$unclassified = 0
$failures = @(foreach ($event in $events) {
  $data = @{}; ([xml]$event.ToXml()).Event.EventData.Data | ForEach-Object { $data[$_.Name] = $_.'#text' }
  if ($event.Id -ne 201 -or ($null -ne $data.ResultCode -and $data.ResultCode -notmatch '^(0|0x0+)$')) {
    [pscustomobject]@{ Time=$event.TimeCreated; Event=$event.Id; Task=$data.TaskName; Action=$data.ActionName; Result=$data.ResultCode; Detail=$event.Message }
  } elseif ($null -eq $data.ResultCode) { $unclassified++ }
})
$verdict = if ($failures.Count) { $false } elseif ($unclassified -or -not $info.IsEnabled -or $events.Count -eq 5000) { $null } else { $true }
Chk $verdict 'Task action failures' "$($failures.Count) failures, $unclassified unclassified records in the retained sample; nonzero application exit codes can be intentional"
$failures | Format-Table -AutoSize -Wrap | Out-String -Width 220""",
       tags=('diagnostic', 'triage', 'health'), keywords="TaskScheduler task exit result code"),
    PR("printer_queue_health", "Printer status and queued jobs",
       "Checks installed printer status and queued jobs, including submission times and job "
       "status. Per-printer access failures are shown rather than treated as empty queues.",
       need_cmd("Get-Printer", "PrintManagement") + CHK + r"""try { $printers = @(Get-Printer -ErrorAction Stop) }
catch { "ERROR: printer inventory failed: $($_.Exception.Message)"; return }
if (-not $printers.Count) { '[INFO] No printers installed.'; return }
foreach ($printer in $printers) {
  "[INFO] $($printer.Name)  status=$($printer.PrinterStatus)  port=$($printer.PortName)  driver=$($printer.DriverName)"
  try {
    $jobs = @(Get-PrintJob -PrinterName $printer.Name -ErrorAction Stop)
    Chk $null $printer.Name "$($jobs.Count) visible queued jobs; a queue alone does not imply a fault"
    $jobs | Select-Object ID, DocumentName, UserName, JobStatus, SubmittedTime, TotalPages, Size | Format-Table -AutoSize -Wrap | Out-String -Width 220
  } catch { "NOTE: queue for $($printer.Name) unavailable or refused: $($_.Exception.Message)" }
}""",
       note="other users' job details may require administrator rights", tags=('diagnostic', 'hardware', 'triage'), keywords="print spooler queue jobs"),
    HW("bluetooth_battery_levels", "Bluetooth battery levels where reported",
       "Checks present Bluetooth devices for a reported battery percentage. Missing battery "
       "properties mean unsupported or unavailable data, never an empty battery.",
       need_cmd("Get-PnpDeviceProperty", "PnP device cmdlets") + CHK + r"""try { $devices = @(Get-PnpDevice -PresentOnly -ErrorAction Stop | Where-Object { $_.InstanceId -match '^BTH' }) }
catch { "ERROR: Bluetooth device inventory failed: $($_.Exception.Message)"; return }
if (-not $devices.Count) { '[INFO] No present Bluetooth device instances.'; return }
foreach ($device in $devices) {
  try {
    $properties = @(Get-PnpDeviceProperty -InstanceId $device.InstanceId -ErrorAction Stop)
    $battery = $properties | Where-Object KeyName -eq '{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2' | Select-Object -First 1
    if (-not $battery -or $null -eq $battery.Data -or "$($battery.Data)" -notmatch '^\d+$' -or [int]$battery.Data -gt 100) {
      Chk $null $device.FriendlyName "battery level not reported; $($device.InstanceId)"
    } else { Chk ([int]$battery.Data -gt 20) $device.FriendlyName "$($battery.Data)% reported; may be cached" }
  } catch { "NOTE: properties for $($device.InstanceId) unavailable: $($_.Exception.Message)" }
}""",
       timeout=180, tags=('diagnostic', 'hardware', 'health'), keywords="Bluetooth battery BTHLE"),
    IN("powershell_module_inventory", "PowerShell module versions and paths",
       "Checks discoverable modules for installed versions, edition compatibility and paths, "
       "including side-by-side versions that can explain module loading differences.",
       r"""try {
  "[INFO] Host: $($PSVersionTable.PSEdition) $($PSVersionTable.PSVersion); modules visible to this host and user only"
  $modules = @(Get-Module -ListAvailable -ErrorAction Stop)
  if (-not $modules.Count) { '[INFO] No modules discoverable on PSModulePath.' }
  $modules | Sort-Object Name, Version -Descending | Select-Object Name, Version, CompatiblePSEditions, Path | Format-Table -AutoSize -Wrap | Out-String -Width 220
  '[INFO] Module search directories:'; $env:PSModulePath -split ';'
} catch { "NOTE: module discovery incomplete or refused: $($_.Exception.Message)" }""",
       timeout=180, tags=('diagnostic', 'inventory', 'baseline'), keywords="PowerShell modules versions PSModulePath"),
    # ---- System (12) -------------------------------------------------------------------------
    SY("upgrade_history", "Windows upgrade history",
       "Every feature update this installation went through (the 'Source OS' records Setup "
       "leaves), oldest first -- when it went from 23H2 to 24H2 and so on.",
       r"""$k = 'HKLM:\SYSTEM\Setup'
$rows = Get-ChildItem $k -ErrorAction SilentlyContinue | Where-Object PSChildName -like 'Source OS*' | ForEach-Object { $p = Get-ItemProperty $_.PSPath
  [pscustomobject]@{ Replaced=($_.PSChildName -replace '^Source OS \(Updated on (.*)\)$','$1'); Was="$($p.ProductName) $($p.DisplayVersion) (build $($p.CurrentBuild).$($p.UBR))" } }
if (-not $rows) { '[INFO] No feature update recorded -- this is the original installation.' } else { $rows | Format-Table -AutoSize | Out-String -Width 220 }
$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'; "Now: $($cv.ProductName) $($cv.DisplayVersion) (build $($cv.CurrentBuild).$($cv.UBR))" """,
       tags=('diagnostic', 'inventory', 'baseline')),
    SY("recovery_env", "Windows Recovery Environment",
       "Whether WinRE is enabled and where its image lives -- without it, 'Reset this PC' and "
       "automatic repair do not work.",
       need_admin("reagentc refuses a standard user") + "reagentc /info", admin=True,
       tags=('diagnostic',)),
    SY("boot_config", "Boot configuration (BCD)",
       "The boot manager and Windows loader entries: timeout, default, recovery sequence, "
       "safe-mode flags and test-signing left on.",
       need_admin("bcdedit refuses a standard user") + "bcdedit /enum", admin=True,
       tags=('diagnostic', 'security')),
    SY("registry_size", "Registry size",
       "Current registry size against its maximum (Win32_Registry).",
       CHK + r"""$r = Get-CimInstance Win32_Registry -ErrorAction Stop
if (-not $r -or $null -eq $r.CurrentSize -or $null -eq $r.MaximumSize -or $r.MaximumSize -le 0) { Chk $null 'Registry size' 'size or limit not reported'; return }
Chk ($r.CurrentSize -lt $r.MaximumSize) 'Registry size' "$($r.CurrentSize) MB of $($r.MaximumSize) MB maximum; status=$($r.Status)"
""",
       tags=('diagnostic', 'health')),
    SY("long_paths", "Long path support and 8.3 names",
       "Whether paths longer than 260 characters are allowed (LongPathsEnabled) and whether 8.3 "
       "short names are still created on the system volume.",
       REG + r"""'LongPathsEnabled: ' + (RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' LongPathsEnabled)
fsutil 8dot3name query $env:SystemDrive 2>&1""", note="fsutil may need elevation",
       tags=('diagnostic',)),
    SY("wer_settings", "Windows Error Reporting settings",
       "Whether crash reporting is on, consent level, and local dump collection for crashing "
       "programs (LocalDumps).",
       REG + r"""RegShow 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting'
''; 'LocalDumps:'; RegShow 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps'""",
       tags=('diagnostic',)),
    SY("storage_sense", "Storage Sense configuration",
       "Whether Storage Sense runs, how often, and what it deletes (Recycle Bin, Downloads, "
       "OneDrive content after N days).",
       REG + r"""RegShow 'HKCU:\Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy'""",
       tags=('diagnostic',)),
    SY("clipboard_settings", "Clipboard history and cloud clipboard",
       "Whether clipboard history and cross-device clipboard sync are on (policy and user).",
       REG + r"""'History (user): ' + (RegVal 'HKCU:\Software\Microsoft\Clipboard' EnableClipboardHistory)
'Cloud sync (user): ' + (RegVal 'HKCU:\Software\Microsoft\Clipboard' CloudClipboardAutomaticUpload)
'Policy AllowClipboardHistory: ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' AllowClipboardHistory)
'Policy AllowCrossDeviceClipboard: ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\System' AllowCrossDeviceClipboard)""",
       tags=('diagnostic', 'security')),
    SY("dev_drive", "Dev Drive and ReFS volumes",
       "Dev Drive support and which volumes are Dev Drives or ReFS -- they skip some Defender "
       "scanning by design.",
       r"""fsutil devdrv query 2>&1
Get-Volume | Where-Object { $_.FileSystem -eq 'ReFS' } | Select-Object DriveLetter, FileSystemLabel, Size | Format-Table -AutoSize | Out-String -Width 220""",
       note="fsutil devdrv may need elevation",
       tags=('diagnostic',)),
    SY("kernel_dma", "Kernel DMA protection",
       "DMA protection capability (Device Guard property 3) and the DMA guard policy. "
       "Capability and policy alone do not prove that Kernel DMA Protection is active.",
       REG + CHK + r"""$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction Stop
"Available security properties: $($dg.AvailableSecurityProperties -join ', ')  (3 = DMA protection)"
$available = if ($dg -and $null -ne $dg.AvailableSecurityProperties) { $dg.AvailableSecurityProperties -contains 3 } else { $null }
Chk $available 'DMA protection capability' 'property 3 indicates hardware support, not active protection; check msinfo32 > System Summary > Kernel DMA Protection for the active state'
'Policy DeviceEnumerationPolicy: ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Kernel DMA Protection' DeviceEnumerationPolicy)
""",
       tags=('diagnostic', 'security', 'hardware', 'health')),
    SY("system_uptime_history", "Uptime per boot (30 days)",
       "Every boot in the last 30 days and how long Windows stayed up before the next "
       "shutdown -- spotting a machine that is never restarted, or one that restarts daily.",
       r"""$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=6005,6006,6008; StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue | Sort-Object TimeCreated
$boot = $null
foreach ($x in $e) { if ($x.Id -eq 6005) { $boot = $x.TimeCreated } elseif ($boot) { $span = $x.TimeCreated - $boot; '{0:yyyy-MM-dd HH:mm} -> {1:yyyy-MM-dd HH:mm}  up {2:%d}d {2:hh}h {2:mm}m{3}' -f $boot, $x.TimeCreated, $span, $(if ($x.Id -eq 6008) {'  (UNEXPECTED end)'} else {''}); $boot = $null } }
if ($boot) { '{0:yyyy-MM-dd HH:mm} -> now               up {1:%d}d {1:hh}h {1:mm}m' -f $boot, ((Get-Date) - $boot) }""",
       tags=('diagnostic', 'triage')),
    SY("os_install_media", "Installation type and servicing channel",
       "Whether this is a Home/Pro/Enterprise/Education install, a LTSC/IoT channel, and the "
       "composition edition. S mode is not read: reading the Code Integrity policy key from a "
       "script was blocked by Bitdefender as CMD:Heur.BZC.ZFV.Boxter (2026-10-18).",
       L(r"""$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$os = Get-CimInstance Win32_OperatingSystem
[pscustomobject]@{ EditionID=$cv.EditionID; CompositionEditionID=$cv.CompositionEditionID; ProductName=$cv.ProductName; Caption=$os.Caption; OperatingSystemSKU=$os.OperatingSystemSKU; DisplayVersion=$cv.DisplayVersion; Build="$($cv.CurrentBuild).$($cv.UBR)"; LCUVer=$cv.LCUVer; SMode='not read (see Settings > System > Activation)' }"""),
       tags=('diagnostic', 'inventory', 'baseline')),
    # ---- Hardware (8) ------------------------------------------------------------------------
    HW("disk_predict_failure", "Disk failure prediction (SMART)",
       "The drive's own SMART 'predict failure' flag for each disk -- a True here means back up "
       "now.",
       need_admin("the SMART classes refuse a standard user") +
       r"""$p = Get-CimInstance -Namespace root\wmi -ClassName MSStorageDriver_FailurePredictStatus -ErrorAction SilentlyContinue
if (-not $p) { '[INFO] No disk reports SMART prediction through this interface (NVMe drives usually do not).'; return }
$p | ForEach-Object { '{0} {1}  PredictFailure={2} Reason={3}' -f $(if ($_.PredictFailure) {'[BAD] '} else {'[OK]  '}), $_.InstanceName, $_.PredictFailure, $_.Reason }""",
       admin=True,
       tags=('diagnostic', 'hardware', 'health')),
    HW("storage_controllers", "Storage controllers and drivers",
       "SATA/NVMe/RAID controllers with their driver and version -- a vendor RAID driver vs "
       "Microsoft's inbox one explains many performance and crash issues.",
       T("Get-CimInstance Win32_PnPSignedDriver | Where-Object DeviceClass -in 'SCSIADAPTER','HDC' | Select-Object DeviceName, DriverProviderName, DriverVersion, @{n='Date';e={if ($_.DriverDate) { $_.DriverDate.ToString('yyyy-MM-dd') }}}, InfName"),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    HW("chassis", "Chassis type",
       "Desktop, laptop, all-in-one, tablet... from SMBIOS, plus whether a lid / battery exist.",
       r"""$t = @{3='Desktop';4='Low-profile desktop';6='Mini tower';7='Tower';8='Portable';9='Laptop';10='Notebook';13='All-in-one';14='Sub-notebook';30='Tablet';31='Convertible';32='Detachable';35='Mini PC'}
Get-CimInstance Win32_SystemEnclosure | ForEach-Object { "Chassis: $(($_.ChassisTypes | ForEach-Object { $t[[int]$_] }) -join ', ')  Manufacturer: $($_.Manufacturer)  Serial: $($_.SerialNumber)" }""",
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    HW("input_devices", "Keyboards, mice and HID devices",
       "Keyboards, mice and other HID devices present, with status.",
       T("Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.Class -in 'Keyboard','Mouse','HIDClass' } | Sort-Object Class, FriendlyName | Select-Object Class, FriendlyName, Status"),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    HW("device_install_history", "Recent driver and device installs",
       "The last device installations from setupapi.dev.log -- which driver was installed when "
       "a device started misbehaving.",
       r"""$log = "$env:SystemRoot\INF\setupapi.dev.log"
if (-not (Test-Path $log)) { 'NOT AVAILABLE: setupapi.dev.log is not present.'; return }
Select-String -Path $log -Pattern '^>>>\s+\[(Device Install|Install Driver).*\]' -ErrorAction SilentlyContinue | Select-Object -Last 40 | ForEach-Object { $i = $_.LineNumber; $t = (Get-Content $log -TotalCount ($i + 1) | Select-Object -Last 1) -replace '^>>>\s+Section start\s+',''; '{0}  {1}' -f $t, ($_.Line -replace '^>>>\s+','') }""",
       timeout=180,
       tags=('diagnostic', 'hardware', 'triage')),
    HW("driverstore_count", "Driver store packages",
       "How many third-party driver packages the driver store holds, by class, with the oldest "
       "-- superseded packages pile up over years.",
       need_admin("pnputil prints its help text instead of the list for a standard user") +
       r"""$o = pnputil /enum-drivers
$cls = $o | Select-String 'Class Name:' | ForEach-Object { ($_.Line -split ':',2)[1].Trim() }
"Third-party driver packages: $($cls.Count)"; $cls | Group-Object | Sort-Object Count -Descending | ForEach-Object { '{0,5}  {1}' -f $_.Count, $_.Name }""",
       admin=True,
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    HW("gpu_driver_age", "Graphics driver age",
       "Each GPU's driver date and how old it is.",
       CHK + r"""Get-CimInstance Win32_VideoController | ForEach-Object { $d = $_.DriverDate; $age = if ($d) { ((Get-Date) - $d).Days } else { $null }
  "[INFO] $($_.Name) -- driver $($_.DriverVersion), dated $(if ($d) { $d.ToString('yyyy-MM-dd') } else { '?' }) ($age days old)" }""",
       tags=('diagnostic', 'hardware', 'health')),
    HW("pci_devices", "PCI devices",
       "Every PCI device present with its class and vendor/device id.",
       T("Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object InstanceId -like 'PCI*' | Sort-Object Class | Select-Object Class, FriendlyName, @{n='VEN_DEV';e={($_.InstanceId -split '\\\\')[1] -replace '&SUBSYS.*',''}}, Status"),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    # ---- Storage (8) -------------------------------------------------------------------------
    DS("big_files_c", "Largest files on the system drive",
       "The 40 biggest files on the system drive (skipping the page, swap and hibernation "
       "files). Slow on a full disk.",
       r"""Get-ChildItem "$env:SystemDrive\" -Recurse -File -Force -ErrorAction SilentlyContinue 2>$null | Where-Object { $_.Name -notin 'pagefile.sys','swapfile.sys','hiberfil.sys' } | Sort-Object Length -Descending | Select-Object -First 40 | ForEach-Object { '{0,9:N2} GB  {1:yyyy-MM-dd}  {2}' -f ($_.Length/1GB), $_.LastWriteTime, $_.FullName }""",
       timeout=900,
       tags=('diagnostic', 'storage')),
    DS("recycle_bins", "Recycle Bin size per drive",
       "How much each drive's Recycle Bin holds, for every user (sizes of the $Recycle.Bin "
       "folders).",
       r"""Get-PSDrive -PSProvider FileSystem | Where-Object { Test-Path "$($_.Root)`$Recycle.Bin" } | ForEach-Object { $m = Get-ChildItem "$($_.Root)`$Recycle.Bin" -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum
  '{0}  {1,9:N2} GB  {2} files' -f $_.Root, ($m.Sum/1GB), $m.Count }""",
       note="other users' recycle bins are only counted elevated", timeout=300,
       tags=('diagnostic', 'storage')),
    DS("downloads_folder", "Downloads folder: size and oldest files",
       "Size of your Downloads folder and the 20 oldest, largest items in it.",
       r"""$d = (New-Object -ComObject Shell.Application).Namespace('shell:Downloads').Self.Path
$f = Get-ChildItem $d -Recurse -File -ErrorAction SilentlyContinue
'{0}: {1:N2} GB in {2} files' -f $d, (($f | Measure-Object Length -Sum).Sum/1GB), $f.Count
$f | Sort-Object LastWriteTime | Select-Object -First 20 | ForEach-Object { '{0:yyyy-MM-dd}  {1,9:N1} MB  {2}' -f $_.LastWriteTime, ($_.Length/1MB), $_.Name }""",
       timeout=300,
       tags=('diagnostic', 'storage')),
    DS("windows_old", "Windows.old and upgrade leftovers",
       "Size of Windows.old, $WINDOWS.~BT and $WINDOWS.~WS -- previous-installation files a "
       "feature update leaves for rollback.",
       r"""foreach ($p in 'Windows.old','$WINDOWS.~BT','$WINDOWS.~WS','ESD') { $f = Join-Path "$env:SystemDrive\" $p
  if (Test-Path $f) { $s = (Get-ChildItem $f -Recurse -Force -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum; '[INFO] {0,-14} {1,8:N2} GB' -f $p, ($s/1GB) } else { "[OK]   $p -- not present" } }""",
       timeout=600,
       tags=('diagnostic', 'storage', 'health')),
    DS("dirty_bit", "File system dirty bit",
       "Whether NTFS flagged a volume as dirty (chkdsk will run at next boot).",
       need_admin("fsutil dirty refuses a standard user") +
       r"""Get-Volume | Where-Object { $_.DriveLetter -and $_.FileSystem -eq 'NTFS' } | ForEach-Object { $r = fsutil dirty query "$($_.DriveLetter):" 2>&1; if ($r -match 'is Dirty') { "[WARN] $r" } else { "[OK]   $r" } }""",
       admin=True,
       tags=('diagnostic', 'storage', 'health')),
    DS("swap_files", "Page, swap and hibernation files",
       "Sizes of pagefile.sys, swapfile.sys (Store apps) and hiberfil.sys on each drive.",
       r"""$found = 0
Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Root -match '^[A-Z]:\\$' } | ForEach-Object { $root = $_.Root
  Get-ChildItem $root -Force -File -Filter *.sys -ErrorAction SilentlyContinue | Where-Object Name -in 'pagefile.sys','swapfile.sys','hiberfil.sys' | ForEach-Object { $found++; '{0,-4} {1,-13} {2,8:N2} GB' -f $root, $_.Name, ($_.Length/1GB) } }
if (-not $found) { '[INFO] No page, swap or hibernation file found at a drive root.' }""",
       tags=('diagnostic', 'storage')),
    DS("do_cache", "Delivery Optimization cache",
       "The Delivery Optimization cache folder and its size.",
       r"""$c = Get-DeliveryOptimizationPerfSnap -ErrorAction SilentlyContinue
if ($c) { 'Cache size: {0:N2} GB  (files: {1})' -f ($c.CacheSizeBytes/1GB), $c.Files } else { '[INFO] Delivery Optimization did not report its cache.' }""",
       note="the cache figures need elevation", admin=True,
       tags=('diagnostic', 'storage')),
    DS("volume_free_trend", "Free space with low-space thresholds",
       "Free space per volume against two thresholds (10% / 15 GB) -- the check fleet "
       "monitoring runs.",
       r"""Get-Volume | Where-Object { $_.DriveLetter -and $_.Size } | ForEach-Object { $pct = 100 * $_.SizeRemaining / $_.Size; $gb = $_.SizeRemaining / 1GB
  $tag = if ($pct -lt 10 -or $gb -lt 15) { '[BAD] ' } elseif ($pct -lt 20) { '[WARN]' } else { '[OK]  ' }
  '{0} {1}:  {2,7:N1} GB free  ({3,4:N1}%)' -f $tag, $_.DriveLetter, $gb, $pct }""",
       tags=('diagnostic', 'storage', 'health')),
    # ---- Performance (6) ---------------------------------------------------------------------
    PF("top_disk_io", "Processes doing the most disk I/O",
       "Disk bytes read and written per process over five seconds.",
       r"""$c = Get-Counter '\Process(*)\IO Data Bytes/sec' -SampleInterval 1 -MaxSamples 5 -ErrorAction SilentlyContinue
$c.CounterSamples | Where-Object { $_.InstanceName -notin '_total','idle' } | Group-Object InstanceName | ForEach-Object { [pscustomobject]@{ Process=$_.Name; MBps=[math]::Round((($_.Group | Measure-Object CookedValue -Average).Average)/1MB,2) } } | Sort-Object MBps -Descending | Select-Object -First 15 | Format-Table -AutoSize | Out-String -Width 220""",
       timeout=60,
       tags=('diagnostic', 'performance')),
    PF("pool_memory", "Kernel pool memory",
       "Paged and non-paged pool use -- a driver leaking non-paged pool is a classic cause of "
       "'out of memory' with free RAM.",
       r"""Get-Counter '\Memory\Pool Nonpaged Bytes','\Memory\Pool Paged Bytes','\Memory\System Cache Resident Bytes','\Memory\Modified Page List Bytes' | ForEach-Object { $_.CounterSamples } | ForEach-Object { '{0,-50} {1,10:N0} MB' -f ($_.Path -replace '^\\\\[^\\]+',''), ($_.CookedValue/1MB) }""",
       tags=('diagnostic', 'performance')),
    PF("dpc_interrupts", "Interrupts and DPC time (5 s)",
       "% time spent in interrupts and deferred procedure calls -- high values mean a driver is "
       "hogging the CPU (audio crackles, stutter).",
       r"""$c = Get-Counter '\Processor(_Total)\% Interrupt Time','\Processor(_Total)\% DPC Time','\System\Context Switches/sec' -SampleInterval 1 -MaxSamples 5 -ErrorAction SilentlyContinue
$c.CounterSamples | Group-Object Path | ForEach-Object { $a = ($_.Group | Measure-Object CookedValue -Average).Average; '{0} {1,-50} {2,12:N2}' -f $(if ($_.Name -match 'Interrupt|DPC' -and $a -gt 5) {'[WARN]'} else {'[OK]  '}), ($_.Name -replace '^\\\\[^\\]+',''), $a }""",
       timeout=60,
       tags=('diagnostic', 'performance', 'health')),
    PF("memory_compression", "Memory compression",
       "The Memory Compression process size and whether memory compression is enabled.",
       r"""$p = Get-Process 'Memory Compression' -ErrorAction SilentlyContinue
"Memory Compression working set: $(if ($p) { '{0:N0} MB' -f ($p.WorkingSet64/1MB) } else { 'not visible (needs elevation)' })"
try { (Get-MMAgent -ErrorAction Stop | Format-List | Out-String).Trim() } catch { 'Get-MMAgent needs administrator rights.' }""",
       tags=('diagnostic', 'performance')),
    PF("visual_settings", "Visual effects and GPU scheduling",
       "Visual effects mode, hardware-accelerated GPU scheduling and Game Mode.",
       REG + r"""'VisualFXSetting (0 auto,1 appearance,2 performance,3 custom): ' + (RegVal 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' VisualFXSetting)
'HwSchMode (2 = GPU scheduling on): ' + (RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' HwSchMode)
'Game Mode (AutoGameModeEnabled): ' + (RegVal 'HKCU:\Software\Microsoft\GameBar' AutoGameModeEnabled)""",
       tags=('diagnostic', 'performance')),
    PF("thread_count_top", "Processes with the most threads",
       "Thread count per process -- runaway thread creation shows here first.",
       T("Get-Process | Sort-Object { $_.Threads.Count } -Descending | Select-Object -First 15 Name, Id, @{n='Threads';e={$_.Threads.Count}}, HandleCount"),
       tags=('diagnostic', 'performance')),
    # ---- Event logs (12) ---------------------------------------------------------------------
    EL("logon_logoff_winlogon", "Sign-in and sign-out times (Winlogon)",
       "Customer Experience Winlogon 7001 (logon) and 7002 (logoff) records -- when this PC "
       "was used, readable without admin.",
       EV + "EvList @{LogName='System'; ProviderName='Microsoft-Windows-Winlogon'; Id=7001,7002} 14 80",
       tags=('diagnostic', 'triage', 'health')),
    EL("service_crashes", "Service crashes grouped",
       "Services that terminated unexpectedly (7031, 7034) in 30 days, grouped.",
       EV + "EvSum @{LogName='System'; ProviderName='Service Control Manager'; Id=7031,7034} 30",
       tags=('diagnostic', 'triage', 'health')),
    EL("msi_installs", "Software installs and removals (MSI)",
       "MsiInstaller events: products installed (1033/11707), removed (1034/11724) and failed "
       "(11708) in 30 days.",
       EV + "EvList @{LogName='Application'; ProviderName='MsiInstaller'; Id=1033,1034,11707,11708,11724} 30 80",
       tags=('diagnostic', 'triage', 'health')),
    EL("defender_events", "Defender events summary",
       "Defender's operational log grouped by event -- scans, detections, updates, config "
       "changes.",
       EV + "EvSum @{LogName='Microsoft-Windows-Windows Defender/Operational'} 7",
       tags=('diagnostic', 'triage', 'health')),
    EL("time_changes", "System time changes",
       "When the clock was changed and by whom (Kernel-General 1) -- clock jumps break "
       "Kerberos and log timelines.",
       EV + "EvList @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-General'; Id=1} 30 40",
       tags=('diagnostic', 'triage', 'health')),
    EL("gp_errors", "Group Policy errors",
       "Group Policy processing errors (1085, 1096, 1129, 1058, 1030) in 7 days.",
       EV + "EvList @{LogName='System'; ProviderName='Microsoft-Windows-GroupPolicy'; Level=1,2,3} 7 60",
       tags=('diagnostic', 'triage', 'health')),
    EL("log_cleared_any", "Event logs that were cleared",
       "Any log cleared (System 104, Security 1102) in 90 days -- evidence being erased.",
       EV + r"""EvList @{LogName='System'; Id=104} 90 40
""" + "try { Get-WinEvent -FilterHashtable @{LogName='Security'; Id=1102} -MaxEvents 20 -ErrorAction Stop | ForEach-Object { \"[BAD]  $($_.TimeCreated)  Security log cleared\" } } catch { if ($_.Exception.Message -match 'No events') { '[OK]   Security log: never cleared in its retained history.' } else { 'NOTE: the Security log needs administrator rights.' } }",
       tags=('diagnostic', 'triage', 'health')),
    EL("kernel_boot_errors", "Boot-time errors",
       "Errors and warnings logged in the first minutes after the last boot -- drivers and "
       "services that fail at startup.",
       r"""$boot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2,3; StartTime=$boot; EndTime=$boot.AddMinutes(10)} -ErrorAction SilentlyContinue | ForEach-Object { '{0} {1:HH:mm:ss} {2,5} {3,-34} {4}' -f $(if ($_.Level -le 2) {'[BAD] '} else {'[WARN]'}), $_.TimeCreated, $_.Id, $_.ProviderName, (($_.Message -split "`r?`n")[0]) }
"Boot at $boot" """,
       tags=('diagnostic', 'triage', 'health')),
    EL("app_errors_grouped", "Application log errors grouped (30 days)",
       "Every error source in the Application log over 30 days with counts.",
       EV + "EvSum @{LogName='Application'; Level=1,2} 30",
       tags=('diagnostic', 'triage', 'health')),
    EL("system_errors_grouped", "System log errors grouped (30 days)",
       "Every error source in the System log over 30 days with counts.",
       EV + "EvSum @{LogName='System'; Level=1,2} 30",
       tags=('diagnostic', 'triage', 'health')),
    EL("bits_errors", "Background transfer (BITS) errors",
       "BITS client errors -- downloads by Windows Update, Office and Store that failed.",
       EV + "EvList @{LogName='Microsoft-Windows-Bits-Client/Operational'; Level=2,3} 7 40",
       tags=('diagnostic', 'triage', 'health')),
    EL("ntfs_events", "NTFS and volume events (90 days)",
       "NTFS self-healing, corruption and volume events over 90 days.",
       EV + "EvSum @{LogName='System'; ProviderName='Ntfs','Microsoft-Windows-Ntfs'} 90",
       tags=('diagnostic', 'triage', 'health')),
    # ---- Network (12) ------------------------------------------------------------------------
    NW("wifi_survey", "Nearby Wi-Fi networks",
       "Every Wi-Fi network in range with BSSID, signal, channel and security -- channel "
       "congestion and a rogue access point with your SSID show here.",
       "netsh wlan show networks mode=bssid", kind=CMD,
       tags=('diagnostic', 'network')),
    NW("adapter_advanced", "Adapter advanced properties",
       "Driver settings per physical adapter: speed/duplex, energy-efficient Ethernet, wake on "
       "LAN, jumbo frames, offloads.",
       T("Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Get-NetAdapterAdvancedProperty -ErrorAction SilentlyContinue | Select-Object Name, DisplayName, DisplayValue"),
       tags=('diagnostic', 'network')),
    NW("port_proxy", "Port forwarding rules (portproxy)",
       "netsh interface portproxy rules -- silently forward a local port elsewhere; attackers "
       "use them to tunnel.",
       r"""$o = netsh interface portproxy show all
if (-not ($o | Where-Object { $_ -match '\d+\.\d+|\*' })) { '[OK]   No port forwarding rules.' } else { $o | ForEach-Object { "[WARN] $_" } }""",
       tags=('diagnostic', 'network', 'health')),
    NW("smb_client_config", "SMB client security settings",
       "Client-side SMB signing, encryption and insecure guest logons.",
       L("Get-SmbClientConfiguration | Select-Object RequireSecuritySignature, EnableSecuritySignature, EnableInsecureGuestLogons, EncryptionCiphers, EnableMultiChannel"),
       tags=('diagnostic', 'network')),
    NW("netbios_cache", "NetBIOS name cache and names",
       "Local NetBIOS names and the remote name cache (nbtstat).",
       r"""nbtstat -n; nbtstat -c""",
       tags=('diagnostic', 'network')),
    NW("network_providers", "Network provider order",
       "The order Windows tries network providers (SMB, WebDAV, RDP, VirtualBox...) when "
       "opening a UNC path -- a slow provider first makes every \\\\server path slow.",
       REG + r"""(RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\NetworkProvider\Order' ProviderOrder) -split ','""",
       tags=('diagnostic', 'network')),
    NW("metered", "Metered connections",
       "Whether each network profile is metered (updates and sync are throttled on metered "
       "networks).",
       r"""$p = Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\NetworkList\DefaultMediaCost' -ErrorAction SilentlyContinue
(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\NetworkList\DefaultMediaCost' -ErrorAction SilentlyContinue).PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { '{0,-10} {1}' -f $_.Name, $(if ($_.Value -eq 2) {'metered by default'} else {'not metered'}) }""",
       tags=('diagnostic', 'network')),
    NW("firewall_logging", "Firewall logging",
       "Whether the firewall logs dropped and allowed connections, and where.",
       T("Get-NetFirewallProfile | Select-Object Name, LogBlocked, LogAllowed, LogFileName, LogMaxSizeKilobytes"),
       tags=('diagnostic', 'network')),
    NW("firewall_log_drops", "Firewall log: most dropped",
       "Top source addresses and ports in the firewall's drop log, when logging is on.",
       r"""$f = [Environment]::ExpandEnvironmentVariables((Get-NetFirewallProfile -Name Public).LogFileName)
if (-not (Test-Path $f)) { "[INFO] No firewall log at $f -- logging of drops is off."; return }
$rows = Get-Content $f -Tail 20000 -ErrorAction SilentlyContinue | Where-Object { $_ -match ' DROP ' } | ForEach-Object { $p = $_ -split ' '; "$($p[2]) $($p[4]) -> $($p[5]):$($p[7])" }
$rows | Group-Object | Sort-Object Count -Descending | Select-Object -First 25 | ForEach-Object { '{0,6}  {1}' -f $_.Count, $_.Name }""",
       note="the firewall log is only readable elevated",
       tags=('diagnostic', 'network')),
    NW("hotspot_ics", "Mobile hotspot and Internet Connection Sharing",
       "Whether Internet Connection Sharing or the mobile hotspot is active -- this PC routing "
       "other devices' traffic.",
       r"""$s = Get-Service SharedAccess,icssvc -ErrorAction SilentlyContinue | Select-Object Name, Status, StartType
$s | Format-Table -AutoSize | Out-String -Width 220""",
       tags=('diagnostic', 'network')),
    NW("ncsi_settings", "Network connectivity indicator settings",
       "Where Windows checks for internet access (NCSI probe host) and whether active probing "
       "is disabled -- a corporate proxy breaking this shows 'No internet' with working "
       "internet.",
       REG + r"""RegShow 'HKLM:\SYSTEM\CurrentControlSet\Services\NlaSvc\Parameters\Internet'""",
       tags=('diagnostic', 'network')),
    NW("dns_suffix", "DNS suffixes and registration",
       "Primary DNS suffix, the search list and per-adapter registration settings.",
       r"""Get-DnsClientGlobalSetting | Format-List | Out-String -Width 220
Get-DnsClient | Where-Object { $_.ConnectionSpecificSuffix -or $_.RegisterThisConnectionsAddress } | Select-Object InterfaceAlias, ConnectionSpecificSuffix, RegisterThisConnectionsAddress | Format-Table -AutoSize | Out-String -Width 220""",
       tags=('diagnostic', 'network')),
    # ---- Security (14) -----------------------------------------------------------------------
    SC("bits_jobs", "BITS jobs (all users)",
       "Background Intelligent Transfer jobs -- legitimate updaters use them, and so does "
       "malware that wants to download and persist.",
       need_admin("other users' BITS jobs are only listed elevated") +
       r"""$j = Get-BitsTransfer -AllUsers -ErrorAction SilentlyContinue
if (-not $j) { '[OK]   No BITS jobs.'; return }
$j | ForEach-Object { '[INFO] {0,-30} {1,-12} {2}  -> {3}' -f $_.DisplayName, $_.JobState, $_.OwnerAccount, (($_.FileList | Select-Object -First 1).RemoteName) }""",
       admin=True,
       tags=('diagnostic', 'security', 'health')),
    SC("accessibility_backdoor", "Accessibility tools integrity",
       "Signatures of sethc, utilman, osk, magnify, narrator and displayswitch -- replacing one "
       "with cmd.exe gives a SYSTEM shell at the sign-in screen.",
       r"""foreach ($n in 'sethc.exe','utilman.exe','osk.exe','Magnify.exe','Narrator.exe','DisplaySwitch.exe','AtBroker.exe') { $p = Join-Path "$env:SystemRoot\System32" $n
  if (-not (Test-Path $p)) { "[INFO] $n -- not present"; continue }
  $s = Get-AuthenticodeSignature $p; $v = (Get-Item $p).VersionInfo
  $ok = $s.Status -eq 'Valid' -and $v.OriginalFilename -notmatch '^cmd|^powershell|^explorer'
  '{0} {1,-18} signature {2,-10} original name {3}' -f $(if ($ok) {'[OK]  '} else {'[BAD] '}), $n, $s.Status, $v.OriginalFilename }""",
       tags=('diagnostic', 'security', 'health')),
    SC("cached_logons", "Cached domain logons",
       "How many domain logons Windows caches for offline sign-in (CachedLogonsCount) -- "
       "hashes an attacker can crack.",
       REG + CHK + r"""$c = RegVal 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' CachedLogonsCount
Chk ([int]$c -le 4) 'Cached logons' "CachedLogonsCount=$c (default 10)" """,
       tags=('diagnostic', 'security', 'health')),
    SC("anonymous_restrictions", "Anonymous access restrictions",
       "RestrictAnonymous, RestrictAnonymousSAM and EveryoneIncludesAnonymous -- what an "
       "unauthenticated user can enumerate.",
       REG + CHK + r"""$k = 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa'
Chk ((RegVal $k RestrictAnonymousSAM) -eq 1) 'RestrictAnonymousSAM' "$(RegVal $k RestrictAnonymousSAM)"
Chk ((RegVal $k RestrictAnonymous) -ge 1) 'RestrictAnonymous' "$(RegVal $k RestrictAnonymous)"
Chk ((RegVal $k EveryoneIncludesAnonymous) -ne 1) 'EveryoneIncludesAnonymous off' "$(RegVal $k EveryoneIncludesAnonymous)" """,
       tags=('diagnostic', 'security', 'health')),
    SC("ntlm_settings", "NTLM restrictions and auditing",
       "NTLM outgoing/incoming restrictions and audit settings (MSV1_0) -- the first step of "
       "an NTLM phase-out.",
       REG + r"""RegShow 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa\MSV1_0'""",
       tags=('diagnostic', 'security')),
    SC("kerberos_settings", "Kerberos encryption types",
       "Which Kerberos encryption types are allowed by policy -- RC4 still allowed is a "
       "Kerberoasting risk.",
       REG + r"""$v = RegVal 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\Kerberos\Parameters' SupportedEncryptionTypes
if ($null -eq $v) { '[INFO] SupportedEncryptionTypes not set by policy (OS defaults).' } else { $m = @{1='DES-CBC-CRC';2='DES-CBC-MD5';4='RC4';8='AES128';16='AES256'}; $on = $m.Keys | Where-Object { $v -band $_ } | ForEach-Object { $m[$_] }; '{0} {1}' -f $(if ($v -band 4) {'[WARN]'} else {'[OK]  '}), ($on -join ', ') }""",
       tags=('diagnostic', 'security', 'health')),
    SC("amsi_providers", "AMSI providers and PowerShell language mode",
       "Antimalware Scan Interface providers registered (which AV scans scripts) and the "
       "current PowerShell language mode.",
       r"""Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\AMSI\Providers' -ErrorAction SilentlyContinue | ForEach-Object { $c = $_.PSChildName; $n = (Get-ItemProperty "Registry::HKEY_CLASSES_ROOT\CLSID\$c" -ErrorAction SilentlyContinue).'(default)'; "[INFO] AMSI provider $c  $n" }
"Language mode: $($ExecutionContext.SessionState.LanguageMode)" """,
       tags=('diagnostic', 'security')),
    SC("wsh_status", "Windows Script Host",
       "Whether Windows Script Host (wscript/cscript, .vbs/.js) is enabled -- disabling it "
       "blocks a common email-attachment attack.",
       REG + CHK + r"""$m = RegVal 'HKLM:\SOFTWARE\Microsoft\Windows Script Host\Settings' Enabled; $u = RegVal 'HKCU:\SOFTWARE\Microsoft\Windows Script Host\Settings' Enabled
Chk $(if ($m -eq 0 -or $u -eq 0) { $true } else { $null }) 'Windows Script Host disabled' "machine=$m user=$u (absent = enabled)" """,
       tags=('diagnostic', 'security', 'health')),
    SC("office_macros", "Office macro and internet-content settings",
       "Per-application VBA warning level and 'block macros from the internet' for Word, Excel "
       "and PowerPoint (policy and user).",
       r"""foreach ($app in 'Word','Excel','PowerPoint') { foreach ($root in 'HKCU:\Software\Policies\Microsoft\Office\16.0','HKCU:\Software\Microsoft\Office\16.0') { $k = "$root\$app\Security"
  $p = Get-ItemProperty $k -ErrorAction SilentlyContinue; if ($p) { '{0,-10} {1,-48} VBAWarnings={2} blockcontentexecutionfrominternet={3}' -f $app, ($root -replace '^HKCU:\\Software\\',''), $p.VBAWarnings, $p.blockcontentexecutionfrominternet } } }
'(absent = Office defaults: macros from the internet are blocked since 2022)'""",
       tags=('diagnostic', 'security')),
    SC("mark_of_web", "Mark of the Web handling",
       "Whether Windows records the download zone on files (SaveZoneInformation) -- turning it "
       "off disables SmartScreen and Office's protected view for downloads.",
       REG + CHK + r"""$v = RegVal 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Policies\Attachments' SaveZoneInformation
Chk ($v -ne 1) 'Zone information kept on downloads' "SaveZoneInformation=$v (1 = DISABLED, absent = on)" """,
       tags=('diagnostic', 'security', 'health')),
    SC("admin_shares", "Administrative shares",
       "Whether the hidden C$/ADMIN$ shares are created (AutoShareWks) and exist now.",
       REG + r"""'AutoShareWks: ' + (RegVal 'HKLM:\SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters' AutoShareWks) + ' (absent = created)'
Get-SmbShare -Special $true -ErrorAction SilentlyContinue | Select-Object Name, Path | Format-Table -AutoSize | Out-String -Width 220""",
       tags=('diagnostic', 'security')),
    SC("remote_registry", "Remote Registry and remote management services",
       "Remote Registry, WinRM, RemoteAccess and SSH server service states -- every way in "
       "besides RDP.",
       CHK + r"""foreach ($n in 'RemoteRegistry','WinRM','RemoteAccess','sshd','TermService','SNMP') { $s = Get-Service $n -ErrorAction SilentlyContinue
  if (-not $s) { "[INFO] $n -- not installed"; continue }
  Chk ($s.Status -ne 'Running') $n "$($s.Status), $($s.StartType)" }""",
       tags=('diagnostic', 'security', 'health')),
    SC("defender_quarantine", "Defender quarantine",
       "Items Defender has in quarantine (MpCmdRun -Restore -ListAll, read-only listing).",
       need_admin("the quarantine is only readable elevated") +
       r"""$m = Get-ChildItem "$env:ProgramData\Microsoft\Windows Defender\Platform" -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
$exe = if ($m) { Join-Path $m.FullName 'MpCmdRun.exe' } else { "$env:ProgramFiles\Windows Defender\MpCmdRun.exe" }
& $exe -Restore -ListAll""", admin=True,
       tags=('diagnostic', 'security')),
    SC("lsass_protection", "LSASS protection and credential dumping defences",
       "RunAsPPL, Credential Guard, the ASR rule that blocks LSASS credential theft and WDigest "
       "-- everything between an attacker and the passwords in memory.",
       REG + CHK + ADM + r"""$l = 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa'
Chk ((RegVal $l RunAsPPL) -in 1,2) 'LSA protection (RunAsPPL)' "$(RegVal $l RunAsPPL)"
$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction SilentlyContinue
Chk ($dg.SecurityServicesRunning -contains 1) 'Credential Guard running' "$($dg.SecurityServicesRunning -join ',')"
$p = Get-MpPreference -ErrorAction SilentlyContinue; $i = [array]::IndexOf(@($p.AttackSurfaceReductionRules_Ids), '9e6c4e1f-7d60-472f-ba1a-a39ef669e4b2')
Chk $(if ($i -ge 0) { @($p.AttackSurfaceReductionRules_Actions)[$i] -eq 1 } else { $false }) 'ASR: block credential stealing from LSASS' $(if ($i -ge 0) { "action $(@($p.AttackSurfaceReductionRules_Actions)[$i])" } else { 'not configured' })
Chk ((RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest' UseLogonCredential) -ne 1) 'WDigest off' "$(RegVal 'HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest' UseLogonCredential)" """,
       tags=('diagnostic', 'security', 'health')),
    # ---- Users & groups (5) ------------------------------------------------------------------
    US("old_passwords", "Local accounts with old passwords",
       "Enabled local accounts whose password is over a year old, or was never set.",
       CHK + r"""Get-LocalUser | Where-Object Enabled | ForEach-Object { $age = if ($_.PasswordLastSet) { ((Get-Date) - $_.PasswordLastSet).Days } else { $null }
  Chk $(if ($null -eq $age) { $false } else { $age -lt 365 }) $_.Name $(if ($null -eq $age) { 'password never set' } else { "$age days old" }) }""",
       tags=('diagnostic', 'security', 'health')),
    US("accounts_created", "Local accounts created or changed (Security log)",
       "4720 created, 4722 enabled, 4724 password reset, 4726 deleted, 4732 added to a group -- "
       "90 days.",
       need_admin("the Security log is only readable elevated") + EV +
       "EvList @{LogName='Security'; Id=4720,4722,4724,4726,4732,4733} 90 80", admin=True,
       tags=('diagnostic', 'security', 'health')),
    US("profile_sizes", "User profile sizes",
       "Disk space used by each profile folder.",
       r"""Get-CimInstance Win32_UserProfile | Where-Object { -not $_.Special -and (Test-Path $_.LocalPath) } | ForEach-Object { $s = (Get-ChildItem $_.LocalPath -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum; '{0,9:N2} GB  {1}' -f ($s/1GB), $_.LocalPath }""",
       note="other users' profiles are only measured elevated", timeout=900,
       tags=('diagnostic', 'security')),
    US("admins_last_logon", "Administrators and their last logon",
       "Every member of the local Administrators group with its last logon (local accounts).",
       r"""Get-LocalGroupMember -SID S-1-5-32-544 | ForEach-Object { $u = if ($_.PrincipalSource -eq 'Local') { Get-LocalUser -SID $_.SID -ErrorAction SilentlyContinue }; '{0,-30} {1,-16} enabled={2}  last logon={3}' -f $_.Name, $_.PrincipalSource, $u.Enabled, $u.LastLogon }""",
       tags=('diagnostic', 'security')),
    US("sessions_detail", "Sessions and their processes",
       "Each logon session with the user and process count -- disconnected sessions keep "
       "using memory.",
       need_admin("Get-Process -IncludeUserName throws for a standard user") + r"""Get-Process -IncludeUserName -ErrorAction SilentlyContinue | Group-Object SessionId, UserName | Sort-Object Name | ForEach-Object { '{0,-40} {1,4} processes  {2,8:N0} MB' -f $_.Name, $_.Count, (($_.Group | Measure-Object WorkingSet64 -Sum).Sum/1MB) }
if (-not $?) { 'Process owners are only visible elevated.' }""",
       note="process owners are only shown elevated", admin=True,
       tags=('diagnostic', 'security')),
    # ---- Updates & servicing (6) -------------------------------------------------------------
    UP("last_update_times", "Last update search and install",
       "When Windows Update last searched successfully and last installed something.",
       r"""$r = (New-Object -ComObject Microsoft.Update.AutoUpdate).Results
"Last search success : $($r.LastSearchSuccessDate)"
"Last install success: $($r.LastInstallationSuccessDate)"
$h = Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1
"Newest hotfix       : $($h.HotFixID) on $($h.InstalledOn)" """,
       tags=('diagnostic', 'triage')),
    UP("patch_age", "How current is this PC (patch age)",
       "Days since the newest installed update -- more than ~45 days means a missed Patch "
       "Tuesday.",
       CHK + r"""$h = Get-HotFix | Where-Object InstalledOn | Sort-Object InstalledOn -Descending | Select-Object -First 1
$age = ((Get-Date) - $h.InstalledOn).Days; Chk ($age -le 45) 'Newest update' "$($h.HotFixID), $age days ago" """,
       tags=('diagnostic', 'triage', 'health')),
    UP("store_autoupdate", "Microsoft Store automatic updates",
       "Whether Store apps update automatically (policy AutoDownload and the user setting).",
       REG + r"""'Policy AutoDownload (2 = off, 4 = on): ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore' AutoDownload)
'Policy DisableStoreApps: ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore' DisableStoreApps)""",
       tags=('diagnostic', 'triage')),
    UP("driver_update_policy", "Drivers through Windows Update",
       "Whether driver updates are excluded from Windows Update and device metadata downloads "
       "are blocked.",
       REG + r"""'ExcludeWUDriversInQualityUpdate: ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate' ExcludeWUDriversInQualityUpdate)
'SearchOrderConfig (0 = never from WU): ' + (RegVal 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\DriverSearching' SearchOrderConfig)
'PreventDeviceMetadataFromNetwork: ' + (RegVal 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Device Metadata' PreventDeviceMetadataFromNetwork)""",
       tags=('diagnostic', 'triage')),
    UP("pending_packages", "Packages pending install or removal",
       "Servicing packages waiting for a restart (Install Pending / Uninstall Pending).",
       need_admin("Get-WindowsPackage needs elevation") +
       r"""$p = Get-WindowsPackage -Online | Where-Object PackageState -match 'Pending'
if (-not $p) { '[OK]   No package is pending.'; return }
$p | ForEach-Object { "[WARN] $($_.PackageName) -- $($_.PackageState)" }""", admin=True, timeout=300,
       tags=('diagnostic', 'triage', 'health')),
    UP("update_errors_decoded", "Recent update failures decoded",
       "Failed updates from the WU history with the error code and its known meaning.",
       r"""$known = @{ '0x80070002'='files missing / interrupted download'; '0x800F0922'='not enough System Reserved space or VPN blocking'; '0x80073712'='component store corrupted (run DISM RestoreHealth)'; '0x8024402C'='proxy / DNS cannot reach WU'; '0x80070005'='access denied'; '0x800F081F'='source files not found'; '0x80240034'='download failed'; '0x8007000D'='corrupt data in update' }
$s = New-Object -ComObject Microsoft.Update.Session; $q = $s.CreateUpdateSearcher(); $n = $q.GetTotalHistoryCount()
$f = if ($n) { $q.QueryHistory(0, [Math]::Min(200, $n)) | Where-Object { $_.ResultCode -in 4,5 } } else { @() }
if (-not $f) { '[OK]   No failed update in recent history.'; return }
$f | ForEach-Object { $c = '0x{0:X8}' -f $_.HResult; '[BAD]  {0:yyyy-MM-dd}  {1}  {2}  {3}' -f $_.Date, $c, $known[$c], $_.Title }""",
       timeout=180,
       tags=('diagnostic', 'triage', 'health')),
    # ---- Services & processes (5) ------------------------------------------------------------
    SV("services_user_accounts", "Services running as a user account",
       "Services that log on as a named user (not LocalSystem/Network/Local Service) -- their "
       "passwords are stored as LSA secrets and break when the password changes.",
       r"""$s = Get-CimInstance Win32_Service | Where-Object { $_.StartName -and $_.StartName -notmatch '^(LocalSystem|NT AUTHORITY\\|NT SERVICE\\)' }
if (-not $s) { '[OK]   No service runs as a user account.'; return }
$s | ForEach-Object { "[INFO] $($_.Name) as $($_.StartName) ($($_.State))" }""",
       tags=('diagnostic', 'triage', 'security', 'health')),
    SV("services_new", "Services installed recently (30 days)",
       "Service Control Manager 7045 records: every service and driver installed in 30 days.",
       EV + "EvList @{LogName='System'; ProviderName='Service Control Manager'; Id=7045} 30 80",
       tags=('diagnostic', 'triage', 'health')),
    SV("processes_elevated", "Process details in your session",
       "Process names, IDs and owners in your session. This does not read token "
       "elevation: administrator rights are needed to inspect other processes, and "
       "even an elevated run of this listing cannot identify elevated tokens.",
       need_admin("Get-Process -IncludeUserName throws for a standard user") + r"""Get-Process -IncludeUserName -ErrorAction Stop | Where-Object { $_.Id -notin 0,4 -and $_.SessionId -eq (Get-Process -Id $PID).SessionId } | Sort-Object ProcessName | Select-Object ProcessName, Id, UserName | Format-Table -AutoSize | Out-String -Width 220
'[INFO] Token elevation was not queried; this list includes elevated and non-elevated processes.' 
""",
       note="process owners require administrator rights; token elevation is not checked", admin=True,
       tags=('diagnostic', 'triage', 'security')),
    SV("service_failures_config", "Services with no recovery action",
       "Automatic services whose recovery is 'take no action' -- they stay down after a crash.",
       r"""$none = Get-CimInstance Win32_Service -Filter "StartMode='Auto'" | ForEach-Object { $q = sc.exe qfailure $_.Name 2>$null; if (-not ($q -match 'RESTART|REBOOT|RUN PROCESS')) { $_.Name } } | Sort-Object
if (-not $none) { '[OK]   Every automatic service has a recovery action.' } else { $none | ForEach-Object { "[INFO] $_ -- no recovery action" } }""",
       timeout=240,
       tags=('diagnostic', 'triage', 'health')),
    SV("process_modules_odd", "DLLs loaded from user folders",
       "DLLs loaded into running processes from Temp, AppData or Downloads -- DLL hijacking "
       "and side-loading leave this trace.",
       r"""Get-Process | ForEach-Object { $p = $_; try { $p.Modules | Where-Object { $_.FileName -match '\\(Temp|Downloads)\\|\\AppData\\(Local|Roaming)\\(?!Microsoft\\|Programs\\)' } | ForEach-Object { '[WARN] {0,-24} {1}' -f $p.ProcessName, $_.FileName } } catch { } } | Sort-Object -Unique | Select-Object -First 80""",
       note="other users' processes are skipped", timeout=300,
       tags=('diagnostic', 'triage', 'security', 'health')),
    # ---- Startup & tasks (6) -----------------------------------------------------------------
    ST("tasks_next_24h", "Tasks that will run in the next 24 hours",
       "Enabled scheduled tasks with a next run time in the next day.",
       r"""Get-ScheduledTask | Where-Object State -ne 'Disabled' | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue; if ($i.NextRunTime -and $i.NextRunTime -lt (Get-Date).AddDays(1)) { [pscustomobject]@{ Next=$i.NextRunTime; Task=$_.TaskPath + $_.TaskName } } } | Sort-Object Next | Format-Table -AutoSize | Out-String -Width 220""",
       timeout=300,
       tags=('diagnostic', 'inventory', 'baseline')),
    ST("logon_scripts", "Logon scripts",
       "Scripts that run at sign-in: UserInitMprLogonScript, GPO logon scripts and the "
       "account's own logon script.",
       REG + r"""'UserInitMprLogonScript: ' + (RegVal 'HKCU:\Environment' UserInitMprLogonScript)
Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Group Policy\Scripts' -Recurse -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-ItemProperty $_.PSPath; if ($p.Script) { "GPO script: $($p.Script) $($p.Parameters)" } }""",
       tags=('diagnostic', 'inventory', 'baseline')),
    ST("startup_folders_all", "Startup folders for all users",
       "Shortcuts and files in every user's Startup folder and the common one.",
       r"""Get-ChildItem "$env:SystemDrive\Users\*\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\*" -Force -ErrorAction SilentlyContinue | ForEach-Object { "[INFO] $($_.FullName)" }
Get-ChildItem "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp\*" -Force -ErrorAction SilentlyContinue | ForEach-Object { "[INFO] $($_.FullName)" }""",
       note="other users' folders are only readable elevated",
       tags=('diagnostic', 'inventory', 'baseline')),
    ST("office_addins", "Office add-ins",
       "COM add-ins registered for Outlook, Word, Excel and PowerPoint with their load "
       "behaviour -- slow Outlook starts are usually one of these.",
       r"""foreach ($app in 'Outlook','Word','Excel','PowerPoint') { foreach ($root in 'HKCU:\Software\Microsoft\Office','HKLM:\SOFTWARE\Microsoft\Office','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Office') {
  Get-ChildItem "$root\$app\Addins" -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-ItemProperty $_.PSPath; '{0,-10} {1,-45} LoadBehavior={2}  {3}' -f $app, $_.PSChildName, $p.LoadBehavior, $p.FriendlyName } } }""",
       tags=('diagnostic', 'inventory', 'baseline')),
    ST("run_keys_all_users", "Run keys of every loaded user",
       "Run entries under every loaded user hive (HKEY_USERS) -- other accounts' autostarts.",
       r"""Get-ChildItem Registry::HKEY_USERS -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^S-1-5-21-[\d-]+$' } | ForEach-Object { $sid = $_.PSChildName
  $p = Get-ItemProperty "Registry::HKEY_USERS\$sid\Software\Microsoft\Windows\CurrentVersion\Run" -ErrorAction SilentlyContinue
  if ($p) { $p.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object { '{0}  {1,-30} {2}' -f $sid, $_.Name, $_.Value } } }""",
       tags=('diagnostic', 'inventory', 'baseline')),
    ST("services_autostart_nonms", "Auto-start services from outside Windows",
       "Automatic services whose binary lives outside the Windows folder.",
       r"""Get-CimInstance Win32_Service -Filter "StartMode='Auto'" | Where-Object { $_.PathName -and $_.PathName -notmatch '\\Windows\\' } | Sort-Object Name | ForEach-Object { '{0,-40} {1,-8} {2}' -f $_.Name, $_.State, $_.PathName }""",
       tags=('diagnostic', 'inventory', 'baseline')),
    # ---- Applications (6) --------------------------------------------------------------------
    AP("apps_per_user", "Programs installed only for you",
       "Programs installed in your profile (HKCU) -- invisible to other users and to "
       "machine-wide inventory tools.",
       T("Get-ItemProperty 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' -ErrorAction SilentlyContinue | Where-Object DisplayName | Sort-Object DisplayName | Select-Object DisplayName, DisplayVersion, Publisher, InstallLocation"),
       tags=('diagnostic', 'inventory', 'baseline')),
    AP("apps_by_publisher", "Installed programs by publisher",
       "How many programs each publisher has installed -- a quick read of what kind of machine "
       "this is.",
       f"Get-ItemProperty {_UNINSTALL} -ErrorAction SilentlyContinue | Where-Object DisplayName | Group-Object Publisher | Sort-Object Count -Descending | Select-Object -First 30 Count, Name | Format-Table -AutoSize | Out-String -Width 220",
       tags=('diagnostic', 'inventory', 'baseline')),
    AP("broken_uninstallers", "Programs whose uninstaller is missing",
       "Installed-program entries whose uninstall command points at a file that no longer "
       "exists -- they cannot be removed from Settings.",
       f"Get-ItemProperty {_UNINSTALL} -ErrorAction SilentlyContinue | Where-Object {{ $_.DisplayName -and $_.UninstallString -and $_.UninstallString -notmatch 'msiexec' }} | ForEach-Object {{\n"
       r"""  $exe = if ($_.UninstallString -match '^"([^"]+)"') { $Matches[1] } else { ($_.UninstallString -split ' (?=[/-])')[0] }
  if (-not (Test-Path $exe)) { "[WARN] $($_.DisplayName): $exe" } }""",
       tags=('diagnostic', 'inventory', 'baseline', 'health')),
    AP("java_installs", "Java installations",
       "Every Java runtime/JDK found (registry and common folders) and JAVA_HOME.",
       f"Get-ItemProperty {_UNINSTALL} -ErrorAction SilentlyContinue | Where-Object {{ $_.DisplayName -match 'Java|JDK|JRE|Temurin|Zulu|Corretto|OpenJDK' }} | ForEach-Object {{ \"$($_.DisplayName) $($_.DisplayVersion)\" }}\n"
       "\"JAVA_HOME: $env:JAVA_HOME\"\nif (Get-Command java -ErrorAction SilentlyContinue) { cmd /c 'java -version 2>&1' } else { 'java is not on PATH' }",
       tags=('diagnostic', 'inventory', 'baseline')),
    AP("dev_tools_versions", "Developer tool versions",
       "Versions of git, python, node, npm, dotnet, code, pwsh and docker found on PATH.",
       r"""foreach ($c in 'git --version','python --version','node --version','npm --version','dotnet --version','code --version','pwsh --version','docker --version','winget --version') { $exe = $c.Split(' ')[0]
  if (Get-Command $exe -ErrorAction SilentlyContinue) { '{0,-8} {1}' -f $exe, ((cmd /c "$c 2>&1") | Select-Object -First 1) } else { '{0,-8} not on PATH' -f $exe } }""",
       timeout=180,
       tags=('diagnostic', 'inventory', 'baseline')),
    AP("exe_in_downloads", "Programs sitting in Downloads",
       "Executables, installers and scripts in your Downloads folder, newest first -- "
       "installers people ran once, and things that should not be there.",
       r"""$d = (New-Object -ComObject Shell.Application).Namespace('shell:Downloads').Self.Path
Get-ChildItem $d -Recurse -File -Include *.exe,*.msi,*.bat,*.cmd,*.ps1,*.vbs,*.js,*.hta,*.scr -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 50 | ForEach-Object { $s = Get-AuthenticodeSignature $_.FullName -ErrorAction SilentlyContinue
  '{0} {1:yyyy-MM-dd}  {2,-12} {3}' -f $(if ($s.Status -eq 'Valid') {'[INFO]'} else {'[WARN]'}), $_.LastWriteTime, $s.Status, $_.Name }""",
       tags=('diagnostic', 'inventory', 'baseline', 'health')),
    # ---- Power (3) ---------------------------------------------------------------------------
    PW("sleep_timeouts", "Sleep and display timeouts",
       "Display off, sleep and hibernate timeouts on AC and battery for the active plan.",
       r"""foreach ($s in @(@('SUB_VIDEO','VIDEOIDLE','Display off'), @('SUB_SLEEP','STANDBYIDLE','Sleep after'), @('SUB_SLEEP','HIBERNATEIDLE','Hibernate after'))) {
  $q = powercfg /query SCHEME_CURRENT $s[0] $s[1]; $ac = ($q | Select-String 'Current AC Power Setting Index: (0x\w+)').Matches.Groups[1].Value; $dc = ($q | Select-String 'Current DC Power Setting Index: (0x\w+)').Matches.Groups[1].Value
  '{0,-16} AC {1,6} min   battery {2,6} min' -f $s[2], ([Convert]::ToInt32($ac,16)/60), ([Convert]::ToInt32($dc,16)/60) }""",
       tags=('diagnostic', 'performance')),
    PW("modern_standby", "Modern Standby",
       "Whether this PC uses Modern Standby (S0 low power idle) or classic S3 sleep -- they "
       "behave very differently with network and battery drain.",
       r"""$a = powercfg /a; if ($a -match 'Standby \(S0 Low Power Idle\) Network (Connected|Disconnected)') { "[INFO] Modern Standby: $($Matches[0])" } else { '[INFO] Classic sleep (no Modern Standby)' }
$a | Select-Object -First 8""",
       tags=('diagnostic', 'performance')),
    PW("battery_saver", "Battery saver and power throttling",
       "Battery saver threshold and power throttling policy (background apps slowed on "
       "battery).",
       REG + r"""RegShow 'HKLM:\SYSTEM\CurrentControlSet\Control\Power\PowerThrottling'
powercfg /query SCHEME_CURRENT SUB_ENERGYSAVER ESBATTTHRESHOLD 2>&1 | Select-String 'Current (AC|DC)' | ForEach-Object { 'Battery saver threshold ' + $_.Line.Trim() }""",
       tags=('diagnostic', 'performance')),
    # ---- Printing (2) ------------------------------------------------------------------------
    PR("default_printer", "Default printer per user",
       "This user's default printer and whether Windows manages it ('let Windows manage my "
       "default printer' changes it to the last one used).",
       REG + r"""'Device: ' + (RegVal 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Windows' Device)
'LegacyDefaultPrinterMode (1 = Windows does NOT manage it): ' + (RegVal 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Windows' LegacyDefaultPrinterMode)""",
       tags=('diagnostic', 'hardware')),
    PR("print_policies", "Printing policies",
       "Package Point-and-Print, approved servers and the RPC over named pipes / TCP settings "
       "of the print spooler.",
       REG + r"""RegShow 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Printers'
''; RegShow 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Printers\RPC'""",
       tags=('diagnostic', 'hardware')),
    # ---- Group Policy (3) --------------------------------------------------------------------
    GP("device_install_policy", "Device installation restrictions",
       "Policies that block installing devices (USB storage, by class or id) and removable "
       "storage access.",
       REG + r"""RegShow 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions'
''; RegShow 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices'""",
       tags=('diagnostic', 'security', 'baseline')),
    GP("user_rights", "User rights held by this session",
       "Privileges in the current token and whether each is enabled (whoami /priv).",
       "whoami /priv", kind=CMD,
       tags=('diagnostic', 'security', 'baseline')),
    GP("applocker", "AppLocker and software restriction policies",
       "AppLocker rule collections in force and legacy Software Restriction Policies.",
       r"""try { $p = Get-AppLockerPolicy -Effective -ErrorAction Stop; $p.RuleCollections | ForEach-Object { '{0,-10} enforcement {1}, {2} rule(s)' -f $_.RuleCollectionType, $_.EnforcementMode, $_.Count } } catch { "AppLocker: $($_.Exception.Message)" }
if (Test-Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Safer\CodeIdentifiers') { '[INFO] Software Restriction Policies are configured.' } else { '[INFO] No Software Restriction Policies.' }""",
       tags=('diagnostic', 'security', 'baseline')),
    # ---- Remote access (3) -------------------------------------------------------------------
    RA("rdp_certificate", "Remote Desktop certificate",
       "The certificate RDP presents, its subject and expiry -- self-signed certificates "
       "trigger the 'identity cannot be verified' warning.",
       r"""$t = (Get-CimInstance -Namespace root\cimv2\TerminalServices -ClassName Win32_TSGeneralSetting -Filter "TerminalName='RDP-Tcp'" -ErrorAction SilentlyContinue).SSLCertificateSHA1Hash
if (-not $t) { 'NOT AVAILABLE: the RDP certificate could not be read (needs elevation, or RDP never started).'; return }
$c = Get-ChildItem "Cert:\LocalMachine\Remote Desktop\$t" -ErrorAction SilentlyContinue
"Thumbprint $t  Subject $($c.Subject)  Expires $($c.NotAfter)  Issuer $($c.Issuer)" """,
       admin=True,
       tags=('diagnostic', 'network', 'security')),
    RA("rdp_shadow", "RDP shadowing and session limits",
       "Whether admins may shadow (watch or control) sessions, with or without consent, and the "
       "idle/disconnected session limits.",
       REG + r"""RegShow 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'""",
       tags=('diagnostic', 'network', 'security')),
    RA("remote_mgmt_rules", "Remote management firewall rules",
       "Firewall rule groups that open remote management: WinRM, WMI, Remote Event Log, Remote "
       "Service and Remote Scheduled Tasks management.",
       T("Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.DisplayGroup -match 'Windows Remote Management|Windows Management Instrumentation|Remote Event Log|Remote Service Management|Remote Scheduled Tasks|Remote Volume' } | Select-Object DisplayGroup, DisplayName, Enabled, Profile"),
       tags=('diagnostic', 'network', 'security')),
    # ---- Inventory (4) -----------------------------------------------------------------------
    IN("one_page_inventory", "One-page inventory",
       "Computer, user, OS and build, model, serial, CPU, RAM, disks, IPs, last boot and "
       "newest update on one screen -- the asset-sheet line.",
       L(r"""$os = Get-CimInstance Win32_OperatingSystem; $cs = Get-CimInstance Win32_ComputerSystem; $cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
[pscustomobject]@{ Computer=$env:COMPUTERNAME; User="$env:USERDOMAIN\$env:USERNAME"; OS="$($cv.ProductName) $($cv.DisplayVersion) $($cv.CurrentBuild).$($cv.UBR)"
  Model="$($cs.Manufacturer) $($cs.Model)"; Serial=(Get-CimInstance Win32_BIOS).SerialNumber; CPU=(Get-CimInstance Win32_Processor | Select-Object -First 1).Name.Trim()
  RAM='{0:N0} GB' -f ($cs.TotalPhysicalMemory/1GB); Disks=((Get-PhysicalDisk | ForEach-Object { '{0} {1:N0} GB' -f $_.FriendlyName, ($_.Size/1GB) }) -join '; ')
  IPs=((Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }).IPAddress -join ', ')
  LastBoot=$os.LastBootUpTime; NewestUpdate=((Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1).InstalledOn) }"""),
       tags=('diagnostic', 'inventory', 'baseline')),
    IN("software_count", "Software inventory counts",
       "Counts of desktop programs, Store apps, services, drivers and scheduled tasks.",
       f"$d = @(Get-ItemProperty {_UNINSTALL} -ErrorAction SilentlyContinue | Where-Object DisplayName).Count\n"
       r"""'Desktop programs : {0}' -f $d
'Store apps       : {0}' -f @(Get-AppxPackage | Where-Object { -not $_.IsFramework }).Count
'Services         : {0}' -f @(Get-Service).Count
'Signed drivers   : {0}' -f @(Get-CimInstance Win32_PnPSignedDriver).Count
'Scheduled tasks  : {0}' -f @(Get-ScheduledTask).Count""",
       tags=('diagnostic', 'inventory', 'baseline')),
    IN("disk_inventory", "Disk inventory",
       "Every physical disk with model, serial, firmware, bus, size and media type.",
       T("Get-PhysicalDisk | Select-Object FriendlyName, SerialNumber, FirmwareVersion, BusType, MediaType, @{n='GB';e={[math]::Round($_.Size/1GB)}}"),
       tags=('diagnostic', 'inventory', 'baseline')),
    IN("license_office", "Office licences",
       "Office licences installed on this PC (product, status, last 5 of the key), via the "
       "Software Licensing service.",
       T("Get-CimInstance SoftwareLicensingProduct -Filter \"PartialProductKey IS NOT NULL\" | Where-Object { $_.Name -match 'Office' } | Select-Object Name, @{n='Status';e={ if ($_.LicenseStatus -eq 1) {'Licensed'} else { $_.LicenseStatus } }}, PartialProductKey"),
       tags=('diagnostic', 'inventory', 'baseline')),
    # ---- Troubleshooting (5) -----------------------------------------------------------------
    TS("wer_reports", "Recent Windows Error Reporting reports",
       "The newest crash and hang reports Windows queued or archived, by program.",
       r"""foreach ($r in "$env:ProgramData\Microsoft\Windows\WER\ReportArchive", "$env:ProgramData\Microsoft\Windows\WER\ReportQueue", "$env:LOCALAPPDATA\Microsoft\Windows\WER\ReportArchive") {
  Get-ChildItem $r -Directory -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 15 | ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1}' -f $_.LastWriteTime, $_.Name } }""",
       tags=('diagnostic', 'triage')),
    TS("upgrade_errors", "Feature update failures (Panther logs)",
       "Errors from the last Windows upgrade attempt's setuperr.log -- why a feature update "
       "rolled back.",
       r"""$logs = Get-ChildItem "$env:SystemDrive\`$WINDOWS.~BT\Sources\Panther\setuperr.log","$env:SystemRoot\Panther\setuperr.log","$env:SystemRoot\Panther\NewOs\Panther\setuperr.log" -ErrorAction SilentlyContinue
if (-not $logs) { '[OK]   No setuperr.log -- no failed upgrade recorded.'; return }
foreach ($l in $logs) { "== $($l.FullName) ($($l.LastWriteTime))"; Get-Content $l.FullName -Tail 25 | ForEach-Object { "[WARN] $_" } }""",
       note="the Panther logs may need elevation",
       tags=('diagnostic', 'triage', 'health')),
    TS("explorer_restarts", "Explorer restarts and hangs",
       "Times Explorer restarted (Winlogon 6000/6001) or stopped responding.",
       EV + "EvList @{LogName='Application'; ProviderName='Application Hang'} 14 40",
       tags=('diagnostic', 'triage', 'health')),
    TS("audio_troubleshoot", "Audio service and devices",
       "Audio services' state, the sound devices and audio endpoint errors -- 'no sound'.",
       CHK + r"""foreach ($n in 'Audiosrv','AudioEndpointBuilder') { $s = Get-Service $n; Chk ($s.Status -eq 'Running') $n "$($s.Status), $($s.StartType)" }
Get-PnpDevice -Class AudioEndpoint -PresentOnly -ErrorAction SilentlyContinue | Select-Object FriendlyName, Status | Format-Table -AutoSize | Out-String -Width 220""",
       tags=('diagnostic', 'triage', 'health')),
    TS("camera_mic_privacy", "Camera and microphone privacy",
       "Whether apps may use the camera and microphone (system and per-user consent store), "
       "and which apps last used them.",
       r"""foreach ($c in 'webcam','microphone') { foreach ($root in 'HKLM:','HKCU:') { $v = (Get-ItemProperty "$root\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\$c" -ErrorAction SilentlyContinue).Value; '{0,-10} {1,-5} {2}' -f $c, $root, $v } }
''; 'Recently used by (non-packaged apps):'
foreach ($c in 'webcam','microphone') { Get-ChildItem "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\$c\NonPackaged" -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-ItemProperty $_.PSPath; if ($p.LastUsedTimeStop) { '{0,-10} {1:yyyy-MM-dd HH:mm}  {2}' -f $c, [DateTime]::FromFileTime($p.LastUsedTimeStop), ($_.PSChildName -replace '#','\') } } }""",
       tags=('diagnostic', 'triage')),
]

SCRIPTS += _CLIENT_EVIDENCE_SCRIPTS

_TS_EVIDENCE = CHK + r"""function TsEvents($filter, $max=100) {
  try {
    $metadata=Get-WinEvent -ListLog $filter.LogName -ErrorAction Stop
    if (-not $metadata.IsEnabled) { Chk $null $filter.LogName 'channel disabled; history incomplete' | Out-Host }
    $events=@(Get-WinEvent -FilterHashtable $filter -MaxEvents $max -ErrorAction Stop)
    if (-not $events.Count) { Chk $null $filter.LogName 'no retained matching events' | Out-Host }
    if ($events.Count -eq $max) { Chk $null $filter.LogName "newest $max events only" | Out-Host }
    $events
  } catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { Chk $null $filter.LogName 'no retained matching events' | Out-Host }
    else { Chk $null $filter.LogName "read unavailable/refused: $($_.Exception.Message)" | Out-Host }
  }
}
function TsData($event) { $d=@{}; ([xml]$event.ToXml()).Event.EventData.Data | ForEach-Object { if ($_.Name) { $d[$_.Name]=$_.'#text' } }; return $d }
"""

SCRIPTS += [
    TS("ts-bugcheck-dump-headers", "Last five blue-screen dump headers and bugcheck events",
       "Decodes PAGE/DUMP and PAGE/DU64 headers, correlating dump paths with WER bugcheck events; flags attribution limits.",
       need_admin('protected minidump headers require elevation') + _TS_EVIDENCE + r"""$events=@(TsEvents @{LogName='System';ProviderName='Microsoft-Windows-WER-SystemErrorReporting';Id=1001} 50)
try {
  $config=Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' -ErrorAction Stop
  $folder=if ($config.MinidumpDir) { [Environment]::ExpandEnvironmentVariables($config.MinidumpDir) } else { "$env:SystemRoot\Minidump" }
  $dumps=@(Get-ChildItem -LiteralPath $folder -Filter '*.dmp' -File -ErrorAction Stop | Sort-Object LastWriteTime -Descending | Select-Object -First 5)
  if (-not $dumps.Count) { Chk $null 'Dump headers' 'no retained minidumps in configured folder' }
  $names=@{ '0000000A'='IRQL_NOT_LESS_OR_EQUAL'; '0000001A'='MEMORY_MANAGEMENT'; '0000003B'='SYSTEM_SERVICE_EXCEPTION'; '0000007E'='SYSTEM_THREAD_EXCEPTION_NOT_HANDLED'; '0000009F'='DRIVER_POWER_STATE_FAILURE'; '00000133'='DPC_WATCHDOG_VIOLATION'; '00000139'='KERNEL_SECURITY_CHECK_FAILURE'; '000000EF'='CRITICAL_PROCESS_DIED' }
  foreach ($file in $dumps) {
    $stream=$null
    try {
      $stream=[IO.File]::Open($file.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
      $header=New-Object byte[] 4096; $count=$stream.Read($header,0,$header.Length)
      if ($count -lt 96) { throw 'truncated header (less than 96 bytes)' }
      $magic=[Text.Encoding]::ASCII.GetString($header,0,8)
      $parameters=@()
      switch ($magic) {
        'PAGEDU64' { $code=[BitConverter]::ToUInt32($header,0x38); for ($j=0;$j -lt 4;$j++) { $parameters += '0x{0:X16}' -f [BitConverter]::ToUInt64($header,(0x40+8*$j)) } }
        'PAGEDUMP' { $code=[BitConverter]::ToUInt32($header,0x28); for ($j=0;$j -lt 4;$j++) { $parameters += '0x{0:X8}' -f [BitConverter]::ToUInt32($header,(0x2c+4*$j)) } }
        default { throw "unsupported header '$magic'; MDMP user-mode/other dump formats cannot be decoded as kernel bugchecks" }
      }
      $hex='{0:X8}' -f $code
      [pscustomobject]@{Dump=$file.FullName; Modified=$file.LastWriteTime; Header=$magic; Bugcheck="0x$hex"; Name=$(if ($names.ContainsKey($hex)) { $names[$hex] } else { 'Unmapped code; consult debugger documentation' }); Parameters=($parameters -join ', '); FaultingModule='NOT AVAILABLE from header: requires symbol-aware stack analysis'} | Format-List | Out-String -Width 220
      Chk ($code -eq 0) 'Recorded bugcheck' "0x$hex; a nonzero code records a crash, not a diagnosis of its cause"
      $matched=@($events | Where-Object { $d=TsData $_; @($d.Values | Where-Object { $_ -and ([string]$_).IndexOf($file.Name,[StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0 })
      if ($matched.Count) { $matched | Select-Object TimeCreated,Message | Format-List | Out-String -Width 220 }
      else { Chk $null 'Event correlation' 'no retained WER record names this dump; modification time alone is not crash time' }
    } catch { Chk $null $file.Name $_.Exception.Message }
    finally { if ($stream) { $stream.Dispose() } }
  }
} catch { Chk $null 'Dump directory/configuration' $_.Exception.Message }
'[INFO] A header has no faulting-module field. Open the selected dump in WinDbg with symbols for stack-based attribution; even a named module can be a victim rather than the cause.'""",
       admin=True, tags=('diagnostic', 'hardware', 'triage', 'health')),
    TS("ts-application-hang-candidates", "Idle processes with hang/deadlock evidence",
       "Joins CPU-time deltas, handle/thread counts and Application Hang events; distinguishes idle candidates from deadlocks.",
       _TS_EVIDENCE + r"""$hangs=@(TsEvents @{LogName='Application';ProviderName='Application Hang';Id=1002;StartTime=(Get-Date).AddDays(-7)} 50)
try {
  $first=@{}; Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object { if ($_.CreationDate) { $first["$($_.ProcessId)|$($_.CreationDate.ToString('o'))"]=$_ } }
  if (-not $first.Count) { throw 'first process snapshot empty' }
  Start-Sleep -Seconds 10
  $last=@(Get-CimInstance Win32_Process -ErrorAction Stop)
  if (-not $last.Count) { throw 'second process snapshot empty' }
  $candidates=@(foreach ($p in $last) {
    if (-not $p.CreationDate) { continue }; $old=$first["$($p.ProcessId)|$($p.CreationDate.ToString('o'))"]
    if (-not $old) { continue }
    if ($null -eq $p.KernelModeTime -or $null -eq $p.UserModeTime -or $null -eq $old.KernelModeTime -or $null -eq $old.UserModeTime -or $null -eq $p.HandleCount -or $null -eq $p.ThreadCount) { Chk $null $p.Name 'CPU/handle/thread counters incomplete' | Out-Host; continue }
    $delta=[decimal]$p.KernelModeTime+[decimal]$p.UserModeTime-[decimal]$old.KernelModeTime-[decimal]$old.UserModeTime
    if ($delta -eq 0 -and ($p.HandleCount -ge 2000 -or $p.ThreadCount -ge 100)) {
      $matching=@($hangs | Where-Object { $d=TsData $_; $d.AppName -eq $p.Name })
      [pscustomobject]@{Process=$p.Name; PID=$p.ProcessId; Created=$p.CreationDate; Handles=$p.HandleCount; Threads=$p.ThreadCount; CPUTimeDeltaMs=$delta/10000; RecentAppHangRecords=$matching.Count}
    }
  })
  $candidates | Sort-Object Handles -Descending | Format-Table -AutoSize | Out-String -Width 220
  Chk $null 'Potential idle/hang candidates' "$($candidates.Count) processes: zero measured CPU with >=2000 handles or >=100 threads. Normal event-driven apps also match; confirm unresponsiveness and inspect wait chains/dumps before diagnosing deadlock. App-name history does not prove this PID hung."
} catch { Chk $null 'Hang sampling' $_.Exception.Message }
$hangs | Select-Object TimeCreated,Message | Format-List | Out-String -Width 220""",
       tags=('diagnostic', 'performance', 'triage')),
    TS("ts-wer-crash-signatures", "WER crash signatures and application fault offsets",
       "Correlates Application Error/Windows Error Reporting records by report ID with module, exception and offset.",
       _TS_EVIDENCE + r"""$events=@(TsEvents @{LogName='Application';ProviderName='Application Error','Windows Error Reporting';Id=1000,1001;StartTime=(Get-Date).AddDays(-7)} 500)
$faults=@($events | Where-Object { $_.ProviderName -eq 'Application Error' -and $_.Id -eq 1000 })
$reports=@($events | Where-Object ProviderName -eq 'Windows Error Reporting')
foreach ($e in $faults) {
  $d=TsData $e; $reportId=if ($d.IntegratorReportId) { $d.IntegratorReportId } else { $d.ReportId }
  $related=@($reports | Where-Object { $v=TsData $_; $reportId -and $v.ReportId -eq $reportId })
  [pscustomobject]@{Time=$e.TimeCreated; Application=$d.AppName; Version=$d.AppVersion; Module=$d.ModuleName; ModuleVersion=$d.ModuleVersion; Exception=$d.ExceptionCode; FaultOffset=$d.FaultingOffset; PID=$d.ProcessId; AppPath=$d.AppPath; ModulePath=$d.ModulePath; ReportId=$reportId; WERMatches=$related.Count} | Format-List | Out-String -Width 220
  if (-not $d.ModuleName -or -not $d.ExceptionCode -or -not $d.FaultingOffset) { Chk $null 'Crash fields' 'one or more fields absent in this event schema; full event follows'; $e.Message }
  if (-not $related.Count) { Chk $null 'WER correlation' 'no retained matching report ID; records can be pruned or delayed' }
  foreach ($r in $related) { $v=TsData $r; "[INFO] WER type=$($v.EventName); bucket=$($v.HashedBucket); report=$($v.ReportId)" }
}
if ($faults.Count) { Chk $false 'Recent application faults' "$($faults.Count) retained Application Error records; repeat signatures suggest a repro target, not necessarily the faulty component" }
else { Chk $null 'Recent application faults' 'no Application Error records returned; WER records may still exist' }
'== WER signatures (including unmatched reports)'
$reports | ForEach-Object { $d=TsData $_; [pscustomobject]@{Time=$_.TimeCreated; Type=$d.EventName; P1=$d.P1; P4=$d.P4; P7=$d.P7; P8=$d.P8; Report=$d.ReportId} } | Format-Table -Wrap | Out-String -Width 220
'[INFO] P1-P10 meanings depend on EventName; only named Application Error fields above are labeled module/exception/offset.'""",
       tags=('diagnostic', 'triage', 'health')),
    TS("ts-service-crashes-recovery", "Service crash history and recovery configuration",
       "Joins seven-day SCM 7031/7034 history to current service identities, state and sc qfailure settings.",
       _TS_EVIDENCE + r"""$events=@(TsEvents @{LogName='System';ProviderName='Service Control Manager';Id=7031,7034;StartTime=(Get-Date).AddDays(-7)} 1000)
try {
  $services=@(Get-CimInstance Win32_Service -ErrorAction Stop)
  if (-not $services.Count) { throw 'service inventory empty' }
} catch { Chk $null 'Current service inventory' $_.Exception.Message; $services=@() }
$rows=@(foreach ($e in $events) { $d=TsData $e; $name=$d.param1; if (-not $name) { $name='(name unavailable)'; $e.Message | Out-Host }; [pscustomobject]@{DisplayName=$name; Time=$e.TimeCreated; ID=$e.Id; Message=$e.Message} })
foreach ($group in $rows | Group-Object DisplayName | Sort-Object Count -Descending) {
  $last=$group.Group | Sort-Object Time -Descending | Select-Object -First 1
  Chk $false $group.Name "$($group.Count) crash records; last=$($last.Time); ID=$($last.ID)"
  $match=@($services | Where-Object { $_.DisplayName -eq $group.Name -or $_.Name -eq $group.Name })
  if ($match.Count -ne 1) { Chk $null 'Service identity' 'not uniquely mapped to a current service; may have been removed/renamed'; $last.Message; continue }
  $s=$match[0]; "[INFO] name=$($s.Name); state=$($s.State); start=$($s.StartMode); exit=$($s.ExitCode); PID=$($s.ProcessId)"
  foreach ($query in 'qfailure','qfailureflag') {
    try {
      $out=@(& "$env:SystemRoot\System32\sc.exe" $query $s.Name 2>&1); $exit=$LASTEXITCODE
      $out
      if ($exit -ne 0 -or -not $out.Count) { Chk $null 'Recovery configuration' "$query exit=$exit; lines=$($out.Count)" }
      else { Chk $null 'Recovery configuration' 'native settings above are current, not necessarily those in effect at crash time; inspect actions and delay' }
    } catch { Chk $null 'Recovery configuration' $_.Exception.Message }
  }
}
if (-not $rows.Count) { Chk $null 'Service crashes' 'no retained matching records; no recovery verdict inferred' }""",
       tags=('diagnostic', 'triage', 'health')),
    TS("ts-driver-verifier-evidence", "Driver Verifier live and next-boot evidence",
       "Correlates verifier query output, configured driver names, signed-driver metadata and recent bugchecks.",
       need_admin('complete live verifier and protected driver configuration reads require elevation') + _TS_EVIDENCE + r"""foreach ($arg in '/query','/querysettings') {
  try {
    "== verifier $arg"
    $out=@(& "$env:SystemRoot\System32\verifier.exe" $arg 2>&1); $exit=$LASTEXITCODE
    $out
    if ($exit -ne 0 -or -not $out.Count) { Chk $null 'Verifier query' "exit=$exit; lines=$($out.Count)" }
  } catch { Chk $null 'Verifier query' $_.Exception.Message }
}
try {
  $key=Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management' -ErrorAction Stop
  $configured=[string]$key.VerifyDrivers
  Chk $null 'Next-boot verifier settings' "VerifyDriverLevel=$($key.VerifyDriverLevel); VerifyDrivers=$configured (absent values mean not configured here; live status is above)"
  if ($configured) {
    $names=@($configured -split '[\s,;]+' | Where-Object { $_ })
    $drivers=@(Get-CimInstance Win32_SystemDriver -ErrorAction Stop)
    if (-not $drivers.Count) { throw 'system driver inventory empty' }
    foreach ($name in $names) {
      $match=@($drivers | Where-Object { $_.PathName -and [IO.Path]::GetFileName($_.PathName.Trim('"')) -eq $name })
      if (-not $match.Count) { Chk $null $name 'configured verifier name not mapped to current driver path' }
      $match | Select-Object Name,State,StartMode,PathName | Format-List | Out-String -Width 220
    }
  }
  $signed=@(Get-CimInstance Win32_PnPSignedDriver -ErrorAction Stop)
  if (-not $signed.Count) { Chk $null 'Signed driver metadata' 'no rows returned' }
  $signed | Sort-Object DriverDate -Descending | Select-Object -First 15 DeviceName,DriverVersion,DriverDate,InfName,DriverProviderName | Format-Table -Wrap | Out-String -Width 220
} catch { Chk $null 'Verifier/driver correlation' $_.Exception.Message }
TsEvents @{LogName='System';ProviderName='Microsoft-Windows-WER-SystemErrorReporting';Id=1001;StartTime=(Get-Date).AddDays(-7)} 10 | Select-Object TimeCreated,Message | Format-List | Out-String -Width 220
Chk $null 'Verification history limits' 'Windows does not expose a complete historical list of verified drivers here. Query lists live/current configuration; driver dates are package dates, not verification timestamps. No verifier flags changed.'""",
       admin=True, tags=('diagnostic', 'hardware', 'triage')),
    TS("ts-servicing-integrity-combined", "SFC, CBS and component-store integrity evidence",
       "Combines retained SFC session endings, CBS corruption errors and DISM cached CheckHealth status without running repairs.",
       need_admin('CBS and DISM component health reads require elevation') + CHK + r"""try {
  $path="$env:SystemRoot\Logs\CBS\CBS.log"
  $lines=@(Get-Content -LiteralPath $path -Tail 30000 -ErrorAction Stop)
  if (-not $lines.Count) { throw 'CBS.log empty' }
  $sfc=@($lines | Where-Object { $_ -match '\[SR\]' })
  '== Newest SFC evidence (current CBS tail only)'; $sfc | Select-Object -Last 40
  $ends=@($sfc | Where-Object { $_ -match 'Verify complete|Repair complete|Verify and Repair Transaction completed' })
  if ($ends.Count) { Chk $null 'Last retained SFC completion marker' "$($ends[-1]); a marker alone is not the original sfc.exe summary or proof of a full scan" }
  else { Chk $null 'Last SFC result' 'no completion marker in current bounded CBS tail; session may be absent, rotated, compressed or incomplete' }
  $errors=@($lines | Where-Object { $_ -match 'Cannot repair member file|CSI Payload Corrupt|CSI Manifest Corrupt|\(F\)|Failed.*0x800f|corrupt.*0x' })
  if ($errors.Count) { Chk $false 'CBS corruption/error leads' "$($errors.Count) matching lines in tail; may predate successful repair"; $errors | Select-Object -Last 40 }
  else { Chk $null 'CBS corruption/error leads' 'no matching lines in current tail; rotated CAB logs excluded, not a clean-system verdict' }
} catch { Chk $null 'CBS/SFC evidence unavailable' $_.Exception.Message }
try {
  $health=@(Repair-WindowsImage -Online -CheckHealth -ErrorAction Stop)
  if (-not $health.Count -or $null -eq $health[0].ImageHealthState) { throw 'DISM returned no image health state' }
  $health | Select-Object ImageHealthState,RestartNeeded,Online | Format-List | Out-String -Width 220
  Chk ("$($health[0].ImageHealthState)" -eq 'Healthy') 'DISM cached component status' "$($health[0].ImageHealthState); CheckHealth queries existing corruption markers, does not scan or repair"
} catch { Chk $null 'DISM CheckHealth' $_.Exception.Message }
'[INFO] CheckHealth can write diagnostic logs as an OS query side effect. No SFC scan, DISM ScanHealth/RestoreHealth or component cleanup was requested. Historical errors and cached status are independent evidence.'""",
       admin=True, timeout=120, tags=('diagnostic', 'storage', 'triage', 'health')),
    TS("ts-cumulative-servicing-timeline", "Cumulative servicing results and CBS durations",
       "Correlates recent RollupFix package metadata, CBS package start/completion markers and Windows Update outcomes.",
       need_admin('online servicing package metadata requires elevation') + _TS_EVIDENCE + r"""$updates=@(TsEvents @{LogName='System';ProviderName='Microsoft-Windows-WindowsUpdateClient';Id=19,20;StartTime=(Get-Date).AddDays(-180)} 200)
try {
  $packages=@(Get-WindowsPackage -Online -ErrorAction Stop | Where-Object PackageName -like '*RollupFix*' | Sort-Object InstallTime -Descending | Select-Object -First 10)
  if (-not $packages.Count) { Chk $null 'Cumulative packages' 'no retained RollupFix packages; superseded packages may have been removed' }
} catch { Chk $null 'Servicing packages' $_.Exception.Message; $packages=@() }
$lines=@()
try {
  $files=@(Get-ChildItem -LiteralPath "$env:SystemRoot\Logs\CBS" -Filter '*.log' -File -ErrorAction Stop | Sort-Object LastWriteTime -Descending | Select-Object -First 3)
  if (-not $files.Count) { Chk $null 'CBS sources' 'no text logs; archived CABs not expanded' }
  foreach ($file in $files) {
    try { $tail=@(Get-Content -LiteralPath $file.FullName -Tail 40000 -ErrorAction Stop); if (-not $tail.Count) { Chk $null $file.Name 'empty log' }; $lines += $tail }
    catch { Chk $null $file.Name $_.Exception.Message }
  }
} catch { Chk $null 'CBS sources' $_.Exception.Message }
foreach ($p in $packages) {
  "== $($p.PackageName); state=$($p.PackageState); installed=$($p.InstallTime)"
  $evidence=@($lines | Where-Object { $_.Contains($p.PackageName) })
  # Use explicit initiation/finalization only, never first/last arbitrary mentions.
  $starts=@($evidence | Where-Object { $_ -match 'Initiating changes for package' } | Sort-Object)
  $ends=@($evidence | Where-Object { $_ -match 'Processing complete.*hr:' } | Sort-Object)
  $start=$null; $end=$null; $duration=$null
  if ($ends.Count) {
    $endLine=$ends[-1]
    if ($endLine -match '^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)') { $end=[datetime]::ParseExact($Matches[1],'yyyy-MM-dd HH:mm:ss',[Globalization.CultureInfo]::InvariantCulture) }
    if ($end) {
      $preceding=@($starts | Where-Object { $_ -match '^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)' -and [datetime]::ParseExact($Matches[1],'yyyy-MM-dd HH:mm:ss',[Globalization.CultureInfo]::InvariantCulture) -le $end })
      if ($preceding.Count -and $preceding[-1] -match '^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)') { $start=[datetime]::ParseExact($Matches[1],'yyyy-MM-dd HH:mm:ss',[Globalization.CultureInfo]::InvariantCulture); $duration=($end-$start).TotalSeconds }
    }
  }
  [pscustomobject]@{Package=$p.PackageName; State=$p.PackageState; CBSStart=$start; CBSEnd=$end; ObservedSeconds=$duration; Markers=$evidence.Count} | Format-List | Out-String -Width 220
  if ($null -eq $duration) { Chk $null 'CBS duration' 'no complete start/end pair in bounded text logs; duration NOT AVAILABLE' }
  else { Chk $null 'CBS duration' 'package processing interval only; retries/session overlap and reboot phases prevent treating this as full update duration' }
  if ($ends.Count) { $ends[-1] }
  $evidence | Select-Object -Last 5
}
'== Windows Update outcomes (independent timeline; package/KB mapping is not assumed)'
$updates | Select-Object TimeCreated,Id,Message | Format-List | Out-String -Width 220
Chk $null 'Coverage' 'up to ten retained RollupFix packages, three newest CBS text logs (40000 lines each), and 200 WU events. Full ten-update history may not survive component cleanup/log rotation.'""",
       admin=True, timeout=120, tags=('diagnostic', 'triage', 'health')),
    TS("ts-pending-operations-audit", "Pending operations across servicing, sessions and drivers",
       "Correlates rename pairs, CBS/WU reboot markers, pending.xml, package states and device problem codes.",
       need_admin('pending packages and machine operation evidence require elevation') + CHK + r"""$root='HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager'
try {
  $key=Get-ItemProperty -LiteralPath $root -ErrorAction Stop
  foreach ($name in 'PendingFileRenameOperations','PendingFileRenameOperations2') {
    $value=$key.PSObject.Properties[$name]
    if (-not $value) { Chk $true $name 'value absent' ; continue }
    $pairs=@($value.Value); Chk ($pairs.Count -eq 0) $name "$($pairs.Count) strings; paired source/destination, empty destination means delete"
    for ($i=0; $i -lt $pairs.Count; $i+=2) {
      $destination=if ($i+1 -ge $pairs.Count) { '(NOT AVAILABLE: incomplete pair)' } elseif (-not $pairs[$i+1]) { '(delete)' } else { $pairs[$i+1] }
      [pscustomobject]@{Source=$pairs[$i]; Destination=$destination} | Format-List | Out-String -Width 220
    }
  }
} catch { Chk $null 'Session Manager' $_.Exception.Message }
foreach ($path in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\PackagesPending','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') {
  try { $exists=Test-Path -LiteralPath $path -ErrorAction Stop; Chk (-not $exists) $path "pending marker present=$exists; marker presence alone does not prove a stuck transaction" }
  catch { Chk $null $path $_.Exception.Message }
}
try {
  $file="$env:SystemRoot\WinSxS\pending.xml"; $present=Test-Path -LiteralPath $file -ErrorAction Stop
  Chk (-not $present) 'Component store pending.xml' "present=$present"
  if ($present) { Get-Item -LiteralPath $file -ErrorAction Stop | Select-Object Length,LastWriteTime | Format-List | Out-String -Width 220 }
} catch { Chk $null 'pending.xml' $_.Exception.Message }
try {
  $all=@(Get-WindowsPackage -Online -ErrorAction Stop)
  if (-not $all.Count) { throw 'package query returned no rows' }
  $pending=@($all | Where-Object PackageState -match 'Pending'); Chk (-not $pending.Count) 'Pending packages' "$($pending.Count) packages"
  $pending | Select-Object PackageName,PackageState,InstallTime | Format-Table -Wrap | Out-String -Width 220
} catch { Chk $null 'Pending packages' $_.Exception.Message }
try {
  $devices=@(Get-CimInstance Win32_PnPEntity -ErrorAction Stop)
  if (-not $devices.Count) { throw 'device inventory empty' }
  $pending=@($devices | Where-Object ConfigManagerErrorCode -in 14,28)
  Chk (-not $pending.Count) 'Driver installation/restart leads' "$($pending.Count) devices: code 14 restart needed, code 28 driver not installed; not a complete installer queue"
  $pending | Select-Object Name,PNPDeviceID,ConfigManagerErrorCode | Format-Table -Wrap | Out-String -Width 220
} catch { Chk $null 'Pending driver evidence' $_.Exception.Message }
'[INFO] Do not delete pending markers or pending.xml. Correlate age and last servicing/driver operation before deciding whether a planned restart is appropriate.'""",
       admin=True, timeout=120, tags=('diagnostic', 'storage', 'triage', 'health')),
    TS("ts-disk-health-correlated", "Disk health: reliability, SMART, dirty bits and CHKDSK",
       "Combines StorageReliabilityCounter, ATA SMART attribute 5, volume dirty state and retained CHKDSK history.",
       need_admin('SMART and volume dirty-state queries require elevation') + _TS_EVIDENCE + r"""try {
  $disks=@(Get-PhysicalDisk -ErrorAction Stop)
  if (-not $disks.Count) { throw 'physical disk inventory empty' }
  foreach ($disk in $disks) {
    "== Disk $($disk.DeviceId): $($disk.FriendlyName); serial=$($disk.SerialNumber); health=$($disk.HealthStatus)"
    try {
      $r=@($disk | Get-StorageReliabilityCounter -ErrorAction Stop)
      if (-not $r.Count) { throw 'reliability provider returned no rows' }
      $r | Select-Object DeviceId,Temperature,Wear,PowerOnHours,ReadErrorsTotal,ReadErrorsUncorrected,WriteErrorsTotal,WriteErrorsUncorrected | Format-List | Out-String -Width 220
      $v=$r[0]
      $ok=if ($null -eq $v.ReadErrorsUncorrected -or $null -eq $v.WriteErrorsUncorrected) { $null } else { $v.ReadErrorsUncorrected -eq 0 -and $v.WriteErrorsUncorrected -eq 0 }
      Chk $ok 'Reported uncorrected errors' 'blank counters mean NOT AVAILABLE; zero is only the reported history, not proof of hardware health'
    } catch { Chk $null 'Reliability counters' $_.Exception.Message }
  }
} catch { Chk $null 'Physical disk inventory' $_.Exception.Message }
try {
  $smart=@(Get-CimInstance -Namespace root\wmi -ClassName MSStorageDriver_FailurePredictData -ErrorAction Stop)
  if (-not $smart.Count) { throw 'no legacy ATA SMART data; common for NVMe/USB/RAID' }
  foreach ($s in $smart) {
    $data=$s.VendorSpecific; $found=$false
    for ($offset=2; $offset+11 -lt $data.Count -and $offset -lt 362; $offset+=12) {
      if ($data[$offset] -eq 5) {
        $found=$true; $raw=[uint64]0
        for ($j=0;$j -lt 6;$j++) { $raw += [uint64]$data[$offset+5+$j] -shl (8*$j) }
        Chk ($raw -eq 0) 'ATA reallocated sectors (attribute 5)' "$($s.InstanceName): raw=$raw; vendor-defined encoding, validate with manufacturer tooling; instance is not assumed to match Storage DeviceId"
      }
    }
    if (-not $found) { Chk $null $s.InstanceName 'SMART attribute 5 absent; reallocated sector count NOT AVAILABLE' }
  }
} catch { Chk $null 'Legacy SMART' $_.Exception.Message }
try {
  $volumes=@(Get-CimInstance Win32_Volume -Filter 'DriveType=3' -ErrorAction Stop)
  if (-not $volumes.Count) { throw 'volume inventory empty' }
  foreach ($v in $volumes) {
    $ok=if ($null -eq $v.DirtyBitSet) { $null } else { -not $v.DirtyBitSet }
    Chk $ok "Dirty bit $($v.DeviceID)" "letter=$($v.DriveLetter); filesystem=$($v.FileSystem); DirtyBitSet=$($v.DirtyBitSet) (blank means unsupported/refused)"
  }
} catch { Chk $null 'Volume dirty bits' $_.Exception.Message }
'== CHKDSK history (read only; no check started)'
TsEvents @{LogName='Application';ProviderName='Microsoft-Windows-Wininit','Chkdsk','Microsoft-Windows-Chkdsk';Id=1001,26226;StartTime=(Get-Date).AddDays(-90)} 15 | Select-Object TimeCreated,ProviderName,Id,Message | Format-List | Out-String -Width 220
Chk $null 'Disk triage' 'use volume identifiers in CHKDSK messages and disk serial/instance IDs to correlate; physical disks and volumes are different layers. Missing SMART/history never means healthy. Escalate uncorrected errors or growing reallocation counts.'""",
       admin=True, tags=('diagnostic', 'storage', 'hardware', 'triage', 'health')),
]

# Reuse the metadata-only scanner text; each generated script is standalone.
_DEEP_BROWSER = r"""
function DcBrowserProfiles {
  foreach ($user in @(DcProfiles)) {
    foreach ($browser in @(@{Name='Chrome';Path='AppData\Local\Google\Chrome\User Data'},@{Name='Edge';Path='AppData\Local\Microsoft\Edge\User Data'},@{Name='Brave';Path='AppData\Local\BraveSoftware\Brave-Browser\User Data'},@{Name='Firefox';Path='AppData\Roaming\Mozilla\Firefox\Profiles'})) {
      $root=Join-Path $user.LocalPath $browser.Path
      foreach ($dir in @(DcChildren $root | Where-Object { $_.PSIsContainer -and ($browser.Name -eq 'Firefox' -or $_.Name -match '^(Default|Profile \d+|Guest Profile|System Profile)$') })) {
        [pscustomobject]@{Browser=$browser.Name;User=$user.SID;Path=$dir.FullName;Profile=$dir.Name;LocalProfile=if ($browser.Name -eq 'Firefox') { $dir.FullName.Replace('\AppData\Roaming\','\AppData\Local\') } else { $dir.FullName }}
      }
    }
  }
}
"""

SCRIPTS += [
    AP('cl2-browser-profile-session-audit', 'Deep cleanup: browser profiles, extensions and session bloat',
       'Per-profile category and extension rankings for four browsers, credential-store presence and bounded Chromium SNSS tab-ID replay.',
       need_admin('all local users browser metadata requires elevation') + _DEEP_CLEANUP + _DEEP_BROWSER + r"""
function DcSession($folder) {
  $files=@(DcChildren (Join-Path $folder 'Sessions') | Where-Object { -not $_.PSIsContainer -and $_.Name -like 'Session_*' } | Sort-Object LastWriteTime -Descending)
  if (-not $files.Count) { Chk $null 'Open-tab estimate' 'no readable modern Session_* files; legacy/current live tabs unavailable'; return }
  $file=$files[0]
  try {
    $safe=DcItem $file.FullName
    if ($safe.Length -gt 32MB -or $safe.Length -lt 8) { throw 'session length outside 8 bytes..32 MiB bounded parser; tab count unavailable' }
    $stream=[IO.File]::Open($safe.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
    $reader=[IO.BinaryReader]::new($stream); $tabs=@{}; $complete=$true; $marker=$false
    try {
      if ($reader.ReadUInt32() -ne 0x53534E53) { throw 'not SNSS' }
      $version=$reader.ReadUInt32()
      if ($version -notin 1,3) { throw "unsupported/encrypted SNSS version $version; no decryption attempted, tab count unavailable" }
      while ($stream.Position -lt $stream.Length) {
        if ($stream.Length-$stream.Position -lt 2) { $complete=$false; break }
        $length=$reader.ReadUInt16()
        if ($length -lt 1 -or $length -gt $stream.Length-$stream.Position) { $complete=$false; break }
        $command=$reader.ReadByte(); $payload=$length-1; $end=$stream.Position+$payload
        # SetTabWindow payload: window ID, tab ID. TabClosed starts with tab ID.
        # Navigation payloads/URLs are skipped, never decoded or displayed.
        if ($command -eq 0 -and $payload -ge 8) { $window=$reader.ReadInt32(); $tab=$reader.ReadInt32(); $tabs[$tab]=$window }
        elseif ($command -in 3,16 -and $payload -ge 4) { $tab=$reader.ReadInt32(); $tabs.Remove($tab) }
        elseif ($command -in 4,17 -and $payload -ge 4) { $window=$reader.ReadInt32(); foreach ($tab in @($tabs.Keys)) { if ($tabs[$tab] -eq $window) { $tabs.Remove($tab) } } }
        elseif ($command -eq 255) { $marker=$true }
        $stream.Position=$end
      }
    } finally { $reader.Dispose(); $stream.Dispose() }
    # Source: chromium/src components/sessions/core/{command_storage_backend,
    # session_service_commands}.cc, refs/heads/main (checked 2026-11-04).
    # Chromium command_storage_backend.cc: v3 requires initial-state marker 255;
    # session_service_commands.cc uses close commands 16/17 (legacy 3/4).
    if ($version -eq 3 -and -not $marker) { $complete=$false }
    $ok=if ($complete) { $tabs.Count -lt 200 } else { $null }
    Chk $ok 'Session restore tab-ID estimate' "$($tabs.Count) retained tab IDs; complete record stream=$complete; file=$($file.Name); 200-tab review threshold. A disk snapshot is not current open-tab or RAM measurement."
  } catch { Chk $null 'Session tab-ID replay' $_.Exception.Message }
}
$profiles=@(DcBrowserProfiles)
if (-not $profiles.Count) { Chk $null 'Browser coverage' 'no standard readable profiles discovered; custom paths/Firefox profiles.ini redirects are outside discovery' }
foreach ($profile in $profiles) {
  "== $($profile.Browser) user=$($profile.User) profile=$($profile.Path)"
  $parts=if ($profile.Browser -eq 'Firefox') {
    @(@{Name='cache2';Root=$profile.LocalProfile},@{Name='startupCache';Root=$profile.LocalProfile},@{Name='places.sqlite';Root=$profile.Path},@{Name='storage\default';Root=$profile.Path},@{Name='extensions';Root=$profile.Path},@{Name='sessionstore-backups';Root=$profile.Path},@{Name='sessionstore.jsonlz4';Root=$profile.Path})
  } else { @('Cache','Code Cache','GPUCache','History','IndexedDB','Service Worker','Local Storage','Extensions','Session Storage','Sessions') | ForEach-Object { @{Name=$_;Root=$profile.Path} } }
  $rows=@(foreach ($part in $parts) { DcMeasure (Join-Path $part.Root $part.Name) })
  DcShow ($rows | Sort-Object Bytes -Descending)
  '== Largest extensions (IDs/filenames only; manifest names not read)'
  $extensions=@(foreach ($extension in @(DcChildren (Join-Path $profile.Path 'Extensions'))) { DcMeasure $extension.FullName })
  DcShow ($extensions | Sort-Object Bytes -Descending | Select-Object -First 15)
  $loginFiles=if ($profile.Browser -eq 'Firefox') { 'logins.json','key4.db' } else { 'Login Data','Login Data For Account' }
  foreach ($name in $loginFiles) {
    try { $i=DcItem (Join-Path $profile.Path $name); "[INFO] Credential-store file PRESENT: $name; $(DcSize $i.Length); contents NEVER read; presence does not prove saved passwords" }
    catch { "NOT AVAILABLE: credential-store presence ${name}: $($_.Exception.Message)" }
  }
  if ($profile.Browser -ne 'Firefox') { DcSession $profile.Path }
  else { Chk $null 'Firefox tab count' 'compressed JSON session data not opened; session artifact sizes above, current tabs/RAM unknown' }
}
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'inventory', 'triage')),
    AP('cl2-appx-state-cache-ratios', 'Deep cleanup: Store app payload and per-user state ratios',
       'Maps installed package families to each user state/cache/temp footprint, flags unmatched families and disproportionate cache growth.',
       need_admin('all-user AppX registrations and state directories require elevation') + _DEEP_CLEANUP + r"""
$packageComplete=$false; $packages=@()
try { $packages=@(Get-AppxPackage -AllUsers -ErrorAction Stop); if (-not $packages.Count) { throw 'AppX inventory empty' }; $packageComplete=$true }
catch { Chk $null 'AppX registration inventory' $_.Exception.Message }
$installSizes=@{}; $families=@{}
foreach ($package in $packages) {
  $families[$package.PackageFamilyName]=$true
  if (-not $installSizes.ContainsKey($package.InstallLocation) -and $package.InstallLocation) {
    $m=DcMeasure $package.InstallLocation; $installSizes[$package.InstallLocation]=$m
    "[INFO] Package=$($package.PackageFullName); install=$($m.Path); $(DcSize $m.Bytes); complete=$($m.Complete); framework=$($package.IsFramework)"
  }
}
$rows=@(foreach ($p in @(DcProfiles)) {
  foreach ($folder in @(DcChildren (Join-Path $p.LocalPath 'AppData\Local\Packages') | Where-Object PSIsContainer)) {
    $parts=@{}; $complete=$true
    foreach ($name in 'LocalState','LocalCache','RoamingState','TempState','AC') {
      $m=DcMeasure (Join-Path $folder.FullName $name); $parts[$name]=$m.Bytes; if (-not $m.Complete) { $complete=$false }
    }
    $cache=$parts.LocalCache+$parts.TempState+$parts.AC; $state=$parts.LocalState+$parts.RoamingState
    $registered=if ($packageComplete) { $families.ContainsKey($folder.Name) } else { $null }
    [pscustomobject]@{SID=$p.SID; Family=$folder.Name; RegisteredAnyUser=$registered; LocalStateBytes=$parts.LocalState; LocalCacheBytes=$parts.LocalCache; RoamingStateBytes=$parts.RoamingState; TempStateBytes=$parts.TempState; ACBytes=$parts.AC; CacheBytes=$cache; Cache=(DcSize $cache); CacheToState=if ($state -gt 0) {[math]::Round($cache/$state,2)} else {$null}; Complete=$complete}
  }
})
DcShow ($rows | Sort-Object CacheBytes -Descending)
foreach ($r in $rows | Where-Object { $_.CacheBytes -gt 1GB -and ($null -eq $_.CacheToState -or $_.CacheToState -gt 5) }) { Chk $false $r.Family "Cache review: $($r.Cache), >1 GiB and >5x durable state (or no state); completeness=$($r.Complete), not proven disposable" }
Chk $null 'AppX interpretation' 'Registration is across all users, not proof this user uses the app. Unmatched family can be retained state, staged or deprovisioned app; not a deletion recommendation. No app databases opened; install and user state are separate.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'inventory')),
    AP('cl2-java-homes-deployment-caches', 'Deep cleanup: Java homes and deployment cache sprawl',
       'Cross-references environment/registry Java homes, vendor install roots and user deployment/WebStart caches, ranking versions and state.',
       need_admin('other users Java caches and machine registrations require elevation') + _DEEP_CLEANUP + r"""
$homes=@{}; if ($env:JAVA_HOME) { $homes[$env:JAVA_HOME]='JAVA_HOME' }; if ($env:JDK_HOME) { $homes[$env:JDK_HOME]='JDK_HOME' }
foreach ($registry in 'HKLM:\SOFTWARE\JavaSoft','HKLM:\SOFTWARE\WOW6432Node\JavaSoft','HKCU:\SOFTWARE\JavaSoft') {
  try {
    foreach ($key in @(Get-ChildItem -LiteralPath $registry -Recurse -ErrorAction Stop)) {
      $v=Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction Stop
      if ($v.JavaHome) { $homes[[string]$v.JavaHome]=$key.Name }
    }
  } catch { Chk $null 'Java registrations' $_.Exception.Message }
}
foreach ($base in @($env:ProgramFiles,${env:ProgramFiles(x86)}) | Where-Object { $_ }) {
  foreach ($vendor in 'Java','Eclipse Adoptium','Microsoft','Amazon Corretto','BellSoft','Zulu') {
    foreach ($dir in @(DcChildren (Join-Path $base $vendor) | Where-Object { $_.PSIsContainer -and ($vendor -ne 'Microsoft' -or $_.Name -match 'jdk|jre') })) { $homes[$dir.FullName]="vendor discovery: $vendor" }
  }
}
$rows=@(foreach ($homePath in $homes.Keys) {
  $m=DcMeasure $homePath; $release=@(DcText (Join-Path $homePath 'release') 100 | Where-Object { $_ -match '^(JAVA_VERSION|IMPLEMENTOR|OS_ARCH)=' })
  [pscustomobject]@{Home=$homePath; Source=$homes[$homePath]; Release=($release -join '; '); Bytes=$m.Bytes; Size=$m.Size; Complete=$m.Complete}
})
DcShow ($rows | Sort-Object Bytes -Descending)
foreach ($p in @(DcProfiles)) {
  foreach ($relative in @('AppData\LocalLow\Sun\Java\Deployment','AppData\Local\Sun\Java\Deployment','AppData\Roaming\Sun\Java\Deployment','AppData\Local\Temp\Java','AppData\Local\Temp\javasharedresources','.cache\icedtea-web','.cache\openwebstart','.config\icedtea-web')) {
    DcShow (DcMeasure (Join-Path $p.LocalPath $relative))
  }
  $temp=Join-Path $p.LocalPath 'AppData\Local\Temp'
  foreach ($dir in @(DcChildren $temp | Where-Object { $_.PSIsContainer -and $_.Name -match '^(hsperfdata_|java|jre|jdk)' })) { DcShow (DcMeasure $dir.FullName) }
  $jars=[decimal]0; $count=0
  DcFiles $temp | Where-Object Extension -eq '.jar' | ForEach-Object { $jars+=$_.Length; $count++ }
  "[INFO] SID=$($p.SID); temp JAR files=$count; bytes=$(DcSize $jars); partial if coverage warnings present"
}
Chk $null 'Java version sprawl' 'Multiple JDKs may be pinned by applications. WebStart caches can contain offline application payload/state. Vendor discovery is bounded to standard roots; embedded runtimes/custom deployment paths not guaranteed. Java executables are not launched.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'inventory')),
    AP('cl2-python-environment-sprawl', 'Deep cleanup: Python environments, conda and bytecode sprawl',
       'Discovers pyvenv.cfg/conda-meta markers across fixed drives, attributes named environment trees and aggregates pip/bytecode footprints.',
       need_admin('whole-machine environment discovery needs protected-folder access') + _DEEP_CLEANUP + r"""
$environments=@{}; $bytecode=@{}; $markers=@{}; $directoryTotals=@{}; $pycBytes=[decimal]0; $pycCount=0
# Report known caches first, so a whole-drive discovery budget cannot starve them.
foreach ($p in @(DcProfiles)) {
  foreach ($relative in 'AppData\Local\pip\Cache','AppData\Local\pypa\Cache','AppData\Local\conda\conda\pkgs','.conda\pkgs','.cache\pip') { DcShow (DcMeasure (Join-Path $p.LocalPath $relative)) }
  '== Conda registered environment paths (plain configuration only)'; DcText (Join-Path $p.LocalPath '.conda\environments.txt') 1000
}
try { $volumes=@(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction Stop) }
catch { Chk $null 'Fixed drives' $_.Exception.Message; return }
foreach ($v in $volumes) {
  DcFiles "$($v.DeviceID)\" | ForEach-Object {
    $f=$_; $envRoot=$null
    if (-not $directoryTotals.ContainsKey($f.DirectoryName)) { $directoryTotals[$f.DirectoryName]=@{Bytes=[decimal]0;Files=0} }
    $directoryTotals[$f.DirectoryName].Bytes+=$f.Length; $directoryTotals[$f.DirectoryName].Files++
    if ($f.Name -eq 'pyvenv.cfg') { $markers[$f.DirectoryName]='pyvenv.cfg' }
    if ($f.Directory.Name -eq 'conda-meta' -and $f.Name -eq 'history') { $markers[$f.Directory.Parent.FullName]='conda-meta/history' }
    if ($f.FullName -match '^(.*?\\(?:\.venv|venv|env|\.env))(?:\\|$)') { $envRoot=$Matches[1] }
    elseif ($f.FullName -match '^(.*?\\(?:anaconda\d*|miniconda\d*|miniforge\d*|mambaforge)\\envs\\[^\\]+)\\') { $envRoot=$Matches[1] }
    if ($envRoot) {
      if (-not $environments.ContainsKey($envRoot)) { $environments[$envRoot]=[pscustomobject]@{Path=$envRoot; Bytes=[decimal]0; Files=0; Evidence='name heuristic; marker checked separately'} }
      $environments[$envRoot].Bytes+=$f.Length; $environments[$envRoot].Files++
    }
    if ($f.Extension -eq '.pyc') {
      $pycBytes+=$f.Length; $pycCount++; $folder=$f.DirectoryName
      if (-not $bytecode.ContainsKey($folder)) { $bytecode[$folder]=[decimal]0 }; $bytecode[$folder]+=$f.Length
    }
  }
}
foreach ($path in $markers.Keys) {
  $total=[decimal]0; $count=0
  foreach ($folder in $directoryTotals.Keys) {
    if ($folder -eq $path -or $folder.StartsWith($path.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { $total+=$directoryTotals[$folder].Bytes; $count+=$directoryTotals[$folder].Files }
  }
  $environments[$path]=[pscustomobject]@{Path=$path; Bytes=$total; Files=$count; Evidence="$($markers[$path]); metadata attributed from the same discovery pass"}
}
DcShow ($environments.Values | Sort-Object Bytes -Descending | Select-Object *,@{n='Size';e={DcSize $_.Bytes}})
"[INFO] .pyc files=$pycCount; total=$(DcSize $pycBytes) (overlaps environment totals; do not add twice)"
DcShow ($bytecode.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 30 @{n='BytecodeFolder';e={$_.Key}},@{n='Bytes';e={$_.Value}},@{n='Size';e={DcSize $_.Value}})
Chk $null 'Python cleanup scope' 'No Python/pip/conda executables launched. Name-only candidates may not be environments; markers provide confirmation. Environment rows overlap if nested; all totals are lower bounds when discovery is partial. Conda package hard links overstate physical bytes. Custom pip cache paths and unscanned directories remain unknown.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'inventory', 'triage')),
    AP('cl2-electron-webview-cache-audit', 'Deep cleanup: Electron, CEF and WebView2 cache signatures',
       'Discovers Chromium profile signatures across user AppData and applies consistent GPU/Code/ServiceWorker/IndexedDB cache accounting.',
       need_admin('all-user embedded-browser profile metadata needs elevation') + _DEEP_CLEANUP + r"""
$candidates=@{}
foreach ($user in @(DcProfiles)) {
  foreach ($relative in 'AppData\Local','AppData\Roaming') {
    $root=Join-Path $user.LocalPath $relative
    # Directory discovery is bounded to 7 levels; signature-based, not a fixed app list.
    $stack=[Collections.Generic.Stack[object]]::new(); $stack.Push(@($root,0))
    while ($stack.Count) {
      if ($dcClock.Elapsed.TotalSeconds -gt 120 -or $dcCount -ge 100000) { $script:dcBounded=$true; break }
      $entry=$stack.Pop(); $children=@(DcChildren $entry[0]); $script:dcCount+=$children.Count
      $names=@($children.Name)
      $signals=@($names | Where-Object { $_ -in 'GPUCache','Code Cache','Service Worker','IndexedDB' })
      if ($signals.Count -ge 2 -or ($names -contains 'EBWebView')) { $candidates[[string]$entry[0]]=($signals -join ',') }
      foreach ($child in $children | Where-Object PSIsContainer) {
        if ([long]$child.Attributes -band 0x441400) { $script:dcSkipped++; continue }
        if ($child.Name -in 'GPUCache','Code Cache','Cache','Service Worker','IndexedDB','node_modules','.git') { continue }
        if ($entry[1] -lt 7) { $stack.Push(@($child.FullName,($entry[1]+1))) } else { $script:dcBounded=$true }
      }
    }
  }
}
$rows=@(foreach ($path in $candidates.Keys) {
  $total=[decimal]0; $cells=@{}; $complete=$true
  foreach ($name in 'GPUCache','Code Cache','Service Worker','IndexedDB','Cache') {
    $m=DcMeasure (Join-Path $path $name); $cells[$name]=$m.Bytes; $total+=$m.Bytes; if (-not $m.Complete) { $complete=$false }
  }
  [pscustomobject]@{Profile=$path; Signatures=$candidates[$path]; GPUBytes=$cells.GPUCache; CodeBytes=$cells['Code Cache']; ServiceWorkerBytes=$cells['Service Worker']; IndexedDBBytes=$cells.IndexedDB; CacheBytes=$cells.Cache; TotalBytes=$total; Total=(DcSize $total); Complete=$complete}
})
DcShow ($rows | Sort-Object TotalBytes -Descending)
Chk $null 'Embedded-browser classification' 'Two matching directories imply Chromium-like storage, not proof of Electron/CEF ownership. EBWebView identifies a WebView2 container; nested profile rows may overlap. IndexedDB/Service Worker can hold offline documents and authentication state, not merely disposable cache.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'inventory')),
    AP('cl2-game-platform-content-cache', 'Deep cleanup: game platform content and cache dependencies',
       'Reads Steam library/app manifests to label per-game shader/workshop/download footprints, plus Epic vault and GOG cache metadata.',
       need_admin('machine-wide game libraries and other-user launcher state need elevation') + _DEEP_CLEANUP + r"""
$steamRoots=@{}; $steam=$null
try { $steam=(Get-ItemProperty -LiteralPath 'HKCU:\Software\Valve\Steam' -ErrorAction Stop).SteamPath }
catch { Chk $null 'Steam current-user registration' $_.Exception.Message }
if ($steam) { $steamRoots[$steam]=$true }
foreach ($root in @("${env:ProgramFiles(x86)}\Steam","$env:ProgramFiles\Steam")) { $steamRoots[$root]=$true }
foreach ($root in @($steamRoots.Keys)) {
  $text=@(DcText (Join-Path $root 'steamapps\libraryfolders.vdf') 5000)
  foreach ($line in $text) { if ($line -match '^\s*"path"\s+"([^"]+)"') { $steamRoots[$Matches[1].Replace('\\','\')]=$true } }
}
$rows=@(foreach ($root in $steamRoots.Keys) {
  $apps=@{}
  foreach ($file in @(DcChildren (Join-Path $root 'steamapps') | Where-Object Name -like 'appmanifest_*.acf')) {
    $appid=$null; $title=$null; $state=$null
    foreach ($line in @(DcText $file.FullName 1000)) {
      if ($line -match '^\s*"appid"\s+"(\d+)"') { $appid=$Matches[1] }
      if ($line -match '^\s*"name"\s+"([^"]+)"') { $title=$Matches[1] }
      if ($line -match '^\s*"StateFlags"\s+"(\d+)"') { $state=$Matches[1] }
    }
    if ($appid) { $apps[$appid]="$title (StateFlags=$state)" }
  }
  foreach ($kind in 'shadercache','downloading','workshop\content','workshop\downloads') {
    foreach ($dir in @(DcChildren (Join-Path $root "steamapps\$kind") | Where-Object PSIsContainer)) {
      $m=DcMeasure $dir.FullName
      [pscustomobject]@{Library=$root; Kind=$kind; AppID=$dir.Name; Manifest=$apps[$dir.Name]; Bytes=$m.Bytes; Size=$m.Size; Complete=$m.Complete; Classification=if ($kind -eq 'shadercache') {'regenerable shader data'} elseif ($kind -like 'workshop*') {'subscribed content; potentially redownloadable, may be required'} else {'download/update staging; active transaction possible'}}
    }
  }
  DcShow (DcMeasure (Join-Path $root 'depotcache'))
})
DcShow ($rows | Sort-Object Bytes -Descending)
foreach ($root in @("${env:ProgramFiles(x86)}\Epic Games\Launcher\VaultCache","$env:ProgramData\Epic\EpicGamesLauncher\VaultCache","$env:ProgramData\GOG.com\Galaxy\webcache","$env:ProgramData\GOG.com\Galaxy\logs","$env:ProgramData\Epic\EpicGamesLauncher\Data\Manifests")) { DcShow (DcMeasure $root) }
try {
  $settings=Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\WOW6432Node\Epic Games\EpicGamesLauncher' -ErrorAction Stop
  if ($settings.AppDataPath) { DcShow (DcMeasure (Join-Path $settings.AppDataPath 'VaultCache')) }
} catch { Chk $null 'Epic configured data path' $_.Exception.Message }
foreach ($p in @(DcProfiles)) {
  foreach ($relative in 'AppData\Local\EpicGamesLauncher\Saved\webcache','AppData\Local\EpicGamesLauncher\Saved\webcache_4430','AppData\Local\GOG.com\Galaxy\webcache') { DcShow (DcMeasure (Join-Path $p.LocalPath $relative)) }
}
Chk $null 'Game cleanup boundaries' 'Missing Steam manifest does not prove orphan content: shared depots, moved libraries or another user can explain it. Workshop is content, not generic cache. Saves, userdata and Galaxy databases are essential state and not marked reclaimable. Custom Epic vault locations may be outside discovery.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'inventory', 'triage')),
    TS('cl2-full-disk-triage-dashboard', 'Deep cleanup: ranked disk-space triage dashboard',
       'Ranks major operational files, servicing stores and user caches against volume capacity, keeping overlapping/VSS accounting separate.',
       need_admin('complete disk triage needs protected machine and user metadata') + _DEEP_CLEANUP + _DEEP_BROWSER + r"""
$root="$env:SystemDrive\"; $targets=[Collections.Generic.List[object]]::new()
foreach ($part in @(@{Name='Windows Temp';Path="$env:SystemRoot\Temp"},@{Name='WinSxS logical (hardlinks overlap OS)';Path="$env:SystemRoot\WinSxS"},@{Name='Windows Update';Path="$env:SystemRoot\SoftwareDistribution"},@{Name='Search index';Path="$env:ProgramData\Microsoft\Search\Data"},@{Name='System WER';Path="$env:ProgramData\Microsoft\Windows\WER"},@{Name='Hibernation';Path="${root}hiberfil.sys"},@{Name='Paging';Path="${root}pagefile.sys"},@{Name='UWP swap';Path="${root}swapfile.sys"},@{Name='Recycle Bin';Path=(Join-Path $root '$Recycle.Bin')})) { $targets.Add($part) }
foreach ($p in @(DcProfiles)) {
  foreach ($relative in 'AppData\Local\Temp','AppData\Local\Microsoft\Windows\WER','AppData\Local\CrashDumps') { $targets.Add(@{Name="$($p.SID) $relative";Path=(Join-Path $p.LocalPath $relative)}) }
}
foreach ($p in @(DcBrowserProfiles)) {
  foreach ($name in $(if ($p.Browser -eq 'Firefox') { @('cache2','startupCache') } else { @('Cache','Code Cache','GPUCache','Service Worker') })) { $targets.Add(@{Name="$($p.User) $($p.Browser) $($p.Profile) $name";Path=(Join-Path $p.LocalProfile $name)}) }
}
try { $volumes=@(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction Stop) }
catch { Chk $null 'Capacity baseline' $_.Exception.Message; return }
$seen=@{}; $rows=@(foreach ($target in $targets) {
  if ($seen.ContainsKey($target.Path)) { continue }; $seen[$target.Path]=$true
  $m=DcMeasure $target.Path; $volume=$volumes | Where-Object { $target.Path.StartsWith($_.DeviceID,[StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
  [pscustomobject]@{Consumer=$target.Name; Path=$m.Path; Bytes=$m.Bytes; Size=$m.Size; PercentCapacity=if ($volume.Size -gt 0) {[math]::Round(100*$m.Bytes/$volume.Size,2)} else {$null}; PercentUsed=if ($volume.Size-$volume.FreeSpace -gt 0) {[math]::Round(100*$m.Bytes/($volume.Size-$volume.FreeSpace),2)} else {$null}; Complete=$m.Complete; ReclaimableBytes=$null}
})
DcShow ($rows | Sort-Object Bytes -Descending)
DcShow ($volumes | Select-Object DeviceID,@{n='Capacity';e={DcSize $_.Size}},@{n='Free';e={DcSize $_.FreeSpace}},@{n='FreePercent';e={if ($_.Size -gt 0) {[math]::Round(100*$_.FreeSpace/$_.Size,1)}}})
try {
  foreach ($s in @(Get-CimInstance Win32_ShadowStorage -ErrorAction Stop)) { "[INFO] VSS separate physical accounting: source=$($s.Volume.DeviceID); diff=$($s.DiffVolume.DeviceID); used=$(DcSize $s.UsedSpace); allocated=$(DcSize $s.AllocatedSpace); max=$(DcSize $s.MaxSpace)" }
} catch { Chk $null 'VSS accounting' $_.Exception.Message }
DcNative "$env:SystemRoot\System32\dism.exe" @('/Online','/English','/Cleanup-Image','/AnalyzeComponentStore')
Chk $null 'Dashboard interpretation' 'Rankings are logical attribution, not an additive disk ledger or deletion plan. WinSxS shares OS hard links; VSS physical bytes are shown separately. Incomplete totals and unknown locked/reclaimable bytes must not become zero-space verdicts.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'triage', 'health')),
    TS('cl2-duplicate-dll-version-audit', 'Deep cleanup: DLL version collisions across application roots',
       'Groups DLL basenames by distinct version/architecture and ranks collisions, separating expected Windows side-by-side copies from app candidates.',
       need_admin('System32, SysWOW64 and protected application metadata require elevation') + _DEEP_CLEANUP + r"""
$groups=@{}
foreach ($root in @("$env:SystemRoot\System32","$env:SystemRoot\SysWOW64",$env:ProgramFiles,${env:ProgramFiles(x86)}) | Where-Object { $_ } | Select-Object -Unique) {
  DcFiles $root | Where-Object Extension -eq '.dll' | ForEach-Object {
    $f=$_
    try {
      $safe=DcItem $f.FullName; $v=[Diagnostics.FileVersionInfo]::GetVersionInfo($safe.FullName)
      $architecture=if ($f.FullName -like "$env:SystemRoot\SysWOW64\*") {'Windows x86 directory'} elseif ($f.FullName -like "$env:SystemRoot\System32\*") {'native system directory'} else {'unknown; path is not PE architecture'}
      $entry=[pscustomobject]@{Name=$f.Name; Path=$f.FullName; FileVersion=$v.FileVersion; ProductVersion=$v.ProductVersion; Company=$v.CompanyName; Bytes=$f.Length; Size=(DcSize $f.Length); ArchitectureHint=$architecture}
      if (-not $groups.ContainsKey($f.Name)) { $groups[$f.Name]=[Collections.Generic.List[object]]::new() }; $groups[$f.Name].Add($entry)
    } catch { "NOT AVAILABLE: DLL version $($f.FullName): $($_.Exception.Message)" | Out-Host }
  }
}
$collisions=@(foreach ($name in $groups.Keys) {
  $rows=@($groups[$name]); $versions=@($rows | Where-Object FileVersion | Select-Object -ExpandProperty FileVersion -Unique)
  if ($rows.Count -gt 1 -and $versions.Count -gt 1) {
    [pscustomobject]@{Name=$name; Copies=$rows.Count; DistinctVersions=$versions.Count; Bytes=($rows | Measure-Object Bytes -Sum).Sum; Versions=($versions -join '; '); Rows=$rows}
  }
})
DcShow ($collisions | Sort-Object Bytes -Descending | Select-Object -First 50 Name,Copies,DistinctVersions,Bytes,Versions)
foreach ($c in $collisions | Sort-Object Bytes -Descending | Select-Object -First 20) { "== $($c.Name)"; DcShow $c.Rows }
Chk $null 'DLL conflict confidence' 'Version collisions are leads, not duplicates or proven loader conflicts. x86/x64, application-private runtimes and side-by-side servicing copies are expected. No hashes, signatures, imports or loader search paths evaluated; version resources alone cannot prove equivalence, safety or reclaimable bytes.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'inventory', 'triage')),
    TS('cl2-profile-migration-readiness', 'Deep cleanup: user profile migration sizing and cloud redirects',
       'Breaks down profiles and loaded known-folder redirects, flags roaming-hostile state and models transfer time while keeping cloud content unknown.',
       need_admin('all-user profile sizes and loaded-user shell folders require elevation') + _DEEP_CLEANUP + r"""
foreach ($profile in @(DcProfiles)) {
  "== $($profile.SID); path=$($profile.LocalPath); loaded=$($profile.Loaded); last use=$($profile.LastUseTime)"
  $paths=@{Desktop=(Join-Path $profile.LocalPath 'Desktop');Downloads=(Join-Path $profile.LocalPath 'Downloads');Documents=(Join-Path $profile.LocalPath 'Documents');Local=(Join-Path $profile.LocalPath 'AppData\Local');Roaming=(Join-Path $profile.LocalPath 'AppData\Roaming')}
  $key="Registry::HKEY_USERS\$($profile.SID)\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
  try {
    $shell=Get-ItemProperty -LiteralPath $key -ErrorAction Stop
    foreach ($map in @(@{Label='Desktop';Value='Desktop'},@{Label='Documents';Value='Personal'},@{Label='Downloads';Value='{374DE290-123F-4565-9164-39C4925E467B}'})) {
      $raw=[string]$shell.($map.Value)
      if ($raw) {
        $resolved=$raw.Replace('%USERPROFILE%',$profile.LocalPath)
        if ($resolved -match '%[^%]+%') { "NOT AVAILABLE: $($map.Label) redirect contains user-dependent variables: $raw; not expanded using administrator environment"; $paths[$map.Label]=$null }
        elseif ($resolved.StartsWith('\\')) { "NOT AVAILABLE: $($map.Label) UNC redirect $resolved; remote trees not scanned"; $paths[$map.Label]=$null }
        else { $paths[$map.Label]=$resolved; "[INFO] $($map.Label) resolved=$resolved; OneDrive path hint=$($resolved -match '(?i)OneDrive')" }
      }
    }
  } catch { Chk $null 'Known-folder redirects' "$($_.Exception.Message); hive not mounted; default paths are fallback only, redirects unknown" }
  $seen=@{}; $rows=@(foreach ($label in $paths.Keys) {
    $path=$paths[$label]; if (-not $path) { continue }
    if ($seen.ContainsKey($path)) { "[INFO] Duplicate known-folder target excluded: $label -> $path" | Out-Host; continue }; $seen[$path]=$true
    $m=DcMeasure $path
    [pscustomobject]@{Area=$label;Path=$path;Bytes=$m.Bytes;Size=$m.Size;Files=$m.Files;OldestWrite=$m.OldestWrite;Complete=$m.Complete}
  })
  DcShow ($rows | Sort-Object Bytes -Descending)
  $total=($rows | Measure-Object Bytes -Sum).Sum; $complete=@($rows | Where-Object { -not $_.Complete }).Count -eq 0 -and $rows.Count -eq 5
  $minutes=[math]::Round($total/(50MB)/60,1)
  $ok=if ($total -gt 20GB) {$false} elseif ($complete) {$true} else {$null}
  Chk $ok 'Quick migration threshold' "Selected roots=$(DcSize $total); 20 GiB review threshold; ideal sequential transfer at 50 MiB/s=$minutes minutes; complete=$complete. Cloud data, permissions and many-small-file overhead can dominate. Nested redirects may overlap; full profile is not summed."
  foreach ($relative in 'AppData\Roaming\npm','AppData\Roaming\Code\Cache','AppData\Roaming\Microsoft\Teams','AppData\Roaming\Slack','AppData\Roaming\discord','AppData\Local\Microsoft\Outlook','AppData\Local\Docker','AppData\Local\Packages') {
    $m=DcMeasure (Join-Path $profile.LocalPath $relative)
    if ($m.Files -or -not $m.Complete) { '== Roaming/migration-hostile lead (overlaps areas above)'; DcShow $m }
  }
  foreach ($cloud in @(DcChildren $profile.LocalPath | Where-Object Name -like 'OneDrive*')) { "[INFO] Cloud candidate=$($cloud.FullName); attributes=$($cloud.Attributes); no traversal or hydration. Verify sync completion, tenant and online-only data with the user before migration." }
}
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'triage', 'inventory')),
]

SCRIPTS += [
    TS('cl-crash-dump-sizes', 'Cleanup triage: crash dump sizes',
       'Minidump counts/bytes, MEMORY.DMP and LiveKernelReports sizes and oldest write ages.',
       _CLEANUP_SCAN + need_admin('system crash dumps and LiveKernelReports are protected') + r"""ReportCleanup "$env:SystemRoot\Minidump" '*.dmp' 1
ReportCleanup "$env:SystemRoot\MEMORY.DMP" '*' 1
ReportCleanup "$env:SystemRoot\LiveKernelReports" '*' 1
ReportCleanup "$env:LOCALAPPDATA\CrashDumps" '*.dmp' 1
'[INFO] Default dump locations only; custom crash-dump destinations may exist. Preserve evidence before cleanup.'""",
       admin=True, timeout=600, tags=('diagnostic', 'storage', 'triage')),
    TS('cl-wer-report-sizes', 'Cleanup triage: WER queued and archived reports',
       'Machine and current-user Windows Error Reporting queue/archive bytes, files and oldest ages.',
       _CLEANUP_SCAN + need_admin('machine-wide WER reports can be protected') + r"""foreach ($root in "$env:ProgramData\Microsoft\Windows\WER", "$env:LOCALAPPDATA\Microsoft\Windows\WER") {
  foreach ($name in 'ReportQueue','ReportArchive') { ReportCleanup (Join-Path $root $name) '*' 1 }
}
'[INFO] Other users WER folders are outside this scan; queued reports may still await submission.'""",
       admin=True, timeout=600, tags=('diagnostic', 'storage', 'triage')),
    TS('cl-event-log-capacity', 'Cleanup triage: event log capacity',
       'EVTX file bytes versus configured maximum, percent full, plus unregistered default-folder EVTX files.',
       _CLEANUP_SCAN + need_admin('complete log configuration and Security EVTX metadata need elevation') + r"""try {
  # -Force includes analytic/debug channels; errors are not suppressed.
  $logs = @(Get-WinEvent -ListLog * -Force -ErrorAction Stop)
  if (-not $logs.Count) { 'NOT AVAILABLE: no event log configurations returned'; return }
} catch { "NOT AVAILABLE: event log inventory incomplete: $($_.Exception.Message)"; return }
$known = @{}
foreach ($log in $logs) {
  if (-not $log.LogFilePath) { "NOT AVAILABLE: $($log.LogName) -- EVTX path not reported"; continue }
  $path = [Environment]::ExpandEnvironmentVariables($log.LogFilePath); $known[$path] = $true
  try {
    $file = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    $max = $log.MaximumSizeInBytes
    $pct = if ($null -ne $max -and $max -gt 0) { [math]::Round(100*$file.Length/$max,1) } else { $null }
    [pscustomobject]@{Log=$log.LogName; Path=$path; Bytes=$file.Length; MB=[math]::Round($file.Length/1MB,2);
      MaxBytes=$max; MaxMB=($max/1MB); PercentFull=$pct; Mode=$log.LogMode; Enabled=$log.IsEnabled} | Format-List | Out-String -Width 240
    $ok = if ($null -ne $pct) { $pct -lt 90 } else { $null }
    Chk $ok $log.LogName "physical file occupancy=$pct%; max bytes=$max; mode=$($log.LogMode)"
  } catch { "NOT AVAILABLE: ${path} -- $(CleanupFailure $_): $($_.Exception.Message); occupancy unknown" }
}
foreach ($file in @(CleanupChildren "$env:SystemRoot\System32\winevt\Logs" | Where-Object { -not $_.PSIsContainer -and $_.Extension -eq '.evtx' })) {
  if (-not $known.ContainsKey($file.FullName)) {
    '[INFO] Unmapped/archive EVTX: {0}; {1:N2} MB ({2} bytes); max/% full unknown' -f $file.FullName,($file.Length/1MB),$file.Length
  }
}
'[INFO] Circular logs normally approach max size. File occupancy does not measure retained coverage or justify clearing logs. Custom-directory archive EVTX files are outside the unmapped-file scan.'""",
       admin=True, timeout=300, tags=('diagnostic', 'storage', 'triage')),
    AP('cl-docker-disk-usage', 'Cleanup triage: Docker disk usage',
       'Docker image, container, local-volume and build-cache usage from the selected Docker context.',
       CHK + r"""try { $docker = Get-Command docker.exe -ErrorAction Stop }
catch { 'NOT AVAILABLE: Docker CLI not found; Docker Desktop may not be installed or on PATH'; return }
try {
  $context = & $docker.Source context show 2>&1; $code = $LASTEXITCODE
  if ($code -ne 0) { "NOT AVAILABLE: Docker context query exited ${code}: $context"; return }
  "[INFO] Selected Docker context: $context (may be remote; does not necessarily describe this client disk)"
  $summary = & $docker.Source system df --format='{{json .}}' 2>&1; $code = $LASTEXITCODE
  if ($code -ne 0) { "NOT AVAILABLE: Docker usage query exited ${code}: $summary"; return }
  if (-not $summary) { Chk $null 'Docker usage' 'daemon returned no usage rows'; return }
  $summary
  '[INFO] Docker reports human sizes and reclaimable estimates above. Raw logical bytes below are per object and must not be summed across shared layers.'
  $images = & $docker.Source image ls --quiet --no-trunc 2>&1; $code = $LASTEXITCODE
  if ($code -ne 0) { "NOT AVAILABLE: image inventory exited ${code}: $images" }
  else {
    foreach ($id in @($images | Select-Object -Unique)) {
      $r = & $docker.Source image inspect --format='{{.Id}} Bytes={{.Size}}' $id 2>&1; $code = $LASTEXITCODE
      if ($code -ne 0) { "NOT AVAILABLE: image inspect exited ${code}: $r" } else { $r }
    }
  }
  $volumes = & $docker.Source system df --verbose 2>&1; $code = $LASTEXITCODE
  if ($code -ne 0) { "NOT AVAILABLE: detailed Docker usage exited ${code}: $volumes" } else { $volumes }
  # GET /system/df exposes unrounded byte counts omitted by CLI formatters.
  # Only use the selected local named-pipe endpoint, never an assumed daemon.
  $endpoint = & $docker.Source context inspect --format '{{json .Endpoints.docker.Host}}' 2>&1; $code = $LASTEXITCODE
  if ($code -ne 0) { "NOT AVAILABLE: Docker endpoint query exited ${code}: $endpoint"; return }
  $hostUri = ($endpoint -join "`n") | ConvertFrom-Json -ErrorAction Stop
  if ($hostUri -notmatch '^npipe:/{4}\./pipe/([^/]+)$') {
    "NOT AVAILABLE: raw volume/build-cache bytes require a local named-pipe context; endpoint=$hostUri"; return
  }
  $pipeName = $Matches[1]; $pipe = $null; $reader = $null
  try {
    $pipe = [IO.Pipes.NamedPipeClientStream]::new('.', $pipeName, [IO.Pipes.PipeDirection]::InOut, [IO.Pipes.PipeOptions]::Asynchronous)
    $pipe.Connect(5000)
    # HTTP/1.0 plus Connection: close gives an EOF-delimited JSON response.
    $request = [Text.Encoding]::ASCII.GetBytes("GET /system/df HTTP/1.0`r`nHost: docker`r`nConnection: close`r`n`r`n")
    $pipe.Write($request,0,$request.Length); $pipe.Flush()
    $reader = [IO.StreamReader]::new($pipe, [Text.Encoding]::UTF8)
    $task = $reader.ReadToEndAsync()
    if (-not $task.Wait(60000)) { throw 'Docker raw usage read timed out' }
    $response = $task.Result; $parts = $response -split "`r`n`r`n",2
    if ($parts.Count -ne 2 -or $parts[0] -notmatch '^HTTP/1\.[01] 200\b') { throw "Docker raw usage HTTP query failed: $($parts[0])" }
    if ($parts[0] -match '(?im)^Transfer-Encoding:\s*chunked') { throw 'Unexpected chunked response; raw bytes unavailable' }
    $usage = $parts[1] | ConvertFrom-Json -ErrorAction Stop
    foreach ($kind in 'Images','Volumes','BuildCache') {
      foreach ($item in @($usage.$kind)) {
        if ($null -eq $item) { continue }
        $bytes = if ($kind -eq 'Volumes') { $item.UsageData.Size } else { $item.Size }
        $id = if ($kind -eq 'Volumes') { $item.Name } else { $item.Id }
        if ($null -eq $bytes -or $bytes -lt 0) { "NOT AVAILABLE: $kind $id -- daemon did not report size" }
        else { '[INFO] {0} {1}: {2:N2} MB ({3} bytes)' -f $kind,$id,($bytes/1MB),$bytes }
      }
    }
    '[INFO] Raw daemon sizes are logical usage with shared layers; do not sum them as reclaimable disk bytes. Desktop VHDX allocation can remain larger.'
  } catch { "NOT AVAILABLE: raw Docker usage bytes: $($_.Exception.Message)" }
  finally { if ($null -ne $reader) { $reader.Dispose() }; if ($null -ne $pipe) { $pipe.Dispose() } }
} catch { "NOT AVAILABLE: Docker usage: $($_.Exception.Message)" }""",
       timeout=300, tags=('diagnostic', 'storage', 'inventory')),
    AP('cl-visual-studio-caches', 'Cleanup triage: Visual Studio caches',
       'Visual Studio ComponentModelCache, MEFCacheData and CodeAnalysisCache sizes per instance.',
       _CLEANUP_SCAN + r"""$instances = @(CleanupChildren "$env:LOCALAPPDATA\Microsoft\VisualStudio" | Where-Object PSIsContainer)
if (-not $instances.Count) { 'NOT AVAILABLE: no readable Visual Studio instance folders'; return }
foreach ($instance in $instances) {
  foreach ($name in 'ComponentModelCache','MEFCacheData','CodeAnalysisCache') { ReportCleanup (Join-Path $instance.FullName $name) '*' 1 }
}
'[INFO] Current-user instance folders only; extensions and VS versions can choose other cache locations.'""",
       timeout=600, tags=('diagnostic', 'storage', 'inventory')),
    AP('cl-nuget-global-packages', 'Cleanup triage: NuGet global packages',
       'NuGet global-packages bytes, package/version counts and oldest write age at the environment/default path.',
       _CLEANUP_SCAN + r"""$path = if ($env:NUGET_PACKAGES) { $env:NUGET_PACKAGES } else { "$env:USERPROFILE\.nuget\packages" }
ReportCleanup $path
$packages = @(CleanupChildren $path | Where-Object PSIsContainer)
$versions = 0
foreach ($package in $packages) { $versions += @(CleanupChildren $package.FullName | Where-Object PSIsContainer).Count }
"[INFO] Readable package-id directories=$($packages.Count); version directories=$versions; discovery errors/skips above mean incomplete counts."
'[INFO] NUGET_PACKAGES overrides the default path. NuGet.Config may redirect it elsewhere; SDK commands are not invoked to avoid first-run setup writes. Packages can be shared by active projects; size does not establish disuse.'""",
       timeout=600, tags=('diagnostic', 'storage', 'inventory')),
    AP('cl-node-modules-largest', 'Cleanup triage: largest node_modules folders',
       'Largest node_modules in common development roots; bounded discovery skips links and cloud folders.',
       _CLEANUP_SCAN + r"""$roots = @("$env:USERPROFILE\source\repos", "$env:USERPROFILE\repos", "$env:USERPROFILE\projects", "$env:USERPROFILE\dev", "$env:SystemDrive\dev", "$env:SystemDrive\src")
$seen = @{}; $rows = [Collections.Generic.List[object]]::new(); $visited = 0; $bounded = $false
foreach ($root in $roots) {
  $pending = [Collections.Generic.Stack[object]]::new(); $pending.Push(@($root,0))
  while ($pending.Count) {
    $entry = $pending.Pop(); $visited++
    if ($visited -gt 10000) { $bounded = $true; break }
    foreach ($dir in @(CleanupChildren $entry[0] | Where-Object PSIsContainer)) {
      if ($dir.Name -eq 'node_modules') {
        if (-not $seen.ContainsKey($dir.FullName)) { $seen[$dir.FullName]=$true; $r = MeasureCleanup $dir.FullName; ShowCleanup $r; $rows.Add($r) }
      } elseif ($dir.Name -notin '.git','.vs','bin','obj') {
        if ($entry[1] -lt 5) { $pending.Push(@($dir.FullName,($entry[1]+1))) } else { $bounded = $true }
      }
    }
  }
  if ($visited -gt 10000) { break }
}
'== Largest discovered node_modules (logical bytes; partial rows are lower bounds)'
$rows | Sort-Object Bytes -Descending | Select-Object -First 20 Path,Bytes,MB,GB,Files,Status | Format-Table -AutoSize | Out-String -Width 240
"[INFO] Found=$($rows.Count); depth/node limit reached=$bounded; maximum descent depth=6; directory budget=10000. Not a whole-disk scan."
'[INFO] No file contents opened; cloud/reparse directories skipped. Nested node_modules are included in their outer folder total and not ranked twice. Empty results do not establish that no node_modules exist.'""",
       timeout=900, tags=('diagnostic', 'storage', 'triage')),
]
# Round 2: protected evidence and advanced incident correlation.
SCRIPTS += [
    TS('ts2-live-kernel-reports', 'Live kernel dumps, WHEA slots and GPU TDR evidence',
       'Decodes live dump headers and named WHEA PCIe fields, correlating nearby display events and LiveKernelEvent reports.',
       need_admin('LiveKernelReports and machine WER artifacts are protected') + _TS_EVIDENCE + r"""$since=(Get-Date).AddDays(-30)
$whea=@(TsEvents @{LogName='System';ProviderName='Microsoft-Windows-WHEA-Logger';StartTime=$since} 200)
$display=@(TsEvents @{LogName='System';ProviderName='Display';Id=4101;StartTime=$since} 100)
foreach ($e in $whea) {
  $d=TsData $e
  $kind=switch ($e.Id) { 17 {'corrected PCI Express error'} 18 {'fatal machine-check hardware error'} 19 {'corrected machine-check error'} 20 {'fatal hardware error'} 46 {'fatal memory error'} 47 {'corrected memory error'} default {'schema-specific WHEA record; inspect rendered message'} }
  "[INFO] WHEA classification: $kind"
  [pscustomobject]@{Time=$e.TimeCreated; Event=$e.Id; ErrorSource=$d.ErrorSource; ErrorType=$d.ErrorType; Segment=$d.Segment; Bus=$d.Bus; Device=$d.Device; Function=$d.Function; Slot=$d.Slot; VendorID=$d.VendorID; DeviceID=$d.DeviceID; PrimaryDevice=$d.PrimaryDeviceName; SecondaryDevice=$d.SecondaryDeviceName; APIC=$d.ApicId} | Format-List | Out-String -Width 220
  $e.Message
  Chk $false 'Reported WHEA incident' 'PCIe bus/device/function and named component above are reported evidence; blank fields are NOT AVAILABLE for this schema. Corrected means hardware/firmware recovered the reported error. A root port can report an endpoint/link fault. Raw CPER sections require a WHEA-aware debugger.'
}
try {
  $hardware=@(Get-CimInstance Win32_PnPSignedDriver -ErrorAction Stop | Where-Object { $_.DeviceClass -eq 'DISPLAY' -or $_.DeviceID -like 'PCI*' })
  if (-not $hardware.Count) { throw 'no current PCI/display driver records' }
  '== Current PCI/display component mapping (historical topology may differ)'
  $hardware | Select-Object DeviceName,DeviceID,Location,DriverProviderName,DriverVersion,InfName | Format-Table -Wrap | Out-String -Width 240
} catch { Chk $null 'Fault component / driver context' $_.Exception.Message }
try {
  $files=@(Get-ChildItem -LiteralPath "$env:SystemRoot\LiveKernelReports" -Filter '*.dmp' -Recurse -File -ErrorAction Stop | Sort-Object LastWriteTime -Descending)
  if (-not $files.Count) { Chk $null 'Live dumps' 'no retained dumps returned' }
  "[INFO] Retained dumps=$($files.Count); reading newest 20 headers only."
  foreach ($file in $files | Select-Object -First 20) {
    $stream=$null
    try {
      $stream=[IO.File]::Open($file.FullName,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::ReadWrite)
      $h=New-Object byte[] 4096; $n=$stream.Read($h,0,$h.Length)
      if ($n -lt 96) { throw 'truncated dump header' }
      $magic=[Text.Encoding]::ASCII.GetString($h,0,8)
      $offset=switch ($magic) { 'PAGEDU64' {0x38} 'PAGEDUMP' {0x28} default {throw "unsupported dump format $magic"} }
      $code=[BitConverter]::ToUInt32($h,$offset)
      $meaning=switch ($code) { 0x117 {'VIDEO_TDR_TIMEOUT_DETECTED'} 0x141 {'VIDEO_ENGINE_TIMEOUT_DETECTED'} 0x142 {'VIDEO_TDR_APPLICATION_BLOCKED'} 0x144 {'BUGCODE_USB3_DRIVER'} 0x193 {'VIDEO_DXGKRNL_LIVEDUMP'} 0x1a8 {'VIDEO_DXGKRNL_BLACK_SCREEN_LIVEDUMP'} default {'unmapped live-dump code; debugger needed'} }
      [pscustomobject]@{Path=$file.FullName; Modified=$file.LastWriteTime; Bytes=$file.Length; Code=('0x{0:X}' -f $code); Meaning=$meaning; ComponentHint=$file.Directory.Name} | Format-List | Out-String -Width 220
      $near=@(@($whea)+@($display) | Where-Object { [math]::Abs(($_.TimeCreated-$file.LastWriteTime).TotalMinutes) -le 5 })
      if ($near.Count) { $near | Select-Object TimeCreated,Id,ProviderName,Message | Format-List | Out-String -Width 220 }
      else { Chk $null 'Dump/event association' 'no retained WHEA/Display record within five minutes of file write; write time is only an approximate incident marker' }
      Chk $false 'Live kernel incident' "$meaning; folder name is a component hint, not a proven faulting driver. GPU TDR means recovery was attempted, not necessarily defective hardware."
    } catch { Chk $null $file.FullName $_.Exception.Message }
    finally { if ($stream) { $stream.Dispose() } }
  }
} catch { Chk $null 'LiveKernelReports scan incomplete/unavailable' $_.Exception.Message }
foreach ($root in "$env:ProgramData\Microsoft\Windows\WER\ReportArchive","$env:ProgramData\Microsoft\Windows\WER\ReportQueue") {
  try {
    $dirs=@(Get-ChildItem -LiteralPath $root -Directory -ErrorAction Stop | Where-Object Name -like '*Kernel*' | Sort-Object LastWriteTime -Descending | Select-Object -First 20)
    if (-not $dirs.Count) { Chk $null $root 'no retained kernel report directories' }
    foreach ($dir in $dirs) { "== $($dir.FullName)"; Get-Content -LiteralPath (Join-Path $dir.FullName 'Report.wer') -ErrorAction Stop | Select-String 'EventType=|Sig\[|DynamicSig\[|ReportIdentifier=|Cab' | ForEach-Object Line }
  } catch { Chk $null 'LiveKernelEvent WER metadata' $_.Exception.Message }
}
Chk $null 'Next step' 'Preserve the matching dump and WER report; inspect !analyze -v and, for WHEA, !errrec with symbols. Match PCI VEN/DEV and bus/device/function to current PnP topology; neither a header nor a folder proves the faulting component.'""",
       admin=True, timeout=120, tags=('diagnostic', 'hardware', 'triage', 'health')),
    TS('ts2-application-shims', 'Compatibility databases and per-application shim layers',
       'Joins registered custom SDB databases, AppPatch files and per-app layers across machine and loaded user hives.',
       need_admin('loaded user hives and machine shim registrations require elevation for complete coverage') + CHK + r"""$registered=@{}
foreach ($view in 'HKLM:\SOFTWARE','HKLM:\SOFTWARE\WOW6432Node') {
  foreach ($leaf in 'InstalledSDB','Custom') {
    $root="$view\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\$leaf"
    try {
      if (-not (Test-Path -LiteralPath $root -ErrorAction Stop)) { Chk $null $root 'registration key absent'; continue }
      $keys=@(Get-ChildItem -LiteralPath $root -Recurse -ErrorAction Stop)
      if (-not $keys.Count) { Chk $null $root 'no custom registrations' }
      foreach ($k in $keys) {
        $p=Get-ItemProperty -LiteralPath $k.PSPath -ErrorAction Stop
        "== $($k.Name)"; $p | Format-List | Out-String -Width 220
        if ($p.DatabasePath) { $registered[[Environment]::ExpandEnvironmentVariables($p.DatabasePath)]=$k.Name }
        Chk $false 'Custom shim registration' 'review provenance and application incident timing; registration is not proof the database is loaded in a running process'
      }
    } catch { Chk $null $root $_.Exception.Message }
  }
}
try {
  $files=@(Get-ChildItem -LiteralPath "$env:SystemRoot\AppPatch" -Filter '*.sdb' -Recurse -File -ErrorAction Stop)
  if (-not $files.Count) { throw 'no SDB files returned' }
  foreach ($f in $files) {
    $custom=$f.FullName -match '\\Custom\\' -or $registered.ContainsKey($f.FullName)
    [pscustomobject]@{Path=$f.FullName; Bytes=$f.Length; Modified=$f.LastWriteTime; Registered=$registered.ContainsKey($f.FullName); CustomCandidate=$custom} | Format-List | Out-String -Width 220
    if ($custom) { Chk $false 'Custom SDB candidate' "$($f.FullName); inspect deployment source with Compatibility Administrator; no SDB installed or removed" }
  }
} catch { Chk $null 'AppPatch file inventory' $_.Exception.Message }
$scopes=@('HKLM:\SOFTWARE','HKLM:\SOFTWARE\WOW6432Node','HKCU:\Software')
try { $scopes += @(Get-ChildItem Registry::HKEY_USERS -ErrorAction Stop | Where-Object PSChildName -match '^S-1-5-21-[\d-]+$' | ForEach-Object { "$($_.PSPath)\Software" }) }
catch { Chk $null 'Loaded user hives' $_.Exception.Message }
foreach ($scope in $scopes) {
  $path="$scope\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
  try {
    if (-not (Test-Path -LiteralPath $path -ErrorAction Stop)) { "[INFO] $path -- no Layers key"; continue }
    $p=Get-ItemProperty -LiteralPath $path -ErrorAction Stop
    $layers=@($p.PSObject.Properties | Where-Object Name -notmatch '^PS')
    if (-not $layers.Count) { Chk $null $path 'no per-app layers' }
    foreach ($v in $layers) { Chk $null $v.Name "scope=$scope layers=$($v.Value); RUNASADMIN, version lies and graphics shims can change application behavior" }
  } catch { Chk $null $path $_.Exception.Message }
}
Chk $null 'Active shim coverage' 'Configured registrations/layers only; unloaded user hives, environment __COMPAT_LAYER and in-process loaded shim state are outside coverage. Built-in sysmain.sdb is expected. Correlate custom entries to failing app paths; do not remove shims during evidence collection.'""",
       admin=True, tags=('diagnostic', 'inventory', 'security', 'triage')),
    TS('ts2-com-dcom-history', 'COM+ / DCOM errors with activation permission evidence',
       'Extracts event XML GUIDs, resolves CLSID/AppID registrations and decodes launch/access permission security descriptors.',
       need_admin('machine COM permission descriptors and complete event evidence require elevation') + _TS_EVIDENCE + r"""$events=@(TsEvents @{LogName='System';ProviderName='Microsoft-Windows-DistributedCOM';Id=10000,10001,10005,10010,10016;StartTime=(Get-Date).AddDays(-14)} 200)
$events += @(TsEvents @{LogName='Application';Level=1,2,3;StartTime=(Get-Date).AddDays(-14)} 3000 | Where-Object ProviderName -match 'COM\+|COMPLUS|COMSysApp|Microsoft-Windows-COMRuntime|EventSystem')
if (-not $events.Count) { Chk $null 'COM incident history' 'no matching records in retained bounded sample' }
$seen=@{}
function ComDescriptor($key,$name) {
  try {
    $v=Get-ItemProperty -LiteralPath $key -ErrorAction Stop
    $prop=$v.PSObject.Properties[$name]
    if (-not $prop) { Chk $null "$key / $name" 'no explicit descriptor; inherited/default policy may apply'; return }
    $sd=[Security.AccessControl.RawSecurityDescriptor]::new([byte[]]$prop.Value,0)
    "[INFO] $name SDDL=$($sd.GetSddlForm([Security.AccessControl.AccessControlSections]::All))"
    foreach ($ace in $sd.DiscretionaryAcl) { "[INFO] ACE SID=$($ace.SecurityIdentifier) type=$($ace.AceType) mask=$('0x{0:X}' -f $ace.AccessMask) (1 execute, 2 local launch/access, 4 remote, 8 local activation, 16 remote activation)" }
  } catch { Chk $null "$key / $name" $_.Exception.Message }
}
foreach ($e in $events | Sort-Object TimeCreated -Descending) {
  "== $($e.TimeCreated) $($e.ProviderName) event $($e.Id)"; $e.Message
  $xml=[xml]$e.ToXml(); $d=TsData $e; $d | Format-Table -Wrap | Out-String -Width 220
  $guids=@([regex]::Matches($xml.OuterXml,'\{[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\}') | ForEach-Object Value | Select-Object -Unique)
  foreach ($guid in $guids) {
    if ($seen.ContainsKey($guid)) { continue }; $seen[$guid]=$true
    foreach ($view in 'HKLM:\SOFTWARE\Classes','HKLM:\SOFTWARE\WOW6432Node\Classes') {
      foreach ($type in 'CLSID','AppID') {
        $key="$view\$type\$guid"
        try {
          if (-not (Test-Path -LiteralPath $key -ErrorAction Stop)) { continue }
          $r=Get-ItemProperty -LiteralPath $key -ErrorAction Stop
          "[INFO] Registered $key"; $r | Format-List | Out-String -Width 220
          $appKey=if ($type -eq 'AppID') { $key } elseif ($r.AppID) { "$view\AppID\$($r.AppID)" } else { $null }
          if ($appKey) { ComDescriptor $appKey 'LaunchPermission'; ComDescriptor $appKey 'AccessPermission' }
        } catch { Chk $null $key $_.Exception.Message }
      }
    }
  }
  Chk $null 'Activation incident' '10016 commonly records expected denied activation followed by fallback. Require matching app failure/time/SID/CLSID before calling permissions misconfigured; event XML includes the requested operation and principal.'
}
foreach ($name in 'DefaultLaunchPermission','MachineLaunchRestriction','DefaultAccessPermission','MachineAccessRestriction') { ComDescriptor 'HKLM:\SOFTWARE\Microsoft\Ole' $name }
Chk $null 'Next step' 'Compare event principal and requested local/remote launch/activation against AppID ACL AND machine restrictions. Group membership, deny ACEs and app-container rights require an effective-access analysis. No ACLs changed; do not grant Everyone to silence 10016.'""",
       admin=True, timeout=120, tags=('diagnostic', 'security', 'triage', 'health')),
    TS('ts2-wmi-repository-health', 'WMI repository consistency and provider failure correlation',
       'Runs verifyrepository only, counts registered providers across discovered namespaces and decodes WMI-Activity failures.',
       need_admin('winmgmt repository verification requires elevation') + _TS_EVIDENCE + r"""try {
  $out=@(& "$env:SystemRoot\System32\wbem\winmgmt.exe" /verifyrepository 2>&1); $code=$LASTEXITCODE; $out
  $text=$out -join "`n"
  if ($text -match '\brepository is inconsistent\b') { Chk $false 'Repository consistency' "winmgmt reported inconsistency; exit=$code; preserve evidence before any repair" }
  elseif ($code -eq 0 -and $text -match '\brepository is consistent\b') { Chk $true 'Repository consistency' 'winmgmt reported consistent; this does not establish provider or client query health' }
  else { Chk $null 'Repository verification' "exit=$code; output lines=$($out.Count); localized/unrecognized/refused output above is authoritative; no consistency verdict inferred" }
} catch { Chk $null 'Repository verification' $_.Exception.Message }
$queue=[Collections.Generic.Queue[string]]::new(); $queue.Enqueue('root'); $visited=0; $registered=0; $failed=0; $clock=[Diagnostics.Stopwatch]::StartNew()
while ($queue.Count) {
  if ($visited -ge 150 -or $clock.Elapsed.TotalSeconds -gt 45) { Chk $null 'Namespace scope' "$($queue.Count) namespaces unvisited; 150 namespace / 45 second budget reached"; break }
  $ns=$queue.Dequeue(); $visited++
  try {
    $providers=@(Get-CimInstance -Namespace $ns -ClassName __Win32Provider -OperationTimeoutSec 3 -ErrorAction Stop)
    $registered += $providers.Count
    "[INFO] Namespace=$ns registered providers=$($providers.Count) (zero means none registered here, not no inherited providers)"
    $providers | Select-Object Name,CLSID,HostingModel | Format-Table -Wrap | Out-String -Width 220
  } catch { $failed++; Chk $null "$ns providers" $_.Exception.Message }
  try { Get-CimInstance -Namespace $ns -ClassName __NAMESPACE -OperationTimeoutSec 3 -ErrorAction Stop | ForEach-Object { $queue.Enqueue("$ns\$($_.Name)") } }
  catch { $failed++; Chk $null "$ns children" $_.Exception.Message }
}
Chk $null 'Provider inventory' "visited=$visited provider registrations=$registered failed reads=$failed pending=$($queue.Count); counts are registrations, not unique or healthy running providers"
$events=@(TsEvents @{LogName='Microsoft-Windows-WMI-Activity/Operational';Id=5857,5858,5859,5860,5861;StartTime=(Get-Date).AddDays(-7)} 200)
$known=@{'0x80041002'='WBEM_E_NOT_FOUND (object/query target absent)';'0x80041003'='WBEM_E_ACCESS_DENIED';'0x8004100E'='WBEM_E_INVALID_NAMESPACE';'0x80041010'='WBEM_E_INVALID_CLASS';'0x80041013'='WBEM_E_PROVIDER_LOAD_FAILURE';'0x80041014'='WBEM_E_INITIALIZATION_FAILURE';'0x80041032'='WBEM_E_CALL_CANCELLED'}
foreach ($e in $events) {
  $d=TsData $e; "== $($e.TimeCreated) event=$($e.Id)"; $d | Format-Table -Wrap | Out-String -Width 220
  $e.Message
  foreach ($m in [regex]::Matches($e.ToXml(),'0x[0-9a-fA-F]{8}')) { $key=$m.Value.ToUpperInvariant().Replace('0X','0x'); if ($known.ContainsKey($key)) { "[INFO] $key -- $($known[$key])" } }
}
Chk $null 'Corruption assessment' 'Invalid class/namespace may mean a missing product provider; access denied and cancellation are not corruption. Correlate repeated provider load/initialization failures with verifyrepository and client PID/operation in event XML. Provider counts cannot prove consistency; preserve evidence before any repair.'""",
       admin=True, timeout=120, tags=('diagnostic', 'health', 'triage', 'inventory')),
]
SCRIPTS += [
    TS('ts2-process-token-privileges', 'Enabled dangerous privileges in process tokens',
       'Queries every readable primary process token, showing enabled sensitive privileges and flagging non-SYSTEM owners.',
       need_admin('other process tokens require elevation; protected processes may still refuse') + CHK + r"""try {
  if (-not ('Ts2TokenReader' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
public static class Ts2TokenReader {
  [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, int id);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
  [DllImport("advapi32.dll", SetLastError=true)] static extern bool GetTokenInformation(IntPtr token, int kind, IntPtr data, int size, out int needed);
  [StructLayout(LayoutKind.Sequential)] struct Luid { public uint Low; public int High; }
  [DllImport("advapi32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool LookupPrivilegeName(string system, ref Luid luid, StringBuilder name, ref int size);
  public sealed class Result { public int PID; public string SID; public string Owner; public string[] Enabled; public string Error; }
  public static Result Read(int id) {
    var r=new Result { PID=id, Enabled=new string[0] }; IntPtr p=IntPtr.Zero,t=IntPtr.Zero,b=IntPtr.Zero;
    try {
      p=OpenProcess(0x1000,false,id); if(p==IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error());
      if(!OpenProcessToken(p,8,out t)) throw new Win32Exception(Marshal.GetLastWin32Error());
      using(var identity=new WindowsIdentity(t)) { r.SID=identity.User.Value; r.Owner=identity.Name; }
      int n; GetTokenInformation(t,3,IntPtr.Zero,0,out n);
      if(n<4) throw new Win32Exception(Marshal.GetLastWin32Error());
      b=Marshal.AllocHGlobal(n); if(!GetTokenInformation(t,3,b,n,out n)) throw new Win32Exception(Marshal.GetLastWin32Error());
      int count=Marshal.ReadInt32(b); if(count<0 || 4L+12L*count>n) throw new Exception("Invalid TOKEN_PRIVILEGES size");
      var names=new List<string>();
      for(int i=0;i<count;i++) {
        IntPtr at=IntPtr.Add(b,4+12*i); uint attr=unchecked((uint)Marshal.ReadInt32(at,8));
        if((attr&2)==0) continue;
        var luid=(Luid)Marshal.PtrToStructure(at,typeof(Luid)); int len=256; var text=new StringBuilder(len);
        if(!LookupPrivilegeName(null,ref luid,text,ref len)) throw new Win32Exception(Marshal.GetLastWin32Error());
        names.Add(text.ToString());
      }
      r.Enabled=names.ToArray();
    } catch(Exception ex) { r.Error=ex.Message; }
    finally { if(b!=IntPtr.Zero) Marshal.FreeHGlobal(b); if(t!=IntPtr.Zero) CloseHandle(t); if(p!=IntPtr.Zero) CloseHandle(p); }
    return r;
  }
}
'@ -ErrorAction Stop
  }
  $danger='SeDebugPrivilege','SeTcbPrivilege','SeAssignPrimaryTokenPrivilege','SeImpersonatePrivilege','SeCreateTokenPrivilege','SeLoadDriverPrivilege','SeBackupPrivilege','SeRestorePrivilege','SeTakeOwnershipPrivilege','SeSecurityPrivilege','SeTrustedCredManAccessPrivilege','SeRelabelPrivilege','SeManageVolumePrivilege'
  $processes=@(Get-CimInstance Win32_Process -ErrorAction Stop)
  if (-not $processes.Count) { throw 'process inventory empty' }
  $read=0; $unknown=0; $flagged=0
  foreach ($p in $processes) {
    $r=[Ts2TokenReader]::Read([int]$p.ProcessId)
    if ($r.Error) { $unknown++; Chk $null "$($p.Name) PID=$($p.ProcessId)" "NOT AVAILABLE: token read: $($r.Error)"; continue }
    $read++; $enabled=@($r.Enabled | Where-Object { $_ -in $danger })
    $nonSystem=$r.SID -ne 'S-1-5-18'
    if ($enabled.Count -and $nonSystem) { $flagged++; Chk $false "$($p.Name) PID=$($p.ProcessId)" "non-SYSTEM owner=$($r.Owner) SID=$($r.SID); enabled=$($enabled -join ', ')" }
    [pscustomobject]@{Name=$p.Name; PID=$p.ProcessId; Owner=$r.Owner; SID=$r.SID; Path=$(if ($p.ExecutablePath) { $p.ExecutablePath } else {'NOT AVAILABLE'}); EnabledDangerous=($enabled -join ', '); Count=$enabled.Count} | Format-List | Out-String -Width 220
  }
  $ok=if ($flagged) { $false } elseif ($unknown) { $null } else { $true }
  Chk $ok 'Token scan coverage' "read=$read unknown=$unknown non-SYSTEM candidates=$flagged; zero listed privileges applies only to successful reads"
} catch { Chk $null 'Token audit' $_.Exception.Message }
Chk $null 'Interpretation' 'Primary tokens only; thread impersonation tokens are outside scope. Services such as LOCAL SERVICE/NETWORK SERVICE can legitimately hold SeImpersonatePrivilege. Enabled privileges indicate capability, not malicious use or full token elevation. Process exit/PID reuse can race metadata. No privileges enabled, adjusted or impersonated.'""",
       admin=True, timeout=120, tags=('diagnostic', 'security', 'triage', 'compliance')),
    TS('ts2-scheduled-task-deep-scan', 'Scheduled tasks with external or indirect actions',
       'Examines every registered task XML, classifies triggers and actions, and decodes last-run results for external/indirect launches.',
       need_admin('complete registered task visibility requires elevation') + CHK + r"""try {
  $tasks=@(Get-ScheduledTask -ErrorAction Stop)
  if (-not $tasks.Count) { throw 'no scheduled tasks returned' }
} catch { Chk $null 'Task inventory' $_.Exception.Message; return }
$roots=@($env:SystemRoot,$env:ProgramFiles,${env:ProgramFiles(x86)},$env:ProgramW6432) | Where-Object { $_ } | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\')+'\' }
$review=0; $failed=0
foreach ($task in $tasks) {
  try {
    $xml=[xml](Export-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction Stop)
    $triggers=@($xml.SelectNodes('/*[local-name()="Task"]/*[local-name()="Triggers"]/*') | ForEach-Object { "$($_.LocalName) enabled=$($_.Enabled) start=$($_.StartBoundary) subscription=$($_.Subscription)" })
    if (-not $triggers.Count) { continue }
    $actions=@($xml.SelectNodes('/*[local-name()="Task"]/*[local-name()="Actions"]/*'))
    if (-not $actions.Count) { Chk $null ($task.TaskPath+$task.TaskName) 'trigger exists but no readable actions'; continue }
    $leads=@()
    foreach ($action in $actions) {
      if ($action.LocalName -ne 'Exec') { $leads += "$($action.LocalName): indirect action; class=$($action.ClassId); review COM registration or handler"; continue }
      $exe=[Environment]::ExpandEnvironmentVariables([string]$action.Command).Trim('"')
      $argsText=[string]$action.Arguments; $absolute=$exe -match '^[A-Za-z]:\\'
      $trusted=$false
      if ($absolute) { $full=[IO.Path]::GetFullPath($exe); foreach ($root in $roots) { if ($full.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)) { $trusted=$true } } }
      $wrapper=[IO.Path]::GetFileName($exe) -match '^(powershell|pwsh|cmd|wscript|cscript|mshta|rundll32|regsvr32|python\d*|java|node)\.exe$'
      if (-not $trusted -or $wrapper) { $leads += "Exec=$exe; arguments=$argsText; workingDirectory=$($action.WorkingDirectory); classification=$(if (-not $absolute) {'relative/UNC/unresolved environment: manual resolution'} elseif (-not $trusted) {'outside Windows/ProgramFiles'} else {'trusted-path interpreter/loader: inspect payload arguments'})" }
    }
    if (-not $leads.Count) { continue }; $review++
    $info=$null
    try { $info=Get-ScheduledTaskInfo -TaskName $task.TaskName -TaskPath $task.TaskPath -ErrorAction Stop }
    catch { $failed++; Chk $null 'Task runtime data' $_.Exception.Message }
    $result='NOT AVAILABLE'; $decoded='NOT AVAILABLE'
    if ($info -and $null -ne $info.LastTaskResult) {
      $code=[uint32]$info.LastTaskResult; $result='0x{0:X8}' -f $code
      # Match formatted strings: PowerShell hex literals above Int32.MaxValue can be signed.
      $decoded=switch ($result) { '0x00000000' {'success'} '0x00041300' {'ready'} '0x00041301' {'running'} '0x00041302' {'disabled'} '0x00041303' {'has not run'} '0x00041306' {'terminated'} '0x80070002' {'file not found'} '0x80070005' {'access denied'} '0x8007052E' {'logon failure'} default { if (($code -band 4294901760L) -eq 2147942400L) { [ComponentModel.Win32Exception]::new([int]($code -band 65535)).Message } else {'scheduler HRESULT or application exit code; consult action documentation'} } }
    }
    [pscustomobject]@{Task=$task.TaskPath+$task.TaskName; State=$task.State; Principal=$task.Principal.UserId; RunLevel=$task.Principal.RunLevel; Triggers=($triggers -join '; '); Actions=($leads -join "`n"); LastRun=$info.LastRunTime; LastResult=$result; Meaning=$decoded; NextRun=$info.NextRunTime} | Format-List | Out-String -Width 240
  } catch { $failed++; Chk $null ($task.TaskPath+$task.TaskName) $_.Exception.Message }
}
Chk $null 'Task review scope' "tasks=$($tasks.Count) candidates=$review failed reads=$failed; all tasks with triggers examined, including disabled tasks. Triggerless on-demand tasks excluded."
Chk $null 'Next step' 'Review external paths, event/logon/boot triggers, owner and run level against approved deployment records. Trusted directory does not establish trust; interpreter payloads may be external/encoded. Current-user environment expansion can differ from the task principal. No task or action executed.'""",
       admin=True, timeout=120, tags=('diagnostic', 'inventory', 'security', 'triage')),
    TS('ts2-vss-consumers-providers', 'VSS writers, providers, consumers and storage allocation',
       'Correlates writer/provider native inventories with shadow ownership clues, provider IDs and per-volume diff-area allocation.',
       need_admin('VSS writer/provider queries require elevation') + _TS_EVIDENCE + r"""foreach ($kind in 'writers','providers') {
  try {
    "== VSS $kind"
    $out=@(& "$env:SystemRoot\System32\vssadmin.exe" list $kind 2>&1); $code=$LASTEXITCODE; $out
    if ($code -ne 0 -or -not $out.Count) { Chk $null "VSS $kind" "NOT AVAILABLE: exit=$code lines=$($out.Count)" }
    elseif ($kind -eq 'writers') {
      # Numeric VSS_WRITER_STATE: stable=1. Labels are localized; parse bracketed numeric state, never translate success text.
      $states=@($out | ForEach-Object { if ([string]$_ -match '\[(\d+)\]') { [int]$Matches[1] } })
      if (-not $states.Count) { Chk $null 'Writer health' 'state numbers not parsed; inspect localized native output, no clean result inferred' }
      else { Chk (-not @($states | Where-Object { $_ -ne 1 }).Count) 'Writer states' "$($states.Count) state values; 1=Stable, 2-5=in progress, 6-15=failed states. Inspect last error for each writer; stable state alone does not prove last backup succeeded." }
    }
  } catch { Chk $null "VSS $kind" $_.Exception.Message }
}
$providers=@{}
try {
  $p=@(Get-CimInstance Win32_ShadowProvider -ErrorAction Stop)
  if (-not $p.Count) { throw 'no VSS providers returned' }
  foreach ($v in $p) {
    $providers[$v.ID]=$v.Name
    $v | Select-Object ID,Name,Type,CLSID,Version,VersionID | Format-List | Out-String -Width 220
    Chk ($v.ID -eq '{b5946137-7b9f-4925-af80-51abd60b20d5}') 'Provider review' "$($v.Name); $($v.ID); non-inbox provider warrants vendor/version verification, not a malware verdict"
  }
} catch { Chk $null 'Provider mapping' $_.Exception.Message }
try {
  $copies=@(Get-CimInstance Win32_ShadowCopy -ErrorAction Stop)
  if (-not $copies.Count) { Chk $null 'Shadow copies' 'no retained shadow copies returned' }
  foreach ($c in $copies) {
    [pscustomobject]@{ID=$c.ID; SetID=$c.SetID; Created=$c.InstallDate; Volume=$c.VolumeName; Device=$c.DeviceObject; ProviderID=$c.ProviderID; Provider=$providers[$c.ProviderID]; Origin=$c.OriginatingMachine; ServiceMachine=$c.ServiceMachine; ClientAccessible=$c.ClientAccessible; Persistent=$c.Persistent; NoAutoRelease=$c.NoAutoRelease; State=$c.State} | Format-List | Out-String -Width 220
  }
} catch { Chk $null 'Shadow copy consumer clues' $_.Exception.Message }
try {
  $storage=@(Get-CimInstance Win32_ShadowStorage -ErrorAction Stop)
  if (-not $storage.Count) { Chk $null 'Diff-area storage' 'no shadow storage associations; third-party providers may manage allocation separately' }
  foreach ($s in $storage) {
    [pscustomobject]@{SourceVolume=$s.Volume; DiffVolume=$s.DiffVolume; UsedBytes=$s.UsedSpace; AllocatedBytes=$s.AllocatedSpace; MaxBytes=$s.MaxSpace} | Format-List | Out-String -Width 220
    $ok=if ($null -eq $s.UsedSpace -or $null -eq $s.MaxSpace -or $s.MaxSpace -eq 0 -or $s.MaxSpace -eq [uint64]::MaxValue) { $null } else { $s.UsedSpace/[double]$s.MaxSpace -lt .9 }
    Chk $ok 'Diff-area headroom' '>=90% of finite quota warrants review; zero/unbounded/absent max is not treated as healthy. Allocated and used space differ.'
  }
} catch { Chk $null 'Shadow storage' $_.Exception.Message }
TsEvents @{LogName='Application';ProviderName='VSS';Level=1,2,3;StartTime=(Get-Date).AddDays(-7)} 30 | Select-Object TimeCreated,Id,Message | Format-List | Out-String -Width 220
Chk $null 'Consumer attribution / next step' 'Shadow copies expose provider and origin, not a reliable requesting backup product/PID. SetID/time can be matched to vendor backup logs and VSS errors; requestor identity is NOT AVAILABLE here. Do not infer an orphan from age or delete snapshots. No snapshot, backup or writer repair started.'""",
       admin=True, timeout=120, tags=('diagnostic', 'storage', 'health', 'inventory', 'triage')),
]

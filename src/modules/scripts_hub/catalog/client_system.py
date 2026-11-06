"""System, Hardware, Storage and Performance checks. Read-only."""
from ._base import CHK, CMD, L, T, maker, need_admin

# Round 2 plan: metadata-safe bounded traversal, source-specific correlations,
# then catalog/PowerShell parser checks. Keep each emitted script standalone.
_DEEP_CLEANUP = CHK + r"""
$script:dcClock = [Diagnostics.Stopwatch]::StartNew()
$script:dcCount = 0; $script:dcFailures = 0; $script:dcSkipped = 0; $script:dcBounded = $false
function DcSize($n) { if ($null -eq $n) { return 'unknown' }; '{0:N3} GiB ({1} bytes)' -f ($n/1GB),$n }
function DcItem($path) {
  # Validate every ancestor too: a direct path beneath a cloud directory must
  # not bypass the walker guard. Get-Item reads attributes, never file data.
  $full = [IO.Path]::GetFullPath($path)
  $parts = [Collections.Generic.Stack[string]]::new(); $cursor = $full
  while ($cursor) { $parts.Push($cursor); $parent = [IO.Directory]::GetParent($cursor); $cursor = if ($parent) { $parent.FullName } else { $null } }
  while ($parts.Count) {
    $item = Get-Item -LiteralPath $parts.Pop() -Force -ErrorAction Stop
    if ([long]$item.Attributes -band 0x441400) { throw "reparse/cloud/offline path excluded: $($item.FullName)" }
  }
  return $item
}
function DcChildren($path) {
  try {
    $i = DcItem $path
    if (-not $i.PSIsContainer) { throw 'expected directory' }
    Get-ChildItem -LiteralPath $i.FullName -Force -ErrorAction Stop
  } catch { $script:dcFailures++; "NOT AVAILABLE: ${path}: $($_.Exception.Message)" | Out-Host }
}
function DcFiles($path) {
  $stack = [Collections.Generic.Stack[string]]::new(); $stack.Push($path)
  while ($stack.Count) {
    if ($dcClock.Elapsed.TotalSeconds -gt 235 -or $script:dcCount -ge 400000) { $script:dcBounded=$true; break }
    $current=$stack.Pop()
    try {
      $i=DcItem $current
      $children=if ($i.PSIsContainer) { @(Get-ChildItem -LiteralPath $current -Force -ErrorAction Stop) } else { @($i) }
      foreach ($c in $children) {
        if ($script:dcCount -ge 400000 -or $dcClock.Elapsed.TotalSeconds -gt 235) { $script:dcBounded=$true; break }
        $script:dcCount++
        if ([long]$c.Attributes -band 0x441400) { $script:dcSkipped++; continue }
        if ($c.PSIsContainer) { $stack.Push($c.FullName) } else { $c }
      }
    } catch { $script:dcFailures++; "NOT AVAILABLE: ${current}: $($_.Exception.Message)" | Out-Host }
  }
}
function DcMeasure($path) {
  $before=$dcFailures; $skip=$dcSkipped; $bytes=[decimal]0; $count=0; $oldest=$null
  DcFiles $path | ForEach-Object {
    $bytes+=$_.Length; $count++
    if ($null -eq $oldest -or $_.LastWriteTime -lt $oldest) { $oldest=$_.LastWriteTime }
  }
  [pscustomobject]@{Path=$path; Bytes=$bytes; Size=(DcSize $bytes); Files=$count; OldestWrite=$oldest;
    Complete=($before -eq $dcFailures -and $skip -eq $dcSkipped -and -not $dcBounded)}
}
function DcShow($rows) {
  # Keep raw numeric columns and add a readable partner for every byte metric.
  $display=@(foreach ($row in @($rows)) {
    if ($null -eq $row) { continue }; $view=[ordered]@{}
    foreach ($property in $row.PSObject.Properties) {
      $view[$property.Name]=$property.Value
      if ($property.Name -match 'Bytes$') { $view[($property.Name -replace 'Bytes$','Size')]=DcSize $property.Value }
    }
    [pscustomobject]$view
  })
  $display | Format-Table -AutoSize -Wrap | Out-String -Width 320
}
function DcNative($exe, $arguments) {
  try {
    $out=@(& $exe @arguments 2>&1); $code=$LASTEXITCODE
    if ($code -ne 0) { throw "exit $code -- $($out -join ' ')" }
    if (-not $out.Count) { throw 'empty response' }; $out
  } catch { "NOT AVAILABLE: $exe $arguments -- $($_.Exception.Message)" | Out-Host }
}
function DcText($path, $tail=12000) {
  try { $i=DcItem $path; Get-Content -LiteralPath $i.FullName -Tail $tail -ErrorAction Stop }
  catch { "NOT AVAILABLE: ${path}: $($_.Exception.Message)" | Out-Host }
}
function DcProfiles {
  try {
    $p=@(Get-CimInstance Win32_UserProfile -ErrorAction Stop | Where-Object { -not $_.Special -and $_.LocalPath })
    if (-not $p.Count) { throw 'no non-special profiles returned' }; $p
  } catch { "NOT AVAILABLE: user profiles: $($_.Exception.Message)" | Out-Host }
}
function DcEnd {
  Chk $null 'Scan coverage' "entries=$dcCount; refused/missing=$dcFailures; excluded cloud/reparse=$dcSkipped; budget reached=$dcBounded (235s/400000 entries). Partial totals are lower bounds."
  '[INFO] Logical lengths may double-count hard links. No file locks are probed; locked/reclaimable bytes require source-specific evidence. No files are changed.'
}
"""

# Native allocation query is metadata only; callers exclude cloud/reparse files.
_DEEP_ALLOCATION = r"""
try {
  if (-not ('CleanupAllocationV2' -as [type])) {
    Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class CleanupAllocationV2 {
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern uint GetCompressedFileSizeW(string name, out uint high);
  [DllImport("kernel32.dll")] static extern void SetLastError(uint code);
  public static ulong Bytes(string name) {
    uint high; SetLastError(0); uint low=GetCompressedFileSizeW(name,out high);
    int error=Marshal.GetLastWin32Error();
    if(low==0xffffffff && error!=0) throw new System.ComponentModel.Win32Exception(error);
    return ((ulong)high << 32) | low;
  }
}
'@
  }
} catch { "NOT AVAILABLE: allocation API: $($_.Exception.Message)"; return }
"""

_DEEP_NTFS = r"""
try {
  if (-not ('CleanupNtfsV2' -as [type])) {
    Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class CleanupNtfsV2 {
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
  static extern SafeFileHandle CreateFile(string p,uint a,uint s,IntPtr sec,uint mode,uint flags,IntPtr t);
  [DllImport("kernel32.dll", SetLastError=true)]
  static extern bool DeviceIoControl(SafeFileHandle h,uint code,IntPtr input,uint insize,byte[] output,uint size,out uint used,IntPtr overlap);
  public static long[] Volume(string path) {
    using(var h=CreateFile(path,0,3,IntPtr.Zero,3,0,IntPtr.Zero)) {
      if(h.IsInvalid) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
      byte[] data=new byte[128]; uint used;
      if(!DeviceIoControl(h,0x90064,IntPtr.Zero,0,data,(uint)data.Length,out used,IntPtr.Zero))
        throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
      if(used<96) throw new InvalidOperationException("short NTFS volume data");
      return new long[]{BitConverter.ToInt64(data,16),BitConverter.ToInt64(data,24),
        BitConverter.ToInt64(data,32),BitConverter.ToUInt32(data,44),BitConverter.ToInt64(data,56)};
    }
  }
}
'@
  }
} catch { "NOT AVAILABLE: native NTFS accounting: $($_.Exception.Message)"; return }
"""

S = maker("System")
H = maker("Hardware")
D = maker("Storage")
P = maker("Performance")

# Sampled checks complement the instantaneous listings below.
_TRIAGE_SCRIPTS = [
    P("client_correlated_triage_60s", "CPU, disk queue and memory: 60-second triage",
      "Twelve correlated snapshots over 60 seconds; CIM counters avoid localized counter paths.",
      CHK + r"""$clock = [Diagnostics.Stopwatch]::StartNew()
for ($sample=1; $sample -le 12; $sample++) {
  $wait = 5*$sample - $clock.Elapsed.TotalSeconds
  if ($wait -gt 0) { Start-Sleep -Milliseconds ([int](1000*$wait)) }
  try {
    $cpu = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'" -ErrorAction Stop
    $mem = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory -ErrorAction Stop
    $disk = @(Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -ErrorAction Stop | Where-Object Name -ne '_Total')
    if (-not $cpu -or -not $mem -or -not $disk.Count) { Chk $null "Sample $sample" 'one or more providers returned no data'; continue }
    $commit = if ($mem.CommitLimit -gt 0 -and $null -ne $mem.CommittedBytes) { [math]::Round(100*$mem.CommittedBytes/$mem.CommitLimit,1) } else { $null }
    foreach ($d in $disk) {
      [pscustomobject]@{Time=(Get-Date).ToString('HH:mm:ss'); CPUPercent=$cpu.PercentProcessorTime; Disk=$d.Name; Queue=$d.CurrentDiskQueueLength; CommitPercent=$commit; AvailableMB=$mem.AvailableMBytes; PagesInPerSec=$mem.PagesInputPersec; PagesOutPerSec=$mem.PagesOutputPersec} | Format-Table -AutoSize | Out-String -Width 220
    }
    if ($null -eq $commit) { Chk $null 'Commit' 'commit usage or limit unavailable' }
    else { Chk ($commit -lt 90) 'Commit headroom' "$commit% committed; 90% is a triage threshold" }
  } catch { Chk $null "Sample $sample" $_.Exception.Message }
}
'[INFO] Queries in each snapshot are sequential, not atomic. Paging includes file-backed hard faults; correlate repeated peaks before diagnosing a bottleneck.'""",
      timeout=180, tags=('diagnostic', 'performance', 'triage', 'health')),
    P("client_etw_session_inventory", "Active ETW trace sessions",
      "Lists existing trace sessions without starting, stopping or exporting a trace.",
      need_admin("complete ETW session visibility needs elevation") + r"""try {
  $result = & "$env:SystemRoot\System32\logman.exe" query -ets 2>&1; $code = $LASTEXITCODE
  if ($code -ne 0) { "ERROR: ETW query exited $code" }
  if (-not $result) { 'NOT AVAILABLE: ETW query returned no output.' } else { $result }
} catch { "ERROR: ETW inventory: $($_.Exception.Message)" }""",
      admin=True, tags=('diagnostic', 'performance', 'inventory')),
    P("client_handle_thread_growth", "Handle and thread growth over 15 seconds",
      "Ranks process handle and thread growth, matching PID and creation time to avoid PID reuse.",
      r"""try {
  $before = @{}; Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object { $before["$($_.ProcessId)|$($_.CreationDate)"] = $_ }
  if (-not $before.Count) { 'NOT AVAILABLE: first process snapshot empty.'; return }
  Start-Sleep -Seconds 15
  $after = @(Get-CimInstance Win32_Process -ErrorAction Stop)
  if (-not $after.Count) { 'NOT AVAILABLE: second process snapshot empty.'; return }
  $rows = @(foreach ($p in $after) {
    $old = $before["$($p.ProcessId)|$($p.CreationDate)"]
    if ($old -and $p.CreationDate -and $null -ne $p.HandleCount -and $null -ne $old.HandleCount) {
      [pscustomobject]@{Name=$p.Name; PID=$p.ProcessId; Handles=$p.HandleCount; HandleDelta=([long]$p.HandleCount-[long]$old.HandleCount); Threads=$p.ThreadCount; ThreadDelta=([long]$p.ThreadCount-[long]$old.ThreadCount)}
    }
  })
  if (-not $rows.Count) { 'NOT AVAILABLE: no processes with comparable readable snapshots.'; return }
  '== Largest handle increases'; $rows | Sort-Object HandleDelta -Descending | Select-Object -First 15 | Format-Table -AutoSize | Out-String -Width 220
  '== Largest thread increases'; $rows | Sort-Object ThreadDelta -Descending | Select-Object -First 15 | Format-Table -AutoSize | Out-String -Width 220
  '[INFO] Positive growth over one interval is not proof of a leak; new/exited processes and missing creation times are excluded.'
} catch { "ERROR: Process growth sampling: $($_.Exception.Message)" }""",
      tags=('diagnostic', 'performance', 'triage')),
    P("client_process_io_read_write", "Per-process read and write byte deltas",
      "Measures read/write transfer counts over five seconds with stable process identities; includes non-disk I/O.",
      r"""try {
  $first = @{}; Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object { $first["$($_.ProcessId)|$($_.CreationDate)"] = $_ }
  if (-not $first.Count) { 'NOT AVAILABLE: initial process snapshot empty.'; return }
  $timer = [Diagnostics.Stopwatch]::StartNew(); Start-Sleep -Seconds 5
  $last = @(Get-CimInstance Win32_Process -ErrorAction Stop); $seconds = $timer.Elapsed.TotalSeconds
  $rows = @(foreach ($p in $last) {
    $old = $first["$($p.ProcessId)|$($p.CreationDate)"]
    if (-not $old -or -not $p.CreationDate) { continue }
    if ($null -eq $old.ReadTransferCount -or $null -eq $p.ReadTransferCount -or $null -eq $old.WriteTransferCount -or $null -eq $p.WriteTransferCount) { "NOT AVAILABLE: I/O counters for PID $($p.ProcessId)"; continue }
    $read = [decimal]$p.ReadTransferCount-[decimal]$old.ReadTransferCount; $write = [decimal]$p.WriteTransferCount-[decimal]$old.WriteTransferCount
    if ($read -lt 0 -or $write -lt 0) { "NOT AVAILABLE: counters reset for PID $($p.ProcessId)"; continue }
    [pscustomobject]@{Name=$p.Name; PID=$p.ProcessId; ReadBytes=$read; WriteBytes=$write; TotalBytes=$read+$write; ReadMBps=[math]::Round($read/1MB/$seconds,3); WriteMBps=[math]::Round($write/1MB/$seconds,3)}
  })
  if (-not $rows.Count) { 'NOT AVAILABLE: no comparable I/O counters.' }
  $rows | Sort-Object TotalBytes -Descending | Select-Object -First 25 | Format-Table -AutoSize | Out-String -Width 220
  '[INFO] Transfer counts include file, network and device I/O. Cached I/O is included; these are not physical disk throughput measurements.'
} catch { "ERROR: Process I/O sampling: $($_.Exception.Message)" }""",
      tags=('diagnostic', 'performance', 'storage', 'triage')),
    P("client_clr_memory_pressure", ".NET Framework CLR heap and GC pressure",
      "Shows managed heaps, large object heap, GC time and collection counters for CLR instances.",
      CHK + r"""try {
  $rows = @(Get-CimInstance Win32_PerfFormattedData_NETFramework_NETCLRMemory -ErrorAction Stop | Where-Object Name -ne '_Global_')
  if (-not $rows.Count) { Chk $null 'CLR memory' 'no instrumented .NET Framework processes returned'; return }
  foreach ($p in $rows) {
    $p | Select-Object Name, ProcessID, BytesinallHeaps, LargeObjectHeapsize, Gen2heapsize, PercentTimeinGC, NumberGCHandles, NumberofPinnedObjects | Format-List | Out-String -Width 220
    $ok = if ($null -eq $p.PercentTimeinGC) { $null } else { $p.PercentTimeinGC -lt 20 }
    Chk $ok $p.Name "$($p.PercentTimeinGC)% in GC at the last collection; 20% is a review threshold, not proof of sustained pressure"
  }
  '[INFO] Modern .NET/Core processes may not publish these legacy CLR counters; absence does not mean no managed applications.'
} catch { Chk $null 'CLR counters unavailable' $_.Exception.Message }""",
      tags=('diagnostic', 'performance', 'health')),
]

SCRIPTS = [
    P("shared_disk_queue_snapshot", "Physical disk queues right now",
      "Current and average read/write queue lengths from CIM performance data, independent of counter language.",
      CHK + r"""try {
  $disks = @(Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -ErrorAction Stop | Where-Object Name -ne '_Total')
  Chk $null 'Disk queues' "$($disks.Count) disk instances; a single snapshot cannot establish sustained saturation or an appropriate queue threshold"
  $disks | Select-Object Name, CurrentDiskQueueLength, AvgDiskQueueLength, AvgDiskReadQueueLength, AvgDiskWriteQueueLength, DiskReadsPersec, DiskWritesPersec | Format-Table -AutoSize | Out-String -Width 220
} catch { Chk $null 'Disk queues unavailable' $_.Exception.Message }""",
      tags=('diagnostic', 'performance', 'storage', 'triage')),
    P("shared_commit_pagefile_ram", "Commit headroom, page files and physical RAM",
      "Compares live commit limit and usage with physical RAM and page-file allocation; no fixed RAM multiplier.",
      CHK + r"""try {
  $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
  $memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory -ErrorAction Stop
  $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
  $files = @(Get-CimInstance Win32_PageFileUsage -ErrorAction Stop)
  [pscustomobject]@{PhysicalRAM_GB=[math]::Round($cs.TotalPhysicalMemory/1GB,2); VisibleRAM_GB=[math]::Round($os.TotalVisibleMemorySize/1MB,2); AutomaticPagefile=$cs.AutomaticManagedPagefile; CommitGB=[math]::Round($memory.CommittedBytes/1GB,2); LimitGB=[math]::Round($memory.CommitLimit/1GB,2); PagefileAllocatedMB=($files | Measure-Object AllocatedBaseSize -Sum).Sum} | Format-List | Out-String -Width 220
  if ($memory.CommitLimit -gt 0 -and $null -ne $memory.CommittedBytes) {
    $percent = 100 * $memory.CommittedBytes / $memory.CommitLimit
    Chk ($percent -lt 90) 'Commit headroom' ('{0:N1}% committed; 90% is a triage threshold, not a page-file sizing recommendation' -f $percent)
  } else { Chk $null 'Commit headroom' 'commit counters not reported' }
  $files | Select-Object Name, AllocatedBaseSize, CurrentUsage, PeakUsage | Format-Table -AutoSize | Out-String -Width 220
} catch { Chk $null 'Memory configuration' $_.Exception.Message }""",
      tags=('diagnostic', 'performance', 'health')),
    H("shared_net_driver_packages", "Network adapter driver package evidence",
      "PnP network driver versions, dates, providers, INF names and device IDs, including virtual adapters.",
      r"""try {
  $drivers = @(Get-CimInstance Win32_PnPSignedDriver -Filter "DeviceClass='NET'" -ErrorAction Stop)
  if (-not $drivers.Count) { '[INFO] No PnP network driver records returned.'; return }
  $drivers | Sort-Object DeviceName | Select-Object DeviceName, DriverVersion, DriverDate, DriverProviderName, InfName, IsSigned, DeviceID | Format-Table -AutoSize -Wrap | Out-String -Width 220
  '[INFO] Driver dates are package metadata, not installation times or proof that an update is needed.'
} catch { "ERROR: Network driver packages: $($_.Exception.Message)" }""",
      tags=('diagnostic', 'hardware', 'network', 'inventory')),
    S("shared_complus_runtime", "COM+ server applications currently running",
      "Reads running COM+ application instances and process IDs without activating or recycling applications.",
      need_admin("COM+ runtime catalog reads may require elevation") + r"""try {
  $catalog = New-Object -ComObject COMAdmin.COMAdminCatalog -ErrorAction Stop
  $instances = $catalog.GetCollection('ApplicationInstances'); $instances.Populate()
  if (-not $instances.Count) { '[INFO] No running COM+ server application instances reported.'; return }
  foreach ($instance in $instances) {
    [pscustomobject]@{Name=$instance.Name; InstanceID=$instance.Key; ApplicationID=$instance.Value('Application'); ProcessID=$instance.Value('ProcessID')}
  }
} catch { "NOT AVAILABLE: COM+ runtime instances: $($_.Exception.Message)" }""",
      admin=True, tags=('diagnostic', 'inventory', 'triage')),
    S("shared_crash_dump_artifacts", "Crash dump files and dedicated dump configuration",
      "Existing kernel and minidump sizes and dates plus dedicated dump settings; never opens dump contents.",
      need_admin("crash dump metadata and protected folders require elevation") + CHK + r"""try {
  $cfg = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' -ErrorAction Stop
  $cfg | Select-Object CrashDumpEnabled, DedicatedDumpFile, DumpFileSize, AlwaysKeepMemoryDump, IgnorePagefileSize, LogEvent | Format-List | Out-String -Width 220
  foreach ($configured in @($cfg.DumpFile, $cfg.DedicatedDumpFile) | Where-Object { $_ } | Select-Object -Unique) {
    $path = [Environment]::ExpandEnvironmentVariables($configured)
    try {
      if (Test-Path -LiteralPath $path -ErrorAction Stop) {
        Get-Item -LiteralPath $path -Force -ErrorAction Stop | Select-Object FullName, Length, LastWriteTime | Format-List | Out-String -Width 220
      } else { Chk $null 'Dump artifact' "$path absent; no crash or retained dump may exist" }
    } catch { Chk $null 'Dump artifact' $_.Exception.Message }
  }
  if ($cfg.MinidumpDir) {
    $dir = [Environment]::ExpandEnvironmentVariables($cfg.MinidumpDir)
    if (Test-Path -LiteralPath $dir -ErrorAction Stop) {
      Get-ChildItem -LiteralPath $dir -Filter '*.dmp' -File -ErrorAction Stop | Sort-Object LastWriteTime -Descending | Select-Object -First 10 Name, Length, LastWriteTime | Format-Table -AutoSize | Out-String -Width 220
    } else { Chk $null 'Minidump folder' "$dir absent" }
  }
} catch { Chk $null 'Crash dump evidence' $_.Exception.Message }""",
      admin=True, tags=('diagnostic', 'storage', 'triage')),
    S("app_crash_details", "Application crash details (7 days)",
      "Checks Application Error and Windows Error Reporting events for faulting applications, "
      "modules, exception codes and report identifiers in the last seven days.",
      CHK + r"""try {
  $events = @(Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Application Error','Windows Error Reporting'; Id=1000,1001; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 200 -ErrorAction Stop)
} catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { Chk $true 'Crash records' 'no matching events in seven days'; return }
  "ERROR: Application log read failed: $($_.Exception.Message)"; return
}
Chk $false 'Crash records' "$($events.Count) newest matching records (limit 200); one crash can produce multiple records"
foreach ($event in $events) {
  $data = @{}; ([xml]$event.ToXml()).Event.EventData.Data | ForEach-Object { if ($_.Name) { $data[$_.Name] = $_.'#text' } }
  "[INFO] $($event.TimeCreated)  $($event.ProviderName)  event $($event.Id)"
  if ($event.Id -eq 1000) { "App: $($data.AppName)  Module: $($data.ModuleName)  Exception: $($data.ExceptionCode)  Path: $($data.AppPath)" }
  else { "Type: $($data.EventName)  App/signature: $($data.P1)  Report: $($data.ReportId)" }
  $event.Message
}""",
      tags=('diagnostic', 'triage', 'health'), keywords="WER fault exception crash"),
    S("pca_event_history", "Application compatibility events (30 days)",
      "Checks Program Compatibility Assistant records for application paths and compatibility "
      "decisions. Records are evidence for investigation, not proof that a shim is active.",
      r"""$log = 'Microsoft-Windows-Application-Experience/Program-Compatibility-Assistant'
try {
  $info = Get-WinEvent -ListLog $log -ErrorAction Stop
  if (-not $info.IsEnabled) { 'NOTE: Program Compatibility Assistant logging is disabled; retained events may be incomplete.' }
  $events = @(Get-WinEvent -FilterHashtable @{LogName=$log; StartTime=(Get-Date).AddDays(-30)} -MaxEvents 100 -ErrorAction Stop)
  foreach ($event in $events) { "[INFO] $($event.TimeCreated)  event $($event.Id)"; $event.Message; $event.ToXml() }
} catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { '[INFO] No compatibility events retained for the last 30 days.' }
  else { "NOTE: compatibility log unavailable or refused: $($_.Exception.Message)" }
}""",
      tags=('diagnostic', 'triage'), keywords="PCA shim compatibility"),
    S("resource_exhaustion_history", "Low virtual memory incidents (30 days)",
      "Checks Resource Exhaustion Detector event 2004 for past commit exhaustion and the "
      "processes Windows identified, including incidents missed by a current memory snapshot.",
      CHK + r"""try {
  $events = @(Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Resource-Exhaustion-Detector'; Id=2004; StartTime=(Get-Date).AddDays(-30)} -MaxEvents 50 -ErrorAction Stop)
  Chk $false 'Virtual memory exhaustion' "$($events.Count) newest incidents (limit 50)"
  foreach ($event in $events) { "[INFO] $($event.TimeCreated)"; $event.Message }
} catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { Chk $true 'Virtual memory exhaustion' 'no event 2004 retained in 30 days' }
  else { "ERROR: resource exhaustion history unavailable: $($_.Exception.Message)" }
}""",
      tags=('diagnostic', 'performance', 'triage', 'health'), keywords="commit pagefile low memory"),
    # ---- System ------------------------------------------------------------------------------
    S("os_version", "Windows version, edition and build",
      "Edition, display version (24H2/25H2...), build and revision (UBR), install type and "
      "the date this installation was created. The UBR is what tells two '26100' machines "
      "apart: it moves with every cumulative update.",
      L(r"""$cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
[pscustomobject]@{ Product=$cv.ProductName; Edition=$cv.EditionID; Version=$cv.DisplayVersion
  Build="$($cv.CurrentBuild).$($cv.UBR)"; InstallationType=$cv.InstallationType
  Installed=([datetime]'1970-01-01').AddSeconds($cv.InstallDate).ToLocalTime()
  RegisteredOwner=$cv.RegisteredOwner }"""),
       tags=('diagnostic', 'inventory', 'baseline')),
    S("activation", "Windows activation status",
      "License status, channel (Retail / OEM / Volume) and partial product key of the Windows "
      "license, from the Software Licensing service -- what slmgr /dli shows, without its "
      "message box.",
      L(r"""Get-CimInstance SoftwareLicensingProduct -Filter "ApplicationID='55c92734-d682-4d71-983e-d6ec3f16059f' AND PartialProductKey IS NOT NULL" |
  Select-Object Name, Description, @{n='Status';e={ switch ($_.LicenseStatus) { 0 {'Unlicensed'} 1 {'Licensed'} 2 {'OOB grace'} 3 {'OOT grace'} 4 {'Non-genuine grace'} 5 {'Notification'} 6 {'Extended grace'} default {$_} } }}, PartialProductKey, ProductKeyChannel"""),
       tags=('diagnostic',)),
    S("env_machine", "System environment variables",
      "Machine-wide environment variables (HKLM), including PATH split one entry per line -- "
      "a missing or duplicated PATH entry is behind many 'command not found' problems.",
      r"""$e = [Environment]::GetEnvironmentVariables('Machine')
$e.Keys | Sort-Object | Where-Object { $_ -ne 'Path' } | ForEach-Object { '{0,-28} {1}' -f $_, $e[$_] }
''; 'PATH entries:'
$e['Path'] -split ';' | Where-Object { $_ } | ForEach-Object { if (Test-Path ([Environment]::ExpandEnvironmentVariables($_))) { "[OK]   $_" } else { "[WARN] $_   (does not exist)" } }""",
       tags=('diagnostic', 'health')),
    S("env_user", "User environment variables",
      "This user's environment variables (HKCU), with the user PATH checked entry by entry "
      "for folders that no longer exist.",
      r"""$e = [Environment]::GetEnvironmentVariables('User')
$e.Keys | Sort-Object | Where-Object { $_ -ne 'Path' } | ForEach-Object { '{0,-28} {1}' -f $_, $e[$_] }
''; 'User PATH entries:'
($e['Path'] -split ';') | Where-Object { $_ } | ForEach-Object { if (Test-Path ([Environment]::ExpandEnvironmentVariables($_))) { "[OK]   $_" } else { "[WARN] $_   (does not exist)" } }""",
       tags=('diagnostic', 'health')),
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
Chk ($repl -eq 0) 'File replacements' "$repl queued replacement(s); queued deletions do not need a restart" """,
       tags=('diagnostic', 'triage', 'health')),
    S("time_sync", "Time and time synchronisation",
      "Time zone, the NTP source Windows Time uses and when it last synced. Kerberos, TLS and "
      "domain sign-in fail when the clock drifts more than five minutes.",
      r"""Get-TimeZone | Select-Object Id, DisplayName | Format-List | Out-String -Width 220
w32tm /query /status /verbose 2>&1 | Select-String 'Source|Last Successful|Leap|Stratum|Poll|Phase' | ForEach-Object { $_.Line.Trim() }""",
      note="w32tm may refuse some fields",
       tags=('diagnostic', 'triage')),
    S("locale", "Language, region and keyboard",
      "Display language, system locale (what non-Unicode programs use -- the cause of "
      "garbled text in old apps), user region format and installed keyboard layouts.",
      L(r"""[pscustomobject]@{ SystemLocale=(Get-WinSystemLocale).Name; UICulture=(Get-UICulture).Name
  Culture=(Get-Culture).Name; HomeLocation=(Get-WinHomeLocation).HomeLocation
  Languages=((Get-WinUserLanguageList).LanguageTag -join ', ')
  Keyboards=((Get-WinUserLanguageList).InputMethodTips -join ', ') }"""),
       tags=('diagnostic', 'inventory', 'baseline')),
    S("features_enabled", "Optional features that are enabled",
      "Windows optional features turned on (Hyper-V, WSL, .NET 3.5, SMB1...). SMB1 or "
      "Telnet enabled here is a hardening finding.",
      need_admin("Get-WindowsOptionalFeature needs elevation") +
      T("Get-WindowsOptionalFeature -Online | Where-Object State -eq 'Enabled' | Sort-Object FeatureName | Select-Object FeatureName, State"),
      admin=True, timeout=240,
       tags=('diagnostic', 'inventory', 'baseline', 'security')),
    S("capabilities", "Installed Windows capabilities",
      "Features on Demand that are installed: RSAT tools, OpenSSH, language packs, legacy "
      "components like Internet Explorer mode or WordPad.",
      need_admin("Get-WindowsCapability needs elevation") +
      T("Get-WindowsCapability -Online | Where-Object State -eq 'Installed' | Sort-Object Name | Select-Object Name, State"),
      admin=True, timeout=240,
       tags=('diagnostic', 'inventory', 'baseline')),
    S("dotnet_versions", ".NET Framework and .NET runtimes",
      ".NET Framework 4.x release (mapped to its version) plus every modern .NET runtime "
      "installed side by side. 'This app needs .NET X' starts here.",
      r"""$rel = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -ErrorAction SilentlyContinue).Release
$fw = switch ($rel) { { $_ -ge 533320 } { '4.8.1' ; break } { $_ -ge 528040 } { '4.8'; break } { $_ -ge 461808 } { '4.7.2'; break } { $_ -ge 394802 } { '4.6.2'; break } default { if ($rel) { "release $rel" } else { 'not found' } } }
".NET Framework: $fw (release $rel)"
''
if (Get-Command dotnet -ErrorAction SilentlyContinue) { 'Runtimes (dotnet --list-runtimes):'; dotnet --list-runtimes } else { 'dotnet host not on PATH -- modern .NET runtimes not found via PATH' }""",
       tags=('diagnostic', 'inventory', 'baseline')),
    S("powershell_info", "PowerShell versions and execution policy",
      "Windows PowerShell version, whether PowerShell 7 is installed, the effective execution "
      "policy per scope and whether script block / module logging is configured.",
      r""""Windows PowerShell: $($PSVersionTable.PSVersion)"
if (Get-Command pwsh -ErrorAction SilentlyContinue) { "PowerShell 7: $((Get-Command pwsh).Version)" } else { 'PowerShell 7: not installed' }
''
Get-ExecutionPolicy -List | Format-Table -AutoSize | Out-String -Width 220
$sb = (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\PowerShell\ScriptBlockLogging' -ErrorAction SilentlyContinue).EnableScriptBlockLogging
"Script block logging policy: $(if ($sb -eq 1) { 'ON' } else { 'not set by policy' })" """,
       tags=('diagnostic', 'inventory', 'baseline')),
    S("wsl_status", "WSL distributions",
      "Whether the Windows Subsystem for Linux is installed and which distributions exist, "
      "with their state and WSL version.",
      r"""$env:WSL_UTF8 = '1'
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { '[INFO] WSL is not installed.'; return }
$out = wsl.exe --list --verbose 2>&1 | ForEach-Object { "$_" -replace "`0", '' } | Where-Object { $_.Trim() }
if ($LASTEXITCODE -ne 0) { "[INFO] No WSL distribution is installed ($(($out | Select-Object -First 1)))" } else { $out }""",
       tags=('diagnostic', 'inventory', 'baseline')),
    S("crash_settings", "Crash dump and recovery settings",
      "Crash dump type, paths, automatic restart and allocated page files. Kernel, "
      "automatic, complete and active dumps are OK; small dumps are WARN and disabled "
      "dumps are BAD. Allocated page file sizes alone cannot prove dump capacity.",
      CHK + L(r"""$c = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl'
[pscustomobject]@{ DumpType = switch ($c.CrashDumpEnabled) { 0 {'None'} 1 {'Complete'} 2 {'Kernel'} 3 {'Small (minidump)'} 7 {'Automatic'} default { $c.CrashDumpEnabled } }
  DumpFile=$c.DumpFile; MinidumpDir=$c.MinidumpDir; AutoReboot=[bool]$c.AutoReboot; Overwrite=[bool]$c.Overwrite
  PageFiles=((Get-CimInstance Win32_PageFileUsage | ForEach-Object { "$($_.Name) $($_.AllocatedBaseSize) MB" }) -join ', ') }""") + r"""
$type = $c.CrashDumpEnabled
if ($null -eq $type) { Chk $null 'Crash dump configuration' 'CrashDumpEnabled not reported' }
elseif ($type -eq 0) { '[BAD]  Crash dump configuration -- None (CrashDumpEnabled=0); crash evidence will not be saved' }
elseif ($type -in 1,2,7,10) { Chk $true 'Crash dump configuration' "CrashDumpEnabled=$type (1 complete, 2 kernel, 7 automatic, 10 active)" }
elseif ($type -eq 3) { Chk $false 'Crash dump configuration' 'Small (minidump); limited diagnostic detail' }
else { Chk $null 'Crash dump configuration' "unrecognised CrashDumpEnabled=$type" }
""",
       tags=('diagnostic', 'triage', 'health')),
    S("page_file", "Page file configuration and usage",
      "Whether Windows manages the page file, where it is, its size and peak usage since boot. "
      "A page file disabled or too small breaks crash dumps and some games.",
      CHK + r"""$cs = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
$files = @(Get-CimInstance Win32_PageFileUsage -ErrorAction Stop)
$settings = @(Get-CimInstance Win32_PageFileSetting -ErrorAction Stop)
$auto = $cs.AutomaticManagedPagefile
Chk $auto 'Automatic page file management' $(if ($null -eq $auto) { 'not reported' } elseif ($auto) { 'Windows manages sizing' } else { 'manual configuration; review allocation and workload' })
$files | Select-Object Name, AllocatedBaseSize, CurrentUsage, PeakUsage | Format-Table -AutoSize | Out-String -Width 220
if (-not $files.Count) { Chk $false 'Page file allocation' 'no active page file reported; review dump and commit requirements'; return }
foreach ($f in $files) {
  $setting = $settings | Where-Object Name -eq $f.Name | Select-Object -First 1
  $managed = ($auto -eq $true) -or ($setting -and $setting.InitialSize -eq 0 -and $setting.MaximumSize -eq 0)
  if ($managed) { Chk $true 'Page file sizing' "$($f.Name): system-managed, currently $($f.AllocatedBaseSize) MB" }
  elseif ($null -eq $f.AllocatedBaseSize) { Chk $null 'Page file sizing' "$($f.Name): size not reported" }
  elseif ($f.AllocatedBaseSize -lt 4096) { Chk $false 'Page file sizing' "$($f.Name): $($f.AllocatedBaseSize) MB; below 4096 MB review threshold, needs depend on workload and dump type" }
  else { Chk $null 'Page file sizing' "$($f.Name): $($f.AllocatedBaseSize) MB manually sized; validate against peak commit and dump requirements" }
}
""",
       tags=('diagnostic', 'triage', 'health')),
    S("virtualization", "Virtualization and memory integrity",
      "Virtualization-based security, Credential Guard and memory integrity (HVCI) as "
      "configured and as actually RUNNING, plus whether the CPU's virtualization is on in "
      "firmware.",
      L(r"""$dg = Get-CimInstance -Namespace root\Microsoft\Windows\DeviceGuard -ClassName Win32_DeviceGuard -ErrorAction SilentlyContinue
$map = @{0='none';1='Credential Guard';2='Memory integrity (HVCI)';3='System Guard';4='SMM protection';5='Kernel-mode HW stack protection'}
[pscustomobject]@{
  VBS = switch ($dg.VirtualizationBasedSecurityStatus) { 0 {'off'} 1 {'enabled, not running'} 2 {'RUNNING'} default {'unknown'} }
  Configured = (($dg.SecurityServicesConfigured | ForEach-Object { $v=[int]$_; if ($map.ContainsKey($v)) { $map[$v] } else { "Unknown($v)" } }) -join ', ')
  Running = (($dg.SecurityServicesRunning | ForEach-Object { $v=[int]$_; if ($map.ContainsKey($v)) { $map[$v] } else { "Unknown($v)" } }) -join ', ')
  FirmwareVirtualization = (Get-CimInstance Win32_Processor | Select-Object -First 1).VirtualizationFirmwareEnabled
  HypervisorPresent = (Get-CimInstance Win32_ComputerSystem).HypervisorPresent }"""),
       tags=('diagnostic', 'security')),
    S("secure_boot_tpm", "Secure Boot, TPM and firmware mode",
      "UEFI or legacy BIOS, Secure Boot on/off and the TPM's presence, version and readiness "
      "-- the Windows 11 and BitLocker prerequisites.",
      CHK + r"""$fw = $env:firmware_type; if (-not $fw) { $fw = if (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State') { 'UEFI' } else { 'Legacy BIOS' } }
Chk ($fw -eq 'UEFI') 'Firmware' $fw
try { $sb = Confirm-SecureBootUEFI -ErrorAction Stop; Chk $sb 'Secure Boot' $(if ($sb) {'on'} else {'off'}) } catch { Chk $null 'Secure Boot' $_.Exception.Message }
try { $t = Get-Tpm -ErrorAction Stop; Chk ($t.TpmPresent -and $t.TpmReady) 'TPM' "present=$($t.TpmPresent) ready=$($t.TpmReady) enabled=$($t.TpmEnabled)" } catch { Chk $null 'TPM' 'needs administrator to read' }
$spec = (Get-CimInstance -Namespace root\cimv2\Security\MicrosoftTpm -ClassName Win32_Tpm -ErrorAction SilentlyContinue).SpecVersion
if ($spec) { "[INFO] TPM spec version -- $spec" }""",
      note="the TPM and Secure Boot reads need elevation", admin=True,
       tags=('diagnostic', 'security', 'health')),
    # ---- Hardware ----------------------------------------------------------------------------
    H("cpu_info", "Processor details",
      "Model, cores and logical processors, base clock, L2/L3 cache, socket and current load.",
      L("Get-CimInstance Win32_Processor | Select-Object Name, Manufacturer, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed, L2CacheSize, L3CacheSize, SocketDesignation, LoadPercentage, VirtualizationFirmwareEnabled"),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    H("memory_modules", "Memory modules (DIMMs)",
      "Every installed memory stick: slot, capacity, configured speed against rated speed "
      "(XMP/EXPO not applied shows up here), manufacturer and part number.",
      T("Get-CimInstance Win32_PhysicalMemory | Select-Object DeviceLocator, @{n='GB';e={[math]::Round($_.Capacity/1GB,1)}}, Speed, ConfiguredClockSpeed, Manufacturer, PartNumber"),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    H("memory_slots", "Memory slots used and maximum",
      "How many memory slots the board has, how many are used and the maximum capacity it "
      "supports -- 'can I add RAM?'.",
      L(r"""$a = Get-CimInstance Win32_PhysicalMemoryArray
$m = @(Get-CimInstance Win32_PhysicalMemory)
[pscustomobject]@{ Slots=$a.MemoryDevices; Used=$m.Count; InstalledGB=[math]::Round(($m | Measure-Object Capacity -Sum).Sum/1GB,1); MaxSupportedGB=[math]::Round($a.MaxCapacityEx/1MB,0) }"""),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    H("gpu_info", "Graphics adapters",
      "Every GPU with its driver version and date, video memory (as Windows reports it -- "
      "capped at 4 GB in this WMI class) and current resolution and refresh rate.",
      T("Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, @{n='DriverDate';e={$_.DriverDate.ToString('yyyy-MM-dd')}}, @{n='VRAM(GB)';e={[math]::Round($_.AdapterRAM/1GB,1)}}, CurrentHorizontalResolution, CurrentVerticalResolution, CurrentRefreshRate, Status"),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    H("motherboard_bios", "Motherboard and BIOS",
      "Board manufacturer, model and version, BIOS vendor, version and release date, and the "
      "system serial number (for warranty lookups).",
      CHK + L(r"""$b = Get-CimInstance Win32_BaseBoard; $bios = Get-CimInstance Win32_BIOS; $cs = Get-CimInstance Win32_ComputerSystemProduct
[pscustomobject]@{ Board="$($b.Manufacturer) $($b.Product) $($b.Version)"; BIOS="$($bios.Manufacturer) $($bios.SMBIOSBIOSVersion)"
  BIOSDate=$bios.ReleaseDate; Serial=$bios.SerialNumber; UUID=$cs.UUID; SMBIOS="$($bios.SMBIOSMajorVersion).$($bios.SMBIOSMinorVersion)" }""") + r"""
if ($bios.ReleaseDate) { $age = ((Get-Date) - $bios.ReleaseDate).Days; Chk $null 'BIOS firmware' "$($bios.SMBIOSBIOSVersion), released $($bios.ReleaseDate.ToString('yyyy-MM-dd')) ($age days old); compare with the OEM release and security advisories" }
else { Chk $null 'BIOS firmware' "$($bios.SMBIOSBIOSVersion); release date not reported" }
""",
       tags=('diagnostic', 'hardware', 'inventory', 'baseline', 'health')),
    H("problem_devices", "Devices with problems",
      "Every device Device Manager shows with a yellow or red mark, with its error code "
      "explained (28 = no driver, 10 = cannot start, 43 = device reported a problem...).",
      r"""$codes = @{1='not configured';3='driver may be corrupted';10='cannot start';12='not enough resources';14='needs a restart';18='reinstall the drivers';19='registry configuration damaged';22='disabled';24='not present or not working';28='no driver installed';31='not working properly';32='driver disabled';37='driver failed to initialise';39='driver missing or corrupted';43='device reported a problem';45='not connected';52='driver signature could not be verified'}
$bad = Get-CimInstance Win32_PnPEntity | Where-Object { $_.ConfigManagerErrorCode -ne 0 }
if (-not $bad) { '[OK]   No device reports a problem.'; return }
$bad | ForEach-Object { $c = [int]$_.ConfigManagerErrorCode; "[WARN] $($_.Name) -- code $c ($($codes[$c]))  [$($_.PNPDeviceID)]" }""",
       tags=('diagnostic', 'hardware', 'triage', 'health')),
    H("usb_devices", "USB devices connected",
      "USB devices present right now, with their vendor/product ids -- useful for 'what is "
      "this unknown device'.",
      T("Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object InstanceId -like 'USB*' | Sort-Object Class, FriendlyName | Select-Object Class, FriendlyName, Status, InstanceId"),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    H("monitors", "Monitors connected",
      "Each monitor's manufacturer, model, serial and year of manufacture, decoded from its "
      "EDID (WmiMonitorID).",
      T(r"""Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID -ErrorAction SilentlyContinue | ForEach-Object {
  [pscustomobject]@{ Manufacturer=-join ($_.ManufacturerName | Where-Object {$_} | ForEach-Object {[char]$_}); Model=-join ($_.UserFriendlyName | Where-Object {$_} | ForEach-Object {[char]$_})
    Serial=-join ($_.SerialNumberID | Where-Object {$_} | ForEach-Object {[char]$_}); Year=$_.YearOfManufacture; Active=$_.Active } }"""),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    H("audio_devices", "Sound devices",
      "Sound hardware Windows knows about, with status -- for 'no sound' tickets.",
      T("Get-CimInstance Win32_SoundDevice | Select-Object Name, Manufacturer, Status"),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    H("network_adapters_hw", "Network adapters (hardware)",
      "Physical network adapters with their driver, link speed, MAC address and media state.",
      T("Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Select-Object Name, InterfaceDescription, Status, LinkSpeed, MacAddress, DriverVersion, DriverDate"),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline', 'network')),
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
if ($c) { "Cycle count: $($c.CycleCount)" }""", note="the battery health classes need elevation", admin=True,
       tags=('diagnostic', 'hardware', 'health')),
    H("bluetooth", "Bluetooth devices",
      "Paired and present Bluetooth devices and the radio itself, with status.",
      T("Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | Sort-Object FriendlyName | Select-Object FriendlyName, Status, Present"),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    H("cameras", "Cameras and imaging devices",
      "Webcams and scanners Windows sees, with status -- for 'the camera does not work in "
      "Teams'.",
      T("Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | Where-Object { $_.Class -in 'Camera','Image' } | Select-Object Class, FriendlyName, Status"),
       tags=('diagnostic', 'hardware', 'inventory', 'baseline')),
    # ---- Storage -----------------------------------------------------------------------------
    D("volumes", "Volumes and free space",
      "Every volume with file system, size, free space and health, with low space flagged.",
      r"""Get-Volume | Where-Object DriveLetter | Sort-Object DriveLetter | ForEach-Object {
  $pct = if ($_.Size) { [math]::Round(100 * $_.SizeRemaining / $_.Size, 1) } else { 0 }
  $tag = if ($pct -lt 10) { '[BAD] ' } elseif ($pct -lt 20) { '[WARN]' } else { '[OK]  ' }
  '{0} {1}:  {2,-6} {3,9:N1} GB free of {4,9:N1} GB ({5}%)  {6}  {7}' -f $tag, $_.DriveLetter, $_.FileSystem, ($_.SizeRemaining/1GB), ($_.Size/1GB), $pct, $_.HealthStatus, $_.FileSystemLabel }""",
       tags=('diagnostic', 'storage', 'health')),
    D("physical_disks", "Physical disks and health",
      "Each disk's model, bus (NVMe/SATA/USB), media type, size, health and operational "
      "status, as Storage Management reports them.",
      CHK + r"""$disks = @(Get-PhysicalDisk -ErrorAction Stop | Sort-Object DeviceId)
if (-not $disks.Count) { Chk $null 'Physical disk health' 'no disks reported'; return }
$disks | Select-Object DeviceId, FriendlyName, BusType, MediaType, @{n='GB';e={[math]::Round($_.Size/1GB)}}, HealthStatus, OperationalStatus, FirmwareVersion | Format-Table -AutoSize | Out-String -Width 220
foreach ($d in $disks) {
  $health = if ("$($d.HealthStatus)" -in 'Healthy','Warning','Unhealthy') { "$($d.HealthStatus)" -eq 'Healthy' } else { $null }
  Chk $health "Disk $($d.DeviceId) $($d.FriendlyName)" "health=$($d.HealthStatus); operational=$($d.OperationalStatus -join ', ')"
}
""",
       tags=('diagnostic', 'storage', 'inventory', 'baseline', 'health')),
    D("disk_reliability", "Disk wear and error counters",
      "Temperature, wear (percentage used of rated endurance), power-on hours and read/write "
      "error counts per disk. Not every disk reports every counter; blanks mean 'not "
      "reported', never zero.",
      T("Get-PhysicalDisk | ForEach-Object { $r = $_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; [pscustomobject]@{ Disk=$_.FriendlyName; TempC=$r.Temperature; Wear=$r.Wear; PowerOnHours=$r.PowerOnHours; ReadErrors=$r.ReadErrorsTotal; WriteErrors=$r.WriteErrorsTotal } }"),
      note="some counters are only reported elevated", admin=True,
       tags=('diagnostic', 'storage', 'triage')),
    D("partitions", "Partitions",
      "Every partition on every disk: type (EFI, MSR, recovery, basic), size, drive letter "
      "and offset -- what Disk Management shows, as text. Needs administrator: for a "
      "standard user Get-Partition returns NOTHING, with no error (measured 2026-10-16).",
      need_admin("Get-Partition returns an empty list for a standard user") + T("Get-Partition | Sort-Object DiskNumber, PartitionNumber | Select-Object DiskNumber, PartitionNumber, DriveLetter, Type, @{n='GB';e={[math]::Round($_.Size/1GB,2)}}, IsBoot, IsSystem, IsActive"), admin=True,
       tags=('diagnostic', 'storage', 'inventory', 'baseline')),
    D("disk_partition_style", "Disks: GPT or MBR",
      "Partition style per disk, boot and system flags. Windows 11 upgrades and UEFI boot "
      "need GPT.",
      T("Get-Disk | Sort-Object Number | Select-Object Number, FriendlyName, PartitionStyle, @{n='GB';e={[math]::Round($_.Size/1GB)}}, IsBoot, IsSystem, OperationalStatus"),
       tags=('diagnostic', 'storage', 'inventory', 'baseline')),
    D("bitlocker", "BitLocker status per volume",
      "Encryption state, method, protection on/off and key protector types for each volume.",
      need_admin("BitLocker status is only readable elevated") +
      T("Get-BitLockerVolume | Select-Object MountPoint, VolumeStatus, EncryptionMethod, EncryptionPercentage, ProtectionStatus, @{n='Protectors';e={($_.KeyProtector.KeyProtectorType) -join ', '}}"),
      admin=True,
       tags=('diagnostic', 'storage', 'security')),
    D("trim", "TRIM (delete notifications)",
      "Whether Windows sends TRIM to SSDs. 0 means ENABLED -- the value is a 'disable' flag.",
      "fsutil behavior query DisableDeleteNotify", kind=CMD,
       tags=('diagnostic', 'storage')),
    D("defrag_status", "Optimise-drives schedule and last run",
      "When each volume was last optimised (TRIM for SSDs, defrag for HDDs) and whether the "
      "scheduled task is enabled.",
      r"""Get-ScheduledTask -TaskPath '\Microsoft\Windows\Defrag\' -ErrorAction SilentlyContinue | ForEach-Object { "Scheduled task $($_.TaskName): $($_.State)"; ($_ | Get-ScheduledTaskInfo | Select-Object LastRunTime, NextRunTime | Format-List | Out-String).Trim() }""",
       tags=('diagnostic', 'storage')),
    D("shadow_copies", "Volume shadow copies and restore storage",
      "Shadow storage per volume (how much space restore points may use) and the shadow "
      "copies that exist.",
      need_admin("vssadmin refuses a standard user") +
      r"""vssadmin list shadowstorage
vssadmin list shadows | Select-String 'Shadow Copy Volume|creation time|Original Volume' | ForEach-Object { $_.Line.Trim() }""",
      admin=True,
       tags=('diagnostic', 'storage', 'inventory', 'baseline')),
    D("restore_points", "System restore points",
      "Every restore point with its time and description, and whether System Protection is on.",
      need_admin("Get-ComputerRestorePoint returns nothing for a standard user") +
      T("Get-ComputerRestorePoint | Select-Object SequenceNumber, @{n='Created';e={$_.ConvertToDateTime($_.CreationTime)}}, Description, RestorePointType"),
      admin=True,
       tags=('diagnostic', 'storage', 'inventory', 'baseline')),
    D("large_folders_profile", "Largest folders in your profile",
      "The 15 biggest top-level folders under your user profile, measured -- where the space "
      "went. OneDrive files that are online-only are counted at their full size.",
      r"""Get-ChildItem $env:USERPROFILE -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
  $s = (Get-ChildItem $_.FullName -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum
  [pscustomobject]@{ Folder=$_.Name; GB=[math]::Round($s/1GB,2) } } | Sort-Object GB -Descending | Select-Object -First 15 | Format-Table -AutoSize | Out-String -Width 220""",
      timeout=600,
       tags=('diagnostic', 'storage')),
    D("temp_size", "Temp folders size",
      "Size and file count of your %TEMP% and %SystemRoot%\\Temp -- the first place an admin "
      "looks when a disk fills.",
      r"""foreach ($p in $env:TEMP, "$env:SystemRoot\Temp") { $m = Get-ChildItem $p -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum
  '{0,-40} {1,10:N2} GB  {2,8} files' -f $p, ($m.Sum/1GB), $m.Count }""", timeout=300,
       tags=('diagnostic', 'storage')),
    D("component_store", "Component store (WinSxS) size",
      "What the WinSxS folder really uses, and whether a cleanup is recommended. Uses DISM "
      "/AnalyzeComponentStore -- READ-ONLY, but it holds the servicing lock for ~25 s.",
      need_admin("DISM refuses a standard user") +
      "Dism.exe /Online /Cleanup-Image /AnalyzeComponentStore", admin=True, timeout=600,
       tags=('diagnostic', 'storage')),
    D("onedrive_status", "OneDrive folders and Known Folder redirection",
      "Where Desktop, Documents and Pictures really are (OneDrive Backup redirects them) and "
      "which OneDrive accounts are set up.",
      r"""foreach ($f in 'Desktop','MyDocuments','MyPictures') { '{0,-12} {1}' -f $f, [Environment]::GetFolderPath($f) }
''
Get-ChildItem 'HKCU:\Software\Microsoft\OneDrive\Accounts' -ErrorAction SilentlyContinue | ForEach-Object { $p = Get-ItemProperty $_.PSPath; if ($p.UserFolder) { "Account $($_.PSChildName): $($p.UserEmail)  -> $($p.UserFolder)" } }""",
       tags=('diagnostic', 'storage', 'inventory', 'baseline')),
    # ---- Performance -------------------------------------------------------------------------
    P("top_cpu", "Top processes by CPU",
      "The 20 processes that used the most CPU time since they started, with their memory.",
      T("Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 Name, Id, @{n='CPU(s)';e={[math]::Round($_.CPU,1)}}, @{n='WS(MB)';e={[math]::Round($_.WorkingSet64/1MB)}}, Handles, StartTime"),
       tags=('diagnostic', 'performance', 'triage')),
    P("top_memory", "Top processes by memory",
      "The 20 processes with the largest working set and private memory.",
      T("Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 20 Name, Id, @{n='WS(MB)';e={[math]::Round($_.WorkingSet64/1MB)}}, @{n='Private(MB)';e={[math]::Round($_.PrivateMemorySize64/1MB)}}, Handles"),
       tags=('diagnostic', 'performance', 'triage')),
    P("cpu_now", "CPU usage right now (5-second sample)",
      "Total and per-core CPU use sampled over five seconds, plus the processor queue length "
      "-- a queue above ~2 per core means work is waiting.",
      r"""$c = Get-Counter '\Processor(_Total)\% Processor Time','\System\Processor Queue Length' -SampleInterval 1 -MaxSamples 5 -ErrorAction SilentlyContinue
$c.CounterSamples | Group-Object Path | ForEach-Object { '{0,-55} {1,8:N1}' -f $_.Name, ($_.Group | Measure-Object CookedValue -Average).Average }""",
      timeout=60,
       tags=('diagnostic', 'performance', 'triage')),
    P("memory_now", "Memory pressure",
      "Physical memory in use, commit charge against the commit limit (what decides 'out of "
      "memory', not physical RAM alone), and the page fault rate.",
      r"""$os = Get-CimInstance Win32_OperatingSystem
$c = Get-Counter '\Memory\Committed Bytes','\Memory\Commit Limit','\Memory\Pages/sec','\Memory\Available MBytes' -ErrorAction SilentlyContinue
'Physical: {0:N1} GB used of {1:N1} GB' -f (($os.TotalVisibleMemorySize - $os.FreePhysicalMemory)/1MB), ($os.TotalVisibleMemorySize/1MB)
$c.CounterSamples | ForEach-Object { '{0,-40} {1,14:N0}' -f ($_.Path -replace '^\\\\[^\\]+',''), $_.CookedValue }""",
       tags=('diagnostic', 'performance', 'triage')),
    P("disk_now", "Disk activity (5-second sample)",
      "Read/write throughput, % idle time and average response time per physical disk. "
      "Response times above ~20 ms on an SSD (or 50 ms on an HDD) mean the disk is the "
      "bottleneck.",
      r"""$c = Get-Counter '\PhysicalDisk(*)\Disk Read Bytes/sec','\PhysicalDisk(*)\Disk Write Bytes/sec','\PhysicalDisk(*)\% Idle Time','\PhysicalDisk(*)\Avg. Disk sec/Transfer' -SampleInterval 1 -MaxSamples 5 -ErrorAction SilentlyContinue
$c.CounterSamples | Where-Object InstanceName -ne '_total' | Group-Object Path | ForEach-Object { '{0,-70} {1,14:N3}' -f ($_.Name -replace '^\\\\[^\\]+',''), ($_.Group | Measure-Object CookedValue -Average).Average }""",
      timeout=60,
       tags=('diagnostic', 'performance', 'triage')),
    P("handles_threads", "Processes with the most handles",
      "Handle and thread counts per process. A process whose handle count keeps growing is "
      "leaking -- run this twice a few minutes apart.",
      T("Get-Process | Sort-Object HandleCount -Descending | Select-Object -First 20 Name, Id, HandleCount, @{n='Threads';e={$_.Threads.Count}}, @{n='WS(MB)';e={[math]::Round($_.WorkingSet64/1MB)}}"),
       tags=('diagnostic', 'performance', 'triage')),
    P("startup_impact", "Boot duration and what slowed it",
      "The last boot durations Windows measured (Diagnostics-Performance 100) and the "
      "components it blamed for slow boots (101 app, 102 driver, 103 service, 106 background "
      "optimisation, 109 device) -- grouped by component, with how often and by how much, "
      "named from the event's own fields (the message's first line never names it).",
      r"""$log = 'Microsoft-Windows-Diagnostics-Performance/Operational'
$e = Get-WinEvent -FilterHashtable @{LogName=$log; Id=100} -MaxEvents 10 -ErrorAction SilentlyContinue
if (-not $e) { 'No boot performance records.'; return }
$e | ForEach-Object { $d = @{}; ([xml]$_.ToXml()).Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }
  '{0:yyyy-MM-dd HH:mm}  boot {1,5:N1} s  (main path {2,5:N1} s, post-boot {3,5:N1} s)' -f $_.TimeCreated, ($d.BootTime/1000), ($d.MainPathBootTime/1000), ($d.BootPostBootTime/1000) }
''
'Components that slowed boots (30 days, worst first):'
$kind = @{101='app'; 102='driver'; 103='service'; 106='background'; 109='device'}
$b = Get-WinEvent -FilterHashtable @{LogName=$log; Id=101,102,103,106,109; StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue | ForEach-Object {
  $d = @{}; ([xml]$_.ToXml()).Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }
  [pscustomobject]@{ Kind=$kind[$_.Id]; Name=$(if ($d.FriendlyName) { "$($d.FriendlyName) ($($d.Name))" } else { $d.Name }); Path=$d.Path
    Total=[int]$d.TotalTime; Degradation=[int]$d.DegradationTime; When=$_.TimeCreated } }
if (-not $b) { '[OK]   Windows blamed nothing for a slow boot in 30 days.'; return }
$b | Group-Object Kind, Name | ForEach-Object { $g = $_.Group | Sort-Object Degradation -Descending; $w = $g[0]
  [pscustomobject]@{ Kind=$w.Kind; Name=$w.Name; Times=$_.Count; MaxDelay=$w.Degradation; Last=($_.Group | Sort-Object When -Descending | Select-Object -First 1).When; Path=$w.Path } } |
  Sort-Object MaxDelay -Descending | ForEach-Object { '[WARN] {0,-10} {1,-55} x{2,-3} slowed boot by up to {3,6:N1} s  (last {4:yyyy-MM-dd})  {5}' -f $_.Kind, $_.Name, $_.Times, ($_.MaxDelay/1000), $_.Last, $_.Path }""",
       tags=('diagnostic', 'performance', 'triage', 'health')),
    P("power_plan", "Power plan and processor limits",
      "The active power plan and its processor minimum/maximum state on AC and battery -- a "
      "maximum below 100% caps performance.",
      r"""powercfg /getactivescheme
powercfg /query SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMAX | Select-String 'Current (AC|DC) Power Setting Index' | ForEach-Object { 'Max processor state ' + $_.Line.Trim() }
powercfg /query SCHEME_CURRENT SUB_PROCESSOR PROCTHROTTLEMIN | Select-String 'Current (AC|DC) Power Setting Index' | ForEach-Object { 'Min processor state ' + $_.Line.Trim() }""",
       tags=('diagnostic', 'performance')),
    P("gpu_load", "GPU engine usage (5-second sample)",
      "GPU utilisation per engine (3D, video encode/decode, copy) summed across processes -- "
      "what Task Manager's GPU graph shows.",
      r"""$c = Get-Counter '\GPU Engine(*)\Utilization Percentage' -SampleInterval 1 -MaxSamples 5 -ErrorAction SilentlyContinue
if (-not $c) { 'GPU counters are not available.'; return }
$c.CounterSamples | Group-Object { ($_.InstanceName -split '_engtype_')[-1] } | ForEach-Object { '{0,-20} {1,6:N1} %' -f $_.Name, (($_.Group | Measure-Object CookedValue -Sum).Sum / 5) } | Sort-Object""",
      timeout=60,
       tags=('diagnostic', 'performance')),
    P("network_throughput", "Network throughput (5-second sample)",
      "Bytes sent and received per second per adapter, sampled for five seconds.",
      r"""$c = Get-Counter '\Network Interface(*)\Bytes Received/sec','\Network Interface(*)\Bytes Sent/sec' -SampleInterval 1 -MaxSamples 5 -ErrorAction SilentlyContinue
$c.CounterSamples | Group-Object Path | ForEach-Object { '{0,-90} {1,12:N0} B/s' -f ($_.Name -replace '^\\\\[^\\]+',''), ($_.Group | Measure-Object CookedValue -Average).Average }""",
      timeout=60,
       tags=('diagnostic', 'performance', 'network')),
    P("reliability_index", "Reliability index (stability score)",
      "Windows' own 1-10 stability score over recent days, from the Reliability Monitor data.",
      T("Get-CimInstance Win32_ReliabilityStabilityMetrics -ErrorAction SilentlyContinue | Sort-Object TimeGenerated -Descending | Select-Object -First 14 @{n='Day';e={$_.TimeGenerated}}, @{n='Index';e={[math]::Round($_.SystemStabilityIndex,2)}}"),
      timeout=180,
       tags=('diagnostic', 'performance')),
    P("services_cpu", "Which services share each svchost",
      "Every svchost.exe process with the services inside it and its memory -- the way to "
      "turn 'svchost is using CPU' into a service name.",
      r"""$svc = Get-CimInstance Win32_Service -Filter "State='Running'" | Group-Object ProcessId -AsHashTable -AsString
Get-Process svchost -ErrorAction SilentlyContinue | Sort-Object WorkingSet64 -Descending | ForEach-Object {
  '{0,6}  {1,6:N0} MB  {2}' -f $_.Id, ($_.WorkingSet64/1MB), ((@($svc["$($_.Id)"]) | ForEach-Object { $_.Name }) -join ', ') }""",
       tags=('diagnostic', 'performance', 'triage')),
]

SCRIPTS += _TRIAGE_SCRIPTS

# Embedded in each diagnostic so exported scripts remain standalone. Metadata
# only: never open file contents, follow junctions, or hydrate cloud directories.
SCRIPTS += [
    D('cl2-component-store-evidence', 'Deep cleanup: component store accounting and servicing history',
      'Separates DISM actual/shared/exclusive accounting from logical WinSxS lengths, package states and CBS cleanup evidence.',
      need_admin('online component store analysis and package inventory require elevation') + _DEEP_CLEANUP + r"""
$analysis=@(DcNative "$env:SystemRoot\System32\dism.exe" @('/Online','/English','/Cleanup-Image','/AnalyzeComponentStore'))
$analysis
# DISM accounts for hard links; summing WinSxS lengths cannot do that.
$fields=@{}
foreach ($line in $analysis) {
  if ($line -match '^\s*(Windows Explorer Reported Size of Component Store|Actual Size of Component Store|Shared with Windows|Backups and Disabled Features|Cache and Temporary Data|Date of Last Cleanup|Number of Reclaimable Packages|Component Store Cleanup Recommended)\s*:\s*(.+)$') { $fields[$Matches[1]]=$Matches[2] }
}
foreach ($key in $fields.Keys) { "[INFO] DISM ${key}: $($fields[$key])" }
function DismBytes($text) {
  if ($text -match '^([\d.]+)\s*(bytes|KB|MB|GB|TB)$') {
    $unit=@{bytes=1;KB=1KB;MB=1MB;GB=1GB;TB=1TB}[$Matches[2]]
    return [decimal]::Parse($Matches[1],[Globalization.CultureInfo]::InvariantCulture)*$unit
  }; return $null
}
foreach ($key in $fields.Keys) {
  $numeric=DismBytes $fields[$key]
  if ($null -ne $numeric) { "[INFO] $key converted from rounded DISM units: $(DcSize $numeric)" }
}
$backup=DismBytes $fields['Backups and Disabled Features']; $cache=DismBytes $fields['Cache and Temporary Data']
if ($null -ne $backup -and $null -ne $cache) {
  "[INFO] Exclusive backup/cache accounting upper bound: $(DcSize ($backup+$cache)); rounded DISM values, includes retained features and rollback state, NOT guaranteed reclaimable."
} else { 'NOT AVAILABLE: numeric exclusive-size projection; retain native DISM units above.' }
$recommended=$fields['Component Store Cleanup Recommended']
Chk $(if ($recommended -eq 'Yes') {$false} elseif ($recommended -eq 'No') {$true} else {$null}) 'DISM cleanup recommendation' "$recommended; use supported servicing tools after reviewing rollback requirements"
try {
  $packages=@(Get-WindowsPackage -Online -ErrorAction Stop)
  if (-not $packages.Count) { throw 'package provider returned no rows' }
  DcShow ($packages | Group-Object PackageState | Select-Object Name,Count)
  "[INFO] Superseded packages=$(@($packages | Where-Object PackageState -eq 'Superseded').Count); this is a package count, not a count of unique component files."
} catch { Chk $null 'Package states' $_.Exception.Message }
foreach ($name in 'Temp\PendingDeletes','Temp\PendingRenames','pending.xml') { DcShow (DcMeasure "$env:SystemRoot\WinSxS\$name") }
$evidence=@(DcText "$env:SystemRoot\Logs\CBS\CBS.log" 30000 | Where-Object { $_ -match '(?i)(scavenge|cleanup|reclaim|supersed).*(size|bytes|complete|HRESULT|pending|failed)' })
if ($evidence.Count) { $evidence | Select-Object -Last 35 } else { Chk $null 'CBS cleanup result/pending bytes' 'no matching evidence in current 30000-line tail; rotated CABs not opened; pending directory lengths are not CBS reclaimable bytes' }
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'health', 'triage')),
    D('cl2-ntfs-metadata-overhead', 'Deep cleanup: NTFS metadata versus volume capacity',
      'Correlates NTFS volume/MFT and USN journal configuration with protected metadata lengths and SVI accounting.',
      need_admin('NTFS metadata and USN queries require elevation') + _DEEP_CLEANUP + _DEEP_NTFS + r"""
try { $volumes=@(Get-CimInstance Win32_Volume -Filter "DriveType=3 AND FileSystem='NTFS'" -ErrorAction Stop) }
catch { Chk $null 'NTFS volumes' $_.Exception.Message; return }
foreach ($v in $volumes) {
  $root=if ($v.DriveLetter) { "$($v.DriveLetter)\" } else { $v.DeviceID }
  "== $root; capacity=$(DcSize $v.Capacity); used=$(DcSize ($v.Capacity-$v.FreeSpace))"
  DcNative "$env:SystemRoot\System32\fsutil.exe" @('fsinfo','ntfsinfo',$root)
  DcNative "$env:SystemRoot\System32\fsutil.exe" @('usn','queryjournal',$root)
  try {
    $device=if ($v.DriveLetter) { '\\.\'+$v.DriveLetter } else { $v.DeviceID.TrimEnd('\') }
    $n=[CleanupNtfsV2]::Volume($device)
    $usedClusters=$n[0]-$n[1]; $usedBytes=[decimal]$usedClusters*$n[3]
    "[INFO] Native MFT valid data=$(DcSize $n[4]); cluster bytes=$($n[3]); reserved clusters=$($n[2]); MFT/used ratio=$(if ($usedBytes -gt 0) {[math]::Round(100*$n[4]/$usedBytes,3)})%. MFT valid length is not its allocated extents or total filesystem metadata."
  } catch { Chk $null 'Native MFT accounting' $_.Exception.Message }
  $known=[decimal]0; $readable=0
  foreach ($name in '$MFT','$MFTMirr','$LogFile','$Bitmap','$Secure','$Extend\$UsnJrnl:$J') {
    try { $i=DcItem (Join-Path $root $name); $known+=$i.Length; $readable++; "[INFO] ${name}: $(DcSize $i.Length) (logical metadata stream)" }
    catch { "NOT AVAILABLE: ${root}${name}: $($_.Exception.Message); raw fsutil MFT/USN evidence above may still be available" }
  }
  DcShow (DcMeasure (Join-Path $root 'System Volume Information'))
  $used=[decimal]$v.Capacity-[decimal]$v.FreeSpace
  Chk $null 'Metadata/data ratio' "Readable metadata streams=$readable; lower-bound metadata=$(DcSize $known); percent of used=$(if ($used -gt 0) {[math]::Round(100*$known/$used,3)}). Full ratio unknown where protected streams refuse. USN maximum is a configured ceiling, not allocated bytes; SVI also holds backups."
}
DcNative "$env:SystemRoot\System32\vssadmin.exe" @('list','shadowstorage')
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'inventory')),
    D('cl2-sparse-compressed-allocation', 'Deep cleanup: sparse/compressed allocation efficiency',
      'Ranks per-directory logical versus allocated gaps, compression savings and allocation overhead without reading file contents.',
      need_admin('whole-volume attribute inventory requires access to protected directories') + _DEEP_CLEANUP + _DEEP_ALLOCATION + r"""
$groups=@{}; $largest=[Collections.Generic.List[object]]::new(); $selected=0
try { $roots=@(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction Stop) }
catch { Chk $null 'Volumes' $_.Exception.Message; return }
foreach ($volume in $roots) {
  DcFiles "$($volume.DeviceID)\" | Where-Object { [long]$_.Attributes -band 0xA00 } | ForEach-Object {
    $file=$_
    try {
      $allocated=[CleanupAllocationV2]::Bytes($file.FullName); $gap=[decimal]$file.Length-$allocated; $selected++
      $key=$file.DirectoryName
      if (-not $groups.ContainsKey($key)) { $groups[$key]=[pscustomobject]@{Folder=$key; Files=0; LogicalBytes=[decimal]0; AllocatedBytes=[decimal]0; GapBytes=[decimal]0} }
      $g=$groups[$key]; $g.Files++; $g.LogicalBytes+=$file.Length; $g.AllocatedBytes+=$allocated; $g.GapBytes+=$gap
      $largest.Add([pscustomobject]@{Path=$file.FullName; Attributes=$file.Attributes; LogicalBytes=$file.Length; AllocatedBytes=$allocated; GapBytes=$gap; Gap=(DcSize $gap)})
      if ($largest.Count -gt 200) { $keep=@($largest | Sort-Object {[math]::Abs($_.GapBytes)} -Descending | Select-Object -First 100); $largest.Clear(); foreach ($r in $keep) { $largest.Add($r) } }
    } catch { "NOT AVAILABLE: allocation $($file.FullName): $($_.Exception.Message)" | Out-Host }
  }
}
'== Largest savings by directory'; DcShow ($groups.Values | Sort-Object GapBytes -Descending | Select-Object -First 30 *,@{n='Gap';e={DcSize $_.GapBytes}})
'== Allocation overhead (negative gap)'; DcShow ($groups.Values | Where-Object GapBytes -lt 0 | Sort-Object GapBytes | Select-Object -First 20)
'== Largest absolute file gaps'; DcShow ($largest | Sort-Object {[math]::Abs($_.GapBytes)} -Descending | Select-Object -First 35)
Chk $null 'Allocation interpretation' "$selected sparse/compressed files measured. Positive gaps already save disk; do not mistake logical size for reclaimable space. Hard-linked names are not deduplicated; gaps are not additive across links."
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'performance')),
    D('cl2-vss-retention-analysis', 'Deep cleanup: VSS storage and retention scenarios',
      'Maps shadow-copy providers, sets and source volumes to storage associations; models oldest-copy removal with honest bounds.',
      need_admin('VSS inventory requires elevation') + _DEEP_CLEANUP + r"""
try {
  $copies=@(Get-CimInstance Win32_ShadowCopy -ErrorAction Stop)
  $stores=@(Get-CimInstance Win32_ShadowStorage -ErrorAction Stop)
  $providers=@(Get-CimInstance Win32_ShadowProvider -ErrorAction Stop)
  if (-not $copies.Count) { '[INFO] No shadow copies returned by the provider.' }
  foreach ($store in $stores) {
    $source=$store.Volume.DeviceID; $target=$store.DiffVolume.DeviceID
    "== Source=$source; diff area=$target; used=$(DcSize $store.UsedSpace); allocated=$(DcSize $store.AllocatedSpace); maximum=$(DcSize $store.MaxSpace)"
    $series=@($copies | Where-Object VolumeName -eq $source | Sort-Object InstallDate)
    foreach ($copy in $series) {
      $provider=$providers | Where-Object ID -eq $copy.ProviderID | Select-Object -First 1
      DcShow ([pscustomobject]@{ID=$copy.ID; Set=$copy.SetID; Created=$copy.InstallDate; Provider=$provider.Name; Persistent=$copy.Persistent; ClientAccessible=$copy.ClientAccessible; NoWriters=$copy.NoWriters; Writer='NOT AVAILABLE: writer membership is not persisted in Win32_ShadowCopy'; DeltaBytes=$null})
    }
    foreach ($n in 1,3,5) {
      $remove=[math]::Min($n,$series.Count)
      Chk $null "Oldest $n scenario" "$remove of $($series.Count) source-volume copies; physical reclaim range 0..$(DcSize $store.UsedSpace). Per-copy delta and exact reclaim NOT AVAILABLE: shared copy-on-write blocks, other snapshot contexts and concurrent changes prevent apportioning aggregate used bytes."
    }
    Chk $(if ($store.MaxSpace -gt 0) { $store.UsedSpace/$store.MaxSpace -lt .9 } else { $null }) 'VSS quota occupancy' '90% review threshold; an unlimited max is not a disk-space guarantee'
  }
  '== All snapshots (includes sources with no returned storage association)'; DcShow ($copies | Select-Object ID,SetID,VolumeName,InstallDate,ProviderID)
} catch { Chk $null 'VSS associations' $_.Exception.Message }
DcNative "$env:SystemRoot\System32\vssadmin.exe" @('list','writers')
'[INFO] Current writer state cannot identify which backup requester created an old copy. Provider is not writer. No shadows are removed.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'triage')),
    D('cl2-disk-growth-projection', 'Deep cleanup: seven-day creation-rate disk forecast',
      'Builds daily file-creation cohorts per volume and ranks contributors; estimates free-space runway with explicit sampling limits.',
      need_admin('whole-volume timestamp inventory needs protected-folder visibility') + _DEEP_CLEANUP + r"""
try { $volumes=@(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction Stop) }
catch { Chk $null 'Volume capacity' $_.Exception.Message; return }
foreach ($v in $volumes) {
  $since=(Get-Date).AddDays(-7); $bins=@{}; $contributors=@{}; $bytes=[decimal]0; $before=$dcFailures; $skip=$dcSkipped
  DcFiles "$($v.DeviceID)\" | Where-Object { $_.CreationTime -ge $since -and $_.CreationTime -le (Get-Date) } | ForEach-Object {
    $day=$_.CreationTime.ToString('yyyy-MM-dd'); $bins[$day]+=$_.Length; $bytes+=$_.Length
    $folder=$_.DirectoryName; $contributors[$folder]+=$_.Length
  }
  $rate=$bytes/7; $days=if ($rate -gt 0) { [math]::Round($v.FreeSpace/$rate,1) } else { $null }
  $complete=$before -eq $dcFailures -and $skip -eq $dcSkipped -and -not $dcBounded
  "== $($v.DeviceID); free=$(DcSize $v.FreeSpace); new-file cohort=$(DcSize $bytes); daily gross=$(DcSize $rate); gross-rate runway days=$days; complete=$complete"
  DcShow ($bins.GetEnumerator() | Sort-Object Name | Select-Object @{n='Date';e={$_.Key}},@{n='Bytes';e={$_.Value}},@{n='Size';e={DcSize $_.Value}})
  DcShow ($contributors.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 20 @{n='Folder';e={$_.Key}},@{n='NewLogicalBytes';e={$_.Value}},@{n='Size';e={DcSize $_.Value}})
  Chk $null 'Days until full' "Scenario=$days days IF observed gross rate continues. Not net growth: excludes growth of old files, deletions, USN rollovers and allocation effects; copied/restored files can distort timestamps. Partial coverage can overestimate runway. No historical free-space baseline exists."
}
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'performance', 'triage')),
    D('cl2-driver-store-package-audit', 'Deep cleanup: driver package device-reference audit',
      'Joins DISM driver packages, PnP signed drivers and device presence to rank FileRepository packages without calling unmatched packages safe.',
      need_admin('online driver package inventory requires elevation') + _DEEP_CLEANUP + r"""
try {
  $packages=@(Get-WindowsDriver -Online -All -ErrorAction Stop)
  $signed=@(Get-CimInstance Win32_PnPSignedDriver -ErrorAction Stop)
  $devices=@(Get-PnpDevice -PresentOnly -ErrorAction Stop)
  if (-not $packages.Count -or -not $signed.Count -or -not $devices.Count) { throw 'one or more driver/device inventories empty; orphan decisions unavailable' }
} catch { Chk $null 'Driver cross-reference' $_.Exception.Message; return }
$present=@{}; foreach ($d in $devices) { $present[$d.InstanceId]=$true }
$rows=@(foreach ($p in $packages) {
  $matches=@($signed | Where-Object InfName -eq $p.Driver)
  $active=@($matches | Where-Object { $present.ContainsKey($_.DeviceID) })
  $peers=@($packages | Where-Object { $_.ProviderName -eq $p.ProviderName -and $_.ClassName -eq $p.ClassName -and [IO.Path]::GetFileName($_.OriginalFileName) -eq [IO.Path]::GetFileName($p.OriginalFileName) -and $_.Date -gt $p.Date })
  $state=if ($active.Count) { 'Active device reference' } elseif ($matches.Count) { 'Non-present device reference' } elseif ($peers.Count) { 'Unmatched older-family candidate' } else { 'Unmatched candidate' }
  $folder=Split-Path -Parent $p.OriginalFileName; $m=if ($folder) { DcMeasure $folder } else { $null }
  $installDates=@($matches | Where-Object { $null -ne $_.InstallDate } | Select-Object -ExpandProperty InstallDate -Unique)
  [pscustomobject]@{PublishedInf=$p.Driver; OriginalInf=$p.OriginalFileName; Provider=$p.ProviderName; Version=$p.Version; DriverDate=$p.Date; DeviceInstallDates=if ($installDates.Count) {$installDates -join '; '} else {'NOT AVAILABLE from signed-driver provider'}; BootCritical=$p.BootCritical; Inbox=$p.Inbox; State=$state; PresentMatches=$active.Count; AllMatches=$matches.Count; NewerFamily=$peers.Count; Bytes=$m.Bytes; Size=$m.Size; Complete=$m.Complete}
})
DcShow ($rows | Sort-Object Bytes -Descending)
Chk $null 'Orphan confidence' 'No matching device means candidate only: staged upgrade, extension, filter, software and non-PnP packages may be required. Newer-family match is heuristic, not proven supersedence. DriverDate is vendor date; device install dates are shown only if reported, package first-staging date remains unknown. Shared repository folders may repeat across INF rows.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'hardware', 'inventory')),
    D('cl2-search-index-analysis', 'Deep cleanup: Search index scope and crawl evidence',
      'Correlates index database footprint, catalog status/item count, crawl rules and OneDrive attributes without hydrating files.',
      need_admin('Search database metadata and machine scope require elevation') + _DEEP_CLEANUP + r"""
DcShow (DcMeasure "$env:ProgramData\Microsoft\Search\Data\Applications\Windows")
DcFiles "$env:ProgramData\Microsoft\Search\Data\Applications\Windows" | Where-Object Extension -in '.edb','.db' | ForEach-Object { "[INFO] Index database $($_.FullName): $(DcSize $_.Length)" }
try {
  # SDK source: microsoft/win32metadata, RecompiledIdlHeaders/um/SearchAPI.h.
  # SearchAPI.h interfaces are IUnknown, not late-bound IDispatch objects.
  # Unused vtable slots are deliberately not invoked; only three read methods
  # on the catalog and GetCatalog on the manager are exposed to this script.
  if (-not ('CleanupSearchV2' -as [type])) {
    Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class CleanupSearchV2 {
  [ComImport,Guid("AB310581-AC80-11D1-8DF3-00C04FB6EF69"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface Manager {
    void Slot0(); void Slot1(); void Slot2(); void Slot3();
    void Slot4(); void Slot5(); void Slot6();
    void GetCatalog([MarshalAs(UnmanagedType.LPWStr)] string name,out Catalog catalog);
  }
  [ComImport,Guid("AB310581-AC80-11D1-8DF3-00C04FB6EF50"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface Catalog {
    void Slot0(); void Slot1(); void Slot2();
    void GetCatalogStatus(out int status,out int paused);
    void Slot4(); void Slot5(); void Slot6(); void Slot7();
    void Slot8(); void Slot9(); void Slot10(); void Slot11();
    void NumberOfItems(out int count);
    void NumberOfItemsToIndex(out int incremental,out int notifications,out int highPriority);
  }
  public static int[] Read() {
    Manager manager=null; Catalog catalog=null;
    try {
      manager=(Manager)Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("7D096C5F-AC08-4F1F-BEB7-5C22C517CE39"),true));
      manager.GetCatalog("SystemIndex",out catalog);
      int status,paused,count,incremental,notifications,high;
      catalog.GetCatalogStatus(out status,out paused); catalog.NumberOfItems(out count);
      catalog.NumberOfItemsToIndex(out incremental,out notifications,out high);
      return new int[]{status,paused,count,incremental,notifications,high};
    } finally {
      if(catalog!=null) Marshal.ReleaseComObject(catalog);
      if(manager!=null) Marshal.ReleaseComObject(manager);
    }
  }
}
'@
  }
  $state=[CleanupSearchV2]::Read()
  "[INFO] Catalog status code=$($state[0]); paused reason=$($state[1]); indexed items=$($state[2]); incremental remaining=$($state[3]); notification queue=$($state[4]); high priority=$($state[5]); status 0=idle, 1=paused, 2=recovering, 3=full crawl, 4=incremental crawl, 5=processing notifications, 6=shutting down."
} catch { Chk $null 'Search catalog COM' $_.Exception.Message }
foreach ($name in 'DefaultRules','WorkingSetRules','SearchRoots') {
  $key="HKLM:\SOFTWARE\Microsoft\Windows Search\CrawlScopeManager\Windows\SystemIndex\$name"
  try {
    "== Search scope registry evidence: $key"
    $rules=@(Get-ChildItem -LiteralPath $key -ErrorAction Stop)
    if (-not $rules.Count) { Chk $null 'Scope rules' 'no rules returned; not proof of an empty index scope' }
    foreach ($rule in $rules) { Get-ItemProperty -LiteralPath $rule.PSPath -ErrorAction Stop | Select-Object PSChildName,URL,Include,Default,Policy,Suppress | Format-List | Out-String -Width 240 }
  } catch { Chk $null 'Search scope registry' $_.Exception.Message }
}
try { DcShow (Get-CimInstance Win32_PerfFormattedData_WSearchIdxPi_SearchIndexer -ErrorAction Stop | Select-Object *) }
catch { Chk $null 'Crawl progress counters' $_.Exception.Message }
foreach ($profile in @(DcProfiles)) {
  foreach ($dir in @(DcChildren $profile.LocalPath | Where-Object Name -like 'OneDrive*')) {
    "[INFO] Cloud root=$($dir.FullName); attributes=$($dir.Attributes); NOT traversed. Compare URL against included/excluded scope rules; placeholder content indexing cannot be inferred from scope alone."
  }
}
Chk $null 'Search cleanup' 'Index database size alone is not evidence of corruption. Rebuild can temporarily increase disk use; no rebuild or scope change performed.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'performance')),
    D('cl2-hibernation-pagefile-analysis', 'Deep cleanup: hibernation, paging and swap overhead',
      'Compares RAM, hibernation configuration, physical system-file lengths and live/configured page files, including other volumes.',
      need_admin('protected root files and system memory configuration need elevation') + _DEEP_CLEANUP + r"""
try {
  $ram=[decimal](Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory
  "[INFO] RAM=$(DcSize $ram); illustrative hibernation range 40%-100%=$(DcSize ($ram*.4)) .. $(DcSize $ram)"
  $auto=(Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).AutomaticManagedPagefile
  $live=@(Get-CimInstance Win32_PageFileUsage -ErrorAction Stop); $config=@(Get-CimInstance Win32_PageFileSetting -ErrorAction Stop)
  DcShow ($live | Select-Object Name,@{n='AllocatedBytes';e={[long]$_.AllocatedBaseSize*1MB}},@{n='Allocated';e={DcSize ([long]$_.AllocatedBaseSize*1MB)}},CurrentUsage,PeakUsage,TempPageFile)
  DcShow ($config | Select-Object Name,InitialSize,MaximumSize)
  "[INFO] AutomaticManagedPagefile=$auto; usage/initial/maximum columns are MiB; a missing setting can mean system-managed, not disabled."
  $total=[decimal]0
  foreach ($v in @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -ErrorAction Stop)) {
    foreach ($name in 'hiberfil.sys','pagefile.sys','swapfile.sys') {
      try {
        $i=DcItem "$($v.DeviceID)\$name"; $total+=$i.Length
        "[INFO] $($i.FullName): $(DcSize $i.Length); percent RAM=$([math]::Round(100*$i.Length/$ram,1))"
      } catch { "NOT AVAILABLE: $($v.DeviceID)\${name}: $($_.Exception.Message)" }
    }
  }
  "[INFO] Readable system-file logical overhead=$(DcSize $total) (lower bound if any read refused)."
} catch { Chk $null 'Memory/system files' $_.Exception.Message }
try {
  Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' -ErrorAction Stop | Select-Object HibernateEnabled,HiberFileType,HiberFileSizePercent | Format-List | Out-String -Width 220
} catch { Chk $null 'Hibernation configuration' $_.Exception.Message }
DcNative "$env:SystemRoot\System32\powercfg.exe" @('/a')
Chk $null 'Paging/hibernation reclaim' 'These are reserved operational files, not disposable caches; crash dumps, commit limit, Fast Startup and standby support constrain any future change.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'performance')),
    D('cl2-font-installation-sprawl', 'Deep cleanup: font registrations, duplicates and provenance',
      'Maps machine/loaded-user font registrations to font files, names and version-resource vendor evidence without hashing cloud files.',
      need_admin('all profile fonts and loaded-user registrations require elevation') + _DEEP_CLEANUP + r"""
$locations=@("$env:SystemRoot\Fonts"); $registrations=@{}
$keys=@('HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')
foreach ($p in @(DcProfiles)) { $locations+=Join-Path $p.LocalPath 'AppData\Local\Microsoft\Windows\Fonts'; $keys+="Registry::HKEY_USERS\$($p.SID)\Software\Microsoft\Windows NT\CurrentVersion\Fonts" }
foreach ($key in $keys) {
  try {
    $values=Get-ItemProperty -LiteralPath $key -ErrorAction Stop
    foreach ($v in $values.PSObject.Properties | Where-Object Name -notlike 'PS*') {
      $leaf=[IO.Path]::GetFileName([string]$v.Value)
      $registrations[$leaf]+=@("$($v.Name) [$key]")
    }
  } catch { "NOT AVAILABLE: font registration ${key}: $($_.Exception.Message); unloaded user hives are not mounted" }
}
$rows=@(foreach ($location in $locations) {
  DcFiles $location | Where-Object Extension -in '.ttf','.otf','.ttc','.fon' | ForEach-Object {
    $f=$_; $vendor='NOT AVAILABLE'; $version=$null
    try { $safe=DcItem $f.FullName; $info=[Diagnostics.FileVersionInfo]::GetVersionInfo($safe.FullName); if ($info.CompanyName) { $vendor=$info.CompanyName }; $version=$info.FileVersion }
    catch { "NOT AVAILABLE: font provenance $($f.FullName): $($_.Exception.Message)" | Out-Host }
    [pscustomobject]@{Name=$f.Name; Path=$f.FullName; RegisteredNames=($registrations[$f.Name] -join '; '); Bytes=$f.Length; Size=(DcSize $f.Length); Company=$vendor; Version=$version}
  }
})
'== Duplicate filenames across locations (not proven identical faces)'; DcShow ($rows | Group-Object Name | Where-Object Count -gt 1 | ForEach-Object { $_.Group })
'== Non-Microsoft or unknown company resource'; DcShow ($rows | Where-Object Company -notmatch '^Microsoft' | Sort-Object Bytes -Descending)
"[INFO] Total readable fonts=$($rows.Count); logical bytes=$(DcSize (($rows | Measure-Object Bytes -Sum).Sum))"
Chk $null 'Font duplicates' 'Filename/registration matches are leads, not byte equality or internal font-family equality. Many valid fonts lack Windows company resources; unknown does not mean third-party or unsafe.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'inventory')),
    D('cl2-group-policy-cache-audit', 'Deep cleanup: Group Policy cache and template freshness',
      'Ranks local/domain policy caches, Registry.pol and GPT.INI ages and compares loaded policy history without contacting domain controllers.',
      need_admin('machine and other-user policy caches require elevation') + _DEEP_CLEANUP + r"""
$roots=@("$env:SystemRoot\System32\GroupPolicy","$env:SystemRoot\System32\GroupPolicyUsers","$env:SystemRoot\PolicyDefinitions","$env:ProgramData\Microsoft\Group Policy\History")
foreach ($p in @(DcProfiles)) { $roots+=Join-Path $p.LocalPath 'AppData\Local\GroupPolicy\DataStore' }
foreach ($root in $roots) {
  "== $root"; DcShow (DcMeasure $root)
  foreach ($dir in @(DcChildren $root | Where-Object PSIsContainer)) { DcShow (DcMeasure $dir.FullName) }
  DcFiles $root | Where-Object { $_.Name -in 'Registry.pol','GPT.INI' -or $_.Extension -in '.adm','.admx' } | ForEach-Object {
    $f=$_; $age=[math]::Floor(((Get-Date)-$f.LastWriteTime).TotalDays)
    "[INFO] $($f.FullName): $(DcSize $f.Length); age=$age days"
    if ($f.Name -eq 'GPT.INI') {
      foreach ($line in @(DcText $f.FullName 30)) {
        if ($line -match '^\s*Version\s*=\s*(\d+)') {
          $version=[uint32]$Matches[1]
          "[INFO] GPT version=$version; user revision=$($version -shr 16); machine revision=$($version -band 65535); path=$($f.FullName)"
        } elseif ($line -match '^\s*(displayName|gPCMachineExtensionNames|gPCUserExtensionNames)=') { $line }
      }
    }
  }
}
foreach ($root in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Group Policy\History','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Group Policy\State\Machine') {
  try {
    Get-ChildItem -LiteralPath $root -Recurse -ErrorAction Stop | ForEach-Object {
      Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction Stop | Select-Object DisplayName,GPOName,DSPath,FileSysPath,Version,VersionUser,VersionMachine
    } | Format-List | Out-String -Width 240
  } catch { Chk $null $root $_.Exception.Message }
}
Chk $null 'GPO freshness' 'Decode GPT version as user=(Version >>16), computer=(Version &65535). File age alone does not prove stale policy; compare GPO GUID/version with history. SYSVOL is not contacted; central-store freshness and unlinked/orphan status unknown.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'inventory')),
    D('cl2-defender-storage-evidence', 'Deep cleanup: Defender definitions, history and quarantine',
      'Correlates signature age/update events with protected definitions, scan history, support and quarantine footprints.',
      need_admin('Defender protected metadata and machine history require elevation') + _DEEP_CLEANUP + r"""
try {
  $status=Get-MpComputerStatus -ErrorAction Stop
  if ($null -eq $status.AntivirusSignatureLastUpdated) { throw 'signature timestamp unavailable' }
  $age=((Get-Date)-$status.AntivirusSignatureLastUpdated).TotalDays
  $status | Select-Object AMRunningMode,AntivirusEnabled,AntivirusSignatureVersion,AntivirusSignatureLastUpdated,AntivirusSignatureAge,QuickScanEndTime,FullScanEndTime | Format-List | Out-String -Width 230
  Chk ($age -lt 3) 'Definition freshness' "$([math]::Round($age,2)) days; review threshold=3 days, managed update schedules may differ"
} catch { Chk $null 'Defender status' $_.Exception.Message }
foreach ($name in 'Definition Updates','Scans\History','Scans\mpenginedb.db','Quarantine','Support','Platform') {
  $root="$env:ProgramData\Microsoft\Windows Defender\$name"; DcShow (DcMeasure $root)
  if ($name -eq 'Definition Updates') { foreach ($dir in @(DcChildren $root | Where-Object PSIsContainer)) { DcShow (DcMeasure $dir.FullName) } }
}
foreach ($root in @("$env:SystemRoot\Temp","$env:SystemRoot\System32")) {
  DcChildren $root | Where-Object Name -like 'MpSigStub*' | ForEach-Object { DcShow (DcMeasure $_.FullName) }
}
try {
  Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Windows Defender/Operational';Id=2000,2001,2003,2004;StartTime=(Get-Date).AddDays(-30)} -MaxEvents 30 -ErrorAction Stop | Select-Object TimeCreated,Id,Message | Format-List | Out-String -Width 240
} catch { Chk $null 'Definition update history' $_.Exception.Message }
Chk $null 'Defender storage' 'Tamper-protected paths may refuse even administrators. Quarantine is security evidence, not free cache; refusal never means empty. Multiple definition/platform versions can be rollback state.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'security', 'health')),
    D('cl2-softwaredistribution-state-map', 'Deep cleanup: SoftwareDistribution and update transaction state',
      'Separates update payload/database/event artifacts and correlates their ages with AU state, services and reboot/pending markers.',
      need_admin('update transaction state and protected download trees need elevation') + _DEEP_CLEANUP + r"""
$base="$env:SystemRoot\SoftwareDistribution"
$rows=@(foreach ($part in 'Download','DataStore','ReportingEvents.log','EventCache','PostRebootEventCache','SLS','ScanFile','SelfUpdate') { DcMeasure (Join-Path $base $part) })
DcShow ($rows | Sort-Object Bytes -Descending)
DcFiles "$base\DataStore" | Where-Object { $_.Extension -in '.edb','.log','.jrs','.chk' } | ForEach-Object { "[INFO] ESE artifact $($_.FullName): $(DcSize $_.Length); modified=$($_.LastWriteTime)" }
try { DcShow (Get-Service -Name wuauserv,bits,cryptsvc,TrustedInstaller -ErrorAction Stop | Select-Object Name,Status,StartType) }
catch { Chk $null 'Servicing services' $_.Exception.Message }
foreach ($key in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') {
  try { Get-ItemProperty -LiteralPath $key -ErrorAction Stop | Select-Object PSPath,AUState,LastSuccessTime,LastError,NextDetectionTime | Format-List | Out-String -Width 220 }
  catch { "NOT AVAILABLE: ${key}: $($_.Exception.Message); an absent optional marker differs from access denied" }
}
foreach ($path in @("$env:SystemRoot\WinSxS\pending.xml","$env:SystemRoot\WinSxS\reboot.xml")) { DcShow (DcMeasure $path) }
DcText "$base\ReportingEvents.log" 100 | Select-Object -Last 15
Chk $null 'Update cleanup readiness' 'Database/payload bytes are not proven obsolete; active services and pending markers require transaction review. No service stops, database opens, resets or deletion performed.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'triage')),
    D('cl2-etl-telemetry-artifact-map', 'Deep cleanup: ETL owners and telemetry artifact map',
      'Ranks ETL files, matches active ETW session output paths, and maps DiagTrack/SQM/compatibility artifact footprints.',
      need_admin('ETW sessions and machine telemetry trees need elevation') + _DEEP_CLEANUP + r"""
$sessions=@(DcNative "$env:SystemRoot\System32\logman.exe" @('query','-ets'))
$sessions; $details=@()
# logman emits name, type and status columns; names can contain spaces.
foreach ($line in $sessions) {
  if ($line -match '^(.+?)\s{2,}(?:Trace|Counter)\s{2,}') {
    $name=$Matches[1].Trim(); $d=@(DcNative "$env:SystemRoot\System32\logman.exe" @('query',$name,'-ets'))
    $details+=@{Name=$name; Text=($d -join "`n")}
  }
}
$etl=@(foreach ($root in @("$env:SystemRoot\Logs","$env:SystemRoot\System32\LogFiles",$env:ProgramData)) {
  DcFiles $root | Where-Object Extension -eq '.etl' | ForEach-Object {
    $f=$_; $owners=@($details | Where-Object { $_.Text.IndexOf($f.FullName,[StringComparison]::OrdinalIgnoreCase) -ge 0 } | ForEach-Object { $_.Name })
    [pscustomobject]@{Path=$f.FullName; Bytes=$f.Length; Size=(DcSize $f.Length); Modified=$f.LastWriteTime; AgeDays=[math]::Floor(((Get-Date)-$f.LastWriteTime).TotalDays); Session=if ($owners.Count) {$owners -join '; '} else {'NOT AVAILABLE: no exact active-session output-path match'}}
  }
})
DcShow ($etl | Sort-Object Bytes -Descending)
foreach ($root in @("$env:ProgramData\Microsoft\Diagnosis","$env:ProgramData\Microsoft\Diagnosis\ETLLogs","$env:ProgramData\Microsoft\SQM","$env:ProgramData\Microsoft\OneSettings","$env:ProgramData\Microsoft\Windows\OneSettings","$env:SystemRoot\System32\sru","$env:SystemRoot\AppCompat","$env:SystemRoot\Logs\CompatTel","$env:SystemRoot\debug\mrt.log")) { DcShow (DcMeasure $root) }
foreach ($p in @(DcProfiles)) {
  foreach ($relative in 'AppData\Local\Microsoft\Windows\Diagnosis','AppData\Local\Microsoft\Windows\Caches','AppData\Local\Microsoft\OneSettings','AppData\Local\Microsoft\SQM') { DcShow (DcMeasure (Join-Path $p.LocalPath $relative)) }
}
Chk $null 'ETL ownership' 'Only exact active output paths establish an association. Rotated/relative paths and localized logman columns may prevent matching. No match is not proof of an orphan. Overlapping telemetry roots must not be summed.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'triage', 'inventory')),
    D('cl2-error-collection-full-map', 'Deep cleanup: system and per-user error evidence map',
      'Maps all WER queue/archive/temp/pending states and dump locations, including configured LocalDumps destinations and retention ages.',
      need_admin('system and other-user crash evidence is protected') + _DEEP_CLEANUP + r"""
$roots=@("$env:ProgramData\Microsoft\Windows\WER"); $dumps=@("$env:SystemRoot\Minidump","$env:SystemRoot\LiveKernelReports","$env:SystemRoot\MEMORY.DMP","$env:ProgramData\Microsoft\Windows\WER\LocalDumps")
foreach ($p in @(DcProfiles)) {
  $roots+=Join-Path $p.LocalPath 'AppData\Local\Microsoft\Windows\WER'
  $dumps+=Join-Path $p.LocalPath 'AppData\Local\CrashDumps'
  $dumps+=Join-Path $p.LocalPath 'AppData\Local\Microsoft\Windows\WER\LocalDumps'
}
$rows=@(foreach ($root in $roots) { foreach ($name in 'ReportArchive','ReportQueue','Temp','Pending','LocalDumps','FullDumps') { DcMeasure (Join-Path $root $name) } }; foreach ($root in $dumps | Select-Object -Unique) { DcMeasure $root })
DcShow ($rows | Sort-Object Bytes -Descending)
foreach ($key in 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\Windows Error Reporting\LocalDumps') {
  try {
    $paths=@($key)+@(Get-ChildItem -LiteralPath $key -ErrorAction Stop | ForEach-Object PSPath)
    foreach ($path in $paths) {
      $v=Get-ItemProperty -LiteralPath $path -ErrorAction Stop
      $v | Select-Object PSPath,DumpFolder,DumpCount,DumpType | Format-List | Out-String -Width 240
      if ($v.DumpFolder) {
        if ($v.DumpFolder -match '%(LOCALAPPDATA|USERPROFILE)%') { "NOT AVAILABLE: user-dependent dump path $($v.DumpFolder); must resolve in crashing-user context, not administrator context" }
        else { DcShow (DcMeasure ([Environment]::ExpandEnvironmentVariables($v.DumpFolder))) }
      }
    }
  } catch { Chk $null 'Custom LocalDumps registration' $_.Exception.Message }
}
try { Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\CrashControl' -ErrorAction Stop | Select-Object DumpFile,MinidumpDir,DedicatedDumpFile,CrashDumpEnabled,AlwaysKeepMemoryDump | Format-List | Out-String -Width 220 }
catch { Chk $null 'System dump configuration' $_.Exception.Message }
Chk $null 'Crash evidence retention' 'OldestWrite is oldest retained artifact, not crash time. Queued/pending reports and full dumps may be required for an open incident. Custom paths can overlap defaults; no additive reclaim estimate.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'triage')),
    D('cl2-setup-upgrade-artifact-map', 'Deep cleanup: setup, rollback and migration artifacts',
      'Maps hidden upgrade staging and recovery trees, then correlates setup/rollback timeline with retained logs and OS install date.',
      need_admin('hidden setup, rollback and migration trees need elevation') + _DEEP_CLEANUP + r"""
$roots=@("$env:SystemRoot\Panther","$env:SystemRoot\System32\Sysprep\Panther","$env:SystemRoot\Logs\MoSetup","$env:SystemRoot\Logs\Setup","$env:SystemRoot\System32\oobe\Panther")
foreach ($name in '$GetCurrent','$SysReset','$WINDOWS.~BT','$WINDOWS.~WS','Windows.old','ESD','Recovery\Logs','Config.Msi') { $roots+=Join-Path "$env:SystemDrive\" $name }
$rows=@(foreach ($root in $roots) { DcMeasure $root })
DcShow ($rows | Sort-Object Bytes -Descending)
foreach ($path in @("$env:SystemRoot\Panther\setupact.log","$env:SystemRoot\Panther\setuperr.log",(Join-Path "$env:SystemDrive\" '$WINDOWS.~BT\Sources\Rollback\setupact.log'),"$env:SystemRoot\Logs\MoSetup\BlueBox.log")) {
  "== Timeline evidence: $path"
  $lines=@(DcText $path 15000 | Where-Object { $_ -match '(?i)(rollback|migration|result|error|completed|finalize)' })
  if ($lines.Count) { $lines | Select-Object -Last 20 } else { Chk $null 'Retained setup timeline' 'no matching lines in bounded tail' }
}
try { Get-CimInstance Win32_OperatingSystem -ErrorAction Stop | Select-Object Caption,Version,BuildNumber,InstallDate,LastBootUpTime | Format-List | Out-String -Width 220 }
catch { Chk $null 'OS install timeline' $_.Exception.Message }
DcNative "$env:SystemRoot\System32\dism.exe" @('/Online','/English','/Get-OSUninstallWindow')
Chk $null 'Setup residue' 'A retained uninstall window, rollback tree or pending upgrade can make large artifacts essential. Log ages and install date are correlation evidence, not proof that removal is safe.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'triage')),
    D('cl2-application-log-growth', 'Deep cleanup: large application log discovery by owner folder',
      'Finds logs above 10 MiB across ProgramData and every local profile, ranks application roots and separates recent versus old bytes.',
      need_admin('other-user AppData and protected application logs require elevation') + _DEEP_CLEANUP + r"""
$roots=@($env:ProgramData); foreach ($p in @(DcProfiles)) { $roots+=Join-Path $p.LocalPath 'AppData\Local' }
$groups=@{}; $largest=[Collections.Generic.List[object]]::new()
foreach ($root in $roots) {
  DcFiles $root | Where-Object { $_.Extension -eq '.log' -and $_.Length -gt 10MB } | ForEach-Object {
    $file=$_; $relative=$file.FullName.Substring($root.TrimEnd('\').Length).TrimStart('\'); $parts=$relative -split '\\'
    $app=if ($parts.Count -gt 2 -and $parts[0] -eq 'Microsoft') { $parts[0]+'\'+$parts[1] } else { $parts[0] }
    $key="$root\$app"; $days=((Get-Date)-$file.LastWriteTime).TotalDays
    if (-not $groups.ContainsKey($key)) { $groups[$key]=[pscustomobject]@{ApplicationRoot=$key; Files=0; Bytes=[decimal]0; Older30dBytes=[decimal]0; Recent7dBytes=[decimal]0} }
    $g=$groups[$key]; $g.Files++; $g.Bytes+=$file.Length
    if ($days -gt 30) { $g.Older30dBytes+=$file.Length }; if ($days -le 7) { $g.Recent7dBytes+=$file.Length }
    $largest.Add([pscustomobject]@{Path=$file.FullName; Bytes=$file.Length; Size=(DcSize $file.Length); Modified=$file.LastWriteTime; AgeDays=[math]::Floor($days)})
    if ($largest.Count -gt 200) { $keep=@($largest | Sort-Object Bytes -Descending | Select-Object -First 100); $largest.Clear(); foreach ($r in $keep) { $largest.Add($r) } }
  }
}
DcShow ($groups.Values | Sort-Object Bytes -Descending | Select-Object *,@{n='Size';e={DcSize $_.Bytes}})
'== Largest individual log files'; DcShow ($largest | Sort-Object Bytes -Descending | Select-Object -First 50)
Chk $null 'Application attribution' 'Owner is inferred from path, not process ownership; recent/old bytes are current lengths classified by modification age, not measured growth. Logs <=10 MiB and rotated non-.log files excluded. Preserve incident/audit evidence.'
DcEnd
""", admin=True, timeout=300, tags=('diagnostic', 'storage', 'triage')),
]

_CLEANUP_SCAN = CHK + r"""
function CleanupFailure($record) {
  if ($record.CategoryInfo.Category -eq 'ObjectNotFound') { 'not found' }
  elseif ($record.CategoryInfo.Category -eq 'PermissionDenied' -or $record.Exception -is [UnauthorizedAccessException]) { 'access denied' }
  else { 'read failed' }
}
function CleanupChildren($path) {
  try {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer) { throw 'Expected a directory' }
    if ([long]$item.Attributes -band 0x441400) {
      "NOT AVAILABLE: $path -- reparse/cloud directory skipped; discovery incomplete" | Out-Host; return
    }
    Get-ChildItem -LiteralPath $path -Force -ErrorAction Stop
  } catch { "NOT AVAILABLE: $path -- $(CleanupFailure $_): $($_.Exception.Message)" | Out-Host }
}
function MeasureCleanup($path, $filter = '*') {
  $r = [pscustomobject]@{Path=$path; Bytes=[long]0; MB=0; GB=0; Files=0; OldestWrite=$null;
    OldestAgeDays=$null; Skipped=0; Failures=0; Status='Complete'; Details=[Collections.Generic.List[string]]::new()}
  $pending = [Collections.Generic.Stack[string]]::new(); $pending.Push($path)
  while ($pending.Count) {
    $current = $pending.Pop()
    try {
      $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
      if ($item.PSIsContainer) {
        if ([long]$item.Attributes -band 0x441400) {
          $r.Skipped++; $r.Details.Add("${current}: reparse/cloud directory skipped"); continue
        }
        foreach ($child in @(Get-ChildItem -LiteralPath $current -Force -ErrorAction Stop)) {
          if ($child.PSIsContainer) { $pending.Push($child.FullName) }
          elseif ($child.Name -like $filter) {
            # Reparse files can be placeholders or links. Exclude them rather
            # than accidentally count link targets as recoverable disk space.
            if ([long]$child.Attributes -band 0x441400) { $r.Skipped++; continue }
            $r.Files++; $r.Bytes += $child.Length
            if ($null -eq $r.OldestWrite -or $child.LastWriteTime -lt $r.OldestWrite) { $r.OldestWrite = $child.LastWriteTime }
          }
        }
      } elseif ($item.Name -like $filter) {
        if ([long]$item.Attributes -band 0x441400) { $r.Skipped++ }
        else { $r.Files++; $r.Bytes += $item.Length; $r.OldestWrite = $item.LastWriteTime }
      }
    } catch {
      $r.Failures++; $r.Details.Add("${current}: $(CleanupFailure $_): $($_.Exception.Message)")
      if ($current -eq $path) { $r.Status = CleanupFailure $_ }
    }
  }
  if ($r.Status -eq 'Complete' -and ($r.Failures -or $r.Skipped)) { $r.Status = 'Partial' }
  $r.MB = [math]::Round($r.Bytes/1MB,2); $r.GB = [math]::Round($r.Bytes/1GB,3)
  if ($null -ne $r.OldestWrite) { $r.OldestAgeDays = [math]::Floor(((Get-Date)-$r.OldestWrite).TotalDays) }
  $r
}
function ShowCleanup($r, $limitGB = 5) {
  if ($r.Status -in 'not found','access denied','read failed') {
    "NOT AVAILABLE: $($r.Path) -- $($r.Status); size/count/age unknown"
  } else {
    $r | Select-Object Path,Bytes,MB,GB,Files,OldestWrite,OldestAgeDays,Status,Skipped,Failures | Format-List | Out-String -Width 240
  }
  foreach ($detail in $r.Details) { "NOT AVAILABLE: $detail" }
  $ok = if ($r.Status -eq 'Complete') { $r.Bytes -lt ($limitGB*1GB) } else { $null }
  Chk $ok $r.Path "$($r.Status); triage threshold $limitGB GB; partial bytes are a lower bound, never a clean verdict"
}
function ReportCleanup($path, $filter = '*', $limitGB = 5) { ShowCleanup (MeasureCleanup $path $filter) $limitGB }
'[INFO] Metadata only; cloud placeholders and reparse points skipped. Logical file sizes are not allocated or reclaimable bytes; hard links may be counted more than once. Ages use last write time. Thresholds are triage hints, not removal recommendations.'
"""

# Read registrations directly: Win32_Product can trigger MSI consistency repairs.
_CLEANUP_MSI = r"""
function CleanupMsiReferences {
  $refs = @{}; $complete = $true
  foreach ($root in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Installer\UserData',
                    'HKCU:\Software\Microsoft\Installer') {
    $pending = [Collections.Generic.Stack[string]]::new(); $pending.Push($root)
    while ($pending.Count) {
      $key = $pending.Pop()
      try {
        $p = Get-ItemProperty -LiteralPath $key -ErrorAction Stop
        if ($p.LocalPackage) { $refs[[Environment]::ExpandEnvironmentVariables([string]$p.LocalPackage)] = $key }
        foreach ($child in @(Get-ChildItem -LiteralPath $key -ErrorAction Stop)) { $pending.Push($child.PSPath) }
      } catch {
        if ($key -eq $root -and $_.CategoryInfo.Category -eq 'ObjectNotFound') {
          "[INFO] Registration root not found: $root" | Out-Host
        } else { $complete = $false; "NOT AVAILABLE: MSI registration ${key}: $(CleanupFailure $_): $($_.Exception.Message)" | Out-Host }
      }
    }
  }
  [pscustomobject]@{References=$refs; Complete=$complete}
}
"""

SCRIPTS += [
    D('cl-temp-breakdown', 'Cleanup triage: temp folder breakdown',
      'Temp folder bytes, file counts and oldest write age; duplicate paths are measured once.',
      _CLEANUP_SCAN + r"""@($env:TEMP, "$env:SystemRoot\Temp", "$env:LOCALAPPDATA\Temp") | Select-Object -Unique | ForEach-Object { ReportCleanup $_ }
'[INFO] TEMP and LOCALAPPDATA\\Temp often identify the same folder.'""",
      admin=True, note='Windows Temp may be only partly readable', timeout=600,
      tags=('diagnostic', 'storage', 'triage')),
    D('cl-browser-cache-sizes', 'Cleanup triage: browser caches',
      'Chrome, Edge, Brave and Firefox cache totals for the current user, across profiles.',
      _CLEANUP_SCAN + r"""foreach ($browser in @(
  @('Chrome', "$env:LOCALAPPDATA\Google\Chrome\User Data"),
  @('Edge', "$env:LOCALAPPDATA\Microsoft\Edge\User Data"),
  @('Brave', "$env:LOCALAPPDATA\BraveSoftware\Brave-Browser\User Data"),
  @('Firefox', "$env:LOCALAPPDATA\Mozilla\Firefox\Profiles"))) {
  "== $($browser[0])"; $total = [long]0; $complete = $true; $found = 0
  $profiles = @(CleanupChildren $browser[1] | Where-Object PSIsContainer)
  if (-not $profiles.Count) { 'NOT AVAILABLE: no readable browser profiles; total unknown'; continue }
  foreach ($profile in $profiles) {
    $names = if ($browser[0] -eq 'Firefox') { @('cache2','startupCache') } else { @('Cache','Code Cache','GPUCache') }
    foreach ($name in $names) {
      $r = MeasureCleanup (Join-Path $profile.FullName $name); ShowCleanup $r
      if ($r.Status -eq 'not found') { continue }
      $found++; $total += $r.Bytes
      if ($r.Status -ne 'Complete') { $complete = $false }
    }
  }
  if (-not $found) { 'NOT AVAILABLE: cache folders not found; browser total unavailable' }
  else { '[INFO] {0} cache total: {1:N2} MB ({2} bytes); complete={3}; current-user default locations only' -f $browser[0],($total/1MB),$total,$complete }
}""", timeout=600, tags=('diagnostic', 'storage', 'inventory')),
    D('cl-windows-update-cache', 'Cleanup triage: Windows Update downloads',
      'SoftwareDistribution download bytes, files and oldest cached package write age.',
      _CLEANUP_SCAN + r"""ReportCleanup "$env:SystemRoot\SoftwareDistribution\Download"
'[INFO] Pending updates may still need these downloads.'""",
      admin=True, note='complete Windows Update cache reads may require elevation', timeout=600,
      tags=('diagnostic', 'storage', 'triage')),
    D('cl-delivery-optimization-cache', 'Cleanup triage: Delivery Optimization cache',
      'Delivery Optimization cache bytes and policy/runtime peer configuration evidence.',
      _CLEANUP_SCAN + need_admin('Delivery Optimization service cache is protected') + r"""ReportCleanup "$env:SystemRoot\ServiceProfiles\NetworkService\AppData\Local\Microsoft\Windows\DeliveryOptimization\Cache"
ReportCleanup "$env:ProgramData\Microsoft\Windows\DeliveryOptimization\Cache"
try {
  $p = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization' -ErrorAction Stop
  if ($null -eq $p.DODownloadMode) { '[INFO] Download mode policy unset; peer behavior is not determined by this key.' }
  else { "[INFO] Policy DODownloadMode=$($p.DODownloadMode); 0/99/100 disable peering; 1 LAN, 2 group, 3 Internet" }
} catch { "NOT AVAILABLE: DO policy -- $(CleanupFailure $_): $($_.Exception.Message); missing policy means Windows defaults, not peers disabled" }
try { Get-DeliveryOptimizationStatus -ErrorAction Stop | Select-Object FileId,Status,BytesFromLanPeers,BytesFromInternetPeers | Format-Table | Out-String -Width 240 }
catch { "NOT AVAILABLE: DO runtime peer evidence: $($_.Exception.Message)" }
'[INFO] No active transfers does not establish that peering is disabled; cache paths vary by Windows build.'""",
      admin=True, timeout=600, tags=('diagnostic', 'storage', 'network')),
    D('cl-thumbnail-cache', 'Cleanup triage: thumbnail cache',
      'Explorer thumbcache database bytes, count and oldest write age for the current user.',
      _CLEANUP_SCAN + r"""ReportCleanup "$env:LOCALAPPDATA\Microsoft\Windows\Explorer" 'thumbcache_*.db' 1""",
      tags=('diagnostic', 'storage', 'inventory')),
    D('cl-font-cache', 'Cleanup triage: font cache',
      'LocalService FontCache bytes, file count and oldest write age.',
      _CLEANUP_SCAN + need_admin('LocalService font cache is protected') + r"""ReportCleanup "$env:SystemRoot\ServiceProfiles\LocalService\AppData\Local\FontCache" '*' 1""",
      admin=True, tags=('diagnostic', 'storage', 'inventory')),
    D('cl-shader-caches', 'Cleanup triage: shader caches',
      'DirectX, NVIDIA and AMD shader cache bytes and ages at common current-user locations.',
      _CLEANUP_SCAN + r"""foreach ($path in @("$env:LOCALAPPDATA\D3DSCache", "$env:LOCALAPPDATA\NVIDIA\DXCache",
 "$env:LOCALAPPDATA\NVIDIA\GLCache", "$env:LOCALAPPDATA\NVIDIA\OptixCache", "$env:LOCALAPPDATA\NVIDIA Corporation\NV_Cache",
 "$env:LOCALAPPDATA\AMD\DxCache", "$env:LOCALAPPDATA\AMD\DxcCache", "$env:LOCALAPPDATA\AMD\GLCache", "$env:LOCALAPPDATA\AMD\VkCache")) { ReportCleanup $path }
'[INFO] Driver versions and applications may use additional cache paths.'""",
      timeout=600, tags=('diagnostic', 'storage', 'inventory')),
    D('cl-installer-cache-references', 'Cleanup triage: Windows Installer cache',
      'Installer bytes and MSI/MSP files absent from readable cached-package registrations.',
      _CLEANUP_SCAN + need_admin('all-user MSI registrations and Installer cache need elevation') + _CLEANUP_MSI + r"""ReportCleanup "$env:SystemRoot\Installer"
$registration = CleanupMsiReferences
if (-not $registration.Complete) { Chk $null 'Unreferenced installer count' 'registration reads incomplete; cannot classify'; return }
try {
  $root = Get-Item -LiteralPath "$env:SystemRoot\Installer" -Force -ErrorAction Stop
  if ([long]$root.Attributes -band 0x441400) { throw 'Installer root is a reparse/cloud directory; classification refused' }
  $files = @(Get-ChildItem -LiteralPath $root.FullName -Force -ErrorAction Stop | Where-Object { -not $_.PSIsContainer -and $_.Extension -in '.msi','.msp' })
} catch { Chk $null 'Unreferenced installer count' "$(CleanupFailure $_): $($_.Exception.Message)"; return }
if (-not $registration.References.Count -and $files.Count) { Chk $null 'Unreferenced installer count' 'no readable LocalPackage references; cannot classify cache files'; return }
$candidates = @($files | Where-Object { -not $registration.References.ContainsKey($_.FullName) })
'[INFO] Unreferenced MSI candidates={0}; MSP candidates={1}; scanned files={2}' -f @($candidates | Where-Object Extension -eq '.msi').Count,@($candidates | Where-Object Extension -eq '.msp').Count,$files.Count
$candidates | Select-Object FullName,Length,LastWriteTime | Format-Table -AutoSize | Out-String -Width 240
Chk ($candidates.Count -eq 0) 'Installer reference candidates' "$($candidates.Count) candidates in readable machine/current-user registrations"
'[INFO] Other unloaded per-user registrations may exist. Unreferenced does not prove orphaned or safe to remove; never manually clean Installer.'""",
      admin=True, timeout=600, tags=('diagnostic', 'storage', 'triage')),
    D('cl-package-caches', 'Cleanup triage: package manager caches',
      'NuGet, npm, pip and conda cache sizes at common locations and environment overrides.',
      _CLEANUP_SCAN + r"""$paths = @("$env:USERPROFILE\.nuget\packages", "$env:LOCALAPPDATA\NuGet\v3-cache", "$env:LOCALAPPDATA\npm-cache",
 "$env:APPDATA\npm-cache", "$env:LOCALAPPDATA\pip\Cache", "$env:USERPROFILE\.conda\pkgs", "$env:USERPROFILE\miniconda3\pkgs",
 "$env:USERPROFILE\anaconda3\pkgs", "$env:ProgramData\anaconda3\pkgs")
foreach ($name in 'NUGET_PACKAGES','npm_config_cache','PIP_CACHE_DIR','CONDA_PKGS_DIRS') {
  $v = [Environment]::GetEnvironmentVariable($name)
  if ($v) { $paths += $v -split ';' }
}
$paths | Select-Object -Unique | ForEach-Object { ReportCleanup $_ }
'[INFO] Common paths and environment overrides only; configuration files may redirect caches elsewhere.'""",
      timeout=600, tags=('diagnostic', 'storage', 'inventory')),
    D('cl-prefetch-inventory', 'Cleanup triage: Prefetch inventory',
      'Prefetch file count, bytes and oldest write age; reports evidence without recommending removal.',
      _CLEANUP_SCAN + need_admin('Prefetch directory reads require elevation') + r"""ReportCleanup "$env:SystemRoot\Prefetch" '*.pf' 1
'[INFO] Prefetch helps application startup; age alone is not a reason to clean it.'""",
      admin=True, tags=('diagnostic', 'storage', 'performance')),
    D('cl-old-user-profiles', 'Cleanup triage: profiles unused for 90 days',
      'Unloaded, non-special profiles with last-use dates older than 90 days and metadata-only sizes.',
      _CLEANUP_SCAN + need_admin('other users profile metadata and sizes need elevation') + r"""try {
  $profiles = @(Get-CimInstance Win32_UserProfile -ErrorAction Stop)
  if (-not $profiles.Count) { 'NOT AVAILABLE: profile provider returned no profiles'; return }
  $count = 0
  foreach ($profile in $profiles | Where-Object { -not $_.Special }) {
    if ($null -eq $profile.LastUseTime) { "NOT AVAILABLE: $($profile.LocalPath) -- last use unknown"; continue }
    if (-not $profile.Loaded -and $profile.LastUseTime -lt (Get-Date).AddDays(-90)) {
      $count++; "[INFO] SID=$($profile.SID); LastUse=$($profile.LastUseTime); Loaded=$($profile.Loaded)"
      ReportCleanup $profile.LocalPath
    }
  }
  "[INFO] $count unloaded profiles meet the 90-day last-use threshold; LastUseTime is provider evidence, not proof of last interactive logon."
} catch { "NOT AVAILABLE: profiles: $($_.Exception.Message)" }
'[INFO] Profiles may contain OneDrive placeholders; skipped cloud/reparse paths make totals incomplete.'""",
      admin=True, timeout=900, tags=('diagnostic', 'storage', 'triage')),
    D('cl-windows-old', 'Cleanup triage: Windows.old',
      'Windows.old presence, directory creation time, bytes and oldest file write age.',
      _CLEANUP_SCAN + need_admin('previous Windows installation contains protected folders') + r"""$path = "$env:SystemDrive\Windows.old"
try { $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop; "[INFO] Created=$($item.CreationTime); age days=$([math]::Floor(((Get-Date)-$item.CreationTime).TotalDays))" }
catch { "NOT AVAILABLE: Windows.old -- $(CleanupFailure $_): $($_.Exception.Message)"; return }
ReportCleanup $path
'[INFO] Presence may support rollback; reported age is not the rollback deadline.'""",
      admin=True, timeout=900, tags=('diagnostic', 'storage', 'triage')),
    D('cl-recycle-bin-per-drive', 'Cleanup triage: Recycle Bin per drive',
      'Per-drive $Recycle.Bin bytes, counts and oldest write age across readable user bins.',
      _CLEANUP_SCAN + need_admin('Recycle Bin roots contain other users protected folders') + r"""try {
  $drives = @(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3 OR DriveType=2' -ErrorAction Stop)
  if (-not $drives.Count) { 'NOT AVAILABLE: no local drives reported'; return }
  foreach ($drive in $drives) { ReportCleanup ($drive.DeviceID + '\$Recycle.Bin') }
} catch { "NOT AVAILABLE: local drive inventory: $($_.Exception.Message)" }
'[INFO] Includes bin metadata; inaccessible user bins produce partial totals.'""",
      admin=True, timeout=900, tags=('diagnostic', 'storage', 'inventory')),
    D('cl-iis-log-accumulation', 'Cleanup triage: local IIS logs',
      'Default inetpub log bytes, file counts and oldest log age if IIS logs exist.',
      _CLEANUP_SCAN + need_admin('IIS logs can be protected') + r"""ReportCleanup "$env:SystemDrive\inetpub\logs" '*.log'
'[INFO] Default location only; IIS sites can configure another log directory.'""",
      admin=True, timeout=600, tags=('diagnostic', 'storage', 'triage')),
    D('cl-print-spool-remnants', 'Cleanup triage: print spool files',
      'Configured/default spool folder sizes and SPL/SHD file ages; no job cancellation.',
      _CLEANUP_SCAN + need_admin('print spool files are protected') + r"""$paths = @("$env:SystemRoot\System32\spool\PRINTERS")
try {
  $p = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Print\Printers' -ErrorAction Stop
  if ($p.DefaultSpoolDirectory) { $paths += [Environment]::ExpandEnvironmentVariables($p.DefaultSpoolDirectory) }
} catch { "NOT AVAILABLE: configured spool location -- $(CleanupFailure $_): $($_.Exception.Message)" }
foreach ($path in $paths | Select-Object -Unique) {
  ReportCleanup $path
  foreach ($ext in '*.spl','*.shd') { ReportCleanup $path $ext 1 }
}
'[INFO] SPL/SHD presence and age are remnants/stuck-job candidates; active print jobs also create them.'""",
      admin=True, tags=('diagnostic', 'storage', 'triage')),
    D('cl-downloaded-program-files', 'Cleanup triage: Downloaded Program Files',
      'Legacy IE ActiveX Downloaded Program Files folder bytes, count and oldest write age.',
      _CLEANUP_SCAN + r"""ReportCleanup "$env:SystemRoot\Downloaded Program Files" '*' 1""",
      tags=('diagnostic', 'storage', 'inventory')),
    D('cl-servicing-log-sizes', 'Cleanup triage: CBS and DISM logs',
      'CBS and DISM log-directory bytes including rotated logs and CAB archives.',
      _CLEANUP_SCAN + need_admin('CBS/DISM directories may contain protected logs') + r"""ReportCleanup "$env:SystemRoot\Logs\CBS" '*' 1
ReportCleanup "$env:SystemRoot\Logs\DISM" '*' 1
'[INFO] Includes archives without opening or extracting them; preserve evidence for servicing investigations.'""",
      admin=True, timeout=600, tags=('diagnostic', 'storage', 'triage')),
    D('cl-missing-cached-installers', 'Cleanup triage: missing cached installer registrations',
      'Registered MSI/MSP LocalPackage paths whose cached files are missing or inaccessible.',
      _CLEANUP_SCAN + need_admin('all-user installer registration reads need elevation') + _CLEANUP_MSI + r"""$registration = CleanupMsiReferences; $missing = 0; $unknown = 0
foreach ($path in $registration.References.Keys) {
  try { $null = Get-Item -LiteralPath $path -Force -ErrorAction Stop }
  catch {
    if ($_.CategoryInfo.Category -eq 'ObjectNotFound') { $missing++; "[WARN] Missing cached installer: $path -- $($registration.References[$path])" }
    else { $unknown++; "NOT AVAILABLE: ${path} -- $(CleanupFailure $_): $($_.Exception.Message)" }
  }
}
if (-not $registration.References.Count) { Chk $null 'Cached installer registrations' 'no LocalPackage references found'; return }
$ok = if ($registration.Complete -and -not $unknown) { $missing -eq 0 } else { $null }
Chk $ok 'Cached installer registrations' "references=$($registration.References.Count); missing=$missing; unreadable=$unknown; registration scan complete=$($registration.Complete)"
'[INFO] Missing cache files may break repair/uninstall; this does not establish that the installed product is orphaned.'""",
      admin=True, timeout=600, tags=('diagnostic', 'storage', 'health')),
]

# Incident checks: keep unavailable measurements distinct from healthy values.
SCRIPTS += [
    P("ts-correlated-performance-30s", "Correlated performance and resource owners (30 seconds)",
      "Joins six CPU, commit, disk and network samples with process CPU/I/O deltas and working sets.",
      CHK + r"""$samples = @(); $previous = @{}; $clock = [Diagnostics.Stopwatch]::StartNew()
for ($i=0; $i -le 6; $i++) {
  $wait = 5*$i-$clock.Elapsed.TotalSeconds
  if ($wait -gt 0) { Start-Sleep -Milliseconds ([int](1000*$wait)) }
  $owners = @()
  try {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    if (-not $processes.Count) { throw 'process snapshot empty' }
    $now = $clock.Elapsed.TotalSeconds; $next = @{}
    $owners = @(foreach ($p in $processes) {
      if (-not $p.CreationDate) { continue }
      $key = "$($p.ProcessId)|$($p.CreationDate.ToString('o'))"; $old = $previous[$key]
      $next[$key] = @{P=$p; Time=$now}
      if ($old) {
        if ($null -eq $p.KernelModeTime -or $null -eq $p.UserModeTime -or $null -eq $old.P.KernelModeTime -or $null -eq $old.P.UserModeTime -or $null -eq $p.ReadTransferCount -or $null -eq $p.WriteTransferCount -or $null -eq $old.P.ReadTransferCount -or $null -eq $old.P.WriteTransferCount -or $null -eq $p.WorkingSetSize) { Chk $null $p.Name 'owner counters incomplete' | Out-Host; continue }
        $dt = $now-$old.Time
        $cpuDelta = [decimal]$p.KernelModeTime+[decimal]$p.UserModeTime-[decimal]$old.P.KernelModeTime-[decimal]$old.P.UserModeTime
        $ioDelta = [decimal]$p.ReadTransferCount+[decimal]$p.WriteTransferCount-[decimal]$old.P.ReadTransferCount-[decimal]$old.P.WriteTransferCount
        if ($cpuDelta -lt 0 -or $ioDelta -lt 0) { continue }
        [pscustomobject]@{Name=$p.Name; PID=$p.ProcessId; CPUCorePct=[math]::Round($cpuDelta/1e7/$dt*100,1); IOMBps=[math]::Round($ioDelta/1MB/$dt,2); WS_MB=[math]::Round([double]$p.WorkingSetSize/1MB,1)}
      }
    }); $previous = $next
  } catch { Chk $null 'Resource owners' $_.Exception.Message }
  if ($i -eq 0) { continue }
  $row = [ordered]@{Time=(Get-Date); CPU=$null; CommitPct=$null; DiskQueue=$null; NetworkMBps=$null}
  foreach ($source in 'CPU','Memory','Disk','Network') {
    try {
      switch ($source) {
        'CPU' { $v=Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'" -ErrorAction Stop; if ($null -eq $v.PercentProcessorTime) { throw 'CPU counter empty' }; $row.CPU=$v.PercentProcessorTime }
        'Memory' { $v=Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory -ErrorAction Stop; if ($v.CommitLimit -le 0 -or $null -eq $v.CommittedBytes) { throw 'commit counters empty' }; $row.CommitPct=100*$v.CommittedBytes/$v.CommitLimit }
        'Disk' { $v=@(Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -ErrorAction Stop | Where-Object { $_.Name -ne '_Total' -and $null -ne $_.CurrentDiskQueueLength }); if (-not $v.Count) { throw 'disk counters empty' }; $top=$v | Sort-Object CurrentDiskQueueLength -Descending | Select-Object -First 1; $row.DiskQueue=$top.CurrentDiskQueueLength; "[INFO] Busiest queue: $($top.Name) = $($top.CurrentDiskQueueLength)" }
        'Network' { $v=@(Get-CimInstance Win32_PerfFormattedData_Tcpip_NetworkInterface -ErrorAction Stop | Where-Object { $null -ne $_.BytesTotalPersec }); if (-not $v.Count) { throw 'network counters empty' }; $row.NetworkMBps=($v | Measure-Object BytesTotalPersec -Sum).Sum/1MB; $top=$v | Sort-Object BytesTotalPersec -Descending | Select-Object -First 1; "[INFO] Busiest NIC: $($top.Name) = $($top.BytesTotalPersec) B/s" }
      }
    } catch { Chk $null "$source sample $i" $_.Exception.Message }
  }
  $samples += [pscustomobject]$row
  foreach ($metric in 'CPUCorePct','IOMBps','WS_MB') {
    $top=$owners | Sort-Object $metric -Descending | Select-Object -First 1
    if ($top) { "[INFO] Sample $i top ${metric}: $($top.Name) PID=$($top.PID) value=$($top.$metric)" }
    else { Chk $null "Sample $i owners" 'no comparable process identities' }
  }
}
$samples | Format-Table -AutoSize | Out-String -Width 220
foreach ($metric in 'CPU','CommitPct','DiskQueue','NetworkMBps') {
  $valid=@($samples | Where-Object { $null -ne $_.$metric }); $m=$valid | Measure-Object $metric -Average -Maximum
  if ($valid.Count) { Chk $null $metric ("valid={0}/6 average={1:N2} peak={2:N2}; blank samples excluded" -f $valid.Count,$m.Average,$m.Maximum) }
  else { Chk $null $metric 'NOT AVAILABLE: no valid samples; average and peak unknown' }
}
$pressure=@($samples | Where-Object { $_.CPU -ge 90 -or $_.CommitPct -ge 90 })
$complete=@($samples | Where-Object { $null -ne $_.CPU -and $null -ne $_.CommitPct }).Count -eq 6
$verdict=if ($pressure.Count) { $false } elseif ($complete) { $true } else { $null }
Chk $verdict 'Observed CPU/commit pressure' "$($pressure.Count) samples at or above 90%; complete CPU/commit coverage=$complete"
'[INFO] CPU owner percentage is per core; I/O includes network/device operations and cannot identify physical disk or NIC process ownership. Network sum may double-count virtual interfaces. Snapshots are sequential; repeat sustained peaks before diagnosing.'""",
      timeout=120, tags=('diagnostic', 'performance', 'triage', 'health')),
    P("ts-memory-growth-30s", "Memory leak candidates by working-set growth (30 seconds)",
      "Compares stable CIM process identities, working sets and private bytes alongside host commit pressure.",
      CHK + r"""try {
  $before=@{}; Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object { if ($_.CreationDate) { $before["$($_.ProcessId)|$($_.CreationDate.ToString('o'))"]=$_ } }
  if (-not $before.Count) { throw 'initial process identities empty' }
  $clock=[Diagnostics.Stopwatch]::StartNew(); Start-Sleep -Seconds 30
  $after=@(Get-CimInstance Win32_Process -ErrorAction Stop); $elapsed=$clock.Elapsed.TotalSeconds
  if (-not $after.Count) { throw 'final process snapshot empty' }
  $rows=@(foreach ($p in $after) {
    if (-not $p.CreationDate) { continue }; $old=$before["$($p.ProcessId)|$($p.CreationDate.ToString('o'))"]
    if (-not $old) { continue }
    if ($null -eq $p.WorkingSetSize -or $null -eq $old.WorkingSetSize -or $null -eq $p.PrivatePageCount -or $null -eq $old.PrivatePageCount) { Chk $null $p.Name 'memory counters unavailable' | Out-Host; continue }
    [pscustomobject]@{Process=$p.Name; PID=$p.ProcessId; WS_MB=[math]::Round([double]$p.WorkingSetSize/1MB,1); GrowthMB=[math]::Round(([decimal]$p.WorkingSetSize-[decimal]$old.WorkingSetSize)/1MB,2); PrivateGrowthMB=[math]::Round(([decimal]$p.PrivatePageCount-[decimal]$old.PrivatePageCount)/1MB,2); MBperMinute=[math]::Round(([decimal]$p.WorkingSetSize-[decimal]$old.WorkingSetSize)/1MB/$elapsed*60,2)}
  })
  if (-not $rows.Count) { throw 'no comparable memory counters' }
  $rows | Sort-Object GrowthMB -Descending | Select-Object -First 20 | Format-Table -AutoSize | Out-String -Width 220
  Chk (-not @($rows | Where-Object { $_.GrowthMB -gt 100 -and $_.PrivateGrowthMB -gt 100 }).Count) 'Leak review threshold' '100 MB growth in both metrics flags candidates only; repeat sampling and inspect allocation stacks before declaring a leak'
} catch { Chk $null 'Memory growth' $_.Exception.Message }
try {
  $m=Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory -ErrorAction Stop
  if ($m.CommitLimit -le 0 -or $null -eq $m.CommittedBytes) { throw 'commit counters unavailable' }
  Chk ($m.CommittedBytes/$m.CommitLimit -lt .9) 'Host commit' ("{0:N1}% committed; available RAM={1} MB" -f (100*$m.CommittedBytes/$m.CommitLimit),$m.AvailableMBytes)
} catch { Chk $null 'Host commit' $_.Exception.Message }
'[INFO] New/exited processes and unreadable creation times are excluded. Working-set growth can be normal cache warming; private growth strengthens evidence but does not prove a leak.'""",
      timeout=120, tags=('diagnostic', 'performance', 'triage', 'health')),
    P("ts-handle-leak-growth-15s", "Handle leak candidates: count and growth rate",
      "Joins PID/creation-time snapshots over 15 seconds, ranking current handles and handle/thread growth.",
      CHK + r"""try {
  $first=@{}; Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object { if ($_.CreationDate) { $first["$($_.ProcessId)|$($_.CreationDate.ToString('o'))"]=$_ } }
  if (-not $first.Count) { throw 'first snapshot empty' }
  $clock=[Diagnostics.Stopwatch]::StartNew(); Start-Sleep -Seconds 15
  $last=@(Get-CimInstance Win32_Process -ErrorAction Stop); $seconds=$clock.Elapsed.TotalSeconds
  if (-not $last.Count) { throw 'second snapshot empty' }
  $rows=@(foreach ($p in $last) {
    if (-not $p.CreationDate) { continue }; $old=$first["$($p.ProcessId)|$($p.CreationDate.ToString('o'))"]
    if (-not $old) { continue }
    if ($null -eq $p.HandleCount -or $null -eq $old.HandleCount) { Chk $null $p.Name 'handle count unavailable' | Out-Host; continue }
    $delta=[long]$p.HandleCount-[long]$old.HandleCount
    [pscustomobject]@{Process=$p.Name; PID=$p.ProcessId; Handles=$p.HandleCount; Growth=$delta; HandlesPerSec=[math]::Round($delta/$seconds,2); Threads=$p.ThreadCount; ThreadGrowth=([long]$p.ThreadCount-[long]$old.ThreadCount)}
  })
  if (-not $rows.Count) { throw 'no stable processes with readable counters' }
  '== Highest current counts'; $rows | Sort-Object Handles -Descending | Select-Object -First 15 | Format-Table -AutoSize | Out-String -Width 220
  '== Fastest growth'; $rows | Sort-Object HandlesPerSec -Descending | Select-Object -First 15 | Format-Table -AutoSize | Out-String -Width 220
  Chk (-not @($rows | Where-Object { $_.Handles -gt 10000 -and $_.Growth -gt 100 }).Count) 'Handle growth review' 'more than 10000 handles and 100 added in this interval warrants repeated sampling and handle-type inspection; not proof of a leak'
} catch { Chk $null 'Handle sampling' $_.Exception.Message }""",
      timeout=120, tags=('diagnostic', 'performance', 'triage', 'health')),
    P("ts-dpc-isr-pressure", "DPC/ISR pressure by CPU (15 seconds)",
      "Correlates per-CPU DPC/interrupt time and interrupt rate over five samples using CIM counters.",
      CHK + r"""$rows=@()
for ($i=1; $i -le 5; $i++) {
  Start-Sleep -Seconds 3
  try {
    $cpus=@(Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -ErrorAction Stop)
    if (-not $cpus.Count) { throw 'processor provider returned no rows' }
    foreach ($c in $cpus) {
      if ($null -eq $c.PercentDPCTime -or $null -eq $c.PercentInterruptTime) { Chk $null $c.Name 'DPC/ISR fields absent'; continue }
      $rows += [pscustomobject]@{CPU=$c.Name; DPC=[double]$c.PercentDPCTime; ISR=[double]$c.PercentInterruptTime; InterruptsPerSec=$c.InterruptsPersec; Sample=$i}
    }
  } catch { Chk $null "Sample $i" $_.Exception.Message }
}
$rows | Group-Object CPU | ForEach-Object {
  $d=$_.Group | Measure-Object DPC -Average -Maximum; $irq=$_.Group | Measure-Object ISR -Average -Maximum
  [pscustomobject]@{CPU=$_.Name; Samples=$_.Count; AvgDPC=$d.Average; PeakDPC=$d.Maximum; AvgISR=$irq.Average; PeakISR=$irq.Maximum; PeakInterrupts=($_.Group | Measure-Object InterruptsPerSec -Maximum).Maximum} | Format-List | Out-String -Width 220
  Chk ($d.Maximum -lt 10 -and $irq.Maximum -lt 5) "CPU $($_.Name) pressure" 'review at DPC >=10% or ISR >=5%; heuristic thresholds, including individual cores'
}
if (-not $rows.Count) { Chk $null 'DPC/ISR verdict' 'no valid samples' }
'[INFO] CPU time percentages do not measure microsecond latency or identify a driver. Correlate with audio/stutter timestamps and an approved external ETW capture for attribution.'""",
      tags=('diagnostic', 'performance', 'hardware', 'triage', 'health')),
    P("ts-last-five-boots", "Last five boots with degradation evidence",
      "Joins Diagnostics-Performance events 100-110 using activity IDs or bounded boot windows.",
      CHK + r"""$log='Microsoft-Windows-Diagnostics-Performance/Operational'
function BootData($event) { $map=@{}; ([xml]$event.ToXml()).Event.EventData.Data | ForEach-Object { if ($_.Name) { $map[$_.Name]=$_.'#text' } }; return $map }
try {
  $meta=Get-WinEvent -ListLog $log -ErrorAction Stop
  if (-not $meta.IsEnabled) { Chk $null 'Boot log' 'disabled; retained history incomplete' }
  $boots=@(Get-WinEvent -FilterHashtable @{LogName=$log;Id=100} -MaxEvents 5 -ErrorAction Stop)
  if (-not $boots.Count) { throw 'no boot summaries returned' }
  $events=@(Get-WinEvent -FilterHashtable @{LogName=$log;Id=(100..110);StartTime=$boots[-1].TimeCreated.AddHours(-1)} -MaxEvents 3000 -ErrorAction Stop)
  if ($events.Count -eq 3000) { Chk $null 'Boot evidence' 'newest 3000 records only; associations may be incomplete' }
  foreach ($boot in $boots) {
    $d=BootData $boot; $xml=[xml]$boot.ToXml(); $activity=[string]$xml.Event.System.Correlation.ActivityID
    "== Boot summary $($boot.TimeCreated); boot=$($d.BootTime) ms; main=$($d.MainPathBootTime) ms; post=$($d.BootPostBootTime) ms"
    $ok=if ($d.BootTime -match '^\d+$') { [long]$d.BootTime -lt 120000 } else { $null }
    Chk $ok 'Boot duration' '120 seconds is a review threshold; compare workload and fast/full boot type'
    $start=$null; $end=$null
    if ($d.BootStartTime) { $start=[datetime]::Parse($d.BootStartTime) }
    if ($d.BootEndTime) { $end=[datetime]::Parse($d.BootEndTime) }
    $related=@($events | Where-Object {
      if ($_.Id -eq 100) { return $false }; $a=[string]([xml]$_.ToXml()).Event.System.Correlation.ActivityID
      if ($activity -and $a) { return $a -eq $activity }
      return ($start -and $end -and $_.TimeCreated -ge $start -and $_.TimeCreated -le $end.AddMinutes(2))
    })
    if (-not $related.Count) { Chk $null 'Degradation evidence' 'no attributable 101-110 records; not proof of a clean boot' }
    foreach ($e in $related) { $v=BootData $e; [pscustomobject]@{Time=$e.TimeCreated; ID=$e.Id; Component=$v.Name; FriendlyName=$v.FriendlyName; TotalMs=$v.TotalTime; DegradationMs=$v.DegradationTime; Path=$v.Path} | Format-List | Out-String -Width 220 }
    if (-not $activity) { Chk $null 'Correlation' 'activity ID absent; time-window association is approximate' }
  }
} catch { Chk $null 'Boot history unavailable or empty' $_.Exception.Message }""",
      tags=('diagnostic', 'performance', 'triage', 'health')),
]
# Advanced incident checks. Each entry remains a standalone read-only script.
SCRIPTS += [
    P('ts2-threadpool-starvation', '.NET thread pool starvation evidence (10 seconds)',
      'Samples installed thread-pool instrumentation alongside processor queues and CLR lock waits; states missing runtime coverage.',
      CHK + r"""$sets=@()
try {
  $sets=@(Get-Counter -ListSet * -ErrorAction Stop | Where-Object CounterSetName -match 'Thread.?Pool')
  if (-not $sets.Count) { Chk $null 'Thread-pool instrumentation' 'NOT AVAILABLE: no installed thread-pool counter sets. Framework CLR lock queues are NOT thread-pool queues.' }
} catch { Chk $null 'Counter discovery (localized names)' $_.Exception.Message }
$paths=@($sets | ForEach-Object PathsWithInstances | Where-Object { $_ -match 'queue|completion|worker|thread' })
if ($paths.Count -gt 100) { Chk $null 'Instrumentation scope' 'first 100 matching paths only'; $paths=@($paths | Select-Object -First 100) }
$samples=@(); $clock=[Diagnostics.Stopwatch]::StartNew()
for ($i=0;$i -le 5;$i++) {
  $wait=2*$i-$clock.Elapsed.TotalSeconds; if ($wait -gt 0) { Start-Sleep -Milliseconds ([int](1000*$wait)) }
  $queue=$null; $cpu=$null
  try {
    $s=Get-CimInstance Win32_PerfFormattedData_PerfOS_System -ErrorAction Stop
    $c=Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'" -ErrorAction Stop
    if ($null -eq $s.ProcessorQueueLength -or $null -eq $c.PercentProcessorTime) { throw 'processor counters empty' }
    $queue=$s.ProcessorQueueLength; $cpu=$c.PercentProcessorTime
  } catch { Chk $null "Host sample $i" $_.Exception.Message }
  $samples += [pscustomobject]@{Time=(Get-Date); ProcessorQueue=$queue; CPUPercent=$cpu}
  if ($paths.Count) {
    try {
      $v=Get-Counter -Counter $paths -ErrorAction Stop
      if (-not $v.CounterSamples.Count) { throw 'thread-pool counters empty' }
      foreach ($counter in $v.CounterSamples) {
        if ($counter.Status -ne 0) { Chk $null $counter.Path "counter status=$($counter.Status)" }
        else { "[INFO] sample=$i path=$($counter.Path) value=$($counter.CookedValue)" }
      }
    } catch { Chk $null 'Thread-pool queued items / IO completion threads' $_.Exception.Message }
  }
  try {
    $clr=@(Get-CimInstance Win32_PerfFormattedData_NETFramework_NETCLRLocksAndThreads -ErrorAction Stop | Where-Object Name -ne '_Global_')
    if (-not $clr.Count) { throw 'no Framework CLR instances; modern .NET is not covered' }
    $clr | Select-Object Name,CurrentQueueLength,ContentionRatePersec,NumberofcurrentphysicalThreads,NumberofcurrentlogicalThreads | Format-Table | Out-String -Width 220
  } catch { Chk $null 'CLR lock contention proxy' $_.Exception.Message }
}
$samples | Format-Table -AutoSize | Out-String -Width 220
Chk $null 'Starvation interpretation' 'Increasing application work queues with low CPU and stalled completions suggest blocked workers; high CPU plus processor queues suggests CPU saturation. CLR CurrentQueueLength counts LOCK waiters, not queued work.'
Chk $null 'Queued work and IOCP coverage' 'NOT AVAILABLE unless installed runtime/application instrumentation above explicitly exposes them. ThreadPool.GetAvailableThreads in this PowerShell would measure only PowerShell. For modern .NET use an approved external System.Runtime counter capture against the affected PID (queue length, thread count, completed items); no trace/attachment started here.'""",
      timeout=120, tags=('diagnostic', 'performance', 'triage')),
    P('ts2-committed-memory-pressure', 'Committed memory pressure and process attribution',
      'Correlates commit headroom, physical availability, modified pages, paging files and per-process private commit.',
      CHK + r"""$mem=$null
try {
  $mem=Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory -ErrorAction Stop
  $os=Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
  if ($mem.CommitLimit -le 0 -or $null -eq $mem.CommittedBytes -or $null -eq $mem.AvailableBytes -or $os.TotalVisibleMemorySize -le 0) { throw 'required memory counters empty' }
  $ram=[double]$os.TotalVisibleMemorySize*1KB; $headroom=[double]$mem.CommitLimit-$mem.CommittedBytes
  [pscustomobject]@{CommitGB=$mem.CommittedBytes/1GB; CommitLimitGB=$mem.CommitLimit/1GB; HeadroomGB=$headroom/1GB; VisibleRAMGB=$ram/1GB; CommitToRAM=$mem.CommittedBytes/$ram; AvailableGB=$mem.AvailableBytes/1GB; ModifiedPageListGB=$(if ($null -ne $mem.ModifiedPageListBytes) { $mem.ModifiedPageListBytes/1GB }); PagesInPerSec=$mem.PagesInputPersec; PagesOutPerSec=$mem.PagesOutputPersec; PagedPoolMB=$mem.PoolPagedBytes/1MB; NonpagedPoolMB=$mem.PoolNonpagedBytes/1MB} | Format-List | Out-String -Width 220
  Chk ($mem.CommittedBytes/$mem.CommitLimit -lt .9) 'Commit headroom' '90% usage is a triage threshold; committed virtual memory is not resident physical RAM'
  Chk ($mem.AvailableBytes/$ram -gt .05) 'Physical availability' '5% available is a review threshold; evaluate paging rate and workload'
  if ($null -eq $mem.ModifiedPageListBytes) { Chk $null 'Modified page list' 'NOT AVAILABLE: counter absent' }
} catch { Chk $null 'Host memory' $_.Exception.Message }
try {
  $files=@(Get-CimInstance Win32_PageFileUsage -ErrorAction Stop)
  if (-not $files.Count) { Chk $null 'Page files' 'no instances; possibly disabled, not zero utilization' }
  foreach ($f in $files) {
    $f | Select-Object Name,AllocatedBaseSize,CurrentUsage,PeakUsage,TempPageFile | Format-List | Out-String -Width 220
    $ok=if ($f.AllocatedBaseSize -gt 0 -and $null -ne $f.CurrentUsage) { $f.CurrentUsage/$f.AllocatedBaseSize -lt .9 } else { $null }
    Chk $ok $f.Name 'current/allocated usage in MB; peak is since boot, not this incident'
  }
} catch { Chk $null 'Page-file usage' $_.Exception.Message }
try {
  $p=@(Get-CimInstance Win32_Process -ErrorAction Stop)
  if (-not $p.Count) { throw 'process inventory empty' }
  $valid=@($p | Where-Object { $null -ne $_.PrivatePageCount })
  if (-not $valid.Count) { throw 'private commit counters absent' }
  $valid | Sort-Object PrivatePageCount -Descending | Select-Object -First 20 Name,ProcessId,@{n='Path';e={if ($_.ExecutablePath) {$_.ExecutablePath} else {'NOT AVAILABLE'}}},@{n='PrivateCommitMB';e={[double]$_.PrivatePageCount/1MB}},@{n='WorkingSetMB';e={if ($null -ne $_.WorkingSetSize) {[double]$_.WorkingSetSize/1MB} else {$null}}} | Format-Table -Wrap | Out-String -Width 220
  $sum=($valid | Measure-Object PrivatePageCount -Sum).Sum
  "[INFO] Readable process private commit=$($sum/1GB) GB; missing counters=$($p.Count-$valid.Count); missing paths=$(@($p | Where-Object { -not $_.ExecutablePath }).Count)."
  if ($mem -and $null -ne $mem.AvailableBytes) { "[INFO] Process private commit / available RAM=$($sum/[math]::Max(1,$mem.AvailableBytes)); this ratio is context, not a health threshold." }
} catch { Chk $null 'Process commit attribution' $_.Exception.Message }
Chk $null 'Next step' 'Low commit headroom with available RAM still present indicates commit pressure; low available RAM with high paging suggests residency pressure. Private commit sums exclude shared/kernel commitments and snapshots are not atomic. Repeat before attributing a leak.'""",
      admin=True, note='some process executable paths require elevation', tags=('diagnostic', 'performance', 'health', 'triage')),
    P('ts2-disk-io-attribution-15s', 'Process I/O attribution with disk context (15 seconds)',
      'Ranks six formatted CIM process I/O rate samples, joins stable process paths and correlates physical disk queues.',
      need_admin('complete process path attribution requires elevation') + CHK + r"""$rows=@(); $clock=[Diagnostics.Stopwatch]::StartNew()
for ($i=0;$i -le 5;$i++) {
  $wait=3*$i-$clock.Elapsed.TotalSeconds; if ($wait -gt 0) { Start-Sleep -Milliseconds ([int](1000*$wait)) }
  try {
    $before=@{}; Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object { $before[[int]$_.ProcessId]=$_ }
    $counters=@(Get-CimInstance Win32_PerfFormattedData_PerfProc_Process -ErrorAction Stop | Where-Object { $_.IDProcess -gt 0 -and $_.Name -ne '_Total' })
    $after=@{}; Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object { $after[[int]$_.ProcessId]=$_ }
    if (-not $counters.Count -or -not $before.Count -or -not $after.Count) { throw 'process/counter inventory empty' }
    foreach ($c in $counters) {
      $p=$before[[int]$c.IDProcess]; $q=$after[[int]$c.IDProcess]
      if (-not $p.CreationDate -or -not $q.CreationDate -or $p.CreationDate -ne $q.CreationDate) { Chk $null "PID $($c.IDProcess)" 'exited/reused/unreadable identity; excluded'; continue }
      if ($null -eq $c.IOReadBytesPersec -or $null -eq $c.IOWriteBytesPersec) { Chk $null $c.Name 'I/O counters absent'; continue }
      $rows += [pscustomobject]@{Key="$($c.IDProcess)|$($p.CreationDate.ToString('o'))"; PID=$c.IDProcess; Name=$p.Name; Path=$(if ($p.ExecutablePath) { $p.ExecutablePath } else { 'NOT AVAILABLE: protected path' }); ReadMBps=[double]$c.IOReadBytesPersec/1MB; WriteMBps=[double]$c.IOWriteBytesPersec/1MB; TotalMBps=([double]$c.IOReadBytesPersec+[double]$c.IOWriteBytesPersec)/1MB}
    }
  } catch { Chk $null "Process sample $i" $_.Exception.Message }
  try {
    $disks=@(Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -ErrorAction Stop | Where-Object Name -ne '_Total')
    if (-not $disks.Count) { throw 'physical disk counters empty' }
    "== Physical disks sample $i"
    $disks | Select-Object Name,DiskReadBytesPersec,DiskWriteBytesPersec,CurrentDiskQueueLength,PercentIdleTime | Format-Table | Out-String -Width 220
  } catch { Chk $null "Disk sample $i" $_.Exception.Message }
}
$ranked=@($rows | Group-Object Key | ForEach-Object { $p=$_.Group[0]; [pscustomobject]@{PID=$p.PID; Name=$p.Name; Path=$p.Path; Samples=$_.Count; MeanReadMBps=($_.Group | Measure-Object ReadMBps -Average).Average; MeanWriteMBps=($_.Group | Measure-Object WriteMBps -Average).Average; PeakTotalMBps=($_.Group | Measure-Object TotalMBps -Maximum).Maximum} })
$ranked | Sort-Object PeakTotalMBps -Descending | Select-Object -First 20 | Format-Table -Wrap | Out-String -Width 240
Chk $null 'I/O consumers' "$($ranked.Count) stable process identities; six point-rate samples across 15 seconds, not cumulative byte deltas. Missing samples reduce comparability."
Chk $null 'Attribution limits / next step' 'Process I/O includes cached file, network and device operations; it cannot be equated to physical disk bytes. Compare repeated owner peaks with disk queues/idle time; use an approved file/disk ETW capture for file and disk attribution. No trace started.'""",
      admin=True, timeout=120, tags=('diagnostic', 'performance', 'storage', 'triage')),
    P('ts2-gpu-resource-contention', 'GPU engine owners and video memory pressure (10 seconds)',
      'Correlates WDDM engine utilization by PID/adapter with dedicated and shared GPU memory counters.',
      need_admin('complete GPU owner paths require elevation') + CHK + r"""$clock=[Diagnostics.Stopwatch]::StartNew()
for ($i=0;$i -le 5;$i++) {
  $wait=2*$i-$clock.Elapsed.TotalSeconds; if ($wait -gt 0) { Start-Sleep -Milliseconds ([int](1000*$wait)) }
  "== GPU sample $i at $(Get-Date -Format o)"
  try {
    $owners=@{}; Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object { $owners[[int]$_.ProcessId]=$_ }
    $engines=@(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine -ErrorAction Stop)
    if (-not $engines.Count) { throw 'GPU engine counters empty; requires supported WDDM driver' }
    foreach ($e in $engines | Sort-Object UtilizationPercentage -Descending | Select-Object -First 20) {
      if ($null -eq $e.UtilizationPercentage) { Chk $null $e.Name 'utilization absent'; continue }
      $owner=$null; if ($e.Name -match 'pid_(\d+)_') { $owner=$owners[[int]$Matches[1]] }
      [pscustomobject]@{Engine=$e.Name; Percent=$e.UtilizationPercentage; Process=$owner.Name; PID=$owner.ProcessId; Path=$(if ($owner.ExecutablePath) { $owner.ExecutablePath } else { 'NOT AVAILABLE: exited/protected owner' })} | Format-List | Out-String -Width 220
      Chk ($e.UtilizationPercentage -lt 90) $e.Name '>=90% is a busy-engine lead; per-engine/PID percentages must not be summed as whole-GPU utilization'
    }
  } catch { Chk $null 'GPU engines' $_.Exception.Message }
  foreach ($class in 'GPUAdapterMemory','GPUProcessMemory') {
    try {
      $memory=@(Get-CimInstance -ClassName "Win32_PerfFormattedData_GPUPerformanceCounters_$class" -ErrorAction Stop)
      if (-not $memory.Count) { throw 'no memory instances returned' }
      $memory | Sort-Object DedicatedUsage -Descending | Select-Object -First 20 Name,DedicatedUsage,SharedUsage,TotalCommitted | Format-Table -Wrap | Out-String -Width 220
      Chk $null $class 'bytes; adapter/PID encoded in instance name; absent fields are NOT AVAILABLE. Adapter and process totals overlap; do not add them.'
    } catch { Chk $null $class $_.Exception.Message }
  }
}
Chk $null 'Video memory budget' 'NOT AVAILABLE: these performance classes expose usage, not DXGI dynamic per-process budgets. Shared usage is system RAM, not extra dedicated VRAM. High usage alone cannot prove eviction pressure.'
Chk $null 'Next step' 'Match pid/luid/phys/eng instance identifiers across rows. Repeated busy engines with increasing shared usage warrant per-adapter DXGI budget and GPUView analysis. Snapshots are sequential; exited/reused PIDs can prevent reliable owner attribution.'""",
      admin=True, timeout=120, tags=('diagnostic', 'performance', 'hardware', 'triage')),
]

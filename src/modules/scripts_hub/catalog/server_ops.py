"""Wintel operations: log parsing, fleet checks across many servers, and more
roles & features. Read-only.

Fleet scripts read their targets from `servers.txt` in the scripts folder
(the one chosen in the pane, passed as %WCT_SCRIPTS_DIR%) (one name per line, # comments); with no list they check THIS
computer, which is how they were verified here. They talk CIM over DCOM, so
they need no WinRM -- only RPC/WMI through the target's firewall and an
account that is admin there.
"""
from ._base import CHK, EV, maker, need_admin, need_cmd

LP = maker("Log parsing", server=True)
FL = maker("Fleet (remote servers)", server=True)
RF = maker("Roles & features", server=True)
S = maker("Roles & features", server=True)
_PRINT = maker("Print server", server=True, requires="PrintManagement module and local print spooler")

_REMOTE_READ = CHK + r"""try {
  $folder = if ($env:WCT_SCRIPTS_DIR) { $env:WCT_SCRIPTS_DIR } else { Join-Path $env:APPDATA 'WindowsTweaker\scripts' }
  $list = Join-Path $folder 'servers.txt'
  $targets = if (Test-Path -LiteralPath $list -ErrorAction Stop) {
    @(Get-Content -LiteralPath $list -ErrorAction Stop | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') } | Sort-Object -Unique)
  } else { @($env:COMPUTERNAME) }
  if (-not (Test-Path -LiteralPath $list -ErrorAction Stop)) { '[INFO] No servers.txt; checking this computer only.' }
  if (-not $targets.Count) { 'NOT AVAILABLE: servers.txt contains no targets.'; return }
} catch { "ERROR: Cannot read fleet targets: $($_.Exception.Message)"; return }
function EachRemote([scriptblock]$block) {
  $definition = ${function:Chk}.ToString()
  $run = [scriptblock]::Create("function Chk { $definition }`n" + $block.ToString())
  foreach ($target in $targets) {
    "== $target"
    try {
      if ($target -in $env:COMPUTERNAME,'localhost','.') { & $run }
      else { Invoke-Command -ComputerName $target -ScriptBlock $run -ErrorAction Stop }
    } catch { Chk $null $target "read refused or unreachable: $($_.Exception.Message)" }
  }
}
"""

#: Defines $targets and `Each { param($name, $cim) ... }` -- runs the block per
#: target with a DCOM CIM session, reporting unreachable ones as [BAD].
FLEET = r"""$list = Join-Path $(if ($env:WCT_SCRIPTS_DIR) { $env:WCT_SCRIPTS_DIR } else { Join-Path $env:APPDATA 'WindowsTweaker\scripts' }) 'servers.txt'
$targets = if (Test-Path $list) { Get-Content $list | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') } } else { @($env:COMPUTERNAME) }
if (-not (Test-Path $list)) { "[INFO] No $list -- checking this computer only. Put one server name per line in that file to check many." }
$opt = New-CimSessionOption -Protocol Dcom
function Each([scriptblock]$block) { foreach ($t in $targets) { try { $cim = New-CimSession -ComputerName $t -SessionOption $opt -OperationTimeoutSec 30 -ErrorAction Stop
    try { & $block $t $cim } finally { Remove-CimSession $cim } } catch { "[BAD]  $t -- unreachable: $($_.Exception.Message)" } } }
"""

SCRIPTS = [
    S("ops-cpu-top-ten-second-average", "Top CPU consumers over ten seconds",
      "Ranks surviving processes by CPU-time deltas over a measured interval, normalized to total logical processors rather than lifetime CPU totals.",
      r"""try {
  $logical = (Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).NumberOfLogicalProcessors
  if ($logical -le 0) { throw 'Logical processor count unavailable' }
  $before = @{}; $clock = [Diagnostics.Stopwatch]::StartNew()
  foreach ($p in Get-CimInstance Win32_Process -ErrorAction Stop) {
    $key = "$($p.ProcessId)|$($p.CreationDate)"
    $before[$key] = @{Ticks=([decimal]$p.KernelModeTime+[decimal]$p.UserModeTime); At=$clock.Elapsed.TotalSeconds}
  }
  Start-Sleep -Seconds 10
  $rows = @(foreach ($p in Get-CimInstance Win32_Process -ErrorAction Stop) {
    $key = "$($p.ProcessId)|$($p.CreationDate)"; $old = $before[$key]
    if ($p.ProcessId -eq 0 -or -not $old) { continue }
    $seconds = $clock.Elapsed.TotalSeconds-$old.At
    $delta = ([decimal]$p.KernelModeTime+[decimal]$p.UserModeTime)-$old.Ticks
    if ($delta -lt 0 -or $seconds -le 0) { continue }
    [pscustomobject]@{ Process=$p.Name; PID=$p.ProcessId; CPUPercent=[math]::Round(100*$delta/10000000/$seconds/$logical,2); IntervalSeconds=[math]::Round($seconds,2) }
  })
  $rows | Sort-Object CPUPercent -Descending | Select-Object -First 20 | Format-Table -AutoSize | Out-String -Width 220
  '[INFO] Excludes processes started or exited during sampling and PID 0. Processor-group accounting can differ on very large hosts.'
} catch { "ERROR: Cannot sample CPU use: $($_.Exception.Message)" }""",
      timeout=60, tags=('diagnostic', 'performance', 'triage')),
    S("ops-memory-paging-now", "Memory availability and paging right now",
      "Reads available MB, commit pressure and pages/sec from locale-independent CIM counters to investigate memory pressure.",
      r"""try {
  $m = @(Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory -ErrorAction Stop)
  if (-not $m.Count) { '[INFO] Memory counters unavailable; no conclusion can be drawn.'; return }
  $m | Select-Object AvailableMBytes, PagesPersec, PagesInputPersec, PagesOutputPersec, PageReadsPersec, PageWritesPersec, PercentCommittedBytesInUse, CommittedBytes, CommitLimit | Format-List | Out-String -Width 220
  '[INFO] One sample is not a sustained-pressure diagnosis; pages/sec also includes mapped-file activity.'
} catch { "ERROR: Cannot read memory counters: $($_.Exception.Message)" }""",
      tags=('diagnostic', 'performance', 'triage')),
    S("ops-firewall-inbound-any-source", "Enabled inbound allow rules from Any",
      "Lists effective inbound allow rules with unrestricted remote addresses, including port and application restrictions for exposure review.",
      need_cmd("Get-NetFirewallRule", "NetSecurity module") + need_admin("effective firewall policy requires elevation") + CHK + r"""try {
  $rules = @(Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow -ErrorAction Stop)
  $found = 0
  foreach ($r in $rules) {
    try {
      $address = $r | Get-NetFirewallAddressFilter -ErrorAction Stop
      if (@($address.RemoteAddress) -notcontains 'Any') { continue }
      $port = $r | Get-NetFirewallPortFilter -ErrorAction Stop
      $app = $r | Get-NetFirewallApplicationFilter -ErrorAction Stop
      $service = $r | Get-NetFirewallServiceFilter -ErrorAction Stop
      $found++
      Chk $false $r.DisplayName "profiles=$($r.Profile); protocol=$($port.Protocol); local ports=$($port.LocalPort -join ', '); program=$($app.Program); service=$($service.Service); source=$($r.PolicyStoreSource)"
    } catch { "ERROR: Rule $($r.Name): $($_.Exception.Message)" }
  }
  "[INFO] $found broad-source rules; flags request review, not proof of Internet reachability. Block rules and other filters still apply."
} catch { "ERROR: Cannot read firewall policy: $($_.Exception.Message)" }""",
      requires="NetSecurity module", admin=True, tags=('diagnostic', 'security', 'network')),
    S("ops-task-last-result-summary", "Scheduled task last-result summary",
      "Groups task outcomes by result code and shows nonzero results with last/next run times; scheduler status codes are not automatically failures.",
      need_cmd("Get-ScheduledTask", "ScheduledTasks module") + need_admin("all task folders require elevation") + r"""try {
  $rows = @(foreach ($t in Get-ScheduledTask -ErrorAction Stop) {
    try {
      $i = $t | Get-ScheduledTaskInfo -ErrorAction Stop
      $code = [long]$i.LastTaskResult
      [pscustomobject]@{ Task="$($t.TaskPath)$($t.TaskName)"; State=$t.State; Result=('0x{0:X8}' -f ($code -band 4294967295L)); Code=$code; LastRun=$i.LastRunTime; NextRun=$i.NextRunTime; Missed=$i.NumberOfMissedRuns }
    } catch { "ERROR: Task $($t.TaskName): $($_.Exception.Message)" }
  })
  $valid = @($rows | Where-Object { $_ -isnot [string] }); $rows | Where-Object { $_ -is [string] }
  $valid | Group-Object Result | Sort-Object Count -Descending | Select-Object Name, Count | Format-Table -AutoSize | Out-String -Width 220
  $valid | Where-Object Code -ne 0 | Sort-Object LastRun -Descending | Select-Object Task, State, Result, LastRun, NextRun, Missed | Format-Table -AutoSize -Wrap | Out-String -Width 220
  '[INFO] 0x00000000 success; 0x00041301 running; 0x00041303 never run. Review other results in task context.'
} catch { "ERROR: Cannot enumerate tasks: $($_.Exception.Message)" }""",
      requires="ScheduledTasks module", admin=True, tags=('diagnostic', 'triage')),
    S("ops-printer-queue-depth", "Print job counts and oldest queued job",
      "Counts jobs across all printers and reports the oldest submission to locate blocked queues without changing jobs.",
      need_cmd("Get-Printer", "PrintManagement module") + need_admin("all print jobs may require elevation") + r"""try { $printers = @(Get-Printer -ErrorAction Stop) }
catch { "ERROR: Cannot enumerate printers: $($_.Exception.Message)"; return }
if (-not $printers.Count) { '[INFO] No printers configured.' }
$total = 0; $failed = 0
foreach ($p in $printers) {
  try {
    $jobs = @(Get-PrintJob -PrinterName $p.Name -ErrorAction Stop); $total += $jobs.Count
    $oldest = $jobs | Sort-Object SubmittedTime | Select-Object -First 1
    [pscustomobject]@{ Printer=$p.Name; Jobs=$jobs.Count; PrinterStatus=$p.PrinterStatus; OldestSubmitted=$oldest.SubmittedTime; OldestStatus=$oldest.JobStatus } | Format-List | Out-String -Width 220
  } catch { $failed++; "ERROR: Printer $($p.Name): $($_.Exception.Message)" }
}
"[INFO] Visible jobs: $total; unreadable queues: $failed. Counts exclude queues that could not be read."
""",
      requires="PrintManagement module", admin=True, tags=('diagnostic', 'triage')),
    S("ops-iis-log-disk-footprint", "IIS per-site log disk footprint",
      "Measures W3C log files per configured site directory and shows the largest file to identify log-driven disk growth.",
      need_cmd("Get-Website", "IIS WebAdministration module") + need_admin("IIS configuration and logs require elevation") + r"""try { $sites = @(Get-Website -ErrorAction Stop) }
catch { "ERROR: Cannot enumerate IIS sites: $($_.Exception.Message)"; return }
foreach ($site in $sites) {
  try {
    $folder = Join-Path ([Environment]::ExpandEnvironmentVariables($site.logFile.directory)) "W3SVC$($site.Id)"
    if (-not (Test-Path -LiteralPath $folder -ErrorAction Stop)) { "[INFO] $($site.Name): no log folder at $folder"; continue }
    $files = @(Get-ChildItem -LiteralPath $folder -Filter *.log -File -Recurse -ErrorAction Stop)
    $largest = $files | Sort-Object Length -Descending | Select-Object -First 1
    [pscustomobject]@{ Site=$site.Name; Folder=$folder; Files=$files.Count; TotalGB=[math]::Round(($files | Measure-Object Length -Sum).Sum/1GB,3); LargestFile=$largest.FullName; LargestMB=[math]::Round($largest.Length/1MB,1) } | Format-List | Out-String -Width 220
  } catch { "ERROR: Logs for $($site.Name): $($_.Exception.Message)" }
}
'[INFO] Per-site W3C folders only; centralized and custom logging destinations require separate review.'""",
      requires="IIS WebAdministration module", admin=True, timeout=300, tags=('inventory', 'storage', 'triage')),
    S("ops-event-log-retained-coverage", "Core event log sizes, retention and coverage",
      "Shows configured capacity and actual oldest/newest retained timestamps for System, Application and Security logs to assess investigation coverage.",
      need_admin("Security log coverage requires elevation") + r"""foreach ($name in 'System','Application','Security') {
  try {
    $config = Get-WinEvent -ListLog $name -ErrorAction Stop
    $config | Select-Object LogName, IsEnabled, LogMode, FileSize, MaximumSizeInBytes, RecordCount | Format-List | Out-String -Width 220
    if ($config.RecordCount -eq 0) { "[INFO] ${name}: no retained records."; continue }
    $oldest = Get-WinEvent -LogName $name -Oldest -MaxEvents 1 -ErrorAction Stop
    $newest = Get-WinEvent -LogName $name -MaxEvents 1 -ErrorAction Stop
    if (-not $oldest.TimeCreated) { "[INFO] ${name}: oldest timestamp unavailable."; continue }
    "[INFO] ${name}: oldest=$($oldest.TimeCreated); newest=$($newest.TimeCreated); days since oldest=$([math]::Round(((Get-Date)-$oldest.TimeCreated).TotalDays,1))"
  } catch { "ERROR: Log ${name}: $($_.Exception.Message)" }
}
'[INFO] Retained time span does not establish continuous auditing or that required events were enabled.'""",
      admin=True, tags=('inventory', 'security', 'compliance')),
    S("ops_performance_snapshot", "Server performance baseline snapshot",
      "One timestamped CPU, memory, disk and network sample using locale-independent CIM counters.",
      r""""[INFO] Snapshot: $((Get-Date).ToString('o')) on $env:COMPUTERNAME"
foreach ($class in 'Win32_PerfFormattedData_PerfOS_Processor','Win32_OperatingSystem','Win32_PerfFormattedData_PerfDisk_LogicalDisk','Win32_PerfFormattedData_Tcpip_NetworkInterface') {
  try {
    "== $class"
    $rows = @(Get-CimInstance -ClassName $class -ErrorAction Stop)
    if (-not $rows.Count) { '[INFO] No instances returned.'; continue }
    $selected = switch ($class) {
      'Win32_PerfFormattedData_PerfOS_Processor' { $rows | Where-Object Name -eq '_Total' | Select-Object Name, PercentProcessorTime }
      'Win32_OperatingSystem' { $rows | Select-Object TotalVisibleMemorySize, FreePhysicalMemory, LastBootUpTime }
      'Win32_PerfFormattedData_PerfDisk_LogicalDisk' { $rows | Select-Object Name, DiskBytesPersec, AvgDisksecPerRead, AvgDisksecPerWrite, CurrentDiskQueueLength }
      'Win32_PerfFormattedData_Tcpip_NetworkInterface' { $rows | Select-Object Name, BytesReceivedPersec, BytesSentPersec, PacketsOutboundErrors, PacketsReceivedErrors }
    }
    $selected | Format-Table -AutoSize | Out-String -Width 220
  } catch { "ERROR: ${class}: $($_.Exception.Message)" }
}
'[INFO] Memory fields are KB; disk latency is seconds. A single sample is not a sustained-load diagnosis.'""",
      tags=('performance', 'baseline', 'hardware')),
    S("ops_shadow_copy_inventory", "VSS shadow copies and storage usage",
      "Shadow copy ages, source volumes and aggregate VSS allocation; snapshots alone do not prove backup success.",
      need_admin("VSS inventory may be refused unelevated") + r"""foreach ($class in 'Win32_ShadowCopy','Win32_ShadowStorage') {
  try {
    "== $class"
    $rows = @(Get-CimInstance -ClassName $class -ErrorAction Stop)
    if (-not $rows.Count) { '[INFO] No instances returned; VSS may not be configured.'; continue }
    if ($class -eq 'Win32_ShadowCopy') {
      $rows | Sort-Object InstallDate -Descending | Select-Object ID, VolumeName, InstallDate, @{n='AgeDays';e={if ($_.InstallDate) { [math]::Round(((Get-Date) - $_.InstallDate).TotalDays, 1) }}}, Persistent, ClientAccessible | Format-Table -AutoSize -Wrap | Out-String -Width 220
    } else {
      $rows | Select-Object Volume, DiffVolume, UsedSpace, AllocatedSpace, MaxSpace | Format-List | Out-String -Width 220
    }
  } catch { "ERROR: ${class}: $($_.Exception.Message)" }
}
'[INFO] Storage sizes are bytes per volume association, not per individual snapshot; these are not off-machine backups.'""",
      admin=True, tags=('inventory', 'storage', 'triage')),
    S("ops_installed_role_summary", "Installed server roles and feature counts",
      "Installed roles and features grouped by type, with state totals including removed payloads.",
      need_cmd("Get-WindowsFeature", "the ServerManager module (Windows Server)") + r"""try {
  $features = @(Get-WindowsFeature -ErrorAction Stop)
  $features | Group-Object InstallState | Select-Object Name, Count | Format-Table -AutoSize | Out-String -Width 220
  $features | Where-Object Installed | Sort-Object FeatureType, Name | Select-Object FeatureType, Name, DisplayName, InstallState | Format-Table -AutoSize -Wrap | Out-String -Width 220
} catch { "ERROR: Cannot read server roles: $($_.Exception.Message)" }""",
      requires="ServerManager module", tags=('inventory', 'baseline', 'compliance')),
    S("ops_backup_event_history", "Windows Server Backup event history",
      "Recent backup events with IDs and severity; missing logs and refused reads are distinct from no events.",
      CHK + r"""$log = 'Microsoft-Windows-Backup'
try { $config = Get-WinEvent -ListLog $log -ErrorAction Stop }
catch { "NOT AVAILABLE: Cannot access backup log: $($_.Exception.Message)"; return }
if (-not $config.IsEnabled) { Chk $null 'Backup event history' 'log is disabled'; return }
try {
  $events = @(Get-WinEvent -FilterHashtable @{LogName=$log; StartTime=(Get-Date).AddDays(-14)} -MaxEvents 100 -ErrorAction Stop)
  '[INFO] Newest 100 backup events within 14 days; absence of errors does not establish backup success.'
  $events | ForEach-Object { '[INFO] {0:yyyy-MM-dd HH:mm} ID={1} level={2} {3}' -f $_.TimeCreated, $_.Id, $_.LevelDisplayName, (($_.Message -split "`r?`n")[0]) }
} catch {
  if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { Chk $null 'Backup event history' 'no events in 14 days; backup protection is unverified' }
  else { "ERROR: Cannot read backup history: $($_.Exception.Message)" }
}""",
      admin=True, note="backup event history may require elevation",
      tags=('diagnostic', 'storage', 'health')),
    S("ops_firewall_log_drops", "Firewall log dropped-traffic summary",
      "Top dropped source/destination flows from the last 20,000 lines of each enabled firewall log.",
      need_admin("firewall log files may be unreadable unelevated") + r"""try { $profiles = @(Get-NetFirewallProfile -ErrorAction Stop) }
catch { "ERROR: Cannot read firewall logging settings: $($_.Exception.Message)"; return }
$paths = @{}
foreach ($profile in $profiles) {
  "[INFO] $($profile.Name): blocked logging=$($profile.LogBlocked), allowed logging=$($profile.LogAllowed)"
  if ($profile.LogBlocked -ne 'True') { continue }
  $path = [Environment]::ExpandEnvironmentVariables($profile.LogFileName)
  if (-not $path -or $paths.ContainsKey($path)) { continue }; $paths[$path] = $true
  try {
    $header = @(Get-Content -LiteralPath $path -TotalCount 100 -ErrorAction Stop | Where-Object { $_ -like '#Fields:*' })
    if (-not $header.Count) { "[WARN] No field header in $path; cannot parse safely."; continue }
    $fields = ($header[-1] -replace '^#Fields:\s*','') -split '\s+'
    $required = @('action','protocol','src-ip','dst-ip','dst-port')
    if (@($required | Where-Object { $fields -notcontains $_ }).Count) { "[WARN] Unsupported field layout: $path"; continue }
    $indexes = @{}; foreach ($name in $required) { $indexes[$name] = [array]::IndexOf($fields, $name) }
    $flows = @(Get-Content -LiteralPath $path -Tail 20000 -ErrorAction Stop | Where-Object { $_ -and $_ -notlike '#*' } | ForEach-Object {
      $parts = $_ -split '\s+'
      if ($parts.Count -eq $fields.Count -and $parts[$indexes['action']] -eq 'DROP') {
        '{0} {1} -> {2}:{3}' -f $parts[$indexes['protocol']], $parts[$indexes['src-ip']], $parts[$indexes['dst-ip']], $parts[$indexes['dst-port']]
      }
    })
    "[INFO] $path -- sampled drops: $($flows.Count); limited to the current file tail, not a time window"
    $flows | Group-Object | Sort-Object Count -Descending | Select-Object -First 20 Count, Name | Format-Table -AutoSize | Out-String -Width 220
  } catch { "ERROR: Cannot read firewall log ${path}: $($_.Exception.Message)" }
}
if (-not $paths.Count) { '[INFO] No profiles configured to log dropped traffic; traffic history is unavailable.' }""",
      admin=True, tags=('diagnostic', 'security', 'network')),
    # ---- Log parsing (18) --------------------------------------------------------------------
    LP("lp_account_lockout_source", "Account lockouts and where they came from",
       "4740 lockout events with the caller computer -- on a DC this names the machine with "
       "the stale password. Runs anywhere; the answer is on the PDC emulator.",
       need_admin("the Security log is only readable elevated") + EV +
       r"""$r = @(EvRead @{LogName='Security'; Id=4740} 30 500)
if (-not $r.Count -or $r[0] -is [string]) { $r; return }
$r | ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1,-25} locked out, caller computer: {2}' -f $_.TimeCreated, $_.Properties[0].Value, $_.Properties[1].Value }""",
       admin=True, tags=('diagnostic', 'health', 'security', 'triage')),
    LP("lp_kerberos_failures", "Kerberos pre-authentication failures",
       "4771 failures by account, client address and failure code (0x18 = bad password, "
       "0x12 = disabled/locked).",
       need_admin("the Security log is only readable elevated") +
       r"""$e = Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4771; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 5000 -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No Kerberos pre-auth failures in 7 days (or this is not a DC).'; return }
$e | ForEach-Object { $d = @{}; ([xml]$_.ToXml()).Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }; "$($d.TargetUserName) from $($d.IpAddress) code $($d.Status)" } | Group-Object | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { '[WARN] x{0,-5} {1}' -f $_.Count, $_.Name }""",
       admin=True, tags=('diagnostic', 'health', 'security', 'triage')),
    LP("lp_ntlm_failures", "NTLM authentication failures",
       "4776 credential validation failures by account and workstation.",
       need_admin("the Security log is only readable elevated") +
       r"""$e = Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4776; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 5000 -ErrorAction SilentlyContinue | Where-Object { $_.Properties[3].Value -ne 0 }
if (-not $e) { '[OK]   No NTLM validation failures in 7 days.'; return }
$e | ForEach-Object { "$($_.Properties[1].Value) from $($_.Properties[2].Value) error 0x$('{0:X}' -f $_.Properties[3].Value)" } | Group-Object | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { '[WARN] x{0,-5} {1}' -f $_.Count, $_.Name }""",
       admin=True, tags=('diagnostic', 'health', 'security', 'triage')),
    LP("lp_logons_by_type", "Logons by account and type (24 h)",
       "Successful logons (4624) in the last day grouped by account and logon type -- service, "
       "batch, network, interactive, RDP.",
       need_admin("the Security log is only readable elevated") +
       r"""$t = @{'2'='interactive';'3'='network';'4'='batch';'5'='service';'7'='unlock';'8'='cleartext';'9'='new creds';'10'='RDP';'11'='cached'}
Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4624; StartTime=(Get-Date).AddDays(-1)} -MaxEvents 20000 -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Properties[6].Value)\$($_.Properties[5].Value) [$($t[[string]$_.Properties[8].Value])]" } | Group-Object | Sort-Object Count -Descending | Select-Object -First 40 | ForEach-Object { '{0,7}  {1}' -f $_.Count, $_.Name }""",
       admin=True, timeout=300, tags=('diagnostic', 'security', 'triage')),
    LP("lp_privileged_logons", "Special-privilege logons (4672)",
       "Accounts that received admin-equivalent privileges at logon in the last day, grouped.",
       need_admin("the Security log is only readable elevated") +
       r"""Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4672; StartTime=(Get-Date).AddDays(-1)} -MaxEvents 20000 -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Properties[2].Value)\$($_.Properties[1].Value)" } | Group-Object | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { '{0,7}  {1}' -f $_.Count, $_.Name }""",
       admin=True, timeout=300, tags=('diagnostic', 'security', 'triage')),
    LP("lp_process_creation", "Process creation log (4688)",
       "The last 60 processes started, with parent and command line when command-line auditing "
       "is on -- needs Process Creation auditing.",
       need_admin("the Security log is only readable elevated") + EV +
       r"""$r = @(EvRead @{LogName='Security'; Id=4688} 1 60)
if (-not $r.Count -or $r[0] -is [string]) { $r; if ($r[0] -match 'No matching') { 'NOTE: Process Creation auditing may be off (Security Dashboard > Audit & Logging).' }; return }
$r | ForEach-Object { $d = @{}; ([xml]$_.ToXml()).Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }; '{0:HH:mm:ss} {1} <- {2}  {3}' -f $_.TimeCreated, $d.NewProcessName, $d.ParentProcessName, $d.CommandLine }""",
       admin=True, tags=('diagnostic', 'health', 'security', 'triage')),
    LP("lp_cbs_errors", "CBS log errors (servicing)",
       "Error lines in CBS.log -- failed updates, corruption found by SFC/DISM.",
       r"""$f = "$env:SystemRoot\Logs\CBS\CBS.log"
if (-not (Test-Path $f)) { 'NOT AVAILABLE: CBS.log is not present (rolled into CbsPersist cabs).'; return }
$l = Select-String -Path $f -Pattern ', Error ' -ErrorAction SilentlyContinue
if (-not $l) { '[OK]   No error lines in the current CBS.log.'; return }
$l | ForEach-Object { ($_.Line -replace '^\S+ \S+, Error\s+','') -replace '\[HRESULT = (0x\w+).*','[$1]' } | Group-Object | Sort-Object Count -Descending | Select-Object -First 25 | ForEach-Object { '[WARN] x{0,-4} {1}' -f $_.Count, $_.Name.Substring(0, [Math]::Min(170, $_.Name.Length)) }""",
       note="CBS.log may be unreadable unelevated", tags=('diagnostic', 'health', 'triage')),
    LP("lp_setupapi_failures", "Driver install failures (setupapi)",
       "Failed device or driver installs recorded in setupapi.dev.log, with the device.",
       r"""$f = "$env:SystemRoot\INF\setupapi.dev.log"
if (-not (Test-Path $f)) { 'NOT AVAILABLE: setupapi.dev.log is not present.'; return }
$hits = Select-String -Path $f -Pattern '!!!|Exit status: FAILURE|Device not started' -ErrorAction SilentlyContinue | Select-Object -Last 40
if (-not $hits) { '[OK]   No failures in setupapi.dev.log.'; return }
$hits | ForEach-Object { "[WARN] line $($_.LineNumber): $($_.Line.Trim())" }""",
       timeout=180, tags=('diagnostic', 'health', 'triage')),
    LP("lp_dism_errors", "DISM log errors",
       "Error lines in dism.log grouped.",
       r"""$f = "$env:SystemRoot\Logs\DISM\dism.log"
if (-not (Test-Path $f)) { 'NOT AVAILABLE: dism.log is not present.'; return }
$l = Select-String -Path $f -Pattern ', Error ' -ErrorAction SilentlyContinue
if (-not $l) { '[OK]   No error lines in dism.log.'; return }
$l | ForEach-Object { $_.Line -replace '^\S+ \S+, Error\s+','' } | Group-Object | Sort-Object Count -Descending | Select-Object -First 20 | ForEach-Object { '[WARN] x{0,-4} {1}' -f $_.Count, $_.Name.Substring(0, [Math]::Min(170, $_.Name.Length)) }""",
       note="dism.log may be unreadable unelevated", tags=('diagnostic', 'health', 'triage')),
    LP("lp_windowsupdate_etl", "Windows Update client errors",
       "Errors from the Windows Update client operational log grouped.",
       EV + "EvSum @{LogName='Microsoft-Windows-WindowsUpdateClient/Operational'; Level=1,2,3} 30", tags=('diagnostic', 'health', 'triage')),
    LP("lp_iis_errors_by_url", "IIS 5xx errors by URL (7 days)",
       "Server errors in the last week's IIS logs grouped by URL and status.",
       need_cmd("Get-Website", "the WebAdministration module (IIS)") +
       r"""Get-Website | ForEach-Object { $dir = [Environment]::ExpandEnvironmentVariables($_.logFile.directory) + "\W3SVC$($_.id)"
  Get-ChildItem $dir -Filter *.log -ErrorAction SilentlyContinue | Where-Object LastWriteTime -gt (Get-Date).AddDays(-7) | ForEach-Object { $f = $_.FullName; $fields = (Get-Content $f -TotalCount 10 | Where-Object { $_ -like '#Fields:*' } | Select-Object -Last 1) -replace '^#Fields: ','' -split ' '
    $u = [array]::IndexOf($fields, 'cs-uri-stem'); $s = [array]::IndexOf($fields, 'sc-status')
    Get-Content $f | Where-Object { $_ -notlike '#*' } | ForEach-Object { $p = $_ -split ' '; if ($p[$s] -like '5*') { "$($p[$s]) $($p[$u])" } } } } | Group-Object | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { '[WARN] x{0,-5} {1}' -f $_.Count, $_.Name }""",
       requires="IIS (WebAdministration)", timeout=900, tags=('diagnostic', 'health', 'triage')),
    LP("lp_iis_slow_requests", "Slowest IIS requests today",
       "The 25 slowest requests (time-taken) in each site's newest log.",
       need_cmd("Get-Website", "the WebAdministration module (IIS)") +
       r"""Get-Website | ForEach-Object { $dir = [Environment]::ExpandEnvironmentVariables($_.logFile.directory) + "\W3SVC$($_.id)"; $f = Get-ChildItem $dir -Filter *.log -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $f) { return }; $fields = (Get-Content $f.FullName -TotalCount 10 | Where-Object { $_ -like '#Fields:*' } | Select-Object -Last 1) -replace '^#Fields: ','' -split ' '
  $t = [array]::IndexOf($fields, 'time-taken'); $u = [array]::IndexOf($fields, 'cs-uri-stem'); "== $($_.Name)"
  Get-Content $f.FullName | Where-Object { $_ -notlike '#*' } | ForEach-Object { $p = $_ -split ' '; [pscustomobject]@{ ms=[int]$p[$t]; url=$p[$u] } } | Sort-Object ms -Descending | Select-Object -First 25 | ForEach-Object { '   {0,8} ms  {1}' -f $_.ms, $_.url } }""",
       requires="IIS (WebAdministration)", timeout=900, tags=('diagnostic', 'performance', 'triage')),
    LP("lp_dhcp_audit_week", "DHCP audit logs (7 days)",
       "All seven DHCP audit logs summarised by event type and the busiest MAC addresses.",
       need_cmd("Get-DhcpServerAuditLog", "the DhcpServer module (DHCP Server)") +
       r"""$p = (Get-DhcpServerAuditLog).Path
$rows = Get-ChildItem $p -Filter 'DhcpSrvLog-*.log' -ErrorAction SilentlyContinue | ForEach-Object { Get-Content $_.FullName | Where-Object { $_ -match '^\d+,' } | ForEach-Object { , ($_ -split ',') } }
$names = @{'10'='new lease';'11'='renewed';'12'='released';'13'='conflict';'14'='scope full';'15'='NAK';'16'='deleted';'17'='expired'}
$rows | ForEach-Object { $_[0] } | Group-Object | Sort-Object Count -Descending | ForEach-Object { '{0,7}  {1,3} {2}' -f $_.Count, $_.Name, $names[$_.Name] }
''; 'Busiest MACs:'; $rows | Where-Object { $_.Count -gt 6 -and $_[6] } | ForEach-Object { "$($_[6]) $($_[5])" } | Group-Object | Sort-Object Count -Descending | Select-Object -First 15 | ForEach-Object { '{0,7}  {1}' -f $_.Count, $_.Name }""",
       requires="DhcpServer module", tags=('diagnostic', 'triage')),
    LP("lp_dns_debug_top", "DNS debug log: top queried names",
       "Top 30 queried names and top clients from the DNS server debug log, when it is on.",
       need_cmd("Get-DnsServerDiagnostics", "the DnsServer module (DNS Server)") +
       r"""$f = (Get-DnsServerDiagnostics).LogFilePath
if (-not $f -or -not (Test-Path $f)) { '[INFO] DNS debug logging is off (no log file).'; return }
$q = Get-Content $f -Tail 50000 | Where-Object { $_ -match ' Rcv ' -and $_ -match '\(\d+\)' }
'Top names:'; $q | ForEach-Object { (($_ -split ' ')[-1]) -replace '\(\d+\)','.' -replace '^\.|\.$','' } | Group-Object | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { '{0,7}  {1}' -f $_.Count, $_.Name }
''; 'Top clients:'; $q | ForEach-Object { ($_ -split '\s+')[7] } | Group-Object | Sort-Object Count -Descending | Select-Object -First 15 | ForEach-Object { '{0,7}  {1}' -f $_.Count, $_.Name }""",
       requires="DnsServer module", tags=('diagnostic', 'triage')),
    LP("lp_rdp_gateway", "RD Gateway connections",
       "Successful and failed RD Gateway connections (302/303/304) by user and source.",
       EV + r"""$r = @(EvRead @{LogName='Microsoft-Windows-TerminalServices-Gateway/Operational'; Id=302,303,304} 7 3000)
if (-not $r.Count -or $r[0] -is [string]) { $r; return }
$r | ForEach-Object { "$($_.Id) $($_.Properties[0].Value) from $($_.Properties[1].Value)" } | Group-Object | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { '{0,6}  {1}' -f $_.Count, $_.Name }""",
       requires="RD Gateway role", tags=('diagnostic', 'health', 'triage')),
    LP("lp_ps_transcripts", "PowerShell transcripts",
       "Transcript files written by PowerShell transcription (if configured), newest first.",
       r"""$d = (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\PowerShell\Transcription' -ErrorAction SilentlyContinue).OutputDirectory
if (-not $d) { '[INFO] PowerShell transcription is not configured by policy.'; return }
Get-ChildItem $d -Recurse -Filter 'PowerShell_transcript*' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 40 | ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1,8:N0} KB  {2}' -f $_.LastWriteTime, ($_.Length/1KB), $_.FullName }""", tags=('diagnostic', 'security', 'triage')),
    LP("lp_sysmon_summary", "Sysmon event summary",
       "Sysmon events grouped by type over 24 hours (if Sysmon is installed).",
       EV + "EvSum @{LogName='Microsoft-Windows-Sysmon/Operational'} 1 20000",
       admin=True, tags=('diagnostic', 'health', 'security', 'triage')),
    LP("lp_event_rates", "Busiest event logs (24 h)",
       "How many events each log wrote in the last day -- a log suddenly writing thousands "
       "is a fault in progress.",
       r"""Get-WinEvent -ListLog * -ErrorAction SilentlyContinue | Where-Object { $_.RecordCount -and $_.LastWriteTime -gt (Get-Date).AddDays(-1) } | ForEach-Object { $n = (Get-WinEvent -FilterHashtable @{LogName=$_.LogName; StartTime=(Get-Date).AddDays(-1)} -MaxEvents 20000 -ErrorAction SilentlyContinue | Measure-Object).Count; [pscustomobject]@{ Log=$_.LogName; Last24h=$n } } | Sort-Object Last24h -Descending | Select-Object -First 25 | Format-Table -AutoSize | Out-String -Width 220""",
       note="some logs are only readable elevated", timeout=600, tags=('diagnostic', 'performance', 'triage')),
    # ---- Fleet (remote servers) (20) ---------------------------------------------------------
    FL("fl_free_space", "Free space on every server",
       "Fixed-disk free space per server, low ones flagged (below 10% or 10 GB).",
       FLEET + r"""Each { param($t, $c) Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -CimSession $c | ForEach-Object { $pct = if ($_.Size) { 100 * $_.FreeSpace / $_.Size } else { 0 }
  '{0} {1,-20} {2} {3,8:N1} GB free of {4,8:N1} GB ({5,4:N1}%)' -f $(if ($pct -lt 10 -or $_.FreeSpace -lt 10GB) {'[BAD] '} elseif ($pct -lt 20) {'[WARN]'} else {'[OK]  '}), $t, $_.DeviceID, ($_.FreeSpace/1GB), ($_.Size/1GB), $pct } }""", tags=('diagnostic', 'health', 'storage', 'fleet')),
    FL("fl_uptime", "Uptime of every server",
       "Last boot and uptime per server -- servers not rebooted since the last Patch Tuesday "
       "are not patched.",
       FLEET + r"""Each { param($t, $c) $o = Get-CimInstance Win32_OperatingSystem -CimSession $c; $up = (Get-Date) - $o.LastBootUpTime
  '{0} {1,-20} up {2,4}d {3,2}h  (since {4})' -f $(if ($up.Days -gt 35) {'[WARN]'} else {'[OK]  '}), $t, $up.Days, $up.Hours, $o.LastBootUpTime }""", tags=('diagnostic', 'health', 'fleet')),
    FL("fl_os_versions", "OS version of every server",
       "Edition, version and build per server.",
       FLEET + r"""Each { param($t, $c) $o = Get-CimInstance Win32_OperatingSystem -CimSession $c; '{0,-20} {1}  build {2}  {3}' -f $t, $o.Caption, $o.BuildNumber, $o.OSArchitecture }""", tags=('inventory', 'baseline', 'fleet')),
    FL("fl_last_patch", "Newest hotfix on every server",
       "The most recent installed update per server and its age.",
       FLEET + r"""Each { param($t, $c) $h = Get-CimInstance Win32_QuickFixEngineering -CimSession $c | Where-Object InstalledOn | Sort-Object InstalledOn -Descending | Select-Object -First 1
  $age = if ($h) { ((Get-Date) - $h.InstalledOn).Days } else { $null }
  '{0} {1,-20} {2} installed {3} ({4} days ago)' -f $(if ($age -gt 45) {'[WARN]'} else {'[OK]  '}), $t, $h.HotFixID, $h.InstalledOn, $age }""", tags=('diagnostic', 'health', 'fleet')),
    FL("fl_stopped_auto_services", "Stopped automatic services on every server",
       "Automatic services not running, per server. Cleanly stopped services are excluded only "
       "when registry Start=2 and configured triggers are confirmed.",
       FLEET + r"""Each { param($t, $c)
  Get-CimInstance Win32_Service -Filter "StartMode='Auto' AND State<>'Running'" -CimSession $c -ErrorAction Stop | ForEach-Object {
    $service = $_; $triggerStart = $false
    if ($service.State -eq 'Stopped' -and $service.ExitCode -eq 0) {
      try {
        $key = "SYSTEM\CurrentControlSet\Services\$($service.Name)"
        $start = Invoke-CimMethod -CimSession $c -Namespace root\cimv2 -ClassName StdRegProv -MethodName GetDWORDValue -Arguments @{hDefKey=[uint32]2147483650; sSubKeyName=$key; sValueName='Start'} -ErrorAction Stop
        $triggers = Invoke-CimMethod -CimSession $c -Namespace root\cimv2 -ClassName StdRegProv -MethodName EnumKey -Arguments @{hDefKey=[uint32]2147483650; sSubKeyName="$key\TriggerInfo"} -ErrorAction Stop
        $triggerStart = $start.ReturnValue -eq 0 -and $start.uValue -eq 2 -and $triggers.ReturnValue -eq 0 -and @($triggers.sNames | Where-Object { $_ -match '^\d+$' }).Count -gt 0
        if ($start.ReturnValue -ne 0 -or $triggers.ReturnValue -notin 0,2) {
          "ERROR: $t / $($service.Name) -- trigger configuration unknown (Start=$($start.ReturnValue), TriggerInfo=$($triggers.ReturnValue))"
        }
      } catch { "ERROR: $t / $($service.Name) -- trigger configuration: $($_.Exception.Message)" }
    }
    if (-not $triggerStart) { '[WARN] {0,-20} {1,-35} state {2} exit {3}' -f $t, $service.Name, $service.State, $service.ExitCode }
  }
}
'[INFO] Only confirmed automatic trigger-start services stopped with exit code 0 are excluded.'""", tags=('diagnostic', 'health', 'triage', 'fleet')),
    FL("fl_installed_apps", "Installed applications on every server",
       "Installed programs per server from Win32_InstalledWin32Program -- never Win32_Product, "
       "which triggers an MSI consistency check (and repairs) on every package it touches.",
       FLEET + r"""Each { param($t, $c) "== $t"; Get-CimInstance Win32_InstalledWin32Program -CimSession $c -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { '   {0,-60} {1,-20} {2}' -f $_.Name, $_.Version, $_.Vendor } }""",
       timeout=600, tags=('inventory', 'fleet')),
    FL("fl_app_search", "Find servers that have a program",
       "Which servers have a program whose name matches the pattern in the first line of "
       "servers-app.txt (default: 'Java').",
       FLEET + r"""$pf = Join-Path $(if ($env:WCT_SCRIPTS_DIR) { $env:WCT_SCRIPTS_DIR } else { Join-Path $env:APPDATA 'WindowsTweaker\scripts' }) 'servers-app.txt'
$pat = if (Test-Path $pf) { (Get-Content $pf | Select-Object -First 1).Trim() } else { 'Java' }
"Looking for: *$pat*"
Each { param($t, $c) Get-CimInstance Win32_InstalledWin32Program -CimSession $c -ErrorAction SilentlyContinue | Where-Object Name -like "*$pat*" | ForEach-Object { '{0,-20} {1} {2}' -f $t, $_.Name, $_.Version } }""",
       timeout=600, tags=('diagnostic', 'triage', 'fleet')),
    FL("fl_logged_on", "Who is logged on to every server",
       "The interactive user on each server (Win32_ComputerSystem) and explorer.exe owners.",
       FLEET + r"""Each { param($t, $c) $u = (Get-CimInstance Win32_ComputerSystem -CimSession $c).UserName
  $ex = Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" -CimSession $c | ForEach-Object { $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -CimSession $c; "$($o.Domain)\$($o.User)" }
  '{0,-20} console: {1}   sessions: {2}' -f $t, $u, (($ex | Sort-Object -Unique) -join ', ') }""", tags=('inventory', 'fleet')),
    FL("fl_cpu_mem", "CPU and memory load on every server",
       "Current CPU load and memory use per server.",
       FLEET + r"""Each { param($t, $c) $cpu = (Get-CimInstance Win32_Processor -CimSession $c | Measure-Object LoadPercentage -Average).Average; $o = Get-CimInstance Win32_OperatingSystem -CimSession $c
  $mem = 100 * (1 - $o.FreePhysicalMemory / $o.TotalVisibleMemorySize)
  '{0} {1,-20} CPU {2,5:N1}%   memory {3,5:N1}% of {4:N1} GB' -f $(if ($cpu -gt 85 -or $mem -gt 90) {'[WARN]'} else {'[OK]  '}), $t, $cpu, $mem, ($o.TotalVisibleMemorySize/1MB) }""", tags=('diagnostic', 'health', 'performance', 'fleet')),
    FL("fl_pending_reboot", "Pending restart on every server",
       "CBS RebootPending and Windows Update RebootRequired per server, read through the "
       "remote registry provider (StdRegProv).",
       FLEET + r"""$HKLM = [uint32]2147483650
Each { param($t, $c) $cbs = Invoke-CimMethod -CimSession $c -Namespace root\cimv2 -ClassName StdRegProv -MethodName EnumKey -Arguments @{hDefKey=$HKLM; sSubKeyName='SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'} -ErrorAction Stop
  $wu = Invoke-CimMethod -CimSession $c -Namespace root\cimv2 -ClassName StdRegProv -MethodName EnumKey -Arguments @{hDefKey=$HKLM; sSubKeyName='SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'} -ErrorAction Stop
  $cbsState = if ($cbs.ReturnValue -eq 0) { $true } elseif ($cbs.ReturnValue -eq 2) { $false } elseif ($cbs.ReturnValue -eq 5) { 'unknown (access denied)' } else { "unknown (registry return code $($cbs.ReturnValue))" }
  $wuState = if ($wu.ReturnValue -eq 0) { $true } elseif ($wu.ReturnValue -eq 2) { $false } elseif ($wu.ReturnValue -eq 5) { 'unknown (access denied)' } else { "unknown (registry return code $($wu.ReturnValue))" }
  $tag = if ($cbs.ReturnValue -eq 0 -or $wu.ReturnValue -eq 0) { '[WARN]' } elseif ($cbs.ReturnValue -eq 2 -and $wu.ReturnValue -eq 2) { '[OK]  ' } else { '[INFO]' }
  '{0} {1,-20} CBS={2} WU={3}' -f $tag, $t, $cbsState, $wuState }""", tags=('diagnostic', 'health', 'triage', 'fleet')),
    FL("fl_shares", "Shares on every server",
       "Non-administrative shares per server.",
       FLEET + r"""Each { param($t, $c) Get-CimInstance Win32_Share -CimSession $c | Where-Object { $_.Type -eq 0 } | ForEach-Object { '{0,-20} {1,-25} {2}' -f $t, $_.Name, $_.Path } }""", tags=('inventory', 'fleet')),
    FL("fl_event_errors", "System errors on every server (24 h)",
       "Count of System-log errors per server in the last day, with the noisiest source.",
       FLEET + r"""Each { param($t, $c) $e = Get-CimInstance Win32_NTLogEvent -CimSession $c -Filter "Logfile='System' AND EventType=1 AND TimeGenerated>='$((Get-Date).AddDays(-1).ToString('yyyyMMddHHmmss')).000000+000'" -ErrorAction SilentlyContinue
  $top = $e | Group-Object SourceName | Sort-Object Count -Descending | Select-Object -First 1
  '{0} {1,-20} {2,4} error(s)  top: {3}' -f $(if (@($e).Count -gt 20) {'[WARN]'} else {'[OK]  '}), $t, @($e).Count, $(if ($top) { "$($top.Name) x$($top.Count)" } else { '-' }) }""",
       timeout=600, tags=('diagnostic', 'health', 'triage', 'fleet')),
    FL("fl_time_offset", "Clock offset of every server",
       "Each server's clock offset against this computer (w32tm /stripchart).",
       r"""$list = Join-Path $(if ($env:WCT_SCRIPTS_DIR) { $env:WCT_SCRIPTS_DIR } else { Join-Path $env:APPDATA 'WindowsTweaker\scripts' }) 'servers.txt'
$targets = if (Test-Path $list) { Get-Content $list | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') } } else { @($env:COMPUTERNAME) }
foreach ($t in $targets) { $o = w32tm /stripchart /computer:$t /samples:1 /dataonly 2>&1 | Select-Object -Last 1; '{0,-20} {1}' -f $t, $o }""",
       timeout=300, tags=('inventory', 'network', 'fleet')),
    FL("fl_bios_serial", "Hardware model and serial of every server",
       "Manufacturer, model, serial and BIOS version per server -- the asset list.",
       FLEET + r"""Each { param($t, $c) $cs = Get-CimInstance Win32_ComputerSystem -CimSession $c; $b = Get-CimInstance Win32_BIOS -CimSession $c
  '{0,-20} {1} {2}  serial {3}  BIOS {4}' -f $t, $cs.Manufacturer, $cs.Model, $b.SerialNumber, $b.SMBIOSBIOSVersion }""", tags=('inventory', 'baseline', 'fleet')),
    FL("fl_ip_config", "IP configuration of every server",
       "IPs, gateway and DNS servers per server.",
       FLEET + r"""Each { param($t, $c) Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=TRUE' -CimSession $c | ForEach-Object { '{0,-20} {1,-30} gw {2,-15} dns {3}' -f $t, ($_.IPAddress -join ','), ($_.DefaultIPGateway -join ','), ($_.DNSServerSearchOrder -join ',') } }""", tags=('inventory', 'network', 'fleet')),
    FL("fl_local_admins", "Local Administrators on every server",
       "Members of each server's local Administrators group.",
       FLEET + r"""Each { param($t, $c) $g = Get-CimInstance Win32_Group -Filter "SID='S-1-5-32-544'" -CimSession $c
  Get-CimAssociatedInstance -InputObject $g -ResultClassName Win32_Account -CimSession $c -ErrorAction SilentlyContinue | ForEach-Object { '{0,-20} {1}\{2}' -f $t, $_.Domain, $_.Name } }""", tags=('inventory', 'security', 'fleet')),
    FL("fl_scheduled_tasks", "Non-Microsoft scheduled tasks on every server",
       "Tasks outside \\Microsoft\\ per server with their action.",
       FLEET + r"""Each { param($t, $c) Get-ScheduledTask -CimSession $c -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } | ForEach-Object { '{0,-20} {1}{2}  -> {3}' -f $t, $_.TaskPath, $_.TaskName, (($_.Actions | ForEach-Object { $_.Execute }) -join '; ') } }""", tags=('inventory', 'fleet')),
    FL("fl_certificates_expiring", "Certificates expiring on every server",
       "Machine certificates expiring within 30 days per server (needs WinRM: Invoke-Command, "
       "since the certificate store is not exposed through CIM).",
       r"""$list = Join-Path $(if ($env:WCT_SCRIPTS_DIR) { $env:WCT_SCRIPTS_DIR } else { Join-Path $env:APPDATA 'WindowsTweaker\scripts' }) 'servers.txt'
$targets = if (Test-Path $list) { Get-Content $list | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') } } else { @($env:COMPUTERNAME) }
foreach ($t in $targets) { try { $r = if ($t -eq $env:COMPUTERNAME) { Get-ChildItem Cert:\LocalMachine\My | Where-Object NotAfter -lt (Get-Date).AddDays(30) } else { Invoke-Command -ComputerName $t -ErrorAction Stop -ScriptBlock { Get-ChildItem Cert:\LocalMachine\My | Where-Object NotAfter -lt (Get-Date).AddDays(30) } }
  if (-not $r) { "[OK]   $t -- nothing expiring" } else { $r | ForEach-Object { "[WARN] $t -- $($_.Subject) expires $($_.NotAfter)" } } } catch { "[BAD]  $t -- $($_.Exception.Message)" } }""", tags=('diagnostic', 'health', 'security', 'fleet')),
    FL("fl_ping_sweep", "Reachability of every server",
       "Ping and RPC (135) / SMB (445) / RDP (3389) / WinRM (5985) reachability per server.",
       r"""$list = Join-Path $(if ($env:WCT_SCRIPTS_DIR) { $env:WCT_SCRIPTS_DIR } else { Join-Path $env:APPDATA 'WindowsTweaker\scripts' }) 'servers.txt'
$targets = if (Test-Path $list) { Get-Content $list | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') } } else { @($env:COMPUTERNAME) }
foreach ($t in $targets) { $ping = Test-Connection $t -Count 1 -Quiet
  $ports = foreach ($p in 135, 445, 3389, 5985) { $ok = Test-NetConnection $t -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue; "$p=$(if ($ok) {'open'} else {'closed'})" }
  '{0} {1,-20} ping={2}  {3}' -f $(if ($ping) {'[OK]  '} else {'[WARN]'}), $t, $ping, ($ports -join ' ') }""",
       timeout=600, tags=('diagnostic', 'health', 'network', 'fleet')),
    FL("fl_defender_status", "Defender status on every server",
       "Real-time protection and signature age per server (MSFT_MpComputerStatus over CIM).",
       FLEET + r"""Each { param($t, $c) $s = Get-CimInstance -Namespace root/Microsoft/Windows/Defender -ClassName MSFT_MpComputerStatus -CimSession $c -ErrorAction SilentlyContinue
  if (-not $s) { "[INFO] $t -- Defender status not available (another AV, or not readable)"; return }
  $age = ((Get-Date) - $s.AntivirusSignatureLastUpdated).Days
  '{0} {1,-20} RTP={2} signatures {3} days old, mode {4}' -f $(if (-not $s.RealTimeProtectionEnabled -or $age -gt 3) {'[WARN]'} else {'[OK]  '}), $t, $s.RealTimeProtectionEnabled, $age, $s.AMRunningMode }""", tags=('diagnostic', 'health', 'security', 'fleet')),
    # ---- Roles & features (5) ----------------------------------------------------------------
    RF("rf_features_available", "Roles and features available (not installed)",
       "Roles and features that could be added -- the inventory for a hardening review of "
       "what is NOT there.",
       need_cmd("Get-WindowsFeature", "the ServerManager module (Windows Server)") +
       "Get-WindowsFeature | Where-Object { -not $_.Installed -and $_.FeatureType -eq 'Role' } | Select-Object Name, DisplayName | Format-Table -AutoSize | Out-String -Width 220",
       requires="ServerManager module", tags=('inventory', 'baseline')),
    RF("rf_risky_features", "Risky features installed",
       "SMB1, Telnet, TFTP, Windows PowerShell 2.0, Internet Printing and WebDAV -- features "
       "that should not be on a modern server.",
       need_cmd("Get-WindowsFeature", "the ServerManager module (Windows Server)") +
       r"""Get-WindowsFeature FS-SMB1, Telnet-Client, TFTP-Client, PowerShell-V2, Print-Internet, WebDAV-Redirector, Web-DAV-Publishing -ErrorAction SilentlyContinue | ForEach-Object { '{0} {1,-22} {2}' -f $(if ($_.Installed) {'[WARN]'} else {'[OK]  '}), $_.Name, $_.InstallState }""",
       requires="ServerManager module", tags=('diagnostic', 'health', 'security')),
    RF("rf_server_core", "Server Core or Desktop Experience",
       "Whether this server runs Server Core -- fewer patches and a smaller attack surface.",
       r"""$it = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').InstallationType
if ((Get-CimInstance Win32_OperatingSystem).ProductType -eq 1) { 'NOT AVAILABLE: this is a Windows client, not a server.'; return }
"[INFO] InstallationType: $it" """,
       requires="Windows Server", tags=('inventory', 'baseline')),
    RF("rf_server_manager_events", "Server Manager / deployment errors",
       "Errors from Server Manager and feature installation in 30 days.",
       EV + "EvSum @{LogName='Microsoft-Windows-ServerManager-MgmtProvider/Operational','Microsoft-Windows-ServerManager-DeploymentProvider/Operational'; Level=1,2,3} 30",
       requires="Windows Server", tags=('diagnostic', 'health')),
    RF("rf_backup", "Windows Server Backup status",
       "Last backup, next scheduled backup and backup targets (wbadmin).",
       need_cmd("wbadmin.exe", "Windows Server Backup") + r"""$s = wbadmin get versions 2>&1 | Select-Object -Last 12
$s
''; wbadmin get status 2>&1 | Select-Object -First 10""",
       requires="Windows Server Backup", admin=True, tags=('inventory', 'storage')),
]

SCRIPTS += [
    _PRINT("print-spool-folder-growth", "Spool folder size and ten-second growth",
       "Measures the configured spool directory twice, reporting queued-file bytes and observed net growth.",
       need_cmd("Get-Printer", "PrintManagement module") + need_admin("spool files require elevation") + CHK + r"""try {
  $config = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Print\Printers' -ErrorAction Stop
  $folder = if ($config.DefaultSpoolDirectory) { [Environment]::ExpandEnvironmentVariables($config.DefaultSpoolDirectory) } else { "$env:SystemRoot\System32\spool\PRINTERS" }
  $custom = @(Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Control\Print\Printers' -ErrorAction Stop | ForEach-Object {
    $p = Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction Stop
    if ($p.SpoolDirectory) { [Environment]::ExpandEnvironmentVariables($p.SpoolDirectory) }
  })
  $folders = @(@($folder)+$custom | Sort-Object -Unique)
  foreach ($path in $folders) {
    try {
      $first = @(Get-ChildItem -LiteralPath $path -File -Force -ErrorAction Stop)
      $bytes1 = ($first | Measure-Object Length -Sum).Sum; $clock = [Diagnostics.Stopwatch]::StartNew()
      Start-Sleep -Seconds 10
      $last = @(Get-ChildItem -LiteralPath $path -File -Force -ErrorAction Stop)
      $bytes2 = ($last | Measure-Object Length -Sum).Sum
      Chk $null $path ("files={0}; size={1:N2} MB; net growth={2:N2} MB over {3:N1}s" -f $last.Count,($bytes2/1MB),(($bytes2-$bytes1)/1MB),$clock.Elapsed.TotalSeconds)
      if (-not $last.Count) { '[INFO] Spool folder empty at second sample.' }
      $last | Sort-Object Length -Descending | Select-Object -First 10 Name, Length, LastWriteTime | Format-Table -AutoSize | Out-String -Width 220
    } catch { "ERROR: Spool folder ${path}: $($_.Exception.Message)" }
  }
  '[INFO] Net growth is not throughput; jobs may arrive and disappear between samples. No spool files were opened or changed.'
} catch { "ERROR: Cannot measure spool usage: $($_.Exception.Message)" }""",
       admin=True, timeout=300, tags=('diagnostic', 'storage', 'performance')),
    _PRINT("print-driver-isolation-registry", "Configured isolation per print driver",
       "Reads actual driver isolation registry values and lists queues using each driver.",
       need_cmd("Get-PrinterDriver", "PrintManagement module") + CHK + r"""try {
  $drivers = @(Get-PrinterDriver -ErrorAction Stop); $printers = @(Get-Printer -ErrorAction Stop)
  if (-not $drivers.Count) { '[INFO] No installed print drivers returned.'; return }
  foreach ($driver in $drivers) {
    try {
      $path = 'HKLM:\SYSTEM\CurrentControlSet\Control\Print\Environments\{0}\Drivers\Version-{1}\{2}' -f $driver.PrinterEnvironment,$driver.MajorVersion,$driver.Name
      $p = Get-ItemProperty -LiteralPath $path -ErrorAction Stop
      $value = $p.PrinterDriverIsolation
      $mode = if ($null -eq $value) { 'unset; effective mode not inferred' } else { switch ([int]$value) { 0 {'None (spooler)'} 2 {'Shared'} 3 {'Isolated'} default {"unknown ($value)"} } }
      Chk $null $driver.Name "type=$($driver.MajorVersion); isolation=$mode; queues=$(($printers | Where-Object DriverName -eq $driver.Name | ForEach-Object Name) -join ', ')"
    } catch { Chk $null $driver.Name "isolation read failed: $($_.Exception.Message)" }
  }
  '[INFO] Type 4 drivers use a different architecture. Configured type 3 isolation can be overridden by compatibility or policy; inspect running PrintIsolationHost processes when investigating crashes.'
} catch { "ERROR: Cannot inspect driver isolation: $($_.Exception.Message)" }""",
       admin=True, tags=('inventory', 'triage', 'baseline')),
    _PRINT("print-spooler-crash-attribution", "Spooler crash modules and restart evidence",
       "Correlates Application Error/WER records for spoolsv and PrintIsolationHost with service failures.",
       need_cmd("Get-Printer", "PrintManagement module") + CHK + r"""foreach ($log in 'Application','System') {
  try {
    $meta = Get-WinEvent -ListLog $log -ErrorAction Stop
    if (-not $meta.IsEnabled) { Chk $null $log 'log disabled'; continue }
    $ids = if ($log -eq 'Application') { @(1000,1001) } else { @(7031,7034) }
    $events = @(Get-WinEvent -FilterHashtable @{LogName=$log; Id=$ids; StartTime=(Get-Date).AddDays(-14)} -MaxEvents 3000 -ErrorAction Stop)
    $hits = @($events | Where-Object { $_.ToXml() -match '(?i)spoolsv\.exe|PrintIsolationHost\.exe|Spooler' })
    Chk $null $log "$($hits.Count) matching print crashes/service failures in newest 3,000 candidate events over 14 days"
    $hits | Select-Object -First 40 TimeCreated, Id, ProviderName, Message | Format-List | Out-String -Width 240
    foreach ($event in $hits | Where-Object Id -eq 1000 | Select-Object -First 20) {
      [xml]$xml = $event.ToXml()
      $xml.Event.EventData.Data | ForEach-Object { "[INFO] $($_.Name)=$($_.'#text')" }
    }
  } catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { Chk $null $log 'no candidate events retained within 14 days' }
    else { "ERROR: Crash events missing or unreadable: $($_.Exception.Message)" }
  }
}
'[INFO] A faulting module is evidence, not proof of root cause. Match crash times to driver versions and affected queues; localized service names may not match the filter.'""",
       admin=True, tags=('diagnostic', 'triage')),
    _PRINT("print-network-port-connectivity", "Network printer configured-port connectivity",
       "Probes configured TCP printer ports and reports protocol, destination and queue mapping.",
       need_cmd("Get-PrinterPort", "PrintManagement module") + need_cmd("Test-NetConnection", "NetTCPIP module") + CHK + r"""try {
  $ports = @(Get-PrinterPort -ErrorAction Stop); $printers = @(Get-Printer -ErrorAction Stop)
  $tcp = @($ports | Where-Object PrinterHostAddress)
  if (-not $tcp.Count) { Chk $null 'Printer ports' 'no standard TCP/IP ports; WSD/USB/custom monitors not probed'; return }
  foreach ($port in $tcp) {
    try {
      $protocol = [int]$port.Protocol
      $number = if ($protocol -eq 1 -and $port.PortNumber -gt 0) { [int]$port.PortNumber } elseif ($protocol -eq 2) { 515 } else { 0 }
      if (-not $number) { Chk $null $port.Name 'protocol or TCP port unknown'; continue }
      $ok = Test-NetConnection -ComputerName $port.PrinterHostAddress -Port $number -InformationLevel Quiet -WarningAction SilentlyContinue -ErrorAction Stop
      Chk $ok $port.Name "host=$($port.PrinterHostAddress); TCP=$number; protocol=$protocol (1 RAW, 2 LPR); LPR queue=$($port.LprQueueName); printers=$(($printers | Where-Object PortName -eq $port.Name | ForEach-Object Name) -join ', ')"
    } catch { Chk $null $port.Name $_.Exception.Message }
  }
  '[INFO] TCP connection only; no print data sent. Does not test SNMP/UDP status, LPR queue authorization or actual printing.'
} catch { "ERROR: Cannot read printer ports: $($_.Exception.Message)" }""",
       timeout=600, tags=('diagnostic', 'health', 'network')),
    FL("fleet-build-ubr-cohort-comparison", "Fleet build and UBR cohort comparison",
       "Compares revisions only within the same build, edition and installation type; cohort maximum is a drift baseline.",
       _REMOTE_READ + r"""$rows = @(EachRemote {
  try {
    $p = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -ErrorAction Stop
    if (-not $p.CurrentBuildNumber -or $null -eq $p.UBR) { throw 'Build or UBR missing' }
    [pscustomobject]@{Server=$env:COMPUTERNAME; Build=$p.CurrentBuildNumber; UBR=[int]$p.UBR; Edition=$p.EditionID; Installation=$p.InstallationType; DisplayVersion=$p.DisplayVersion}
  } catch { Chk $null $env:COMPUTERNAME "build read failed: $($_.Exception.Message)" }
})
$rows | Where-Object { $_ -is [string] }
$valid = @($rows | Where-Object { $_.Server -and $null -ne $_.UBR })
if (-not $valid.Count) { Chk $null 'Fleet builds' 'no readable build records'; return }
foreach ($group in $valid | Group-Object Build,Edition,Installation) {
  $max = ($group.Group | Measure-Object UBR -Maximum).Maximum
  foreach ($row in $group.Group) {
    Chk ($row.UBR -eq $max) $row.Server "build=$($row.Build).$($row.UBR); edition=$($row.Edition); installation=$($row.Installation); highest observed UBR in cohort=$max"
  }
}
'[INFO] This detects cohort drift, not current Microsoft patch compliance. A single-node or uniformly stale cohort can match while remaining outdated.'""",
       requires="PowerShell remoting/WinRM for remote targets; servers.txt", timeout=600, tags=('diagnostic', 'compliance', 'fleet')),
    FL("fleet-reboot-source-detail", "Fleet pending reboot sources and rename evidence",
       "Reads servicing, Windows Update, rename pairs, computer renames and optional ConfigMgr reboot state.",
       _REMOTE_READ + r"""EachRemote {
  foreach ($item in @(
    @('CBS','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'),
    @('Windows Update','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
  )) {
    try { $present = Test-Path -LiteralPath $item[1] -ErrorAction Stop; Chk (-not $present) $item[0] "reboot marker present=$present" }
    catch { Chk $null $item[0] $_.Exception.Message }
  }
  try {
    $manager = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -ErrorAction Stop
    $pairs = @($manager.PendingFileRenameOperations | Where-Object { $null -ne $_ })
    Chk $null 'PendingFileRenameOperations' "$($pairs.Count) entries; may be stale and does not alone prove a reboot is required"
    if (-not $pairs.Count) { '[INFO] No pending file rename entries.' } else { $pairs }
  } catch { Chk $null 'File renames' $_.Exception.Message }
  try {
    $base = 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName'
    $active = (Get-ItemProperty "$base\ActiveComputerName" -ErrorAction Stop).ComputerName
    $pending = (Get-ItemProperty "$base\ComputerName" -ErrorAction Stop).ComputerName
    if (-not $active -or -not $pending) { Chk $null 'Computer name' 'active or configured name empty' }
    else { Chk ($active -eq $pending) 'Computer rename' "active=$active; configured=$pending" }
  } catch { Chk $null 'Computer rename' $_.Exception.Message }
  try {
    if (-not (Test-Path 'HKLM:\SOFTWARE\Microsoft\CCM' -ErrorAction Stop)) { '[INFO] ConfigMgr client not installed; its reboot source does not apply.' }
    else {
      $r = Invoke-CimMethod -Namespace root/ccm/ClientSDK -ClassName CCM_ClientUtilities -MethodName DetermineIfRebootPending -ErrorAction Stop
      if ($r.ReturnValue -ne 0 -or $null -eq $r.RebootPending) { Chk $null 'ConfigMgr reboot' "return=$($r.ReturnValue); state unavailable" }
      else { Chk (-not $r.RebootPending -and -not $r.IsHardRebootPending) 'ConfigMgr reboot' "pending=$($r.RebootPending); hard=$($r.IsHardRebootPending)" }
    }
  } catch { Chk $null 'ConfigMgr reboot' $_.Exception.Message }
}""",
       requires="PowerShell remoting/WinRM and remote registry read rights; optional ConfigMgr client", timeout=600, tags=('diagnostic', 'health', 'triage', 'fleet')),
    FL("fleet-certificate-stores-expiry-evidence", "Fleet service certificate expiry with store evidence",
       "Checks My, WebHosting and Remote Desktop stores, including expired certificates and empty-store evidence.",
       _REMOTE_READ + r"""EachRemote {
  foreach ($store in 'My','WebHosting','Remote Desktop') {
    try {
      $path = "Cert:\LocalMachine\$store"
      if (-not (Test-Path -LiteralPath $path -ErrorAction Stop)) { Chk $null $store 'store absent on this server'; continue }
      $certs = @(Get-ChildItem -LiteralPath $path -ErrorAction Stop)
      if (-not $certs.Count) { Chk $null $store 'store empty; service certificate coverage unverified'; continue }
      foreach ($cert in $certs) {
        $days = [math]::Floor(($cert.NotAfter-(Get-Date)).TotalDays)
        Chk ($days -gt 45 -and $cert.NotBefore -le (Get-Date)) "$store / $($cert.Thumbprint)" "subject=$($cert.Subject); expires=$($cert.NotAfter); days=$days; private key=$($cert.HasPrivateKey); DNS=$($cert.DnsNameList -join ', ')"
      }
    } catch { Chk $null $store "read refused: $($_.Exception.Message)" }
  }
  '[INFO] Machine stores only. Includes unbound certificates; binding, chain trust, service key access and user/service-account stores are not verified.'
}""",
       requires="PowerShell remoting/WinRM and certificate store read rights", timeout=600, tags=('diagnostic', 'health', 'security', 'fleet')),
    FL("fleet-service-account-password-age", "Fleet service logon accounts and password age",
       "Maps services to local/domain accounts and reads password timestamps without retrieving credentials.",
       _REMOTE_READ + r"""EachRemote {
  try {
    $services = @(Get-CimInstance Win32_Service -ErrorAction Stop | Where-Object { $_.StartName -and $_.StartName -notmatch '^(?i:LocalSystem|NT AUTHORITY\\|NT SERVICE\\)' })
    if (-not $services.Count) { '[INFO] No custom service logon accounts returned.'; return }
    foreach ($group in $services | Group-Object StartName) {
      $name = $group.Name
      try {
        "[INFO] Account=$name; services=$($group.Group.Name -join ', ')"
        if ($name -match '^\.\\|^'+[regex]::Escape($env:COMPUTERNAME)+'\\') {
          if (-not (Get-Command Get-LocalUser -ErrorAction SilentlyContinue)) { Chk $null $name 'LocalAccounts module unavailable'; continue }
          $user = Get-LocalUser -Name ($name -split '\\',2)[1] -ErrorAction Stop
          $date = $user.PasswordLastSet
        } elseif ($name.EndsWith('$')) {
          Chk $null $name 'managed/computer account; suffix alone does not prove gMSA, password rotation must be checked in AD'; continue
        } elseif (Get-Command Get-ADUser -ErrorAction SilentlyContinue) {
          $account = New-Object Security.Principal.NTAccount $name
          $sid = $account.Translate([Security.Principal.SecurityIdentifier]).Value
          if ($name -match '\\') { $domain = ($name -split '\\',2)[0] } elseif ($name -match '@') { $domain = ($name -split '@',2)[1] } else { throw 'Unqualified account; domain cannot be determined safely' }
          $dc = Get-ADDomainController -Discover -DomainName $domain -ErrorAction Stop
          $user = Get-ADUser -Identity $sid -Server $dc.HostName[0] -Properties PasswordLastSet,PasswordNeverExpires -ErrorAction Stop
          $date = $user.PasswordLastSet
        } else { Chk $null $name 'ActiveDirectory module unavailable on target; domain password age unknown'; continue }
        if (-not $date) { Chk $null $name 'PasswordLastSet empty, never set or not visible'; continue }
        $age = [math]::Floor(((Get-Date)-$date).TotalDays)
        Chk ($age -le 90) $name "last set=$date; age days=$age; never expires=$($user.PasswordNeverExpires); 90-day review threshold, not an instruction to rotate"
      } catch { Chk $null $name "account read failed: $($_.Exception.Message)" }
    }
  } catch { "ERROR: Service inventory refused: $($_.Exception.Message)" }
}""",
       requires="PowerShell remoting/WinRM; LocalAccounts or ActiveDirectory module on target", timeout=600, tags=('diagnostic', 'security', 'fleet')),
]

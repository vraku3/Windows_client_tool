"""Wintel operations: log parsing, fleet checks across many servers, and more
roles & features. Read-only.

Fleet scripts read their targets from `servers.txt` in the scripts folder
(the one chosen in the pane, passed as %WCT_SCRIPTS_DIR%) (one name per line, # comments); with no list they check THIS
computer, which is how they were verified here. They talk CIM over DCOM, so
they need no WinRM -- only RPC/WMI through the target's firewall and an
account that is admin there.
"""
from ._base import EV, maker, need_admin, need_cmd

LP = maker("Log parsing", server=True)
FL = maker("Fleet (remote servers)", server=True)
RF = maker("Roles & features", server=True)

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
    # ---- Log parsing (18) --------------------------------------------------------------------
    LP("lp_account_lockout_source", "Account lockouts and where they came from",
       "4740 lockout events with the caller computer -- on a DC this names the machine with "
       "the stale password. Runs anywhere; the answer is on the PDC emulator.",
       need_admin("the Security log is only readable elevated") + EV +
       r"""$r = @(EvRead @{LogName='Security'; Id=4740} 30 500)
if (-not $r.Count -or $r[0] -is [string]) { $r; return }
$r | ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1,-25} locked out, caller computer: {2}' -f $_.TimeCreated, $_.Properties[0].Value, $_.Properties[1].Value }""",
       admin=True),
    LP("lp_kerberos_failures", "Kerberos pre-authentication failures",
       "4771 failures by account, client address and failure code (0x18 = bad password, "
       "0x12 = disabled/locked).",
       need_admin("the Security log is only readable elevated") +
       r"""$e = Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4771; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 5000 -ErrorAction SilentlyContinue
if (-not $e) { '[OK]   No Kerberos pre-auth failures in 7 days (or this is not a DC).'; return }
$e | ForEach-Object { $d = @{}; ([xml]$_.ToXml()).Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }; "$($d.TargetUserName) from $($d.IpAddress) code $($d.Status)" } | Group-Object | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { '[WARN] x{0,-5} {1}' -f $_.Count, $_.Name }""",
       admin=True),
    LP("lp_ntlm_failures", "NTLM authentication failures",
       "4776 credential validation failures by account and workstation.",
       need_admin("the Security log is only readable elevated") +
       r"""$e = Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4776; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 5000 -ErrorAction SilentlyContinue | Where-Object { $_.Properties[3].Value -ne 0 }
if (-not $e) { '[OK]   No NTLM validation failures in 7 days.'; return }
$e | ForEach-Object { "$($_.Properties[1].Value) from $($_.Properties[2].Value) error 0x$('{0:X}' -f $_.Properties[3].Value)" } | Group-Object | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { '[WARN] x{0,-5} {1}' -f $_.Count, $_.Name }""",
       admin=True),
    LP("lp_logons_by_type", "Logons by account and type (24 h)",
       "Successful logons (4624) in the last day grouped by account and logon type -- service, "
       "batch, network, interactive, RDP.",
       need_admin("the Security log is only readable elevated") +
       r"""$t = @{'2'='interactive';'3'='network';'4'='batch';'5'='service';'7'='unlock';'8'='cleartext';'9'='new creds';'10'='RDP';'11'='cached'}
Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4624; StartTime=(Get-Date).AddDays(-1)} -MaxEvents 20000 -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Properties[6].Value)\$($_.Properties[5].Value) [$($t[[string]$_.Properties[8].Value])]" } | Group-Object | Sort-Object Count -Descending | Select-Object -First 40 | ForEach-Object { '{0,7}  {1}' -f $_.Count, $_.Name }""",
       admin=True, timeout=300),
    LP("lp_privileged_logons", "Special-privilege logons (4672)",
       "Accounts that received admin-equivalent privileges at logon in the last day, grouped.",
       need_admin("the Security log is only readable elevated") +
       r"""Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4672; StartTime=(Get-Date).AddDays(-1)} -MaxEvents 20000 -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Properties[2].Value)\$($_.Properties[1].Value)" } | Group-Object | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { '{0,7}  {1}' -f $_.Count, $_.Name }""",
       admin=True, timeout=300),
    LP("lp_process_creation", "Process creation log (4688)",
       "The last 60 processes started, with parent and command line when command-line auditing "
       "is on -- needs Process Creation auditing.",
       need_admin("the Security log is only readable elevated") + EV +
       r"""$r = @(EvRead @{LogName='Security'; Id=4688} 1 60)
if (-not $r.Count -or $r[0] -is [string]) { $r; if ($r[0] -match 'No matching') { 'NOTE: Process Creation auditing may be off (Security Dashboard > Audit & Logging).' }; return }
$r | ForEach-Object { $d = @{}; ([xml]$_.ToXml()).Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }; '{0:HH:mm:ss} {1} <- {2}  {3}' -f $_.TimeCreated, $d.NewProcessName, $d.ParentProcessName, $d.CommandLine }""",
       admin=True),
    LP("lp_cbs_errors", "CBS log errors (servicing)",
       "Error lines in CBS.log -- failed updates, corruption found by SFC/DISM.",
       r"""$f = "$env:SystemRoot\Logs\CBS\CBS.log"
if (-not (Test-Path $f)) { 'NOT AVAILABLE: CBS.log is not present (rolled into CbsPersist cabs).'; return }
$l = Select-String -Path $f -Pattern ', Error ' -ErrorAction SilentlyContinue
if (-not $l) { '[OK]   No error lines in the current CBS.log.'; return }
$l | ForEach-Object { ($_.Line -replace '^\S+ \S+, Error\s+','') -replace '\[HRESULT = (0x\w+).*','[$1]' } | Group-Object | Sort-Object Count -Descending | Select-Object -First 25 | ForEach-Object { '[WARN] x{0,-4} {1}' -f $_.Count, $_.Name.Substring(0, [Math]::Min(170, $_.Name.Length)) }""",
       note="CBS.log may be unreadable unelevated"),
    LP("lp_setupapi_failures", "Driver install failures (setupapi)",
       "Failed device or driver installs recorded in setupapi.dev.log, with the device.",
       r"""$f = "$env:SystemRoot\INF\setupapi.dev.log"
if (-not (Test-Path $f)) { 'NOT AVAILABLE: setupapi.dev.log is not present.'; return }
$hits = Select-String -Path $f -Pattern '!!!|Exit status: FAILURE|Device not started' -ErrorAction SilentlyContinue | Select-Object -Last 40
if (-not $hits) { '[OK]   No failures in setupapi.dev.log.'; return }
$hits | ForEach-Object { "[WARN] line $($_.LineNumber): $($_.Line.Trim())" }""",
       timeout=180),
    LP("lp_dism_errors", "DISM log errors",
       "Error lines in dism.log grouped.",
       r"""$f = "$env:SystemRoot\Logs\DISM\dism.log"
if (-not (Test-Path $f)) { 'NOT AVAILABLE: dism.log is not present.'; return }
$l = Select-String -Path $f -Pattern ', Error ' -ErrorAction SilentlyContinue
if (-not $l) { '[OK]   No error lines in dism.log.'; return }
$l | ForEach-Object { $_.Line -replace '^\S+ \S+, Error\s+','' } | Group-Object | Sort-Object Count -Descending | Select-Object -First 20 | ForEach-Object { '[WARN] x{0,-4} {1}' -f $_.Count, $_.Name.Substring(0, [Math]::Min(170, $_.Name.Length)) }""",
       note="dism.log may be unreadable unelevated"),
    LP("lp_windowsupdate_etl", "Windows Update client errors",
       "Errors from the Windows Update client operational log grouped.",
       EV + "EvSum @{LogName='Microsoft-Windows-WindowsUpdateClient/Operational'; Level=1,2,3} 30"),
    LP("lp_iis_errors_by_url", "IIS 5xx errors by URL (7 days)",
       "Server errors in the last week's IIS logs grouped by URL and status.",
       need_cmd("Get-Website", "the WebAdministration module (IIS)") +
       r"""Get-Website | ForEach-Object { $dir = [Environment]::ExpandEnvironmentVariables($_.logFile.directory) + "\W3SVC$($_.id)"
  Get-ChildItem $dir -Filter *.log -ErrorAction SilentlyContinue | Where-Object LastWriteTime -gt (Get-Date).AddDays(-7) | ForEach-Object { $f = $_.FullName; $fields = (Get-Content $f -TotalCount 10 | Where-Object { $_ -like '#Fields:*' } | Select-Object -Last 1) -replace '^#Fields: ','' -split ' '
    $u = [array]::IndexOf($fields, 'cs-uri-stem'); $s = [array]::IndexOf($fields, 'sc-status')
    Get-Content $f | Where-Object { $_ -notlike '#*' } | ForEach-Object { $p = $_ -split ' '; if ($p[$s] -like '5*') { "$($p[$s]) $($p[$u])" } } } } | Group-Object | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { '[WARN] x{0,-5} {1}' -f $_.Count, $_.Name }""",
       requires="IIS (WebAdministration)", timeout=900),
    LP("lp_iis_slow_requests", "Slowest IIS requests today",
       "The 25 slowest requests (time-taken) in each site's newest log.",
       need_cmd("Get-Website", "the WebAdministration module (IIS)") +
       r"""Get-Website | ForEach-Object { $dir = [Environment]::ExpandEnvironmentVariables($_.logFile.directory) + "\W3SVC$($_.id)"; $f = Get-ChildItem $dir -Filter *.log -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $f) { return }; $fields = (Get-Content $f.FullName -TotalCount 10 | Where-Object { $_ -like '#Fields:*' } | Select-Object -Last 1) -replace '^#Fields: ','' -split ' '
  $t = [array]::IndexOf($fields, 'time-taken'); $u = [array]::IndexOf($fields, 'cs-uri-stem'); "== $($_.Name)"
  Get-Content $f.FullName | Where-Object { $_ -notlike '#*' } | ForEach-Object { $p = $_ -split ' '; [pscustomobject]@{ ms=[int]$p[$t]; url=$p[$u] } } | Sort-Object ms -Descending | Select-Object -First 25 | ForEach-Object { '   {0,8} ms  {1}' -f $_.ms, $_.url } }""",
       requires="IIS (WebAdministration)", timeout=900),
    LP("lp_dhcp_audit_week", "DHCP audit logs (7 days)",
       "All seven DHCP audit logs summarised by event type and the busiest MAC addresses.",
       need_cmd("Get-DhcpServerAuditLog", "the DhcpServer module (DHCP Server)") +
       r"""$p = (Get-DhcpServerAuditLog).Path
$rows = Get-ChildItem $p -Filter 'DhcpSrvLog-*.log' -ErrorAction SilentlyContinue | ForEach-Object { Get-Content $_.FullName | Where-Object { $_ -match '^\d+,' } | ForEach-Object { , ($_ -split ',') } }
$names = @{'10'='new lease';'11'='renewed';'12'='released';'13'='conflict';'14'='scope full';'15'='NAK';'16'='deleted';'17'='expired'}
$rows | ForEach-Object { $_[0] } | Group-Object | Sort-Object Count -Descending | ForEach-Object { '{0,7}  {1,3} {2}' -f $_.Count, $_.Name, $names[$_.Name] }
''; 'Busiest MACs:'; $rows | Where-Object { $_.Count -gt 6 -and $_[6] } | ForEach-Object { "$($_[6]) $($_[5])" } | Group-Object | Sort-Object Count -Descending | Select-Object -First 15 | ForEach-Object { '{0,7}  {1}' -f $_.Count, $_.Name }""",
       requires="DhcpServer module"),
    LP("lp_dns_debug_top", "DNS debug log: top queried names",
       "Top 30 queried names and top clients from the DNS server debug log, when it is on.",
       need_cmd("Get-DnsServerDiagnostics", "the DnsServer module (DNS Server)") +
       r"""$f = (Get-DnsServerDiagnostics).LogFilePath
if (-not $f -or -not (Test-Path $f)) { '[INFO] DNS debug logging is off (no log file).'; return }
$q = Get-Content $f -Tail 50000 | Where-Object { $_ -match ' Rcv ' -and $_ -match '\(\d+\)' }
'Top names:'; $q | ForEach-Object { (($_ -split ' ')[-1]) -replace '\(\d+\)','.' -replace '^\.|\.$','' } | Group-Object | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { '{0,7}  {1}' -f $_.Count, $_.Name }
''; 'Top clients:'; $q | ForEach-Object { ($_ -split '\s+')[7] } | Group-Object | Sort-Object Count -Descending | Select-Object -First 15 | ForEach-Object { '{0,7}  {1}' -f $_.Count, $_.Name }""",
       requires="DnsServer module"),
    LP("lp_rdp_gateway", "RD Gateway connections",
       "Successful and failed RD Gateway connections (302/303/304) by user and source.",
       EV + r"""$r = @(EvRead @{LogName='Microsoft-Windows-TerminalServices-Gateway/Operational'; Id=302,303,304} 7 3000)
if (-not $r.Count -or $r[0] -is [string]) { $r; return }
$r | ForEach-Object { "$($_.Id) $($_.Properties[0].Value) from $($_.Properties[1].Value)" } | Group-Object | Sort-Object Count -Descending | Select-Object -First 30 | ForEach-Object { '{0,6}  {1}' -f $_.Count, $_.Name }""",
       requires="RD Gateway role"),
    LP("lp_ps_transcripts", "PowerShell transcripts",
       "Transcript files written by PowerShell transcription (if configured), newest first.",
       r"""$d = (Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\PowerShell\Transcription' -ErrorAction SilentlyContinue).OutputDirectory
if (-not $d) { '[INFO] PowerShell transcription is not configured by policy.'; return }
Get-ChildItem $d -Recurse -Filter 'PowerShell_transcript*' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 40 | ForEach-Object { '{0:yyyy-MM-dd HH:mm}  {1,8:N0} KB  {2}' -f $_.LastWriteTime, ($_.Length/1KB), $_.FullName }"""),
    LP("lp_sysmon_summary", "Sysmon event summary",
       "Sysmon events grouped by type over 24 hours (if Sysmon is installed).",
       EV + "EvSum @{LogName='Microsoft-Windows-Sysmon/Operational'} 1 20000",
       admin=True),
    LP("lp_event_rates", "Busiest event logs (24 h)",
       "How many events each log wrote in the last day -- a log suddenly writing thousands "
       "is a fault in progress.",
       r"""Get-WinEvent -ListLog * -ErrorAction SilentlyContinue | Where-Object { $_.RecordCount -and $_.LastWriteTime -gt (Get-Date).AddDays(-1) } | ForEach-Object { $n = (Get-WinEvent -FilterHashtable @{LogName=$_.LogName; StartTime=(Get-Date).AddDays(-1)} -MaxEvents 20000 -ErrorAction SilentlyContinue | Measure-Object).Count; [pscustomobject]@{ Log=$_.LogName; Last24h=$n } } | Sort-Object Last24h -Descending | Select-Object -First 25 | Format-Table -AutoSize | Out-String -Width 220""",
       note="some logs are only readable elevated", timeout=600),
    # ---- Fleet (remote servers) (20) ---------------------------------------------------------
    FL("fl_free_space", "Free space on every server",
       "Fixed-disk free space per server, low ones flagged (below 10% or 10 GB).",
       FLEET + r"""Each { param($t, $c) Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' -CimSession $c | ForEach-Object { $pct = if ($_.Size) { 100 * $_.FreeSpace / $_.Size } else { 0 }
  '{0} {1,-20} {2} {3,8:N1} GB free of {4,8:N1} GB ({5,4:N1}%)' -f $(if ($pct -lt 10 -or $_.FreeSpace -lt 10GB) {'[BAD] '} elseif ($pct -lt 20) {'[WARN]'} else {'[OK]  '}), $t, $_.DeviceID, ($_.FreeSpace/1GB), ($_.Size/1GB), $pct } }"""),
    FL("fl_uptime", "Uptime of every server",
       "Last boot and uptime per server -- servers not rebooted since the last Patch Tuesday "
       "are not patched.",
       FLEET + r"""Each { param($t, $c) $o = Get-CimInstance Win32_OperatingSystem -CimSession $c; $up = (Get-Date) - $o.LastBootUpTime
  '{0} {1,-20} up {2,4}d {3,2}h  (since {4})' -f $(if ($up.Days -gt 35) {'[WARN]'} else {'[OK]  '}), $t, $up.Days, $up.Hours, $o.LastBootUpTime }"""),
    FL("fl_os_versions", "OS version of every server",
       "Edition, version and build per server.",
       FLEET + r"""Each { param($t, $c) $o = Get-CimInstance Win32_OperatingSystem -CimSession $c; '{0,-20} {1}  build {2}  {3}' -f $t, $o.Caption, $o.BuildNumber, $o.OSArchitecture }"""),
    FL("fl_last_patch", "Newest hotfix on every server",
       "The most recent installed update per server and its age.",
       FLEET + r"""Each { param($t, $c) $h = Get-CimInstance Win32_QuickFixEngineering -CimSession $c | Where-Object InstalledOn | Sort-Object InstalledOn -Descending | Select-Object -First 1
  $age = if ($h) { ((Get-Date) - $h.InstalledOn).Days } else { $null }
  '{0} {1,-20} {2} installed {3} ({4} days ago)' -f $(if ($age -gt 45) {'[WARN]'} else {'[OK]  '}), $t, $h.HotFixID, $h.InstalledOn, $age }"""),
    FL("fl_stopped_auto_services", "Stopped automatic services on every server",
       "Automatic services not running, per server (trigger-start ones excluded by state "
       "'Stopped' with exit code 0).",
       FLEET + r"""Each { param($t, $c) Get-CimInstance Win32_Service -Filter "StartMode='Auto' AND State<>'Running'" -CimSession $c | Where-Object { $_.ExitCode -ne 0 } | ForEach-Object { '[WARN] {0,-20} {1,-35} exit {2}' -f $t, $_.Name, $_.ExitCode } }
'(services with exit code 0 are trigger-start / stopped by design and not listed)'"""),
    FL("fl_installed_apps", "Installed applications on every server",
       "Installed programs per server from Win32_InstalledWin32Program -- never Win32_Product, "
       "which triggers an MSI consistency check (and repairs) on every package it touches.",
       FLEET + r"""Each { param($t, $c) "== $t"; Get-CimInstance Win32_InstalledWin32Program -CimSession $c -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { '   {0,-60} {1,-20} {2}' -f $_.Name, $_.Version, $_.Vendor } }""",
       timeout=600),
    FL("fl_app_search", "Find servers that have a program",
       "Which servers have a program whose name matches the pattern in the first line of "
       "servers-app.txt (default: 'Java').",
       FLEET + r"""$pf = Join-Path $(if ($env:WCT_SCRIPTS_DIR) { $env:WCT_SCRIPTS_DIR } else { Join-Path $env:APPDATA 'WindowsTweaker\scripts' }) 'servers-app.txt'
$pat = if (Test-Path $pf) { (Get-Content $pf | Select-Object -First 1).Trim() } else { 'Java' }
"Looking for: *$pat*"
Each { param($t, $c) Get-CimInstance Win32_InstalledWin32Program -CimSession $c -ErrorAction SilentlyContinue | Where-Object Name -like "*$pat*" | ForEach-Object { '{0,-20} {1} {2}' -f $t, $_.Name, $_.Version } }""",
       timeout=600),
    FL("fl_logged_on", "Who is logged on to every server",
       "The interactive user on each server (Win32_ComputerSystem) and explorer.exe owners.",
       FLEET + r"""Each { param($t, $c) $u = (Get-CimInstance Win32_ComputerSystem -CimSession $c).UserName
  $ex = Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" -CimSession $c | ForEach-Object { $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -CimSession $c; "$($o.Domain)\$($o.User)" }
  '{0,-20} console: {1}   sessions: {2}' -f $t, $u, (($ex | Sort-Object -Unique) -join ', ') }"""),
    FL("fl_cpu_mem", "CPU and memory load on every server",
       "Current CPU load and memory use per server.",
       FLEET + r"""Each { param($t, $c) $cpu = (Get-CimInstance Win32_Processor -CimSession $c | Measure-Object LoadPercentage -Average).Average; $o = Get-CimInstance Win32_OperatingSystem -CimSession $c
  $mem = 100 * (1 - $o.FreePhysicalMemory / $o.TotalVisibleMemorySize)
  '{0} {1,-20} CPU {2,5:N1}%   memory {3,5:N1}% of {4:N1} GB' -f $(if ($cpu -gt 85 -or $mem -gt 90) {'[WARN]'} else {'[OK]  '}), $t, $cpu, $mem, ($o.TotalVisibleMemorySize/1MB) }"""),
    FL("fl_pending_reboot", "Pending restart on every server",
       "CBS RebootPending and Windows Update RebootRequired per server, read through the "
       "remote registry provider (StdRegProv).",
       FLEET + r"""$HKLM = [uint32]2147483650
Each { param($t, $c) $cbs = Invoke-CimMethod -CimSession $c -Namespace root\cimv2 -ClassName StdRegProv -MethodName EnumKey -Arguments @{hDefKey=$HKLM; sSubKeyName='SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'}
  $wu = Invoke-CimMethod -CimSession $c -Namespace root\cimv2 -ClassName StdRegProv -MethodName EnumKey -Arguments @{hDefKey=$HKLM; sSubKeyName='SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'}
  $p = ($cbs.ReturnValue -eq 0) -or ($wu.ReturnValue -eq 0)
  '{0} {1,-20} CBS={2} WU={3}' -f $(if ($p) {'[WARN]'} else {'[OK]  '}), $t, ($cbs.ReturnValue -eq 0), ($wu.ReturnValue -eq 0) }"""),
    FL("fl_shares", "Shares on every server",
       "Non-administrative shares per server.",
       FLEET + r"""Each { param($t, $c) Get-CimInstance Win32_Share -CimSession $c | Where-Object { $_.Type -eq 0 } | ForEach-Object { '{0,-20} {1,-25} {2}' -f $t, $_.Name, $_.Path } }"""),
    FL("fl_event_errors", "System errors on every server (24 h)",
       "Count of System-log errors per server in the last day, with the noisiest source.",
       FLEET + r"""Each { param($t, $c) $e = Get-CimInstance Win32_NTLogEvent -CimSession $c -Filter "Logfile='System' AND EventType=1 AND TimeGenerated>='$((Get-Date).AddDays(-1).ToString('yyyyMMddHHmmss')).000000+000'" -ErrorAction SilentlyContinue
  $top = $e | Group-Object SourceName | Sort-Object Count -Descending | Select-Object -First 1
  '{0} {1,-20} {2,4} error(s)  top: {3}' -f $(if (@($e).Count -gt 20) {'[WARN]'} else {'[OK]  '}), $t, @($e).Count, $(if ($top) { "$($top.Name) x$($top.Count)" } else { '-' }) }""",
       timeout=600),
    FL("fl_time_offset", "Clock offset of every server",
       "Each server's clock offset against this computer (w32tm /stripchart).",
       r"""$list = Join-Path $(if ($env:WCT_SCRIPTS_DIR) { $env:WCT_SCRIPTS_DIR } else { Join-Path $env:APPDATA 'WindowsTweaker\scripts' }) 'servers.txt'
$targets = if (Test-Path $list) { Get-Content $list | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') } } else { @($env:COMPUTERNAME) }
foreach ($t in $targets) { $o = w32tm /stripchart /computer:$t /samples:1 /dataonly 2>&1 | Select-Object -Last 1; '{0,-20} {1}' -f $t, $o }""",
       timeout=300),
    FL("fl_bios_serial", "Hardware model and serial of every server",
       "Manufacturer, model, serial and BIOS version per server -- the asset list.",
       FLEET + r"""Each { param($t, $c) $cs = Get-CimInstance Win32_ComputerSystem -CimSession $c; $b = Get-CimInstance Win32_BIOS -CimSession $c
  '{0,-20} {1} {2}  serial {3}  BIOS {4}' -f $t, $cs.Manufacturer, $cs.Model, $b.SerialNumber, $b.SMBIOSBIOSVersion }"""),
    FL("fl_ip_config", "IP configuration of every server",
       "IPs, gateway and DNS servers per server.",
       FLEET + r"""Each { param($t, $c) Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=TRUE' -CimSession $c | ForEach-Object { '{0,-20} {1,-30} gw {2,-15} dns {3}' -f $t, ($_.IPAddress -join ','), ($_.DefaultIPGateway -join ','), ($_.DNSServerSearchOrder -join ',') } }"""),
    FL("fl_local_admins", "Local Administrators on every server",
       "Members of each server's local Administrators group.",
       FLEET + r"""Each { param($t, $c) $g = Get-CimInstance Win32_Group -Filter "SID='S-1-5-32-544'" -CimSession $c
  Get-CimAssociatedInstance -InputObject $g -ResultClassName Win32_Account -CimSession $c -ErrorAction SilentlyContinue | ForEach-Object { '{0,-20} {1}\{2}' -f $t, $_.Domain, $_.Name } }"""),
    FL("fl_scheduled_tasks", "Non-Microsoft scheduled tasks on every server",
       "Tasks outside \\Microsoft\\ per server with their action.",
       FLEET + r"""Each { param($t, $c) Get-ScheduledTask -CimSession $c -ErrorAction SilentlyContinue | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } | ForEach-Object { '{0,-20} {1}{2}  -> {3}' -f $t, $_.TaskPath, $_.TaskName, (($_.Actions | ForEach-Object { $_.Execute }) -join '; ') } }"""),
    FL("fl_certificates_expiring", "Certificates expiring on every server",
       "Machine certificates expiring within 30 days per server (needs WinRM: Invoke-Command, "
       "since the certificate store is not exposed through CIM).",
       r"""$list = Join-Path $(if ($env:WCT_SCRIPTS_DIR) { $env:WCT_SCRIPTS_DIR } else { Join-Path $env:APPDATA 'WindowsTweaker\scripts' }) 'servers.txt'
$targets = if (Test-Path $list) { Get-Content $list | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') } } else { @($env:COMPUTERNAME) }
foreach ($t in $targets) { try { $r = if ($t -eq $env:COMPUTERNAME) { Get-ChildItem Cert:\LocalMachine\My | Where-Object NotAfter -lt (Get-Date).AddDays(30) } else { Invoke-Command -ComputerName $t -ErrorAction Stop -ScriptBlock { Get-ChildItem Cert:\LocalMachine\My | Where-Object NotAfter -lt (Get-Date).AddDays(30) } }
  if (-not $r) { "[OK]   $t -- nothing expiring" } else { $r | ForEach-Object { "[WARN] $t -- $($_.Subject) expires $($_.NotAfter)" } } } catch { "[BAD]  $t -- $($_.Exception.Message)" } }"""),
    FL("fl_ping_sweep", "Reachability of every server",
       "Ping and RPC (135) / SMB (445) / RDP (3389) / WinRM (5985) reachability per server.",
       r"""$list = Join-Path $(if ($env:WCT_SCRIPTS_DIR) { $env:WCT_SCRIPTS_DIR } else { Join-Path $env:APPDATA 'WindowsTweaker\scripts' }) 'servers.txt'
$targets = if (Test-Path $list) { Get-Content $list | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') } } else { @($env:COMPUTERNAME) }
foreach ($t in $targets) { $ping = Test-Connection $t -Count 1 -Quiet
  $ports = foreach ($p in 135, 445, 3389, 5985) { $ok = Test-NetConnection $t -Port $p -InformationLevel Quiet -WarningAction SilentlyContinue; "$p=$(if ($ok) {'open'} else {'closed'})" }
  '{0} {1,-20} ping={2}  {3}' -f $(if ($ping) {'[OK]  '} else {'[WARN]'}), $t, $ping, ($ports -join ' ') }""",
       timeout=600),
    FL("fl_defender_status", "Defender status on every server",
       "Real-time protection and signature age per server (MSFT_MpComputerStatus over CIM).",
       FLEET + r"""Each { param($t, $c) $s = Get-CimInstance -Namespace root/Microsoft/Windows/Defender -ClassName MSFT_MpComputerStatus -CimSession $c -ErrorAction SilentlyContinue
  if (-not $s) { "[INFO] $t -- Defender status not available (another AV, or not readable)"; return }
  $age = ((Get-Date) - $s.AntivirusSignatureLastUpdated).Days
  '{0} {1,-20} RTP={2} signatures {3} days old, mode {4}' -f $(if (-not $s.RealTimeProtectionEnabled -or $age -gt 3) {'[WARN]'} else {'[OK]  '}), $t, $s.RealTimeProtectionEnabled, $age, $s.AMRunningMode }"""),
    # ---- Roles & features (5) ----------------------------------------------------------------
    RF("rf_features_available", "Roles and features available (not installed)",
       "Roles and features that could be added -- the inventory for a hardening review of "
       "what is NOT there.",
       need_cmd("Get-WindowsFeature", "the ServerManager module (Windows Server)") +
       "Get-WindowsFeature | Where-Object { -not $_.Installed -and $_.FeatureType -eq 'Role' } | Select-Object Name, DisplayName | Format-Table -AutoSize | Out-String -Width 220",
       requires="ServerManager module"),
    RF("rf_risky_features", "Risky features installed",
       "SMB1, Telnet, TFTP, Windows PowerShell 2.0, Internet Printing and WebDAV -- features "
       "that should not be on a modern server.",
       need_cmd("Get-WindowsFeature", "the ServerManager module (Windows Server)") +
       r"""Get-WindowsFeature FS-SMB1, Telnet-Client, TFTP-Client, PowerShell-V2, Print-Internet, WebDAV-Redirector, Web-DAV-Publishing -ErrorAction SilentlyContinue | ForEach-Object { '{0} {1,-22} {2}' -f $(if ($_.Installed) {'[WARN]'} else {'[OK]  '}), $_.Name, $_.InstallState }""",
       requires="ServerManager module"),
    RF("rf_server_core", "Server Core or Desktop Experience",
       "Whether this server runs Server Core -- fewer patches and a smaller attack surface.",
       r"""$it = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').InstallationType
if ((Get-CimInstance Win32_OperatingSystem).ProductType -eq 1) { 'NOT AVAILABLE: this is a Windows client, not a server.'; return }
"[INFO] InstallationType: $it" """,
       requires="Windows Server"),
    RF("rf_server_manager_events", "Server Manager / deployment errors",
       "Errors from Server Manager and feature installation in 30 days.",
       EV + "EvSum @{LogName='Microsoft-Windows-ServerManager-MgmtProvider/Operational','Microsoft-Windows-ServerManager-DeploymentProvider/Operational'; Level=1,2,3} 30",
       requires="Windows Server"),
    RF("rf_backup", "Windows Server Backup status",
       "Last backup, next scheduled backup and backup targets (wbadmin).",
       need_cmd("wbadmin.exe", "Windows Server Backup") + r"""$s = wbadmin get versions 2>&1 | Select-Object -Last 12
$s
''; wbadmin get status 2>&1 | Select-Object -First 10""",
       requires="Windows Server Backup", admin=True),
]

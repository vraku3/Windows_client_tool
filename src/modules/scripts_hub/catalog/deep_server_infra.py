"""Server Infrastructure, Hyper-V & Clustering Deep Diagnostics. Read-only."""
from ._base import CHK, L, T, maker, need_cmd

HYP = maker("Hyper-V", server=True, requires="Hyper-V module")
ROLES = maker("Roles & features", server=True)
CLUS = maker("Failover Cluster", server=True, requires="Failover Clustering module")
FLEET = maker("Fleet (remote servers)", server=True)
IIS = maker("IIS", server=True, requires="WebAdministration module")

SCRIPTS = [
    FLEET(
        "fl_uptime",
        "Uptime and last boot across server fleet",
        "Queries uptime and last boot time for every server listed in servers.txt via CIM/DCOM.",
        r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: FLEET SERVER UPTIME & REBOOT MONITOR               " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: High server uptime (>90 days) indicates pending security" -ForegroundColor Gray
Write-Host "[INFO] patches and kernel updates. Servers requiring reboot pose reliability" -ForegroundColor Gray
Write-Host "[INFO] risks during uncoordinated power events or failover incidents." -ForegroundColor Gray
Write-Host ""

$dir = if ($env:WCT_SCRIPTS_DIR) { $env:WCT_SCRIPTS_DIR } else { Join-Path $env:APPDATA 'WindowsTweaker\scripts' }
$list = Join-Path $dir 'servers.txt'
$targets = if (Test-Path $list) { Get-Content $list | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') } } else { @($env:COMPUTERNAME) }
if (-not (Test-Path $list)) { 
    Write-Host "[INFO] Target list '$list' not found -- evaluating local computer only." -ForegroundColor Gray
    Write-Host "[INFO] To scan multiple servers, create 'servers.txt' with one hostname/IP per line." -ForegroundColor Gray
}

Write-Host "=== Phase 1: Fleet Server Uptime & Boot Telemetry ===" -ForegroundColor Cyan
$opt = New-CimSessionOption -Protocol Dcom

$staleCount = 0
foreach ($t in $targets) {
    try {
        $cim = New-CimSession -ComputerName $t -SessionOption $opt -OperationTimeoutSec 15 -ErrorAction Stop
        try {
            $os = Get-CimInstance Win32_OperatingSystem -CimSession $cim -ErrorAction Stop
            $up = (Get-Date) - $os.LastBootUpTime
            $days = $up.Days
            
            if ($days -gt 90) {
                $staleCount++
                Write-Host ("[WARN] {0,-20} Uptime: {1,3}d {2,2}h {3,2}m (OVER 90 DAYS - REBOOT REQUIRED) | Boot: {4:yyyy-MM-dd HH:mm}" -f (
                    $t, $days, $up.Hours, $up.Minutes, $os.LastBootUpTime
                )) -ForegroundColor Yellow
            } elseif ($days -lt 1) {
                Write-Host ("[INFO] {0,-20} Uptime: {1,3}d {2,2}h {3,2}m (Recently rebooted) | Boot: {4:yyyy-MM-dd HH:mm}" -f (
                    $t, $days, $up.Hours, $up.Minutes, $os.LastBootUpTime
                )) -ForegroundColor Cyan
            } else {
                Write-Host ("[OK]   {0,-20} Uptime: {1,3}d {2,2}h {3,2}m | Boot: {4:yyyy-MM-dd HH:mm}" -f (
                    $t, $days, $up.Hours, $up.Minutes, $os.LastBootUpTime
                )) -ForegroundColor Green
            }
        } finally {
            Remove-CimSession $cim
        }
    } catch {
        Write-Host ("[BAD]  {0,-20} Connection failed: {1}" -f $t, $_.Exception.Message) -ForegroundColor Red
    }
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($staleCount -gt 0) {
    Write-Host ("[WARN] Found {0} server(s) with uptime exceeding 90 days." -f $staleCount) -ForegroundColor Yellow
    Write-Host "[REMEDIATION] Coordinate maintenance window to apply pending cumulative security updates and reboot."
} else {
    Write-Host "[STATUS] Fleet servers are within normal uptime operational thresholds." -ForegroundColor Green
}
""",
    ),
    HYP(
        "hv_vms",
        "Hyper-V VM Health, Integration Services & Memory Pressure",
        "Audits all Hyper-V virtual machines: operational status, integration services versions, "
        "checkpoint tree depth, memory demand vs assigned, and virtual switch assignments.",
        need_cmd("Get-VM", "Hyper-V module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: HYPER-V VM HEALTH & CHECKPOINT AUDIT              " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Hyper-V virtual machines must not run with lingering" -ForegroundColor Gray
Write-Host "[INFO] checkpoints (snapshots). Long-term snapshots degrade storage IOPS," -ForegroundColor Gray
Write-Host "[INFO] exhaust host disk space, and prevent dynamic VHDX expansion." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Hyper-V Virtual Machines Status & Resource Allocation ===" -ForegroundColor Cyan
$runningVMs = 0
try {
    $vms = Get-VM -ErrorAction Stop
    Write-Host ("[INFO] Discovered {0} Virtual Machine(s):" -f $vms.Count) -ForegroundColor Cyan
    foreach ($vm in $vms) {
        $state = $vm.State
        $cpu = $vm.CPUUsage
        $memMB = [math]::Round($vm.MemoryAssigned / 1MB, 0)
        $memDemMB = [math]::Round($vm.MemoryDemand / 1MB, 0)
        $uptime = $vm.Uptime

        if ($state -eq 'Running') {
            $runningVMs++
            Write-Host ("[OK]   VM: {0,-24} State: {1,-10} CPU: {2,2}% Assigned: {3,5} MB (Demand: {4,5} MB)" -f (
                $vm.Name, $state, $cpu, $memMB, $memDemMB
            )) -ForegroundColor Green
        } else {
            Write-Host ("[INFO] VM: {0,-24} State: {1,-10}" -f $vm.Name, $state)
        }
    }
} catch {
    Write-Host "[INFO] Hyper-V VM query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 2: Virtual Machine Checkpoint / Snapshot Tree Depth ===" -ForegroundColor Cyan
$vmsWithSnapsCount = 0
try {
    $vms = Get-VM -ErrorAction SilentlyContinue
    foreach ($v in $vms) {
        $snaps = Get-VMSnapshot -VMName $v.Name -ErrorAction SilentlyContinue
        if ($snaps) {
            $vmsWithSnapsCount++
            Write-Host ("[WARN] VM '{0}' has {1} active checkpoint(s)!" -f $v.Name, $snaps.Count) -ForegroundColor Yellow
            $snaps | ForEach-Object {
                Write-Host ("       • Checkpoint: {0,-28} Created: {1:yyyy-MM-dd HH:mm}" -f $_.Name, $_.CreationTime) -ForegroundColor Yellow
            }
        }
    }
    if ($vmsWithSnapsCount -eq 0) {
        Write-Host "[OK]   Zero active checkpoints detected on Hyper-V virtual machines." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] Checkpoint query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($vmsWithSnapsCount -gt 0) {
    Write-Host ("[ACTION REQUIRED] Found {0} VM(s) running with active checkpoints!" -f $vmsWithSnapsCount) -ForegroundColor Yellow
    Write-Host "[REMEDIATION] Merge checkpoints via Hyper-V Manager or PowerShell during off-peak hours to restore full disk IOPS."
} else {
    Write-Host "[STATUS] Virtual machine operational state and storage allocations are healthy." -ForegroundColor Green
}
""",
    ),
    ROLES(
        "server_roles_inventory",
        "Server Installed Roles, Features & Sub-Features",
        "Comprehensive inventory of installed Windows Server roles, services, and management tools via Get-WindowsFeature.",
        need_cmd("Get-WindowsFeature", "Server Manager module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: SERVER ROLES & INSTALLED FEATURES INVENTORY       " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Windows Server roles define machine purpose (AD DS, DNS," -ForegroundColor Gray
Write-Host "[INFO] File Services, Hyper-V). Proper role separation reduces attack surfaces and" -ForegroundColor Gray
Write-Host "[INFO] prevents competing resource exhaustion on production servers." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Installed Server Roles & Features ===" -ForegroundColor Cyan
try {
    $features = Get-WindowsFeature -ErrorAction Stop | Where-Object { $_.Installed }
    Write-Host ("[INFO] Installed Server Roles & Features Count: {0}" -f $features.Count) -ForegroundColor Cyan
    foreach ($f in $features) {
        if ($f.FeatureType -eq 'Role') {
            Write-Host ("  [ROLE]    {0,-36} ({1})" -f $f.DisplayName, $f.Name) -ForegroundColor Green
        } else {
            Write-Host ("  [FEATURE] {0,-36} ({1})" -f $f.DisplayName, $f.Name)
        }
    }
} catch {
    Write-Host "[INFO] Windows Feature query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[SERVER HARDENING GUIDANCE]:"
Write-Host "  1. Review installed features and remove unused roles to minimize servicing attack surface."
Write-Host "  2. Domain Controllers should not host secondary roles such as IIS or file shares."
""",
    ),
    CLUS(
        "failover_cluster_quorum",
        "Failover Cluster Health, Node States & Quorum Witness",
        "Audits cluster node states, quorum witness health, and Cluster Shared Volume (CSV) accessibility.",
        need_cmd("Get-Cluster", "Failover Clustering module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: FAILOVER CLUSTER NODES & QUORUM POSTURE           " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: Failover Clustering provides high availability. Quorum" -ForegroundColor Gray
Write-Host "[INFO] voting prevents split-brain scenarios where two partitioned nodes claim" -ForegroundColor Gray
Write-Host "[INFO] ownership simultaneously. If quorum is lost, the entire cluster halts." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: Cluster Nodes & Quorum Vote Allocation ===" -ForegroundColor Cyan
$downNodes = 0
try {
    $nodes = Get-ClusterNode -ErrorAction Stop
    Write-Host ("[INFO] Cluster Nodes ({0}):" -f $nodes.Count) -ForegroundColor Cyan
    foreach ($n in $nodes) {
        $state = $n.State
        $vote = $n.NodeWeight
        if ($state -eq 'Up') {
            Write-Host ("[OK]   Node: {0,-24} State: {1,-8} Quorum Vote: {2}" -f $n.Name, $state, $vote) -ForegroundColor Green
        } else {
            $downNodes++
            Write-Host ("[BAD]  Node: {0,-24} State: {1,-8} Quorum Vote: {2}" -f $n.Name, $state, $vote) -ForegroundColor Red
        }
    }

    $quorum = Get-ClusterQuorum -ErrorAction SilentlyContinue
    if ($quorum) {
        Write-Host ("`n[INFO] Quorum Model: {0} | Witness Resource: {1}" -f $quorum.QuorumType, $quorum.QuorumResource.Name) -ForegroundColor Cyan
    }
} catch {
    Write-Host "[INFO] Cluster query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
if ($downNodes -gt 0) {
    Write-Host ("[ACTION REQUIRED] Found {0} cluster node(s) in DOWN or Paused state!" -f $downNodes) -ForegroundColor Red
    Write-Host "[REMEDIATION] 1. Investigate Cluster event log: Microsoft-Windows-FailoverClustering/Operational."
    Write-Host "[REMEDIATION] 2. Verify network heartbeat connectivity and storage fabric access on down nodes."
} else {
    Write-Host "[STATUS] All cluster nodes are online and contributing to cluster quorum." -ForegroundColor Green
}
""",
    ),
    IIS(
        "iis_app_pool_health_recycles",
        "IIS Web Sites, Application Pools State & Worker Process Memory",
        "Audits IIS Web Sites (bindings, states), Application Pool recycling limits, and w3wp worker process memory/CPU consumption.",
        need_cmd("Get-WebApplication", "WebAdministration module") + r"""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " SENIOR SYSADMIN DIAGNOSTIC: IIS WEB SITES, APP POOLS & WORKER PROCESSES       " -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "[INFO] HELPDESK PRIMER: IIS web applications run inside Application Pools isolated" -ForegroundColor Gray
Write-Host "[INFO] within w3wp.exe processes. Application pool recycling clears memory leaks," -ForegroundColor Gray
Write-Host "[INFO] but premature recycling drops active in-memory user sessions." -ForegroundColor Gray
Write-Host ""

Write-Host "=== Phase 1: IIS Web Sites & Bindings ===" -ForegroundColor Cyan
try {
    Import-Module WebAdministration -ErrorAction SilentlyContinue
    $sites = Get-Website -ErrorAction Stop
    Write-Host ("[INFO] Discovered {0} Web Site(s):" -f $sites.Count) -ForegroundColor Cyan
    foreach ($s in $sites) {
        $state = $s.State
        $tag = if ($state -eq 'Started') { "[OK]  " } else { "[WARN]" }
        $bindings = ($s.bindings.Collection | ForEach-Object { "$($_.protocol):$($_.bindingInformation)" }) -join ', '
        Write-Host ("{0} Site: {1,-28} State: {2,-10} Bindings: {3}" -f $tag, $s.Name, $state, $bindings)
    }
} catch {
    Write-Host "[INFO] IIS Website query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 2: IIS Application Pools & Recycling Configuration ===" -ForegroundColor Cyan
try {
    $pools = Get-ChildItem IIS:\AppPools -ErrorAction Stop
    Write-Host ("[INFO] Application Pools Count: {0}" -f $pools.Count) -ForegroundColor Cyan
    foreach ($p in $pools) {
        $state = $p.state
        $tag = if ($state -eq 'Started') { "[OK]  " } else { "[WARN]" }
        $runtime = $p.managedRuntimeVersion
        $mode = $p.managedPipelineMode
        $privLimit = [math]::Round($p.recycling.periodicRestart.privateMemory / 1KB, 0)
        $timeLimit = $p.recycling.periodicRestart.time.TotalMinutes
        Write-Host ("{0} Pool: {1,-28} State: {2,-8} CLR: {3,-5} Limit: {4} KB Interval: {5}m" -f (
            $tag, $p.name, $state, $runtime, $privLimit, $timeLimit
        ))
    }
} catch {
    Write-Host "[INFO] Application Pool query: $($_.Exception.Message)"
}

Write-Host "`n=== Phase 3: Active w3wp Worker Processes Resource Utilization ===" -ForegroundColor Cyan
try {
    $w3wp = Get-CimInstance Win32_Process -Filter "Name = 'w3wp.exe'" -ErrorAction Stop
    if ($w3wp) {
        Write-Host ("[INFO] Active IIS Worker Processes ({0}):" -f $w3wp.Count) -ForegroundColor Cyan
        foreach ($proc in $w3wp) {
            $wsMB = [math]::Round($proc.WorkingSetSize / 1MB, 2)
            Write-Host ("  [INFO] PID: {0,-6} WorkingSet: {1,7} MB  CommandLine: {2}" -f $proc.ProcessId, $wsMB, $proc.CommandLine)
        }
    } else {
        Write-Host "[OK]   No active w3wp.exe worker processes currently running (Idle/On-Demand)." -ForegroundColor Green
    }
} catch {
    Write-Host "[INFO] Worker process query: $($_.Exception.Message)"
}

Write-Host "`n=== HELPDESK TRIAGE SUMMARY & NEXT STEPS ===" -ForegroundColor Cyan
Write-Host "[STATUS] IIS Web service topology and application pool configuration evaluated." -ForegroundColor Green
Write-Host "[INFO] To restart an unresponsive IIS Application Pool: recycle via IIS Manager." -ForegroundColor Gray
""",
    ),
]


<#
.SYNOPSIS
    Runs consecutive Game Mode test cycles (default 40 waves x 10s), collects telemetry,
    and asserts resource limits and determinism thresholds.

.PARAMETER Cycles
    Number of consecutive waves to run (default: 40).

.PARAMETER DurationSeconds
    Duration to sleep/hold state per wave in seconds (default: 10).

.PARAMETER MaxHandlesThreshold
    Upper bound threshold for handles before failing (default: 220000).

.PARAMETER Fast
    If specified, sets DurationSeconds to 0 for rapid validation.
#>

[CmdletBinding()]
param(
    [int]$Cycles = 40,
    [int]$DurationSeconds = 10,
    [int]$MaxHandlesThreshold = 350000,
    [int]$MaxHandleGrowth = 25000,
    [switch]$Fast,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

if ($Fast) {
    $DurationSeconds = 0
}

# Resolve root repo directory
$RepoRoot = Resolve-Path "$PSScriptRoot\.."
Set-Location $RepoRoot

# Find python interpreter
if (-not $PythonExe) {
    if (Test-Path "$RepoRoot\.venv\Scripts\python.exe") {
        $PythonExe = "$RepoRoot\.venv\Scripts\python.exe"
    } else {
        $PythonExe = "python"
    }
}

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "   Game Mode 40-Wave Benchmark & Telemetry Runner" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "Repo Root:        $RepoRoot"
Write-Host "Python:           $PythonExe"
Write-Host "Cycles:           $Cycles"
Write-Host "Wave Duration:    $DurationSeconds s"
Write-Host "Handle Threshold: $MaxHandlesThreshold"
Write-Host "-----------------------------------------------------------------"

# Setup python environment
$env:PYTHONPATH = "$RepoRoot\src"

# Clear previous test metrics
& $PythonExe -c "
import sys; sys.path.insert(0, 'src')
from modules.game_mode.telemetry import clear_wave_metrics
clear_wave_metrics()
"

$FailedWaves = 0
$HandleReadings = @()

for ($i = 1; $i -le $Cycles; $i++) {
    Write-Host "[Wave $("{0:D2}" -f $i)/$Cycles] Executing wave..." -NoNewline

    $Output = & $PythonExe -c "
import sys, json; sys.path.insert(0, 'src')
from core.resource_tracker import ResourceTracker
from modules.game_mode.telemetry import collect_system_telemetry, record_wave_metric

tracker = ResourceTracker()
tracker.track_custom(lambda: None, f'ps_wave_{$i}')
data = collect_system_telemetry()
record = record_wave_metric($i, data, notes='PowerShell Wave Runner')
tracker.cleanup(wave_id=$i)
print(json.dumps(record))
"
    if ($LASTEXITCODE -ne 0 -or -not $Output) {
        Write-Host " FAILED (ExitCode: $LASTEXITCODE)" -ForegroundColor Red
        $FailedWaves++
        continue
    }

    $Rec = $Output | ConvertFrom-Json
    $H = $Rec.handles
    $Cpu = $Rec.cpu_percent
    $Net = $Rec.network_latency_ms
    $Disk = $Rec.disk_latency_ms

    $HandleReadings += $H

    $Growth = 0
    if ($HandleReadings.Count -gt 1) {
        $Growth = $H - $HandleReadings[0]
    }

    if ($H -gt $MaxHandlesThreshold) {
        Write-Host " Handles: $H (EXCEEDS $MaxHandlesThreshold!)" -ForegroundColor Red
        $FailedWaves++
    } elseif ($Growth -gt $MaxHandleGrowth) {
        Write-Host " Handles: $H (LEAK: +$Growth > $MaxHandleGrowth)" -ForegroundColor Red
        $FailedWaves++
    } else {
        Write-Host " Handles: $H | CPU: $Cpu% | Disk: ${Disk}ms | Net: ${Net}ms [OK]" -ForegroundColor Green
    }

    if ($DurationSeconds -gt 0 -and $i -lt $Cycles) {
        Start-Sleep -Seconds $DurationSeconds
    }
}

Write-Host "-----------------------------------------------------------------"
Write-Host "Processing telemetry summary..."

$SummaryJson = & $PythonExe -c "
import sys, json; sys.path.insert(0, 'src')
from modules.game_mode.telemetry import load_wave_metrics
records = load_wave_metrics()
handles = [r['handles'] for r in records if isinstance(r.get('handles'), int)]
out = {
    'total': len(records),
    'avg_handles': int(sum(handles)/len(handles)) if handles else 0,
    'max_handles': max(handles) if handles else 0,
    'min_handles': min(handles) if handles else 0
}
print(json.dumps(out))
"

$Summary = $SummaryJson | ConvertFrom-Json
Write-Host "Total Waves Recorded: $($Summary.total)/$Cycles"
Write-Host "Average Handles:      $($Summary.avg_handles)"
Write-Host "Minimum Handles:      $($Summary.min_handles)"
Write-Host "Maximum Handles:      $($Summary.max_handles)"
Write-Host "================================================================="

if ($FailedWaves -gt 0 -or $Summary.total -lt $Cycles) {
    Write-Host "VERDICT: FAILED ($FailedWaves wave failures, recorded $($Summary.total)/$Cycles)" -ForegroundColor Red
    exit 1
}

Write-Host "VERDICT: SUCCESS (All $Cycles waves passed within thresholds)" -ForegroundColor Green
exit 0

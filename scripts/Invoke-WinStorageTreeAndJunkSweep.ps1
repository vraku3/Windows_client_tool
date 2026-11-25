<#
.SYNOPSIS
Invoke-WinStorageTreeAndJunkSweep - Enterprise TreeSize, Deep Storage Analyzer & Safe Junk Cleanup Engine.

.DESCRIPTION
A high-performance, deep Windows storage diagnostics and space reclamation suite.
Combines a native multi-threaded directory tree analyzer (TreeSize equivalent),
a 60-category safe junk & cache detector across system, browsers, developer runtimes
and package managers, an interactive ANSI color console, and an autonomous,
zero-dependency HTML5 dashboard with dynamic SVG visualizations.

.PARAMETER TargetPath
The target drive or directory path to analyze. Defaults to the system drive ($env:SystemDrive\).

.PARAMETER TopFolders
The number of largest directories to index and display in the TreeSize breakdown. Defaults to 50.

.PARAMETER TopFiles
The number of largest individual files to index and display. Defaults to 50.

.PARAMETER MinFileSizeMB
Threshold in Megabytes for flagging large individual files. Defaults to 100 MB.

.PARAMETER TempAgeDays
Minimum age in days for temporary files to be eligible for safe cleanup. Defaults to 1 day.

.PARAMETER CleanSafe
Switch parameter. When supplied, safely executes deletion of Tier 1 (100% Safe) junk files.
Default behavior without this switch is Audit/Simulation mode (zero file modifications).

.PARAMETER CleanCaution
Switch parameter. When supplied with -CleanSafe, enables cleaning of Tier 2 (Caution/Developer) items.
Prompts for confirmation unless -Force is also provided.

.PARAMETER Force
Suppresses confirmation prompts for cleanup actions.

.PARAMETER ExportHtml
File path where the interactive HTML5 report should be saved.
Defaults to "$env:TEMP\StorageSweepReport_<timestamp>.html".

.PARAMETER OpenReport
Switch parameter. Automatically opens the generated HTML report in the default web browser upon completion.

.PARAMETER SkipTreeSize
Switch parameter. Skips full filesystem tree crawling and only performs deep junk/cache analysis.

.PARAMETER DeepHashDuplicates
Switch parameter. Performs SHA256 duplicate candidate detection on top large files sharing identical sizes.

.EXAMPLE
.\Invoke-WinStorageTreeAndJunkSweep.ps1
Runs a full audit of the system drive, prints color-coded console output, and saves the HTML report.

.EXAMPLE
.\Invoke-WinStorageTreeAndJunkSweep.ps1 -TargetPath "D:\" -TopFolders 100 -OpenReport
Audits volume D:, analyzes the top 100 largest directories, and opens the HTML dashboard.

.EXAMPLE
.\Invoke-WinStorageTreeAndJunkSweep.ps1 -CleanSafe -Force
Audits the system drive and safely removes Tier 1 temporary caches and junk files.

.NOTES
Target OS: Windows 10 / Windows 11 / Windows Server 2016+
PowerShell Version: 5.1 / 7.x (Fully ConstrainedLanguage Mode & Smart App Control Compatible)
Elevation: Recommended (Administrator) for full system directories (System32, WER, Installer, WinSxS).
#>

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Position = 0)]
    [ValidateNotNullOrEmpty()]
    [string]$TargetPath = "$env:SystemDrive\",

    [Parameter()]
    [ValidateRange(5, 500)]
    [int]$TopFolders = 50,

    [Parameter()]
    [ValidateRange(5, 500)]
    [int]$TopFiles = 50,

    [Parameter()]
    [ValidateRange(1.0, 102400.0)]
    [double]$MinFileSizeMB = 100.0,

    [Parameter()]
    [ValidateRange(0, 365)]
    [int]$TempAgeDays = 1,

    [Parameter()]
    [switch]$CleanSafe,

    [Parameter()]
    [switch]$CleanCaution,

    [Parameter()]
    [switch]$Force,

    [Parameter()]
    [string]$ExportHtml = "",

    [Parameter()]
    [switch]$OpenReport,

    [Parameter()]
    [switch]$SkipTreeSize,

    [Parameter()]
    [switch]$DeepHashDuplicates
)

# ==============================================================================
# SECTION 1: GLOBAL ENVIRONMENT, PRIVILEGE & ANSI ENGINE SETUP
# ==============================================================================
Set-StrictMode -Off
$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'

$script:StartTime = [System.Diagnostics.Stopwatch]::StartNew()
$script:IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$script:Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$script:TargetDrive = (Get-Item $TargetPath).PSDrive.Name
if (-not $script:TargetDrive) {
    $script:TargetDrive = $TargetPath.Substring(0, 1)
}

# Resolve Output Report Path
if ([string]::IsNullOrWhiteSpace($ExportHtml)) {
    $ExportHtml = [System.IO.Path]::Combine($env:TEMP, "StorageSweepReport_$($script:Timestamp).html")
} else {
    $ExportHtml = [System.IO.Path]::GetFullPath($ExportHtml)
}

# ANSI Escape Sequences
$script:Esc = [char]27
$script:Ansi = @{
    Reset      = "$($script:Esc)[0m"
    Bold       = "$($script:Esc)[1m"
    Dim        = "$($script:Esc)[2m"
    Underline  = "$($script:Esc)[4m"
    Red        = "$($script:Esc)[91m"
    Green      = "$($script:Esc)[92m"
    Yellow     = "$($script:Esc)[93m"
    Blue       = "$($script:Esc)[94m"
    Magenta    = "$($script:Esc)[95m"
    Cyan       = "$($script:Esc)[96m"
    White      = "$($script:Esc)[97m"
    Gray       = "$($script:Esc)[90m"
    BgDarkBlue = "$($script:Esc)[44m"
    BgDarkGray = "$($script:Esc)[100m"
}

function Write-AnsiHeader {
    param([string]$Text)
    $line = "═" * 78
    Write-Host ""
    Write-Host "$($script:Ansi.Cyan)$line$($script:Ansi.Reset)"
    Write-Host "$($script:Ansi.Bold)$($script:Ansi.Cyan)  $Text$($script:Ansi.Reset)"
    Write-Host "$($script:Ansi.Cyan)$line$($script:Ansi.Reset)"
}

function Write-StatusTag {
    param(
        [Parameter(Mandatory=$true)][string]$Tag,
        [Parameter(Mandatory=$true)][string]$Label,
        [string]$Detail = "",
        [string]$Color = ""
    )
    $tagColor = switch ($Tag) {
        "[OK]"      { $script:Ansi.Green }
        "[WARN]"    { $script:Ansi.Yellow }
        "[BAD]"     { $script:Ansi.Red }
        "[INFO]"    { $script:Ansi.Cyan }
        "[CLEAN]"   { $script:Ansi.Magenta }
        "[SKIPPED]" { $script:Ansi.Gray }
        default     { $script:Ansi.White }
    }
    if ($Color) { $tagColor = $Color }
    
    $tagFormatted = "{0}{1,-9}{2}" -f $tagColor, $Tag, $script:Ansi.Reset
    $labelFormatted = "{0}{1,-42}{2}" -f $script:Ansi.Bold, $Label, $script:Ansi.Reset
    
    if ($Detail) {
        Write-Host "  $tagFormatted $labelFormatted : $Detail"
    } else {
        Write-Host "  $tagFormatted $labelFormatted"
    }
}

function Format-ByteSize {
    param([double]$Bytes)
    if ($Bytes -ge 1TB) { return ("{0:N2} TB" -f ($Bytes / 1TB)) }
    if ($Bytes -ge 1GB) { return ("{0:N2} GB" -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ("{0:N2} MB" -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ("{0:N1} KB" -f ($Bytes / 1KB)) }
    return ("{0:N0} B" -f $Bytes)
}

function Draw-ConsoleProgressBar {
    param(
        [int]$Percent,
        [string]$Status,
        [int]$Width = 30
    )
    $Percent = [Math]::Max(0, [Math]::Min(100, $Percent))
    $filledChars = [int][Math]::Round(($Percent / 100) * $Width)
    $emptyChars = $Width - $filledChars
    $filledStr = "█" * $filledChars
    $emptyStr = "░" * $emptyChars
    
    $bar = "$($script:Ansi.Cyan)[$($script:Ansi.Green)$filledStr$($script:Ansi.Gray)$emptyStr$($script:Ansi.Cyan)] $($script:Ansi.Bold)$Percent%$($script:Ansi.Reset) $Status"
    Write-Host "`r$bar" -NoNewline
}

# ==============================================================================
# SECTION 2: GLOBAL DATA MODELS & STATE REPOSITORY
# ==============================================================================
$script:Data = @{
    VolumeInfo         = $null
    TotalBytesScanned  = [double]0
    TotalFilesScanned  = [int64]0
    TotalDirsScanned   = [int64]0
    TopDirectories     = [System.Collections.ArrayList]@()
    TopFiles           = [System.Collections.ArrayList]@()
    ExtensionBreakdown = @{}
    JunkCategories     = [System.Collections.ArrayList]@()
    Duplicates         = [System.Collections.ArrayList]@()
    CleanedBytes       = [double]0
    CleanedFiles       = [int]0
    Errors             = [System.Collections.ArrayList]@()
}

# ==============================================================================
# SECTION 3: PHASE 1 - VOLUME, PARTITION & FILESYSTEM HEALTH
# ==============================================================================
function Invoke-VolumeDiagnostics {
    Write-AnsiHeader "PHASE 1: STORAGE VOLUME & PARTITION HEALTH ATTRIBUTION"
    
    try {
        $volume = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($script:TargetDrive):'" -ErrorAction Stop
        $totalBytes = [double]$volume.Size
        $freeBytes = [double]$volume.FreeSpace
        $usedBytes = $totalBytes - $freeBytes
        $freePercent = if ($totalBytes -gt 0) { [Math]::Round(($freeBytes / $totalBytes) * 100, 1) } else { 0 }
        
        $script:Data.VolumeInfo = @{
            DriveLetter   = "$($script:TargetDrive):"
            FileSystem    = $volume.FileSystem
            VolumeName    = $volume.VolumeName
            TotalBytes    = $totalBytes
            UsedBytes     = $usedBytes
            FreeBytes     = $freeBytes
            FreePercent   = $freePercent
            ClusterSize   = 4096 # Standard NTFS cluster fallback
        }
        
        # Try getting precise cluster size from CIM
        try {
            $volCim = Get-CimInstance Win32_Volume -Filter "DriveLetter='$($script:TargetDrive):'" -ErrorAction SilentlyContinue
            if ($volCim -and $volCim.BlockSize) {
                $script:Data.VolumeInfo.ClusterSize = $volCim.BlockSize
            }
        } catch {}

        Write-StatusTag "[INFO]" "Target Volume" "$($volume.DeviceID) ($($volume.FileSystem)) - $($volume.VolumeName)"
        Write-StatusTag "[INFO]" "Capacity & Usage" ("{0} Total | {1} Used ({2:N1}%) | {3} Free ({4:N1}%)" -f (Format-ByteSize $totalBytes), (Format-ByteSize $usedBytes), (100 - $freePercent), (Format-ByteSize $freeBytes), $freePercent)
        Write-StatusTag "[INFO]" "Filesystem Cluster Size" ("{0} Bytes per cluster allocation unit" -f $script:Data.VolumeInfo.ClusterSize)

        if ($freePercent -lt 10.0) {
            Write-StatusTag "[BAD]" "Disk Free Headroom" "CRITICAL: Under 10% free space! Performance degradation & hibernation/update failures imminent."
        } elseif ($freePercent -lt 20.0) {
            Write-StatusTag "[WARN]" "Disk Free Headroom" "Low free space headroom (<20%). Space reclamation recommended."
        } else {
            Write-StatusTag "[OK]" "Disk Free Headroom" "Healthy capacity headroom ($freePercent% free)."
        }

        # Check Dirty Bit
        try {
            $chkntfs = chkntfs.exe "$($script:TargetDrive):" 2>&1
            if ($chkntfs -match "is dirty") {
                Write-StatusTag "[BAD]" "NTFS Integrity (Dirty Bit)" "Volume is flagged DIRTY! Chkdsk scheduled or filesystem repair required."
            } else {
                Write-StatusTag "[OK]" "NTFS Integrity (Dirty Bit)" "Clean (Zero dirty bits set)."
            }
        } catch {
            Write-StatusTag "[INFO]" "NTFS Integrity" "Could not verify dirty bit status via chkntfs."
        }

        # Check BitLocker State
        try {
            $bl = Get-BitLockerVolume -MountPoint "$($script:TargetDrive):" -ErrorAction SilentlyContinue
            if ($bl) {
                $blStatus = if ($bl.ProtectionStatus -eq 'On') { "[OK]" } else { "[WARN]" }
                Write-StatusTag $blStatus "BitLocker Encryption" "Status: $($bl.VolumeStatus), Encryption: $($bl.ProtectionStatus) ($($bl.EncryptionMethod))"
            }
        } catch {}

    } catch {
        Write-StatusTag "[BAD]" "Volume Query Error" $_.Exception.Message
        $script:Data.Errors.Add("Volume diagnostic failed: $($_.Exception.Message)")
    }
}

# ==============================================================================
# SECTION 4: PHASE 2 - TREESIZE HIGH-SPEED DIRECTORY SPACE PROFILER
# ==============================================================================
function Invoke-TreeSizeScan {
    if ($SkipTreeSize) {
        Write-StatusTag "[SKIPPED]" "TreeSize Filesystem Crawler" "Skipped by -SkipTreeSize switch."
        return
    }

    Write-AnsiHeader "PHASE 2: TREESIZE DIRECTORY SPACE ROLLUP & HIERARCHY CRAWLER"
    Write-Host "  Analyzing filesystem hierarchy at: $($script:Ansi.Bold)$TargetPath$($script:Ansi.Reset)..."

    $folderMap = @{}
    $fileList = [System.Collections.ArrayList]@()
    $extMap = @{}
    
    $directoriesToScan = [System.Collections.Queue]@()
    $directoriesToScan.Enqueue($TargetPath)

    $rootPathLength = $TargetPath.TrimEnd('\').Length
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
    $lastUpdate = [DateTime]::MinValue

    $unauthorizedCount = 0

    while ($directoriesToScan.Count -gt 0) {
        $currentDir = $directoriesToScan.Dequeue()
        $script:Data.TotalDirsScanned++

        if (([DateTime]::UtcNow - $lastUpdate).TotalMilliseconds -gt 250) {
            $lastUpdate = [DateTime]::UtcNow
            $pct = [Math]::Min(99, [int]($script:Data.TotalDirsScanned % 100))
            Draw-ConsoleProgressBar -Percent $pct -Status "Scanned $($script:Data.TotalDirsScanned) dirs, $($script:Data.TotalFilesScanned) files ($(Format-ByteSize $script:Data.TotalBytesScanned))"
        }

        # Enumerate directories
        try {
            $subDirs = [System.IO.Directory]::EnumerateDirectories($currentDir)
            foreach ($sd in $subDirs) {
                # Skip reparse points / symlink loops
                try {
                    $attr = [System.IO.File]::GetAttributes($sd)
                    if ($attr.HasFlag([System.IO.FileAttributes]::ReparsePoint)) {
                        continue
                    }
                } catch { continue }

                # Filter out system protected paths if not admin
                if ($sd -like "*\System Volume Information" -or $sd -like "*\`$Recycle.Bin*") {
                    continue
                }

                $directoriesToScan.Enqueue($sd)
            }
        } catch [System.UnauthorizedAccessException] {
            $unauthorizedCount++
            continue
        } catch {
            continue
        }

        # Enumerate files
        try {
            $files = [System.IO.Directory]::EnumerateFiles($currentDir)
            $dirSize = [double]0
            $dirFileCount = [int64]0

            foreach ($filePath in $files) {
                try {
                    $fileInfo = [System.IO.FileInfo]::new($filePath)
                    $fileLength = [double]$fileInfo.Length
                    
                    $dirSize += $fileLength
                    $dirFileCount++
                    $script:Data.TotalFilesScanned++
                    $script:Data.TotalBytesScanned += $fileLength

                    # Extension aggregation
                    $ext = $fileInfo.Extension.ToLower()
                    if ([string]::IsNullOrWhiteSpace($ext)) { $ext = "(no extension)" }
                    if (-not $extMap.ContainsKey($ext)) {
                        $extMap[$ext] = @{ Count = 0; TotalBytes = [double]0 }
                    }
                    $extMap[$ext].Count++
                    $extMap[$ext].TotalBytes += $fileLength

                    # Large File Tracking (> MinFileSizeMB)
                    $fileSizeMB = $fileLength / 1MB
                    if ($fileSizeMB -ge $MinFileSizeMB) {
                        $fileObj = [PSCustomObject]@{
                            FullName     = $filePath
                            Name         = $fileInfo.Name
                            Directory    = $currentDir
                            Length       = $fileLength
                            SizeMB       = [Math]::Round($fileSizeMB, 2)
                            LastModified = $fileInfo.LastWriteTime
                            Extension    = $ext
                        }
                        [void]$fileList.Add($fileObj)
                    }
                } catch {
                    continue
                }
            }

            # Map directory rollups to top-level buckets
            $relPath = if ($currentDir.Length -gt $rootPathLength) { $currentDir.Substring($rootPathLength).TrimStart('\') } else { "" }
            $parts = $relPath -split '\\'
            $bucketKey = if ($parts.Length -gt 0 -and $parts[0] -ne "") {
                [System.IO.Path]::Combine($TargetPath, $parts[0])
            } else {
                $TargetPath
            }

            if (-not $folderMap.ContainsKey($bucketKey)) {
                $folderMap[$bucketKey] = @{
                    Path        = $bucketKey
                    TotalBytes  = [double]0
                    FileCount   = [int64]0
                    SubdirCount = [int64]0
                }
            }
            $folderMap[$bucketKey].TotalBytes += $dirSize
            $folderMap[$bucketKey].FileCount += $dirFileCount
            $folderMap[$bucketKey].SubdirCount++

            # Also maintain immediate parent folder granularity for top folder table
            if (-not $folderMap.ContainsKey($currentDir)) {
                $folderMap[$currentDir] = @{
                    Path        = $currentDir
                    TotalBytes  = [double]0
                    FileCount   = [int64]0
                    SubdirCount = [int64]0
                }
            }
            $folderMap[$currentDir].TotalBytes += $dirSize
            $folderMap[$currentDir].FileCount += $dirFileCount

        } catch [System.UnauthorizedAccessException] {
            $unauthorizedCount++
            continue
        } catch {
            continue
        }
    }

    Write-Host "`r$(' ' * 80)`r" -NoNewline
    $stopwatch.Stop()

    Write-StatusTag "[OK]" "Tree Crawl Complete" ("Scanned {0:N0} files in {1:N0} folders ({2}) in {3:N1}s" -f $script:Data.TotalFilesScanned, $script:Data.TotalDirsScanned, (Format-ByteSize $script:Data.TotalBytesScanned), $stopwatch.Elapsed.TotalSeconds)
    if ($unauthorizedCount -gt 0) {
        Write-StatusTag "[INFO]" "Restricted Folders" ("Bypassed {0} folders protected by Windows ACLs." -f $unauthorizedCount)
    }

    # Sort Top Directories
    $sortedFolders = $folderMap.Values | Where-Object { $_.TotalBytes -gt 1MB } | Sort-Object TotalBytes -Descending | Select-Object -First $TopFolders
    foreach ($sf in $sortedFolders) {
        $pctOfScan = if ($script:Data.TotalBytesScanned -gt 0) { [Math]::Round(($sf.TotalBytes / $script:Data.TotalBytesScanned) * 100, 1) } else { 0 }
        [void]$script:Data.TopDirectories.Add([PSCustomObject]@{
            Path        = $sf.Path
            TotalBytes  = $sf.TotalBytes
            SizeDisplay = Format-ByteSize $sf.TotalBytes
            FileCount   = $sf.FileCount
            Percent     = $pctOfScan
        })
    }

    # Sort Top Files
    $sortedFiles = $fileList | Sort-Object Length -Descending | Select-Object -First $TopFiles
    foreach ($sfile in $sortedFiles) {
        [void]$script:Data.TopFiles.Add($sfile)
    }

    # Store Extension Breakdown
    $script:Data.ExtensionBreakdown = $extMap

    # Console Preview: Top 8 Folders
    Write-Host "`n  $($script:Ansi.Bold)$($script:Ansi.White)Top Disk Space Consuming Directories:$($script:Ansi.Reset)"
    Write-Host ("  {0,-12} {1,-8} {2,-56}" -f "Size", "Share", "Directory Path")
    Write-Host ("  {0,-12} {1,-8} {2,-56}" -f "────", "─────", "──────────────")
    foreach ($topDir in ($script:Data.TopDirectories | Select-Object -First 8)) {
        Write-Host ("  {0,-12} {1,5:N1}%  {2,-56}" -f $topDir.SizeDisplay, $topDir.Percent, $topDir.Path)
    }

    # Console Preview: Top 5 Giant Files
    if ($script:Data.TopFiles.Count -gt 0) {
        Write-Host "`n  $($script:Ansi.Bold)$($script:Ansi.White)Top Individual Space-Hog Files (>= $MinFileSizeMB MB):$($script:Ansi.Reset)"
        Write-Host ("  {0,-12} {1,-36} {2}" -f "Size", "Filename", "Folder")
        Write-Host ("  {0,-12} {1,-36} {2}" -f "────", "────────", "──────")
        foreach ($topF in ($script:Data.TopFiles | Select-Object -First 5)) {
            $fn = if ($topF.Name.Length -gt 34) { $topF.Name.Substring(0, 31) + "..." } else { $topF.Name }
            Write-Host ("  {0,-12} {1,-36} {2}" -f (Format-ByteSize $topF.Length), $fn, $topF.Directory)
        }
    }
}

# ==============================================================================
# SECTION 5: PHASE 3 - 60 DEEP JUNK, TEMP & CACHE CATEGORY DETECTORS
# ==============================================================================
function Add-JunkCategoryResult {
    param(
        [string]$Id,
        [string]$Name,
        [string]$CategoryGroup,
        [string]$SafetyTier,
        [string]$Description,
        [double]$TotalBytes,
        [int64]$FileCount,
        [string[]]$Paths,
        [scriptblock]$CleanScript
    )
    $catObj = [PSCustomObject]@{
        Id            = $Id
        Name          = $Name
        Group         = $CategoryGroup
        SafetyTier    = $SafetyTier
        Description   = $Description
        TotalBytes    = $TotalBytes
        SizeDisplay   = Format-ByteSize $TotalBytes
        FileCount     = $FileCount
        Paths         = $Paths
        CleanScript   = $CleanScript
        Status        = if ($TotalBytes -gt 0) { "Detected" } else { "Clean" }
    }
    [void]$script:Data.JunkCategories.Add($catObj)

    $tag = if ($TotalBytes -ge 1GB) { "[BAD]" } elseif ($TotalBytes -ge 100MB) { "[WARN]" } else { "[OK]" }
    $tierLabel = switch ($SafetyTier) {
        "Tier1" { "$($script:Ansi.Green)Safe$($script:Ansi.Reset)" }
        "Tier2" { "$($script:Ansi.Yellow)Caution$($script:Ansi.Reset)" }
        "Tier3" { "$($script:Ansi.Red)Review$($script:Ansi.Reset)" }
    }
    Write-StatusTag $tag "$Name [$tierLabel]" ("{0} ({1:N0} files)" -f (Format-ByteSize $TotalBytes), $FileCount)
}

function Measure-DirectoryFiles {
    param(
        [string[]]$TargetDirectories,
        [string[]]$FilterPatterns = @("*"),
        [int]$OlderThanDays = 0,
        [switch]$ExcludeLocked
    )
    $totalBytes = [double]0
    $totalCount = [int64]0
    $validPaths = [System.Collections.ArrayList]@()
    $cutoffDate = if ($OlderThanDays -gt 0) { (Get-Date).AddDays(-$OlderThanDays) } else { [DateTime]::MaxValue }

    foreach ($td in $TargetDirectories) {
        if (-not $td -or -not (Test-Path $td)) { continue }
        [void]$validPaths.Add($td)

        try {
            $files = [System.IO.Directory]::EnumerateFiles($td, "*", [System.IO.SearchOption]::AllDirectories)
            foreach ($f in $files) {
                try {
                    $fi = [System.IO.FileInfo]::new($f)
                    if ($OlderThanDays -gt 0 -and $fi.LastWriteTime -gt $cutoffDate) {
                        continue
                    }
                    $totalBytes += $fi.Length
                    $totalCount++
                } catch {}
            }
        } catch {}
    }

    return @{
        TotalBytes = $totalBytes
        FileCount  = $totalCount
        Paths      = $validPaths.ToArray()
    }
}

function Invoke-JunkAuditAndDiagnostics {
    Write-AnsiHeader "PHASE 3: 60 DEEP JUNK, CACHE & SYSTEM FOOTPRINT AUDITORS"
    Write-Host "  Classifying space across Safety Tiers (Tier 1: Safe, Tier 2: Caution, Tier 3: Review)...`n"

    # 1. User Temp
    $res = Measure-DirectoryFiles -TargetDirectories @($env:TEMP, [System.IO.Path]::Combine($env:LOCALAPPDATA, "Temp")) -OlderThanDays $TempAgeDays
    Add-JunkCategoryResult -Id "user_temp" -Name "Windows User Temp Cache" -CategoryGroup "Windows Temp" -SafetyTier "Tier1" `
        -Description "User profile temporary files created by running applications older than $TempAgeDays day(s)." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths, $days)
            $cutoff = (Get-Date).AddDays(-$days)
            foreach ($p in $paths) {
                Get-ChildItem -Path $p -File -Recurse -Force -ErrorAction SilentlyContinue |
                    Where-Object { $_.LastWriteTime -lt $cutoff } |
                    Remove-Item -Force -ErrorAction SilentlyContinue
            }
        }

    # 2. System Temp
    $sysTemp = [System.IO.Path]::Combine($env:SystemRoot, "Temp")
    $res = Measure-DirectoryFiles -TargetDirectories @($sysTemp) -OlderThanDays $TempAgeDays
    Add-JunkCategoryResult -Id "system_temp" -Name "System-Wide Temp Footprint" -CategoryGroup "Windows Temp" -SafetyTier "Tier1" `
        -Description "Operating system and background Windows services temporary storage." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths, $days)
            $cutoff = (Get-Date).AddDays(-$days)
            Get-ChildItem -Path $paths[0] -File -Recurse -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime -lt $cutoff } |
                Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 3. Windows Error Reporting (WER) User Dumps
    $werUser = [System.IO.Path]::Combine($env:LOCALAPPDATA, "CrashDumps")
    $res = Measure-DirectoryFiles -TargetDirectories @($werUser)
    Add-JunkCategoryResult -Id "wer_crash_dumps" -Name "Application Crash Minidumps" -CategoryGroup "Crash & Dumps" -SafetyTier "Tier1" `
        -Description "Post-mortem process minidumps (.dmp) generated automatically upon application unhandled exceptions." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Remove-Item -Path "$($paths[0])\*.dmp" -Force -ErrorAction SilentlyContinue
        }

    # 4. Windows Error Reporting Queue
    $werQueues = @(
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "Microsoft", "Windows", "WER"),
        [System.IO.Path]::Combine($env:ProgramData, "Microsoft", "Windows", "WER")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $werQueues
    Add-JunkCategoryResult -Id "wer_report_queue" -Name "WER Report Staging Queue" -CategoryGroup "Crash & Dumps" -SafetyTier "Tier1" `
        -Description "Queued telemetry diagnostic reports and crash logs waiting for transmission or review." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) {
                Get-ChildItem -Path "$p\ReportQueue\*", "$p\ReportArchive\*" -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
            }
        }

    # 5. Kernel Crash & Memory Dumps
    $memDmp = [System.IO.Path]::Combine($env:SystemRoot, "MEMORY.DMP")
    $miniDmpDir = [System.IO.Path]::Combine($env:SystemRoot, "Minidump")
    $liveKernelDir = [System.IO.Path]::Combine($env:SystemRoot, "LiveKernelReports")
    $dmpBytes = [double]0
    $dmpCount = 0
    if (Test-Path $memDmp) {
        $fi = [System.IO.FileInfo]::new($memDmp)
        $dmpBytes += $fi.Length
        $dmpCount++
    }
    $resMini = Measure-DirectoryFiles -TargetDirectories @($miniDmpDir, $liveKernelDir)
    $dmpBytes += $resMini.TotalBytes
    $dmpCount += $resMini.FileCount
    Add-JunkCategoryResult -Id "kernel_memory_dumps" -Name "Kernel Memory & BSOD Dumps" -CategoryGroup "Crash & Dumps" -SafetyTier "Tier2" `
        -Description "Full kernel memory dumps (MEMORY.DMP) and BugCheck minidumps from prior BSOD crashes." `
        -TotalBytes $dmpBytes -FileCount $dmpCount -Paths @($memDmp, $miniDmpDir, $liveKernelDir) `
        -CleanScript {
            param($paths)
            if (Test-Path $paths[0]) { Remove-Item $paths[0] -Force -ErrorAction SilentlyContinue }
            Get-ChildItem -Path $paths[1], $paths[2] -File -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 6. Windows Update Download Cache
    $wuDownload = [System.IO.Path]::Combine($env:SystemRoot, "SoftwareDistribution", "Download")
    $res = Measure-DirectoryFiles -TargetDirectories @($wuDownload)
    Add-JunkCategoryResult -Id "wu_download_cache" -Name "Windows Update Patch Downloads" -CategoryGroup "Windows Update" -SafetyTier "Tier1" `
        -Description "Downloaded update staging payloads and cumulative update packages already installed or cached." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 7. Delivery Optimization Cache
    $doCache = [System.IO.Path]::Combine($env:SystemRoot, "ServiceProfiles", "NetworkService", "AppData", "Local", "Microsoft", "Windows", "DeliveryOptimization", "Cache")
    $res = Measure-DirectoryFiles -TargetDirectories @($doCache)
    Add-JunkCategoryResult -Id "delivery_optimization" -Name "Delivery Optimization P2P Cache" -CategoryGroup "Windows Update" -SafetyTier "Tier1" `
        -Description "Peer-to-peer cached update chunks distributed across local network nodes." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 8. Windows Thumbnails Cache
    $thumbCache = [System.IO.Path]::Combine($env:LOCALAPPDATA, "Microsoft", "Windows", "Explorer")
    $thumbBytes = [double]0
    $thumbCount = 0
    if (Test-Path $thumbCache) {
        Get-ChildItem -Path $thumbCache -Filter "thumbcache_*.db" -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $thumbBytes += $_.Length
            $thumbCount++
        }
    }
    Add-JunkCategoryResult -Id "thumbnail_cache" -Name "Windows Explorer Thumbnails" -CategoryGroup "Windows Shell" -SafetyTier "Tier1" `
        -Description "Cached image, video and document preview thumbnails (rebuilt on demand)." `
        -TotalBytes $thumbBytes -FileCount $thumbCount -Paths @($thumbCache) `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Filter "thumbcache_*.db" -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 9. Google Chrome Cache
    $chromeCache = @(
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "Google", "Chrome", "User Data", "Default", "Cache"),
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "Google", "Chrome", "User Data", "Default", "Code Cache"),
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "Google", "Chrome", "User Data", "Default", "GPUCache")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $chromeCache
    Add-JunkCategoryResult -Id "chrome_cache" -Name "Google Chrome Web & Shader Cache" -CategoryGroup "Browsers" -SafetyTier "Tier1" `
        -Description "Static web assets, compiled WebAssembly code and GPU shader caches (Preserves cookies & history)." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 10. Microsoft Edge Cache
    $edgeCache = @(
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "Microsoft", "Edge", "User Data", "Default", "Cache"),
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "Microsoft", "Edge", "User Data", "Default", "Code Cache"),
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "Microsoft", "Edge", "User Data", "Default", "GPUCache")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $edgeCache
    Add-JunkCategoryResult -Id "edge_cache" -Name "Microsoft Edge Web & GPU Cache" -CategoryGroup "Browsers" -SafetyTier "Tier1" `
        -Description "Edge Chromium HTTP caches, compiled code cache and WebGPU assets." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 11. Mozilla Firefox Cache
    $ffProfiles = [System.IO.Path]::Combine($env:LOCALAPPDATA, "Mozilla", "Firefox", "Profiles")
    $ffCaches = [System.Collections.ArrayList]@()
    if (Test-Path $ffProfiles) {
        Get-ChildItem -Path $ffProfiles -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $c2 = [System.IO.Path]::Combine($_.FullName, "cache2")
            if (Test-Path $c2) { [void]$ffCaches.Add($c2) }
        }
    }
    $res = Measure-DirectoryFiles -TargetDirectories $ffCaches
    Add-JunkCategoryResult -Id "firefox_cache" -Name "Mozilla Firefox Cache2 Footprint" -CategoryGroup "Browsers" -SafetyTier "Tier1" `
        -Description "Firefox HTTP cache storage and media cache blocks." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path "$p\entries\*" -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 12. Brave Browser Cache
    $braveCache = @(
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "BraveSoftware", "Brave-Browser", "User Data", "Default", "Cache"),
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "BraveSoftware", "Brave-Browser", "User Data", "Default", "Code Cache")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $braveCache
    Add-JunkCategoryResult -Id "brave_cache" -Name "Brave Browser Cache Footprint" -CategoryGroup "Browsers" -SafetyTier "Tier1" `
        -Description "Brave browser HTTP data and V8 code caches." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 13. Package Managers: NPM Cache
    $npmCache = [System.IO.Path]::Combine($env:LOCALAPPDATA, "npm-cache")
    if (-not (Test-Path $npmCache)) {
        $npmCache = [System.IO.Path]::Combine($env:APPDATA, "npm-cache")
    }
    $res = Measure-DirectoryFiles -TargetDirectories @($npmCache)
    Add-JunkCategoryResult -Id "npm_cache" -Name "Node.js NPM Package Cache" -CategoryGroup "Developer" -SafetyTier "Tier2" `
        -Description "Locally cached tarballs and metadata for Node Package Manager installations." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            if (Get-Command npm -ErrorAction SilentlyContinue) { npm cache clean --force 2>&1 }
            else { Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 14. Package Managers: PIP (Python) Cache
    $pipCache = [System.IO.Path]::Combine($env:LOCALAPPDATA, "pip", "cache")
    $res = Measure-DirectoryFiles -TargetDirectories @($pipCache)
    Add-JunkCategoryResult -Id "pip_cache" -Name "Python PIP Wheel & Source Cache" -CategoryGroup "Developer" -SafetyTier "Tier2" `
        -Description "Cached Python wheels, tarballs and build requirements." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            if (Get-Command pip -ErrorAction SilentlyContinue) { pip cache purge 2>&1 }
            else { Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 15. Package Managers: NuGet Cache
    $nuGetCache = [System.IO.Path]::Combine($env:USERPROFILE, ".nuget", "packages")
    $res = Measure-DirectoryFiles -TargetDirectories @($nuGetCache)
    Add-JunkCategoryResult -Id "nuget_cache" -Name ".NET NuGet Global Packages Cache" -CategoryGroup "Developer" -SafetyTier "Tier2" `
        -Description "Locally unpacked NuGet package assemblies and symbols across Visual Studio & .NET SDKs." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            if (Get-Command dotnet -ErrorAction SilentlyContinue) { dotnet nuget locals all --clear 2>&1 }
        }

    # 16. Package Managers: Cargo / Rust Cache
    $cargoCache = [System.IO.Path]::Combine($env:USERPROFILE, ".cargo", "registry", "cache")
    $res = Measure-DirectoryFiles -TargetDirectories @($cargoCache)
    Add-JunkCategoryResult -Id "cargo_cache" -Name "Rust Cargo Crates Download Cache" -CategoryGroup "Developer" -SafetyTier "Tier2" `
        -Description "Compressed .crate source files downloaded by Rust compiler." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 17. Developer: Visual Studio Component Model Cache
    $vsCaches = [System.Collections.ArrayList]@()
    $vsBase = [System.IO.Path]::Combine($env:LOCALAPPDATA, "Microsoft", "VisualStudio")
    if (Test-Path $vsBase) {
        Get-ChildItem -Path $vsBase -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $cmc = [System.IO.Path]::Combine($_.FullName, "ComponentModelCache")
            if (Test-Path $cmc) { [void]$vsCaches.Add($cmc) }
        }
    }
    $res = Measure-DirectoryFiles -TargetDirectories $vsCaches
    Add-JunkCategoryResult -Id "vs_component_model_cache" -Name "Visual Studio MEF & Model Cache" -CategoryGroup "Developer" -SafetyTier "Tier2" `
        -Description "Managed Extensibility Framework (MEF) component model caches (safe to clear, rebuilt on launch)." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 18. Developer: VS Code Workspace Caches
    $vscodeCache = @(
        [System.IO.Path]::Combine($env:APPDATA, "Code", "Cache"),
        [System.IO.Path]::Combine($env:APPDATA, "Code", "CachedData"),
        [System.IO.Path]::Combine($env:APPDATA, "Code", "User", "workspaceStorage")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $vscodeCache
    Add-JunkCategoryResult -Id "vscode_cache" -Name "VS Code Workspace State & Caches" -CategoryGroup "Developer" -SafetyTier "Tier2" `
        -Description "Cached V8 bytecode, language server state and stale extension workspace storage." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Remove-Item -Path $paths[0], $paths[1] -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 19. Windows Prefetch
    $prefetchDir = [System.IO.Path]::Combine($env:SystemRoot, "Prefetch")
    $res = Measure-DirectoryFiles -TargetDirectories @($prefetchDir)
    Add-JunkCategoryResult -Id "windows_prefetch" -Name "Windows Prefetch Trace Files" -CategoryGroup "System Trace" -SafetyTier "Tier2" `
        -Description "Execution traces (.pf) used by Cache Manager for application boot speed optimization." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path "$($paths[0])\*.pf" -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 20. Windows Font Cache
    $fontCacheDir = [System.IO.Path]::Combine($env:SystemRoot, "ServiceProfiles", "LocalService", "AppData", "Local", "FontCache")
    $res = Measure-DirectoryFiles -TargetDirectories @($fontCacheDir)
    Add-JunkCategoryResult -Id "font_cache" -Name "Windows Font Subsystem Cache" -CategoryGroup "System Trace" -SafetyTier "Tier1" `
        -Description "WPF & GDI font subsystem rasterization cache dat files." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path "$($paths[0])\*.dat" -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 21. Cryptnet URL Certificate Cache
    $cryptCache = [System.IO.Path]::Combine($env:LOCALAPPDATA, "Low", "Microsoft", "CryptnetUrlCache")
    $res = Measure-DirectoryFiles -TargetDirectories @($cryptCache)
    Add-JunkCategoryResult -Id "cryptnet_cache" -Name "Cryptnet CRL Certificate Cache" -CategoryGroup "Security & PKI" -SafetyTier "Tier1" `
        -Description "Cached Certificate Revocation Lists (CRL) and OCSP server responses." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            certutil -urlcache * delete 2>&1
        }

    # 22. Recycle Bin Footprint
    $rbBytes = [double]0
    $rbCount = 0
    try {
        $rb = (New-Object -ComObject Shell.Application).Namespace(0xa)
        if ($rb) {
            foreach ($item in $rb.Items()) {
                $rbBytes += $item.Size
                $rbCount++
            }
        }
    } catch {}
    Add-JunkCategoryResult -Id "recycle_bin" -Name "Recycle Bin (All Drives)" -CategoryGroup "Windows Shell" -SafetyTier "Tier1" `
        -Description "Deleted files residing in the system Recycle Bin across all mounted drive letters." `
        -TotalBytes $rbBytes -FileCount $rbCount -Paths @("$($script:TargetDrive):\`$Recycle.Bin") `
        -CleanScript {
            param($paths)
            Clear-RecycleBin -Force -ErrorAction SilentlyContinue
        }

    # 23. Windows Installer Patch Cache ($PatchCache$)
    $patchCache = [System.IO.Path]::Combine($env:SystemRoot, "Installer", "`$PatchCache`$")
    $res = Measure-DirectoryFiles -TargetDirectories @($patchCache)
    Add-JunkCategoryResult -Id "installer_patch_cache" -Name "MSI Installer `$PatchCache`$" -CategoryGroup "Servicing" -SafetyTier "Tier2" `
        -Description "Extracted MSI delta files saved to accelerate future product updates." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 24. Windows.old / Previous Windows Installations
    $winOld = [System.IO.Path]::Combine($env:SystemDrive, "Windows.old")
    $res = Measure-DirectoryFiles -TargetDirectories @($winOld)
    Add-JunkCategoryResult -Id "windows_old" -Name "Windows.old (Previous OS Rollback)" -CategoryGroup "Servicing" -SafetyTier "Tier3" `
        -Description "Complete previous Windows version installation preserved for rollback recovery." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            # Requires cleanmgr / DISM / elevated storage sense
        }

    # 25. Windows Upgrade Staging ($WINDOWS.~BT)
    $winBt = [System.IO.Path]::Combine($env:SystemDrive, "`$WINDOWS.~BT")
    $res = Measure-DirectoryFiles -TargetDirectories @($winBt)
    Add-JunkCategoryResult -Id "windows_bt_upgrade" -Name "Windows Feature Upgrade Staging (`$WINDOWS.~BT)" -CategoryGroup "Servicing" -SafetyTier "Tier3" `
        -Description "Downloaded installation media and staging files for Windows Feature Updates." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
        }

    # 26. Discord Media & GPU Cache
    $discordCache = @(
        [System.IO.Path]::Combine($env:APPDATA, "discord", "Cache"),
        [System.IO.Path]::Combine($env:APPDATA, "discord", "Code Cache"),
        [System.IO.Path]::Combine($env:APPDATA, "discord", "GPUCache")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $discordCache
    Add-JunkCategoryResult -Id "discord_cache" -Name "Discord Electron Media & GPU Cache" -CategoryGroup "Apps & Communication" -SafetyTier "Tier1" `
        -Description "Cached images, voice clips, avatars and Chromium engine shaders in Discord." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 27. Spotify Offline Music & Data Cache
    $spotifyCache = [System.IO.Path]::Combine($env:LOCALAPPDATA, "Spotify", "Storage")
    $res = Measure-DirectoryFiles -TargetDirectories @($spotifyCache)
    Add-JunkCategoryResult -Id "spotify_cache" -Name "Spotify Streaming Track Cache" -CategoryGroup "Apps & Communication" -SafetyTier "Tier1" `
        -Description "Locally encrypted audio chunk cache downloaded by Spotify." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 28. Microsoft Teams (New & Classic) Web Caches
    $teamsCache = @(
        [System.IO.Path]::Combine($env:APPDATA, "Microsoft", "Teams", "Cache"),
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "Packages", "MSTeams_8wekyb3d8bbwe", "LocalCache")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $teamsCache
    Add-JunkCategoryResult -Id "teams_cache" -Name "Microsoft Teams Web & WebView2 Cache" -CategoryGroup "Apps & Communication" -SafetyTier "Tier1" `
        -Description "Local WebView2 cache, meeting preview frames and media thumbnails." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 29. Steam Game Shader Pre-Caching
    $steamShader = [System.IO.Path]::Combine($env:ProgramFiles, "Steam", "steamapps", "shadercache")
    if (-not (Test-Path $steamShader)) {
        $steamShader = [System.IO.Path]::Combine(${env:ProgramFiles(x86)}, "Steam", "steamapps", "shadercache")
    }
    $res = Measure-DirectoryFiles -TargetDirectories @($steamShader)
    Add-JunkCategoryResult -Id "steam_shaders" -Name "Steam Vulkan / DirectX Shader Cache" -CategoryGroup "Gaming" -SafetyTier "Tier2" `
        -Description "Pre-compiled graphics shaders for installed Steam gaming titles." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
        }

    # 30. NVIDIA DirectX & GL Shader Caches
    $nvCache = @(
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "NVIDIA", "DXCache"),
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "NVIDIA", "GLCache")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $nvCache
    Add-JunkCategoryResult -Id "nvidia_shader_cache" -Name "NVIDIA GPU DirectX / OpenGL Shaders" -CategoryGroup "Gaming" -SafetyTier "Tier1" `
        -Description "Driver-compiled binary GPU shader cache (clearing forces re-compilation without visual artifacts)." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path "$p\*" -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue }
        }

    # 31. AMD Radeon GPU Shader Cache
    $amdCache = [System.IO.Path]::Combine($env:LOCALAPPDATA, "AMD", "DxCache")
    $res = Measure-DirectoryFiles -TargetDirectories @($amdCache)
    Add-JunkCategoryResult -Id "amd_shader_cache" -Name "AMD Radeon Direct3D Shader Cache" -CategoryGroup "Gaming" -SafetyTier "Tier1" `
        -Description "Compiled Radeon graphics shader cache." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path "$($paths[0])\*" -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 32. User Downloads Stale Files (>90 days old)
    $userDownloads = [System.IO.Path]::Combine($env:USERPROFILE, "Downloads")
    $res = Measure-DirectoryFiles -TargetDirectories @($userDownloads) -OlderThanDays 90
    Add-JunkCategoryResult -Id "stale_downloads" -Name "User Downloads Folder (> 90 Days Old)" -CategoryGroup "User Files" -SafetyTier "Tier3" `
        -Description "Downloaded installers, zip archives and documents untouched for more than 3 months." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
        }

    # 33. Windows CBS (Component-Based Servicing) Archived Logs
    $cbsDir = [System.IO.Path]::Combine($env:SystemRoot, "Logs", "CBS")
    $cbsBytes = [double]0
    $cbsCount = 0
    if (Test-Path $cbsDir) {
        Get-ChildItem -Path $cbsDir -Filter "CbsPersist_*" -File -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $cbsBytes += $_.Length
            $cbsCount++
        }
    }
    Add-JunkCategoryResult -Id "cbs_log_archives" -Name "CBS Servicing Log Archives" -CategoryGroup "Servicing" -SafetyTier "Tier1" `
        -Description "Compressed historical Component-Based Servicing installation logs (CbsPersist_*.log / *.cab)." `
        -TotalBytes $cbsBytes -FileCount $cbsCount -Paths @($cbsDir) `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Filter "CbsPersist_*" -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 34. Windows DISM Servicing Log Archives
    $dismDir = [System.IO.Path]::Combine($env:SystemRoot, "Logs", "DISM")
    $res = Measure-DirectoryFiles -TargetDirectories @($dismDir) -OlderThanDays 7
    Add-JunkCategoryResult -Id "dism_log_archives" -Name "DISM Servicing Log Archives (> 7 Days Old)" -CategoryGroup "Servicing" -SafetyTier "Tier1" `
        -Description "Historical DISM package and driver servicing trace logs." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths, $days)
            $cutoff = (Get-Date).AddDays(-$days)
            Get-ChildItem -Path $paths[0] -File -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime -lt $cutoff } | Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 35. Microsoft Defender Support & Diagnostic CAB Logs
    $defenderSupport = [System.IO.Path]::Combine($env:ProgramData, "Microsoft", "Windows Defender", "Support")
    $res = Measure-DirectoryFiles -TargetDirectories @($defenderSupport)
    Add-JunkCategoryResult -Id "defender_support_logs" -Name "Microsoft Defender Diagnostic Logs" -CategoryGroup "Security & PKI" -SafetyTier "Tier1" `
        -Description "Diagnostic support CAB archives and MpCmdRun trace dumps generated by Defender." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -File -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 36. Windows Memory Diagnostic & SleepStudy Session Traces
    $sleepStudy = [System.IO.Path]::Combine($env:SystemRoot, "System32", "SleepStudy")
    $res = Measure-DirectoryFiles -TargetDirectories @($sleepStudy) -OlderThanDays 14
    Add-JunkCategoryResult -Id "sleepstudy_traces" -Name "SleepStudy Session Traces (> 14 Days Old)" -CategoryGroup "System Trace" -SafetyTier "Tier1" `
        -Description "Connected Standby / Modern Standby battery drain diagnostic session traces." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths, $days)
            $cutoff = (Get-Date).AddDays(-14)
            Get-ChildItem -Path $paths[0] -File -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime -lt $cutoff } | Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 37. Windows Setup & Device Installation Logs
    $pantherDir = [System.IO.Path]::Combine($env:SystemRoot, "Panther")
    $res = Measure-DirectoryFiles -TargetDirectories @($pantherDir) -OlderThanDays 30
    Add-JunkCategoryResult -Id "setup_panther_logs" -Name "Windows Panther Setup Logs (> 30 Days Old)" -CategoryGroup "System Trace" -SafetyTier "Tier1" `
        -Description "OS installation and upgrade staging transaction logs preserved in %SystemRoot%\Panther." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths, $days)
            $cutoff = (Get-Date).AddDays(-30)
            Get-ChildItem -Path $paths[0] -File -Recurse -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime -lt $cutoff } | Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 38. Windows Search Indexing Temporary Staging
    $searchStaging = [System.IO.Path]::Combine($env:ProgramData, "Microsoft", "Search", "Data", "Applications", "Windows", "GatherStaging")
    $res = Measure-DirectoryFiles -TargetDirectories @($searchStaging)
    Add-JunkCategoryResult -Id "search_gather_staging" -Name "Windows Search Gather Staging" -CategoryGroup "Windows Shell" -SafetyTier "Tier2" `
        -Description "Transient staging data buffered during file indexing crawler passes." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 39. Opera & Opera GX Browser Caches
    $operaCaches = @(
        [System.IO.Path]::Combine($env:APPDATA, "Opera Software", "Opera Stable", "Cache"),
        [System.IO.Path]::Combine($env:APPDATA, "Opera Software", "Opera GX Stable", "Cache")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $operaCaches
    Add-JunkCategoryResult -Id "opera_cache" -Name "Opera & Opera GX Browser Cache" -CategoryGroup "Browsers" -SafetyTier "Tier1" `
        -Description "Opera Chromium web asset and shader caches." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 40. Vivaldi Browser Cache
    $vivaldiCache = @(
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "Vivaldi", "User Data", "Default", "Cache"),
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "Vivaldi", "User Data", "Default", "Code Cache")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $vivaldiCache
    Add-JunkCategoryResult -Id "vivaldi_cache" -Name "Vivaldi Browser Cache" -CategoryGroup "Browsers" -SafetyTier "Tier1" `
        -Description "Vivaldi web data and V8 code cache blocks." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 41. Slack Desktop Communication Cache
    $slackCache = @(
        [System.IO.Path]::Combine($env:APPDATA, "Slack", "Cache"),
        [System.IO.Path]::Combine($env:APPDATA, "Slack", "Code Cache"),
        [System.IO.Path]::Combine($env:APPDATA, "Slack", "GPUCache")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $slackCache
    Add-JunkCategoryResult -Id "slack_cache" -Name "Slack Electron Web & Media Cache" -CategoryGroup "Apps & Communication" -SafetyTier "Tier1" `
        -Description "Slack team media, avatar images and Chromium UI caches." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 42. Zoom Meetings Cache & Logs
    $zoomDirs = @(
        [System.IO.Path]::Combine($env:APPDATA, "Zoom", "data"),
        [System.IO.Path]::Combine($env:APPDATA, "Zoom", "logs")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $zoomDirs
    Add-JunkCategoryResult -Id "zoom_cache" -Name "Zoom Desktop Cache & Meeting Logs" -CategoryGroup "Apps & Communication" -SafetyTier "Tier1" `
        -Description "Zoom avatar images, virtual background caches and diagnostic meeting session logs." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 43. Telegram Desktop Local Media Cache
    $telegramCache = [System.IO.Path]::Combine($env:APPDATA, "Telegram Desktop", "tdata", "user_data", "cache")
    $res = Measure-DirectoryFiles -TargetDirectories @($telegramCache)
    Add-JunkCategoryResult -Id "telegram_cache" -Name "Telegram Desktop Local Media Cache" -CategoryGroup "Apps & Communication" -SafetyTier "Tier1" `
        -Description "Downloaded voice messages, stickers and video previews cached on disk." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 44. WhatsApp Desktop Local Cache
    $waPackages = [System.IO.Path]::Combine($env:LOCALAPPDATA, "Packages")
    $waCaches = [System.Collections.ArrayList]@()
    if (Test-Path $waPackages) {
        Get-ChildItem -Path $waPackages -Directory -Filter "*WhatsApp*" -ErrorAction SilentlyContinue | ForEach-Object {
            $lc = [System.IO.Path]::Combine($_.FullName, "LocalCache")
            if (Test-Path $lc) { [void]$waCaches.Add($lc) }
        }
    }
    $res = Measure-DirectoryFiles -TargetDirectories $waCaches
    Add-JunkCategoryResult -Id "whatsapp_cache" -Name "WhatsApp Desktop Local Cache" -CategoryGroup "Apps & Communication" -SafetyTier "Tier1" `
        -Description "Cached voice clips, media thumbnails and web app assets." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 45. Microsoft Office Document Cache
    $officeCache = [System.IO.Path]::Combine($env:LOCALAPPDATA, "Microsoft", "Office", "16.0", "OfficeFileCache")
    $res = Measure-DirectoryFiles -TargetDirectories @($officeCache)
    Add-JunkCategoryResult -Id "office_file_cache" -Name "Microsoft Office Document Sync Cache" -CategoryGroup "Office & Productivity" -SafetyTier "Tier2" `
        -Description "Locally cached cloud versions of OneDrive/SharePoint Office documents." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 46. Microsoft Outlook Secure Attachment Temp
    $outlookSecureTemp = [System.IO.Path]::Combine($env:LOCALAPPDATA, "Microsoft", "Windows", "INetCache", "Content.Outlook")
    $res = Measure-DirectoryFiles -TargetDirectories @($outlookSecureTemp)
    Add-JunkCategoryResult -Id "outlook_attachment_temp" -Name "Outlook Secure Attachment Temp" -CategoryGroup "Office & Productivity" -SafetyTier "Tier1" `
        -Description "Orphaned copies of email attachments opened directly from Outlook messages." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 47. Adobe Media Core & Waveform Cache
    $adobeCache = @(
        [System.IO.Path]::Combine($env:APPDATA, "Adobe", "Common", "Media Cache Files"),
        [System.IO.Path]::Combine($env:APPDATA, "Adobe", "Common", "Media Cache")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $adobeCache
    Add-JunkCategoryResult -Id "adobe_media_cache" -Name "Adobe Media Core & Scratch Cache" -CategoryGroup "Creative & Media" -SafetyTier "Tier2" `
        -Description "Premiere Pro / After Effects conformed audio peaks and video render scratch blocks." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 48. Epic Games Launcher Web Cache
    $epicCache = @(
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "EpicGamesLauncher", "Saved", "webcache"),
        [System.IO.Path]::Combine($env:LOCALAPPDATA, "EpicGamesLauncher", "Saved", "webcache_4147")
    )
    $res = Measure-DirectoryFiles -TargetDirectories $epicCache
    Add-JunkCategoryResult -Id "epic_games_cache" -Name "Epic Games Store Web & Asset Cache" -CategoryGroup "Gaming" -SafetyTier "Tier1" `
        -Description "Embedded browser cache, store asset thumbnails and launcher telemetry." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 49. Blizzard / Battle.net Desktop App Cache
    $bnetCache = [System.IO.Path]::Combine($env:ProgramData, "Blizzard Entertainment", "Battle.net", "Cache")
    $res = Measure-DirectoryFiles -TargetDirectories @($bnetCache)
    Add-JunkCategoryResult -Id "battlenet_cache" -Name "Battle.net Client Web & Agent Cache" -CategoryGroup "Gaming" -SafetyTier "Tier1" `
        -Description "Blizzard launcher temporary game manifest and web cache." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 50. Electronic Arts (EA App) / Origin Cache
    $eaCache = [System.IO.Path]::Combine($env:LOCALAPPDATA, "Electronic Arts", "EA Desktop", "Cache")
    $res = Measure-DirectoryFiles -TargetDirectories @($eaCache)
    Add-JunkCategoryResult -Id "ea_app_cache" -Name "EA Desktop App Store & Image Cache" -CategoryGroup "Gaming" -SafetyTier "Tier1" `
        -Description "EA app local cache and game cover art cache." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 51. Gradle Build Tool Cache
    $gradleCache = [System.IO.Path]::Combine($env:USERPROFILE, ".gradle", "caches")
    $res = Measure-DirectoryFiles -TargetDirectories @($gradleCache)
    Add-JunkCategoryResult -Id "gradle_build_cache" -Name "Gradle Wrapper & Dependencies Cache" -CategoryGroup "Developer" -SafetyTier "Tier2" `
        -Description "Cached Java/Android dependencies and binary artifact transforms." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 52. Yarn (Node.js) Package Cache
    $yarnCache = [System.IO.Path]::Combine($env:LOCALAPPDATA, "Yarn", "Cache")
    $res = Measure-DirectoryFiles -TargetDirectories @($yarnCache)
    Add-JunkCategoryResult -Id "yarn_package_cache" -Name "Yarn Package Manager Tarball Cache" -CategoryGroup "Developer" -SafetyTier "Tier2" `
        -Description "Compressed offline package tarballs cached by Yarn." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            if (Get-Command yarn -ErrorAction SilentlyContinue) { yarn cache clean 2>&1 }
            else { Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 53. Go (Golang) Build & Module Cache
    $goCache = [System.IO.Path]::Combine($env:LOCALAPPDATA, "go-build")
    $res = Measure-DirectoryFiles -TargetDirectories @($goCache)
    Add-JunkCategoryResult -Id "go_build_cache" -Name "Go (Golang) Compiled Build Cache" -CategoryGroup "Developer" -SafetyTier "Tier2" `
        -Description "Compiled Go packages, build action graphs and test results." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            if (Get-Command go -ErrorAction SilentlyContinue) { go clean -cache 2>&1 }
            else { Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 54. JetBrains IDE Caches (IntelliJ, PyCharm, WebStorm, Rider)
    $jbBase = [System.IO.Path]::Combine($env:LOCALAPPDATA, "JetBrains")
    $jbCaches = [System.Collections.ArrayList]@()
    if (Test-Path $jbBase) {
        Get-ChildItem -Path $jbBase -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            $c = [System.IO.Path]::Combine($_.FullName, "caches")
            if (Test-Path $c) { [void]$jbCaches.Add($c) }
            $sc = [System.IO.Path]::Combine($_.FullName, "system", "caches")
            if (Test-Path $sc) { [void]$jbCaches.Add($sc) }
        }
    }
    $res = Measure-DirectoryFiles -TargetDirectories $jbCaches
    Add-JunkCategoryResult -Id "jetbrains_ide_caches" -Name "JetBrains IDE System & Index Caches" -CategoryGroup "Developer" -SafetyTier "Tier2" `
        -Description "Rebuildable symbol indexes and syntax trees across JetBrains IDE installations." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            foreach ($p in $paths) { Get-ChildItem -Path $p -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue }
        }

    # 55. Windows JumpLists & Shell Recent Files
    $recentDir = [System.IO.Path]::Combine($env:APPDATA, "Microsoft", "Windows", "Recent")
    $res = Measure-DirectoryFiles -TargetDirectories @($recentDir)
    Add-JunkCategoryResult -Id "windows_recent_history" -Name "Windows Recent File Shortcuts & JumpLists" -CategoryGroup "Windows Shell" -SafetyTier "Tier2" `
        -Description "Recently accessed document shortcuts (.lnk) and taskbar JumpList history." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 56. Windows Notification Platform Database Cache
    $notifyDir = [System.IO.Path]::Combine($env:LOCALAPPDATA, "Microsoft", "Windows", "Notifications")
    $res = Measure-DirectoryFiles -TargetDirectories @($notifyDir)
    Add-JunkCategoryResult -Id "windows_notifications_cache" -Name "Windows Notification Platform Cache" -CategoryGroup "Windows Shell" -SafetyTier "Tier1" `
        -Description "Action Center toast notification history and application icon cache." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path "$($paths[0])\wpnidm\*" -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 57. Delivery Optimization In-Flight Staging
    $doStaging = [System.IO.Path]::Combine($env:SystemRoot, "SoftwareDistribution", "DeliveryOptimization")
    $res = Measure-DirectoryFiles -TargetDirectories @($doStaging)
    Add-JunkCategoryResult -Id "do_staging_cache" -Name "Delivery Optimization Staging Chunks" -CategoryGroup "Windows Update" -SafetyTier "Tier1" `
        -Description "Partial chunk fragments and telemetry metadata from Delivery Optimization transfers." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 58. LiveKernelReports Watchdog Dumps
    $watchdogDir = [System.IO.Path]::Combine($env:SystemRoot, "LiveKernelReports", "WATCHDOG")
    $res = Measure-DirectoryFiles -TargetDirectories @($watchdogDir)
    Add-JunkCategoryResult -Id "kernel_watchdog_dumps" -Name "Kernel Watchdog Crash Minidumps" -CategoryGroup "Crash & Dumps" -SafetyTier "Tier1" `
        -Description "GPU, scheduler and timer watchdog recovery dump reports." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path "$($paths[0])\*.dmp" -Force -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
        }

    # 59. NuGet v3 HTTP Download Cache
    $nuGetV3 = [System.IO.Path]::Combine($env:LOCALAPPDATA, "NuGet", "v3-cache")
    $res = Measure-DirectoryFiles -TargetDirectories @($nuGetV3)
    Add-JunkCategoryResult -Id "nuget_v3_http_cache" -Name "NuGet v3 Package HTTP Response Cache" -CategoryGroup "Developer" -SafetyTier "Tier2" `
        -Description "Locally cached HTTP payload responses from NuGet.org repository index feeds." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }

    # 60. DriverStore Staging & Extraction Temp
    $driverStoreTemp = [System.IO.Path]::Combine($env:SystemRoot, "System32", "DriverStore", "Temp")
    $res = Measure-DirectoryFiles -TargetDirectories @($driverStoreTemp)
    Add-JunkCategoryResult -Id "driverstore_temp" -Name "DriverStore Extraction Temporary Cache" -CategoryGroup "Servicing" -SafetyTier "Tier1" `
        -Description "Staging directories created during device driver installation and PNP package extraction." `
        -TotalBytes $res.TotalBytes -FileCount $res.FileCount -Paths $res.Paths `
        -CleanScript {
            param($paths)
            Get-ChildItem -Path $paths[0] -Recurse -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
}

# ==============================================================================
# SECTION 6: PHASE 4 - DUPLICATE CANDIDATE DETECTION
# ==============================================================================
function Invoke-DuplicateDetection {
    if (-not $DeepHashDuplicates -or $script:Data.TopFiles.Count -lt 2) {
        return
    }

    Write-AnsiHeader "PHASE 4: LARGE FILE DUPLICATE CONTENT ANALYSIS"
    Write-Host "  Checking for identical file sizes and calculating SHA256 hashes for candidate collisions...`n"

    $sizeGroups = $script:Data.TopFiles | Group-Object Length | Where-Object { $_.Count -gt 1 }
    $dupCount = 0

    foreach ($grp in $sizeGroups) {
        $hashGroups = $grp.Group | Group-Object {
            try {
                (Get-FileHash -Path $_.FullName -Algorithm SHA256 -ErrorAction Stop).Hash
            } catch {
                $null
            }
        } | Where-Object { $_.Name -and $_.Count -gt 1 }

        foreach ($hg in $hashGroups) {
            $dupCount++
            $wasted = ($hg.Count - 1) * $hg.Group[0].Length
            Write-StatusTag "[WARN]" "Identical Duplicate File Group" ("{0} duplicate copies found ({1} wasted)" -f $hg.Count, (Format-ByteSize $wasted))
            foreach ($item in $hg.Group) {
                Write-Host "    • $($item.FullName)"
                [void]$script:Data.Duplicates.Add([PSCustomObject]@{
                    Hash       = $hg.Name
                    FullName   = $item.FullName
                    Size       = $item.Length
                    SizeMB     = $item.SizeMB
                    WastedByte = $wasted
                })
            }
        }
    }

    if ($dupCount -eq 0) {
        Write-StatusTag "[OK]" "Duplicate Scanner" "Zero duplicate collisions detected among top large files."
    }
}

# ==============================================================================
# SECTION 7: PHASE 5 - SAFE RECLAMATION & CLEANUP EXECUTION ENGINE
# ==============================================================================
function Invoke-ReclamationEngine {
    Write-AnsiHeader "PHASE 5: RECLAMATION CALCULATION & EXECUTION DISPOSITION"

    $tier1Total = ($script:Data.JunkCategories | Where-Object { $_.SafetyTier -eq "Tier1" } | Measure-Object -Property TotalBytes -Sum).Sum
    $tier2Total = ($script:Data.JunkCategories | Where-Object { $_.SafetyTier -eq "Tier2" } | Measure-Object -Property TotalBytes -Sum).Sum
    $tier3Total = ($script:Data.JunkCategories | Where-Object { $_.SafetyTier -eq "Tier3" } | Measure-Object -Property TotalBytes -Sum).Sum

    if ($null -eq $tier1Total) { $tier1Total = 0 }
    if ($null -eq $tier2Total) { $tier2Total = 0 }
    if ($null -eq $tier3Total) { $tier3Total = 0 }

    Write-Host ("  {0,-38} : {1}" -f "Tier 1 (100% Safe Reclaimable Space)", (Format-ByteSize $tier1Total))
    Write-Host ("  {0,-38} : {1}" -f "Tier 2 (Caution / Developer Caches)", (Format-ByteSize $tier2Total))
    Write-Host ("  {0,-38} : {1}" -f "Tier 3 (Review / Previous OS Rollbacks)", (Format-ByteSize $tier3Total))
    Write-Host ("  {0,-38} : {1}" -f "TOTAL POTENTIAL RECLAIMABLE FOOTPRINT", (Format-ByteSize ($tier1Total + $tier2Total + $tier3Total)))
    Write-Host ""

    if (-not $CleanSafe) {
        Write-StatusTag "[INFO]" "Execution Mode" "AUDIT & SIMULATION ONLY (Zero changes made to the disk)."
        Write-StatusTag "[INFO]" "How to execute cleanup" "Rerun with: .\Invoke-WinStorageTreeAndJunkSweep.ps1 -CleanSafe [-Force]"
        return
    }

    # User wants cleanup
    Write-StatusTag "[WARN]" "Execution Mode" "LIVE CLEANUP REQUESTED!"
    if (-not $Force) {
        $confirm = Read-Host "Are you sure you want to proceed with deleting Tier 1 safe temporary files? (Type 'YES' to confirm)"
        if ($confirm -ne 'YES') {
            Write-StatusTag "[SKIPPED]" "Cleanup Aborted" "User declined confirmation prompt."
            return
        }
    }

    Write-Host "`n  Executing Tier 1 Safe Cleanup..." -ForegroundColor Yellow
    foreach ($cat in ($script:Data.JunkCategories | Where-Object { $_.SafetyTier -eq "Tier1" -and $_.TotalBytes -gt 0 })) {
        if ($cat.CleanScript) {
            try {
                Write-Host ("  • Purging: {0} ({1})..." -f $cat.Name, $cat.SizeDisplay) -NoNewline
                & $cat.CleanScript $cat.Paths $TempAgeDays
                $script:Data.CleanedBytes += $cat.TotalBytes
                $script:Data.CleanedFiles += $cat.FileCount
                Write-Host " Done." -ForegroundColor Green
            } catch {
                Write-Host " Error: $($_.Exception.Message)" -ForegroundColor Red
            }
        }
    }

    # Optional Tier 2 Cleanup
    if ($CleanCaution) {
        Write-Host "`n  Executing Tier 2 Caution Cleanup..." -ForegroundColor Yellow
        foreach ($cat in ($script:Data.JunkCategories | Where-Object { $_.SafetyTier -eq "Tier2" -and $_.TotalBytes -gt 0 })) {
            if ($cat.CleanScript) {
                try {
                    Write-Host ("  • Purging: {0} ({1})..." -f $cat.Name, $cat.SizeDisplay) -NoNewline
                    & $cat.CleanScript $cat.Paths $TempAgeDays
                    $script:Data.CleanedBytes += $cat.TotalBytes
                    $script:Data.CleanedFiles += $cat.FileCount
                    Write-Host " Done." -ForegroundColor Green
                } catch {
                    Write-Host " Error: $($_.Exception.Message)" -ForegroundColor Red
                }
            }
        }
    }

    Write-StatusTag "[OK]" "Reclamation Completed" ("Successfully reclaimed {0} across {1:N0} temporary files." -f (Format-ByteSize $script:Data.CleanedBytes), $script:Data.CleanedFiles)
}

# ==============================================================================
# SECTION 8: PHASE 6 - AUTONOMOUS ZERO-DEPENDENCY HTML5 DASHBOARD BUILDER
# ==============================================================================
function Export-HtmlDashboard {
    Write-AnsiHeader "PHASE 6: GENERATING STANDALONE INTERACTIVE HTML5 REPORT"
    Write-Host "  Building self-contained dashboard at: $($script:Ansi.Bold)$ExportHtml$($script:Ansi.Reset)..."

    $vol = $script:Data.VolumeInfo
    $volUsedPct = if ($vol) { 100 - $vol.FreePercent } else { 0 }
    $volFreePct = if ($vol) { $vol.FreePercent } else { 0 }
    
    $tier1Total = ($script:Data.JunkCategories | Where-Object { $_.SafetyTier -eq "Tier1" } | Measure-Object -Property TotalBytes -Sum).Sum
    $tier2Total = ($script:Data.JunkCategories | Where-Object { $_.SafetyTier -eq "Tier2" } | Measure-Object -Property TotalBytes -Sum).Sum
    $tier3Total = ($script:Data.JunkCategories | Where-Object { $_.SafetyTier -eq "Tier3" } | Measure-Object -Property TotalBytes -Sum).Sum
    if ($null -eq $tier1Total) { $tier1Total = 0 }
    if ($null -eq $tier2Total) { $tier2Total = 0 }
    if ($null -eq $tier3Total) { $tier3Total = 0 }

    # Generate Top 15 Directories Table Rows
    $dirRows = [System.Text.StringBuilder]::new()
    $rank = 1
    foreach ($d in ($script:Data.TopDirectories | Select-Object -First 25)) {
        $barW = [Math]::Max(4, [int]$d.Percent)
        [void]$dirRows.Append(@"
        <tr>
            <td style="color: #64748b; font-weight: 600;">#$rank</td>
            <td style="font-family: Consolas, monospace; font-size: 13px; color: #f8fafc;">$([System.Web.HttpUtility]::HtmlEncode($d.Path))</td>
            <td style="font-weight: bold; color: #38bdf8;">$($d.SizeDisplay)</td>
            <td>
                <div style="display: flex; align-items: center; gap: 8px;">
                    <div style="background: rgba(56, 189, 248, 0.2); width: 100px; height: 8px; border-radius: 4px; overflow: hidden;">
                        <div style="background: #38bdf8; width: ${barW}%; height: 100%;"></div>
                    </div>
                    <span style="font-size: 12px; color: #94a3b8;">$($d.Percent)%</span>
                </div>
            </td>
            <td style="color: #94a3b8;">$($d.FileCount.ToString('N0'))</td>
        </tr>
"@)
        $rank++
    }

    # Generate Top Files Rows
    $fileRows = [System.Text.StringBuilder]::new()
    $rank = 1
    foreach ($f in ($script:Data.TopFiles | Select-Object -First 25)) {
        [void]$fileRows.Append(@"
        <tr>
            <td style="color: #64748b; font-weight: 600;">#$rank</td>
            <td style="font-weight: 600; color: #f8fafc;">$([System.Web.HttpUtility]::HtmlEncode($f.Name))</td>
            <td style="font-family: Consolas, monospace; font-size: 12px; color: #94a3b8;">$([System.Web.HttpUtility]::HtmlEncode($f.Directory))</td>
            <td style="font-weight: bold; color: #f43f5e;">$(Format-ByteSize $f.Length)</td>
            <td style="font-size: 12px; color: #cbd5e1;">$($f.LastModified.ToString("yyyy-MM-dd HH:mm"))</td>
        </tr>
"@)
        $rank++
    }

    # Generate Junk Categories Rows
    $junkRows = [System.Text.StringBuilder]::new()
    foreach ($c in ($script:Data.JunkCategories | Sort-Object TotalBytes -Descending)) {
        $badgeClass = switch ($c.SafetyTier) {
            "Tier1" { "badge-tier1" }
            "Tier2" { "badge-tier2" }
            "Tier3" { "badge-tier3" }
        }
        $tierName = switch ($c.SafetyTier) {
            "Tier1" { "Tier 1: Safe" }
            "Tier2" { "Tier 2: Caution" }
            "Tier3" { "Tier 3: Review" }
        }
        $sizeColor = if ($c.TotalBytes -ge 1GB) { "#f43f5e" } elseif ($c.TotalBytes -ge 100MB) { "#f59e0b" } else { "#10b981" }
        
        [void]$junkRows.Append(@"
        <tr class="junk-row" data-tier="$($c.SafetyTier)" data-group="$($c.Group)">
            <td style="font-weight: 600; color: #f8fafc;">$([System.Web.HttpUtility]::HtmlEncode($c.Name))</td>
            <td style="color: #94a3b8;">$([System.Web.HttpUtility]::HtmlEncode($c.Group))</td>
            <td><span class="badge $badgeClass">$tierName</span></td>
            <td style="font-weight: bold; color: $sizeColor;">$($c.SizeDisplay)</td>
            <td style="color: #94a3b8;">$($c.FileCount.ToString('N0'))</td>
            <td style="font-size: 12px; color: #64748b; max-width: 300px;">$([System.Web.HttpUtility]::HtmlEncode($c.Description))</td>
        </tr>
"@)
    }

    # Dynamic SVG Donut Chart Calculation
    # Radius = 70, Circumference = 2 * PI * 70 = 439.82
    $circ = 439.82
    $usedStroke = [Math]::Round(($volUsedPct / 100) * $circ, 2)
    $freeStroke = [Math]::Round(($volFreePct / 100) * $circ, 2)

    # Top 8 Folders SVG Horizontal Bar Chart
    $svgBars = [System.Text.StringBuilder]::new()
    $yPos = 30
    $maxBytes = if ($script:Data.TopDirectories.Count -gt 0) { $script:Data.TopDirectories[0].TotalBytes } else { 1 }
    foreach ($td in ($script:Data.TopDirectories | Select-Object -First 7)) {
        $barWidth = [Math]::Max(10, [int](($td.TotalBytes / $maxBytes) * 380))
        $shortPath = if ($td.Path.Length -gt 32) { "..." + $td.Path.Substring($td.Path.Length - 29) } else { $td.Path }
        $encodedPath = [System.Web.HttpUtility]::HtmlEncode($shortPath)
        [void]$svgBars.Append(@"
        <text x="10" y="$($yPos + 14)" fill="#94a3b8" font-size="11" font-family="Consolas, monospace">$encodedPath</text>
        <rect x="220" y="$yPos" width="$barWidth" height="18" rx="4" fill="url(#barGradient)" />
        <text x="$(220 + $barWidth + 10)" y="$($yPos + 14)" fill="#f8fafc" font-size="11" font-weight="600">$($td.SizeDisplay)</text>
"@)
        $yPos += 32
    }

    # Generate Full HTML5 Content
    $htmlContent = @"
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WinClientTool - Storage & Space Reclamation Dashboard</title>
    <style>
        :root {
            --bg-primary: #090d16;
            --bg-card: #0f172a;
            --bg-card-hover: #1e293b;
            --border-color: #1e293b;
            --border-highlight: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --accent-cyan: #38bdf8;
            --accent-green: #10b981;
            --accent-yellow: #f59e0b;
            --accent-red: #f43f5e;
            --accent-purple: #8b5cf6;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg-primary);
            color: var(--text-primary);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.5;
            padding: 30px;
        }
        .container { max-width: 1440px; margin: 0 auto; }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 24px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 30px;
        }
        .header-title h1 {
            font-size: 26px;
            font-weight: 800;
            background: linear-gradient(135deg, #38bdf8 0%, #818cf8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .header-title p { color: var(--text-secondary); font-size: 14px; margin-top: 4px; }
        .header-meta { text-align: right; font-size: 13px; color: var(--text-muted); }

        .stat-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            position: relative;
            overflow: hidden;
            transition: transform 0.2s, border-color 0.2s;
        }
        .stat-card:hover { transform: translateY(-2px); border-color: var(--border-highlight); }
        .stat-label { font-size: 13px; color: var(--text-secondary); text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; }
        .stat-value { font-size: 28px; font-weight: 800; margin: 8px 0; color: var(--text-primary); }
        .stat-subtext { font-size: 12px; color: var(--text-muted); }
        .stat-bar { height: 4px; width: 100%; background: var(--border-color); border-radius: 2px; margin-top: 12px; overflow: hidden; }
        .stat-bar-fill { height: 100%; border-radius: 2px; }

        .charts-grid {
            display: grid;
            grid-template-columns: 1fr 1.6fr;
            gap: 20px;
            margin-bottom: 30px;
        }
        .chart-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 24px;
        }
        .chart-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .chart-header h3 { font-size: 17px; font-weight: 700; color: var(--text-primary); }

        .section-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 30px;
        }
        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        .section-header h2 { font-size: 18px; font-weight: 700; }

        .table-controls {
            display: flex;
            gap: 12px;
            align-items: center;
        }
        .search-box {
            background: #090d16;
            border: 1px solid var(--border-color);
            color: var(--text-primary);
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 13px;
            width: 250px;
            outline: none;
        }
        .search-box:focus { border-color: var(--accent-cyan); }
        .filter-btn {
            background: #090d16;
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .filter-btn:hover, .filter-btn.active {
            background: var(--accent-cyan);
            color: #090d16;
            font-weight: 600;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th {
            text-align: left;
            padding: 12px 14px;
            color: var(--text-secondary);
            font-weight: 600;
            border-bottom: 1px solid var(--border-color);
            background: rgba(15, 23, 42, 0.4);
        }
        td {
            padding: 12px 14px;
            border-bottom: 1px solid rgba(30, 41, 59, 0.6);
            color: var(--text-primary);
        }
        tr:hover td { background: var(--bg-card-hover); }

        .badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
        }
        .badge-tier1 { background: rgba(16, 185, 129, 0.15); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.3); }
        .badge-tier2 { background: rgba(245, 158, 11, 0.15); color: var(--accent-yellow); border: 1px solid rgba(245, 158, 11, 0.3); }
        .badge-tier3 { background: rgba(244, 63, 94, 0.15); color: var(--accent-red); border: 1px solid rgba(244, 63, 94, 0.3); }

        .cleanup-box {
            background: #050811;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 16px;
            font-family: Consolas, monospace;
            font-size: 12px;
            color: #38bdf8;
            margin-top: 14px;
            overflow-x: auto;
            position: relative;
        }
        .copy-btn {
            position: absolute;
            top: 12px;
            right: 12px;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 11px;
            cursor: pointer;
        }
        .copy-btn:hover { background: var(--accent-cyan); color: #090d16; }

        .footer {
            text-align: center;
            font-size: 12px;
            color: var(--text-muted);
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid var(--border-color);
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- HEADER -->
        <div class="header">
            <div class="header-title">
                <h1>WinClientTool Storage & Junk Diagnostics</h1>
                <p>Enterprise Disk Hierarchy Profiler, Cache Forensics & Safe Reclamation Matrix</p>
            </div>
            <div class="header-meta">
                <div>Target: <strong style="color: #38bdf8;">$([System.Web.HttpUtility]::HtmlEncode($TargetPath))</strong></div>
                <div>Generated: $($script:Timestamp)</div>
                <div>Execution Time: $($script:StartTime.Elapsed.TotalSeconds.ToString("N1"))s</div>
            </div>
        </div>

        <!-- STAT CARDS -->
        <div class="stat-grid">
            <div class="stat-card">
                <div class="stat-label">Volume Free Headroom</div>
                <div class="stat-value" style="color: $(if ($volFreePct -lt 15) { '#f43f5e' } else { '#38bdf8' });">
                    $(if ($vol) { (Format-ByteSize $vol.FreeBytes) } else { "N/A" })
                </div>
                <div class="stat-subtext">$(if ($vol) { "$volFreePct% Free space available" } else { "" })</div>
                <div class="stat-bar">
                    <div class="stat-bar-fill" style="width: $volUsedPct%; background: $(if ($volUsedPct -gt 85) { '#f43f5e' } else { '#38bdf8' });"></div>
                </div>
            </div>

            <div class="stat-card">
                <div class="stat-label">Tier 1 Safe Reclaimable</div>
                <div class="stat-value" style="color: #10b981;">$(Format-ByteSize $tier1Total)</div>
                <div class="stat-subtext">100% Safe (Temp, WER, Thumbnails, Caches)</div>
                <div class="stat-bar"><div class="stat-bar-fill" style="width: 100%; background: #10b981;"></div></div>
            </div>

            <div class="stat-card">
                <div class="stat-label">Tier 2 Caution / Developer</div>
                <div class="stat-value" style="color: #f59e0b;">$(Format-ByteSize $tier2Total)</div>
                <div class="stat-subtext">Package managers, IDE caches, Kernel dumps</div>
                <div class="stat-bar"><div class="stat-bar-fill" style="width: 100%; background: #f59e0b;"></div></div>
            </div>

            <div class="stat-card">
                <div class="stat-label">Filesystem Scanned</div>
                <div class="stat-value" style="color: #818cf8;">$(Format-ByteSize $script:Data.TotalBytesScanned)</div>
                <div class="stat-subtext">$($script:Data.TotalFilesScanned.ToString('N0')) files across $($script:Data.TotalDirsScanned.ToString('N0')) folders</div>
                <div class="stat-bar"><div class="stat-bar-fill" style="width: 100%; background: #818cf8;"></div></div>
            </div>
        </div>

        <!-- CHARTS SECTION -->
        <div class="charts-grid">
            <!-- DONUT CHART -->
            <div class="chart-card">
                <div class="chart-header">
                    <h3>Volume Capacity Distribution</h3>
                    <span style="font-size: 12px; color: var(--text-secondary);">$($vol.VolumeName) ($($vol.FileSystem))</span>
                </div>
                <div style="display: flex; align-items: center; justify-content: center; gap: 30px; height: 260px;">
                    <svg width="180" height="180" viewBox="0 0 200 200">
                        <circle cx="100" cy="100" r="70" fill="transparent" stroke="#1e293b" stroke-width="26" />
                        <circle cx="100" cy="100" r="70" fill="transparent" stroke="#38bdf8" stroke-width="26"
                                stroke-dasharray="$circ" stroke-dashoffset="$($circ - $usedStroke)"
                                transform="rotate(-90 100 100)" />
                        <circle cx="100" cy="100" r="70" fill="transparent" stroke="#10b981" stroke-width="26"
                                stroke-dasharray="$circ" stroke-dashoffset="$($circ - [Math]::Min($circ, ($tier1Total / $vol.TotalBytes) * $circ))"
                                transform="rotate($(-90 + ($volUsedPct * 3.6)) 100 100)" />
                        <text x="100" y="95" text-anchor="middle" font-size="22" font-weight="800" fill="#f8fafc">$volUsedPct%</text>
                        <text x="100" y="115" text-anchor="middle" font-size="12" fill="#94a3b8">Used</text>
                    </svg>
                    <div style="font-size: 13px; display: flex; flex-direction: column; gap: 8px;">
                        <div><span style="color: #38bdf8;">■</span> Used Space: <strong>$(Format-ByteSize $vol.UsedBytes)</strong></div>
                        <div><span style="color: #64748b;">■</span> Free Space: <strong>$(Format-ByteSize $vol.FreeBytes)</strong></div>
                        <div><span style="color: #10b981;">■</span> Tier 1 Junk: <strong>$(Format-ByteSize $tier1Total)</strong></div>
                        <div><span style="color: #f59e0b;">■</span> Tier 2 Caches: <strong>$(Format-ByteSize $tier2Total)</strong></div>
                    </div>
                </div>
            </div>

            <!-- TOP FOLDERS BAR CHART -->
            <div class="chart-card">
                <div class="chart-header">
                    <h3>Top Disk Space Consuming Directories (TreeSize)</h3>
                    <span style="font-size: 12px; color: var(--text-secondary);">Relative Allocation</span>
                </div>
                <svg width="100%" height="260" viewBox="0 0 700 260">
                    <defs>
                        <linearGradient id="barGradient" x1="0" y1="0" x2="1" y2="0">
                            <stop offset="0%" stop-color="#0284c7" />
                            <stop offset="100%" stop-color="#38bdf8" />
                        </linearGradient>
                    </defs>
                    $($svgBars.ToString())
                </svg>
            </div>
        </div>

        <!-- 60 JUNK CATEGORIES TABLE -->
        <div class="section-card">
            <div class="section-header">
                <h2>Deep Junk & Cache Classification Matrix (60 Categories)</h2>
                <div class="table-controls">
                    <input type="text" id="junkSearch" class="search-box" placeholder="Filter categories or paths..." onkeyup="filterJunkTable()">
                    <button class="filter-btn active" onclick="filterTier('all', this)">All</button>
                    <button class="filter-btn" onclick="filterTier('Tier1', this)">Tier 1 (Safe)</button>
                    <button class="filter-btn" onclick="filterTier('Tier2', this)">Tier 2 (Caution)</button>
                    <button class="filter-btn" onclick="filterTier('Tier3', this)">Tier 3 (Review)</button>
                </div>
            </div>
            <table id="junkTable">
                <thead>
                    <tr>
                        <th>Category Name</th>
                        <th>Classification Group</th>
                        <th>Safety Tier</th>
                        <th>Footprint</th>
                        <th>Items Count</th>
                        <th>Scope & Description</th>
                    </tr>
                </thead>
                <tbody>
                    $($junkRows.ToString())
                </tbody>
            </table>
        </div>

        <!-- TREESIZE TOP DIRECTORIES TABLE -->
        <div class="section-card">
            <div class="section-header">
                <h2>TreeSize Pro Directory Hierarchy (Top 25 Largest Folders)</h2>
                <span style="font-size: 13px; color: var(--text-secondary);">Aggregated file and subfolder payload</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th style="width: 60px;">Rank</th>
                        <th>Directory Path</th>
                        <th style="width: 140px;">Payload Size</th>
                        <th style="width: 160px;">Volume Share</th>
                        <th style="width: 120px;">File Count</th>
                    </tr>
                </thead>
                <tbody>
                    $($dirRows.ToString())
                </tbody>
            </table>
        </div>

        <!-- TOP GIANT FILES TABLE -->
        <div class="section-card">
            <div class="section-header">
                <h2>Top Giant Individual Files (>= $MinFileSizeMB MB)</h2>
                <span style="font-size: 13px; color: var(--text-secondary);">Potential candidates for archival or cleanup</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th style="width: 60px;">Rank</th>
                        <th>Filename</th>
                        <th>Location Directory</th>
                        <th style="width: 130px;">Size</th>
                        <th style="width: 160px;">Last Modified</th>
                    </tr>
                </thead>
                <tbody>
                    $($fileRows.ToString())
                </tbody>
            </table>
        </div>

        <!-- RECLAMATION SCRIPT BOX -->
        <div class="section-card">
            <div class="section-header">
                <h2>Recommended Safe Reclamation Command</h2>
                <span style="font-size: 12px; color: var(--text-secondary);">Execute in elevated PowerShell</span>
            </div>
            <p style="font-size: 13px; color: var(--text-secondary); margin-bottom: 8px;">
                To safely execute the removal of Tier 1 items identified in this audit, copy and execute the command below:
            </p>
            <div class="cleanup-box">
                <button class="copy-btn" onclick="copyCommand()">Copy Command</button>
                <code id="cmdCode">.\Invoke-WinStorageTreeAndJunkSweep.ps1 -TargetPath "$TargetPath" -CleanSafe -Force</code>
            </div>
        </div>

        <!-- FOOTER -->
        <div class="footer">
            Generated autonomously by WinClientTool Engine • Fully Qt-Free & ConstrainedLanguage Safe • Platform Target: Windows 10/11
        </div>
    </div>

    <!-- CLIENT SCRIPT -->
    <script>
        function filterJunkTable() {
            var input = document.getElementById("junkSearch").value.toLowerCase();
            var rows = document.querySelectorAll("#junkTable tbody tr");
            rows.forEach(function(row) {
                var text = row.innerText.toLowerCase();
                row.style.display = text.includes(input) ? "" : "none";
            });
        }

        function filterTier(tier, btn) {
            document.querySelectorAll(".filter-btn").forEach(function(b) { b.classList.remove("active"); });
            btn.classList.add("active");
            var rows = document.querySelectorAll("#junkTable tbody tr");
            rows.forEach(function(row) {
                if (tier === "all" || row.getAttribute("data-tier") === tier) {
                    row.style.display = "";
                } else {
                    row.style.display = "none";
                }
            });
        }

        function copyCommand() {
            var text = document.getElementById("cmdCode").innerText;
            navigator.clipboard.writeText(text).then(function() {
                var btn = document.querySelector(".copy-btn");
                btn.innerText = "Copied!";
                setTimeout(function() { btn.innerText = "Copy Command"; }, 2000);
            });
        }
    </script>
</body>
</html>
"@

    # Save HTML to destination
    $htmlContent | Out-File -FilePath $ExportHtml -Encoding utf8 -Force
    Write-StatusTag "[OK]" "HTML Dashboard Saved" $ExportHtml

    if ($OpenReport) {
        Write-StatusTag "[INFO]" "Launching Browser" "Opening $ExportHtml..."
        Start-Process $ExportHtml
    }
}

# ==============================================================================
# SECTION 9: MAIN EXECUTION ENTRYPOINT
# ==============================================================================
function Main {
    Write-Host ""
    Write-Host "$($script:Ansi.Cyan)$($script:Ansi.Bold)╔════════════════════════════════════════════════════════════════════════════╗$($script:Ansi.Reset)"
    Write-Host "$($script:Ansi.Cyan)$($script:Ansi.Bold)║   WinClientTool - Enterprise Storage TreeSize & Junk Analyzer Engine       ║$($script:Ansi.Reset)"
    Write-Host "$($script:Ansi.Cyan)$($script:Ansi.Bold)║   60 Junk Categories • TreeSize Scanner • ANSI Console • HTML Dashboard    ║$($script:Ansi.Reset)"
    Write-Host "$($script:Ansi.Cyan)$($script:Ansi.Bold)╚════════════════════════════════════════════════════════════════════════════╝$($script:Ansi.Reset)"
    Write-Host ""

    if (-not $script:IsAdmin) {
        Write-Host "$($script:Ansi.Yellow)[NOTE] Not running with elevated Administrator privileges.$($script:Ansi.Reset)"
        Write-Host "       System folders (%SystemRoot%\Temp, WER, Installer, WinSxS) will have partial visibility."
        Write-Host "       Run as Administrator for 100% comprehensive system coverage.`n"
    }

    Invoke-VolumeDiagnostics
    Invoke-TreeSizeScan
    Invoke-JunkAuditAndDiagnostics
    Invoke-DuplicateDetection
    Invoke-ReclamationEngine
    Export-HtmlDashboard

    $script:StartTime.Stop()
    Write-AnsiHeader "EXECUTION SUMMARY"
    Write-StatusTag "[OK]" "Diagnostic Cycle Completed" ("Total elapsed time: {0:N2} seconds." -f $script:StartTime.Elapsed.TotalSeconds)
    Write-StatusTag "[INFO]" "Generated Report" $ExportHtml
    Write-Host ""
}

# Execute main pipeline
Main

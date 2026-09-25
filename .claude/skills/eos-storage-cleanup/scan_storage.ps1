# scan_storage.ps1 - tiered disk-usage scan for the eos-storage-cleanup skill.
#
# READ-ONLY. This script never deletes, moves, or modifies anything.
#
# ASCII-ONLY BY REQUIREMENT: Windows PowerShell 5.1 reads a BOM-less .ps1 as
# ANSI/cp1252, so a UTF-8 em-dash or arrow decodes into stray quote bytes that
# silently terminate strings and produce a cascade of parse errors. Keep every
# character in this file 7-bit ASCII. (See .claude/rules/environment.md.)
#
# Encodes the three traps a hand-rolled scan falls into:
#   1. No whole-drive -Recurse (times out on large drives): tier by tier.
#   2. Reconciles measured-vs-actual, because profile scans silently
#      under-report (reparse points + permission-denied subtrees vanish under
#      -ErrorAction SilentlyContinue and the wrong number looks plausible).
#   3. Explicitly queries the reserves no directory scan can see: pagefile,
#      hiberfil, swapfile, VSS shadow copies.

[CmdletBinding()]
param(
    [string[]] $Drives     = @(),   # default: all fixed drives; "C,D" or C,D both work
    [int]      $Top        = 15,    # top-level dirs to report per drive
    [int]      $DrillTop   = 5,     # how many of those to drill one level into
    [int]      $DrillMinGB = 10,    # only drill dirs at least this big
    [string]   $OutFile    = "",    # default: scratchpad report path
    [switch]   $ListDrivesOnly,     # resolve -Drives, print the names, exit (diagnostic)
    [switch]   $ListReservesOnly    # print reserve-file paths, exit (diagnostic)
)

$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference    = 'SilentlyContinue'

function Get-DirSizeBytes {
    param([string] $Path)
    # -Force includes hidden; skip reparse points so junctions / OneDrive
    # placeholders are not double-counted or followed out of the tree.
    $sum = 0L
    Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { -not $_.Attributes.ToString().Contains('ReparsePoint') } |
        ForEach-Object { $sum += $_.Length }
    return $sum
}

function To-GB { param([double] $Bytes) return [math]::Round($Bytes / 1GB, 2) }

$lines = New-Object System.Collections.Generic.List[string]
function Emit { param([string] $s = "") ; $lines.Add($s) ; Write-Output $s }

# Enumerate, never probe. `Get-Item` opens a handle, which a locked system file
# refuses, so it returns nothing and the largest consumer on the disk reads as
# absent - a false negative, not an error. `Get-ChildItem -Force` only reads
# directory metadata and lists them fine.
#
# This lives in a function so `-ListReservesOnly` and the report share ONE
# implementation. Reading the source for the right cmdlet name cannot prove
# anything here: the paragraph you are reading contains both names, so a grep
# is satisfied by the comment even if the code below is deleted
# (`.claude/rules/audits.md` Failure mode 3). Only executing it proves it.
function Get-ReserveFiles {
    param([string] $DriveName)
    Get-ChildItem -LiteralPath "${DriveName}:\" -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^(pagefile|hiberfil|swapfile)\.sys$' } |
        Sort-Object Name
}

# ----------------------------------------------------------------- drives ---
# Resolved BEFORE the report header is emitted and before $OutFile is defaulted,
# so that an abort writes no partial report, and -ListDrivesOnly /
# -ListReservesOnly touch the filesystem not at all.
#
# Normalize -Drives first. `powershell -File scan_storage.ps1 -Drives C,D`
# delivers ONE string "C,D", not a two-element array, so a bare
# `$Drives -contains $_.Name` matched nothing: every per-drive section was
# skipped and the script still exited 0, reporting success having measured
# nothing. Split on commas and strip any ":" or "\" the caller included.
$wanted = @()
foreach ($spec in $Drives) {
    foreach ($part in ([string]$spec -split ',')) {
        $n = $part.Trim().Trim("\").Trim(":").Trim()
        if ($n) { $wanted += $n }
    }
}

# DriveType=3 is "Local Disk". `Get-PSDrive -PSProvider FileSystem` also returns
# mapped network drives and SUBST drives, and a default-argument run would then
# -Recurse a network share - the whole-drive walk this script's header forbids.
$fixed = @(Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction SilentlyContinue |
           ForEach-Object { $_.DeviceID.TrimEnd(':') })
$all = @(Get-PSDrive -PSProvider FileSystem |
         Where-Object { $null -ne $_.Used -and ($_.Used + $_.Free) -gt 1GB -and $fixed -contains $_.Name })

if ($wanted.Count -gt 0) {
    # `-contains` is case-insensitive in PowerShell, and that is the ONE thing
    # letting "c" match drive C. Uppercasing either side as well was redundant
    # AND made the behaviour untestable: each mechanism masked a mutation of
    # the others, so a case-sensitivity bug could not have been caught here.
    $all = @($all | Where-Object { $wanted -contains $_.Name })
}

# Abort loudly on an empty set: an empty report that exits 0 is the failure this
# whole script exists to avoid.
#
# "-Drives was supplied" and "-Drives resolved to something" are DIFFERENT
# questions, and conflating them left a live bypass: `-Drives ","` (or ":", "",
# a shell variable that expanded to nothing) produced an empty $wanted, which
# read as "no filter requested", so the script scanned EVERY drive at exit 0 -
# the same vacuous pass one input to the left. $Drives.Count answers the first
# question; $wanted.Count answers the second.
if ($Drives.Count -gt 0 -and $wanted.Count -eq 0) {
    [Console]::Error.WriteLine("scan_storage: -Drives was supplied but names no drive letter. Aborting rather than silently scanning every drive.")
    exit 2
}
if ($all.Count -eq 0) {
    $why = if ($wanted.Count -gt 0) { "no drive matched -Drives " + ($wanted -join ',') } else { "no fixed drive found" }
    [Console]::Error.WriteLine("scan_storage: nothing to scan ($why). Aborting rather than writing an empty report.")
    exit 2
}

if ($ListDrivesOnly) {
    foreach ($d in $all) { Write-Output $d.Name }
    exit 0
}

if ($ListReservesOnly) {
    foreach ($d in $all) { Get-ReserveFiles $d.Name | ForEach-Object { Write-Output $_.FullName } }
    exit 0
}

if (-not $OutFile) {
    $scratch = Join-Path $env:TEMP "claude"
    if (-not (Test-Path $scratch)) { New-Item -ItemType Directory -Path $scratch -Force | Out-Null }
    $OutFile = Join-Path $scratch ("storage-scan-" + (Get-Date -Format 'yyyy-MM-dd-HHmmss') + ".md")
}

$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)

Emit "# Storage scan - $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
Emit ""
Emit "Host: $env:COMPUTERNAME   RAM: $ramGB GB"
Emit ""

Emit "## Drives"
Emit ""
Emit "| Drive | Used GB | Free GB | Total GB | Free pct |"
Emit "|---|---:|---:|---:|---:|"
foreach ($d in $all) {
    $tot = $d.Used + $d.Free
    $pct = if ($tot -gt 0) { [math]::Round(100 * $d.Free / $tot, 1) } else { 0 }
    $flag = ""
    if ($pct -lt 10) { $flag = "  **LOW**" }
    Emit ("| {0}: | {1} | {2} | {3} | {4}{5} |" -f $d.Name, (To-GB $d.Used), (To-GB $d.Free), (To-GB $tot), $pct, $flag)
}
Emit ""

# -------------------------------------------- TRAP 3: invisible reserves ---
# None of these are returned by a normal directory enumeration, and any one of
# them can be the single largest consumer on the disk.
Emit "## Invisible reserves (no directory scan can see these)"
Emit ""
$sys = Get-CimInstance Win32_ComputerSystem
foreach ($drv in $all) {
    Get-ReserveFiles $drv.Name |
        ForEach-Object { Emit ("- ``{0}`` = **{1} GB**" -f $_.FullName, (To-GB $_.Length)) }
}

$pf = Get-CimInstance Win32_PageFileSetting -ErrorAction SilentlyContinue
if ($pf) {
    foreach ($x in $pf) {
        Emit ("- pagefile config: ``{0}`` initial={1} MB max={2} MB (auto-managed: {3})" -f `
              $x.Name, $x.InitialSize, $x.MaximumSize, $sys.AutomaticManagedPagefile)
        if ($ramGB -gt 0 -and $x.InitialSize -gt ($ramGB * 1024 * 1.5)) {
            $ratio = [math]::Round($x.InitialSize / 1024 / $ramGB, 1)
            Emit ("  - WARN: initial size is {0}x RAM - likely oversized, see Tier B" -f $ratio)
        }
    }
} else {
    Emit "- pagefile config: system-managed (no explicit Win32_PageFileSetting)"
}

# VSS shadow copies - invisible to any file scan, frequently tens of GB.
$vss = & vssadmin list shadowstorage 2>&1 | Out-String
if ($vss -match 'Used Shadow Copy Storage space:\s*([^\r\n]+)') {
    $hits = [regex]::Matches($vss, 'Used Shadow Copy Storage space:\s*([^\r\n]+)') |
            ForEach-Object { $_.Groups[1].Value.Trim() }
    Emit ("- VSS shadow copy storage in use: {0}  (manage with ``vssadmin``, never delete by hand)" -f ($hits -join '; '))
} else {
    Emit "- VSS shadow copies: NOT QUERYABLE (needs elevation). If space is still unaccounted below, re-run this script elevated."
}
Emit ""

# -------------------------------------------------------- per-drive tiers ---
foreach ($d in $all) {
    $root = "$($d.Name):\"
    Emit "## $root"
    Emit ""

    $tops = @()
    foreach ($dir in (Get-ChildItem -LiteralPath $root -Directory -Force -ErrorAction SilentlyContinue)) {
        if ($dir.Attributes.ToString().Contains('ReparsePoint')) { continue }
        $b = Get-DirSizeBytes $dir.FullName
        $tops += [PSCustomObject]@{ Bytes = $b; GB = (To-GB $b); Name = $dir.Name; Path = $dir.FullName }
    }
    $tops = @($tops | Sort-Object Bytes -Descending)

    Emit "| GB | Directory |"
    Emit "|---:|---|"
    foreach ($t in ($tops | Select-Object -First $Top)) { Emit ("| {0} | {1} |" -f $t.GB, $t.Name) }
    Emit ""

    # ------------------------------------ TRAP 2: reconcile measured vs real
    # If this gap is large the scan lied (reparse points / denied subtrees) and
    # every number above is a floor, not a total.
    $measured = 0L
    foreach ($t in $tops) { $measured += $t.Bytes }
    $rootFiles = 0L
    Get-ChildItem -LiteralPath $root -File -Force -ErrorAction SilentlyContinue |
        ForEach-Object { $rootFiles += $_.Length }
    $accounted = $measured + $rootFiles
    $gap = $d.Used - $accounted
    $gapPct = if ($d.Used -gt 0) { [math]::Round(100 * $gap / $d.Used, 1) } else { 0 }

    Emit ("**Reconciliation**: measured {0} GB of {1} GB used; UNACCOUNTED **{2} GB ({3} pct)**." -f `
          (To-GB $accounted), (To-GB $d.Used), (To-GB $gap), $gapPct)
    if ($gapPct -gt 10) {
        Emit ""
        Emit "> WARNING: unaccounted space exceeds 10 pct. The directory scan under-reported. Usual causes: permission-denied subtrees, reparse points / junctions, VSS shadow copies, or a reserve file listed above. Do not treat the table as complete - drill the suspect trees explicitly."
    }
    Emit ""

    foreach ($t in ($tops | Select-Object -First $DrillTop)) {
        if ($t.GB -lt $DrillMinGB) { continue }
        Emit ("### {0}{1} = {2} GB" -f $root, $t.Name, $t.GB)
        Emit ""
        $subs = @()
        foreach ($sub in (Get-ChildItem -LiteralPath $t.Path -Directory -Force -ErrorAction SilentlyContinue)) {
            if ($sub.Attributes.ToString().Contains('ReparsePoint')) { continue }
            $sb = Get-DirSizeBytes $sub.FullName
            $subs += [PSCustomObject]@{ Bytes = $sb; GB = (To-GB $sb); Name = $sub.Name }
        }
        if ($subs.Count -eq 0) { Emit "_(no subdirectories)_"; Emit ""; continue }
        Emit "| GB | Subdirectory |"
        Emit "|---:|---|"
        foreach ($s in ($subs | Sort-Object Bytes -Descending | Select-Object -First 10)) {
            Emit ("| {0} | {1} |" -f $s.GB, $s.Name)
        }
        Emit ""
    }
}

# -------------------------------------------------------- known caches ------
Emit "## Known regenerable caches (Tier A candidates)"
Emit ""
$caches = [ordered]@{
    "pip"            = "$env:LOCALAPPDATA\pip\Cache"
    "uv"             = "$env:LOCALAPPDATA\uv"
    "rattler/conda"  = "$env:LOCALAPPDATA\rattler"
    "npm"            = "$env:APPDATA\npm-cache"
    "user Temp"      = "$env:LOCALAPPDATA\Temp"
    "Windows Temp"   = "C:\Windows\Temp"
    "Windows Update" = "C:\Windows\SoftwareDistribution\Download"
    "playwright"     = "$env:LOCALAPPDATA\ms-playwright"
    "puppeteer"      = "$env:USERPROFILE\.cache\puppeteer"
    "huggingface"    = "$env:USERPROFILE\.cache\huggingface"
    "torch"          = "$env:USERPROFILE\.cache\torch"
    "ollama blobs"   = "$env:USERPROFILE\.ollama\models\blobs"
    "LM Studio"      = "$env:USERPROFILE\.cache\lm-studio"
}
Emit "| GB | Cache | Path |"
Emit "|---:|---|---|"
foreach ($k in $caches.Keys) {
    $p = $caches[$k]
    if (Test-Path -LiteralPath $p) {
        $b = Get-DirSizeBytes $p
        if ($b -gt 100MB) { Emit ("| {0} | {1} | ``{2}`` |" -f (To-GB $b), $k, $p) }
    }
}
Emit ""
Emit "> Model caches (huggingface / ollama / LM Studio / torch) are **Tier B, not Tier A** - regenerable only with bandwidth, and often actively wired up. Check what is in use before proposing removal."
Emit ""

# ------------------------------------------------------ never-touch note ----
Emit "## Never touch (considered and deliberately excluded)"
Emit ""
Emit "- ``C:\Windows\Installer`` - MSI/MSP cache; deleting breaks uninstall / repair / update of every installed program. Looks like junk, is not."
Emit "- ``WinSxS`` by hand - evict only via ``DISM /Online /Cleanup-Image /StartComponentCleanup``."
Emit "- ``System Volume Information`` by hand - manage shadow copies with ``vssadmin``."
Emit "- The vault, any ``.git`` directory, ``data/*.db*``, ``data/secrets/``, source trees."
Emit "- OneDrive placeholder files - deleting a dehydrated placeholder can delete the cloud copy."
Emit ""

$lines | Out-File -FilePath $OutFile -Encoding utf8
Write-Output ""
Write-Output "Report written to: $OutFile"

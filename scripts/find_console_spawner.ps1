<#
.SYNOPSIS
    Name whatever is spawning console windows — live now, or from Event 4688 history.

.DESCRIPTION
    The payoff for scripts/enable_process_auditing.ps1.

    The console-storm that hard-froze this machine on 2026-08-01 and 2026-08-15
    was never attributed, because its children exit in milliseconds and so never
    appear in a process snapshot. Two things can still catch them:

      LIVE      — while consoles are alive, Win32_Process carries ParentProcessId.
                  Run this the moment the watchdog shouts CONSOLE STORM.
      HISTORY   — Event 4688 records parentage at spawn time, so it works after
                  the fact. Needs enable_process_auditing.ps1 to have been run,
                  and an elevated shell to read the Security log.

    Prints both. Whichever has data is the answer.

.PARAMETER Minutes
    How far back to search 4688 history (default 30).

.PARAMETER Top
    How many distinct parents to list (default 15).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\find_console_spawner.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\find_console_spawner.ps1 -Minutes 180
#>
[CmdletBinding()]
param(
    [int]$Minutes = 30,
    [int]$Top = 15,
    [int]$MaxEvents = 200000
)

$CONSOLE_IMAGES = 'conhost.exe', 'OpenConsole.exe'

Write-Host '=== LIVE: console hosts alive right now ===' -ForegroundColor Cyan
$live = Get-CimInstance Win32_Process -Filter (
    ($CONSOLE_IMAGES | ForEach-Object { "Name='$_'" }) -join ' OR '
) -ErrorAction SilentlyContinue

if (-not $live) {
    Write-Host '  none' -ForegroundColor DarkGray
} else {
    Write-Host ("  {0} console hosts" -f $live.Count)
    # Resolve each distinct parent PID to a name once — during a storm there are
    # thousands of children and (usually) one parent, so group before resolving.
    $byParent = $live | Group-Object ParentProcessId | Sort-Object Count -Descending
    Write-Host ''
    Write-Host ('  {0,-8} {1,-28} {2,7}  {3}' -f 'PPID', 'PARENT', 'CHILDREN', 'PARENT COMMAND LINE')
    foreach ($g in $byParent | Select-Object -First $Top) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($g.Name)" -ErrorAction SilentlyContinue
        $nm = '(exited)'
        $cl = ''
        if ($p) {
            $nm = $p.Name
            if ($p.CommandLine) { $cl = $p.CommandLine }
            if ($cl.Length -gt 90) { $cl = $cl.Substring(0, 90) + '...' }
        }
        Write-Host ('  {0,-8} {1,-28} {2,7}  {3}' -f $g.Name, $nm, $g.Count, $cl)
    }
    if ($byParent.Count -gt $Top) {
        Write-Host ("  ... and {0} more distinct parents" -f ($byParent.Count - $Top)) -ForegroundColor DarkGray
    }

    # Self-interpreting, because this gets read during a crisis on a thrashing
    # box. Healthy is MANY parents with a FEW children each (measured: 32 hosts
    # across ~25 parents, 1-7 apiece). A storm is the inverse — one parent
    # owning nearly all of them.
    Write-Host ''
    $top1 = $byParent[0]
    $share = [math]::Round(100 * $top1.Count / $live.Count)
    if ($live.Count -ge 300 -and $share -ge 50) {
        $msg = "  VERDICT: STORM - PPID {0} owns {1} of {2} console hosts ({3}%). That is the culprit."
        Write-Host ($msg -f $top1.Name, $top1.Count, $live.Count, $share) -ForegroundColor Red
    } elseif ($live.Count -ge 300) {
        $msg = "  VERDICT: STORM ({0} hosts) but no single dominant parent (top = {1}%)."
        Write-Host ($msg -f $live.Count, $share) -ForegroundColor Red
        Write-Host '           Children exit faster than they can be sampled - use the 4688 history below.' -ForegroundColor Red
    } else {
        $msg = "  VERDICT: healthy - {0} hosts across {1} parents, top owns {2}%."
        Write-Host ($msg -f $live.Count, $byParent.Count, $share) -ForegroundColor Green
    }
}

Write-Host ''
Write-Host "=== HISTORY: Event 4688 console-host creations, last $Minutes min ===" -ForegroundColor Cyan

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# Write-Host, not Write-Warning: the warning stream can be silenced by a profile
# or a caller's $WarningPreference, and a control-flow-critical "this is why you
# got no output" message must not be suppressible. State the branch either way,
# so a run that produces nothing still says which path it took.
Write-Host ("  elevated: {0}" -f $isAdmin) -ForegroundColor DarkGray
if (-not $isAdmin) {
    Write-Host '  Reading the Security log needs an ELEVATED shell. Re-run as admin for this half.' -ForegroundColor Yellow
    return
}

# Filter in the LOG ENGINE, not in PowerShell. Measured on this box: fetching
# 3000 events unfiltered costs 892 ms, the same query as server-side XPath costs
# 35 ms, and adding an EventData field predicate costs 18 ms — 25-50x. With 4688
# enabled a busy box makes tens of thousands of creations an hour, so the
# unfiltered version spends a minute returning data we throw 99% of away, which
# is indistinguishable from a hang.
#
# Console-host image paths are resolved from live processes rather than
# hardcoded, because OpenConsole lives under a version-stamped WindowsApps path
# (and WezTerm ships its own copy). System32\conhost.exe is added unconditionally
# as the one stable, always-relevant path.
$paths = @("$env:SystemRoot\System32\conhost.exe")
$paths += Get-CimInstance Win32_Process -Filter (
    ($CONSOLE_IMAGES | ForEach-Object { "Name='$_'" }) -join ' OR '
) -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ExecutablePath
$paths = $paths | Where-Object { $_ } | Sort-Object -Unique

$pred = ($paths | ForEach-Object { "Data[@Name='NewProcessName']='$_'" }) -join ' or '
$ms = [int64]$Minutes * 60 * 1000
$xpath = "*[System[EventID=4688 and TimeCreated[timediff(@SystemTime) <= $ms]]] and *[EventData[$pred]]"

Write-Host ("  querying {0} console-host image path(s), last {1} min..." -f $paths.Count, $Minutes) -ForegroundColor DarkGray

$events = $null
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
    $events = Get-WinEvent -LogName Security -FilterXPath $xpath -MaxEvents $MaxEvents -ErrorAction Stop
} catch {
    # Get-WinEvent throws on an empty result set, which is the common healthy
    # case — distinguish it from a real failure rather than blaming auditing.
    if ($_.Exception.Message -match 'No events were found') {
        Write-Host ("  no console hosts created in the last {0} min - auditing is on, there was no storm." -f $Minutes) -ForegroundColor Green
        Write-Host '  (4688 is NOT retroactive: it only covers processes created since it was enabled.)' -ForegroundColor DarkGray
    } else {
        Write-Host ("  query failed: {0}" -f $_.Exception.Message) -ForegroundColor Red
    }
    return
}

Write-Host ("  fetched {0:N0} console-host creations in {1:N1}s" -f @($events).Count, $sw.Elapsed.TotalSeconds) -ForegroundColor DarkGray

# Locate the EventData fields by name ONCE, from a single event, then read every
# other event positionally via .Properties.
#
# Field indices are discovered rather than hardcoded because the 4688 layout has
# shifted across Windows versions. Validated against the XML on 376 field
# comparisons, zero mismatches. It is a small win on its own (under 2x) — the
# real speed comes from the server-side XPath above, so the events reaching this
# loop are already only console hosts. The leaf check below is kept as a cheap
# belt-and-braces in case a future path list is looser than intended.
$idx = @{}
$probe = ([xml]$events[0].ToXml()).Event.EventData.Data
for ($i = 0; $i -lt $probe.Count; $i++) {
    # Guard the unnamed case: some providers emit <Data> with no Name attribute,
    # and $idx[$null] throws NullArrayIndex — which would abort the whole
    # history half over a cosmetic field. 4688 names all of its fields, but the
    # probe must not be the thing that breaks.
    $n = $probe[$i].Name
    if ($n) { $idx[$n] = $i }
}

$iNew = $idx['NewProcessName']
$iParent = $idx['ParentProcessName']
$iCmd = $idx['CommandLine']
if ($null -eq $iNew) {
    Write-Warning 'Could not locate NewProcessName in this event schema - falling back to slow XML scan.'
}

$rows = foreach ($e in $events) {
    if ($null -ne $iNew) {
        $newProc = $e.Properties[$iNew].Value
        if (-not $newProc) { continue }
        if ($CONSOLE_IMAGES -notcontains (Split-Path $newProc -Leaf)) { continue }
        $parent = '(unknown)'
        if ($null -ne $iParent) { $parent = $e.Properties[$iParent].Value }
        $cmd = ''
        if ($null -ne $iCmd) { $cmd = $e.Properties[$iCmd].Value }
        [pscustomobject]@{ Parent = $parent; CommandLine = $cmd }
    } else {
        $d = @{}
        foreach ($item in ([xml]$e.ToXml()).Event.EventData.Data) { $d[$item.Name] = $item.'#text' }
        if (-not $d['NewProcessName']) { continue }
        if ($CONSOLE_IMAGES -notcontains (Split-Path $d['NewProcessName'] -Leaf)) { continue }
        [pscustomobject]@{ Parent = $d['ParentProcessName']; CommandLine = $d['CommandLine'] }
    }
}

if (-not $rows) {
    Write-Host '  no console hosts created in that window' -ForegroundColor DarkGray
    Write-Host '  (auditing is on and working — there was simply no storm)' -ForegroundColor DarkGray
    return
}

Write-Host ("  {0} console hosts created" -f @($rows).Count)
Write-Host ''
$rows | Group-Object Parent | Sort-Object Count -Descending |
    Select-Object -First $Top @{n = 'Spawned'; e = { $_.Count } }, @{n = 'Parent'; e = { $_.Name } } |
    Format-Table -AutoSize

Write-Host 'If one parent dominates that list, it is the culprit.' -ForegroundColor Green

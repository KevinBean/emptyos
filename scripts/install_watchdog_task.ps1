<#
.SYNOPSIS
    Register (or remove) the EmptyOS daemon watchdog as a durable Windows
    Scheduled Task, so self-healing survives sleep / logoff / window-close.

.DESCRIPTION
    restart.bat launches daemon_watchdog.py --restart in a minimized cmd
    window. That window-bound watchdog dies when the console is closed, the
    machine sleeps and the session is torn down, or the user logs off - and
    nothing brings it back. When the (separately-detached) daemon later wedges,
    there is no supervisor alive to recover it. That gap caused a ~19h silent
    outage on 2026-06-26.

    This task closes the gap:
      * AtLogOn trigger             - starts the watchdog after every logon/wake-relogon.
      * 5-minute repetition         - if the watchdog ever dies, the next tick relaunches it.
      * Restart-on-failure          - Task Scheduler retries if the process exits non-zero.
      * MultipleInstances=IgnoreNew - never double-launches; the recovery lock is the backstop.

    It runs the watchdog as the current interactive user (only when logged on),
    matching the daemon's own session requirement (system-tray, global-hotkey,
    GPU plugins all need the interactive session). It does NOT start, kill, or
    touch the daemon itself - it only keeps the supervisor alive. The watchdog's
    own recovery lock means this task and restart.bat's per-session watchdog
    coexist safely (only one performs recovery; the other is evidence-only).

.PARAMETER Remove
    Unregister the task instead of installing it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\install_watchdog_task.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install_watchdog_task.ps1 -Remove
#>
[CmdletBinding()]
param(
    [switch]$Remove,
    # Internal: set when the script self-relaunches elevated, so the new admin
    # window pauses on exit instead of vanishing before you can read the result.
    [switch]$Elevated
)

$ErrorActionPreference = 'Stop'
$TaskName = 'EmptyOS Watchdog'

# Register-ScheduledTask in the root folder needs admin. Self-elevate (one UAC
# prompt) rather than failing with Access-denied. Elevation does not change the
# user identity, so the task still registers and runs as the current user.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    $q = [char]34
    $relaunchArgs = @('-ExecutionPolicy', 'Bypass', '-File', ($q + $PSCommandPath + $q), '-Elevated')
    if ($Remove) { $relaunchArgs += '-Remove' }
    Write-Host 'Not elevated - relaunching as administrator (accept the UAC prompt)...' -ForegroundColor Yellow
    Start-Process -FilePath 'powershell' -Verb RunAs -ArgumentList $relaunchArgs
    return
}

# Repo root = parent of this script's directory.
$RepoRoot = Split-Path -Parent $PSScriptRoot

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'." -ForegroundColor Green
    } else {
        Write-Host "No scheduled task '$TaskName' to remove." -ForegroundColor Yellow
    }
    if ($Elevated) { Read-Host 'Press Enter to close' }
    return
}

# Resolve the same python the user runs the daemon with (full path so the task
# context's PATH cannot pick a different interpreter).
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    throw "Could not find 'python' on PATH. Activate the daemon's environment, then re-run."
}
Write-Host "python      : $python"
Write-Host "working dir : $RepoRoot"

$action = New-ScheduledTaskAction -Execute $python -Argument 'scripts\daemon_watchdog.py --restart' -WorkingDirectory $RepoRoot

# Start at logon, then re-check every 5 minutes indefinitely. The 5-min
# repetition is the load-bearing durability: after a sleep/wake (no re-logon)
# or a window-close, the next tick relaunches the watchdog if it is not running.
# NOTE: [TimeSpan]::MaxValue serializes to P99999999DT23H59M59S, which Task
# Scheduler rejects as out-of-range. A large finite duration (10 years) is
# effectively indefinite for a watchdog and is valid XML.
$trigger = New-ScheduledTaskTrigger -AtLogOn
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition

$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable

# Run as the current interactive user (matches the daemon's session needs), at
# HIGHEST run level. The run level is load-bearing: :9000 runs elevated on this
# machine (restart.bat self-elevates when a non-elevated kill can't reach it), so
# a Limited watchdog would get Access-Denied on taskkill, fail to free the port,
# and loop on WinError 10048 until the storm guard fires. Highest can kill an
# elevated OR a non-elevated daemon, so it works in both restart.bat paths.
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'Keeps the EmptyOS daemon watchdog (daemon_watchdog.py --restart) alive across sleep/logoff/window-close so :9000 self-healing never silently stops.' -Force | Out-Null

Write-Host "Registered scheduled task '$TaskName'." -ForegroundColor Green

# Start it immediately so the watchdog is live without waiting for the next
# logon. The recovery lock keeps it from clashing with restart.bat's watchdog.
Start-ScheduledTask -TaskName $TaskName
Write-Host "Started '$TaskName' now." -ForegroundColor Green
if ($Elevated) { Read-Host 'Press Enter to close' }

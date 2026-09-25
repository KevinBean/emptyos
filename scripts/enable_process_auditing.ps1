<#
.SYNOPSIS
    Turn on Windows process-creation auditing (Event 4688) with command lines.

.DESCRIPTION
    Run this ELEVATED. It is the missing instrument for the console-storm
    failure that killed this machine twice (2026-08-01, 2026-08-15).

    Both times, ~1800-4300 conhost/OpenConsole processes appeared in minutes,
    each one a terminal window, together holding 21-45 GB of RAM until the box
    ran out of commit and froze. Both times the *clients* exited too fast to
    appear in any process snapshot, so the spawner was never identified — three
    separate hypotheses (a console-less daemon spawning git, the meeting-capture
    soundcard probe, obsidian-git) were each tested and disproven after the fact.

    The reason it stayed unsolved is simple: nothing recorded parentage. Event
    4688 records it at spawn time, so the next occurrence names its own culprit
    in one query instead of a night of forensics.

.PARAMETER LogSizeMB
    Security log cap. 4688 is high-volume; the 20 MB default rolls over in
    hours on a busy box, which would lose the evidence this exists to keep.

.NOTES
    PRIVACY: command lines are recorded, and command lines sometimes carry
    secrets (an API key passed as an argument). The Security log is
    admin-readable only, but this is a real tradeoff — see CLAUDE.md rule 13.
    Pass -NoCommandLine to enable 4688 without argument capture; you still get
    parent PID + image name, which is enough to name a spawner.

.EXAMPLE
    # after the next storm, name the spawner:
    Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4688} -MaxEvents 2000 |
      Where-Object { $_.Message -match 'conhost|OpenConsole' } |
      ForEach-Object { ([xml]$_.ToXml()).Event.EventData.Data } |
      Where-Object { $_.Name -eq 'ParentProcessName' } |
      Group-Object '#text' | Sort-Object Count -Descending
#>
[CmdletBinding()]
param(
    [int]$LogSizeMB = 512,
    [switch]$NoCommandLine,
    [switch]$Disable
)

$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error @"
This script must run ELEVATED.

Open an admin PowerShell (Win+X -> 'Terminal (Admin)') and run:
    powershell -ExecutionPolicy Bypass -File "$PSCommandPath"
"@
    exit 1
}

$auditKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\Audit'

if ($Disable) {
    Write-Host 'Disabling process-creation auditing...' -ForegroundColor Yellow
    auditpol /set /subcategory:"Process Creation" /success:disable /failure:disable | Out-Null
    if (Test-Path $auditKey) {
        Set-ItemProperty -Path $auditKey -Name 'ProcessCreationIncludeCmdLine_Enabled' -Value 0 -Type DWord
    }
    Write-Host 'Disabled. (Security log size left as-is.)' -ForegroundColor Green
    exit 0
}

# 1 — the audit subcategory itself.
Write-Host 'Enabling Event 4688 (process creation, success)...' -ForegroundColor Cyan
auditpol /set /subcategory:"Process Creation" /success:enable | Out-Null

# 2 — command lines. Optional: parent PID alone already names a spawner.
if (-not $NoCommandLine) {
    Write-Host 'Enabling command-line capture in 4688...' -ForegroundColor Cyan
    if (-not (Test-Path $auditKey)) { New-Item -Path $auditKey -Force | Out-Null }
    Set-ItemProperty -Path $auditKey -Name 'ProcessCreationIncludeCmdLine_Enabled' -Value 1 -Type DWord
} else {
    Write-Host 'Skipping command-line capture (-NoCommandLine).' -ForegroundColor DarkGray
}

# 3 — retention. Without this the evidence ages out before anyone looks: the
# 20 MB default is ~20k events with command lines, and a storm alone can spend
# that in minutes.
#
# NOTE the assignment. `wevtutil sl Security /ms:($LogSizeMB * 1MB)` looks
# right and is wrong: PowerShell treats the parenthesised expression as its own
# argv token, so wevtutil receives "/ms:" and "536870912" separately and fails
# with "Too many arguments are specified." Build the value first so "/ms:$bytes"
# expands into a single token.
$bytes = [int64]$LogSizeMB * 1MB
Write-Host "Raising Security log cap to $LogSizeMB MB ($bytes bytes)..." -ForegroundColor Cyan
wevtutil sl Security /ms:$bytes

$applied = (wevtutil gl Security | Select-String 'maxSize:\s*(\d+)').Matches.Groups[1].Value
if ([int64]$applied -ge $bytes) {
    Write-Host "  maxSize now $applied bytes" -ForegroundColor DarkGray
} else {
    Write-Warning "Security log cap is still $applied bytes - the raise did not take."
}

Write-Host ''
Write-Host 'Done. Verify:' -ForegroundColor Green
auditpol /get /subcategory:"Process Creation"
Write-Host ''
Write-Host 'To turn it back off:  .\scripts\enable_process_auditing.ps1 -Disable' -ForegroundColor DarkGray
Write-Host 'Storm triage query is in this file''s .EXAMPLE block (Get-Help -Full).' -ForegroundColor DarkGray

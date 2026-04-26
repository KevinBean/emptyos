# Unstick Windows camera (FrameServer hang recovery).
# Requires admin. Writes result to .camera-fix.log next to repo root.

$ErrorActionPreference = 'Continue'
$log = Join-Path $PSScriptRoot '..\.camera-fix.log'
$log = [IO.Path]::GetFullPath($log)

"=== $(Get-Date -Format s) ===" | Out-File -FilePath $log -Encoding utf8

try {
    "before:" | Out-File -FilePath $log -Encoding utf8 -Append
    Get-Service FrameServer, FrameServerMonitor -ErrorAction SilentlyContinue |
        Format-Table Name, Status, StartType -AutoSize | Out-String |
        Out-File -FilePath $log -Encoding utf8 -Append

    Restart-Service FrameServer -Force -ErrorAction Stop
    "Restart-Service FrameServer: OK" | Out-File -FilePath $log -Encoding utf8 -Append

    Start-Service FrameServerMonitor -ErrorAction Stop
    "Start-Service FrameServerMonitor: OK" | Out-File -FilePath $log -Encoding utf8 -Append

    Start-Sleep -Seconds 1

    "after:" | Out-File -FilePath $log -Encoding utf8 -Append
    Get-Service FrameServer, FrameServerMonitor -ErrorAction SilentlyContinue |
        Format-Table Name, Status, StartType -AutoSize | Out-String |
        Out-File -FilePath $log -Encoding utf8 -Append

    "RESULT: success" | Out-File -FilePath $log -Encoding utf8 -Append
} catch {
    "ERROR: $($_.Exception.Message)" | Out-File -FilePath $log -Encoding utf8 -Append
    "RESULT: failed" | Out-File -FilePath $log -Encoding utf8 -Append
    exit 1
}

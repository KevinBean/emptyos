# Escalation: disable + re-enable the USB Camera device.
# Equivalent to Device Manager > Cameras > Disable, then Enable.
# Requires admin.

$ErrorActionPreference = 'Continue'
$log = Join-Path $PSScriptRoot '..\.camera-fix.log'
$log = [IO.Path]::GetFullPath($log)

"=== device reset $(Get-Date -Format s) ===" | Out-File -FilePath $log -Encoding utf8 -Append

try {
    $cam = Get-PnpDevice -Class Camera -ErrorAction Stop |
        Where-Object { $_.FriendlyName -match '(?i)camera|webcam' -and $_.FriendlyName -notmatch 'DeskJet|Printer' } |
        Select-Object -First 1
    if (-not $cam) {
        "ERROR: no USB camera device found" | Out-File -FilePath $log -Encoding utf8 -Append
        exit 1
    }
    "found: $($cam.FriendlyName) [$($cam.InstanceId)]" | Out-File -FilePath $log -Encoding utf8 -Append

    Disable-PnpDevice -InstanceId $cam.InstanceId -Confirm:$false -ErrorAction Stop
    "disabled" | Out-File -FilePath $log -Encoding utf8 -Append
    Start-Sleep -Seconds 2

    Enable-PnpDevice -InstanceId $cam.InstanceId -Confirm:$false -ErrorAction Stop
    "enabled" | Out-File -FilePath $log -Encoding utf8 -Append
    Start-Sleep -Seconds 2

    $after = Get-PnpDevice -InstanceId $cam.InstanceId
    "final status: $($after.Status)" | Out-File -FilePath $log -Encoding utf8 -Append
    "RESULT: device reset complete" | Out-File -FilePath $log -Encoding utf8 -Append
} catch {
    "ERROR: $($_.Exception.Message)" | Out-File -FilePath $log -Encoding utf8 -Append
    "RESULT: device reset failed" | Out-File -FilePath $log -Encoding utf8 -Append
    exit 1
}

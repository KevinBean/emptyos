[CmdletBinding()]
param(
    [ValidateRange(0.1, 3600)]
    [double]$IntervalSeconds = 3,

    [switch]$SelfTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RightArrowKey = "{RIGHT}"

function ConvertTo-IntervalMilliseconds {
    param(
        [Parameter(Mandatory)]
        [double]$Seconds
    )

    return [Math]::Max(1, [int][Math]::Round($Seconds * 1000))
}

if ($SelfTest) {
    if ((ConvertTo-IntervalMilliseconds -Seconds 3) -ne 3000) {
        throw "Three seconds must convert to 3000 milliseconds."
    }
    if ((ConvertTo-IntervalMilliseconds -Seconds 0.1) -ne 100) {
        throw "Fractional-second intervals must be preserved."
    }
    if ($RightArrowKey -ne "{RIGHT}") {
        throw "The configured key must be Right Arrow."
    }

    Write-Output "Auto Right Arrow self-test passed."
    exit 0
}

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

[System.Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object System.Windows.Forms.Form
$form.Text = "Auto Right Arrow"
$form.ClientSize = New-Object System.Drawing.Size(420, 230)
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedDialog
$form.MaximizeBox = $false
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen

$titleLabel = New-Object System.Windows.Forms.Label
$titleLabel.Text = "Press Right Arrow automatically"
$titleLabel.Font = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
$titleLabel.AutoSize = $true
$titleLabel.Location = New-Object System.Drawing.Point(22, 20)
$form.Controls.Add($titleLabel)

$helpLabel = New-Object System.Windows.Forms.Label
$helpLabel.Text = "Start the tool, then focus the window that should receive the key."
$helpLabel.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$helpLabel.AutoSize = $true
$helpLabel.Location = New-Object System.Drawing.Point(24, 58)
$form.Controls.Add($helpLabel)

$intervalLabel = New-Object System.Windows.Forms.Label
$intervalLabel.Text = "Every"
$intervalLabel.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$intervalLabel.AutoSize = $true
$intervalLabel.Location = New-Object System.Drawing.Point(24, 99)
$form.Controls.Add($intervalLabel)

$intervalInput = New-Object System.Windows.Forms.NumericUpDown
$intervalInput.DecimalPlaces = 1
$intervalInput.Minimum = [decimal]0.1
$intervalInput.Maximum = [decimal]3600
$intervalInput.Increment = [decimal]0.5
$intervalInput.Value = [decimal]$IntervalSeconds
$intervalInput.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$intervalInput.Size = New-Object System.Drawing.Size(86, 26)
$intervalInput.Location = New-Object System.Drawing.Point(80, 95)
$form.Controls.Add($intervalInput)

$secondsLabel = New-Object System.Windows.Forms.Label
$secondsLabel.Text = "seconds"
$secondsLabel.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$secondsLabel.AutoSize = $true
$secondsLabel.Location = New-Object System.Drawing.Point(175, 99)
$form.Controls.Add($secondsLabel)

$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Text = "Stopped"
$statusLabel.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$statusLabel.AutoSize = $true
$statusLabel.Location = New-Object System.Drawing.Point(24, 139)
$form.Controls.Add($statusLabel)

$startButton = New-Object System.Windows.Forms.Button
$startButton.Text = "Start"
$startButton.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$startButton.Size = New-Object System.Drawing.Size(112, 36)
$startButton.Location = New-Object System.Drawing.Point(174, 178)
$form.Controls.Add($startButton)

$stopButton = New-Object System.Windows.Forms.Button
$stopButton.Text = "Stop"
$stopButton.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$stopButton.Size = New-Object System.Drawing.Size(112, 36)
$stopButton.Location = New-Object System.Drawing.Point(294, 178)
$stopButton.Enabled = $false
$form.Controls.Add($stopButton)

$timer = New-Object System.Windows.Forms.Timer
$timer.Add_Tick({
    try {
        [System.Windows.Forms.SendKeys]::SendWait($RightArrowKey)
    }
    catch {
        $timer.Stop()
        $startButton.Enabled = $true
        $stopButton.Enabled = $false
        $intervalInput.Enabled = $true
        $statusLabel.Text = "Stopped: $($_.Exception.Message)"
    }
})

$startButton.Add_Click({
    $timer.Interval = ConvertTo-IntervalMilliseconds -Seconds ([double]$intervalInput.Value)
    $timer.Start()
    $startButton.Enabled = $false
    $stopButton.Enabled = $true
    $intervalInput.Enabled = $false
    $statusLabel.Text = "Running - switch to the target window now."
})

$stopButton.Add_Click({
    $timer.Stop()
    $startButton.Enabled = $true
    $stopButton.Enabled = $false
    $intervalInput.Enabled = $true
    $statusLabel.Text = "Stopped"
})

$form.Add_FormClosing({
    $timer.Stop()
    $timer.Dispose()
})

[void][System.Windows.Forms.Application]::Run($form)

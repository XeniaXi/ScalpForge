param(
    [Parameter(Mandatory = $true)] [string] $ProjectRoot,
    [Parameter(Mandatory = $true)] [string] $Protocol,
    [Parameter(Mandatory = $true)] [string] $SourceDir,
    [int] $IntervalMinutes = 5
)

$ErrorActionPreference = "Stop"
if ($IntervalMinutes -lt 5) { throw "IntervalMinutes must be at least 5." }

$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$protocolPath = (Resolve-Path -LiteralPath $Protocol).Path
$source = (Resolve-Path -LiteralPath $SourceDir).Path
$python = Join-Path $project ".venv\Scripts\python.exe"
$initializer = Join-Path $project ".venv\Scripts\scalpforge-init-demo-shadow.exe"

foreach ($required in @($python, $initializer)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required file is missing: $required"
    }
}

$verification = & $initializer --verify $protocolPath
if ($LASTEXITCODE -ne 0) { throw "Protocol verification command failed." }
$verified = $verification | ConvertFrom-Json
if ($verified.ready -ne $true) { throw "Protocol is not ready or its evidence hashes changed." }

$arguments = @(
    "-m", "scalpforge_strategy.demo_shadow_worker_cli",
    "--protocol", ('"' + $protocolPath + '"'),
    "--source-dir", ('"' + $source + '"'),
    "--interval-seconds", ($IntervalMinutes * 60)
) -join " "

$action = New-ScheduledTaskAction `
    -Execute $python `
    -Argument $arguments `
    -WorkingDirectory $project
$now = Get-Date
$alignedMinute = $now.Minute - ($now.Minute % $IntervalMinutes)
$firstRun = Get-Date -Hour $now.Hour -Minute $alignedMinute -Second 5
if ($firstRun -le $now) { $firstRun = $firstRun.AddMinutes($IntervalMinutes) }
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $firstRun `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName "ScalpForge-Demo-Shadow" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Read-only Candidate A AvaTrade demo shadow; hypothetical fills only; never trades" `
    -Force | Out-Null

Write-Host "Installed read-only demo-shadow task."
Write-Host "Task: ScalpForge-Demo-Shadow"
Write-Host "Interval: $IntervalMinutes minutes"
Write-Host "Protocol: $protocolPath"
Write-Host "Source: $source"
Write-Host "Persistent worker; Task Scheduler should remain Running. No order submission is enabled."

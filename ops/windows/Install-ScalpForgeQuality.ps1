param(
    [Parameter(Mandatory = $true)] [string] $ProjectRoot,
    [int] $IntervalMinutes = 60
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$runner = Join-Path $project "ops\windows\Run-ScalpForgeQuality.ps1"
$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" -ProjectRoot `"$project`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "ScalpForge-Quality-Reports" -Action $action -Trigger $trigger -Settings $settings -Description "Read-only tick and news quality reports" -Force | Out-Null
Write-Host "Installed hourly read-only quality reporting task."

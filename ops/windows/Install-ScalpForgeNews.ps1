param(
    [Parameter(Mandatory = $true)] [string] $ProjectRoot,
    [int] $IntervalMinutes = 15
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $project ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "ScalpForge virtual environment is missing." }

$arguments = @("-m", "scalpforge_news.cli", "gdelt", "--max-records", "100")
$action = New-ScheduledTaskAction -Execute $python -Argument (($arguments | ForEach-Object { '"' + $_ + '"' }) -join ' ') -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "ScalpForge-News-Collector" -Action $action -Trigger $trigger -Settings $settings -Description "Research-only GDELT evidence collector; never trades" -Force | Out-Null
Write-Host "Installed research-only GDELT collector task."
Write-Host "Interval: $IntervalMinutes minutes"
Write-Host "Raw and normalized news data remain under C:\ScalpForge\data and outside Git."

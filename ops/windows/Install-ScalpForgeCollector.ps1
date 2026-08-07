param(
    [Parameter(Mandatory = $true)] [string] $ProjectRoot,
    [Parameter(Mandatory = $true)] [string] $Mt4DataRoot,
    [string] $OutputPrefix = "scalpforge",
    [string] $Symbol = "GOLD"
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$mt4 = (Resolve-Path -LiteralPath $Mt4DataRoot).Path
$expertSource = Join-Path $project "mt4\Experts\ScalpForgeRecorder.mq4"
$expertTarget = Join-Path $mt4 "MQL4\Experts\ScalpForgeRecorder.mq4"
$commonFiles = Join-Path (Split-Path $mt4 -Parent) "Common\Files"
$archive = Join-Path $project "data\raw\avatrade\GOLD"
$python = Join-Path $project ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) { throw "Virtual environment missing. Run: py -3.12 -m venv .venv" }
Copy-Item -LiteralPath $expertSource -Destination $expertTarget -Force
New-Item -ItemType Directory -Force -Path $archive | Out-Null
$arguments = @("-m", "scalpforge_collector.cli", "--source-dir", $commonFiles, "--prefix", $OutputPrefix, "--symbol", $Symbol, "--archive", $archive, "--watch-seconds", "60", "--stale-seconds", "180")
$action = New-ScheduledTaskAction -Execute $python -Argument (($arguments | ForEach-Object { '"' + $_ + '"' }) -join ' ') -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName "ScalpForge-Demo-Collector" -Action $action -Trigger $trigger -Settings $settings -Description "Read-only AvaTrade MT4 demo tick collector" -Force | Out-Null
Write-Host "Installed read-only collector task."
Write-Host "EA copied to: $expertTarget"
Write-Host "Daily source directory: $commonFiles"
Write-Host "Archive: $archive"
Write-Host "No credentials were copied and no trading permission was enabled."

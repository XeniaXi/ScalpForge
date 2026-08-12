param(
    [string]$ProjectRoot = "C:\ScalpForge",
    [Parameter(Mandatory = $true)]
    [string]$JForexStrategiesRoot
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$destination = [IO.Path]::GetFullPath($JForexStrategiesRoot)
$source = Join-Path $project "jforex\Strategies\ScalpForgeHistoricalExporter.java"
$hoursSource = Join-Path $project "jforex\Strategies\ScalpForgeMarketHoursExporter.java"

if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Exporter source not found: $source"
}
if (-not (Test-Path -LiteralPath $hoursSource -PathType Leaf)) {
    throw "Market-hours exporter source not found: $hoursSource"
}

New-Item -ItemType Directory -Path $destination -Force | Out-Null
$target = Join-Path $destination "ScalpForgeHistoricalExporter.java"
Copy-Item -LiteralPath $source -Destination $target -Force
$hoursTarget = Join-Path $destination "ScalpForgeMarketHoursExporter.java"
Copy-Item -LiteralPath $hoursSource -Destination $hoursTarget -Force

Write-Host "Installed read-only JForex historical exporter."
Write-Host "Strategy: $target"
Write-Host "Market hours: $hoursTarget"
Write-Host "The strategy contains no IEngine or order operations."
Write-Host "Compile and run it locally from the JForex Strategies panel."

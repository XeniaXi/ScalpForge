param(
    [Parameter(Mandatory = $true)] [string] $ProjectRoot,
    [Parameter(Mandatory = $true)] [string] $Mt4DataRoot,
    [string] $OutputPrefix = "scalpforge",
    [string] $Symbol = "GOLD"
)

$date = (Get-Date).ToUniversalTime().ToString("yyyyMMdd")
$commonFiles = Join-Path (Split-Path $Mt4DataRoot -Parent) "Common\Files"
$source = Join-Path $commonFiles "${OutputPrefix}_${Symbol}_${date}_ticks.csv"
$health = Join-Path $ProjectRoot "data\raw\avatrade\GOLD\health.latest.json"
[pscustomobject]@{
    Mt4Running = [bool](Get-Process terminal -ErrorAction SilentlyContinue)
    DailyTickFile = Test-Path -LiteralPath $source
    CollectorTask = [bool](Get-ScheduledTask -TaskName "ScalpForge-Demo-Collector" -ErrorAction SilentlyContinue)
    HealthFile = Test-Path -LiteralPath $health
    AutoTradingRequired = $false
    Source = $source
    Health = $health
} | Format-List

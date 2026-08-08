param([Parameter(Mandatory = $true)] [string] $ProjectRoot)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$python = Join-Path $project ".venv\Scripts\python.exe"
& $python -m scalpforge_quality.cli ticks
if ($LASTEXITCODE -ne 0) { throw "Tick quality report failed with exit code $LASTEXITCODE" }
& $python -m scalpforge_quality.cli news
if ($LASTEXITCODE -ne 0) { throw "News quality report failed with exit code $LASTEXITCODE" }

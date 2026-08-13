param(
    [Parameter(Mandatory = $true)] [string] $ProjectRoot,
    [Parameter(Mandatory = $true)] [string] $Protocol,
    [Parameter(Mandatory = $true)] [string] $SourceDir
)

$ErrorActionPreference = "Stop"
$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$protocolPath = (Resolve-Path -LiteralPath $Protocol).Path
$source = (Resolve-Path -LiteralPath $SourceDir).Path
$engine = Join-Path $project ".venv\Scripts\scalpforge-run-demo-shadow.exe"
$logRoot = Join-Path (Split-Path $protocolPath -Parent) "logs"
$log = Join-Path $logRoot ((Get-Date).ToUniversalTime().ToString("yyyyMMdd") + ".jsonl")

if (-not (Test-Path -LiteralPath $engine -PathType Leaf)) {
    throw "Demo-shadow executable is missing. Run: .\.venv\Scripts\python.exe -m pip install -e ."
}

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$output = & $engine --protocol $protocolPath --source-dir $source 2>&1
$exitCode = $LASTEXITCODE

$record = [ordered]@{
    invoked_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    exit_code = $exitCode
    output = @($output | ForEach-Object { $_.ToString() })
    order_submission_enabled = $false
}
($record | ConvertTo-Json -Compress -Depth 4) | Out-File -LiteralPath $log -Append -Encoding utf8

if ($exitCode -ne 0) {
    throw "Demo-shadow engine failed with exit code $exitCode. See $log"
}

$output

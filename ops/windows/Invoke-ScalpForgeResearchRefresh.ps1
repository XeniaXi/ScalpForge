param(
    [Parameter(Mandatory = $true)]
    [string]$SourceManifest,

    [string]$ProjectRoot = "C:\ScalpForge"
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$venvScripts = Join-Path $ProjectRoot ".venv\Scripts"
$featureRoot = Join-Path $ProjectRoot "data\curated\features"
$outcomeRoot = Join-Path $ProjectRoot "data\curated\outcomes"
$structureRoot = Join-Path $ProjectRoot "data\curated\structure"
$experimentRoot = Join-Path $ProjectRoot "outputs\experiments"

function Invoke-JsonCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    Write-Host "Running $([IO.Path]::GetFileName($Executable))..."
    $output = & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed with exit code $LASTEXITCODE"
    }
    $json = ($output | Select-Object -Last 1) | ConvertFrom-Json
    $json | ConvertTo-Json -Depth 20
    return $json
}

if (-not (Test-Path -LiteralPath $SourceManifest -PathType Leaf)) {
    throw "Source manifest does not exist: $SourceManifest"
}

$features = Invoke-JsonCommand `
    -Executable (Join-Path $venvScripts "scalpforge-build-features.exe") `
    -Arguments @("--source-manifest", $SourceManifest, "--output-root", $featureRoot)
$featureManifest = Join-Path $featureRoot "$($features.dataset_id)\manifest.json"

$outcomes = Invoke-JsonCommand `
    -Executable (Join-Path $venvScripts "scalpforge-build-outcomes.exe") `
    -Arguments @("--feature-manifest", $featureManifest, "--output-root", $outcomeRoot)
$outcomeManifest = Join-Path $outcomeRoot "$($outcomes.dataset_id)\manifest.json"

$structure = Invoke-JsonCommand `
    -Executable (Join-Path $venvScripts "scalpforge-build-structure.exe") `
    -Arguments @("--feature-manifest", $featureManifest, "--output-root", $structureRoot)
$structureManifest = Join-Path $structureRoot "$($structure.dataset_id)\manifest.json"

$structuralLab = Invoke-JsonCommand `
    -Executable (Join-Path $venvScripts "scalpforge-run-structural-lab.exe") `
    -Arguments @(
        "--feature-manifest", $featureManifest,
        "--structural-manifest", $structureManifest,
        "--outcome-manifest", $outcomeManifest,
        "--output-root", (Join-Path $experimentRoot "structural-lab")
    )

$sequenceLab = Invoke-JsonCommand `
    -Executable (Join-Path $venvScripts "scalpforge-run-sequence-lab.exe") `
    -Arguments @(
        "--feature-manifest", $featureManifest,
        "--structural-manifest", $structureManifest,
        "--output-root", (Join-Path $experimentRoot "sequence-lab")
    )

$feasibility = Invoke-JsonCommand `
    -Executable (Join-Path $venvScripts "scalpforge-run-feasibility-map.exe") `
    -Arguments @(
        "--feature-manifest", $featureManifest,
        "--structural-manifest", $structureManifest,
        "--output-root", (Join-Path $experimentRoot "feasibility")
    )

[PSCustomObject]@{
    Status = "complete"
    FeatureManifest = $featureManifest
    OutcomeManifest = $outcomeManifest
    StructureManifest = $structureManifest
    StructuralLabReport = $structuralLab.report_id
    SequenceLabReport = $sequenceLab.report_id
    FeasibilityReport = $feasibility.report_id
    HoldoutEvaluated = $false
    RealMoneyEnabled = $false
} | ConvertTo-Json -Depth 5

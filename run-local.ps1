[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $StepFile,

    [switch] $Build,
    [switch] $KeepInput
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$analyzerRoot = Join-Path $repoRoot "occt-analyzer"
$projectorRoot = Join-Path $repoRoot "occt-projector"
$inputDir = Join-Path $analyzerRoot "input"
$analysisDir = Join-Path $analyzerRoot "output"
$projectionDir = Join-Path $projectorRoot "output"

$resolvedStep = (Resolve-Path -LiteralPath $StepFile -ErrorAction Stop).Path
if (-not (Test-Path -LiteralPath $resolvedStep -PathType Leaf)) {
    throw "STEP file does not exist: $StepFile"
}
$extension = [IO.Path]::GetExtension($resolvedStep).ToLowerInvariant()
if ($extension -notin @(".stp", ".step")) {
    throw "Only .stp and .step files are supported: $resolvedStep"
}

New-Item -ItemType Directory -Force -Path $inputDir, $analysisDir, $projectionDir | Out-Null
$fileName = [IO.Path]::GetFileName($resolvedStep)
$inputRootPath = [IO.Path]::GetFullPath($inputDir).TrimEnd('\') + '\'
$resolvedStepPath = [IO.Path]::GetFullPath($resolvedStep)
$stagingDir = $null
if ($resolvedStepPath.StartsWith($inputRootPath, [StringComparison]::OrdinalIgnoreCase)) {
    $relativeInput = $resolvedStepPath.Substring($inputRootPath.Length).Replace('\', '/')
}
else {
    $stagingName = ".local-run-$([Guid]::NewGuid().ToString('N'))"
    $stagingDir = Join-Path $inputDir $stagingName
    New-Item -ItemType Directory -Path $stagingDir | Out-Null
    Copy-Item -LiteralPath $resolvedStep -Destination (Join-Path $stagingDir $fileName)
    $relativeInput = "$stagingName/$fileName"
}

$stem = [IO.Path]::GetFileNameWithoutExtension($fileName)
$analysisName = "$stem.json"
$analysisPath = Join-Path $analysisDir $analysisName
$modelOutput = Join-Path $projectionDir $stem
New-Item -ItemType Directory -Force -Path $modelOutput | Out-Null

try {
    Push-Location $analyzerRoot
    try {
        if ($Build) {
            docker compose build analyzer
            if ($LASTEXITCODE -ne 0) { throw "Analyzer image build failed (exit code $LASTEXITCODE)" }
        }
        Write-Host "Analyzing STEP: $fileName"
        docker compose run --rm analyzer "/data/input/$relativeInput" "/data/output/$analysisName"
        if ($LASTEXITCODE -ne 0) { throw "Analyzer failed (exit code $LASTEXITCODE)" }
    }
    finally { Pop-Location }

    Push-Location $projectorRoot
    try {
        if ($Build) {
            docker compose build projector
            if ($LASTEXITCODE -ne 0) { throw "Projector image build failed (exit code $LASTEXITCODE)" }
        }
        Write-Host "Generating views: $stem"
        docker compose run --rm projector "/data/input/$relativeInput" "/data/output/$stem" "/data/analysis/$analysisName"
        if ($LASTEXITCODE -ne 0) { throw "Projector failed (exit code $LASTEXITCODE)" }
    }
    finally { Pop-Location }
}
finally {
    if ($null -ne $stagingDir -and -not $KeepInput) {
        $resolvedStagingDir = [IO.Path]::GetFullPath($stagingDir)
        if (-not $resolvedStagingDir.StartsWith($inputRootPath, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove staging directory outside input: $resolvedStagingDir"
        }
        Remove-Item -LiteralPath $resolvedStagingDir -Recurse -Force
    }
}

Write-Host ""
Write-Host "Completed:"
Write-Host "  Analysis: $analysisPath"
Write-Host "  Views: $modelOutput"

$ErrorActionPreference = "Stop"

$nxManaged = "D:\LenovoSoftstore\NXUG\NXBIN\managed"
$compiler = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$source = Join-Path $PSScriptRoot "Nx3d2dProjection.cs"
$outputDirectory = Join-Path $PSScriptRoot "bin"
$output = Join-Path $outputDirectory "Nx3d2dProjectionV8.dll"
$nxMeasureV26 = Join-Path $PSScriptRoot "..\NxMeasureV8\bin\NxMeasureV26.dll"
$nxMeasureV27 = Join-Path $PSScriptRoot "..\NxMeasureV8\bin\NxMeasureV27.dll"

if (-not (Test-Path $nxManaged)) {
    throw "NX managed directory not found: $nxManaged"
}

New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

if (-not (Test-Path $nxMeasureV26)) {
    throw "NxMeasureV26.dll not found: $nxMeasureV26"
}

if (-not (Test-Path $nxMeasureV27)) {
    throw "NxMeasureV27.dll not found: $nxMeasureV27"
}

& $compiler `
    /nologo `
    /target:library `
    /platform:x64 `
    /optimize+ `
    /out:$output `
    /reference:"$nxManaged\NXOpen.dll" `
    /reference:"$nxManaged\NXOpen.UF.dll" `
    /reference:"$nxManaged\NXOpen.Utilities.dll" `
    /reference:"$nxMeasureV26" `
    /reference:"$nxMeasureV27" `
    $source

if ($LASTEXITCODE -ne 0) {
    throw "Compilation failed. Exit code: $LASTEXITCODE"
}

Copy-Item -LiteralPath $nxMeasureV26 -Destination $outputDirectory -Force
Copy-Item -LiteralPath $nxMeasureV27 -Destination $outputDirectory -Force

Write-Host "Build succeeded: $output"

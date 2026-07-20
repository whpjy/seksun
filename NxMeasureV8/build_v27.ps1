$ErrorActionPreference = "Stop"

$nxManaged = "D:\LenovoSoftstore\NXUG\NXBIN\managed"
$compiler = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$source = Join-Path $PSScriptRoot "NxMeasureV27.cs"
$outputDirectory = Join-Path $PSScriptRoot "bin"
$output = Join-Path $outputDirectory "NxMeasureV27.dll"

New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

& $compiler `
    /nologo `
    /target:library `
    /platform:x64 `
    /optimize+ `
    /out:$output `
    /reference:"$nxManaged\NXOpen.dll" `
    /reference:"$nxManaged\NXOpen.UF.dll" `
    /reference:"$nxManaged\NXOpen.Utilities.dll" `
    $source

if ($LASTEXITCODE -ne 0) {
    throw "Compilation failed. Exit code: $LASTEXITCODE"
}

Write-Host "Build succeeded: $output"


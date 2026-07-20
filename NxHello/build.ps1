$ErrorActionPreference = "Stop"

$nxManaged = "D:\LenovoSoftstore\NXUG\NXBIN\managed"
$compiler = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$source = Join-Path $PSScriptRoot "NxHello.cs"
$outputDirectory = Join-Path $PSScriptRoot "bin"
$output = Join-Path $outputDirectory "NxHello.dll"

if (-not (Test-Path -LiteralPath $compiler)) {
    throw "找不到 C# 编译器：$compiler"
}

if (-not (Test-Path -LiteralPath (Join-Path $nxManaged "NXOpen.dll"))) {
    throw "找不到 NXOpen.dll：$nxManaged"
}

New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

& $compiler `
    /nologo `
    /target:library `
    /platform:x64 `
    /out:$output `
    /reference:"$nxManaged\NXOpen.dll" `
    /reference:"$nxManaged\NXOpen.Utilities.dll" `
    $source

if ($LASTEXITCODE -ne 0) {
    throw "编译失败，退出代码：$LASTEXITCODE"
}

Write-Host "编译成功：$output"

param(
    [string]$DevEcoStudioHome = (Join-Path $env:ProgramFiles 'Huawei/DevEco Studio'),
    [switch]$AppBundle
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$node = Join-Path $DevEcoStudioHome 'tools/node/node.exe'
$hvigor = Join-Path $DevEcoStudioHome 'tools/hvigor/bin/hvigorw.js'
if (-not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $hvigor)) {
    throw 'DevEco Studio toolchain not found. Supply the installation directory through -DevEcoStudioHome.'
}

$previousSdk = $env:DEVECO_SDK_HOME
$previousPath = $env:Path
$env:DEVECO_SDK_HOME = Join-Path $DevEcoStudioHome 'sdk'
$env:Path = (Join-Path $DevEcoStudioHome 'tools/ohpm/bin') + ';' + $previousPath
$flags = if ($AppBundle) {
    @('assembleApp', '--mode', 'project', '-p', 'product=default', '-p', 'buildMode=release')
} else {
    @('assembleHap', '--mode', 'module', '-p', 'module=entry@default', '-p', 'product=default', '-p', 'buildMode=debug')
}
Push-Location (Join-Path $projectRoot 'harmony-demo')
try {
    & $node $hvigor @flags --no-daemon
    $buildExit = $LASTEXITCODE
} finally {
    Pop-Location
    $env:DEVECO_SDK_HOME = $previousSdk
    $env:Path = $previousPath
}
exit $buildExit

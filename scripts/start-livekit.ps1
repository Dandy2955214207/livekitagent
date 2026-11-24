. "$PSScriptRoot/project-env.ps1"
$serverExe = Join-Path $ProjectRoot '.tools/livekit/livekit-server.exe'
$serverConfig = Join-Path $ProjectRoot 'config/livekit.local.yaml'
if (-not (Test-Path -LiteralPath $serverExe)) { throw 'LiveKit server has not been installed.' }
if (-not (Test-Path -LiteralPath $serverConfig)) { & "$PSScriptRoot/init-local.ps1" }
& $serverExe --config $serverConfig
exit $LASTEXITCODE

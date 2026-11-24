. "$PSScriptRoot/project-env.ps1"
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
Set-Location $ProjectRoot
& (Join-Path $ProjectRoot '.venv/Scripts/python.exe') -m livekit_vlm_agent.worker start
exit $LASTEXITCODE

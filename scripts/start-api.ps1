. "$PSScriptRoot/project-env.ps1"
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
Set-Location $ProjectRoot
& (Join-Path $ProjectRoot '.venv/Scripts/python.exe') -m uvicorn livekit_vlm_agent.api:app --host 127.0.0.1 --port 8000 --no-access-log
exit $LASTEXITCODE

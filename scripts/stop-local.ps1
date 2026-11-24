. "$PSScriptRoot/project-env.ps1"
& (Join-Path $ProjectRoot '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'run_local.py') --stop
exit $LASTEXITCODE

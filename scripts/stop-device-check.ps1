. "$PSScriptRoot/project-env.ps1"
& (Join-Path $ProjectRoot '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'run_device_check.py') --stop
exit $LASTEXITCODE

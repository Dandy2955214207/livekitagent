. "$PSScriptRoot/project-env.ps1"
& (Join-Path $ProjectRoot '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'check_environment.py')
exit $LASTEXITCODE

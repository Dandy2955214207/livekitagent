. "$PSScriptRoot/project-env.ps1"
& (Join-Path $ProjectRoot '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'probe_qwen.py')
exit $LASTEXITCODE

. "$PSScriptRoot/project-env.ps1"
Set-Location $ProjectRoot
& (Join-Path $ProjectRoot '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'run_local.py') --mobile --harness
exit $LASTEXITCODE

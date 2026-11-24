. "$PSScriptRoot/project-env.ps1"
Set-Location (Join-Path $ProjectRoot 'web')
& npm.cmd run dev
exit $LASTEXITCODE

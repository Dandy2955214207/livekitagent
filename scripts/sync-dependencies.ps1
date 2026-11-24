. "$PSScriptRoot/project-env.ps1"
Push-Location $ProjectRoot
try {
    & $UvExe sync --locked
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency sync failed.' }
    Push-Location (Join-Path $ProjectRoot 'web')
    try {
        & npm.cmd ci --ignore-scripts --no-fund --no-audit
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency sync failed.' }
    } finally { Pop-Location }
} finally { Pop-Location }

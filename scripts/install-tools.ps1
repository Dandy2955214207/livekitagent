# Project-local Windows x64 tools; no system PATH or registry changes.
. "$PSScriptRoot/project-env.ps1"
if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString() -ne 'X64') {
    throw 'This tool manifest targets Windows x64.'
}
$versions = Get-Content -LiteralPath (Join-Path $ProjectRoot 'config/tool-versions.json') -Raw | ConvertFrom-Json
$downloads = Join-Path $ProjectRoot '.tools/downloads'
New-Item -ItemType Directory -Force -Path $downloads | Out-Null

function Get-VerifiedArtifact($Url, $Target, $Hash) {
    if (Test-Path -LiteralPath $Target) {
        if ((Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Hash) {
            throw "Existing artifact checksum mismatch: $Target"
        }
    } else {
        Invoke-WebRequest -Uri $Url -OutFile $Target -TimeoutSec 180
        if ((Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Hash) {
            throw "Downloaded artifact checksum mismatch: $Target"
        }
    }
}

if (-not (Test-Path -LiteralPath $UvExe)) {
    $archive = Join-Path $downloads 'uv-wheel.zip'
    Get-VerifiedArtifact $versions.uv.url $archive $versions.uv.sha256
    $unpack = Join-Path $ProjectRoot '.tools/uv-wheel'
    Expand-Archive -LiteralPath $archive -DestinationPath $unpack -Force
    $binaries = @(Get-ChildItem -LiteralPath $unpack -Recurse -Filter uv.exe)
    if ($binaries.Count -ne 1) { throw 'Expected exactly one uv.exe in the official wheel.' }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $UvExe) | Out-Null
    Copy-Item -LiteralPath $binaries[0].FullName -Destination $UvExe
}
$serverDir = Join-Path $ProjectRoot '.tools/livekit'
if (-not (Test-Path -LiteralPath (Join-Path $serverDir 'livekit-server.exe'))) {
    $archive = Join-Path $downloads 'livekit.zip'
    Get-VerifiedArtifact $versions.livekit_server.url $archive $versions.livekit_server.sha256
    Expand-Archive -LiteralPath $archive -DestinationPath $serverDir -Force
}
& $UvExe python install $versions.python.version --no-bin --no-registry
if ($LASTEXITCODE -ne 0) { throw 'Python installation failed.' }
& "$PSScriptRoot/init-local.ps1"
Write-Output 'Tools ready. Run scripts/sync-dependencies.ps1 next.'

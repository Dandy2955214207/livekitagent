# Idempotent: never overwrites an existing .env or local server configuration.
. "$PSScriptRoot/project-env.ps1"
$envPath = Join-Path $ProjectRoot '.env'
$serverPath = Join-Path $ProjectRoot 'config/livekit.local.yaml'
if (-not (Test-Path -LiteralPath $envPath)) {
    $key = 'local_' + [Guid]::NewGuid().ToString('N')
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $secret = -join ($bytes | ForEach-Object { $_.ToString('x2') })
    $content = [IO.File]::ReadAllText((Join-Path $ProjectRoot '.env.example'))
    $content = $content.Replace('LIVEKIT_API_KEY=', "LIVEKIT_API_KEY=$key")
    $content = $content.Replace('LIVEKIT_API_SECRET=', "LIVEKIT_API_SECRET=$secret")
    [IO.File]::WriteAllText($envPath, $content, (New-Object Text.UTF8Encoding $false))
    Write-Output 'Created .env with random local LiveKit credentials.'
}
if (-not (Test-Path -LiteralPath $serverPath)) {
    $values = @{}
    foreach ($line in [IO.File]::ReadAllLines($envPath)) {
        if ($line -match '^(LIVEKIT_API_KEY|LIVEKIT_API_SECRET)=(.*)$') {
            $values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
    $key = $values['LIVEKIT_API_KEY']
    $secret = $values['LIVEKIT_API_SECRET']
    if ($key -notmatch '^[A-Za-z0-9_-]+$' -or $secret -notmatch '^[A-Za-z0-9_-]{32,}$') {
        throw 'Local credentials must use letters, digits, _ or -; secret must be >=32 characters.'
    }
    $content = [IO.File]::ReadAllText((Join-Path $ProjectRoot 'config/livekit.example.yaml'))
    $content = $content.Replace('replace_with_local_key', $key)
    $content = $content.Replace('replace_with_random_secret_at_least_32_characters', $secret)
    [IO.File]::WriteAllText($serverPath, $content, (New-Object Text.UTF8Encoding $false))
    Write-Output 'Created config/livekit.local.yaml.'
}
Write-Output 'Local configuration ready; existing files were preserved.'

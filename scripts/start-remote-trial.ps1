param(
    [switch]$Mock,
    [switch]$Stop,
    [switch]$ReuseInviteCode,
    [ValidateRange(1024, 65535)][int]$Port = 8097
)

. "$PSScriptRoot/project-env.ps1"
Set-Location $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot 'src'
$python = Join-Path $ProjectRoot '.venv/Scripts/python.exe'
if ($Stop -or -not $Mock) {
    $flag = if ($Stop) { '--stop' } else { '--qwen-live' }
    $flags = @('--trial-port', $Port, $flag)
    if ($ReuseInviteCode) { $flags += '--reuse-trial-invite' }
    & $python (Join-Path $PSScriptRoot 'run_local.py') @flags
    exit $LASTEXITCODE
}
$secretDir = New-Item -ItemType Directory -Force (Join-Path $ProjectRoot '.runtime/remote-trial')
$inviteFile = Join-Path $secretDir.FullName "mock-$Port-invite.txt"
$secrets = & $python -c "import secrets; print(secrets.token_urlsafe(24)); print(secrets.token_urlsafe(32))"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$env:HARMONY_TRIAL_CODE = $secrets[0]
$env:HARMONY_GATEWAY_TOKEN = $secrets[1]
try {
    Write-Host "Remote trial: http://127.0.0.1:$Port (mock). Invite code file: $inviteFile"
    Set-Content -LiteralPath $inviteFile -Value $env:HARMONY_TRIAL_CODE -Encoding ascii
    & $python -m livekit_vlm_agent.harmony_gateway --mode mock --host 127.0.0.1 --port $Port
    $trialExit = $LASTEXITCODE
} finally {
    Remove-Item Env:HARMONY_TRIAL_CODE, Env:HARMONY_GATEWAY_TOKEN -ErrorAction SilentlyContinue
}
exit $trialExit

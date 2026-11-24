param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('manual','vision','cancel','semantic','controls','pause','backchannel')]
    [string]$Stage,
    [ValidateSet('manual','semantic_vad','semantic_vad_ec')]
    [string]$PauseMode = 'semantic_vad',
    [switch]$SaveAudio
)
. "$PSScriptRoot/project-env.ps1"
$arguments = @((Join-Path $PSScriptRoot 'verify_m0.py'), $Stage, '--pause-mode', $PauseMode)
if ($SaveAudio) { $arguments += '--save-audio' }
& (Join-Path $ProjectRoot '.venv/Scripts/python.exe') @arguments
exit $LASTEXITCODE

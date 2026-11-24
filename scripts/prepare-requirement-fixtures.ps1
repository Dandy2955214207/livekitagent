$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$fixtureDir = Join-Path (Split-Path $PSScriptRoot -Parent) '.runtime/m0-fixtures'
New-Item -ItemType Directory -Force -Path $fixtureDir | Out-Null
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $synth.SelectVoice('Microsoft Huihui Desktop')
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
    $samples = @{
        'requirement-timer' = '顺便三分钟后提醒我休息。'
        'requirement-question' = '画这个圆有什么技巧？'
        'requirement-cancel' = '休息的提醒不用了。'
        'requirement-pause' = '画画先暂停一下。'
    }
    foreach ($name in $samples.Keys) {
        $synth.SetOutputToWaveFile((Join-Path $fixtureDir "$name.wav"), $format)
        $synth.Speak($samples[$name]); $synth.SetOutputToNull()
    }
} finally { $synth.Dispose() }
Write-Output 'Four synthetic 16 kHz mono speech fixtures prepared.'

. "$PSScriptRoot/project-env.ps1"
Add-Type -AssemblyName System.Speech
$fixtureDir = Join-Path $ProjectRoot '.runtime/m0-fixtures'
New-Item -ItemType Directory -Force -Path $fixtureDir | Out-Null
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $synth.SelectVoice('Microsoft Huihui Desktop')
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
        16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
    $synth.SetOutputToWaveFile((Join-Path $fixtureDir 'event-warmup.wav'), $format)
    $synth.Speak('你好，我们做一次观察提醒测试。')
    $synth.SetOutputToNull()
} finally { $synth.Dispose() }

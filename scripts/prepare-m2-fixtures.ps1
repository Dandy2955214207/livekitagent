. "$PSScriptRoot/project-env.ps1"
Add-Type -AssemblyName System.Speech
$dir = Join-Path $ProjectRoot '.runtime/m2-fixtures'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$texts = @(
    '请说出当前画面里的动物是什么。',
    '请只说出当前画面的主要颜色。',
    '请只说出现在画面的主要颜色。',
    '请只回答：语音仍然正常。'
)
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $synth.SelectVoice('Microsoft Huihui Desktop')
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(48000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
    for($index=0; $index -lt $texts.Count; $index++) {
        $synth.SetOutputToWaveFile((Join-Path $dir "round-$($index+1).wav"), $format)
        $synth.Speak($texts[$index]); $synth.SetOutputToNull()
    }
} finally { $synth.Dispose() }
Write-Output 'Prepared four fixed, non-confidential synthetic voice questions for M2.'

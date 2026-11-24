$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$memorySynth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $memorySynth.SelectVoice('Microsoft Huihui Desktop')
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
    $target = Join-Path $PSScriptRoot '../.runtime/m0-fixtures/call-memory.wav'
    $memorySynth.SetOutputToWaveFile([System.IO.Path]::GetFullPath($target), $format)
    # UTF-8 payload supports Windows PowerShell's legacy script encoding.
    # Question: recall the circle's initial color and its later color in this call.
    $text = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('6K+35Zue5b+G6L+Z5qyh6YCa6K+d5YmN6Z2i55S76Z2i5Lit5ZyG5b2i55qE6aKc6Imy77yM5YWI5piv5LuA5LmI6aKc6Imy77yM5ZCO5p2l5Y+Y5oiQ5LqG5LuA5LmI6aKc6Imy77yf'))
    $memorySynth.Speak($text)
    $memorySynth.SetOutputToNull()
} finally { $memorySynth.Dispose() }

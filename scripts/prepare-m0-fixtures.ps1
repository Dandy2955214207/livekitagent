. "$PSScriptRoot/project-env.ps1"
Add-Type -AssemblyName System.Speech
$fixtureDir = Join-Path $ProjectRoot '.runtime/m0-fixtures'
New-Item -ItemType Directory -Force -Path $fixtureDir | Out-Null
$photoPath = Join-Path $fixtureDir 'dog-original.jpg'
if (-not (Test-Path -LiteralPath $photoPath)) {
    Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/pytorch/hub/master/images/dog.jpg' -OutFile $photoPath -TimeoutSec 45
}
$photoHash = (Get-FileHash -LiteralPath $photoPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($photoHash -ne 'f3f87bb8ab3c26c7ecfd3ac60421d7f32b0503d1d6c5baf8bac42ed93d86351a') {
    throw 'Public photo fixture changed: SHA256 does not match the verified M0 sample.'
}
$utterances = [ordered]@{
    greeting = '你好，请只回答：语音测试成功。'
    memory = '请记住我的测试代号是蓝鲸七号。请简短确认。'
    recall = '我刚才说的测试代号是什么？请只说代号。'
    photo = '请用一句话描述图片中最主要的动物。'
    color = '请只说当前图片中大圆形的颜色。'
    long = '请从一开始连续数到五十，不要省略。'
    interrupt = '停一下，不要继续数数了，请只回答：已经停止。'
    recover = '现在请只回答：新的回合。'
    backchannel = '嗯。'
    pause_first = '我想问你一个问题：'
    pause_second = '一加一等于几？请只回答数字。'
}
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $synth.SelectVoice('Microsoft Huihui Desktop')
    $synth.Rate = 0
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
        16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
    foreach ($entry in $utterances.GetEnumerator()) {
        $target = Join-Path $fixtureDir ($entry.Key + '.wav')
        $synth.SetOutputToWaveFile($target, $format)
        $synth.Speak($entry.Value)
        $synth.SetOutputToNull()
        Write-Output "Prepared $($entry.Key).wav"
    }
} finally { $synth.Dispose() }
$utterances | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $fixtureDir 'utterances.json') -Encoding utf8
& (Join-Path $ProjectRoot '.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'prepare_m0_images.py')
exit $LASTEXITCODE

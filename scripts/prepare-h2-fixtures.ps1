. "$PSScriptRoot/project-env.ps1"
Add-Type -AssemblyName System.Speech
$dir = Join-Path $ProjectRoot '.runtime/h2-fixtures'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$samples = @{
    guide = '请用三个步骤指导我在桌上摆放三个积木，每一步只让我摆一个，现在先讲第一步，等待我确认。'
    done = '当前这一步已经完成了，请继续下一步。'
    question = '这一步应该怎么放，请简短再解释一下。'
    after = '指导任务已经结束，现在普通聊天：桌上可以放哪些日常小物品？'
    showcase = '接下来请逐个介绍我展示的物品，我会切换画面，不需要我每次再说话。每件物品只讲一两句。'
}
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    $synth.SelectVoice('Microsoft Huihui Desktop')
    $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000,
        [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
        [System.Speech.AudioFormat.AudioChannel]::Mono)
    foreach ($name in $samples.Keys) {
        $synth.SetOutputToWaveFile((Join-Path $dir "$name.wav"), $format)
        $synth.Speak($samples[$name]); $synth.SetOutputToNull()
    }
} finally { $synth.Dispose() }

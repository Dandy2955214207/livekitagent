. "$PSScriptRoot/project-env.ps1"
Add-Type -AssemblyName System.Speech
$dir = Join-Path $ProjectRoot '.runtime/m1-fixtures'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$texts = @(
    '你好，请只回答：第一轮成功。',
    '我的测试代号是蓝鲸七号，请简短确认。',
    '请只复述刚才我说的测试代号。',
    '请只回答：第四轮成功。',
    '请只回答：第五轮成功。',
    '请只回答：第六轮成功。',
    '请只回答：第七轮成功。',
    '请只回答：第八轮成功。',
    '请只回答：第九轮成功。',
    '请只回答：第十轮成功。'
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
@'
from pathlib import Path
import wave
p=Path('.runtime/m1-fixtures')
for count in (1,10):
    output=bytearray(48000*2*10)
    for i in range(1,count+1):
        with wave.open(str(p/f'round-{i}.wav')) as w:
            assert (w.getframerate(),w.getsampwidth(),w.getnchannels())==(48000,2,1)
            data=w.readframes(w.getnframes())
        output.extend(data)
        output.extend(bytes(max(0,48000*2*12-len(data))))
    output.extend(bytes(48000*2*5))
    with wave.open(str(p/f'browser-{count}.wav'),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(48000);w.writeframes(output)
print('Prepared isolated browser fake-microphone WAV fixtures.')
'@ | & (Join-Path $ProjectRoot '.venv/Scripts/python.exe') -
exit $LASTEXITCODE

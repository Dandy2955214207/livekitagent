# M0 真实协议验证报告

日期：2026-10-29（Asia/Shanghai）。模型：`qwen3.8-omni-flash-realtime`，北京工作空间，Tina 音色。依赖沿用 2026-10-28 的锁定组合。

## 结论

语音、会话内追问、图片、显式取消和语音插话的协议基线已完成，可以开始 M1 的 LiveKit 纯语音集成。语义轮次仍有已知问题，**不属于所有效果验收通过**：`semantic_vad` 在一条短句样本中漏掉问题前半句，`semantic_vad_ec` 能识别主问题，但将句末补充要求分成第二轮。

本次使用本机 Windows 中文 TTS 生成的真实 16 kHz PCM 波形调用云端模型，没有使用文字消息代替音频，也没有模拟模型结果。没有启用麦克风、摄像头或扬声器。固定 TTS 样本不能代替后续真人语音、噪声、回声和自然停顿验收。

## 实测结果

| 检查项 | 观察 | 结论 |
| --- | --- | --- |
| 音频单轮 | 固定问句得到“语音测试成功”，同时返回 PCM 与输出转写 | 通过 |
| 同连接记忆 | 说出“蓝鲸七号”，下一轮正确复述代号 | 通过 |
| 公开照片 | 识别出草地上的白色萨摩耶犬，与人工检查照片一致 | 该样本通过 |
| 图片切换 | 同连接先红圆再蓝圆，分别回答“红色”“蓝色” | 该发送顺序通过 |
| 显式取消 | 首段音频后发送 response.cancel，78 ms 后收到 cancelled；下一轮回答“新的回合” | 通过 |
| 自动成轮 | semantic_vad 和 semantic_vad_ec 均在语音结束后自动生成，不发送 commit/response.create | 通过 |
| 明确插话 | 两模式均取消正在进行的数数，随后回答“已经停止” | 通过 |
| 孤立“嗯” | 两模式均未创建额外回答 | 该样本通过 |
| 回答期间的“嗯” | 两模式均继续完成数数，没有新增回答 | 该样本通过 |
| 句中短暂停顿 | 约400 ms 显式静音之外，保留最多80 ms 语音尾部。未观察到在第二段开始前提前回答，但后续分句处理有问题 | 部分通过，见下文 |
| 迟到音频与重连隔离 | 真实取消事件回放 + 人工注入取消后/下一轮后的旧音频 + 旧连接标记 | 7 项离线回归通过 |

图片使用 JPEG，最大边640、最大高480；照片原始约646 KB，发送前缩小到约55 KB；红蓝测试图各约12 KB。均符合当前文档的 Base64 256 KB 上限。发送顺序是先至少一段音频，再图片，再完成本轮音频并提交。尚未验证连续1 FPS摄像头流或“更换实物后立即提问”。

## 短句问题与对照实验

固定输入：“我想问你一个问题：” + 约400 ms显式停顿 + “一加一等于几？请只回答数字。”

| 模式 | 实际输入转写 | 实际回答 / 事件 | 判断 |
| --- | --- | --- | --- |
| semantic_vad | “请只回答数字。” | “1”；独立诊断重复出现 | 本轮转写缺少算术问题，不能用于稳定短句验收 |
| 手动提交 | 完整保留两段话 | “2” | 同样波形可识别和回答，排除明显 PCM 格式错误 |
| semantic_vad_ec | 首项包含完整主问题；末尾“请只回答数字”成为第二项 | 首回答“一加一等于二。”；事件中共出现两次 response.created/done | 保留主问题，但边界更积极，不能视为整句单轮通过 |

输入转写和回答表现共同指向轮次识别/裁剪与该样本的交互；无法仅凭这几个样本确定模型内部的具体原因。EC 的首段回答在整段音频发送完毕前已经开始，因此这条样本的负“首音延迟”是提前响应现象，未纳入延迟汇总。

当前本地配置 `QWEN_TURN_DETECTION=semantic_vad_ec`，作为 M1 的候选默认值，以减少该样本的主问题丢失；可改回 `semantic_vad` 对照。候选选择不代表轮次已调校完成。M3 需补充真人短句、附和、完整/不完整语义停顿、多句输入，必要时再评估手动提交策略。

## 测量口径与用量

- 9 个普通、未中断的基线回答：固定输入发送完毕到服务端收到首段音频，范围563–797 ms，中位数688 ms。样本很少；不含浏览器播放，不报告生产 P95。
- 显式取消：一次78 ms，为本地发起取消到收到服务端终态的耗时，**不是用户实际听到旧声音停止的时间**。
- 明确插话：开始发送插话波形到 speech_started，semantic_vad 一次953 ms，semantic_vad_ec 一次2000 ms。样本不足以比较两模式优劣；也不等同于从真人首次发声起算。
- 取消终态和 speech_started 在接收器内几乎同时被处理，记录差值为0 ms。不能据此声称播放能在0 ms停止。
- 所有尝试合计观察到21个 response.done，其中3个 cancelled。17个回答的用量合计17118 tokens；取消及一个额外分句回答未完整纳入用量，**这是已知下限，不是账单总量**。价格以账号控制台为准。

模型文本可能显著早于音频抵达：显式取消样本仅收到0.32秒音频时，文本已推进到数数的较后部分。后续集成不能把全部已生成文字当作用户已经听到的内容。本次没有验证远端精确历史裁剪。

## 工程交付与复现

- `src/livekit_vlm_agent/providers/qwen_client.py`：独立异步 WebSocket 客户端，串行写入、独立接收、超时、有界测试事件/音频内存、取消输出门控，无自动重试。
- `scripts/prepare-m0-fixtures.ps1`：生成固定中文音频；下载公开照片时校验已验证的 SHA256，生成红蓝图和输入清单。
- `scripts/verify-m0.ps1` / `verify_m0.py`：逐阶段真实模型验证；已存在的同阶段结果自动复制到 history，保留失败尝试。
- `scripts/summarize_m0.py`：汇总全部尝试，并从事件数识别额外分句，明确标记未全部验收通过。
- `tests/fixtures/qwen-cancel-trace.json`：来自本次真实调用的取消样本，ID已匿名化，无原始音视频或Base64。
- `tests/`：7项离线检查，涵盖响应完成、取消后迟到音频、下一轮污染、旧连接、错误脱敏及断线唤醒。

项目根目录执行：

```powershell
# 无模型费用；准备本机 TTS、固定图片及 SHA256 清单
.\scripts\prepare-m0-fixtures.ps1

# 以下每条都会调用真实云端模型；按需选择，不必重复整套测试
.\scripts\verify-m0.ps1 manual -SaveAudio
.\scripts\verify-m0.ps1 vision
.\scripts\verify-m0.ps1 cancel
.\scripts\verify-m0.ps1 semantic
.\scripts\verify-m0.ps1 backchannel
.\scripts\verify-m0.ps1 pause -PauseMode semantic_vad
.\scripts\verify-m0.ps1 pause -PauseMode manual
.\scripts\verify-m0.ps1 pause -PauseMode semantic_vad_ec

# 无模型费用
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe .\scripts\summarize_m0.py
```

本机完整结果、失败历史、固定输入哈希和用量见 `.runtime/m0-results/summary.json` 及同目录各阶段 JSON。事件文件只记录类型、时间、ID和字节数，不记录Base64媒体或Key。仅为试听而显式保存了第一条固定回答 `greeting-output.wav`；没有录制物理设备音视频。

## 后续接入决定与边界

优先采用开发方案的自定义 LiveKit RealtimeModel / RealtimeSession 适配路线，复用独立 Qwen 客户端。锁定 SDK 已提供音频、视频推送、模型侧轮次事件和 interrupt 等扩展点；尚未实现或运行 AgentSession 适配器，因此框架级播放和打断还要在 M1/M3 检查。

能力声明建议：模型侧轮次、音频输出、手动成轮可启用；用户转写事件已观察到，但不能承诺稳定完整。`message_truncation`、可变远端历史、工具调用和无缝恢复保持关闭。`supports_overlapping_speech` 需在实际框架播放中验证后再开启，不能仅凭本次直连测试声明。

尚未验收：浏览器实际播放延迟、输出积压清理/尾音、自然语音和回声、连续视频时效、精确历史裁剪、20分钟稳定性、30轮性能统计、额度/并发上限与完整账单。这些属于后续里程碑，不以本次短协议测试替代。

官方参考：[实时协议](https://www.alibabacloud.com/help/en/model-studio/realtime)、[客户端事件](https://www.alibabacloud.com/help/en/model-studio/client-events)、[服务端事件](https://www.alibabacloud.com/help/en/model-studio/server-events)、[照片来源](https://github.com/pytorch/hub/blob/master/images/dog.jpg)。

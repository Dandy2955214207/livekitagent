# M0 协议与 SDK 初步记录

后续M1已实现自定义RealtimeModel/AgentSession接入，并通过浏览器固定音频连续10轮验证，见 [M1验收记录](m1-verification.md)。下文保留M0时点的协议观察。

## 2026-10-29 更新

真实语音、同会话追问、公开照片、图片切换、显式取消和语义插话均已验证。短句轮次存在可复现差异：semantic_vad 漏掉一条样本的问题前半句；semantic_vad_ec 保留主问题但将句末指令另成轮。当前候选配置为 semantic_vad_ec，保留可切换选项，未将全部效果标记为通过。

详细能力矩阵、对照实验、时间与用量口径、复现命令和接入决定见 [M0真实协议验证报告](verification-report.md)。截至本次，有7项离线回归通过，已保留真实脱敏取消事件以及全部失败尝试。

主路线继续采用独立 Qwen 客户端 + 自定义 LiveKit RealtimeModel 适配；框架播放集成留到 M1。精确历史裁剪和无缝恢复未验证，不声明支持。

## 2026-10-28 历史记录

日期：2026-10-28。仅记录真实观察，未测试的行为保持待验证。

| 能力 | 状态 | 依据与边界 |
| --- | --- | --- |
| 工作空间 Endpoint / Key / 模型可连接 | 通过 | 真实 WebSocket 收到 session.created |
| PCM + Tina + 手动轮次配置 | 配置确认通过 | 收到 session.updated；未发送媒体或验证听感 |
| semantic_vad | 配置确认通过 | 收到 session.updated；未验证语义轮次/附和/打断行为 |
| 单轮/多轮语音、回答文字 | 待验证 | 需要固定语音样本及真实输出 |
| 图像限制与视频时序 | 待验证 | 仍需确认发送顺序、帧龄和画面切换 |
| 取消、迟到音频、历史关系 | 待验证 | 不能从配置握手推断 |
| 原始 LiveKit RTC 音频路径 | 通过 | 两客户端真实本机音频发布/订阅/解码 |

锁定的 `livekit-agents==1.8.9` 在已安装源码 `livekit/agents/llm/realtime.py` 中提供 `RealtimeModel.session()`、`RealtimeSession.push_audio()`、`push_video()`、`generate_reply()`、`interrupt()`、`truncate()` 等扩展点。`RealtimeCapabilities` 包括 turn_detection、message_truncation、user_transcription、audio_output、supports_overlapping_speech 等能力声明。

这些接口的存在支持继续评估方案中的自定义实时模型路线，但尚未实现适配器，不能宣布框架行为已与 Qwen 对齐。特别是 truncate 的远端语义、取消后的历史一致性和重连需要实测；能力声明应保守填写。

Qwen 本次有序交换的脱敏事件类型：

```text
RECV session.created
SEND session.update   # manual / PCM / voice
RECV session.updated
SEND session.update   # semantic_vad
RECV session.updated
CLOSE
```

没有调用 response.create 或发送 input_audio_buffer.append / input_image_buffer.append，没有记录原始音视频。下一阶段再准备固定样本，完成开发方案要求的可重复协议基线。

官方参考：[Qwen Realtime](https://www.alibabacloud.com/help/en/model-studio/realtime)、[客户端事件](https://www.alibabacloud.com/help/en/model-studio/client-events)。

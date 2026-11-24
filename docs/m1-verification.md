# M1 LiveKit 浏览器纯语音闭环

日期：2026-10-29。状态：实现完成，浏览器固定音频连续10轮自动验收通过；真人设备听感与回声仍需后续人工验证。

## 已实现的链路

```text
浏览器 → 本机会话API → 随机房间 + 显式派发 qwen-voice-agent
浏览器麦克风轨道 → LiveKit → AgentSession / RoomIO
RoomIO 16 kHz mono PCM → 自定义 QwenRealtimeModel → Qwen WebSocket
Qwen 24 kHz PCM / 文字 → AgentSession → LiveKit → 浏览器播放 / 转写
```

沿用 M0 的独立 Qwen 客户端，实现 LiveKit `RealtimeModel` / `RealtimeSession` 适配，未启用 RTC 薄桥接备用路径，也未增加独立 STT 或 TTS 服务。

| 模块 | 行为 |
| --- | --- |
| API | 创建随机房间和用户身份，签发房间限定60秒加入Token，显式派发；单活动会话；状态查询、挂断和页面关闭尽力通知 |
| Worker | 按派发元数据只绑定目标用户；创建 AgentSession 和模型连接；每5秒心跳，离房/错误关闭会话 |
| 模型适配 | 16 kHz单声道输入、24 kHz输出；轮次/转写/生成流事件转换；旧响应门控；不宣称精确历史裁剪或无缝恢复 |
| 浏览器 | 加入、麦克风开关、挂断、回答音频、用户/AI转写、连接状态和错误；播放被阻止时显示启用声音按钮 |
| 清理 | 45秒接通超时、默认30分钟TTL、Worker心跳超时清理；派发失败回滚自己创建的房间 |
| 缓冲 | 输入最多100个20 ms帧，约2秒；输出待消费音频默认最多10秒，溢出终止并提示重新加入 |

浏览器只取得房间Token和本会话管理凭据；模型Key与LiveKit Secret不返回端侧。Worker回调使用另一份独立随机凭据。API拒绝未知Origin和非JSON创建请求。短期房间Token限制加入窗口，删除房间不应被理解为通用JWT即时吊销机制。

实时运行时，Qwen客户端只保留最近256个事件和最多32条响应状态，不积累M0测试轨迹。会话关闭后释放模型连接和媒体任务，不保存原始音视频。房间和会话均由当前API管理，不删除其他项目的房间。

## 验收结果

测试使用已安装的 Edge，通过 playwright-core 1.64.1 启动独立无头测试配置。固定中文TTS WAV作为假麦克风进入浏览器的真实 getUserMedia / WebRTC 路径，再经过本机LiveKit、显式派发、AgentSession和真实Qwen API。没有采集物理麦克风，没有改变用户浏览器配置或权限。

| 检查 | 实际结果 |
| --- | --- |
| 一轮闭环 | 页面自动接通Agent，模型回答“第一轮成功”，浏览器收到文字与非静音音频 |
| 连续10轮 | 同一房间连续得到10条最终AI回答，包含记住“蓝鲸七号”后正确追问复述；用户和AI各10条最终转写 |
| RTC媒体 | 10轮报告记录入站168722 bytes、出站375886 bytes、入站总音频能量0.627，统计丢包0 |
| 浏览器播放 | 音频元素currentTime约120.15秒、paused=false、readyState=4；验证了播放链路推进，未测量扬声器的实际声学输出 |
| 麦克风切换 | 关闭/开启按钮和轨道静音状态切换通过 |
| 正常挂断 | API返回closed，用户离房；Worker异步退出当前任务，清理检查最多等待20秒，最终Demo房间、Worker任务、API活动会话均为0 |
| 麦克风权限拒绝 | 显示明确提示，未创建API会话或模型连接 |
| 加入途中挂断 | 延迟创建响应后取消，迟到响应被清理，不重新接通、不遗留活动会话 |
| 窄屏布局 | 390px宽度无横向溢出，控件仍可访问 |
| 自动回归 | 19项Python测试通过，覆盖API访问边界、并发加入、派发回滚、TTL/Worker清理、流事件与缓冲、旧响应/旧连接、断线与错误脱敏 |
| 构建与启动 | Vite构建通过；统一启动、健康检查、停止与再次启动通过 |

首次十轮尝试因比较器只接受中文“七”而误判模型正确的“蓝鲸7号”；并观察到多句追问被分轮。原始失败报告已保留在history。最终脚本统一比较中英文数字写法，将连通性验收的追问改为单句；没有修改模型返回内容。M0的多句轮次限制仍然成立。

本机完整证据位于 `.runtime/m1-results/`：

- `browser-10-report.json`：十轮转写、RTC统计、播放状态、页面错误列表（空）、最终会话状态。
- `browser-10.png`：十轮完成时的浏览器页面。
- `browser-1-report.json`：统一启动器下的最终单轮复核。
- `lifecycle-report.json`：权限拒绝、取消加入、窄屏检查。
- `cleanup-report.json`：房间数、Worker活动任务数、API活动会话数清理核对。
- `history/`：历史尝试，包含失败记录，不覆盖掩盖。

## 启动和停止

首次安装按README准备工具和依赖。已准备的工作区中：

```powershell
# 修改前端后重新构建；API直接托管构建结果，无需另开Vite
npm.cmd run build --prefix web
.\scripts\start-local.ps1
```

打开 `http://127.0.0.1:8000`，戴耳机后点击“加入通话”，允许麦克风。若浏览器阻止自动播放，点击“启用声音播放”。此时使用的才是用户实际麦克风，语音会发送到已配置的Qwen服务。

```powershell
# 另一个PowerShell窗口，或启动窗口按Ctrl+C
.\scripts\stop-local.ps1
```

统一启动器只启动和停止自己的子进程；端口被其他进程占用时直接退出，不终止已有服务。停止整套服务会断开通话并重置上下文。脱敏服务日志在 `.runtime/logs/`，不上传云端。

开发时也可在独立终端运行 `start-livekit.ps1`、`start-api.ps1`、`start-worker.ps1`、`start-web.ps1`；开发页端口5173，代理到8000。不要与统一启动器同时占用相同端口。

## 复现自动检查

```powershell
# 无模型调用
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm.cmd run build --prefix web

# 下列浏览器语音测试会调用真实模型；使用固定假麦克风
.\scripts\prepare-m1-fixtures.ps1
node web/tests/m1-smoke.mjs 10

# 页面生命周期检查不发布用户媒体；清理检查只查询本机服务
node web/tests/lifecycle.mjs
.\.venv\Scripts\python.exe .\scripts\check_m1_cleanup.py
```

## 仍保留的边界

- 默认候选仍为semantic_vad_ec，较积极的多句分轮和偶发输入转写不准没有在M1解决。
- 模型侧插话已由M0验证，适配器已接入本地播放中断与取消门控；M1没有用真人声学测量验证尾音或中断P95，完整打断验收属于M3。
- 没有摄像头输入；浏览器页面仅纯语音。连续视频与帧龄在M2实现。
- 没有完成20分钟稳定性、外放回声、30轮性能统计、真实设备自然语音准确率或断线无缝恢复。
- 本次浏览器测试采集的是固定样本的文字和媒体统计；不把它们当作真人体验验收。

下一步：M2订阅目标用户摄像头、最新帧缓存、采样/JPEG编码、图片发送和帧龄观测。

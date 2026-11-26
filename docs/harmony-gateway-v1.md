# 新视界鸿蒙通话网关协议 v1

新视界原生通话页通过一条 WebSocket 发送麦克风和相机数据，接收 AI 语音、字幕和通话状态。模拟网关和真实网关使用相同协议，App 切换环境时只需要换地址和测试凭据。真实网关通过现有会话 API 创建通话，代表手机加入 LiveKit 房间。App 不需要 LiveKit SDK，也不接触 LiveKit 入房凭据或模型密钥。

## 1. 连接和鉴权

| 项目 | 约定 |
| --- | --- |
| WebSocket | `GET /v1/calls`，例如 `wss://gateway.example.com/v1/calls` |
| 鉴权 | 握手请求头 `Authorization: Bearer <测试令牌>`。缺少或无效时返回 HTTP `401`，升级前结束。 |
| 健康检查 | `GET /health` 返回 `{"status":"ok","mode":"mock"}` 或 `mode:"live"`。这里只检查网关进程。 |
| TLS | 真机使用 `wss://`，证书须被设备信任且匹配地址。开发机回环可用普通 `ws://`。 |
| 会话 | 一条连接对应一次通话。重连新建会话，上次上下文随通话释放。真实环境沿用现有服务的单活跃通话限制。 |
| 帧类型 | UTF-8 JSON 放在 WebSocket 文本帧，PCM 和 JPEG 放在二进制帧。无自定义 WebSocket 子协议。 |

调试令牌由网关进程配置，通过测试配置交给 App。每个环境用不同令牌，正式版本使用团队的鉴权系统签发凭据后再面向用户开放。不要把测试令牌放进源码、日志或 URL。

## 2. 通话状态和设备控制

1. 用户点「开始通话」后，App 申请麦克风权限，准备播放设备，建立 WebSocket。升级后 **10 秒内**发送 `start`。
2. 端收到 `connecting` 后显示接通中。真实网关接通上限为 **45 秒**。收到 `ready` 后开始发送麦克风帧。首帧前无需发送 `set_microphone`，麦克风默认打开，相机默认关闭。
3. 开相机时先申请系统权限，再发送 `set_camera`。收到 `camera_state` 为 `true` 后开始上传 JPEG。关相机或静音时立即停止对应数据，再发送开关指令。重开时等状态确认后再恢复发送。
4. 挂断、页面退出或连接终止时，App 本地立即停止采集和播放、清空队列、释放设备。连接还可用时发送 `end` 并等待 `ended`，等待上限建议 5 秒，随后关闭连接。即使 `end` 未到达，网关也会在检测到断线后回收房间。

### App 发送的 JSON

| 消息 | 样例 | 发送时机 |
| --- | --- | --- |
| 开始 | `{"type":"start","version":1}` | 升级成功后一次 |
| 结束 | `{"type":"end"}` | 可以在接通中或通话中发送 |
| 麦克风开关 | `{"type":"set_microphone","enabled":false}` | 通话就绪后 |
| 相机开关 | `{"type":"set_camera","enabled":true}` | 通话就绪后 |
| 播放开始 | `{"type":"playback_started","output_id":7}` | 该段语音真正开始播放后 |
| 播放完成 | `{"type":"playback_completed","output_id":7,"sequence":3}` | 收到 `audio_end` 且该段最后一帧实际播完后 |
| 播放失败 | `{"type":"playback_blocked","output_id":7}` | 播放设备不可用、序号缺失或队列无法继续播放时 |
| 应用层心跳 | `{"type":"ping","id":42}` | 可选，网关回 `{"type":"pong","id":42}` |

客户端 JSON 每条最多 4096 字节，`id`、`output_id` 和 `sequence` 都是无符号 32 位整数。浏览器式或 ArkTS WebSocket 库应能回复协议级 Ping。网关启用 15 秒心跳，同时支持上述应用层探活。App 可以在未收消息时自行探活，探活超时后按断线处理。

### 网关发送的 JSON

| 消息 | 样例 | App 行为 |
| --- | --- | --- |
| 接通中 | `{"type":"state","state":"connecting"}` | 显示接通中 |
| 已就绪 | `{"type":"state","state":"ready","call_id":"8b8c1f..."}` | 开始送音频，保存通话编号供排查。编号是字符串，按不透明值处理。 |
| 已结束 | `{"type":"state","state":"ended","reason":"client_end"}` | 结束页面中的通话，已释放设备时可忽略重复清理。`reason` 也可能为 `error` 或 `server_shutdown`。 |
| 麦克风状态 | `{"type":"microphone_state","enabled":false}` | 更新开关状态 |
| 相机状态 | `{"type":"camera_state","enabled":true}` | 更新开关状态，允许开始送 JPEG |
| 首帧状态 | `{"type":"camera_status","has_frame":true}` | 当前相机已收到有效画面。真实模式由 Worker 报告，模拟模式由网关报告。 |
| 转写 | `{"type":"transcript","role":"assistant","id":"text-1","text":"这是一个杯子。"}` | 按 `role` 和 `id` 更新对应文本，`role` 可为 `user` 或 `assistant` |
| 即时字幕 | `{"type":"caption","text":"正在识别画面…"}` | 可选状态，替换当前即时字幕 |
| 状态提示 | `{"type":"notice","text":"请展示要识别的物体。"}` | 可选状态，展示短提示 |
| 检测到说话 | `{"type":"speech","active":true}` | 可选状态，更新说话指示。播放清理由 `audio_clear` 控制。 |
| 语音段结束 | `{"type":"audio_end","output_id":7,"sequence":3}` | 此段共 3 个分片。等待音频实际播完再回执。 |
| 打断和清理 | `{"type":"audio_clear","through_id":7}` | 立即停止 `output_id <= 7` 的正在播放音频，清空这些 ID 的待播队列，并忽略之后收到的旧分片。 |
| 错误 | `{"type":"error","code":"busy","message":"已有通话正在进行或清理，请稍后重试。","fatal":true}` | 用 `code` 做分支，`message` 可直接提示。`fatal:true` 立即释放设备并结束。 |

`audio_clear` 后，已清理的输出不再发送 `playback_completed`。每个输出最多发送一次真实播放开始和一次完成回执。若失败则发送 `playback_blocked`，停止播放该段并清掉该段队列。App 可以忽略未来服务端新增的可选消息。正式通话中的转写、检测到说话等事件视模型和会话情况出现。

## 3. 二进制帧

| 方向 | 第 0 字节 | 后续内容 |
| --- | --- | --- |
| App → 网关，麦克风 | `0x01` | `byte[1..640]` 是 16 kHz、单声道、有符号 16 位小端 PCM。每帧 20 ms，320 个采样。整包 641 字节。 |
| App → 网关，相机 | `0x02` | 从 `byte[1]` 开始是一个完整 JPEG。宽高各不超过 640，最长边建议 640，单帧最多 160 KiB，建议 2–4 帧/秒。上传前按当前设备方向转正，无需 EXIF 或手动镜像。超过处理速率的帧可能被丢弃。 |
| 网关 → App，AI 语音 | `0x11` | `byte[1..4]` 是 `output_id`，`byte[5..8]` 是该段从 0 开始的 `sequence`。两字段均是无符号 32 位**大端**。从 `byte[9]` 开始是 24 kHz、单声道、有符号 16 位**小端** PCM，单片最多 4800 字节。 |

例如 `11 00 00 00 07 00 00 00 02 34 12 …` 表示语音段 7 的第 3 个分片，首个采样是 `0x1234`。音频帧无需 WAV 头、Base64 或额外的 JSON 封装。App 按序把 PCM 放到播放器队列，`audio_end.sequence` 等于分片总数。上行积压时优先保语音和新画面，避免把历史摄像头帧补发给模型。

## 4. 错误和重连

| 错误码 | 含义及处理 |
| --- | --- |
| `bad_message` | 文本、二进制帧或时序错误。核对 `ready`、设备开关和分片格式。大于 WebSocket 上限时连接也可能直接以 `1009` 关闭。 |
| `unsupported_version` | `start.version` 不是整数 `1`。检查客户端和网关版本。 |
| `start_timeout` | 等待 `start` 超过 10 秒。重新连接。 |
| `busy` | 真实服务已有通话或正在清理。等待后由用户重试。 |
| `unsupported_mode` | 上游未启用 `qwen_live_official`。由服务端检查配置。 |
| `connect_timeout`、`upstream_unavailable`、`upstream_closed` | 接通超时、上游不可达或通话中断。释放设备，用户可发起新通话。 |
| `slow_client` | 设备或网络来不及接收。清空播放队列并释放设备。 |
| `invalid_camera_frame` | 已丢弃无效 JPEG，`fatal:false`。继续语音，检查画面编码与尺寸。 |

App 也要处理 TLS 失败、WebSocket 直接关闭和没有收到末尾 JSON 的情况。这些情况下，设备释放和 UI 复位都在客户端本地完成。

## 5. 黄区模拟网关

模拟模式可在黄区开发机上独立启动，无需 LiveKit、Node 和模型账号。最小文件是 `src/livekit_vlm_agent/__init__.py`、`harmony_gateway.py`、`harmony_protocol.py`，保持它们在同一个包下。使用 Python 3.11 安装 `aiohttp` 和 `Pillow`，或直接使用本仓库已装好的环境。

```powershell
$env:PYTHONPATH = 'src'
$env:HARMONY_GATEWAY_TOKEN = '<填入至少 16 字符的测试令牌>'
python -m livekit_vlm_agent.harmony_gateway --mode mock --port 8095
```

开发机本机地址是 `ws://127.0.0.1:8095/v1/calls`。真机不能使用开发机的 `127.0.0.1`。存放模拟网关的机器需要一个手机能访问的地址，可用反向代理提供 WSS，或使用 `--host <监听地址> --certfile <证书文件> --keyfile <私钥文件>` 直接提供 TLS。App 的握手头保持一致。

| 测试场景 | 地址后缀及行为 |
| --- | --- |
| 正常 | `/v1/calls` 或 `?scenario=normal`。每 10 个有效麦克风帧触发固定转写和约 800 ms 的测试音。回复完成或播放失败回执后可继续下一轮。 |
| 打断 | `?scenario=interrupt`。收到第一段的 `playback_started` 后发送 `audio_clear` 并给出第二段，验证旧音频立即停止、第二段继续播放。 |
| 接通失败 | `?scenario=error`。开始后返回 `upstream_unavailable`，供 App 测试错误提示和释放设备。 |
| 相机 | 正常场景开相机后，上传一个合规 JPEG 可收到 `camera_status`。模拟模式不会识别画面。 |

## 6. 本机真实网关和远程迁移

先以官方 Harness 模式运行已有的 LiveKitAgent，再单独启动真实网关。继续复用现有进程和端口，网关监听独立端口。

```powershell
$env:PYTHONPATH = 'src'
$env:HARMONY_GATEWAY_TOKEN = '<填入真实联调环境的测试令牌>'
.\.venv\Scripts\python.exe -m livekit_vlm_agent.harmony_gateway --mode live --port 8096 `
  --api-url http://127.0.0.1:8000 --livekit-url ws://127.0.0.1:7880
```

`--api-url` 指向现有会话 API，`--livekit-url` 是**网关自己**连接 LiveKit 的地址。省略后者时，网关使用会话 API 返回的 LiveKit 地址。也可分别设置 `HARMONY_GATEWAY_API_URL` 和 `HARMONY_GATEWAY_LIVEKIT_URL`。迁移远程服务器时，更新这两个地址和对外 WSS 地址。反向代理把 `/v1/calls` 转到网关，保留 WebSocket Upgrade 和 Authorization，并设置足够长的连接超时。App 只改 WSS 地址和测试凭据，消息格式保持一致。

真机验收顺序是正常语音通话、播放回执、打断、打开相机并提问、挂断及直接断网释放设备。后端一侧也可用隔离 RTC 试验验证链路，它启动单独的 LiveKit、会话 API 和 Worker，使用模拟模型。运行时需要仓库的本地 Python、Node 和 LiveKit 测试环境。

```powershell
.\.venv\Scripts\python.exe scripts\verify_official_harness.py --harmony-gateway
```

## 7. 跨城浏览器试用

先执行 `npm.cmd run build --prefix web`。`scripts/start-remote-trial.ps1` 启动同一电脑上的独立 LiveKit、会话 API 和官方 Harness Worker，试用页默认监听 `127.0.0.1:8097`。用相同的 `-Port` 和 `-Stop` 停止这组服务。加 `-Mock` 可验证采集、字幕及播放，无模型调用，按 `Ctrl+C` 停止。脚本将新试用码写到 `.runtime/remote-trial/`，可用 `-ReuseInviteCode` 在同一端口重启时沿用。浏览器提交试用码后获得当前进程的 Cookie，WebSocket 和记录接口也检查同源请求。

浏览器使用手机全屏通话页。「更多功能」打开任务与对话，支持持续讲解、点选主体、分步教学和提醒。百炼模式启用通话内视觉记忆，挂断后清空。WebSocket 使用 `/v1/calls?client=browser`，只接受同源的试用会话；`official_event` 和 `video_event` 转发状态，`official_control` 转发任务操作。网关验证试用 Worker 和会话的事件签名后才转发。原生 App 继续使用 `/v1/calls` 的原版消息格式。

用支持 WebSocket 的 HTTPS 隧道只转发试用端口。以 Cloudflare Quick Tunnel 为例，执行 `cloudflared tunnel --url http://127.0.0.1:8097` 获取临时地址。会话 API、LiveKit 和报告读取接口留在本机。同行可自行勾选「记录本次测试」并标记问题，复盘仍由本机打开。用不同网络验证试用码、问答、任务控制、记忆及挂断清理。每次只支持一场通话。试用结束后停止隧道和网关。

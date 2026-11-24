# LiveKit + Qwen 实时视觉通话 Demo

从 [项目导航](PROJECT_GUIDE.md) 了解架构和模式。当前手机试测主线使用百炼和官方 Qwen Live Harness，启动入口为 `scripts/start-qwen-live-mobile.ps1`。

讲解响应优化（2026-11-06）：平时 1 FPS，启动讲解、点选和转镜头时短暂 4 FPS；点选复用观察连接，仅讲解观察接收加速帧，教学/辅助提醒保持原节奏。详见 [快速采样与响应验证](docs/guide-fast-response.md)。

最新讲解试测（2026-11-05）：增加主体区域验证、身份锁定、背景切换限制及手机点选纠正；镜头调整保留原主体线索，目标歧义时有限询问。详见 [主体选择、验证与手机测试](docs/guide-target-selection.md)。

最新手机试测（2026-11-05）：新增通话内统一需求管理，支持一个主讲解/教学场景与最多两项辅助需求共存；区分动作进度与语音送达，保留中断后的已完成步骤。增加分段时延记录，去掉指引前固定等待、加快有候选证据后的复核。开场行为不变。详见 [本轮改动、验证边界及手机测试](docs/latency-requirements.md)。

最新手机试测（2026-11-04）：增加主体讲解与持续分步教学。讲解聚焦主体、分短段、识别换主体后撤回旧播报；教学一次规划完整任务，按当前步骤完成条件推进，手机可暂停/继续、确认完成和重做。保留百炼、官方 Harness 与 LiveKit。见 [场景适配、成本和测试记录](docs/scene-guide-teaching.md)。

当前手机试测（2026-11-04）：已完善显式意图的单次事件、状态转变与重复出现提醒；可在手机查看/切换一次或每次规则，查看观察状态与用量。模型使用百炼，保留 Qwen Live Harness + LiveKit，本阶段不开发记忆。见 [实现、验证与手机测试步骤](docs/explicit-intent-intervention.md)。下文保留此前阶段记录。

独立观察实验（2026-11-02）：保留 M2 直接问答，增加可选的独立文字观察和手动管理的本地文本记忆。语音提醒和模型记忆回注仍待实现。见 [实验范围与验证记录](docs/direct-background-d2-integration-verification.md)。

M2 基线记录：桌面真实 Qwen 验证已识别狗、红色画面及换成蓝色后的新画面，摄像头控制和关闭后语音检查通过；手机正式媒体通话与 20 个物品人工验收待完成。见 [M2 实现与验证](docs/m2-verification.md) 和 [M0 轮次限制](docs/verification-report.md)。

## 启动通话

**H2 手机指导测试**已接入明确开始/提交、当前步骤和暂停/继续/结束，使用原 HTTPS 首页。先执行 `scripts/stop-local.ps1`，再执行 `scripts/start-harness-mobile.ps1`；操作与限制见 [H2 手机测试说明](docs/harness-h2-verification.md)。下面原启动方式继续使用 M2 自动问答。

鸿蒙手机本地摄像头、麦克风与 HTTPS 已由用户确认通过。M2 正式通话使用 `scripts/start-mobile.ps1`，手机打开启动器输出的 HTTPS 地址。管理员需先执行 `scripts/mobile-firewall.ps1 -Media` 放行仅限该手机的媒体端口。 [M2 手机验收](docs/m2-verification.md)

已安装依赖的当前工作区，在项目根目录执行：

```powershell
npm.cmd run build --prefix web
.\scripts\start-local.ps1
```

打开 **http://127.0.0.1:8000**，戴耳机后点击“加入通话”并允许麦克风。页面显示接通后即可说话；再点击“开启摄像头”可发布测试画面，支持前后镜头切换、关闭摄像头继续语音、挂断和回答文字。语音和开启摄像头后的抽帧会发送Qwen云端，仅拍摄非保密测试物品。

停止：启动窗口按Ctrl+C，或在另一个PowerShell窗口执行 `.\scripts\stop-local.ps1`。统一启动器只管理自己的进程，端口被占用时不会停止已有服务。服务日志在 `.runtime/logs/`，已脱敏。

## 已准备的环境

| 资源 | 版本 / 位置 |
| --- | --- |
| Python | 3.11.18，`.tools/python/` |
| uv | 0.13.6，`.tools/uv/uv.exe` |
| LiveKit Server | 1.13.11，`.tools/livekit/livekit-server.exe` |
| Node.js / npm | 现有系统版本 24.22.0 / 11.19.1 |
| LiveKit Agents / RTC / API | 1.8.9 / 1.1.21 / 1.2.2 |
| 浏览器 SDK / Vite | 2.23.0 / 8.3.5 |

Python 依赖由 `uv.lock` 固定，前端依赖由 `web/package-lock.json` 固定。uv 与 LiveKit 下载地址及 SHA256 记录在 `config/tool-versions.json`。工具、虚拟环境和缓存都位于项目目录；未修改系统 PATH 或 Python 注册表。

## 当前配置

- 提供商可选 `MODEL_PROVIDER=aliyun`（默认）或 `yibuapi`。一步 API 在本机 `.env` 填 `YIBUAPI_API_KEY`，使用 `wss://yibuapi.com/v1/realtime` 和独立密钥，不使用阿里云工作空间或密钥。模型仍由 `QWEN_MODEL` 选择，当前保留 `qwen3.8-omni-flash-realtime`。
- 一步 API [实时接口文档](https://yibuapi.apifox.cn/6777339m0)说明了连接地址和请求头；其 Qwen 实时音视频、音色和 Harness 协议兼容性仍须用本机密钥实测。填写后先执行 `scripts/probe-qwen.ps1`（只建立连接并验证配置，不发送媒体或生成回答），通过后再重启通话服务。切回阿里云只需设 `MODEL_PROVIDER=aliyun`。
- LiveKit 信令：`ws://127.0.0.1:7880`；媒体 TCP 7881、UDP 7882。
- Python API及构建后的测试页：`http://127.0.0.1:8000`；可选Vite开发页：5173。
- Qwen：北京，`qwen3.8-omni-flash-realtime`，`Tina` 音色。
- 轮次模式：`QWEN_TURN_DETECTION=semantic_vad_ec`，候选默认；仍需调校多句输入的成轮边界，可切换 `semantic_vad` 对照。
- 输入：16 kHz、16-bit、单声道 PCM；输出：24 kHz PCM。
- 视频配置：1 FPS、最大 640×480、发送前最大帧龄 1500 ms；保持宽高比、最新帧覆盖、JPEG异步编码。日志帧龄从电脑收到解码帧开始，不包含手机上行耗时。
- 本地 `.env` 已填写工作空间、Endpoint 和 Key；LiveKit 使用随机生成的本机凭据。
- 原始音视频录制关闭。不要把 `.env` 或 `config/livekit.local.yaml` 提交到版本库。

## 检查与启动

在项目根目录的 PowerShell 中运行：

```powershell
# 离线导入依赖与配置一致性检查，不调用模型
.\scripts\check-environment.ps1

# 前台启动本机 LiveKit，Ctrl+C 停止
.\scripts\start-livekit.ps1
```

LiveKit 运行时，在另一个 PowerShell 窗口中验证房间 API 和合成音频传输：

```powershell
.\.venv\Scripts\python.exe .\scripts\smoke_livekit.py
```

该脚本只连接本机，创建随机临时房间，连接两个 RTC 参与者，发送合成音频并验证解码，最后清理连接和房间。它不访问物理音频设备或 Qwen。

已获用户允许的短时 Qwen 连接/配置验证：

```powershell
.\scripts\probe-qwen.ps1
```

该脚本读取本地 Key，等待 `session.created`，验证手动轮次的 PCM/音色配置，再验证当前轮次模式配置，随后断开。最长 40 秒，无自动重试，不发送音视频、不创建回答。仅收到配置确认不代表已验证实际语音、图像或打断效果。脱敏结果保存在 `.runtime/qwen-probe.json`。

真实媒体验证使用 `scripts/prepare-m0-fixtures.ps1` 和 `scripts/verify-m0.ps1`，逐项命令与费用边界见 [M0验证报告](docs/verification-report.md)。离线回归：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 在同类 Windows x64 环境复现

前提：已安装符合 `web/package.json` 的 Node.js 24 LTS，且可访问官方 GitHub、PyPI、npm 下载源。

```powershell
.\scripts\install-tools.ps1
.\scripts\sync-dependencies.ps1
.\scripts\check-environment.ps1
```

`install-tools.ps1` 下载固定版本、校验 SHA256、安装项目内 Python 并初始化随机 LiveKit 凭据。初始化会保留已有 `.env` 和本地 Server 配置。`sync-dependencies.ps1` 使用锁定文件；前端安装禁用生命周期脚本。

新的环境需要在 `.env` 填入 `QWEN_API_KEY` 和 `QWEN_WORKSPACE_ID`。`QWEN_REALTIME_URL` 留空时可自动从地域和工作空间 ID 推导。北京地址格式：

```text
wss://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime
```

工作空间 ID 是百炼中管理模型、密钥和权限的空间标识，不是应用 ID；默认空间也有独立 ID。可在控制台选择北京后查看当前工作空间信息，或在“工作空间管理”中查找。

## 下一步

2026-11-04 官方 Harness 实验已切回原百炼配置，手机启动入口为 `scripts/start-qwen-live-mobile.ps1`。原密钥／工作空间与模型保留；百炼连接和会话配置检查通过，手机语音、画面及观察提醒待实测。

2026-11-03 已建立 `codex/qwen-live-harness-adapter` 分支，开始以官方 `qwen-live-harness@1.0.0` 核心适配现有手机页面、LiveKit 与一步 API，见 [官方核心适配实验](docs/qwen-live-harness-adaptation.md)。新增入口 `scripts/run_local.py --qwen-live --mobile`，可追加 `--official-proactive` 开放官方观察／提醒工具。真实一步 API 单次语音生成和本地完整 RTC／网页播放通过；真实手机观察提醒及自动记忆尚未验收。原入口保留，现有手机服务没有自动切换。

2026-11-02 新主线为 [M2 直接对话 + 后台观察 + 记忆](docs/m2-background-observation-memory-plan.md)。首轮交付了 D0 探针和 D1 可测试骨架，真实协议门槛仍待验证；见 [D0 能力矩阵](docs/direct-background-d0-verification.md) 和 [D1 验证](docs/direct-background-d1-verification.md)。

```powershell
# 离线检查单，不连接供应商
.\.venv\Scripts\python.exe scripts/probe_live_background.py
# 显式启动 D1 直接问答骨架（通话使用当前配置的云端提供商）
.\.venv\Scripts\python.exe scripts/run_local.py --direct-background
# 手机沿用已有 HTTPS profile
.\.venv\Scripts\python.exe scripts/run_local.py --direct-background --mobile
```

`direct_background` 保留 M2 自动语音问答；后台提醒、自然语音任务和记忆回注均尚未接通，页面及会话 API 明确显示能力状态。原默认启动仍为 M2（若 `.env` 显式设了 `HARNESS_MODE` 则按配置），原 `--harness` 仍为 H2；新旧模式只能在新通话选择，不会自动切换。需要切换已运行服务时先按原入口停止，再启动所选模式。本轮没有重启现有手机服务或修改 `.env`。

后续已增加独立观察任务/Monitor 组件和可操作的本地文本记忆面板，见 [D2 前置实现与本地记忆](docs/direct-background-d2-verification.md)。新模式通话中可登记目标（当前暂停待协议验证），手动保存/纠正/检索/禁用/清空事实；显式选择库 ID 后可恢复该本地库的事实。默认匿名通话隔离。记忆没有回注模型，Monitor 尚未接入运行时，无额外整理模型调用。离线页面检查：`node web/tests/direct-local.mjs`。

独立文字观察须同时使用 `scripts/run_local.py --direct-background --observe-text --mobile` 和一步 API 的 `qwen3.8-omni-flash-realtime` 配置。后台观察默认关闭，开启后还须在页面点击“开始文字观察”才会发起后台模型调用，每通话默认最多十次后台尝试。钥匙图组件验证通过，RTC 整链路尚未通过，语音提醒和模型记忆回注均未开放。 [D2 接线与真实媒体验证](docs/direct-background-d2-integration-verification.md)

新增架构方案：[实时多模态模型 + Harness 构建方案](docs/harness-implementation-plan.md)。H1 离线骨架和 H2 明确提交的真实通话已实现；语音续答采用首次配置即保持语音模态。手机可测试问答、指导与用户确认推进，自动视觉推进和免按键交互未开放。见 [H2 验证与手机测试](docs/harness-h2-verification.md)；[H0/H1 记录](docs/harness-h1-verification.md) 保留前一阶段结果。

2026-11-02 新增连续物品介绍原型：用 `scripts/start-harness-mobile.ps1` 启动，首次提交“逐个介绍我展示的物品”，随后对准、停留和换物品即可触发观察与简短讲解。包括本地移动过滤、两次观察确认、已讲内容去重和暂停/继续/结束。启动需求和插话仍使用语音提交按钮。实现边界及测试记录见 [连续物品介绍验证](docs/harness-showcase-verification.md)。

手机“确认开始后一直不讲”的问题已针对性调整：分离布尔确认、请求纯文本内部判断、容忍短暂晃动和亮度变化，并显示当前观察状态。验证范围与手机复测见 [稳定性修正](docs/harness-showcase-stability-fix.md)。

根据后续手机反馈，画面过滤已进一步按短时运动趋势区分“持续扫动”和“围绕物品停留”，停留时允许自然晃动，不再要求画面相对旧锚点保持静止。验证见上述记录末尾。

等待时间优化新增提前观察、有条件快速路径和到首音测量，保留完整确认与回退开关。当前完整模拟仍未稳定达到 1–3 秒，不能宣称目标已完成。实测、失败和手机网络限制见 [等待时间优化验证](docs/harness-showcase-latency-verification.md)。

离线演示（不连接云端）：

```powershell
.\.venv\Scripts\python.exe scripts/demo_harness.py
```

完成M2手机真实通话、换物和20个物品的人工验收。已保留真实验证中的云端内容检查/通用错误记录，不能据短时成功承诺稳定性。短句与多句轮次、真人插话尾音及回声在M3继续调校。

详见 [开发方案](docs/development-plan.md)、[M1验收记录](docs/m1-verification.md)、[资源准备验证记录](docs/environment-setup.md) 和 [协议验证记录](docs/protocol-findings.md)。

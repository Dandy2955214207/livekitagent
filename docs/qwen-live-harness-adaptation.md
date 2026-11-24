# Qwen Live Harness 适配实验

2026-11-04 后续更新：观察证据协议、重复提醒状态、手机规则切换、预算及测试记录见 [显式意图主动介入](explicit-intent-intervention.md)。当前默认预算为 40 次总生成 / 20 次后台生成 / 180 秒，模型使用百炼，记忆暂停开发。以下为此前接入里程碑记录。

日期：2026-11-03（Asia/Shanghai）。分支：`codex/qwen-live-harness-adapter`。

当前交付为第一阶段：**官方核心已实际接入，真实一步 API 语音生成通过，本地模拟提供商的完整 LiveKit／浏览器链路通过。** 尚未完成真实一步 API + 手机实物观察提醒的整体验收，自动记忆保持关闭。

## 官方核心与本项目的边界

运行的是 npm 官方发布包 `qwen-live-harness@1.0.0`，不是将原 Python Harness 改名。依赖及完整性固定在 `harness/qwen-live/package-lock.json`，来源记录在 `harness/qwen-live/upstream.json`。官方构建的源内容指纹为 `ddfc13c2796f31181c731c49aaba510b6bd87aac7b0fd2357fff69f10693e0a8`。本轮另下载的 GitHub main 仅供阅读，没有替换运行包。没有修改官方包文件。

直接使用官方 `LiveSession`、Realtime、工具处理、`BackendRegistry([])`、`ProactiveScheduler`、Monitor 和通知流程。没有接入后台编程代理，不读取官方个人配置，不启动 Electron Host，不调用终端／文件操作工具。官方普通回答采用 semantic VAD 检测轮次、应用显式创建回答，不经过原 H2 DECIDE/SPEAK。

适配层为：

```text
手机页面 ── LiveKit 麦克风／摄像头 ── Python Worker
                                       │ 私有 stdin/stdout
                                       ▼
                               Node LiveKitHost 适配
                                       │
                             官方 LiveSession 核心
                          前台对话／Proactive／Monitor
                                       │
                          本机认证 WebSocket 转接层
                                       │ 固定一步 API 地址
                                       ▼
                         qwen3.8-omni-flash-realtime

官方音频 → LiveKit 可靠数据包 → 手机 Web Audio 渲染
手机匹配播放回执 → LiveKit → LiveKitHost → 官方调度器
```

手机原有加入、麦克风、摄像头、切换镜头、转写显示和挂断入口保留。**此实验模式的下行音频暂用 LiveKit 可靠数据通道携带 PCM，而非原下行音频轨道**，以便在浏览器渲染端关联具体输出。它还需要真机网络下的延迟／拥塞验证；现有 M2／H2 的音频轨道实现保留。

转接层只监听随机回环端口并使用每通话随机 token。2026-11-04 起，实际云端地址按选定提供商严格校验：百炼使用原工作空间地址及百炼密钥；一步 API 保留为隔离的可选提供商。真实密钥仅从 Python 私有 stdin 传入，不写新配置文件、不进入手机或命令行，也不放入官方个人目录。

官方核心的内部模块尚非稳定公开 SDK。本轮明确锁定发行版及 build 指纹，升级需重新验收。原许可证及第三方 notices 保留在依赖分发包中。

## 生命周期和成本

- 一通话拥有一个 Node 子进程及本机转接端口，挂断关闭自建子进程和所有自建模型连接。
- 默认不开 Proactive，无观察任务就没有 Monitor 生成；`--official-proactive` 显式开启官方自然语言任务工具。
- 默认整通话最多 20 次生成、后台文字子连接共享 10 次上限、最多 180 秒；连接同时最多 4 条、累计最多 8 次。后台文字统计包括 Monitor，也覆盖其他纯文字辅助连接。
- 后台生成达到上限只停止该后台连接，前台保留；官方按其失败规则收束任务。整通话生成预算或时限达到上限则结束实验通话。
- 每次主连接生成输出最多 512 tokens，文字子连接最多 256 tokens；不增加应用级重试。官方自身的恢复和兜底也共用上述总预算。
- 真正的新帧通过 `feedImage` 输入官方 Monitor，不轮询缓存图像制造新证据。关摄像头清理前台排队图像和官方观察缓冲。
- `callId + epoch + outputId + sequence` 关联输出。只有浏览器 AudioContext 运行、匹配结束标记且所有源已结束才回传完成。中断／挂断／暂停播放不确认完成，旧输出回执不能推进新输出。
- 浏览器渲染完成仍不能证明设备外放音量或人实际听见，页面使用“浏览器已确认播放完成”。
- 原始音视频不录制；日志保留调用次数、返回用量和有限错误码。官方会话完整日志／调试媒体归档没有开启。
- 官方模式已接入通话内视觉记忆，后台使用同一配置的实时模型进行有界多帧观察，历史问题经 `recall_call_history` 查询本通话记录。挂断释放记录。默认开启，可由 `QWEN_LIVE_MEMORY_ENABLED=false` 关闭。 [实现与验证](official-memory-design.md)

## 启动和验证

2026-11-04 已按用户要求切回原百炼：`.env` 使用 `MODEL_PROVIDER=aliyun`，保留原 `QWEN_API_KEY`、`QWEN_WORKSPACE_ID`、区域和模型配置。适配层按提供商传递工作空间地址及独立密钥；百炼不附带一步 API 的认证头。无需修改现有手机 TLS profile。

```powershell
npm.cmd ci --prefix harness/qwen-live --ignore-scripts
npm.cmd run check --prefix harness/qwen-live
npm.cmd run build --prefix web

# 官方核心直接问答，默认不观察
.\.venv\Scripts\python.exe scripts/run_local.py --qwen-live --mobile
# 显式开放官方观察／提醒工具
.\.venv\Scripts\python.exe scripts/run_local.py --qwen-live --official-proactive --mobile
```

已有手机服务运行时，启动器拒绝占用端口，不会接管或自动重启旧服务。本开发轮没有切换用户正在运行的模式。

```powershell
# 官方核心 + 本机假提供商；不调用云端模型
npm.cmd test --prefix harness/qwen-live
node --test web/tests/official-audio.test.mjs
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node web/tests/direct-local.mjs
.\.venv\Scripts\python.exe scripts/verify_official_harness.py

# 默认只显示检查单；加 --live 才连接真实提供商
.\.venv\Scripts\python.exe scripts/probe_official_harness.py
```

## 本轮证据

1. 官方核心适配七项本地测试通过：真实官方 Realtime 配置／轮次、工具创建观察及 UI 停止、转接认证与生成预算、后台预算不阻断前台、旧输出回执隔离及多字节任务摘要包大小。
2. 全部 179 项 Python 回归通过，包括实际 Node 子进程／官方核心／本机假提供商、子进程清理、输入和控制队列边界。前端播放器三项测试覆盖浏览器排空、暂停、旧通话、打断和序列错误；原记忆面板离线检查、前端生产构建及 `git diff --check` 通过。保留原有构建包大于 500 kB 提示。
3. 本地完整 RTC 首轮音频和回执通过，模拟摄像头无持续帧导致后续图像等待超时；失败保留在 `.runtime/official-harness-rtc/96f3e3acc7944cf4a67fbedec8163beb/`。修正测试夹具持续重绘后，全链路通过于 `.runtime/official-harness-rtc/05e54da200bf400ea9bd0707d9f1d0ad/`。使用独立 LiveKit 7890、API 8002、Worker 8083、假凭据和浏览器合成媒体；没有云端调用或物理设备。
4. 一步 API 首次尝试就绪前失败，未取得 usage，根因未知；保留 `.runtime/official-harness-probes/1c31f5337dce419198afd860d6a6ff0b.json`。补诊断后的限额复测通过：一条连接、一次生成、用户及助手转写各一份、207360 字节音频；输入 5885、输出 84，合计 **5969 reported tokens**。证据 `.runtime/official-harness-probes/1fb9742ec9434f5c839a7fbcecaf47ae.json`。该探针没有浏览器，不伪造播放回执，也没有创建观察任务。

最终预算／旧回执隔离修正后的本地完整 RTC 复测也通过，记录于 `.runtime/official-harness-rtc/14a0055f36c94a249c9440c8854ad4f4/`，自建服务已退出。付费用量不因随后本地测试而增加。机器可读摘要见同目录文档 `qwen-live-harness-adaptation-verification.json`。

已返回的 5969 tokens 是可核对下限，首轮失败／连接输入可能另计费用，不视为完整账单，不估算人民币费用。本轮停止追加付费调用。所有测试自己的服务退出，未改动 `.env`、手机配置、证书、防火墙或原服务记录。

## 下一阶段

2026-11-04 提供商切换验证：179 项 Python 回归、9 项官方核心适配测试、3 项播放器测试、前端构建通过。百炼真实连接／会话配置探针通过，零输入、零生成；记录 `.runtime/official-harness-probes/c41f042defc24dd8b1ea64c6ab82d8b8.json`。该结果只证明连接和配置成功，真实问答与观察提醒交由手机测试。便捷入口 `scripts/start-qwen-live-mobile.ps1` 使用官方核心并开启 Proactive。

先用小预算验收真实一步 API 下“自然语音创建单次视觉任务 → 实物正负例 → 手机主动播报 → 用户插话／取消 → 匹配浏览器播放回执”。同步检查可靠数据通道播放的时延、拥塞和断线行为。记忆阶段优先验收同一次通话中出现过的物品、事件和顺序的语音召回。 [官方模式记忆设计](official-memory-design.md)

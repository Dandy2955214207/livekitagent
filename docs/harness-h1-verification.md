# Harness H0 / H1 实现与验证

日期：2026-10-30（Asia/Shanghai）。本轮完成 H1 离线控制骨架、H0 可执行探针及四次固定样本云端生成。**尚未完成 H2 通话接入；持续讲解和操作指导目前是离线状态机能力。**

## Git 基线

项目初始化为本地 `main` 仓库，首次提交 `7ed00e1` 保存原有 M2 基线。提交使用 `Codex <codex@localhost>` 标识自动开发提交，未设置用户的全局 Git 身份。命令按仓库路径使用 `-c safe.directory=...` 适配沙箱的不同文件所有者，未修改全局安全目录。

`.env`、本地 LiveKit 凭据、`.runtime/`、工具、虚拟环境、依赖和构建产物均由原有 `.gitignore` 排除；没有创建远端或推送。

## H1 的实际能力

- `harness/types.py`：严格、不可变的决定 schema 和上下文；动作必填约束，拒绝未知字段，计划最多五步，文本独立限长。
- `state.py`、`context.py`：任务和输入/生成/播放分别存储；上下文冻结本轮任务、当前有效帧、输入来源 ID 和有界历史。当前只保留最新帧句柄，不保存原始媒体。
- `controller.py`：唯一状态拥有者。回调通过 `post` 入有界优先队列，`drain` 同步执行转换、返回 I/O 命令，无耗时 I/O 阻塞状态机。停止/插话优先于观察，tick 合并，frame 最新覆盖。
- `fake.py`：假时钟和离线后端，执行取消后才接受下一次生成，断言活动生成最多一个。
- 用户确认的三步指导：DECIDE → 接受工具结果 → SPEAK → 对应段播放完成 → 等待操作 → 新的确认决定 → 下一步。`好了` 的判断由模型决定契约提供，控制器不会用关键词自动认定完成。
- 三段讲解：每段播放和生成均完成后，才安排下一段 DECIDE；有限提纲播完后停止。未播完的段记录为中断，恢复时保留当前步骤。
- 指导检查：`incomplete/unknown` 可安静等待；完成必须绑定当前步骤和本轮证据，区分 `visual/user_reported/mixed`。不接受过期帧、假证据、跳步、提前完成或只靠时间推进。
- 插话、镜头变化、暂停和结束使在途旧决定失效。重复 call_id、重复回调、冲突工具、取消/截断响应不能重复推进。坏决定最多纠正一次，之后暂停。
- 默认视觉检查关闭；离线测试显式开启。间隔至少五秒；所有生成共用每分钟 20 次额度，观察预留两个交互名额；等待无进展 120 秒暂停；生成/播放超时分别为 30/60 秒。时间均可注入，生产执行器仍需按具体段长调整。

`FunctionCallCollector` 仅在完整 `response.output_item.done` 和所属 Response 成功终态后交付整批函数；参数增量不执行，多调用由控制器整体拒绝。`qwen_client.configure` 新增显式 tools/modalities/budget 参数，原有默认语音配置保持兼容。`qwen_realtime.py` 没有开启 Function Calling 能力声明。

## H0 云端结果

使用原有北京 Qwen 配置和 M0 的固定 greeting 音频、红/蓝图片。没有打开物理摄像头、麦克风、扬声器或 LiveKit 服务。两个探针各最多两次生成，分别执行一次，无自动重试。脱敏事件和用量摘要见 [harness-h0-results.json](harness-h0-results.json)；完整脱敏运行记录仍在被 Git 排除的 `.runtime/harness-probes/`。

| 门槛 | 本次观察 | 状态与边界 |
| --- | --- | --- |
| P1 手动提交与生成 | 提交后 500 ms 未自动生成；显式请求后完成 | 固定样本通过；不是 SDK 最终实时生成权验收 |
| P2 内部函数 | 完整函数项 → `response.done(completed)` → 回填 → 对应 `conversation.item.created` | 一次固定 `answer` 决定通过；实际任务规划质量未测 |
| P3 安静决定后发声 | DECIDE 零音频；切到 text+audio 且清空 tools 后配置确认成功，但续答只有文字，零音频 | **失败**，不能开启正式两阶段语音闭环 |
| P4 沉默后新画面 | 同连接先提交红图再蓝图，各使用 0.5 秒 `transport_silence`，两次完整决定分别识别红/蓝 | 两个固定场景通过；未使用新问题或重放旧问题，不能代替真实动作判断 |
| P5 延迟续答 | 工具回填后等一秒，无自动生成；显式续答得到完成的文字响应 | 延迟文字续答已观察；语音仍受 P3 阻塞 |
| P6 播放与取消 | 锁定 SDK 存在 `AudioOutput.wait_for_playout` 和 `playback_finished`，事件含 interrupted | 只检查源码，未接线/实测；保持 unknown |
| P7 独占生成与自然输入 | 未接入 AgentSession 手动轮次/本地端点 | unknown |
| P8 图片关联与历史 | 固定探针已关联 A/B；未验证 RTC 帧 ID 绑定或历史多图 | 正式能力 unknown |

控制探针整体返回失败，原始报告保留。原版脚本在语音断言失败前没有写入 P3/P5 结论，摘要的 `interpretation` 是对事件和用量的分析，不改写原始报告。第一次探针日志缺少 call_id 和配置字段；已补充后续日志，仅保留标识、字段名、模态、预算、工具名称，省略参数内容和工具输出。未再次付费重跑。

官方字段已核对：[客户端事件](https://help.aliyun.com/zh/model-studio/client-events)、[Function Calling](https://help.aliyun.com/zh/model-studio/qwen-function-calling)。配置确认不能证明输出语音已生效，P3 的根因还需有界对照验证。

## 复现

以下三项均离线，不访问 Qwen：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/demo_harness.py
.\.venv\Scripts\python.exe scripts/probe_harness.py
```

本轮 **66 项 Python 回归通过**，其中新增 35 项（26 项控制器、5 项函数事件、2 项配置/脱敏、2 项探针回归），原有 31 项全部通过。前端 `npm.cmd run build --prefix web` 通过，保留原有大包体积提示。演示输出依次为 s1/s2/s3 等待操作，然后 completed，完成依据全部为 user_reported。测试包含三段续讲、生成与播放乱序、插话取消、过期画面、冲突/截断工具、有界纠正、预算、超时和挂断清理；模拟结果不构成真实模型或手机验收。

短时付费探针需显式加 `--live`，每次 90 秒上限、最多两次生成、不重试，报告使用唯一文件名保留失败记录：

```powershell
.\.venv\Scripts\python.exe scripts/probe_harness.py --live --case control
.\.venv\Scripts\python.exe scripts/probe_harness.py --live --case vision
```

## 后续接入约束

1. 优先定位 P3：分别比较会话级切模态和供应商明确支持的生成选项，核对 session.updated 的返回值。候选是固定 audio 模态配合 DECIDE 本地音频/字幕门禁；仍使用同一 Qwen，先验证后选择。
2. H2 执行器按命令顺序完成配置回执、媒体提交、工具回填；取消需等回执和旧输出清理，再执行新生成。所有 Response/工具/播放事件由程序绑定 request_id，不采信模型回传标识。H1 命令返回不代表远端取消已经完成。
3. 独立实现 P6 的真实段落关联和取消尾音测量。SDK 的队列播放结束以 `server_playout` 标记，不能当作浏览器已听到或用户已理解；无法关联时暂停。
4. 将明确提交接入受控路径，保留原有自动通话作为对照。H3/H4 再验收真实三段/三步、静默未完成/遮挡、插话、步骤修订和结束。H5 才接自然端点及手机验证。

本轮没有声称实现真实免按键指导、真实物品自动完成判断、连续语音或计划动态修订。关键帧历史、多图引用、远端裁剪和跨连接恢复继续保持待验证。

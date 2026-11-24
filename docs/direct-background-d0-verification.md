# D0 协议探针与能力矩阵

日期：2026-11-02（Asia/Shanghai）。状态：探针、离线契约已实现；真实协议门槛未验收。

基线 HEAD：`b915ed18398d23779303eab2a06d31b41a793ee3`，本轮为未提交工作区增量。开始时已有 `.env.example`、README、旧实施计划、提供商配置/客户端/探针和测试修改，全部保留。已阅读 M2、H2、自动输入结果（含任务后问答超时）、合并回执及协议记录；不将这些旧结果当作新提供商的通过证据。仓库及上级路径未发现 AGENTS.md。

当前配置：`yibuapi` / `yibuapi_realtime`（`wss://yibuapi.com/v1/realtime`）/ `qwen3.8-omni-flash-realtime` / `semantic_vad_ec`。不记录密钥和工作空间标识，不更换提供商。

| 门槛 | 当前状态 | 本轮证据 / 待完成 |
| --- | --- | --- |
| P0-A 双连接 | passed（固定探针） | 追加实测前台两次语音完成；Monitor 独立红蓝判断，关闭 Monitor 后前台继续回答；并发限额/长会话待验 |
| P0-B 图像观察 | passed（固定探针） | text Monitor 红→蓝分别 matched/not_matched，无音频/语音事件；两次转写完成均为空文本 |
| P0-C 结构化输出 | passed（固定探针） | 两次真实结果通过严格 JSON/schema 解析；negative 未混为 unknown；真实模糊图 unknown 与纠正分支待验 |
| P0-D 注入发声 | unknown（本路径执行失败） | 全量配置及 instructions 快照均确认；空输入新连接 response.create 收到供应商 error，无响应生成；原因和有历史连接路径未验证 |
| P0-E 自动轮次竞争 | unknown | 尚未实现真实注入/生成/播放三阶段竞争探针，禁止启用主动语音 |
| P0-F 任务工具 | unknown | 保留 D3 门槛，本轮无自然语言任务执行器 |
| P0-G 记忆回注 | unknown | 实现新连接纠正快照 + 固定 recall 音频探针；持久库、删除、指令隔离与恢复确认待验 |
| P0-H 预算与提供商 | unknown | 已有连接独立用量及事件输出、全局生成计数、无自动重试；真实配额、单价和费用待验 |

最初真实尝试命令：`.venv/Scripts/python.exe scripts/probe_live_background.py --live`。在打开第一个连接时得到 `PermissionError`；媒体发送 0、生成 0、供应商 usage 无返回。原始脱敏报告保留于 `.runtime/direct-background-probes/b7acae3497dd45558c16e532c31d7fc4.json`。旧版探针将活动 P0-A 标为 failed，表示该次执行失败，不能解释为供应商双连接不支持；能力矩阵保持 unknown。新探针已分开记录 execution 与能力 status。

首次提权重试在进程创建前被自动审批拒绝：外部一步 API 目的地的固定媒体发送及可能费用缺少明确授权。用户随后明确回复“允许，但要注意成本控制”，已解除该授权阻断。后续始终使用同一目的地和固定媒体范围。

探针默认离线；`--live` 才使用当前凭据。`--case dual|event|memory|all` 可分项。all 计划六轮生成、最多两个同时打开的连接、整体 150 秒，异常即停止；不使用设备、不播放、不保存输出音频、无自动纠正/重试。超过生成预算立即使接收器失败并关闭；意外供应商生成也计入总数。仅保留固定测试文本、关联、事件类型、耗时和 usage。执行前验证 WAV 格式及十秒输入上限，记录 PCM SHA256。

协议依据复核了 [阿里云客户端事件](https://help.aliyun.com/zh/model-studio/client-events) 的 `session.update`、嵌套音频、modalities、instructions、turn_detection。这些说明不能证明一步 API 兼容。当前未新增未经验证的 VAD 字段，没有伪造用户消息或工具 call_id。运行时仍沿用 M2 自动响应；事件注入、调度及记忆回注路线须待 P0 实测后决定。

此前离线回归：149 项 Python 测试通过（原 141 项 + 新增 8 项）；前端构建通过（保留既有大包提示）。固定输入包括小型编码帧、严格 JSON、模拟确认/超时。没有启动/停止现有服务或修改手机 profile、证书、防火墙及 `.env`。结构化结果见同名 JSON。

## 授权后的成本受控实测

2026-11-02 17:24（Asia/Shanghai）执行同一 all 探针。提权账号与 sandbox Git 所有者不同，第一次在读取 HEAD 时退出，未连接网络。已用命令级 `git -c safe.directory=<当前仓库>` 修正元数据读取，不修改全局配置；随后进行了一次真实网络执行。

真实执行报告：`.runtime/direct-background-probes/87833ec9825e453888e6c61dcd6a5b48.json`。两个连接均收到 `session.created/session.updated`。Monitor 收到 500ms 分块静音及一张 320×240 红色 JPEG，显式生成完成，输出 `matched`、摘要“红色”、纯红背景依据；无输出音频。前台同时上传约 2.68 秒固定合成音频，收到 speech_started，在生成回答之前被并发检查失败取消。

失败点为原探针把任意 `conversation.item.input_audio_transcription.completed` 都当作静音泄漏。Monitor 没有 speech_started/stopped，但有一次 transcription.completed。脱敏 trace 未保存 transcript 或长度，因此无法确定是否仅为空文本完成回执；保留原始 failed 记录，不能宣称“已确认空转写”或“供应商静音泄漏”。从已保存文本独立严格解析，红图 JSON 有效，但只有一个正例，不构成 P0-C 通过。

成本控制：原计划六轮、150 秒、最多双连接，预定输出上限合计 2048 tokens（两个 Monitor 各 512，四个前台各 256）。本次失败即停，实际只生成一次，无付费重跑。Monitor 的供应商 usage 为输入 **166**（text 79、audio 7、video 80）、输出 **28**、合计 **194 tokens**。前台未返回 usage，已上传输入可能仍计费；194 是已报告用量，不是完整账单或费用上限。没有可验证的本账户单价，因此不估算人民币费用。事件播报、记忆、蓝图及后续问答均未执行。两个连接关闭，未遗留后台任务。

随后仅做离线修正：按转写内容区分空文本、非空文本和内容未知，记录计数而不保存转写；保留真实 speech_started/stopped 拒绝条件。新增两个用例，四项探针专项测试全部通过。另增加安全的应用错误码采集，避免 ExceptionGroup 只留下类型而无法定位，同时不持久化供应商错误消息。修正后尚未付费复验，不因离线修改升级能力。

下一步：已有固定媒体授权有效；需先用新计数确认 Monitor 的转写行为，再做蓝图、前台回答和关闭隔离。避免重复全量付费回归，按剩余未知项安排小范围探针。P0-E 的来源关联和 RTC 三阶段竞争探针仍待实现；D0 真实门槛未通过前，不进入 D2 主动提醒装配。

## 明确追加授权后的第二次实测

用户明确回复“允许追加测试”后，2026-11-02 18:19（Asia/Shanghai）执行一次现有 all 探针，没有更换配置或提供商。原始报告保留于 `.runtime/direct-background-probes/ae8e004005264b7e860c4178e33cf57d.json`，精简结构化结果为 [追加测试结果](direct-background-live-followup-results.json)。本次整体仍为 failed，失败发生前的通过项目单独保留。

- P0-A 固定双连接样例通过：前台在 Monitor 独立运行时完成一次合成语音问答；Monitor 关闭后，前台再次回答“语音测试成功”。两次语音分别返回 72960 / 69120 字节 PCM，未经过 H2 的决定工具。这不证明实际并发限额、长会话可靠性或 RTC 手机播放。
- P0-B 红蓝固定图像样例通过：红图返回 matched、红色及纯红背景依据；同一 Monitor 随后蓝图返回 not_matched、蓝色及与红色不符的依据。两次输出音频均为零。每次各有一个 transcription.completed，内容均为已知空字符串；speech_started/stopped、非空转写和未知转写计数均为零。前次无法判定的失败记录保留，本次才确认空回执可出现。
- P0-C 固定结构化样例通过：两次真实 JSON 均严格解析成功，negative 没有混为 unknown。真实模糊图返回 unknown、截断及有界纠正分支仍待验，不扩展为任意条件识别通过。
- P0-D 当前事件路径执行失败：新前台连接的完整配置、仅 instructions 快照更新均收到 session.updated，快照后没有自动生成。显式 response.create 后供应商返回 error，没有 response.created 或音频。脱敏 trace 的 error_code 为 null，仅保留 QwenProtocolError；因此根因未知。该连接没有发送过媒体/用户输入，不能排除“空输入新连接”这一前置条件的影响，也不能据此断言有真实对话历史的连接不支持回注。
- P0-G 记忆探针因前项失败未执行；P0-E/F/H 的完整门槛保持 unknown。没有为了绕过错误增加播报连接、伪造函数 call_id 或修改普通问答调度。

实际完成 **4 次生成**，在六轮/150 秒范围内遇错即停，付费重跑 **0 次**。已返回 usage 合计输入 **1997**、输出 **101**、总计 **2098 tokens**：Monitor 为 512（191+321），前台问答为 1586（767+819）。2048 是原定六轮的输出 token 上限，并非输入加输出的总额；本次输出 101，未超该预算。报错连接没有返回 usage，完整计费仍以账户账单为准，未估算人民币费用。加上前次的 194，累计已报告 2292 tokens，仍不包含可能未报告的输入或错误请求费用。

普通问答两次固定音频结束到供应商音频首包约 3906 / 3343 ms，仅为原始样本；另记录 response.created 到首包 15 / 2156 ms。这些不是浏览器/手机解码首音，且只有两个样本，不报告 p50/p95，不作为 M2 非退化验收。

此次三个连接（前台、Monitor、事件探针前台）全部关闭。未执行记忆探针连接，未启动服务、读取物理设备、保存原始媒体或重启原手机入口。运行时主动语音和记忆回注保持关闭。下一步先离线完善供应商错误的安全诊断，再设计仅覆盖事件响应前置条件的最小探针；不重复已通过的红蓝和问答样例来增加成功次数。

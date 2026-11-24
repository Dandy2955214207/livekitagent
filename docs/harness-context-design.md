# Harness 上下文构建设计：多轮视觉问答

日期：2026-10-29。状态：设计草案，尚未实现或做新增云端验证。

2026-10-30 补充：[实时多模态模型 + Harness 构建方案](harness-implementation-plan.md) 已扩展持续讲解、操作指导、内部控制工具和任务循环。新增开发以该方案为主；本文保留多轮问答、证据选择和上下文边界的细化设计。`staged` / `streaming_gated` 的目标及实施顺序以新方案说明为准。

## 1. 第一版目标

面向换物、指代、比较、追问，让应用明确决定每轮回答使用哪些音频、图片、历史证据和行为规则，以及何时允许生成。

核心交付物是可检查的 `TurnContext` 和对应的执行记录。目标是提高有依据的回答和恰当澄清的比例；上下文构建本身不能保证模型总是回答正确。

用户选择的优先场景是多轮视觉问答。第一版不要求任务规划、多 Agent、长期用户记忆、向量数据库或通用工具调用。

## 2. 当前实现与目标的差异

当前：RoomIO 音频和 CameraInput 图片，经适配器直接持续写入 Qwen 实时连接；Qwen 自动判轮并生成；本地 ChatContext 根据返回文字记录对话，不负责重建云端历史。

目标：

```text
LiveKit 媒体与设备事件
  -> 本地短时缓冲 + 带来源的事件记录
  -> 确定本轮边界
  -> 更新会话状态 / 选择关联历史与画面
  -> 构建不可变 TurnContext
  -> 检查证据、预算、版本与模型接口能力
  -> 编译为供应商请求并显式生成
  -> 播放、取消、记录实际结果
  -> 更新下一轮可用状态
```

必须区分两个模式：

- `staged`：音视频先经过本地缓冲和选择，再发送本轮输入。符合“先构建上下文，再送模型”的严格目标，代价是增加端点检测、上传等待和缓冲压力。
- `streaming_gated`：输入可提前流入云端，但应用持有回答触发权。降低延迟，但不能撤回已发送内容，也不代表完整掌握远端上下文。

先验证 staged；后者作为后续延迟优化。两种模式复用状态、选择器和生成门禁。

## 3. 最小模块

建议置于 `src/livekit_vlm_agent/harness/`，第一版可以在少量文件中实现，不需要每个职责单独建服务。

| 职责 | 输入 | 输出 |
| --- | --- | --- |
| EventStore / MediaBuffer | 音视频、摄像头启停、播放与转写事件 | 有界事件和短期媒体引用 |
| SessionState | 事件、已确认用户信息、模型观察 | 当前设备、指代、话题与证据状态 |
| ContextBuilder | 本轮输入、状态快照、历史证据 | 不可变 TurnContext |
| TurnController | 轮次边界、上下文就绪、插话 | prepare / dispatch / cancel / finish |
| ModelBackend | TurnContext | 模型事件、音频、文字、协议回执 |

harness 不依赖 Qwen 事件字段。Qwen 协议留在 providers 中；worker 负责装配，LiveKit 负责采集与播放。

## 4. 状态与证据

每条记录至少关联 session_id、turn_id、事件顺序、服务器接收单调时间和来源。不要用客户端时钟直接判断跨设备帧龄。

### 4.1 设备与媒体状态

- 摄像头是否开启、当前 camera_epoch、最新接收帧与时间。
- 本轮音频起止时间、帧引用、是否完整。
- generation_epoch、当前回答 ID、正在生成或播放的状态。

camera_epoch 表示镜头生命周期变化；换了拍摄物并不必然触发 camera_epoch。
图片差异只能产生“场景可能变化”的信号，不能直接断言物体身份发生变化。

### 4.2 指代与对象

- 当前话题与当前关注对象候选。
- 最近提及对象候选及其依据 frame_id / user_event_id。
- 用户明确纠正、明确命名与尚未消解的歧义。

摄像头状态、帧时间是应用可直接知道的事实；“这是杯子”“它是红色”通常是模型观察，不自动升级为确定事实。

对象记录使用稳定的本地引用标识；只有用户确认或可靠关联依据才能合并两个候选。没有关联依据时，“它”保留为 unresolved。

### 4.3 历史证据

每项证据包含 evidence_id、来源、时间、关联话题/对象、适用范围、是否过期及原始媒体引用。

原始图片可作为视觉证据；模型先前描述应标记为 model_observation。模型自报置信度不能作为校准概率。
用户口述也可能有误，保留 user_reported 标签，与图像观察分别存储。

短时媒体默认仅在内存保留，设置数量、字节和时长上限；挂断释放，不因可回放要求默认落盘。

## 5. TurnContext 契约

示意数据结构，字段不代表可直接发给 Qwen 的 API：

```yaml
session_id: s1
turn_id: t12
state_revision: 38
generation_epoch: 7
policy_version: visual-v1
input:
  audio_ref: audio_t12
  transcript: null
  transcript_status: unavailable
  turn_boundary: explicit_release
intent:
  kind: unresolved
  referents: []
runtime:
  camera_enabled: true
  camera_epoch: 3
evidence:
  - id: frame_now
    kind: image
    role: current_view
    media_ref: image_103
    age_ms_at_build: 240
  - id: frame_previous
    kind: image
    role: prior_comparison_candidate
    media_ref: image_087
history:
  recent_turn_refs: [t10, t11]
  user_confirmed_facts: []
  model_observations: []
  unresolved_references: []
response_policy:
  missing_current_view: ask_for_camera_or_frame
  ambiguous_reference: clarify
  unsupported_comparison: explain_missing_evidence
```

TurnContext 包含结构化文本和媒体引用，不强制把所有音视频先转换成文字。
缺少转写时，harness 可以按确定性策略选择当前画面和少量相关历史候选，再由回答模型理解原始语音。
如果要在回答前理解问题并精确检索，需增加独立 ASR 或经验证的提交后转写阶段；不能依赖回答完成后才到达的转写来构建同一轮输入。

## 6. 上下文选择规则

按当前任务选最少但足够的信息，设置媒体与文本预算；按时间裁掉全部旧消息不能解决指代问题。

| 问题类型 | 优先证据 | 缺失处理 |
| --- | --- | --- |
| “现在这个是什么” | 当前有效画面、本轮语音 | 无新画面则请求开启摄像头或重新展示 |
| “它是什么颜色” | 当前画面、最近明确指代、必要上一轮 | 多候选则询问指的是哪个 |
| “和刚才那个比，哪个有把手” | 两个相关对象的图像、顺序映射 | 缺少历史图片则不能把旧描述当作已验证观察 |
| “刚才那个是什么颜色” | 对应历史画面及其历史标记 | 不用当前帧替换历史证据 |
| “你刚才认错了，这是碗” | 用户纠正、相关画面、被纠正的观察 | 更新指代状态，保留来源和纠正关系 |

当前画面过期阈值用于当前视觉判断。用户明确询问过去时，历史证据可以使用，但必须标明历史，不伪装成当前画面。

判断物理尺寸等问题还需要尺度和视角依据；两张图片本身不一定足以支持比较。

初期不必让额外模型承担意图分类。可用少量最近轮次和候选关键帧形成有界上下文。只有当精细选择收益明确时再增加 ASR/语义提取，并单独测量错误与延迟。

## 7. 编译为模型输入

逻辑上分为：固定规则、运行状态、用户确认信息、模型观察、相关历史、当前音频及图片。

规则与证据分开；识别/OCR/用户引述均作为资料，不能提升为行为指令。避免把上一轮模型猜测写成固定规则。

当前适配器可使用 session.update 更新 instructions，但它是会话级配置，不是已验证的任意历史编辑接口。若暂用 instructions 承载动态文本，采用固定模板、明确的数据边界和限长，每轮整体重建，移除上一轮临时内容。仍需验证模型遵循情况。

当前官方客户端事件文档中的 conversation.item.create 面向工具结果和 MCP 审批，不能假定可用它重放任意 user/assistant 消息。

历史图片与当前图片如何在同一轮保持独立且可引用，必须实测；不能假设连续 append 两张图片就一定能进行可靠比较。若不支持，需要选用满足逐轮多图输入的后端，或经验证的独立历史视觉分析路径。

## 8. 控制时序

首版验证用明确的“说完/提交”边界或按住说话，先排除自动判停干扰。自动模式后续接入本地 VAD/端点判断，并另外处理附和与插话。

```text
本地收集本轮媒体
-> 确定轮次边界
-> 冻结本轮输入与状态快照
-> 构建 TurnContext
-> 校验证据和版本
-> 更新会话指令，等待确认
-> 按已验证协议发送本轮音频和图片
-> input_audio_buffer.commit，等待 committed
-> 再次检查本轮未被取消，必要时复查媒体新鲜度
-> response.create
-> 绑定 response_id 与 turn_id/context_id
-> 播放与完成，或取消
```

以上是待验证的适配流程，不声明已在当前 LiveKit 手动轮次路径跑通。

单一控制器持有生成权；一次连接最多一个未完成的指令配置更新，防止 session.updated 被误归因。
采用本地 context_id/hash 关联协议回执与发送顺序，不假设云端会回传自定义 context_id。
下一轮媒体先进入本地缓冲，不能混入尚未提交的本轮输入。

state_revision 用于检测构建期间的相关状态变化；generation_epoch 用于取消和屏蔽旧回答。
普通新帧不必使所有构建失效。镜头切换、用户插话等关键变化需按规则使上下文失效或重建。
取消可以在任意等待点发生；发送前再次检查版本。清理旧输入和处理迟到回执后，才允许下一轮发送。

关闭云端 VAD 后不能继续依赖其 speech_started/stopped 作为本地成轮和打断的唯一来源，必须补齐控制器与 LiveKit 的事件映射。

## 9. 控制边界

显式 response.create 解决生成时机，不能自动删除 Qwen 已有会话历史。

- 会话复用：保留实时性能，但只能保证本轮新增输入经过 harness；不能声称云端仅使用 TurnContext。
- 严格逐轮上下文：需要支持完整请求上下文的后端，或验证新建模型会话后按指定上下文重建的可行性与成本。可只重建模型连接，保持 LiveKit 通话连接。

首版应明确采用哪种语义。至少实现“本轮新增输入经过 harness，生成经过门禁”；若要求“模型只能使用选择后的上下文”，必须完成后端能力验证，不能靠提示词声称已经隔离。

模型生成文字、已转发文字、播放进度及用户确认分别记录。无法精确知道用户实际听到的字词时，保留 unknown，不以 response.done 替代听完。

## 10. 实施顺序与验收

1. 协议小实验：手动轮次、更新指令后显式生成、提交后转写时机、多图关联、取消后缓冲清理、LiveKit 播放事件映射。保留失败记录。
2. 先做纯逻辑 EventStore / State / ContextBuilder 和固定输入用例，不调用云端。
3. 接入 staged 模式和明确轮次边界，打通一个当前画面问题及一个追问。
4. 加入历史关键帧、指代与比较；明确能力不足时的澄清和后端降级。
5. 经过对照验收后再加入自动判停与 streaming_gated，测量控制收益与延迟代价。

固定验收场景：换物后立即问、旧物追问、当前/历史物体比较、两个物体指代歧义、摄像头关闭、镜头切换、构建期间插话、用户纠正、长对话无关历史、过期或缺失画面。

每个用例记录：期望引用的证据、实际选择的证据、排除理由、context/policy 版本、生成触发顺序、实际回答与是否恰当澄清。
既要测回答准确，也要测缺证据时不瞎答；不能把一律澄清当成成功。
与当前透传模式使用同一组输入对照，记录端点检测、构建、上传、模型首音和播放延迟，不预先承诺性能指标。

默认日志只保存脱敏元数据、证据 ID、选择原因、版本与时序；原始音视频和用户完整内容不默认写盘。固定非敏感测试集可用于受控回放。

## 11. 依据与待验证项

- 当前代码：worker.py、providers/qwen_realtime.py、providers/qwen_client.py、media/video.py。
- 项目已验证手动直连语音、会话内追问、图像等基线；不等于本设计的编排顺序已验证。见 verification-report.md。
- 官方客户端事件：https://www.alibabacloud.com/help/en/model-studio/client-events （2026-10-29 阅读，页面更新日期 2026-10-23）。该文档说明 turn_detection=null、手动 commit、显式 response.create、图片与音频共同提交。

文档能力不代替当前模型、Endpoint 与锁定 SDK 的真实协议验证。

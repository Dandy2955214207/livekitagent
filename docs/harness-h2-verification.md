# H2 受控问答与手机指导测试

日期：2026-10-30（Asia/Shanghai）。H2 已接入同一个 Qwen 连接和现有 AgentSession / RoomIO，手机沿用原 HTTPS 通话首页，使用明确开始/提交语音。本阶段不启用自然端点、自动静默检查、免按键指导或连续讲解。

## 手机测试

若之前启动了 M2 服务，先停止，再启动指导测试模式：

```powershell
.\scripts\stop-local.ps1
.\scripts\start-harness-mobile.ps1
```

本工作区前端已构建。修改前端后须先 `npm.cmd run build --prefix web`。电脑与手机网络、IP、证书和媒体防火墙规则沿用 M2 配置，不新增规则、不关闭防火墙。

手机打开 **https://172.20.10.9:8443/**，刷新页面，加入通话。加入后应出现“步骤指导测试”和“开始说话”；若仍是自动收音的旧页面，请确认启动的是 `start-harness-mobile.ps1` 并刷新。

1. 开启摄像头，等待“画面已准备”。点击“开始说话”，问物品是什么，说完点“提交本次语音”。本模式仅在提交时上传本轮音频与一张有效新图，静音等待不会自行回答。
2. 再次点击开始，提出追问并提交。也可关闭摄像头继续语音问答。
3. 请求：“用三个步骤指导我在桌上摆三个积木，每步摆一个，等我确认再继续。”应显示第 1 / 3 步并讲第一步，然后等待操作。
4. 可以问“这一步怎么做”，当前步骤应保持不变。操作后明确说“当前这一步已经完成，请继续”，提交后才进入下一步。图片本身不自动证明动作完成。
5. 测试暂停、继续和结束任务。结束任务保留通话；再次开始说话可提普通问题。录音中、思考中或讲解中可重新开始说话来明确打断旧轮次。

每段最长 30 秒；超限丢弃整段并提示重新分段，不静默截断。等待操作 120 秒无进展会暂停，可点继续。模型没返回有效决定时最多纠正一次，仍失败会提示重新提交；不要把无回答当成动作已确认。

## 实现

- `qwen_harness.py` 增加受控适配器，复用原协议客户端与语音映射。控制器独占提交和生成，SDK 关闭服务端自动成轮，不会同时启动自动回答。
- 原始 16 kHz PCM 在本地有界缓存，明确提交后交给同一个 Qwen 理解，不新增 STT/TTS 或规划模型。`audio_ref/input_id` 与真实输入绑定，用户口述确认不冒充视觉确认。
- 同一连接从首次配置保持 text+audio；仅在本地将 DECIDE 的音频、文字和函数事件完全隔离。SPEAK 才发出 SDK generation 和字幕/音频。
- 使用官方嵌套 `function` 工具格式；展开 schema 的引用与 nullable 可选项，计划明确为对象数组。应用侧仍用严格 schema 校验、未知字段拒绝和有界长度。
- 工具声明保持固定；DECIDE/SPEAK 使用统一规则并明确本次阶段。SPEAK 的 `allowed_actions` 为空，任何工具返回均不能执行，只会中止该轮。**此处是应用执行门禁，不声称供应商强制关闭工具选择。**
- 完整函数项及成功 Response 到达后才执行；真实工具结果回填、等待对应创建回执，再请求语音。错误给出不含原始值的字段级原因，最多一次纠正。
- 开始说话先使旧决定和输出失效；执行器确认取消并清理旧播放后，才接受新的生成。插话与完整工具同时到达时，过期调用也会收到拒绝回执，不遗留未闭合调用。
- 使用 `speech_created` 的真实 SpeechHandle 关联当前段，`wait_for_playout` 完成且未 interrupted 后才进入等待。口径是 `server_playout`，不等价于浏览器实际听到或用户理解。
- `HARNESS_MODE` 默认 `legacy_auto`；新启动脚本仅对子进程设置 `controlled_manual`。原 `start-mobile.ps1` 保留 M2 自动问答，不能在同一连接热切换。

## 验证与已知限制

**79 项 Python 回归、前端生产构建和挂断清理检查通过**。回归涵盖原有基线和新增 13 项执行器测试：原始音频不依赖转写、安静 DECIDE、工具回填后发声、取消回执先于新生成、配置期间暂停、过期输出隔离、录音限额、坏参数有界纠正、schema 兼容、SPEAK 工具拒绝及插话/工具竞态。

真实固定媒体通过浏览器 WebRTC → LiveKit → AgentSession → 同一个 Qwen → RoomIO 播放路径验证，未使用物理麦克风、摄像头或扬声器。验证结果和保留的失败尝试见 [harness-h2-results.json](harness-h2-results.json)。390px 页面截图保存在 `.runtime/h2-results/phone-width.png`，原始运行报告均保留在该目录。

最终一次完整验证六个用户轮次通过：红图、蓝图追问、三步指导的第一步、同一步追问、确认后第二步，以及结束任务后的普通问答。暂停/恢复不改变步骤，控制内容未进入字幕；浏览器接收 275,380 字节 RTC 音频，页面无异常、无横向溢出，挂断后活动轨道为 0。房间、Worker 活动任务、API 活动会话均为 0。手机启动脚本已启动核对，原地址 HTTPS 使用现有 CA 正常校验证书并返回新版页面；手机真实媒体仍由用户验收。

开发中观察到：text 初始化后切到音频虽配置确认却无语音；固定 audio 从连接开始可正常续答。旧工具格式、复杂 nullable/ref schema 和不够明确的多阶段规则组合曾导致计划格式错误或后续轮次不调用工具；本次一并改成官方格式、展开 schema 和明确规则，不把多变量对照单独归因为供应商故障。

还有一个保留的失败样本：结束任务后要求逐字复述“语音测试成功”，模型两次直接生成普通语音而没有工具；本地门禁按设计拦截，没有隐式推进。此类工具遵循失败会暂停/提示重提，尚不能保证任意措辞都成功。工具参数缺失证据的真实样本也被拒绝，并在一次纠正后通过。

未验收：手机真人听感与尾音、真实噪声/回声、手机镜头切换的新指导上下文、自然插话、长会话稳定性，以及完整 H3/H4/H5。持续讲解在此模式禁用，指导只接受 `user_reported` 的完成依据，自动视觉检查保持关闭。

## 开发复现

```powershell
# 离线，不连接云端
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/probe_h2_loop.py

# 固定非真人真实验证，产生模型调用
.\scripts\prepare-h2-fixtures.ps1
.\.venv\Scripts\python.exe scripts/run_local.py --harness
# 另一个终端执行，完成后 stop-local.ps1
node web/tests/h2-smoke.mjs
```

仅补测指导可在该测试进程设置 `H2_GUIDE_ONLY=1`。诊断探针 `probe_h2_loop.py --live --fixed-tools` 每次最多四次生成、90 秒上限，无自动重试；仅用于固定输入对照，不替代 SDK 通话验收。

协议参考：[Qwen 客户端事件](https://help.aliyun.com/zh/model-studio/client-events)、[官方 Realtime Function Calling 示例](https://help.aliyun.com/zh/model-studio/qwen-function-calling)。SDK 行为依据锁定的 LiveKit Agents 1.8.9 本地源码与真实通话，不增加未经验证的能力声明。

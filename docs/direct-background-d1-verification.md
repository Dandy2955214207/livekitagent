# D1 直接对话可测试骨架

日期：2026-11-02（Asia/Shanghai）。状态：实现及离线回归通过；供应商/RTC/手机非退化验收待完成，不宣称 D1 全阶段验收通过。

新增模式 `direct_background`，worker 装配 `QwenLiveModel`。前台继承 M2 的音频映射、有序发送、语义 VAD 自动回答、OutputGate、取消及关闭行为，没有每轮 DECIDE/SPEAK、工具门禁或本地 VAD。D1 不创建 Monitor、不注入记忆、不请求主动语音。

原编码器增加同步轻量发布钩子；新模式将不可变编码字符串与服务器接收时间、镜头 epoch、程序递增帧 ID 放入独立缓冲。前台仍使用原 `take()`，后台按独立游标读快照，不会取走前台帧。容量最多 10 帧、300 万 Base64 字符（计内存中的编码负载）、10 秒时间窗；读取及发布时裁剪。没有读写时不启动清理 timer，旧缓存至多保留固定容量，读取时不会返回过期帧。镜头关闭/切换与挂断清空缓冲；在途编码仍使用原 epoch 检查。后台发布异常只计 `fanout_errors`，前台编码及发送继续。

API 创建通话时返回明确能力：直接问答可用，observation/proactive_audio/task_tools/memory_injection 均 unavailable。页面给出 D1 提示，不显示 H2 的手动录音/任务控制。启动器新增互斥的 `--direct-background` 与原 `--harness`，支持复用 `--mobile` HTTPS profile，不改旧开关含义或默认配置。

验证命令：

```powershell
$env:PYTHONPATH='src'
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
npm.cmd run build --prefix web
.\.venv\Scripts\python.exe scripts/probe_live_background.py
.\.venv\Scripts\python.exe scripts/run_local.py --help
```

149 项 Python 测试通过，前端构建通过（既有 >500kB 提示）。新增覆盖：严格结果解析；双消费游标、帧数/字节/帧龄/镜头隔离；实际编码复用及前台 take 不受影响；关闭时模型/编码任务/输出流/缓冲释放；无需工具直接生成；后台发布异常隔离；API 能力；注入等待确认与缺确认关闭。旧取消、在途编码失效、输入/输出队列上限测试一并通过。没有增加模型调用。

待验证：Q1 普通问答/追问/立即换物、真实插话，RTC server_playout、同条件 M2/H2/新模式延迟比较，以及所有手机案例和 20 分钟稳定性。尚无新模式实际播放回执关联器；它必须在 D2 开放主动语音前实现。无 Monitor 实例，Q2 的推理并发与故障验证留待 D2。没有统计新模式性能分位数或宣称与 M2 等价的实测延迟。

运行配置、HEAD、云端阻断及已有修改见 [D0 报告](direct-background-d0-verification.md)。回退：停止对应启动器后以原 M2 入口启动（`HARNESS_MODE=legacy_auto`），或原 `--harness` 启动 H2；不在通话内热切换、不自动回退。现有手机服务未重启，只有源码和前端构建产物更新。结构化结果见同名 JSON。

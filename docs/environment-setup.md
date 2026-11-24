# Windows 资源准备与配置记录

日期：2026-10-28（Asia/Shanghai）。范围：开发方案的环境准备、依赖锁定、本机媒体连通性和云端配置验证。

## 已完成

| 项目 | 结果 / 证据 |
| --- | --- |
| 机器架构 | Windows x64 |
| Node.js / npm | 复用系统 24.22.0 / 11.19.1 |
| uv | 0.13.6，来自官方 PyPI Windows wheel，SHA256 匹配 |
| Python | uv 管理的 CPython 3.11.18，项目内安装，无注册表/全局 PATH 修改 |
| LiveKit Server | 1.13.11，官方 Windows 包，SHA256 匹配官方 checksums.txt |
| Python 依赖 | 77 个安装包，`uv.lock` 固定；`uv pip check` 兼容性检查通过 |
| 前端依赖 | LiveKit Client 2.23.0、Vite 8.3.5，`package-lock.json` 固定 |
| Python 导入 | RTC、API、Agents、FastAPI、Uvicorn、Pydantic Settings、WebSockets、Pillow、NumPy、PyYAML 均通过 |
| 前端导入 | Node 成功导入 LiveKit Room 和 Vite；尚未实现或验证浏览器页面 |
| 本地凭据 | 生成随机 API Key 与 256-bit 随机 Secret；`.env` 与 Server YAML 一致 |
| 本机房间 API | 鉴权、创建、列表查询、删除临时房间通过 |
| 本机 RTC 媒体 | 两个真实 RTC 客户端加入临时房间，合成 PCM 经发布、订阅与解码验证为非静音；连接及房间已清理 |
| Qwen 连接 | 北京工作空间 Endpoint + 本地 Key，收到 `session.created` |
| Qwen 配置 | 手动轮次、16 kHz s16le mono 输入、24 kHz 输出、Tina 音色收到 `session.updated`；semantic_vad 更新也收到确认 |

Qwen 验证约在北京时间 20:26 完成；实际事件记录见 `.runtime/qwen-probe.json`。本次没有发送模型音视频输入，也没有请求生成回答；没有得到可用于估算每分钟成本的用量数据。

## 配置与端口

- 7880/TCP：LiveKit 信令，仅绑定 127.0.0.1。
- 7881/TCP、7882/UDP：RTC 媒体；使用 127.0.0.1 作为公布的候选地址，开启 loopback candidate，关闭外部 IP 探测。实测 TCP 媒体监听为通配地址，UDP 同时监听 loopback 与另一网卡；`bind_addresses` 不限制全部媒体套接字。信令仍仅绑定 loopback，本次未配置外部设备接入或修改防火墙。
- 8000/TCP、5173/TCP：保留给后续业务 API 与测试页，当前未启动服务。
- 不配置公网入口、TURN、HTTPS、Redis、数据库、Docker、WSL 或 GPU。

本地文件 `.env` 和 `config/livekit.local.yaml` 含凭据，已加入 `.gitignore`。当前目录还没有 Git 仓库；将来初始化版本管理时应保留这些忽略规则。公开模板和记录不包含 Key。

验证结束后已停止测试用 LiveKit Server，并确认 7880/7881/7882 没有遗留监听。后续使用 `scripts/start-livekit.ps1` 手动启动。

## 已确认事项

- 用户确认 Key 属于北京，并提供北京工作空间主机名；配置已完成且真实鉴权成功。
- 用户允许少量真实协议验证。本次仅做连接和配置握手。
- 用户已自行把 Key 写入 `.env`，检查与探测脚本不输出 Key。
- 音色先用官方默认 Tina，后续有听感偏好再调整。

## 限制与待继续

Windows Server 启动时报告 CPU 监控不受支持，容量管理关闭；Server 仍正常运行，API 和 RTC 音频检查已通过。当前单房间验证可以继续，不能据此承诺生产环境容量管理能力。

M0 尚未完成：尚未验证真实语音单轮/多轮、图片输入、语义轮次与打断行为、模型配额/并发上限、单价/用量、20 分钟稳定通话。也尚未确认耳机/摄像头或浏览器设备授权。这些不会阻塞现阶段的资源准备。

LiveKit CLI 为可选项，本次使用已安装 Python API/RTC SDK 完成调试，未额外安装 CLI。

## 官方来源

- [LiveKit 本地运行与 Windows 包](https://docs.livekit.io/transport/self-hosting/local/)
- [LiveKit Server 1.13.11](https://github.com/livekit/livekit/releases/tag/v1.13.11)
- [uv 0.13.6](https://github.com/astral-sh/uv/releases/tag/0.13.6)
- [uv Python 管理](https://docs.astral.sh/uv/guides/install-python/)
- [Qwen 实时模型 Endpoint 与协议](https://www.alibabacloud.com/help/en/model-studio/realtime)
- [查找工作空间 ID](https://www.alibabacloud.com/help/en/model-studio/obtain-the-app-id-and-workspace-id)

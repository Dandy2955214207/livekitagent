# LiveKitAgent 实时视觉通话

LiveKitAgent 通过麦克风和摄像头提供视觉问答、持续讲解、分步教学、条件提醒和通话内视觉记忆。当前主线是百炼 Qwen 与官方 Harness。 [代码目录与边界](PROJECT_GUIDE.md#代码目录与边界) · [项目导航](PROJECT_GUIDE.md)

## 启动当前主线

首次在 Windows x64 安装时，先准备与 `web/package.json` 匹配的 Node.js，再在仓库根目录用 PowerShell 执行以下命令。工具与依赖按 `config/tool-versions.json` 和锁文件安装。 [环境配置与验证](docs/environment-setup.md)

```powershell
.\scripts\install-tools.ps1
.\scripts\sync-dependencies.ps1
npm.cmd ci --prefix harness/qwen-live --ignore-scripts
.\scripts\check-environment.ps1
```

在本机 `.env` 中填写百炼的 `QWEN_API_KEY` 和 `QWEN_WORKSPACE_ID`。当 `QWEN_REALTIME_URL` 留空时，服务按地域和工作空间 ID 推导地址。配置字段见 [示例](.env.example)。模型、LiveKit 和鸿蒙签名凭据由本机或部署环境管理。

已配置环境时，构建网页并启动桌面通话。浏览器打开 `http://127.0.0.1:8000`，允许麦克风，接通后再打开摄像头。按 `Ctrl+C` 或执行 `.\scripts\stop-local.ps1` 停止。

```powershell
npm.cmd run build --prefix web
.\.venv\Scripts\python.exe .\scripts\run_local.py --official-proactive
```

| 用途 | 入口 |
| --- | --- |
| 局域网手机 | `scripts/start-qwen-live-mobile.ps1`，需先准备手机 HTTPS 配置。 [手机准备](docs/mobile-device-check.md) |
| 跨城邀请试用 | `scripts/start-remote-trial.ps1`，默认仅监听本机 `8097`，可接 HTTPS 入口。加 `-Mock` 可测试采集与播放。 [试用配置](docs/harmony-gateway-v1.md#7-跨城浏览器试用) |
| 独立鸿蒙 App | ArkWeb 加载同一通话页。 [构建与签名](harmony-demo/README.md) |
| 鸿蒙原生联调 | 模拟或真实 WSS 网关。 [协议与验收](docs/harmony-gateway-v1.md) |

通用启动脚本也使用官方 Harness。已有的 `--qwen-live` 命令和 `HARNESS_MODE=qwen_live_official` 配置可以继续使用。若旧 `.env` 指定了其他模式，删除该行或将它改为 `qwen_live_official`。

## 回归验证

在当前 Windows 开发环境运行。下面的 RTC 联调使用模拟模型和独立服务端口。 [本机运行与排查](PROJECT_GUIDE.md#运行与排查入口)

```powershell
$env:PYTHONPATH = "$PWD/src"
.\.venv\Scripts\python.exe -m unittest discover -s tests
npm.cmd test --prefix harness/qwen-live
node --test web/tests/guide-target.test.mjs web/tests/official-audio.test.mjs
.\.venv\Scripts\python.exe scripts/verify_official_harness.py --harmony-gateway
.\.venv\Scripts\python.exe scripts/verify_official_harness.py --browser-trial --call-view
.\.venv\Scripts\python.exe scripts/verify_official_harness.py --lifecycle
```

## 历史实验

早期模式的代码和当时的启动、验证命令保存在 Git 标签 `pre-official-only`。以下文档记录各阶段的实现与验证。

- M0 模型协议与 M1/M2 通话基线。 [M0 验证](docs/verification-report.md) · [M1 验证](docs/m1-verification.md) · [M2 验证](docs/m2-verification.md)
- 自研控制器的手动指导、连续物品介绍和延迟优化。 [H1](docs/harness-h1-verification.md) · [H2](docs/harness-h2-verification.md) · [展示实验](docs/harness-showcase-verification.md) · [延迟验证](docs/harness-showcase-latency-verification.md)
- 直接问答与独立后台文字观察实验。 [集成验证](docs/direct-background-d2-integration-verification.md)
- 官方 Harness 适配和后续任务能力。 [适配记录](docs/qwen-live-harness-adaptation.md) · [讲解与教学](docs/scene-guide-teaching.md) · [提醒](docs/explicit-intent-intervention.md) · [通话内记忆](docs/official-memory-design.md)

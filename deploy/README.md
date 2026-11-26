# 单机服务运维

Windows 启动器用于开发。Windows Docker Desktop 的 Linux 引擎与 Ubuntu 使用同一份 Compose 清单。每个环境单独初始化凭据。生产和模拟栈使用各自的项目名、配置目录和回环端口。

### 首次启动和发布

以下是从仓库根目录执行的 Linux 示例，需要 Python 3、Docker Engine 和支持 `up --wait` 的 Compose。先确定数据目录、空闲端口和维护窗口。状态文件和模型密钥放在源码目录外，交给运行服务的操作系统账户保管。

```sh
cfg="$HOME/.local/share/livekitagent"
rev=$(git rev-parse --short=12 HEAD)
python3 deploy/service_config.py "$cfg" init
docker build -f deploy/Dockerfile -t "livekitagent/service:$rev" .
```

在 `compose.env` 设置回环端口。真实环境由运维填写 `provider.env` 的百炼工作空间和 API key，仅 Worker 读取。能力开关、提供商选择和会话凭据在 `session.env`；Worker 与网关共享的事件签名密钥在 `event.env`。备份整个配置目录时限制备份访问权限。Docker 主机管理员可以读取容器环境变量。

```sh
python3 deploy/release.py "$cfg" "$rev" --project livekitagent
```

本地离线验收在**独立配置目录**执行上述发布命令，使用 `--project livekitagent-audit --fixture`。这一模式挂载仓库现有测试 Worker 和模拟模型，Worker 网络无公网出口。真实环境需要到已配置模型服务的出站连接。

### 日常操作

以下函数对应真实环境。管理模拟栈时改用它的配置目录、项目名，并在 `"$@"` 前加 `-f deploy/compose.fixture.yaml`。

```sh
svc() { docker compose --env-file "$cfg/compose.env" -p livekitagent -f deploy/compose.yaml "$@"; }
svc ps
svc logs --since 15m gateway worker api livekit
svc stats --no-stream
curl --fail http://127.0.0.1:18095/ready
```

启动用 `svc up -d --wait`，有计划的停机用 `svc stop`，配置变更后用 `svc up -d --wait`。在维护窗口用 `svc restart worker` 单独重启 Worker。容器退出后 Docker 按 `unless-stopped` 拉起，人工停止后保持停止。Ubuntu 开机恢复还依赖 Docker 服务已启用，可用 `systemctl is-enabled docker` 核实。`/health` 表示网关存活，`/ready` 探测会话 API、LiveKit 房间服务和 Worker 的 SDK 健康端点。模型可用性需要单独通话确认。Docker 健康状态供监控读取，探针变红时应排查。每个服务的日志最多轮转三份，每份 10 MiB。

单通话由独立 LiveKit 实例前的会话 API 统一控制。Worker 使用容器资源上限，默认最多 2 核和 1536 MiB。快速重拨时，SDK 的派发预留槽会短暂将空闲 Worker 标成满载，因此这套专用部署关闭 SDK 的负载拒单阈值。共享主机部署前根据资源余量调整 `WORKER_CPUS` 和 `WORKER_MEMORY`，并复测接通延时与内存峰值。

### 邀请和远程入口

```sh
python3 deploy/service_config.py "$cfg" issue --hours 24
python3 deploy/service_config.py "$cfg" revoke "$invite_id"
```

给每位同事单独签发，把输出的 credential 私下交给本人，保留 id 供撤销。网页输入邀请码，原生客户端将同一个人的凭据作为 Bearer 访问 `/v1/calls`。撤销或过期后拒绝新连接，在用连接每 5 秒复核并发起挂断。网关重启后浏览器重新登录。一次只接受一场通话，第二位同事收到 busy，前一通挂断后可重试。测试录像和报告入口在该网关关闭。

远程只将已确认的内测 HTTPS 域名反代到网关回环端口。代理需支持 WebSocket Upgrade、保留原始 Host、覆盖 X-Forwarded-Proto 为 https，空闲超时覆盖通话时长。核实域名、证书和已有业务后再配置。Compose 只映射网关，API、Worker、LiveKit 信令和媒体通过容器网络互通。

### 更新和回滚

留存上一版代码提交、镜像和对应部署配置。使用新提交号构建新镜像，然后执行上面的发布命令。一次由一位操作者发布，维护窗口请同事暂缓拨入。脚本先检查现有通话，再等待整组就绪，成功后更新 `compose.env`。已运行的版本升级失败时，脚本用原镜像拉起服务。手动回滚可将相同命令的版本参数改为保留的旧标签。如果 Compose 清单或配置格式一起变更，先切回对应的清单与兼容配置。API 故障无法读取活动会话数时，在确认中断影响后用原清单和已知可用镜像进行人工恢复。

发布后从入口拨一次测试电话，核对收发、挂断及再次拨入。进程内会话会随 API 重启中断。API 启动时清理同一安装标识遗留的房间，密钥和标识应跨版本保留。

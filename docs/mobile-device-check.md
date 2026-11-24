# 鸿蒙手机接入准备与能力检测

日期：2026-10-29。用户设备：华为 Mate 80 Pro Max，HarmonyOS 7.0.0.466，华为浏览器 6.1.9.321，手机与电脑连接同一 Wi-Fi。

状态：用户已确认手机HTTPS无警告、前后摄像头预览/切换、麦克风授权、实际提示音四项均通过。侧边排查后当前地址为电脑172.20.10.9、手机172.20.10.8，原测试CA继续使用。M2正式媒体通话步骤见 [M2验证记录](m2-verification.md)。下面保留能力检测模式的操作与边界。

## 已明确的保密边界

用户明确允许：手机只拍摄非保密测试物品，这些测试音视频可以发送给Qwen云端API。不能以此授权发送手机中的其他保密内容。

通过 `start-device-check.ps1` 启动的是能力检测专用模式：

- 仅允许配置中的手机IP、电脑自身和回环来源。
- 摄像头/麦克风由用户主动点击并授权后采集，只在手机本地预览。
- 不加入LiveKit，不启动业务API、Worker或模型连接；网关阻止 `/api/sessions` 和 `/livekit` 路由。
- 不读取相册、文件或后台画面，不录制原始音视频；复制的报告不含持久设备ID或媒体内容。
- 正式M2视觉问答使用另一个启动模式 `start-mobile.ps1`，通话页说明语音和开启摄像头后的抽帧会发往Qwen。

## 用户现在需要做的操作

1. 在电脑以管理员身份打开PowerShell，执行：

   ```powershell
   & 'C:\Users\lzh\Project\LiveKitAgent\scripts\mobile-firewall.ps1'
   ```

   该脚本从 `.runtime/mobile/profile.json` 读取已确认的手机地址，只允许这台手机访问电脑的TCP 8443/8088；不开放RTC媒体端口、不关闭防火墙。它会核实规则是否存在，权限不足时返回失败。

2. 在手机华为浏览器打开助手给出的HTTP证书入口，下载 `livekit-mobile-test-ca.cer`。入口地址和HTTPS地址保存在 `.runtime/mobile/profile.json`，IP变化后需重新准备。

3. 手动安装并信任该测试CA，证书名称为 **LiveKitAgent Mobile Test CA**。不假设HarmonyOS 7的具体菜单路径：可尝试打开下载的 `.cer`，或在系统设置中搜索“证书”“凭据”；没有安装入口时，反馈当前显示的选项，再按实际界面排查。不要以跳过证书错误作为测试成功。

4. 打开HTTPS能力检测页，依次点击“开始本地采集”“切换前后摄像头”“停止采集”，然后测试提示音。确认实际看见画面、实际听到声音后，复制检测结果回传。

本次公开CA文件的SHA256：

```text
152fddc0f5dc542380cb1d1ff08d524f21e3da13afbc6fa5b4ff1fa8d8024cf9
```

CA有效期约30天，服务器证书约7天；实际起止时间以证书详情为准。私钥只保存在电脑项目 `.runtime/mobile/`，不由下载入口提供。当前未将CA加入电脑的系统信任库。测试结束后可删除手机上的该测试CA。

## 电脑侧检查结果

- 25项Python回归通过，其中6项为新增网关边界/转发检查。
- 独立桌面Edge假摄像头：可显示预览、先释放旧轨道再重新采集、停止后释放轨道、启动AudioContext；没有访问会话或模型端点。
- 使用本地CA验证HTTPS证书链及主机名通过，没有关闭TLS校验。
- 当前监听入口由电脑自身验证可用；公开证书下载内容与本地DER证书完全一致。
- 当前检测入口的云端会话创建请求返回403。

证据在 `.runtime/mobile/desktop-check-report.json`、`tls-check-report.json`、`readiness.json`。桌面检查不替代手机真机结果。

## 启动、停止与撤销

```powershell
# 已准备手机地址、证书和构建结果后，只启动能力检测，不启动模型
.\scripts\start-device-check.ps1

# 停止能力检测
.\scripts\stop-device-check.ps1

# 测试结束，在管理员PowerShell移除本项目规则
.\scripts\mobile-firewall.ps1 -Remove
```

若电脑或手机IP变化，重新执行 `prepare-mobile.ps1 -ComputerIP <电脑IP> -PhoneIP <手机IP>` 并核对规则。准备脚本需要PowerShell 7的证书API；防火墙脚本可在管理员PowerShell中独立运行。

能力检测已由用户确认通过。M2已实现摄像头发布、服务端采样、图片发送，桌面固定视觉样例通过；手机LiveKit媒体链路与真实物品验收仍待完成，见 `m2-verification.md`。正式通话使用 `start-mobile.ps1` 与通话首页 `/`，不要把检测页的本地预览当成已发送到模型。

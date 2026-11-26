# 视觉通话试用 · HarmonyOS Demo

打开 App 后进入现有全屏通话页。试用码在通话页输入，继续使用 Windows 上的 LiveKitAgent 服务。现有网页支持的语音和视觉问答、讲解、分步指导、提醒与通话内记忆均使用同一套服务和界面。

首次打开时点击「设置服务地址」，填写内测人员获发的 HTTPS 入口。顶部的「服务地址」可更换入口，已填写的地址保存在设备上。连接失败时，「重试连接」会重新加载页面。只接受 HTTPS 地址，麦克风和摄像头在网页请求对应能力时才向系统申请。公网连接仍依赖 Windows 服务和隧道，试用时只展示非保密物品。

## 构建

在项目根目录运行以下命令。也可以在 DevEco Studio 打开 `harmony-demo` 目录后构建。

```powershell
.\scripts\build-harmony-demo.ps1
.\scripts\build-harmony-demo.ps1 -AppBundle
```

第一条命令在 `entry/build/default/outputs/default/` 生成调试 HAP，第二条在 `build/outputs/default/` 生成发布用 APP 包。无签名配置时，产物用于构建检查或开发环境调试。

## 真机和同事试用

1. 验证真机时，用 USB 连接一台鸿蒙手机，在 DevEco Studio 为 `com.livekitagent.visualdemo` 配置调试签名，再运行 `entry` 模块。检查登录、麦克风授权、摄像头授权、镜头切换、AI 回复和挂断。 [华为真机调试说明](https://developer.huawei.com/consumer/cn/doc/app/agc-help-debug-overview-0000001955332054)
2. 同事远程下载安装时，在 HarmonyOS 开发者中心为该独立包名配置发布签名并提交 AppTest 邀请测试版本。已注册设备的小范围团队也可以使用指定设备发布。试用码继续通过单独渠道分享。 [AppTest 邀请测试](https://developer.huawei.com/consumer/cn/doc/app/agc-help-apptest-invite-testuser-0000002258071224/) · [指定设备发布](https://developer.huawei.com/consumer/cn/doc/app/agc-help-internal-test-overview-0000002253054942/)

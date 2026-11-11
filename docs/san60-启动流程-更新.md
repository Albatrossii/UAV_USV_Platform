# SAN60 与平台启动流程（更新）

适用目录：

- 平台：`D:\Project4_Frontend_Backend\UAV_USV_Platform`
- SAN60 包：`D:\Project4_Frontend_Backend\SAN60\SAN60_Windows_Offline_20261019`

之前的流程通过回放服务和桥接器把数据送进平台已有的光电视觉传感器卡片。新增独立「态势感知」页面后，平台后端可以直接读取和控制 SAN60，因此新页面不再需要单独启动回放网页或桥接器。原光电视觉页面保留。

## 推荐：一个终端启动全部

检查 `8775`、`8081`、`5177` 端口未被其他程序占用，然后打开 PowerShell：

```powershell
cd D:\Project4_Frontend_Backend\UAV_USV_Platform
npm run dev:san60
```

等待终端显示 SAN60、Spring Boot 和 Vite 均已启动后，在浏览器打开：

```text
http://localhost:5177/login
```

登录后，从左侧「光电视觉」下面进入「态势感知」，点击「开始回放」。本脚本默认使用端口 `8775 / 8081 / 5177`，并设置与旧 `start-voice-frontend.ps1` 相同的语音前端开关。保持终端开启；按 Ctrl+C 停止本次脚本启动的服务。若提示端口被占用，不要结束不确定归属的进程，先检查相应窗口。

一键脚本也保留截图中的后端配置：浏览器 Unity 指令模式、启用语音控制、关闭 ROS 网关与视觉传感器 WebSocket、关闭云台相机推流。集成令牌读取现有环境变量或本机后端配置，不在启动脚本内保存。若现有 `algorithm-service\.venv\Scripts\python.exe` 存在，会自动使用它运行算法。显式设置的环境变量优先于这些默认值；脚本不会修改系统环境变量。MySQL 仍需按原流程保持运行。

新解压文件夹应保留在：

```text
D:\Project4_Frontend_Backend\SAN60\SAN60_Windows_Offline_20261019
```

脚本默认在这个目录读取随包 Python runtime 与 BAG，不复制大型运行环境或数据。若压缩包以后移到其他目录，先在 PowerShell 指定实际位置：

```powershell
$env:SAN60_PACKAGE_ROOT = 'E:\实际位置\SAN60_Windows_Offline_20261019'
npm run dev:san60
```

## 需要保留原四窗口操作时

仍可分开运行各服务，但要将 SAN60 设为**手动输入模式**，让平台控制回放：

1. 窗口一：SAN60 服务

```powershell
cd D:\Project4_Frontend_Backend\SAN60\SAN60_Windows_Offline_20261019
.\Start_Manual.bat --port 8775 --no-browser
```

这里不要运行 `Start_Replay.bat`。若自动回放完成后再打开新页面，页面会正确显示最后数据，但「开始回放」控制需要继续通过同一 SAN60 服务工作；`Start_Manual.bat` 能避免启动时先自行回放。

2. 窗口二：平台后端

```powershell
cd D:\Project4_Frontend_Backend\UAV_USV_Platform
$env:COMMAND_DISPATCH_MODE = 'browser-unity'
$env:ROS_GATEWAY_V1_ENABLED = 'false'
$env:VISUAL_SENSOR_WEBSOCKET_ENABLED = 'false'
# 集成令牌沿用本机后端配置或当前环境变量，不将实际值写入脚本。
$env:VOICE_CONTROL_ENABLED = 'true'
$env:APP_GIMBAL_CAMERA_STREAM_ENABLED = 'false'
$env:APP_ALGORITHM_PYTHON_COMMAND = (Resolve-Path '.\algorithm-service\.venv\Scripts\python.exe').Path
$env:SAN60_SERVICE_URL = 'http://127.0.0.1:8775'
npm run dev:backend
```

3. 窗口三：平台前端。该脚本会检查后端健康状态并沿用原语音前端配置：

```powershell
cd D:\Project4_Frontend_Backend\UAV_USV_Platform
.\scripts\start-voice-frontend.ps1 -FrontendPort 5177 -BackendPort 8081
```

它启动时会为前端设置匹配的 `8081` 后端代理与原语音功能开关。登录地址为 `http://localhost:5177/login`。

4. 可选窗口四：旧光电视觉 SAN60 卡片桥接。**只有需要给旧卡片供数时才启动**；新的「态势感知」页面不依赖它。

```powershell
cd D:\Project4_Frontend_Backend\UAV_USV_Platform
$san60Credential = [System.Net.NetworkCredential]::new('', (Read-Host '输入与后端配置一致的本机集成令牌' -AsSecureString))
python .\scripts\san60_offline_bridge.py `
  --source-url http://127.0.0.1:8775/api/state `
  --backend-url http://127.0.0.1:8081/api/integration/san60/spectrum `
  --token $san60Credential.Password
```

## 不再使用的入口

- 旧截图里的 `D:\Project4_Frontend_Backend\SAN60_Windows_Offline_20261019` 是之前的旧包目录；本次以新解压目录 `D:\Project4_Frontend_Backend\SAN60\SAN60_Windows_Offline_20261019` 为准。
- `Start_Replay.bat --port 8775 --speed 10` 会在独立 SAN60 窗口直接回放；推荐一键脚本使用手动模式，由网页按钮控制回放倍速和停止。
- 不要为新「态势感知」页面额外打开 `san60_offline_bridge.py`。它用于旧「光电视觉」卡片的桥接链路。
- 5174 是旧开发配置默认值。原流程使用并保留 5177，因此推荐脚本现默认使用 5177。统一启动脚本会把 Vite 代理明确指向 8081。
- `http://localhost:5174/login` 和旧截图的旧 SAN60 解压路径不再是推荐地址。

详细功能和已完成验收见 [SAN60 独立模块说明](san60-situational-awareness.md) 与 [验收记录](san60-acceptance.md)。

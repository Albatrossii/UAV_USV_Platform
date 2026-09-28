# 光电视觉 / SAN60 / Unity 联调接续文档

> 更新时间：2026-09-28（Asia/Shanghai）
> 本文档用于在新的 Codex 窗口中继续工作。它是当前联调状态的权威摘要，并取代早期基于 `radar_scan` / Protobuf 的电子探测仪设想。

## 1. 当前结论

SAN60 电子探测仪已经完成真实设备联调，当前链路可用：

```text
SAN60
  -> ROS /san60/spectrum
  -> SensorStreamAdapter（约 100 Hz 主动抽样到约 10 Hz）
  -> Gateway
  -> JSON WebSocket ws://10.16.85.173:8765/ws
  -> Spring Boot Backend（按流 latest-only 缓存）
  -> Vue 光电视觉页面（约 10 Hz 实时频谱）
  -> UnitySpectrumBridge（latest-only 接收）
```

系统另外保持以下连接：

- 状态、相机、传感器 JSON：`ws://10.16.85.173:8765/ws`
- 双向控制 Protobuf v1：`ws://10.16.85.173:8765/uav_usv/v1`
- Gateway HTTP 调试页：`http://10.16.85.173:8080`

当前 Protobuf v1 schema 没有 SAN60 Spectrum 消息，因此 SAN60 使用 JSON `/ws`，不要误认为它已经通过 `/uav_usv/v1` 传输。

## 2. SAN60 正式消息约定

后端筛选：

```text
message_type == "spectrum_frame"
```

当前真实数据源：

```text
vehicle_id = uav_01
sensor_id  = san60
stream_id  = uav_01_san60
```

核心 envelope：

```json
{
  "schema_version": "1.0",
  "message_type": "spectrum_frame",
  "timestamp": 1790228140.25,
  "sequence": 12345,
  "source": "uav_usv_fleet_gateway",
  "data": {
    "vehicle_id": "uav_01",
    "sensor_id": "san60",
    "stream_id": "uav_01_san60",
    "type": "spectrum",
    "captured_at": 1790228140.14739,
    "start_hz": 2399948790.93684,
    "stop_hz": 2500046445.56842,
    "bin_hz": 61035.1552631579,
    "rbw_hz": 100000,
    "ref_level_dbm": 0,
    "temperature_c": 39.07,
    "peak_hz": 2461289121.97632,
    "peak_dbm": -56.7138519287109,
    "powers_dbm": ["线上为 1641 个 JSON number"],
    "sequence": 38423
  }
}
```

频率轴必须按下式恢复：

```text
frequency[i] = start_hz + i * bin_hz
i = 0 ... powers_dbm.length - 1
```

不得通过 `stop_hz` 重新平均计算步长。

两个 sequence 的含义不同：

- 顶层 `sequence`：Gateway 全局消息序号。
- `data.sequence`：SAN60 原始采集序号。
- Gateway 将约 100 Hz 主动抽样为约 10 Hz，因此 SAN60 sequence 正常会按 `100、110、120...` 跳号；这不是网络丢包，平台不得据此告警。

## 3. 后端实现

主要文件：

- `backend/src/main/java/com/uavusv/platform/module/monitoring/integration/RosPoseWebSocketClient.java`
- `backend/src/main/java/com/uavusv/platform/module/sensor/service/SensorRuntimeService.java`
- `backend/src/main/java/com/uavusv/platform/module/sensor/dto/RadarOverviewResponse.java`
- `backend/src/main/java/com/uavusv/platform/module/visualsensor/service/VisualSensorService.java`
- `backend/src/main/java/com/uavusv/platform/module/visualsensor/integration/VisualSensorWebSocketClient.java`

实现行为：

1. 识别顶层 `message_type=spectrum_frame`。
2. 校验 `data`、`vehicle_id`、`stream_id` 和非空数值型 `powers_dbm`。
3. 使用 `(vehicle_id, stream_id)` 作为结构化缓存键。
4. 每个 stream 只保留最新一帧，新帧覆盖旧帧；没有无界历史队列。
5. 后端完整保留 1641 点，不重采样、不压缩、不修改原始 sequence。
6. 分别保存 Gateway sequence 和 SAN60 sequence。
7. 保留 `captured_at/start_hz/stop_hz/bin_hz/rbw_hz/ref_level_dbm/temperature_c/peak_hz/peak_dbm`。
8. 通过 `GET /api/sensors/radar` 暴露最新频谱给前端。

真实设备首次收帧日志：

```text
Connected to ROS pose WebSocket ws://10.16.85.173:8765/ws
Receiving ROS WebSocket message type spectrum_frame
SAN60 spectrum stream online vehicle=uav_01 sensor=san60 stream=uav_01_san60
gatewaySeq=177868 san60Seq=237381 points=1641
bandHz=2399948790.93684-2500046445.56842
binHz=61035.1552631579 peakHz=2471420957.75
peakDbm=-67.4175567626953 tempC=42.78
```

## 4. 光电视觉前端实现

页面名称保持“光电视觉”，当前交互要求与实现一致：

- 左侧一个大屏。
- 右侧七个小屏：原有 6 路摄像头 + 1 路 SAN60 电子探测仪。
- 点击右侧任意小屏切换左侧主屏。
- 已取消 720P/1080P 按钮，摄像头使用最高可用质量。
- 2D 与 3D 使用同一条真实 `spectrum_frame` 数据；3D 不需要新增 ROS/Gateway 消息。
- 主屏采用 2D + 3D 联合视图：左侧为实时二维频谱，右侧为最近 100 秒三维瀑布曲面，不再通过按钮切换。
- 3D 按 X=频率、Y=时间、Z=功率绘制，可拖拽旋转、滚轮缩放、双击复位。
- 前端按 Gateway/SAN60 sequence 去重，只保存 100 秒有界历史，并将 1641 点降采样为 164 个显示峰值桶；后端仍只缓存 latest frame。
- SAN60 主屏显示真实功率频谱、频段、峰值频率、峰值功率、RBW 和设备温度。
- 频谱约 10 Hz 刷新，带请求重叠保护。
- 圆形空间雷达只作为未来方位/距离点迹数据的兼容视图；SAN60 当前是 RF 频谱，不应伪装成空间雷达。

主要文件：

- `frontend/src/views/OpticalVisionView.vue`
- `frontend/src/components/vision/SpectrumWaterfall3D.vue`
- `frontend/src/types/sensor.ts`
- `frontend/src/stores/unityBridge.ts`
- `frontend/src/components/unity/OverviewUnityWebglPanel.vue`
- `frontend/public/unity-overview/index.html`
- `frontend/optical-electronic-preview.html`

曾修复的 Unity 遮罩问题：Unity 实际已在背后渲染，但 `platformInitialized` 关闭遮罩后，较晚到达的完整 `platformBridgeReady` 又把 `loading` 设回 `true`。现在后续就绪消息只更新能力，不会重新打开加载遮罩。

## 5. Unity 实现

Unity 源码仓库：

```text
F:\UVA_USV\UAV_USV_Unity
https://github.com/wangling416614-create/UAV_USV_Unity
```

新增：

- `Assets/Scripts/UavUsv/PlatformTools/UnitySpectrumBridge.cs`
- `Assets/Scripts/UavUsv/PlatformTools/UnitySpectrumBridge.cs.meta`

修改：

- `Assets/Scripts/UavUsv/PlatformTools/UnityPlatformCompatibilityBridge.cs`

Unity 行为：

- 声明 `spectrum-frame` capability。
- 接收 Vue 转发的完整 SAN60 frame。
- 内存中只保存 `LatestFrame`，每帧替换上一帧，不建立队列。
- 成功后回传 `spectrumFrameApplied`，包含 `streamId/sequence/pointCount`。
- Vue 负责二维实时频谱和 100 秒三维瀑布图展示；Unity 保留最新完整帧，供未来场景特效或原生仪表消费。

浏览器桥接验收结果：

```json
{
  "platformReady": true,
  "buildId": "unity-f24959c-platform-v4",
  "spectrumCapability": true,
  "spectrumReceipt": {
    "success": true,
    "streamId": "uav_01_san60",
    "sequence": 237381,
    "pointCount": 1641,
    "status": "Latest spectrum frame applied"
  }
}
```

Unity WebGL 已使用 Unity `2022.3.57f1` 重新构建，并将产物更新到：

```text
frontend/public/unity-overview/Build/
```

## 6. 仓库与已推送提交

### 前后端仓库

```text
仓库：https://github.com/yqylyx/UAV_USV_Platform
分支：codex/optical-vision-san60-integration
提交：fb11d6b440cb9c56b8c8290a5abd17441638c38f
标题：feat: integrate SAN60 spectrum and seven-source vision
```

旧远端分支 `nly/voice-control-p1-preparation` 已删除；不要再使用该名称。

PR 创建地址：

```text
https://github.com/yqylyx/UAV_USV_Platform/pull/new/codex/optical-vision-san60-integration
```

### Unity 仓库

```text
仓库：https://github.com/wangling416614-create/UAV_USV_Unity
分支：main
提交：512a16fb38f6a0bb90cc493f3de7e8efa2b8d8c7
标题：feat: add latest-only SAN60 spectrum bridge
```

## 7. 验证记录

已完成：

- `SensorRuntimeServiceTests` 和 `VisualSensorServiceTests` 通过。
- `npm --prefix frontend run build` 通过。
- Unity WebGL batch build 成功，日志位于 Unity 仓库的 `Logs/codex-webgl-build.log`。
- 真实 Gateway `/ws` 收到 `camera_frame` 和 `spectrum_frame`。
- `/uav_usv/v1` Protobuf 控制连接成功。
- 真实 SAN60 帧为约 10 Hz、1641 点。
- 浏览器验证 Unity `spectrum-frame` capability 和 1641 点回执成功。
- Unity 加载遮罩时序问题已修复。

## 8. 本地启动与检查

平台仓库目录：

```text
F:\UVA_USV\UAV_USV_Platform
```

启动：

```powershell
npm run dev
```

停止：

```powershell
npm run stop
```

前端：

```text
http://127.0.0.1:5174/
http://127.0.0.1:5174/vision
```

如果更新 Unity 二进制后仍显示旧页面，使用 `Ctrl+F5` 强制刷新。

后端针对性测试：

```powershell
backend\mvnw.cmd -f backend\pom.xml -q "-Dtest=SensorRuntimeServiceTests,VisualSensorServiceTests" test
```

前端构建：

```powershell
npm --prefix frontend run build
```

## 9. 工作区注意事项

- 平台仓库中仍可能存在 `.codex-run-logs/` 和语音控制相关未跟踪文件，它们不属于本次光电视觉工作，不要误提交或删除。
- Unity 仓库中的 `ProjectSettings/ProjectSettings.asset` 曾存在未提交改动，本次 Unity 提交没有包含它；继续工作前先检查归属。
- 不要把 SAN60 改回 Protobuf `radar_scan`，除非双方正式扩展并冻结新的 Spectrum protobuf schema。
- 不要在后端建立频谱历史无界队列；当前 Waterfall 历史只在 `/vision` 页面内保存最近 100 秒，离开页面即释放。

## 10. 下一窗口建议首先执行

```powershell
git status --short
git branch --show-current
git log -1 --oneline
```

然后确认：

1. 当前分支仍为 `codex/optical-vision-san60-integration`。
2. Gateway `10.16.85.173:8765` 可达。
3. `/vision` 中电子探测仪同时显示真实 2D 频谱和最近 100 秒 3D 瀑布图。
4. 6 路摄像头小屏与主屏切换正常。
5. Unity 遮罩在初始化后消失。
6. 如需继续上线，创建 PR 并安排长时间运行、内存增长和断线重连测试。

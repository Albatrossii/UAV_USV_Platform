# 光电视觉电子探测仪 ROS 接入约定

## 结论

电子探测仪不新增 TCP/UDP 端口。ROS Gateway 继续使用现有连接：

- 状态、相机和兼容传感器 JSON：`ws://10.16.79.66:8765/ws`
- Protobuf v1 控制与结构化遥测：`ws://10.16.79.66:8765/uav_usv/v1`
- Gateway 调试页：`http://10.16.79.66:8080`

电子探测 2D 数据优先通过 Protobuf v1 的 `GatewayEnvelope.radar_scan`
发送，`message_type` 固定为 `perception.radar_scan`。浏览器不直接连接 ROS；数据路径为：

```text
电子探测仪/ROS Topic
  -> ROS Gateway
  -> /uav_usv/v1（二进制 Protobuf）
  -> Spring Boot SensorRuntimeService
  -> GET /api/sensors/radar
  -> 光电视觉电子探测画面
```

## 第一阶段：2D 扫描数据（当前可接）

ROS 团队按仓库中的 `backend/src/main/proto/uav_usv_gateway_v1.proto` 生成代码，
发送一个 `GatewayEnvelope`：

```text
spec_version = "1.0.0"
message_type = "perception.radar_scan"
message_id   = 每条消息唯一 UUID/ULID
stream_id    = "electronic-detector-01.scan"
sequence     = 在该 stream 内严格递增
source       = ROS Gateway 实例名
device_code  = 设备安装载体编号；固定站可为空
frame_id     = "map" 或设备坐标系名称
body         = radar_scan
```

`RadarScan` 必填约定：

| 字段 | 单位/约束 | 示例 |
|---|---|---|
| `sensor_id` | 稳定且全局唯一，建议 `EDET-01` | `EDET-01` |
| `sensor_frame_sequence` | 设备帧号，严格递增 | `18231` |
| `source_timestamp` | 设备采样时间，不是转发时间 | Protobuf Timestamp |
| `angle_min_rad` | 弧度 | `-3.1415926` |
| `angle_max_rad` | 弧度 | `3.1415926` |
| `angle_increment_rad` | 弧度，必须大于 0 | `0.0087266` |
| `range_min_m` | 米 | `0.5` |
| `range_max_m` | 米 | `12000` |
| `ranges_m` | 与角度采样一一对应；无效点用 `NaN`/`Inf` | packed float |
| `intensities` | 可选；若提供，长度应与 `ranges_m` 一致 | packed float |

建议从 5–10 Hz 开始联调。先保证时间戳、序列号和单位正确，再提高频率；
避免在同一帧中重复发送静态历史点。

## 第二阶段：目标航迹与 3D 预留

2D 页面第一阶段只依赖 `RadarScan`。若设备能直接输出已聚类目标，双方再启用
`GatewayEnvelope.radar_detections`，消息语义使用 `DetectionBatch`：

- `range_m`：目标距离，米；
- `azimuth_deg`：方位角，度；
- `elevation_deg`：俯仰角，度，2D 设备填 0；
- `radial_velocity_mps`：径向速度，米/秒；
- `local_position_enu_m`：三维阶段使用 ENU，`X=East, Y=North, Z=Up`；
- `track_id`：跨帧稳定，不能每帧重新编号；
- `confidence`：0–1。

当前页面没有 3D 数据时保持 2D 模式。未来 3D 不开新端口，只扩展为
`radar_detections` 中的 `elevation_deg/local_position_enu_m`，或使用既有点云消息。
启用 `radar_detections` 前，需要平台端补齐该消息类型的运行时映射与验收测试。

## ROS 团队需要确认的五件事

1. 设备原始 ROS Topic 名称和 ROS 消息类型（例如 `sensor_msgs/LaserScan`）。
2. `sensor_id` 最终命名，以及设备安装在哪个 `device_code` 上。
3. 角度零点、顺逆时针方向和坐标系 `frame_id`。
4. 实际扫描频率、每帧点数、最大距离和无效值表示。
5. 当前只输出原始扫描，还是也输出带稳定 `track_id` 的目标航迹。

## 联调验收

- Gateway 的 `gateway.hello.capabilities` 声明 `perception.radar_scan`。
- 断线重连后 `sequence` 和 `stream_id` 行为明确，不产生大批旧帧回放。
- 平台 `/api/sensors/radar` 在 30 秒新鲜窗口内显示 `online=true`。
- 已知距离与角度的测试目标在 2D 极坐标画面中位置正确。
- ROS 停止发送后，页面在超时后转为离线，不继续显示过期数据。
- 2D 设备不伪造高度；3D 字段缺失时页面仍可正常工作。

## 当前联调注意事项

平台已经成功连接两条 WebSocket。`/ws` 当前确实广播 `camera_frame`，但其中
相机标识字段与平台期望的顶层 `camera_id` 尚未对齐，平台日志会将该帧标记为
`EMPTY_OR_UNMAPPED_CAMERA_ID`。ROS/Gateway 团队需提供一条去除图像 Base64 内容的
`camera_frame` JSON 样例，或把相机编号统一为：

```text
uav_01 uav_02 uav_03 usv_01 usv_02 usv_03
```


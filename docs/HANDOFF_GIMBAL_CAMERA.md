# 云台相机联调交接

## 当前进度

- 光电视觉页面保留原有协同回传，并新增“云台相机”模式；云台作为独立设备 gimbal_01，不加入 UAV01 原有相机列表。
- 视频链路：Gateway WebSocket ws://10.16.88.211:8765/ws，目标流 gimbal_01_a10tr，image/jpeg，1280×720。后端接收后由 GET /api/gimbal-camera/frame?since=<timestamp> 提供最近帧。
- 控制链路：前端 → /api/gimbal-camera/commands → A10TR 适配器 http://10.16.88.211:8780/commands → 192.168.2.119:2000。平台控制默认关闭。
- 当前已确认：Gateway 曾广播真实云台帧；适配器认证状态接口可返回 HTTP 200；控制抓包已确认方向停止/回中协议候选。

## 当前阻塞

最近一次 Windows 联调中，前端 5177 和后端 8081 已恢复，但 Windows 到 10.16.88.211:8765 的 TCP 连通性测试失败，后端日志持续出现 Gimbal video gateway connection failed。需先恢复该网络路径，再登录页面验证 JPEG 帧和实际渲染。

未登录请求帧接口返回 401 属于认证行为，不能作为帧缓存为空的依据。

## 启动与验证

1. 确认 Windows 可达 10.16.88.211:8765。
2. 启动后端（项目 backend/target/platform-backend-0.1.0-SNAPSHOT.jar，本地配置 backend/src/main/resources/application-local.yml）和前端 npm --prefix frontend run dev -- --host 127.0.0.1 --port 5177。
3. 保持 APP_GIMBAL_CAMERA_ENABLED=false；视频接收可保持 APP_GIMBAL_CAMERA_STREAM_ENABLED=true。
4. 打开 http://127.0.0.1:5177/vision，由操作员手动登录。
5. 登录后只读检查：GET /api/gimbal-camera/status、GET /api/gimbal-camera/frame?since=0；记录 HTTP 状态、Content-Type=image/jpeg、页面画面及浏览器控制台。

## 安全边界

- 不要在仓库、日志或交接文档写入 X-Adapter-Token 的实际值。
- 不调用 /commands，不启用平台或适配器控制开关，变倍保持禁用。
- 实机 MOVE/STOP/HOME、看门狗、断连和急停验收完成并获得现场确认后，才讨论开启控制。

详尽协议、抓包结论和接口说明见 docs/gimbal-camera-integration.md。

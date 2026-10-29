# SAN60 BAG 离线回放接入

本流程把 SAN60 Windows 离线部署包的 BAG 回放，通过本地桥接程序送入 UAV_USV_Platform 后端，并在“光电视觉”页面展示二维频谱、定位信息和三维空间测量态势。桥接程序只使用 Python 标准库，不修改 SAN60 部署包。

## 启动

分别打开四个 PowerShell 窗口，每个窗口都保持运行。请按后端、前端、桥接器、回放的顺序启动，避免高速回放在桥接器开始读取前就结束。

1. 启动课题四后端：

   ```powershell
   cd D:\Project4_Frontend_Backend\UAV_USV_Platform
   npm run dev:backend
   ```

2. 启动课题四前端，并登录后打开“光电视觉”页面：

   ```powershell
   cd D:\Project4_Frontend_Backend\UAV_USV_Platform
   npm run dev:frontend
   ```

3. 启动桥接器：

   ```powershell
   cd D:\Project4_Frontend_Backend\UAV_USV_Platform
   python .\scripts\san60_offline_bridge.py
   ```

   前端开发地址通常是 `http://localhost:5174`，后端默认是 `http://localhost:8081`。桥接器启动后会先显示数据发送错误，直到 BAG 回放服务启动。

4. 最后启动 BAG 回放，首次验收建议用正常速度：

   ```powershell
   cd D:\Project4_Frontend_Backend\SAN60_Windows_Offline_20261019
   .\Start_Replay.bat --port 8775 --speed 1
   ```

   确认桥接器持续出现 `sent sequence=... accepted=True` 后，再按需将倍速改为 `--speed 10`。桥接器只在 BAG 帧序号变化时转发新帧。

   如果后端使用自定义地址或 token，可覆盖参数：

   ```powershell
   python .\scripts\san60_offline_bridge.py --backend-url http://127.0.0.1:8081/api/integration/san60/spectrum --token uav-usv-local-agent
   ```

## 查看与检查

打开 `http://localhost:5174/vision`，点击右侧“电子探测仪 01”。选择“2D 频谱”查看频谱曲线、峰值和温度；选择“3D 态势”查看 BAG 里的实测空间轨迹和功率体素。三维画布可拖动旋转。GNSS 状态、经纬度、海拔和最新 ENU 局部坐标在二维摘要中显示。

也可以检查后端查询接口：

```powershell
Invoke-RestMethod http://127.0.0.1:8081/api/sensors/radar
```

桥接终端显示 `sent sequence=...` 表示当前序号已成功推送。停止回放后，后端按现有规则在 30 秒后将频谱标记为离线。

默认集成 token 来自后端配置 `app.integration.token`；若环境变量 `PLATFORM_INTEGRATION_TOKEN` 被设置，桥接命令的 `--token` 需要与该值一致。

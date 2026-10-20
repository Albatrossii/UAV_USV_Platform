# Unity–平台兼容层

## 当前算法仿真的实际构建路径（2026-10-14）

`/?workspace=simulation` 使用独立的 `unity-virtual-fleet`，不是下面旧任务中心的
`AlgorithmScenarioBridge.poseFrame`。实际链路为：

`applyPoseBatch → VirtualFleetScenarioController → VirtualFleetManager → Unity 显示`。

当前本机工程为 `F:/UVA_USV/UAV_USV_Unity_VirtualFleet`，编辑器为
`F:/Software/Hub/2022.3.57f1/Editor/Unity.exe`。本轮修改前该工程已有未提交的
岸边基础设施、标签和镜头修改，均已保留，不能用旧平台桥文件整目录覆盖。

本轮受控显示层源文件快照位于 `unity-tools/virtual-fleet/Assets/`，对应实际工程的
`Assets/` 路径。这是从上述工程当前工作树保留既有改动后生成的独立 overlay，
并非从旧 `unity-tools/Assets/` 兼容桥推导的替代文件。同步前先比较目标工程差异，
只合并对应文件；不要覆盖其他 Unity 业务、场景、地形或已有未提交改动。

- `VirtualFleetScenarioController.cs` 将 `sampleTime` 交给显示层。
- `VirtualFleetPosePlayback.cs` 采用 180 ms 时间戳缓冲和线性插值，不外推。
  普通阶段变化不重置位置；只有显式生成/重置允许立即定位。断帧后从屏幕上
  已有位置有限时地恢复，断流时保留最后位置，不外推运动。按界面要求，
  不在 Unity 右上角绘制即时模式同步提示框；连接状态统一保留在网页顶部。
- `VirtualFleetManager.cs` 保留最新权威期望位置，只将插值结果用于模型显示；
  不修改 Python 的位置、速度、到位或任务完成判定。
- `WebDeviceObserverCamera.cs` 保留 1.2 秒返回全景过渡及用户手动模式；全景
  纳入当前运行的 UAV、USV、被护船/敌船，不加入停机坪、泊位或遗留场景船。
  全景收缩需超过 6% 并持续 650 ms，扩张仍优先满足可见性。
- `VirtualFleetPlaybackValidation.cs` 提供 10 个确定性显示回归用例，
  `WebCameraTransitionValidation.cs` 保留既有 20 个镜头过渡用例。

验证并构建（输出到暂存目录，成功后再部署 `Build/`，保留现有集成 `index.html`）：

```powershell
$env:UAV_USV_WEBGL_OUTPUT = 'F:/UAV_USV_Platform1/.local-tools/smooth-build/unity-virtual-fleet'
& 'F:/Software/Hub/2022.3.57f1/Editor/Unity.exe' -batchmode -nographics -quit `
  -projectPath 'F:/UVA_USV/UAV_USV_Unity_VirtualFleet' `
  -executeMethod UavUsv.Editor.Tools.VirtualFleetPlaybackValidation.BuildValidated `
  -logFile 'F:/UAV_USV_Platform1/.local-tools/smooth-build/validation-build.log'
```

`BuildValidated` 先执行位姿显示、镜头过渡和原有岸边基础设施回归，再调用
`VueWebGlBuildTool.BuildVirtualFleet`。该构建只编译现有虚拟编队场景，不重建
场景布局。若只验证，将方法改为 `VirtualFleetPlaybackValidation.Run`。

以下章节描述旧系统总览/任务中心兼容桥，不能与上述独立仿真路径混用。

当前平台 WebGL 基线对应 Unity 提交：

- Unity commit：`f24959c32b072cc7f18ff815f6ab22a580fc531b`
- 平台桥版本：`unity-f24959c-platform-v2`
- 设备：3 UAV、3 USV
- 任务对象：`friendly_ship`、`enemy_ship`

## 边界

平台不得修改 Unity 地形、仿真流程或算法业务代码。所有前后端适配代码统一维护在：

```text
unity-tools/Assets/Scripts/UavUsv/PlatformTools/
```

更新 Unity 后，将该目录覆盖到隔离构建副本的：

```text
Assets/Scripts/UavUsv/PlatformTools/
```

再执行 `UavUsv.Editor.Tools.VueWebGlBuildTool.Build`。不要在未经验证时直接覆盖正式 Unity 项目。

## WebGL 能力握手

`UnityPlatformCompatibilityBridge` 在场景与各工具桥就绪后发送：

```text
platformBridgeReady
```

能力列表包括：

- `camera-control`
- `vehicle-control`
- `trajectory-telemetry`
- `local-capture-scenario`
- `algorithm-scenario`
- `visual-sensor`
- `gazebo-comparison`

Vue 只有收到完整就绪消息后才清空指令队列。Unity 画面加载成功不等于控制桥已经可用。

## 运行域

- `SYSTEM_OVERVIEW`：系统总览、设备观察、内置简单任务和 Unity 视觉传感器。
- `MISSION_CENTER`：任务中心独立算法运行，2D 与 3D 使用同一个运行批次。

两个运行域可复用同一套 WebGL 构建文件，但必须使用不同 iframe 与运行实例标识，不能共享任务状态。

## 相机协调

新版 Unity 默认启用 `GazeboComparisonCamera`。平台发出设备选择、网页相机切换、算法场景加载或算法位姿帧时，兼容桥会关闭固定 Gazebo 相机，避免其在 `LateUpdate` 中覆盖网页相机。切换到 `gazebo-comparison` 模式时可恢复该视角。

新版 `SimulationBootstrap` 只创建 Gazebo 场景，不再挂载本地围捕组件。`UnityScenarioCompatibilityInstaller` 会在运行时绑定现有三机三艇、目标船、停机坪和岸基节点，并让本地场景保持未启动状态，直到平台发送 `missionStart`。任务中心算法坐标保持局部坐标，3D 展示时只平移到 Gazebo 安全任务海域，2D 数据不改写。

## 更新后的最低验收

1. 六台设备均可选择，`uav_01/usv_01` 与 `UAV-01/USV-01` 均能识别。
2. 系统总览与任务中心运行域互不串联。
3. 围捕、护航的算法帧能同时驱动任务中心 2D 和 3D。
4. 六路视觉画面直接来自 Unity，无需等待 ROS 摄像头。
5. 隐藏/显示轨迹必须收到 Unity 的 `trajectoryVisibilityChanged` 确认。
6. 前端、后端和 Unity WebGL 构建全部通过后才可同步至正式项目。

## 紧凑设备标签 B 方案（2026-10-15）

`VirtualFleetDeviceLabelOverlay` 按设备编号、完整状态文字中较长一行的实际字体尺寸计算标签宽度；保留原有自适应字号，使用居中文字、1 像素细边框、轻量深色底。去掉类型/状态方块和连接线，选中设备保留金色边框。仅修改展示，不改变算法、设备位姿或相机逻辑。

验证入口为 `UavUsv.Editor.Tools.VirtualFleetLabelValidation.BuildValidated`：先验证 72 组标签尺寸（含不同缩放、密集设备和长状态），再执行原有播放、相机及返航基础检查并构建 WebGL。使用 `UAV_USV_WEBGL_OUTPUT` 输出到隔离目录，成功后仅替换前端 `unity-virtual-fleet/Build` 资源，保留平台集成 `index.html`。本次已在独立六设备 WebGL 场景确认实际紧凑标签效果。

## 全节点紧凑自动取景（2026-10-16）

算法仿真相机使用 `unity-tools/virtual-fleet/Assets/Scripts/UavUsv/PlatformTools/WebDeviceObserverCamera.cs`，不修改系统总览旧相机。默认全局视角改为 42° 斜俯视，逐帧包含当前全部 UAV、USV、护航目标和威胁目标的模型边界；待命、返航、停止的设备仍在取景集合内，不以隐藏远端节点换取放大效果。岸线、停机坪、泊位和旧场景设备不参与拉远镜头。

以透视投影约束求最紧凑中心，按实际宽高比、像素标签余量求距离；平滑中心后再次计算安全距离。向外扩展立即满足可见性，向内缩小保留 6% 阈值、650ms 稳定期及阻尼，避免反复拉近拉远。窗口、侧栏和节点条调整后使用实际相机视口重新拟合，不通过 CSS 裁切或移动算法节点实现放大。

设备跟随使用 52° 视场，并在有限范围内纳入附近节点；选中设备始终是观察主体，远离编队的节点不会把跟随视角拉成全局。岸基视角复用全节点拟合。原有鼠标交互与 1.2s 返回全局过渡保留：手动探索/从近景切回的过渡期间不保证全员同时入镜，自动全局稳定后保证完整模型取景。

新增验证/构建入口 `UavUsv.Editor.Tools.WebCameraFramingValidation.BuildValidated`：先检查取景几何，再运行原有标签、播放、镜头过渡及返航基础验证。输出目录末级应为 `unity-virtual-fleet`，构建成功后仅部署四个 Build 资源并更新仿真 iframe 的 build 标识。该改动不改变场景坐标、任务状态或护航/围捕/返航算法。

本次 Unity batch 验证通过：取景 17 项、标签 72 项、播放 10 项、镜头过渡 20 项、原场景返航基础 26 项，共 145 项；前端相机联动、桥协议和 WebGL 容器测试 36 项通过。

WebGL 已构建并部署到本地仿真资源（版本标识 `20261016-tight-all-nodes`），前端生产构建通过。浏览器实测了岸边待命、起飞离泊、编队移动、UAV/USV 跟随及返回全局，并在 1920×1080、1280×900、900×1080 窗口检查全局取景；验收后恢复默认尺寸和岸边待命场景。旧 Build 资源保留在 `.local-tools/all-node-camera-build/previous-build` 便于本地回退。

## 出发阶段单设备接管（2026-10-15）

岸边安全出发期间对单台设备下发“悬停/驻留/停止”，只接管指定设备，不再把它保留为全队集结屏障。其余设备继续完成安全起飞或离泊，完成后由剩余可用编组启动护航或协同围捕；被接管设备保持实际位置，收到“归队”后按限速路线加入当前实时编队。返航仍必须实际抵达对应停机坪或泊位后才算退出，不会因先前悬停而提前放行集结。

运行帧通过 `deploymentDeferredCount` 和 `deploymentDeferredDeviceCodes` 区分“人工接管但可归队”与 `deploymentWithdrawnCount` 的“已返航退出”。前端会显示指定设备保持当前位置、其余设备继续任务。护航和协同围捕均覆盖 UAV 悬停、USV 驻留、继续出发和归队回归。

## 原场景无人艇返航导航（2026-10-14）

`scene-existing-v1` 的 USV 返航由算法服务统一执行，护航和协同围捕使用同一实现；不改变任务核心算法、原泊位或 Unity 地形。固定外海入口不再是必经点：从实际位置在安全水域图中按预计航行耗时选路，接入泊位通道的有效前向段。近泊位 32 米内最高 1.5 m/s，最后 8 米最高 0.6 m/s，均不超过用户设定速度；返航转向最高 60°/s，正常加速及航点减速使用 0.8 m/s²。

- 数据：`algorithm-service/app/navigation/data/native_berth_water.json`，全球 ENU、2 米网格，范围 east `[-700,-40)`、north `[-600,100)`。每个安全格覆盖整个格内艇中心位置及 6 米船体净距；未知区域拒绝通行。`fleetOrigin` 只在导航接口转换一次。
- 地图来自原 Catalina 地形三角面和原建筑水线边界，不是凭空设定的岸线。仅额外认可经过验证的原进港中心线，不把中心线的船体净距误当成横向通行宽度。
- 动态船体阻挡会触发重规划；无安全路径时保持实际位置并输出 `WAITING_FOR_CLEAR_PATH`，不会直线穿岸、穿船或假报到港。暂停冻结返航；实际回到自己的泊位并对齐朝向后才输出 `RETURNED`。
- 已在泊位不会驶离再绕回；驻留后重新返航从当时实际位置规划。新代码由新生成场景的 Python 进程加载，已有运行不热替换路线，也不需要重建 WebGL。

变更地形、原基地几何或艇模型尺寸后，必须重新导出地图并回归，不能继续沿用旧安全认证。导出器为 `unity-tools/Assets/Editor/NativeBerthWaterExporter.cs`，Unity batch 入口 `UavUsv.Editor.Tools.NativeBerthWaterExporter.Run`，参数 `-nativeWaterOutput <JSON绝对路径>`；只在隔离验证场景计算，不保存场景或构建玩家。地图内记录地形依赖 hash 和安全判定口径。

回归覆盖见 `test_usv_return_planner.py`、`test_native_return_navigation.py`、`test_return_infrastructure.py`：两种算法、原泊位幂等、近岸返航、驻留后重发、暂停、三艇同时返航、船体净距、限速和转向连续性。人工复测须先停止旧运行并重新生成场景，再分别录音“一号无人艇返航”“二号无人艇返航”；应向安全进港路径行驶，不应无条件先驶向旧外海入口。

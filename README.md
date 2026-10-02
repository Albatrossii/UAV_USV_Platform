# UAV-USV 海空协同仿真与任务控制平台

面向无人机、无人艇协同感知与围捕任务的半实物仿真验证平台。当前工程统一管理 Spring Boot 后端、Vue 前端，并预留 ROS2/Gazebo、Unity3D、算法服务和任务控制的集成接口。

## 目录

- `backend`: Spring Boot 3.5 + Java 17 + MySQL
- `frontend`: Vue 3 + TypeScript + Vite
- `scripts`: 本地联合启动脚本
- `docs`: 项目架构、模块规划和后续开发说明

## 项目文档

- [总体架构说明](docs/architecture.md)
- [前后端模块规划](docs/frontend-backend-roadmap.md)
- [任务管理模块设计](docs/mission-management-design.md)

## 当前开发状态

已完成：

- 登录与管理员会话
- 系统总览科幻态势页
- 设备管理模块
- 运行监控模块
- ROS WebSocket 位姿接入
- Unity 心跳接入
- 本地运行控制的只读/安全启动框架

下一阶段优先开发：

- 任务管理 / 任务配置
- 任务阶段与事件日志
- 通信网络监控
- 协同围捕过程展示
- 实验评估与回放

### 后端分层约定

后端采用按业务模块组织、模块内按职责分层的结构：

```text
com.uavusv.platform
├─ common
│  ├─ api                        # 统一 API 响应格式
│  └─ exception                  # 错误码、业务异常与全局异常处理
├─ config                         # Spring 与 Web 配置
└─ module
   ├─ system                     # 系统基础模块
   ├─ device                     # 设备管理模块
   ├─ monitoring                 # 运行监控模块
   └─ runtimecontrol             # ROS/Unity 运行控制模块
      ├─ controller              # HTTP 接口层
      ├─ dto
      │  ├─ request              # 请求参数对象（按需创建）
      │  └─ response             # 接口响应对象
      ├─ entity                  # JPA 数据库实体
      ├─ repository              # 数据访问层
      └─ service
         └─ impl                 # 业务接口及实现
```

后续的任务、通信、围捕、评估、回放等业务均在 `module` 下建立独立模块，不跨层直接调用。

所有业务接口统一返回 `code`、`message`、`data` 和 `timestamp`，异常由全局异常处理器转换为标准错误响应。

### 初始登录

- 登录地址：`http://localhost:5174/login`
- 初始管理员：`admin`
- 初始密码：`123456`

初始管理员仅在数据库中不存在同名用户时创建，密码使用 BCrypt 保存。部署到其他环境时应通过配置覆盖初始账号并在首次登录后修改密码。

### 设备管理模块

前端入口：登录后进入左侧菜单 `设备管理`。

后端接口：

- `GET /api/devices`：分页查询设备，支持 `keyword`、`type`、`status`、`page`、`size`
- `GET /api/devices/{id}`：查询单个设备
- `POST /api/devices`：新增设备，仅管理员可用
- `PUT /api/devices/{id}`：修改设备，仅管理员可用
- `DELETE /api/devices/{id}`：删除设备，仅管理员可用

当前设备类型包括 `UAV`、`USV`、`ROS_NODE`、`UNITY_NODE`。历史迁移中保留了 `LIGHTHOUSE` 类型兼容字段，但当前设备管理页面不再展示导航灯塔登记项。

演示任务的标准设备编组为三架无人机与三艘无人艇：`uav-01`～`uav-03`、`usv-01`～`usv-03`。六台载体均参与 `MT-20260624-001` 三机三艇协同围捕任务；ROS 与 Unity 节点作为基础设施单独统计。

## IntelliJ IDEA

1. 用 IntelliJ IDEA 打开本目录。
2. 等待 Maven 识别 `backend/pom.xml`。
3. 等待前端依赖索引完成。
4. 运行根目录 npm 脚本 `dev`，可同时启动前后端。

也可以分别启动：

- 后端：运行 `PlatformApplication`
- 前端：在 `frontend/package.json` 运行 `dev`

## 默认地址

- 前端：http://localhost:5174
- 后端：http://localhost:8081
- 健康接口：http://localhost:8081/api/system/health

## ROS / Gateway 可选连接

平台页面和本地算法仿真不依赖 ROS/Gateway 在线。默认关闭 Gateway v1 与视觉传感器外连，任务控制默认走浏览器 Unity 通道；Gateway 不可达时，摄像头、ROS 遥测和对应的远端控制功能会显示离线，但不会阻止前后端启动或其他页面使用。本地配置模板也默认关闭这些外连。

需要接入 Gateway 时，按实际部署显式设置 `ROS_GATEWAY_V1_ENABLED=true`、`VISUAL_SENSOR_WEBSOCKET_ENABLED=true` 和对应的 WebSocket 地址；同时按目标选择 `MISSION_DISPATCH_MODE=ros-gateway-v1` 与/或 `COMMAND_DISPATCH_MODE=ros-gateway-v1`。legacy ROS 位姿链路由 `ROS_TRANSPORT=legacy` 与 `ROS_ENABLED=true` 启用。各链路开关彼此独立，切换或断开其中一条不会自动切换到另一条控制通道，也不会把本地仿真命令转发到 ROS。

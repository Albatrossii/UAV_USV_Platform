# 项目说明文档索引

本目录用于记录 UAV-USV 海空协同仿真与任务控制平台的开发约定、模块边界和后续迭代方向。

## 文档列表

- [方案 A 统一控制台与节点监测版本备注](releases/20261015-layout-a-node-monitor.md)：2026-10-15 的界面与监测变更、验证结果、备份范围和恢复方法。
- [architecture.md](architecture.md)：对齐 PPT 的五层软件架构，说明当前项目在 Spring Boot、Vue、ROS2/Gazebo、Unity3D 和算法服务中的定位。
- [frontend-backend-roadmap.md](frontend-backend-roadmap.md)：梳理前后端已有模块、下一阶段模块和接口开发顺序。
- [mission-management-design.md](mission-management-design.md)：任务管理 / 任务配置模块的数据库、后端、前端和接口设计草案。
- [execution-and-command-roadmap.md](execution-and-command-roadmap.md)：说明仿真会话、任务执行批次、控制指令确认和后续开发顺序。

## 维护原则

- 算法、Unity/WebGL 和页面交互修改必须遵循 [工程化画面验收规范](engineering-visual-acceptance.md)：核对连续动作、状态逻辑与截图，分别给出功能、视觉和性能结论，不以“显示完成”代替验收。

- 先写清楚模块边界，再写代码。
- 前端页面必须围绕业务闭环，不做只有静态装饰的大屏。
- 后端模块保持企业级 MVC 分层结构。
- ROS/Unity 接入先做只读监控和状态回传，再逐步做控制命令。
- 危险的启动/停止/进程控制必须有明确状态判断和人工确认。

## 本地联调环境

- [local-runtime-environment.md](local-runtime-environment.md)：记录当前开发机的 Windows、WSL2、ROS2、Unity、Java、Node、MySQL 等环境信息，并说明其他电脑拉取代码后需要配置的本地路径和运行参数。

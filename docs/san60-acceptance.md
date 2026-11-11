# SAN60 集成验收记录

验收时间：2026-11-10 晚至 2026-11-11 凌晨，Windows x64 本机。

开发目标仅为 `UAV_USV_Platform`；未修改 `_latest` 副本。原有 Maven wrapper、Unity 构建文件、Element Plus 类型声明等未提交修改均保留；组件声明文件仅由现有 Vite 插件补充新组件类型。提交准备时仅纳入本次 SAN60 修改，原有修改继续留在本地。

## 验证结果

| 检查 | 实际结果 |
|---|---|
| 源包原生启动 | 使用源包 Python 3.13.16 / NumPy 2.2.6 / SciPy 1.15.3，环境检查和 BAG 头检查通过；浏览器独立页面可见频谱、GNSS、体素和轨迹 |
| 完整 BAG 回放 | 58,567 帧频谱、1,179 个空间测量点、60 个实测体素、479 个插值体素（默认 200 cm） |
| 源位置限制 | 原服务返回「轨迹接近直线，源位置约束不足」，`source=null`；平台保留原结论 |
| 完整状态转发 | 停止状态下比对平台与 Python 的完整 `spatial` 对象一致，没有旧桥接的数组抽样，坐标保持 m |
| 导航 | 展开和折叠导航均新增同级「态势感知」，位于「光电视觉」之后；独立路由需登录 |
| 回放控制 | 浏览器实际操作回放、10× / 100×、停止、清空；源包独立 1× 也完成完整回放 |
| 停止后设置 | 停止采集后将体素 200 cm 改为 50 cm，状态即更新，体素数变为 82；改回 200 cm 后恢复 60，不依赖新频谱序号 |
| 参数验证 | 无效频段、0.005 m 体素和 101× 均被接口拒绝；前端另有输入验证 |
| 图层和视角 | 浏览器操作插值与轨迹开关、恢复和重置视角；单元测试验证更新不改变用户相机和坐标范围 |
| JSON 导出 | 浏览器下载后解析通过：完整帧数、轨迹、体素、原始单位、源位置限制均正确 |
| PNG 导出 | 浏览器下载后检查 PNG 文件头与尺寸，1800 × 1100，322,749 字节 |
| 外部 JSON | 本地合成测试频谱和同步 GNSS 通过平台进入 Python，形成空间测量，页面切为外部输入模式 |
| 外部 WebSocket | 本地合成 rosbridge 测试服务收到两个订阅，频谱进入平台并持续形成轨迹；切换模式关闭连接 |
| 模拟模式 | 顶部「模拟演示 · 合成数据」，图中模拟轨迹和体素明确标识；计算仍使用原包演示逻辑 |
| 断线与恢复 | 实际停止 Python，页面保留最后画面并提示非实时；用新脚本恢复同端口，无需刷新页面即自动重连 |
| 原光电视觉 | 实际导航回原页面，原入口、七路信号源和 Unity WebGL 在线画面仍存在；未修改原视图业务代码 |
| 窄屏 | 760 px 验证，页面宽度 745 px，无水平溢出；测量区与控制区改为纵向布局 |
| 一键启动与退出 | 在临时端口 SAN60 18775 / 后端 18081 / 前端 15174 实际启动完整链路，Vite 代理正确；请求正常退出后三个端口全部释放，正式验收端口不受影响 |
| 端口占用 | 单服务和整体启动脚本均明确拒绝占用端口，没有终止原服务 |
| 会话与 CSRF | 未登录查询返回 401；登录后无 CSRF 的控制请求返回 403；带有效 CSRF 可正常控制 |

## 自动化检查

- 后端 `San60ServiceTests` 和 `SensorRuntimeServiceTests`：15 项通过。
- 前端绘图、页面控制、布局与导航：23 项通过。
- 源包 `test_cm_engine.py`：4 项通过；`test_cm_display.js` 通过，包含 cm 显示、1 cm 设置、功率不变和视角保持。
- 最终 `npm --prefix frontend run build` 通过，包含 Vue / TypeScript 类型检查及 Vite 生产构建。
- `node --check scripts/san60-dev.mjs` 和本次已跟踪文件的 `git diff --check` 通过。
- 后端本机启动连接现有 MySQL，21 个 Flyway 迁移验证通过，没有新增数据库迁移。

本次运行的是与 SAN60 集成有关的测试，未将此记录表述为仓库所有测试均通过。

2026-11-11 提交前复核：上述后端 15 项、前端 23 项测试与前端生产构建再次通过。新版一键启动默认使用 `8775 / 8081 / 5177`，语音开关和可选算法虚拟环境保持原流程，端口配置与脚本语法已检查。先前完整链路的浏览器验收仍为上表记录，本次提交前未重复整套浏览器操作。

可复查命令：

```powershell
.\backend\mvnw.cmd -o -f backend\pom.xml '-Dtest=San60ServiceTests,SensorRuntimeServiceTests' test
npm --prefix frontend test -- src/components/san60/plot.spec.ts src/views/SituationalAwarenessView.spec.ts src/components/layout/ConsoleLayout.spec.ts src/components/layout/ConsoleNavigationRail.spec.ts
npm --prefix frontend run build
```

## 修改清单

| 部分 | 文件及用途 |
|---|---|
| 后端 | `San60Service.java` 转接本机服务、处理连接和原服务校验错误；`San60Controller.java` 暴露状态、控制、输入和绘图库接口 |
| 配置 | `backend/src/main/resources/application.yml` 增加 `SAN60_SERVICE_URL`，默认 8775 |
| 页面 | `frontend/src/views/SituationalAwarenessView.vue` 独立页面及操作；`api/san60.ts`、`types/san60.ts` 接口和类型 |
| 绘图 | `components/san60/plot.ts` 复用原包网格、频谱、色阶与相机逻辑；`San60Plots.vue` 管理绘图及资源释放 |
| 导航 | `router/index.ts`、`ConsoleLayout.vue`、`ConsoleNavigationRail.vue`；组件类型声明增加新图表组件 |
| 启动 | `scripts/start-san60.ps1`、`scripts/san60-dev.mjs`、根目录 `package.json` |
| 测试 | 后端服务测试、前端绘图及页面测试、现有导航测试更新 |
| 说明 | 本文件、`san60-situational-awareness.md`、`san60-启动流程-更新.md`、旧桥接说明链接与截图目录 |

没有把 Python runtime、BAG 或 8 MiB Plotly 文件复制进前端。计算源包保持原有代码，Plotly 由平台后端从该包按需提供。

## 截图

提交版本中的登录账号区域已遮盖为 `USER`，并移除图片元数据；保留测量与模拟模式标识及功能验收画面。启动说明不保存实际集成令牌，令牌由本机配置、环境变量或交互输入提供。

- [平台完整 BAG 测量](san60-screenshots/01-platform-bag.jpg)
- [外部输入（本地合成验收源）](san60-screenshots/02-external-input.jpg)
- [服务断开与历史画面提示](san60-screenshots/03-disconnected.jpg)
- [原光电视觉页面](san60-screenshots/04-optical-preserved.jpg)
- [760 px 窄屏布局](san60-screenshots/05-narrow-layout.jpg)
- [最终完整页面](san60-screenshots/06-final-platform.jpg)
- [最终页面首屏](san60-screenshots/07-final-viewport.jpg)

## 范围与现场待验事项

本次本地软件集成已完成。Windows USB 驱动和采集桥不在原包中，本次未增加；实物 SAN60、现场 GNSS、真实远端 WebSocket/rosbridge 及远程生产部署仍需现场环境验收。本地 WebSocket 检查使用明确标记的合成测试源，不代表实物采集验收。

源包这段 BAG 的轨迹约束不足，未产生可信源位置估计；没有放宽原算法阈值以制造估计结果。高倍速为源包的目标回放倍速，实际吞吐也受本机计算和绘图负载影响。

光电视觉中的旧 SAN60 入口继续使用原桥接链路，如需同时给该入口供数，仍可按 `san60-offline-integration.md` 启动旧桥接器。独立态势感知页直接通过新的后端适配接口工作。

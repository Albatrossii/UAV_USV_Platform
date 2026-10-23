# 方案 A 统一控制台与节点监测版本备注

本版本将算法仿真页面切换为方案 A 的左侧统一控制台，并接入真实设备节点监测。场景配置、任务控制、相机和语音执行能力保持原有业务逻辑；本次不改动护航与协同围捕的核心算法。

日期：2026-10-15

仓库：https://github.com/Albatrossii/UAV_USV_Platform

工作分支：`codex/voice-control-next-stages-20261010`

备份标签：`backup/20261015-layout-a`

上一版本：`5a1a36d`（岸边任务控制与语音流程）

## 界面与交互

- 将导航、语音控制、场景配置和平台节点合并为左侧控制台，扩大右侧 Unity 仿真区域。
- 语音与场景配置默认展开，平台节点默认折叠；各模块独立折叠，也可整体收起总控。
- 数量、速度、算法选择、生成场景与重置继续保留；算法名称保持“智能粒球仿真护航”和“GB-SFLA-CS 协同围捕”。
- 语音卡片采用紧凑布局，删除“一句话，调度海空设备”，改为“点击录音”等功能性提示；保留权限、执行范围、编辑确认、错误和执行回执。
- 折叠不卸载语音组件、不清空输入，也不发送任务控制命令。为总控尺寸变化增加 Unity 画面位置跟踪，覆盖窄窗口折叠、模块展开及回执引起的布局变化。
- 其他页面保留原导航布局与折叠偏好。

## 节点监测

- 平台节点与当前场景目标在同一折叠卡片中展示；目标不计入通信节点在线数量。
- 按设备编号关联实际运行节点，支持节点详情、心跳新鲜度和异常筛选；Unity 仿真心跳不作为真实机艇在线证据。
- 监测按周期更新，处理超时、失效快照和页面生命周期，未连接或数据不可用时不伪造在线状态。
- 移除独立平台通道展示及“检测与分级说明”。
- 信号形状的条形图表示心跳新鲜度，不代表无线信号强度；节点 RTT 和超时率尚未采集，显示缺省值，不宣称已完成 4G、5G 或语义通信的实测。

## 验证结果

2026-10-15 发布前复验：

- `cd frontend; npm test`：44 个测试文件、1183 项测试通过。
- `cd frontend; npm run build`：类型检查与生产构建通过。依赖中存在非阻塞的 PURE 注释位置警告。
- 源码差异检查通过；既有交接讨论稿的 3 处 Markdown 硬换行双空格按原文保留。
- 浏览器检查：单侧总控、精简文案、折叠保留输入、窄窗口下 Unity 位置对齐及默认窗口布局已验证。

本次是前端布局与监测发布，未新增实机通信验收，也未重复执行麦克风录音和完整护航／围捕任务人工验收。

## 备份范围

GitHub 工作分支与带说明的备份标签定位同一版本。本机另保存自包含 Git bundle，并记录提交号与 SHA-256 校验值；实际备份文件信息见本机备份目录内的 `backup-manifest.txt`。

备份包含已提交源码、测试、文档及仓库中原有的 Unity WebGL 产物。既有的 [20 条控制指令交接稿](../算法执行交接-初版20条指令-v0.1-20261014.md) 原样纳入，其中的 `chase`、`bind` 等仍是需求与接口讨论，不表示本版本实现了这些命令。

本地凭证和机器配置（如 `.env.local`、本地后端配置）、数据库内容、依赖目录、构建缓存、运行日志和 `.local-tools` 临时演示文件不纳入本次 Git 备份。恢复到新电脑后需要重新配置本地运行环境与凭证。

## 恢复方法

从 GitHub 在新目录检出备份版本，不覆盖正在使用的工作区：

```powershell
git clone --branch backup/20261015-layout-a --single-branch https://github.com/Albatrossii/UAV_USV_Platform.git UAV_USV_Platform-layout-a-restore
cd UAV_USV_Platform-layout-a-restore
git switch -c codex/restore-layout-a-20261015
```

离线恢复时，在本地备份文件所在目录执行：

```powershell
git clone --branch codex/voice-control-next-stages-20261010 UAV_USV_Platform-layout-a-20261015.bundle UAV_USV_Platform-layout-a-restore
git -C UAV_USV_Platform-layout-a-restore bundle verify ../UAV_USV_Platform-layout-a-20261015.bundle
```

恢复后在 `frontend` 执行 `npm ci`、`npm test`、`npm run build`，并按 [本地运行环境](../local-runtime-environment.md) 配置服务。新目录名称须不存在，避免覆盖现有工程。

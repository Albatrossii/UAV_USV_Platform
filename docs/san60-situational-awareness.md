# SAN60 独立态势感知模块

入口：左侧「光电视觉」之后的「态势感知」，地址 `/situational-awareness`，需要平台登录。原光电视觉页面及其电子探测仪入口保留。

## 启动

与你之前截图对应的完整操作步骤见 [更新后的启动流程](san60-启动流程-更新.md)。一键脚本默认沿用原后端语音、浏览器 Unity 和本地传感器配置，并在存在时使用已有算法服务虚拟环境；显式设置的环境变量优先。

推荐在 `UAV_USV_Platform` 目录运行一个命令，统一启动 SAN60、现有平台后端和前端：

```powershell
npm run dev:san60
```

脚本启动 SAN60（手动输入模式）、现有 Spring Boot 后端和 Vite 前端。默认端口分别为 `8775`、`8081` 和 `5177`，并保留原语音前端启动器使用的功能开关。待后端就绪后打开 `http://127.0.0.1:5177/login`，登录后可从左侧进入「态势感知」或原有平台模块。Ctrl+C 会清理本次脚本创建的进程。端口被占用时会明确报错，不会终止其他服务。前后端依赖、JDK 17、Node 20.19+ 和项目现有 MySQL 配置需可用。

一键启动不需要旧 SAN60 桥接器。「态势感知」新页面通过平台后端直接连接 SAN60。若还需要给原「光电视觉」内的电子探测仪卡片供数，再按后文单独启动桥接器。

默认源包位于平台目录的同级路径 `SAN60/SAN60_Windows_Offline_20261019`。运行环境和约 801 MiB BAG 留在源包，不复制到前端，也不需要安装 Python 包。

```powershell
$env:SAN60_PACKAGE_ROOT = 'D:\Project4_Frontend_Backend\SAN60\SAN60_Windows_Offline_20261019'
$env:SAN60_PORT = '8775'
$env:VITE_DEV_PORT = '5177'
npm run dev:san60
```

已有前后端运行时，可以只启动 SAN60：

```powershell
npm run start:san60
# 或指定目录、端口、初始模式：
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-san60.ps1 -PackageRoot 'D:\SAN60\SAN60_Windows_Offline_20261019' -Port 8775 -Mode manual
```

单独启动后端时通过 `SAN60_SERVICE_URL=http://127.0.0.1:8775` 配置服务地址（修改后重启后端）。支持 HTTP 回环地址，不将 Python 文件服务对外暴露。端口必须与 SAN60 启动参数一致。源包 launcher 有端口自动回退机制，启动脚本会预先检查占用；若仍遇端口竞争，以启动日志为准。

## 功能与数据

- 开始回放使用源包附带的 BAG；也可填入 **SAN60 服务所在电脑** 的 BAG 完整路径。更改倍速后点击「开始回放」会重新开始回放。
- 停止采集保留历史测量。清空测量清除已积累空间点；输入仍在运行时会继续积累新点。重新回放或启动模拟演示会按源包逻辑重置测量。
- 频段和体素设置通过 Python 原接口生效；更换频段会清空原频段空间测量。停止后也能更新设置，不依赖频谱序号增长。
- 实测体素、近邻插值、轨迹和透明度由页面控制。绘图复用源包 Plotly 的 mesh3d/scatter3d、色阶、视角和频谱逻辑。初次出现空间点时适配视角，后续更新保留手动视角；点击「重置视角」重新取景。
- 热点是高功率测量位置；源位置是原 Python 算法在覆盖、拟合误差及稳定性满足条件时给出的模型估计。条件不足时展示原因，不伪造源位置。
- 图片保存为 PNG；JSON 导出为完整原始状态，包含频谱、空间数据、GNSS、模式和单位。导出坐标仍是 **m**，三维画布与体素输入是 **cm**，换算只发生在显示或提交设置时。厘米显示不代表厘米定位精度。
- 模拟演示明确标记为合成数据，BAG 标记为实测回放，外部输入有独立模式。
- 外部 JSON 支持对象、数组和逐行 JSON。发送前自动停止回放。字段遵循源包 `接口说明.md`，频谱与有效 GNSS 时间差不超过 1.5 秒才产生空间点。
- 外部 WebSocket 由浏览器连接，可订阅 rosbridge 的 `/san60/spectrum` 和 `/san60/gnss/status`，收到的数据通过平台后端送入 Python。离开页面会断开该浏览器外部连接；BAG 回放继续运行。WebSocket 端点需能被浏览器访问，HTTPS 部署应使用 WSS。
- 此包没有 Windows USB 驱动或采集桥；本次没有添加实物 USB 支持。

## 接口与生命周期

```
Vue 页面 → 已登录的 Spring Boot /api/san60/* → 本机 Python 原服务
```

| 平台接口 | Python 原接口 | 用途 |
|---|---|---|
| GET `/api/san60/state` | GET `/api/state` | 完整状态，包含连接状态，不缓存、不按序号过滤 |
| POST `/api/san60/control` | POST `/api/control` | bag / stop / clear / settings / demo |
| POST `/api/san60/ingest` | POST `/api/ingest` | 外部输入，沿用源包验证 |
| GET `/api/san60/plotly.min.js` | GET `/plotly.min.js` | 使用原包离线绘图库，后端内存缓存 |

接口沿用现有会话认证和 CSRF。后端请求使用 Python 自身 Host，不转发浏览器 Origin，兼容原服务的本机访问校验。页面约每 750 ms 顺序拉取一次完整状态，断线时降低频率并自动重连；保留上次画面并明确提示非实时。离开页面取消拉取、断开 WebSocket、释放 Plotly 与 ResizeObserver。

开发环境继续使用 Vite 的 `/api` 代理。生产环境需将同域 `/api` 转到平台后端，并为前端路由配置 history fallback。SAN60 跟随平台后端部署在同一台 Windows 电脑上，远程浏览器无需访问自己的 localhost。不要将 `standalone.html` 当成无服务的完整计算程序。

## 验收

1. 源包独立启动，确认能读取 BAG、频谱、有效 GNSS、轨迹和插值体素。
2. 平台登录后，检查两种导航中的新菜单位置和独立路由；光电视觉原入口仍可使用。
3. 回放 1× / 10×，停止，修改体素并应用，确认停止状态下也更新；错误频段与越界体素应显示错误。
4. 检查图层、重置视角、PNG 和完整 JSON 导出；模拟演示的合成数据标识明确。
5. 停止 SAN60 服务，检查断线提示和历史画面；恢复同端口后自动重连。
6. 用带同步 GNSS 的外部 JSON 验证外部输入；真实设备及其 rosbridge 服务需在现场另行验收。

本次实际运行记录和截图见同目录 `san60-acceptance.md`。

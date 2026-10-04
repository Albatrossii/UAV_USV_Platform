# 阿里云语音控制快速启动

本说明用于另一台 Windows 电脑从 Git 分支启动“阿里云一句话识别 + 本地 Python 仿真 + Unity WebGL 展示”。凭据只保存在本机，不进入 Git。

## 1. 拉取代码

```powershell
git fetch origin
git switch --track origin/codex/aliyun-asr-integration
```

如果本地已经有该分支：

```powershell
git switch codex/aliyun-asr-integration
git pull
```

## 2. 安装依赖

- JDK 17 或更新的兼容版本；`java` 需要在 `PATH`。
- Node.js 20.19 或更新版本，包含 `npm`。
- MySQL 8；提前创建空数据库 `uav_usv_platform`，表由 Flyway 自动迁移。
- Python 3，并安装 `algorithm-service/requirements.txt`。
- FFmpeg；放入 `PATH`，或稍后在本机配置中填写绝对路径。

首次拉取后安装前端依赖：

```powershell
npm --prefix frontend install
```

安装 Python 算法依赖示例：

```powershell
python -m pip install -r algorithm-service/requirements.txt
```

## 3. 创建本机配置

```powershell
Copy-Item .\config\aliyun-demo.example.psd1 .\config\aliyun-demo.local.psd1
```

编辑 `config/aliyun-demo.local.psd1`，填写三项阿里云配置，并按本机情况核对数据库配置：

- `AliyunAppKey`
- `AliyunAccessKeyId`
- `AliyunAccessKeySecret`
- `MysqlUrl`
- `MysqlUsername`
- `MysqlPassword`（本机数据库无密码时可以留空）

AccessKey 应属于 RAM 用户，并具有 `nls:CreateToken` 权限，例如授予 `AliyunNLSSpeechServiceAccess`。不要使用主账号 AccessKey。`aliyun-demo.local.psd1` 已在 `.gitignore` 中，仍禁止把它复制到聊天、日志或提交记录。

脚本故意不接受命令行形式的 AccessKey，避免凭据进入 PowerShell 历史和进程命令行。手工临时 Token 留空；后端会自动申请、缓存并刷新 Token。

建议为 `ResultEncryptionKey` 生成并保存一份固定的 32 字节 Base64 值，以便后端重启后恢复加密保存的识别结果：

```powershell
$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes); [Convert]::ToBase64String($bytes) } finally { $rng.Dispose() }
```

只把输出填入被忽略的本机配置。该项留空也能启动，但脚本会生成仅对当次运行有效的内存密钥并显示警告。

## 4. 预检与启动

只检查环境，不启动服务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-aliyun-demo.ps1 -CheckOnly
```

完整启动：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-aliyun-demo.ps1
```

如果默认端口已被其他项目占用，可以只对本次启动覆盖端口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-aliyun-demo.ps1 -BackendPort 8083 -FrontendPort 5177
```

默认地址：

- 前端：`http://127.0.0.1:5174/?workspace=simulation`
- 后端健康检查：`http://127.0.0.1:8081/actuator/health`
- 初始登录：使用本机配置中的 `BootstrapAdminUsername` 和 `BootstrapAdminPassword`

启动脚本会检查 Java、Node、npm、Python 算法包、FFmpeg、MySQL TCP 连接、依赖文件和端口占用。检查输出只显示路径与“凭据已配置”，不会显示任何凭据值。按 `Ctrl+C` 停止前台服务；默认端口也可另开 PowerShell 执行 `npm run stop`。

## 5. 最小验收顺序

1. 登录后进入 `/?workspace=simulation`，选择算法并生成场景。
2. 先上传或录制“开始任务”，确认页面显示阿里云转写文字和成功执行回执。
3. 依次测试“暂停任务”“继续任务”“停止任务”。
4. 新建运行并启动后，测试“第一架无人机悬停”“第一架无人机返航”。
5. 核对被接管设备是 `UAV-001`，其余设备仍执行群体算法。

如果只出现文字而不执行控制，请确认是通过本页脚本启动，而不是普通的 `npm run dev`。如果返回 `VOICE_PROVIDER_UNAVAILABLE`，先检查 AppKey、RAM 权限、AccessKey 和网络；如果返回 `VOICE_NO_SPEECH`，说明云端链路可用，但音频中没有识别到有效语音。

## 6. 当前边界

- 当前是“一句话录完再识别”，不是边说边返回的流式识别。
- 本地仿真不要求 ROS/Gateway 在线；真实设备控制仍需单独配置和验收。
- 首次自动取 Token 与缓存逻辑已通过；真实 Token 临近过期时的长时间现场刷新仍需补一次运行验收。
- 启动脚本为本地开发/演示入口，不替代服务器生产部署和密钥托管方案。

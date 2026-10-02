# 语音提速与单设备控制并行开发接口约定

日期：2026-09-27  
基线：本地集成提交 `aeb1f98`，含 SAN60 optical 与 P1 本地语音分支

## 分支与交付边界

两条工作线从同一集成基线分别开发，互不直接修改对方文件：

| 工作线 | 本地分支 | 允许的主要改动 |
| --- | --- | --- |
| 本地 ASR 提速 | `codex/asr-latency-optimization` | `asr-service/` 的解码、性能日志和同录音基准工具；仅在需要呈现计时指标时修改 ASR 结果展示 |
| 单设备控制 | `codex/single-device-control` | `frontend/src/components/control/`、专用单设备命令适配器，以及现有视图中的设备命令提交调用点；必要时 `backend/.../runtimecontrol/` 的定向校验与回执适配 |

## ASR 接口契约

- 浏览器至后端的既有入口保持 `POST /api/voice/intelligence/transcriptions`。
- 请求仍由音频 Blob、语言、唯一 request ID 组成，并使用 `X-Request-ID` 与 `Idempotency-Key`；成功仍返回 `VoiceTranscript`：识别文字、语言、音频长度、provider 和模型标识。
- ASR 工作线只负责把相同输入音频转成文本并报告耗时。它不解析控制意图、不选择设备、不调用语音命令接口，也不改变任务或设备状态。
- 性能诊断只记录阶段耗时、音频长度/字节数、参数档位和匿名请求标识；不得记录音频、转写文本、令牌或认证信息。
- 比较不同 beam 档位必须对同一音频运行，记录端到端和推理耗时、逐字转写差异。当前 beam 2 默认值基于一段包含四条任务指令的 10.24 秒录音；两档转写一致，但样本有限，仍需扩展说话人和环境样本持续验收。

## 单设备控制接口契约

- 前端继续通过既有 `POST /api/runtime-control/commands` 发出命令；请求沿用 `RuntimeCommandRequest` 的 `commandType`、单一 `deviceCode`、运行范围和运行实例标识，返回沿用 `RuntimeCommandResponse` 的 `commandKey` 与命令状态。
- 单设备路径必须只带一个有效设备编号。设备类型、在线/遥测新鲜度、运行实例和动作允许状态由后端控制服务校验；执行成功以现有 Gateway/Unity 命令回执为准，不能以 HTTP 接收成功冒充设备执行成功。
- 设备控制工作线不新增绕过 `RuntimeControlService` 的直接 ROS/WebSocket 控制链路，不改语音转写/解析入口，不改任务状态机或任务生命周期 API。
- `DashboardView` 和 `MissionExecutionView` 中允许调整单设备命令提交这一小段调用适配；不得修改任务创建、开始、暂停、恢复、完成等状态流转。
- 任务状态仍由现有任务与运行时服务维护；单设备控制只提交定向设备命令并读取其回执，不直接写任务状态。

## 集成规则

- 两条工作线分别提交，先各自在自己的分支构建，再通过接口契约评审后集成。
- 公共入口、设备命令 DTO、任务状态机若确需变更，应单独提交并说明迁移影响；不得把这类变更夹带进 ASR 参数优化。
- 原 ASR 基线为 2.16 秒音频、beam 5、推理约 53.3 秒、端到端约 54.3 秒。随后用一段 10.24 秒录音依次说出“开始任务、暂停任务、继续任务、停止任务”进行同音频对照：beam 5 中位推理 15.860 秒，beam 2 为 12.287 秒；两档都与人工给出的四条指令顺序一致（忽略标点）。据此将 beam 2 设为默认；样本覆盖目前限于该录音，后续应继续观察其他说话人和环境下的准确率。

## 当前分支集成检查（2026-09-28）

以 `codex/p1-integration-edit`（当前 `4146cdf`）为集成目标，对本地缓存提交完成合并检查：

| 分支 | 当前可见提交 | 检查结论 |
| --- | --- | --- |
| `codex/p1-integration-edit` | `4146cdf` | 当前集成目标；已包含 ASR 与单设备控制提交 |
| `codex/asr-latency-optimization` | `89ec9c8` | 已通过合并提交 `4146cdf` 集成；GitHub 最新状态尚未刷新确认 |
| `codex/single-device-control` | `ae3fcdb` | 已包含在当前集成目标中 |
| `codex/single-device-simulation-integration` | 本地无引用 | GitHub 当前不可连接，尚不能确认远端分支状态或检查合并情况 |

ASR 与单设备控制提交都涉及本文件；现已合入，并保留了性能数据、接口边界和待检查项。`codex/optical-vision-san60-integration` 的本地远端跟踪提交 `fb11d6b` 已位于当前目标分支历史中，无需重复合并。

### 本地集成验证

- 前端 `npm test`：96/96 通过；`npm run build` 通过。构建仍提示主 JS chunk 约 789 kB，Rollup 也提示两个第三方 `PURE` 注释位置不可解析。
- ASR Python 语法检查通过；不依赖音频容器的 Hash/HTTP 子集 13/13 通过。完整 21 项运行中，10 项通过、11 项因当前 Python 环境缺少 `av` 包报错；需在锁文件环境补齐依赖后重跑。
- 后端尚未运行测试：当前 Java 为 1.8.0_171，项目要求 Java 17。

### 待完成

1. GitHub 连接恢复后，刷新远端分支引用并检查 `codex/single-device-simulation-integration` 是否存在及其相对基线的提交差异。
2. 当前本地改动已从 stash 恢复并保留备份。分别验证语音识别、设备命令和仿真状态的接口。
3. 切换到 Java 17 并在 ASR 锁定依赖环境中补跑后端和 ASR 完整测试，记录实际结果及剩余验收工作。

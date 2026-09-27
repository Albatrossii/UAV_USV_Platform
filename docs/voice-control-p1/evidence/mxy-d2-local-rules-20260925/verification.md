# D2 本地规则解析工作区验证记录

日期：2026-09-25（Asia/Shanghai）

分支：`codex/p1-integration-edit`

HEAD：`680e0f5`

状态：本地未提交验证记录；不是 GitHub 基线或最终验收报告。

## 验证范围

- Java 本地解析器 `local-rules/rules-v1`，仅生成候选，不调用算法 Runner 或 Unity。
- 解析 HTTP MockMvc、安全与幂等边界、D1 ASR HTTP 回归、P0 提案来源关联。
- 前端语音控制右侧面板、ASR 输入和 P0 来源恢复相关测试及生产构建。

## 结果

| 检查 | 结果 |
|---|---|
| 后端定向回归：IntentService、Intent HTTP、提案来源关联、ASR HTTP | 60 次测试执行，0 失败、0 错误、0 跳过 |
| 后端全量 Maven 测试 | 927 项，0 失败、0 错误、180 项按环境条件跳过，`BUILD SUCCESS` |
| 前端 Vitest | 11 个测试文件，92 项通过（含本轮新增旧协议上下文回归） |
| 前端类型检查与生产构建 | 通过（Vite 有依赖注释与 chunk 体积提示） |
| `git diff --check` | 通过 |

定向的 60 次执行包含 `VoiceInterpretationSourceTests` 继承的 P0 测试，不代表 60 条彼此独立的 D2 用例。全量测试中的 MySQL、真实 Runner 和 JVM 故障窗口等条件测试未在本轮启用。首次将前端测试与慢速 Runner 用例并行时出现一次 I05 5 秒超时；停止并发后前端全套单独重跑为 91/91 通过。

## 覆盖映射（自动化层）

| D2 用例 | 当前证据 | 限制 |
|---|---|---|
| D2-01～03 登录、CSRF、ADMIN | `IntentHttpTests` | MockMvc 身份，不是真实登录会话 |
| D2-04 标识一致、重复头、查询参数 | `IntentHttpTests` | MockMvc 请求 |
| D2-05 文本、未知字段、数组校验 | `IntentHttpTests`、`IntentServiceTests` | 仍需与正式冻结 Schema/样例做契约联跑 |
| D2-06 四种动作 | `IntentHttpTests` | 本地规则，不是真实 LLM |
| D2-07～11 否定、歧义、无动作、不支持能力/目标 | `IntentServiceTests` | 规则样例集仍需团队复核边界语言 |
| D2-12 上下文代次/能力校验 | `IntentServiceTests` | 单测/H2 运行上下文 |
| D2-13～14 幂等重放与冲突 | `IntentHttpTests`、`IntentServiceTests` | D2 缓存在内存，Java 重启会清空候选 |
| D2-15 候选用户、动作、代次、过期校验 | `IntentServiceTests`、`VoiceInterpretationSourceTests` | 浏览器跨账号实测未做 |
| D2-16 提案幂等键绑定来源 ID | `VoiceInterpretationSourceTests` | 服务层测试 |
| D2-17 候选只创建待确认提案 | `VoiceInterpretationSourceTests` | H2 服务层测试，不代表真实 DB 证据 |
| D2-18 显式确认后才排入执行队列 | `VoiceInterpretationSourceTests`、P0 服务测试 | 未连接真实 Runner/Unity；不得自动点击确认 |

## 仍未完成

1. 在新开的 `http://127.0.0.1:5177/login` 手动登录后，生成隔离的算法预览运行，确认 Java—Python `algorithm.command.v1` Runner 就绪；再用 STOP 候选创建 `AWAITING_CONFIRMATION` 提案。确认后会进入 P0 执行队列，必须由操作者在页面明确确认。
2. 本轮没有验证真实 LLM、双账号浏览器或 Java 重启后的 D2 候选恢复。浏览器没有展示解析 requestId，因此本次现场记录未留存该 ID。

## 隔离联调服务准备（2026-09-25）

- 新隔离后端：`http://127.0.0.1:8082`，从当前工作区源代码临时副本启动，连接本机 `uav_usv_platform`；`/actuator/health` 返回 HTTP 200。D2 开关进程级启用，`VOICE_CONTROL_ENABLED=false`。数据库口令、集成令牌和 bootstrap 占位密码仅通过进程环境提供，没有写入仓库或日志。
- 新隔离前端：`http://127.0.0.1:5176/login`，GET 返回 HTTP 200；只在 Vite 进程启用 P1 preparation/backend、关闭 Mock，并代理到 8082。
- 未登录向 D2 endpoint POST 返回 HTTP 401，鉴权门正常阻止匿名请求；这不是登录后的业务解析验收。
- 原有 `8081`、`5175`、`18081`、Python ASR `18082` 未被停止或替换。本轮没有新启动 Python Runner、没有操作 Unity 控制，也没有开启 P0；浏览器中原有 Unity WebGL 仅用于显示。

## 登录后浏览器检查（2026-09-25）

- 用户已在隔离页面完成登录；页面显示账号 `admin`、角色 `ADMIN`。没有读取或记录登录凭据。
- 页面当前算法运行上下文显示 `PREPARED`、心跳失效、场景 `WAITING`、8 台设备；语音控制按钮提示该实例为旧协议且不支持 P0。Unity WebGL 画面在线不等同于后端算法 Runner 就绪。
- 修复前两次输入“暂停当前任务”后点击解析，组件因旧协议运行上下文变化而重置；未将这两次尝试计为通过。
- 前端现已将非 `algorithm.command.v1` 上下文从解析请求中解绑，但仍用它阻止提案提交和动作控制。修复后真实页面经 Java 平台接口再次解析，显示 `PAUSE / MISSION_PAUSE` 候选，状态为“已生成候选；创建冻结提案后仍需人工确认”。
- 候选下方按钮显示“旧协议实例不支持 P0 指令”且为禁用状态；四个 P0 动作按钮也都禁用。未创建提案、未确认、未生成 execution、未下发算法命令。当前运行仍为 `PREPARED`、心跳失效、场景 `WAITING`，算法上下文不支持 P0；WebGL 在线只代表画面载入，不代表 Runner 或 Unity 场景就绪。
- 后端健康检查为 HTTP 200；日志持续有 ROS Gateway v1 连接拒绝告警，未记录可关联本次解析的 requestId。浏览器没有在候选卡片展示 requestId，因此现场证据记为：**真实页面解析与候选展示 PASS；requestId 留证和候选→提案→确认 BLOCKED/未验收**。
- 修复回归：`VoiceP0ControlPanel.spec.ts` 定向测试 11/11 通过；前端全量 11 个测试文件、92/92 通过；`npm run build` 中 `vue-tsc -b` 与 Vite 构建通过（保留依赖注释及大 chunk 提示）。
- 下一步要创建 STOP 待确认提案，仍需有效且心跳新鲜的 `algorithm.command.v1` Runner 上下文；START/RESUME 提案还需要 Unity 场景就绪证据。当前没有点击确认或发出控制动作。

## P0 预览提案浏览器检查（2026-09-25）

- 前端 `http://127.0.0.1:5177/?workspace=simulation` 登录成功，页面显示 `admin / ADMIN`。没有读取或记录登录凭据。
- 生成场景后，P0 面板显示算法运行 `1790346768629`、`PREPARED`、帧 1、心跳正常；Unity Virtual Fleet WebGL 在线，但系统总览 Unity 一直显示 `WAITING FOR PLATFORM`，P0 场景状态仍为 `WAITING`。因此本轮没有 START/RESUME，更不能据此声称 Unity 就绪。
- 真实文本解析输入“停止当前任务”两次，输入在提交时被组件清空，并提示“操作员已切换或运行上下文变化，请重新输入指令”；另有系统总览“仍在等待系统总览平台初始化”提示。未观察到候选卡片或解析 requestId，解析验收记为 **BLOCKED/未通过现场验收**，其根因仍待查，不能把后端自动化结果替代真实 HTTP 页面结果。
- 为继续检验提案和人工闸门，改用 P0 面板独立“停止任务”按钮创建待确认 STOP 提案（这条证据不代表“解析候选→提案”闭环通过）。页面显示提案 `AWAITING_CONFIRMATION`，动作 STOP，运行 `PREPARED`，设备快照 6 台：`UAV-001..003`、`USV-001..003`；运行实例 `d55443fd-4613-4b73-a09c-bde68994b92d`，代际 `37ee5321-6495-4fdd-bd48-5c59e2fd5154`，计划版本 `v1 / voice-p0.v1`，哈希 `94948c88937c2840e47c22c4fbe0823212cb5036cc2ba85f8f236d83ebcd689e`。
- 首个提案 `77292a3f-4701-45bd-b560-5589d189e63f` 和续建提案 `72a26a3b-a483-4616-b520-fb80c6472c38` 自然过期。第三个提案 `cf90a272-cade-4691-aaed-b5549377322d` 页面显示待确认后，由用户在页面完成人工确认（本 agent 未点击确认按钮），数据库状态变为 `CONFIRMED`。execution `ba9a045c-d929-4ef5-9284-75d7d71f725f`、command `1b53fcb8-8277-4f82-b985-38ee3007ac8c`，数据库状态 `SUCCEEDED / SUCCESS`；命令事件序列 1=`ACCEPTED`、2=`SUCCEEDED`，outbox=`SENT`，runtime=`STOPPED`，最后心跳 `2026-09-25T14:52:48Z`。页面显示“算法执行成功 停止任务 · 算法结果 SUCCESS · 展示状态无需 Unity 同步”。没有 START/PAUSE/RESUME 或 Unity 展示报告。Runner 原始 stdout、PID、exit code 未保存，进程级证据缺失。
- 曾尝试额外启动 Java 8083 实例，但端口被进程 PID 9988（Java，21:48:15 启动）占用，额外启动失败；没有停止该既有进程。实际页面请求已成功写入本机 `uav_usv_platform` 数据库，因此本轮需要按“复用了当时已监听 8083 的进程”记录，不应声称是本轮新启动的隔离 Java 实例，也不应在未确认该进程用途前将其关闭。
- 最新提案已由用户人工确认并成功执行 STOP；详情见下方“最新修复与候选闭环”。

## 最新修复与候选闭环（2026-09-25）

- 根因：`VoiceIntelligenceInput` 原本用 `watch(() => [a,b,c...], callback)` 监听运行身份。Vue 将返回的新数组按单一对象比较；父组件刷新 `/api/voice/contexts` 会重建上下文对象，运行 ID、代际、版本都没变化时仍触发回调，导致输入被清空。临时 UI 诊断两次确认重置前后 `runtimeRef=7f85643d…`、`generation=38e20b46…`、`contextVersion=3`、`inputDisabled=false` 完全相同。
- 修复：改成 Vue 多源监听，按 `operatorScope`、权限禁用状态、`runtimeRef`、`runtimeGeneration`、`contextVersion` 逐项比较。只有任一实际身份/权限值改变时才清空输入与候选；移除临时诊断文案。
- 新增回归：仅替换上下文对象、身份和版本不变时，解析候选与输入保留；generation 真正变化时仍清候选。定向 `VoiceIntelligenceInput.spec.ts` 11/11 通过。
- 修复后真实浏览器 `5177` 重新建立 PREPARED Runner：`algorithmRunId=1790348341125`，`runtimeRef=7f85643d-5de7-4549-bd7a-6a0cf9d594e9`，`runtimeGeneration=38e20b46…`，`contextVersion=3`。输入“停止当前任务”生成 `STOP / MISSION_STOP` 候选，页面不再清空输入。
- 候选到提案：提案 `86a7675d-5c69-40bd-bae4-425c5e6fcfa6` 中记录 `interpretationId=b33fa7aa-792e-4855-9ebb-a1c58a54bdb6`，动作 STOP，后由用户在页面人工确认；agent 未点击确认。
- 执行证据：execution `00c1a559-94be-465a-b10d-e1ab068aafe5`，command `c72e93dc-fc7d-4205-9d21-581f3c147a3f`，状态 `SUCCEEDED / SUCCESS`；outbox `SENT`；Runner 回执序列 `ACCEPTED` → `SUCCEEDED`，运行状态 `STOPPED`。未发 START/PAUSE/RESUME。
- 以上证明本地规则解析器（`local-rules / rules-v1`）候选→提案→用户确认→STOP 回执闭环，不是 LLM 验收。系统总览 Unity 仍为 `WAITING FOR PLATFORM`、P0 场景状态 `WAITING`；没有验证 Unity `SCENE_READY` 或画面同步。Runner 原始 stdout、PID、exitCode 未保存，进程级证据仍缺失。
- 最终验证：`VoiceIntelligenceInput.spec.ts` 定向 11/11 通过；此前并行全量测试出现一次 I05 5 秒超时，I05 单文件复测 4/4 通过；随后按单 worker 串行运行全量前端测试，11 个测试文件 93/93 通过。`npm run build` 中 `vue-tsc -b` 与 Vite 生产构建均通过，仅保留依赖注释和 chunk 体积提示。

## 后续浏览器联调补充（2026-09-26）

- `5177` 页面以 `admin / ADMIN` 登录。生成隔离预览运行 `1790392952512` 后，页面显示 `PREPARED`、心跳正常；运行日志记录 `11:22:37 scenarioReady: success`、8 个初始位姿及 `algorithm prepared: ESCORT_GUARD`。
- 输入“停止当前任务”后，真实页面显示 `STOP / MISSION_STOP` 候选。用户在页面手动确认冻结提案；随后 UI 显示“算法执行成功 停止任务”，Runner 算法结果 `SUCCESS`，其展示状态为无需 Unity 同步。运行日志在 `11:22:59` 记录 `missionStop / missionStop:1790392979170:cw8mob`，并同步运行状态为 `STOPPED stateVersion=1`。
- 协议面板显示 Unity WebGL `ONLINE`、协议 `V3`、模式 `VIRTUAL_SIMULATION`，场景确认 `READY`；但同一 `missionStop` 的 Unity `commandAck` 为 `success=false`、`code=mission_state_conflict`、`runId=""`、`runtimeScope=VIRTUAL_FLEET`。因此 Runner 的 STOP 成功不能当作 Unity STOP 回执成功；系统总览 Unity 仍显示 `WAITING FOR PLATFORM`，P0 上下文的场景状态仍为 `WAITING`。
- 本次页面没有展示 proposalId、interpretation requestId、executionId 或 commandId；日志中的 `missionStop` 标识不是这些资源 ID，不能替代它们。没有保存 Runner 原始 stdout、PID 或 exit code，因此进程级证据仍缺失。
- 本次没有执行 START/PAUSE/RESUME，也没有通过 P0 展示绑定完成 `SCENE_READY` 或画面同步验证。停止后运行心跳失效，不能再对该运行创建新提案。
- 本补充证明本地规则解析器的浏览器候选及经用户确认的 STOP 执行链路现场通过；不代表真实 LLM 验收、Unity 就绪或画面同步通过。

## D3 本地离线 LLM 接入续验（2026-09-26）

- 复用本机已缓存的 `Qwen2.5-1.5B-Instruct Q4_K_M` 和 llama.cpp CPU 服务，没有重新下载模型或二进制。模型 SHA256 与此前验收留档一致：`6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`。
- 本机 `127.0.0.1:18083` 的既有服务 `/v1/models` 返回 HTTP 200；真实推理输入“结束当前任务”返回结构化标签 `STOP`。推理请求只发送到回环地址，不访问云端。移除了本轮重复启动的第二个模型进程，原有进程保持运行。
- 将此前 D3 的 Java 适配器接回当前 D2 服务：模型只能产生有限标签，Java 仍校验危险能力、单设备目标、否定语句、动作白名单及当前运行能力；LLM 只生成候选，不调用 Runner 或 Unity，之后仍需人工确认。保留当前 D2 幂等缓存容量的并发复核。
- 在临时 ASCII 路径副本上执行 `mvn -DskipTests package`，186 个主 Java 源和 49 个测试源编译成功，测试执行阶段明确跳过，Spring Boot 包装成功。直接在中文工作区路径编译时 protobuf 原生工具无法解析中文路径；本轮没有覆盖活动后端构建产物。
- 浏览器当前 `5177` 仍连接此前启动的 D2 Java 进程。真实页面对“结束当前任务”仍显示规则版“需要澄清”，表明活动 Java 进程尚未加载新适配器和 `local-llm` 配置。其启动环境中的数据库口令和集成令牌不可从本会话读取，因此本轮没有重启服务、创建或确认提案，也没有发出设备命令。
- `application-local.example.yml` 已列出本地适配器地址、模型别名、超时及通过进程环境注入 token 的配置项。切换服务时应设置 `APP_VOICEINTELLIGENCE_INTENT_PROVIDER=local-llm` 和 `APP_VOICEINTELLIGENCE_LLM_TOKEN` 后重启；token 不得写入仓库。

## 安全边界

本轮没有改管理员密码、没有清库、没有改远端分支、没有提交或推送。曾尝试另起 `8083` Java 进程但因端口占用失败；没有停止 PID 9988，也没有确认该进程环境开关。提案创建通过真实已监听后端写入本机数据库；STOP 提案由用户在页面人工确认，agent 未点击确认按钮。

## 后端启动补充与进度检查点（2026-09-26 12:47，Asia/Shanghai）

本节更新前述“尚未重启后端”的历史状态；此前各阶段的验证边界仍保留。

- 用户要求启动更新后的后端。旧 8083 后端及其 Maven 启动进程已停止，当前工作区的新 JAR 已在前台终端启动，进程 PID 5128；日志明确记录 `Tomcat started on port 8083` 与 `Started PlatformApplication`。
- 启动沿用本机已有数据源及本地 profile 配置，数据库连接成功；Flyway 报告数据库版本 20、无需迁移。凭据仅在本机进程环境中使用，未纳入仓库。
- 新进程配置 `APP_VOICEINTELLIGENCE_INTENT_PROVIDER=local-llm`，模型地址为 `http://127.0.0.1:18083`，模型为 `qwen2.5-1.5b-instruct-q4_k_m`。前述模型直连推理和 Java 打包结果是已有证据；本次启动不代表页面经过新 Java 适配器的 LLM 完整闭环已经验收。
- 右侧 `5177` 联调页保留；语音面板显示登录状态失效，需要重新登录并恢复新鲜的算法运行上下文。尚未完成本地 LLM 候选→提案→人工确认→算法回执的新一轮验收。
- ROS Gateway v1 持续连接失败。当前语音任务流程使用独立的 `algorithm.command.v1` 通道，可继续本地模型与算法仿真联调；依赖 ROS 网关的设备指令、遥测与回执闭环仍未就绪。
- 本次进度保存没有重新运行测试套件。最近的 Java 打包为 `mvn -DskipTests package` 成功；前端及规则解析链路的历史验证见上文，不能替代新增 LLM 链路验收。
- 下一步：重新登录，恢复算法运行上下文，验证本地 LLM 页面候选及人工确认链路；随后处理 Unity 展示绑定、场景就绪和 ROS 网关连接，并补齐运行进程与命令回执证据。

## 离线模型页面解析与仿真配置修复（2026-09-26 13:23，Asia/Shanghai）

- 用户要求暂缓 ROS 网关连接，先推进其他联调；本轮未操作网关服务。
- 登录有效的 `5177` 页面输入“结束当前任务”，首次解析因浏览器 12 秒等待阈值显示超时；点击“使用原请求恢复查询”后，页面显示 `STOP / MISSION_STOP` 候选。此时后端为已配置 `local-llm` 的进程；这证明页面解析链路产生了候选，但本次未保存该响应的模型元数据或请求编号，也未生成提案或执行动作。
- 对齐前端等待阈值至 25 秒，以覆盖 Java 默认 20 秒模型请求期限及响应处理。新增可折叠的解析来源、模型及请求编号显示，方便后续把真实响应与提案来源关联；此次更新后的真实页面解析仍待重新登录后验证。
- 页面提示重启前的算法实例不存在。通过现有重置入口恢复预览后，新运行 `1790399661177` 被注册为 `legacy`，界面阻止 P0 提案。代码核对确认原因是上一轮启动沿用 8082 配置时保留了关闭状态的 `VOICE_CONTROL_ENABLED`。
- 正常退出旧后端进程并启动 PID 22168，命令行明确设置 `app.voicecontrol.enabled=true`，保留本地模型及数据源配置。13:21:27 日志记录 8083 启动成功，Flyway 无需迁移。该开关启用现有的受人工确认保护的仿真提案流程，不跳过确认、代际或场景就绪校验。
- 重启后页面明确跳转到登录页，已在右侧保留给用户。尚未创建新协议预览、生成 LLM 待确认提案或确认执行。
- 定向前端回归使用 `npm.cmd test -- src/api/__tests__/voiceIntelligence.spec.ts src/components/voice/__tests__/VoiceIntelligenceInput.spec.ts src/components/voice/__tests__/VoiceP0ControlPanel.spec.ts --maxWorkers=1 --pool=threads`，3 个测试文件、35 项全部通过。初次普通运行遇到 `spawn EPERM`，提升权限后默认进程 worker 启动超时且未运行测试；使用单线程 worker 后通过，不能把之前两次启动失败计作断言通过。

## 登录后本地模型候选与冻结提案（2026-09-26）

- 用户重新登录后，通过现有重置入口重建预览。新运行 `1790400319531` 为 `PREPARED`、帧 1、心跳正常；STOP 提案可用，START 仍因 Unity 场景未就绪被阻止。
- 本次 Runner PID 为 `8292`，父进程为 Java `22168`；启动命令明确包含 `--command-protocol v1`，运行实例 `10f3652f-81a6-4d75-9ab0-b2e916205410`，代际 `8df212a2-5368-4846-920f-e2d073430074`。后端记录 Python 3.13.15。此处仅保存启动身份，不代表已采集原始 stdout 或退出码。
- 页面输入“结束当前任务”，在更新后的等待预算内返回 `STOP / MISSION_STOP` 候选，本次未触发浏览器超时恢复。展开解析来源，页面明确显示“本地离线模型”、`qwen2.5-1.5b-instruct-q4_k_m`，请求编号 `e11d9d94-22ca-445c-bfc2-8c0764f993a9`。
- 从该候选点击“生成待确认提案”，页面显示冻结计划：停止任务、运行 `1790400319531 / PREPARED`，成员快照 6 个：`UAV-001..003`、`USV-001..003`。页面明确提示只有点击“确认执行”才下发；已交给用户手动确认，agent 未点击执行。
- 此记录证明真实离线模型经页面解析并进入冻结提案。执行结果需以用户确认后的新回执补充，不能引用面板历史 STOP 成功记录替代。
- 首份提案在等待人工操作期间自动过期，页面显示 `EXPIRED` 并禁用“确认执行”；未把该提案计为已确认或执行成功。
- 用户反馈已过期后，重新解析得到同模型的 STOP 候选，请求编号 `afe01192-3548-4338-8ecf-75f03637b4d4`，用于重建待确认提案。执行结果仍需另行核对。

## 本地 Qwen → 仿真 STOP 闭环结果（2026-09-26 13:35，Asia/Shanghai）

- 第二次解析关联提案 `1c4eb360-ff5b-4e45-8ce5-7f6f331edb63`，数据库记录 `interpretationId=afe01192-3548-4338-8ecf-75f03637b4d4`、`status=CONFIRMED`。agent 仅尝试点击生成提案，没有点击“确认执行”；随后页面显示本轮运行 STOPPED 及算法 SUCCESS，已用只读数据库查询关联本轮候选、提案和回执。
- execution `7c5a4830-4576-4799-9bc4-954c3fbead12`，command `bc1ac406-8be2-47f8-9acc-e01f0a19ea69`，状态 `SUCCEEDED`，outbox=`SENT`，展示状态=`NOT_REQUIRED`。
- 回执序列 1=`ACCEPTED`、2=`SUCCEEDED`；成功回执记录 `runtimeState=STOPPED`、`stateVersion=1`，`affectedDeviceCodes` 为 3 架 UAV 与 3 艘 USV。接收时间分别为 `2026-09-26T05:31:38.013405Z`、`2026-09-26T05:31:38.393198Z`。
- 后端终端在 `2026-09-26T13:31:39.260+08:00` 记录 `Algorithm process exited`，关联运行 `1790400319531`、PID `8292`、`exitCode=0`。已留存启动身份与退出结果；未保存 Runner 原始 stdout 全文。
- 只读关联证据保存于 [llm-stop-20260926.json](llm-stop-20260926.json)，同时保留首份过期提案且无 execution 的记录。本轮不是沿用历史规则解析成功记录。
- 结论：真实页面使用本地离线 Qwen 解析文本→候选→冻结提案→确认→Python 仿真 STOP→成功回执已验证。范围是 PREPARED 仿真任务的 STOP；未验证本轮语音录音转写、START/PAUSE/RESUME、Unity 场景就绪及画面同步，也未连接 ROS 网关或控制真实设备。

## Unity 场景确认续验（2026-09-26）

- 复现并修复 `VoiceP0ControlPanel` 的监听问题：同一运行的心跳刷新会替换 context 对象，原先返回数组的单 getter 监听因此清空未完成的展示 challenge 与 READY 状态。改用逐项比较的多源监听，运行身份或 Unity 场景发生变化时才重新绑定。
- 新增回归覆盖心跳刷新保留 challenge、同一身份不重复接管、sceneRevision 改变后清除旧 challenge 并接管新场景。修复前该用例失败（challenge 意外为 null）；修复后单线程定向检查 12/12 通过。
- 核对现有 WebGL 的提交来源：`4ea9074` 部署展示桥，后续 `31a1e2b` 收紧边界；已有验收文档标注 Unity 源提交 `ab1f8a0ca946c24c85abcb4388efd27e5156f226`。页面入口已有小写的 `presentation_hello` / `presentation_probe` 转发，构建数据中也有 `unity.presentation.v1` 与对应响应类型。仅搜索大写常量而判断入口缺失是错误的，已通过实际内容、构建记录和真实页面纠正。
- 本机 Unity 2022.3.57f1 缺少 WebGL 构建模块。已准备独立源代码副本，并从官方版本页下载约 560 MiB 的对应模块到忽略目录，Authenticode 状态 Valid；没有运行安装程序、改动原 Unity 工程或覆盖现有 WebGL。隔离 Unity batchmode 检查因 LicensingClient IPC 连接超时退出（日志 return code 199），没有完成编译；本轮最终复用仓库已有构建。
- 重启本机 5177 前端，代理继续指向 8083；仅本次进程启用 `VITE_VOICE_UNITY_PRESENTATION_V1=true`，本地模型输入保持开启，MOCK 与 ASR_ONLY 关闭。没有重启后端、修改登录凭据或连接 ROS 网关。
- 重新打开真实页面后自动建立预览运行 `1790402623851`，状态 PREPARED、帧 1、心跳正常。展示绑定建立，页面从 WAITING 进入 READY，并显示“Unity 展示会话已就绪”；P0 开始任务按钮解除 SCENE_NOT_READY 门禁。没有把旧 `scenarioReady` 回调伪造成展示证据，也没有直接写入场景就绪状态。
- 本节证明新预览的 Unity 场景确认已连通；尚不代表本地模型 START/PAUSE/RESUME 执行或 FRAME_APPLIED 已验收，最终动作仍需用户手动确认。
- 真实页面输入“开始当前任务”，返回 `START / MISSION_START` 候选；展开来源显示 `local-llm` 对应文案“本地离线模型”、模型 `qwen2.5-1.5b-instruct-q4_k_m`，请求编号 `c16716cc-b431-4ee6-883e-1f044130d2c1`。该记录仅为 START 候选，执行成功及画面应用需在人工确认后另行核对。
- 后续刷新右侧页面时，界面显示运行 `1790402623851` 为 `RUNNING`，仿真进度约 7%、帧 116，执行卡片显示“算法执行成功 开始任务 / SUCCESS / Unity 已应用”。记录了现场观察。代理未点击“确认执行”；由于页面提案已过期、重新生成按钮状态曾变化，本地数据库尚未关联该 START 提案 ID、执行 ID 与回执，也不能仅凭观察判定是谁提交了确认。后续继续联调前应核对这组执行记录；当前仿真已在运行。

## 本地语音自动提交实现（2026-09-26）

- 按用户要求，录音停止后自动请求 ASR；仅 `local-asr` 成功且非空的转写会自动送入解析器。只有 `local-llm` 返回单一白名单候选、当前用户为管理员、P1 与 Python 仿真门禁通过时，才自动创建冻结提案并调用原幂等确认接口。
- 自动提案需匹配原解析编号、动作、运行实例与代际；提交后状态文案还核对 execution 与冻结提案的编号、动作和运行身份。提案/确认结果不明确时停止，不换幂等键重试。
- 不明确、否定、不支持或 ASR 失败时保留转写文字并提供重新输入路径；文字手输仍不自动下发。MOCK、ASR_ONLY、ROS/真实设备范围以及非 Python 仿真不会进入自动语音执行。
- 当前仿真页面没有启动新的录音或命令。此次代码和文档修改尚未运行构建、自动化检查或真实麦克风端到端验证；本记录只说明实现范围，不代表联调通过。

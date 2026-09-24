# P1-D3 本地 LLM 阶段验收记录（2026-09-24）

## 当前结论

- `Qwen2.5-1.5B-Instruct Q4_K_M` 已在 CPU 本机服务启动，监听 `127.0.0.1:18083`。
- 模型 SHA256 与官方值一致：`6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e`。
- llama.cpp `b11147` Windows CPU 包 SHA256：`b6776a3bfd46ac027d177782e80260c196ed069698e9a80c5d2c4bf62eb7a219`。
- 7 个直接模型核心样例全部通过；本机单次分类约 2–3 秒。
- 真实前端代理 → Java → 本地 LLM HTTP 检查 10/10 通过，证据见 `d3-real-http-20260924.json`。
- Java 定向回归 52/52 通过：失败 0、错误 0、跳过 0。
- 真实浏览器链路已通过：`文字 → 本地 LLM 解析 → 候选 → 冻结提案 → 人工确认 → STOP SUCCEEDED`。
- 前端 `http://127.0.0.1:5175`、Java `http://127.0.0.1:18081`、LLM `http://127.0.0.1:18083` 已启动。

## 已验证范围

四动作和“进入工作状态”“结束当前任务”等表达返回候选；多动作、无动作、危险能力、单设备与否定句被安全拦截；同键同内容返回原结果，同键不同内容返回 `IDEMPOTENCY_CONFLICT`。所有成功结果均带 `provider=local-llm` 和固定模型别名。

模型只返回 Schema 约束的单标签，Java 再生成公共状态与固定中文提示。非回环配置、429、超时、非法结构和不重试由 6 项适配器测试覆盖；D2 解析、HTTP 安全与 P0 来源关联由其余 46 项定向测试覆盖。

补充执行了未带分组的 Maven 全量测试命令。该命令会混跑需要专用前置脚本、报告文件、真实 Runner 崩溃窗口及不兼容 H2 迁移环境的测试，结果为 942 项中 4 个失败、17 个错误、110 个跳过；失败集中在 `VoiceJvmCrashWindowTests`、`VoicePrepareDeadlineTests`、`VoiceStopStabilityTests` 和既有 `PlatformContextIntegrationTests`，不涉及本次 D3 类。该命令不能替代项目已经冻结的分组验收流程，也不计为 D3 失败；D3 相关 52 项已单独重跑并全部通过。

## 真实浏览器验收

使用隔离数据库中的临时 ADMIN 账号，在算法仿真右侧面板输入“结束当前任务”。页面先显示 `STOP / MISSION_STOP` 候选，并明确候选不会直接执行；点击“生成待确认提案”后仍需人工点击“确认执行”。最终页面显示运行状态 `STOPPED`、算法结果 `SUCCESS`、Unity 展示状态 `NOT_REQUIRED`。

数据库只读关联结果为：

- `interpretationId=b0e2eea8-9b0c-4436-931e-fea16b82b533`
- `proposalId=b147de21-35ee-41d7-ab56-663fcfc7e387`
- `executionId=c0a3c2c2-8f3b-4059-94f6-db0435577ffe`
- `commandId=71775caa-6cca-4aba-8b06-4d748863de5b`
- `runtimeRef=ae32ff92-0935-4f3a-8461-aa27d507733b`
- `runtimeGeneration=578795b7-b256-49c5-9455-d214a655fc5a`

安全边界同时得到验证：第一次候选提案超过 30 秒后变为 `EXPIRED`，未生成 execution；P0 手工 STOP 独立创建不带 `interpretationId` 的提案；重建运行上下文后，第二次候选提案在有效期内确认并成功执行。浏览器已退出登录，临时账号已从隔离数据库删除。结构化证据见 `d3-browser-acceptance-20260924.json`。

## 仓库与外部文件边界

仓库只保存 Java 适配器、测试、启动检查脚本和脱敏证据。模型权重、llama.cpp 二进制、API key、数据库密码和本机日志均留在 `.local-tools`，不得提交。

# D2 本机真实浏览器联调证据（2026-09-24）

## 环境

- 分支：`mxy/p1-intent-20260923`
- 验证起点：`e1544144e11f140cc89b0a40ef922a0c7739ba51`
- 前端：`http://localhost:5175/?workspace=simulation`
- Java 后端：`http://127.0.0.1:18081`
- Python ASR：`http://127.0.0.1:18082`
- 数据库：本机 MySQL 隔离库 `uav_usv_p0_integration`，Flyway V20
- 算法执行端：真实 Python Runner
- 展示端：Unity WebGL

本文件不包含账号密码、内部令牌、录音或模型文件。

## 独立文字解析

通过登录后的前端代理和真实 Java 接口验证 9 个输入：

| 输入类别 | 预期结果 | 结果 |
| --- | --- | --- |
| START / PAUSE / RESUME / STOP | `CANDIDATE` 及匹配动作 | PASS（4/4） |
| 否定句 | `NOT_ACTIONABLE / NEGATED_ACTION` | PASS |
| 多动作歧义 | `NEEDS_CLARIFICATION / AMBIGUOUS_ACTION` | PASS |
| 无支持动作 | `NEEDS_CLARIFICATION / NO_SUPPORTED_ACTION` | PASS |
| 不支持能力 | `UNSUPPORTED / UNSUPPORTED_CAPABILITY` | PASS |
| 单设备指令 | `UNSUPPORTED / UNSUPPORTED_TARGETING` | PASS |

解析器返回 `provider=local-rules`、`model=rules-v1`。解析阶段没有直接创建 execution，也没有直接调用 Runner。

## 真实浏览器闭环

操作链路：

`输入“开始任务” → 解析指令 → START 候选 → 生成待确认提案 → 人工点击“确认执行” → Runner SUCCEEDED → Unity REPORTED_APPLIED`

页面最终显示：

- 运行状态：`RUNNING`
- 算法结果：`SUCCESS`
- 展示状态：`Unity 已应用`

关联证据：

| 字段 | 值 |
| --- | --- |
| algorithmRunId | `1790205421511` |
| runtimeRef | `4678880a-0219-422d-bff9-84385433fb2b` |
| runtimeGeneration | `ec4511f8-2b41-446d-9a90-9d87e74c14b0` |
| interpretationId | `7ccf8266-6e59-4df4-8978-2189d0b3007c` |
| proposalId | `d59e096e-9025-4761-945d-dd65e02a2fa2` |
| executionId | `bf9f9cf9-8930-4af3-ba6c-81a908642b0b` |
| commandId | `367a0550-a8cc-4a4d-a9f6-a99a161958ad` |
| bindingId | `25cb584c-3b4f-45bd-abc5-23b5ef187a63` |

数据库记录显示 proposal 为 `CONFIRMED` 且保留 `interpretationId`；execution 为 `SUCCEEDED / SUCCESS / REPORTED_APPLIED`。Runner 事件 1 为 `ACCEPTED`，事件 2 为 `SUCCEEDED`，最终 `runtimeState=RUNNING`、`stateVersion=1`，六台任务设备与冻结计划一致。

## 联调中发现并修复的问题

前端此前使用数组作为 Vue `watch` 的返回值。运行上下文轮询会替换同值对象，导致监听回调反复执行，文字输入及解析结果被清空。修复后，重置条件只由操作员、权限、`runtimeRef` 或 `runtimeGeneration` 的实际变化触发；同一代次的 `contextVersion` 刷新不再清空输入，提案时仍由后端执行当前版本校验。

验证结果：

- `VoiceIntelligenceInput.spec.ts`：11/11 PASS
- `npm run build`：PASS
- 输入后等待超过一次上下文轮询：内容保持，解析按钮仍可用
- 真实浏览器候选、提案、人工确认、Runner 和 Unity 闭环：PASS

## 四动作连续验收与安全拦截补测

同一真实浏览器会话先验证以下输入，页面均给出预期提示且未生成提案、未调用 Runner：

| 输入 | 页面结果 | 结果 |
| --- | --- | --- |
| `不要停止任务` | 检测到否定表达，要求重新明确 | PASS |
| `暂停然后继续` | 一句话包含多个动作，要求一次说明一个动作 | PASS |
| `让USV-001停止任务` | 仅支持整队控制，拒绝单设备指令 | PASS |
| `攻击目标` | 能力未接入，不能生成执行提案 | PASS |

随后新建运行 `1790207557031`，在同一 runtime generation 内依次通过文字解析、候选审阅、提案创建和人工确认执行四个动作：

| 动作 | interpretationId | proposalId | executionId | commandId | 最终结果 | Unity 展示 |
| --- | --- | --- | --- | --- | --- | --- |
| START | `5e1150bf-4042-4b16-8355-47cc18441da4` | `297512d0-69e4-4d7e-a5bb-f0a694611747` | `24cdef68-eb45-4050-a8c4-d955a02f5656` | `68262724-4dc0-4762-9d0e-d2e88caa5738` | `RUNNING / SUCCESS` | `REPORTED_APPLIED` |
| PAUSE | `80a40357-1f15-4cb7-b760-2e346ac08589` | `d65f154d-10d8-4460-afe6-327e1b9add9c` | `e9447844-a668-461e-a296-b8186b25d7a2` | `4c8750cb-be58-4894-b793-a947fea0ffbd` | `PAUSED / SUCCESS` | `NOT_REQUIRED` |
| RESUME | `8827c043-fdef-4008-aaae-0f7c3afec873` | `12769797-2b15-4dd4-81e1-f0c27ba0a691` | `f509599e-925b-4219-9750-b1bf731c3c1a` | `f2c267f3-9880-4d22-bef9-4292ccad741f` | `RUNNING / SUCCESS` | `REPORTED_APPLIED` |
| STOP | `5f267f5f-897e-43f7-8ebc-576fd890a54c` | `aaa08f41-2e59-45c7-9396-a2580c1cb8fd` | `4489fe91-9970-4020-9d96-580ba48b3176` | `6a49dfbc-fe15-4d1a-88ef-0d3db53e6c24` | `STOPPED / SUCCESS` | `NOT_REQUIRED` |

本轮公共身份：

- `runtimeRef=21ec7742-b072-4cfd-9019-fdac3df4053b`
- `runtimeGeneration=318212a9-cb68-4f37-9288-37abb406425a`
- `bindingId=b157d4fb-32f3-417a-ad67-fbe63fc47c48`

四个 execution 均记录 `ACCEPTED → SUCCEEDED` 两个有序事件，stateVersion 依次为 `0 → 1 → 2 → 3 → 4`。STOP 后 Runner 进程退出码为 `0`。

## 真实物理麦克风整链路补验

在本机同一隔离环境中完成：

`物理麦克风录音 → 本地 Whisper small 转写 → 原请求恢复 → 文字核对 → 本地规则解析 → START 候选 → 冻结提案 → 人工确认 → Runner SUCCEEDED → Unity REPORTED_APPLIED`

本轮浏览器最初显示转写超时，但 Java 日志确认原请求已由本地 ASR 返回 HTTP 200。按幂等约定点击“使用原请求恢复查询”，没有更换请求 ID，也没有再次调用模型；页面恢复文字“开始任务”后才继续解析。该恢复行为验证了浏览器等待超时与后端已完成结果之间的安全收敛。

| 字段 | 值 |
| --- | --- |
| transcriptionRequestId | `812d02f2-69b2-420e-b9d2-ef57d029a99d` |
| 转写受理 / 完成 | `2026-09-24T01:03:28.909972Z` / `2026-09-24T01:03:57.052Z` |
| 转写结果 | `开始任务` |
| interpretationId | `056d2b4b-581c-4131-b144-c35a73a605d2` |
| 解析结果 | `CANDIDATE / MISSION_START / START` |
| algorithmRunId | `1790207857879` |
| runtimeRef | `92fa2089-1a32-4e32-b3f2-1af6a219a8fc` |
| runtimeGeneration | `c796556b-cb4d-457a-afd5-e6c9811e25b6` |
| bindingId | `a9bcec81-afdc-4dd4-b7bf-dd36825f4b43` |
| proposalId | `c9af8b66-5451-4fae-b698-f16398923e9c` |
| executionId | `87e1e64f-34a3-437d-af5f-919f12fe6c73` |
| commandId | `1c373432-2be3-4e8f-9257-0682b617e7d7` |
| 数据库终态 | proposal `CONFIRMED`；execution `SUCCEEDED / SUCCESS / REPORTED_APPLIED` |
| Runner 事件 | `ACCEPTED (PREPARED, v0) → SUCCEEDED (RUNNING, v1)` |
| 浏览器终态 | `RUNNING`；算法 `SUCCESS`；`Unity 已应用` |

提案记录保留并校验了 `interpretationId`，证明候选来源与冻结计划已关联。执行记录的展示绑定与当前运行代次一致，六台任务设备与冻结计划一致。录音文件、账号密码、内部令牌和本机配置均未写入证据。

机器可读的脱敏证据见 [d2-physical-microphone-chain-20260924.json](d2-physical-microphone-chain-20260924.json)。

## D2 全清单补验

真实 HTTP 脚本随后完成 D2-01 至 D2-17，共记录 21 个 PASS 项；D2-18 使用本文件中的真实浏览器人工确认链路。临时 VIEWER/第二 ADMIN 身份测试结束后已恢复原账号状态，未确认测试提案已取消且数据库中没有对应 execution。

完整映射见 [D2-COVERAGE-20260924.md](D2-COVERAGE-20260924.md)，脱敏原始结果见 [d2-real-http-20260924.json](d2-real-http-20260924.json)。

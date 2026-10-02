# P0 HTTP 与应用服务契约

版本 voice-p0.v1。本文所有新接口为拟实现；现有 Controller 不自动具备这些保证。字段精确类型见 contracts.schema.json；禁止静默忽略未知字段。

## 1. 信任边界与服务职责

浏览器、模型候选输出、音频转写、设备名称和 Unity 回执都是输入数据，不构成授权。P0 不涉及模型；结构化请求同样不可信。

新增 VoiceCommandApplicationService，提供 createProposal、confirm、cancel、getProposal、getExecution；通过受 Spring 方法安全代理的公共方法统一检查登录主体、ADMIN 和实例所有权。禁止从请求体获取操作者；禁止通过 self-invocation 绕过方法安全。异步 worker 使用已持久化操作者 ID 重新验证当前角色与所有权，不依赖丢失的 ThreadLocal。

现有 AlgorithmRuntimeController 的 @PreAuthorize 不会随 Service 调用继承。新路径必须执行应用服务授权，旧入口继续保留原权限。P0 启用时，新旧写操作进入同一实例串行调度器，避免旧手动入口绕过状态版本检查。

SecurityConfig 的现有 /api/runtime-control/** CSRF 豁免不能复制到 /api/voice/**。POST 必须校验当前会话 CSRF；使用项目 /api/auth/csrf 返回的 headerName/token，不硬编码一个供应商 header。

## 2. 运行登记与读取

RuntimeContextRegistry 在后端 prepare 流程创建登记，外部没有“任意注册实例”接口。

- 现有 prepare 参数中的 standaloneVirtualSimulation 是业务线索，不是跳过授权的凭据。
- runtimeRef：UUID，逻辑实例引用；runtimeGeneration：UUID，进程生命周期代次。prepare 对同一存活进程幂等返回原代次；重建进程或替换配置必须换代。
- ownerUserId 从 authenticated principal 提取，绑定 prepare 时操作者。历史无 owner 实例只能只读提示重新准备，不能“先访问者认领”。
- 同一独立仿真槽位被其他用户占用时返回 RUNTIME_BUSY，不得通过 prepare 关闭他人进程。同一用户显式重新准备可沿用现有替换流程，但必须换代并使旧提案失效。
- algorithmRunId 在新 DTO 中为十进制字符串，兼容原 Java Long；missionId/missionRunId 在 P0 为 null。数据库任务和 ROS 未来经明确映射扩展，不能按数值猜测。
- RuntimeScope 复用 MISSION_CENTER；runtimeKind=STANDALONE_ALGORITHM，executionBackend=PYTHON_SIMULATION 是拟新增字段，不向旧 MissionExecutionMode 填入 VIRTUAL_SIMULATION 等不存在值。
- contextVersion 是整数版本，只在权限/成员/能力/运行状态/代次/配置等计划相关条件变化时递增；普通帧 sequence 增加不递增它。
- stateVersion 来自 runner，状态转换时递增，普通位姿帧不递增。contextVersion 与 stateVersion 不可互换。
- backend 单调时钟记录最后心跳和场景回执接收时间；响应中的 UTC receivedAt 用于展示，不能依赖浏览器或 runner 时钟判在线。

P0 仅列出本人实例（ADMIN 也不获得跨用户实例权限）。列表无实例返回空列表；明确访问不存在或无权实例统一 404，避免泄露对象是否存在。

读取字段包括：runtimeRef/generation、contextVersion/stateVersion、上述域/类型/执行端、algorithmRunId、state、protocolVersion、capabilities、lastHeartbeatReceivedAt、latestFrameSequence、sceneReady。具体数据结构见 RuntimeContext。

## 3. HTTP 接口表

全部路径以 /api/voice 为前缀，返回现有 ApiResponse 形状：code、message、data、timestamp。时间采用 RFC3339 的 UTC 子集：YYYY-MM-DDTHH:mm:ss[.1至6位小数]Z，不接受无时区、非Z偏移或闰秒；所有响应不缓存（Cache-Control: no-store）。认证使用现有会话 Cookie；跨源 CORS 不在 P0 开放。

| 方法与路径 | 输入定义 | 成功 data 定义 | HTTP |
| --- | --- | --- | --- |
| GET /contexts | 无 body | RuntimeContext[]，仅本人，当前最多一个活动独立实例 | 200 |
| GET /contexts/{runtimeRef} | UUID path | RuntimeContext，允许读取本人 LOST/终态记录 | 200 |
| POST /commands/proposals | ProposalRequest | Proposal | 首次 201；幂等重放 200 |
| GET /commands/{proposalId} | UUID path | Proposal | 200 |
| POST /commands/{proposalId}/confirm | ConfirmRequest | ConfirmResult（Proposal + Execution） | 首次 202；重放 200 |
| POST /commands/{proposalId}/cancel | CancelRequest | Proposal | 200 |
| GET /executions/{executionId} | UUID path | Execution | 200 |

P0 不实现 /interpret、/clarify、音频接口、实时 WS 或公共 commandResult 上报接口。后续模型解释器只能调用同一 createProposal 应用服务，不另建执行入口。澄清在 P1 生成新提案，避免修改已展示计划。

所有 POST 必需 Idempotency-Key，UUID。首次请求体不含 clientRequestId，避免两种幂等标识优先级冲突。body 限制 16 KiB，Content-Type application/json；超限 413，格式错误 400，类型不支持 415。UUID/枚举/版本等校验不通过为 400 INVALID_REQUEST。

## 4. 请求与不可变计划

ProposalRequest 只允许 runtimeRef、runtimeGeneration、expectedContextVersion、intent。整队动作的 targetScope 固定 FLEET，由后端生成；`SINGLE_DEVICE_CONTROL` 仅能引用同一用户当前活动的 `_SINGLE_DEVICE` 仿真实例中、由后端已认证并缓存的单设备候选。请求体不能指定目标设备或命令；候选须匹配用户、请求、运行代次和上下文版本。不允许用户提供 owner、runId、执行端、设备列表或 requiresConfirmation。

ConfirmRequest 和 CancelRequest 只含 expectedPlanVersion、expectedPlanHash；动作和参数完全从持久化提案读取。每个提案只有一个冻结版本（P0 planVersion=1）；修改需求创建新 proposalId，不原地覆盖。

I05 校验顺序：先校验请求 Schema，再校验持久化计划。expectedPlanVersion 必须为 1；传入 2 等不受支持的版本返回 **400 INVALID_REQUEST**。expectedPlanVersion=1 且 expectedPlanHash 为合法的 64 位小写十六进制字符串、但与提案哈希不一致时，返回 **409 PLAN_MISMATCH**。哈希格式错误仍为 400。确认与取消使用相同规则。两种拒绝均不创建 execution/outbox、不发送算法命令、不修改原提案。前端不得将这两类确定性拒绝当作网络未知结果自动重发确认。

计划包含 runtimeRef/generation、contextVersion、stateVersion、action、explicitDeviceCodes、policyVersion。设备数组按已登记 canonical code 去重排序，至少一项；FLEET 表示这个冻结集合，确认时成员集合变更使提案失效。禁止按“UAV 数量”临时重新展开为另一组设备。单设备计划使用 `DEVICE_COMMAND`，必须只含一个 explicitDeviceCode，并冻结 `targetDeviceCode` 与 `deviceCommandType`；仍需人工确认，确认时复核设备仍属于该运行且具备对应能力。歧义、缺失目标或不支持的指令不得回退成整队动作。

planHash 计算规范：对 Schema 限定的 Plan 对象递归按键名排序，数组顺序保留（explicitDeviceCodes 生成时已排序），字符串采用 JSON Unicode 转义且使用小写十六进制，不添加空白，布尔/null 使用 JSON 标准值，整数用十进制，不允许浮点/NaN；UTF-8 后 SHA-256，返回小写 64 位 hex。不得把时间戳或本地化文案加入 Plan。Java/Python 实现必须通过 fixtures 中同一个黄金哈希样例，不能仅各自自测。

expiresAt = createdAt + 30 秒，以后端注入 Clock 计算；now >= expiresAt 判过期。该值为 P0 默认配置，并非已上线行为。

## 5. 状态表与前置条件

| 意图 → action | 允许的算法状态 | 成功后的状态 | 补充条件 |
| --- | --- | --- | --- |
| MISSION_START → START | PREPARED、PREVIEW | RUNNING | 有权威首帧，当前代次场景已就绪且回执在 10 秒内 |
| MISSION_PAUSE → PAUSE | RUNNING | PAUSED | 无需等下一帧；画面离线不阻止暂停 |
| MISSION_RESUME → RESUME | PAUSED | RUNNING | 进程可用，当前场景已就绪且回执在 10 秒内 |
| MISSION_STOP → STOP | PREPARED、PREVIEW、RUNNING、PAUSED | STOPPED | 不等同 CANCEL、返航、降落、业务任务成功 |

全部写动作要求：ADMIN + owner、PYTHON_SIMULATION、v1 协议、能力匹配、进程存活、收到心跳不超过 5 秒。暂停状态继续心跳，不以最新位姿帧的年龄判断失联。

COMPLETED、FAILED、STOPPED、CANCELLED、LOST 不可写，不允许“恢复已结束任务”。同一个 commandId 重复投递返回旧结果；新的 commandId 请求重复 PAUSE/STOP 则按状态表拒绝，不把网络重试与新的用户意图区分混淆。

提案：AWAITING_CONFIRMATION → CONFIRMED / CANCELLED / EXPIRED / INVALIDATED。输入语义不支持返回 422，不在 P0 创建 INTERPRETING/NEEDS_CLARIFICATION 等模型状态。确认后不能取消提案；要停止实例必须建立新的 STOP 提案。

执行：QUEUED → DISPATCHED → ACCEPTED → EXECUTING → SUCCEEDED；快速动作可 ACCEPTED → SUCCEEDED。发送前条件改变 → INVALIDATED；runner 拒绝 → REJECTED；明确执行异常 → FAILED；超时结果未知 → TIMED_OUT。超时后可信终结回执可对账为 SUCCEEDED/REJECTED/FAILED，并保留 timedOutAt；不自动重发。旧代次迟到回执可更新其原执行记录，绝不能更新当前代次状态。

状态与旧 CommandStatus 映射：QUEUED→PENDING、TIMED_OUT→TIMEOUT，其余同名状态直接映射；INVALIDATED 仅保留在新 execution 表，不硬塞入旧枚举；若未来生成旧 ControlCommand，可映射 REJECTED 并保留 CONTEXT_CHANGED 原因。P0 Python 执行不必创建 ROS ControlCommand。

## 6. 确认事务、取消竞态与发送前复检

确认执行顺序：认证/CSRF → 查询本人提案 → 校验幂等请求 → 锁定实例与提案 → 若已 CONFIRMED 返回已有 execution → 检查未过期/版本/计划哈希 → 重新鉴权、代次、contextVersion、成员、状态与协议 → 创建 execution 和 outbox → 提案改 CONFIRMED → 提交。

过期时原子设置 EXPIRED；上下文变化时设置 INVALIDATED。一个提案唯一关联一个 execution。两个不同幂等键同时确认同一提案也只产生一个执行，后到请求返回已有结果，不创建第二条 outbox。

cancel 仅对 AWAITING_CONFIRMATION 生效；重复取消返回同一个 CANCELLED 提案；与确认竞争时由同一条件更新决定胜者。已确认取消返回 409 ALREADY_CONFIRMED；取消请求不得向 runner 发送 STOP/CANCEL。已过期或失效提案的取消分别返回 PROPOSAL_EXPIRED 或 PROPOSAL_INVALIDATED；不存在则 404。

每实例一个未决 execution：QUEUED/DISPATCHED/ACCEPTED/EXECUTING 以及结果未知的 TIMED_OUT 都占槽。新确认返回 409 EXECUTION_IN_PROGRESS。已确认提案重放只读返回旧记录。保留既有手动暂停/停止管理入口处理异常，但它也须串行化并记录为手动动作；不是第二条语音指令旁路。

worker 在真正写管道前，在实例串行调度器内复检权限、代次、状态版本和心跳；失效则 INVALIDATED 且零写入。runner 在实际应用时还校验 expectedStateVersion；队列等待期间的自然终态不会被 START/RESUME 覆盖。

## 7. 幂等与持久化约束

幂等作用域：(userId, operationKey, Idempotency-Key)。operationKey 包含路由模板和资源 ID，例如 confirm:<proposalId>；请求体按同一无浮点规范哈希。相同键相同 body 返回已存在资源的最新授权视图；同键不同 body 返回 409 IDEMPOTENCY_CONFLICT。鉴权先于重放，已撤权不能利用缓存拿到旧响应。

建议表（设计，不生成迁移）：

| 表 | 关键约束 |
| --- | --- |
| runtime_context | runtimeRef 唯一；ownerUserId、generation、contextVersion、进程状态；单独立槽位唯一锁记录 |
| voice_proposal | proposalId 主键；冻结计划 JSON/hash、状态、expiresAt、version |
| voice_execution | executionId 主键；proposalId UNIQUE；commandId UNIQUE；generation、commandSequence、结果、timedOutAt |
| voice_idempotency | (userId, operationKey, idempotencyKey) UNIQUE；bodyHash、资源 ID |
| voice_outbox | executionId UNIQUE；READY/SENDING/SENT/UNCERTAIN、领取者和领取时间 |
| voice_command_event | (executionId,eventSequence) UNIQUE；保存来源、有效性与迟到事件，不写隐藏推理或密钥 |

同一事务提交提案确认、执行和 outbox；禁止在数据库事务中假装 stdin 写入也可回滚。

worker 先将 READY 改 SENDING 并提交，后写入；成功 flush 后改 SENT。进程在 SENDING 阶段崩溃，恢复后设 UNCERTAIN，绝不无条件重发。即使最终一次实际也没写出，也优先查询结果，不以可用性换重复动作。接收端查询 UNKNOWN 不是“从未执行”的证明。

后端重启时失去对原管道的可靠所有权：原代次 LOST，未确认提案 INVALIDATED，已发送/不确定执行 TIMED_OUT 或保持待对账。只有外部证据确定未发送的 QUEUED 记录才能标 INVALIDATED；不要启动新 runner 重放旧命令。

TIMED_OUT 不因计时结束自动释放实例槽位。经进程退出证据确认原代次结束后，可以释放旧代次槽位并允许用户显式重新准备；旧执行仍保持 UNKNOWN，不伪造终态成功。

去重记录至少保留 30 天且不少于执行审计周期；清理不得早于关联提案与执行终结，未对账记录不自动删除。现有 Flyway 迁移编号实施时读取最新版本分配，不在本设计硬编码 V19。

## 8. 错误码

| HTTP | code | 语义 |
| --- | --- | --- |
| 400 | INVALID_REQUEST | JSON、字段、额外字段、格式或不受支持的请求版本；无资源/管道副作用 |
| 401 | UNAUTHORIZED | 未登录 |
| 403 | FORBIDDEN / CSRF_INVALID | 角色无权限或 CSRF 失败 |
| 404 | RESOURCE_NOT_FOUND | 无权资源和不存在资源统一 |
| 409 | CONTEXT_CHANGED / GENERATION_MISMATCH | 快照或代次变化，提案失效 |
| 409 | PLAN_MISMATCH / INVALID_STATE | 通过 Schema 校验后的计划哈希不符或动作状态不合法（不受支持的请求版本为 400） |
| 409 | PROPOSAL_EXPIRED / ALREADY_CONFIRMED | 确认已过期或取消已确认提案 |
| 409 | PROPOSAL_CANCELLED / PROPOSAL_INVALIDATED | 确认已取消或失效提案 |
| 409 | IDEMPOTENCY_CONFLICT / EXECUTION_IN_PROGRESS / RUNTIME_BUSY | 重用键、占槽或独立实例被占用 |
| 409 | RUNTIME_UNAVAILABLE / SCENE_NOT_READY | 进程/心跳或开始/恢复的场景就绪条件不满足 |
| 413/415 | PAYLOAD_TOO_LARGE / UNSUPPORTED_MEDIA_TYPE | body 体积或媒体类型不支持 |
| 422 | UNSUPPORTED_INTENT / UNSUPPORTED_CAPABILITY / PROTOCOL_UNSUPPORTED | 意图、能力或 runner 协议不支持 |
| 503 | VOICE_CONTROL_DISABLED | P0 写功能关闭；不影响既有手动流程 |

新错误响应 data=null，沿用 ApiResponse；不新增顶层字段破坏旧客户端。错误原因写 message/服务端审计；客户端可 GET 本人提案查看当前状态。Schema 中的未知意图枚举作为非法请求返回 400；422 UNSUPPORTED_INTENT 用于应用服务接收 P1 候选但 P0 不支持的已知意图。

## 9. P0 可见结果与兼容策略

渲染状态字段为 NOT_REQUIRED/PENDING/REPORTED_APPLIED/STALE。开始/恢复可保持 PENDING，算法 SUCCEEDED 不等同画面同步；暂停/停止无需下一帧，默认 NOT_REQUIRED。不能只凭任意浏览器 requestId 修改执行成功状态。

GET execution 返回 action、commandId、runtimeRef/generation、state、outcome（SUCCESS/REJECTED/FAILED/UNKNOWN）、errorCode、timedOutAt、presentationStatus 和时间。中间状态 outcome=UNKNOWN，TIMED_OUT 也为 UNKNOWN。SUCCEEDED 仅表示该动作生效，不表示整个任务完成。

功能开关 app.voicecontrol.enabled 默认 false。只在明确启用的新实例上协商 v1；运行中的 legacy 实例不热切换。新增字段和接口不得改动现有算法 JSON/设备命令枚举。P1 供应商与语音模型尚未确定，不阻塞 P0。

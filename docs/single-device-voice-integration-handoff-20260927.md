# 单设备控制与语音接入交接说明（2026-09-27）

## 1. 当前代码基线

- 仓库：`https://github.com/yqylyx/UAV_USV_Platform`
- 开发分支：`codex/single-device-simulation-integration`
- 单设备控制主提交：`6ed212e`
- 返航完成规则：`72486af`
- 多设备接管标签：`7f814bf`
- 当前保留的可恢复返航规则：`b0edda5`
- `0955a1c` 的动态活动编组/闭环重构方案验证效果不理想，已由 `8adbb8d` 完整撤销；当前相关文件内容与 `b0edda5` 一致。

本分支未改动两个标准算法入口。单设备能力只在以下两个入口启用：

- `ESCORT_GUARD_SINGLE_DEVICE`
- `GB_SFLA_CS_SINGLE_DEVICE`

## 2. 已完成的单设备执行链路

### 前端

- 算法下拉框已有四个入口：两个标准模式、两个单设备模式。
- 单设备页签可选择具体 UAV/USV，并发送悬停/驻留、停止、返航、归队命令。
- 每台已接管设备有独立状态标签，不会只显示最后一次操作。
- 前端统一调用 `POST /api/runtime-control/commands`，没有另造一套单机 API。

关键文件：

- `frontend/src/views/VirtualFleetConfigView.vue`
- `frontend/src/services/singleDeviceControl.ts`
- `frontend/src/api/runtimeControl.ts`

### 后端

`RuntimeControlService` 已经能够识别独立仿真的单设备算法运行，并将带 `runId + deviceCode + commandType` 的设备命令转交 Python 算法进程。已有校验包括：

- 当前算法必须以 `_SINGLE_DEVICE` 结尾；
- 设备必须存在于当前算法帧；
- UAV 命令只能发给 `UAV-*`；
- USV 命令只能发给 `USV-*`；
- 算法仿真单设备命令必须使用 `MISSION_CENTER` 范围；
- 成功与拒绝都会进入现有控制命令记录。

HTTP 请求格式：

```json
{
  "commandType": "USV_RETURN",
  "runId": 1790500000000,
  "deviceCode": "USV-002",
  "runtimeScope": "MISSION_CENTER",
  "runtimeInstanceId": "ALGORITHM_RUN:1790500000000",
  "detail": "语音确认：USV-002 独立返航"
}
```

关键文件：

- `backend/src/main/java/com/uavusv/platform/module/runtimecontrol/controller/RuntimeControlController.java`
- `backend/src/main/java/com/uavusv/platform/module/runtimecontrol/service/RuntimeControlService.java`
- `backend/src/main/java/com/uavusv/platform/module/mission/service/AlgorithmRuntimeManager.java`

### Python 与 Unity

Python runner 已接收 `DEVICE_COMMAND`，调用单设备适配器，并在下一权威帧中输出设备状态。Unity 不需要增加语音专用协议，继续消费 Python 权威帧即可。

当前支持的 Python 命令：

| 设备 | 命令 |
| --- | --- |
| UAV | `UAV_HOVER`、`UAV_RETURN`、`UAV_RESUME`、`UAV_LAND` |
| USV | `USV_HOLD`、`USV_RETURN`、`USV_RESUME`、`USV_STOP` |

关键文件：

- `algorithm-service/runner.py`
- `algorithm-service/app/adapters/single_device.py`
- `algorithm-service/tests/test_single_device_control.py`

## 3. 当前语音能力与缺口

现有语音链路只支持整队任务动作：

- `START`
- `PAUSE`
- `RESUME`
- `STOP`

`IntentService` 中的 `TARGETED` 正则会识别 `UAV-001`、`USV-002`、“一号无人机”等定向表达，但随后主动返回：

```text
UNSUPPORTED_TARGETING
当前仅支持整队任务控制，暂不支持指定单台设备。
```

此外，当前 `VoiceFrozenPlan.explicitDeviceCodes` 固定使用运行上下文里的整个 `_members` 集合，`VoiceDispatcher` 也按整队受影响设备做一致性校验。因此不能只删除 `TARGETED` 拒绝逻辑，否则可能把“让 USV-002 停止”错误降级为整队 `STOP`。

关键文件：

- `backend/src/main/java/com/uavusv/platform/module/voiceintelligence/IntentService.java`
- `backend/src/main/java/com/uavusv/platform/module/voiceintelligence/IntentClassification.java`
- `backend/src/main/java/com/uavusv/platform/module/voicecontrol/VoiceCommandApplicationService.java`
- `backend/src/main/java/com/uavusv/platform/module/voicecontrol/VoiceDispatcher.java`
- `frontend/src/types/voiceControl.ts`
- `frontend/src/components/voice/VoiceP0ControlPanel.vue`

## 4. 建议的语音候选契约

保留原有整队动作不变，为单设备候选增加独立类型：

```json
{
  "status": "CANDIDATE",
  "intent": "SINGLE_DEVICE_CONTROL",
  "action": "DEVICE_COMMAND",
  "targetDeviceCode": "USV-002",
  "deviceCommandType": "USV_RETURN",
  "requiresConfirmation": true
}
```

冻结计划至少应包含：

```json
{
  "action": "DEVICE_COMMAND",
  "explicitDeviceCodes": ["USV-002"],
  "targetDeviceCode": "USV-002",
  "deviceCommandType": "USV_RETURN"
}
```

确认后不要直接向 Unity 发语音命令，应复用现有接口：

```text
VoiceDispatcher
  -> RuntimeControlService.issueCommand(...)
  -> AlgorithmRuntimeManager.controlDevice(...)
  -> Python DEVICE_COMMAND
  -> 权威算法帧
  -> Unity 展示
```

## 5. 中文语义映射建议

| 语音示例 | 目标命令 |
| --- | --- |
| “让 UAV-001 悬停” | `UAV_HOVER` |
| “一号无人机返航” | `UAV_RETURN` |
| “UAV-001 安全归队” | `UAV_RESUME` |
| “停止 UAV-001” | `UAV_LAND`（见下方限制） |
| “让 USV-002 驻留” | `USV_HOLD` |
| “二号无人艇返航” | `USV_RETURN` |
| “USV-002 安全归队” | `USV_RESUME` |
| “停止 USV-002” | `USV_STOP` |

设备编号统一规范化为大写和连字符格式，例如 `uav_1`、`一号无人机`最终转换为 `UAV-001`。

## 6. 必须保留的安全规则

1. 单设备语音命令继续走“候选 -> 人工确认 -> 执行”，不能语音直达执行。
2. 没有明确设备编号时不得自动选择当前设备，也不得降级为整队命令。
3. “二号返航”等无法确定 UAV/USV 类型的表达应进入 `NEEDS_CLARIFICATION`。
4. 一句话包含多台设备或多个动作时先拒绝或澄清；第一阶段只支持“一台设备、一个动作”。
5. 只有 `_SINGLE_DEVICE` 算法运行上下文才能生成单设备候选。
6. 候选中的设备必须出现在当前运行上下文的设备集合中。
7. 确认前后必须校验 `runtimeRef`、`runtimeGeneration`、`contextVersion` 和目标设备，防止场景重建后执行旧候选。
8. UAV/USV 命令类型必须与目标设备一致。
9. `DEVICE_COMMAND` 的回执应只声明目标设备，不能沿用整队 `_members` 作为 `affectedDeviceCodes`。
10. 当前 Python 中 `UAV_LAND` 实际状态为 `STOPPED`，还不是真实下降着陆。语音界面暂时建议展示“停止 UAV”，不要承诺“降落完成”。

## 7. 建议开发顺序

1. 扩展意图结果与前端类型，增加 `DEVICE_COMMAND`、`targetDeviceCode`、`deviceCommandType`。
2. 在规则解析器中先完成精确设备编号与单动作解析，再更新本地 Qwen 提示词和 JSON 约束。
3. 扩展候选冻结与确认校验，确保只冻结一个目标设备。
4. 在 `VoiceDispatcher` 中调用现有 `RuntimeControlService.issueCommand`，不重复实现算法通信。
5. 将 Python 的单设备回执映射为语音执行结果，并验证 `affectedDeviceCodes` 只有目标设备。
6. 前端候选卡明确展示“设备、动作、运行批次”，人工确认后显示执行结果。

## 8. 最小验收用例

- “让 UAV-001 悬停”生成正确候选，确认后只有 UAV-001 进入 `HOLDING`。
- “让 USV-002 返航”确认后只有 USV-002 进入 `RETURNING`。
- “让 UAV-001 归队”确认后进入 `REJOINING`，随后恢复 `ACTIVE`。
- 将 `UAV_HOVER` 明确指定给 `USV-001` 时被类型校验拒绝，不生成可执行候选。
- “二号返航”返回需要澄清。
- “全部返航”不被当作单设备命令。
- 标准算法模式下拒绝单设备候选。
- 场景重建后确认旧候选，返回上下文已变化。
- 未点击人工确认时不得产生 `DEVICE_COMMAND`。
- 单设备命令失败时不得伪装成整队任务成功或失败。

## 9. 当前已知限制

- 当前保留的是 `b0edda5` 行为。围捕过程中人工退出设备后，群算法没有可靠的动态重新分槽能力；某些场景可能停在 99%。此前的 `0955a1c` 尝试已撤销，不应作为语音接入依赖。
- 语音同学只需接通“识别、候选、确认、调度和回执”；不要在语音模块内重新实现运动、返航轨迹或 Unity 状态机。
- 模型权重与密钥仍不入库；本地 Qwen/ASR 按现有部署说明单独配置。

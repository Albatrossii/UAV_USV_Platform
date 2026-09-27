# 单设备控制与群体算法协同规则

## 1. 适用范围

本规则只用于两个“单设备控制”算法入口：

- `ESCORT_GUARD_SINGLE_DEVICE`
- `GB_SFLA_CS_SINGLE_DEVICE`

原有标准护航和标准围捕入口不改变。

## 2. 设备状态与任务资格

| 状态 | 是否参与群体任务 | 是否保留在场景与避碰域 | 是否阻塞最终成功 |
| --- | --- | --- | --- |
| `ACTIVE` | 是 | 是 | 否 |
| `HOLDING` | 否 | 是 | 仅在剩余活动编组不满足最低结构时阻塞 |
| `STOPPED` | 否 | 是 | 仅在剩余活动编组不满足最低结构时阻塞 |
| `RETURNING` | 否 | 是 | 是，直到到达返航点并转为 `RETURNED` |
| `RETURNED` | 否 | 是 | 否；按已退出任务编组处理 |
| `REJOINING` | 否 | 是 | 是，直到安全归队并转为 `ACTIVE` |

人工接管不会直接判定任务失败。兵力不足时任务进入可恢复的 `DEGRADED` 状态；设备安全归队后重新计算活动编组并继续任务。

## 3. 围捕成功规则

围捕模式的活动编组必须同时满足：

1. 至少 3 台活动设备；
2. 至少 1 台 UAV；
3. 至少 1 台 USV；
4. 目标位于活动设备形成的闭环内部；
5. 基础算法的距离、槽位收敛和稳定保持条件已经完成；
6. 不存在 `RETURNING` 或 `REJOINING` 的未完成控制权交接。

因此 3 UAV + 3 USV 的场景最多可同时退出 3 台设备，但剩余 3 台仍须同时包含 UAV 和 USV。少于 3 台时只能继续跟踪/截击，不能判定围捕成功。

如果基础算法已按原编组到位，但退出设备在实际闭环中留下缺口，单设备接入层会对剩余活动设备执行限速闭环重构。重构期间保持 99%，实际闭环重新满足围捕契约后才成功。

## 4. 护航成功规则

护航模式的活动编组必须同时满足：

1. 至少 2 台活动设备；
2. 至少 1 台 UAV，用于空中预警/截击；
3. 至少 1 台 USV，用于水面伴随/近程保护；
4. 受保护目标按基础算法抵达安全终点，威胁处置完成；
5. 不存在 `RETURNING` 或 `REJOINING` 的未完成控制权交接。

只有单一设备类型时任务保持运行但标记为降级，不宣告成功，也不直接失败。

## 5. 四类控制命令

- 悬停/驻留：设备立即退出任务分配，在当前位置保持；剩余活动编组满足结构时任务可继续并成功。
- 单机停止：与悬停/驻留采用相同任务资格规则；设备仍作为固定障碍参与避碰。
- 独立返航：设备退出任务分配并按限速轨迹返回初始点；返航途中暂缓最终成功，到达后不再阻塞。
- 安全归队：设备先以 `REJOINING` 状态接近当前算法位置，完成平滑交接后恢复 `ACTIVE`；归队完成前暂缓最终成功。

## 6. 页面与接口判据

运行帧通过以下字段公开判定依据：

- `activeMissionDeviceCount` / `activeMissionDeviceCodes`
- `excludedMissionDeviceCodes`
- `requiredActiveMissionDeviceCount`
- `requiredActiveDeviceTypes` / `missingActiveDeviceTypes`
- `deviceControlStates`
- `missionCapability`: `FULL` 或 `DEGRADED`
- `completionBlocker`: `NONE`、`WAITING_FOR_RETURN`、`WAITING_FOR_REJOIN`、`INSUFFICIENT_ACTIVE_FORCE` 或 `ACTIVE_FORMATION_NOT_CLOSED`

任何阻塞状态都把展示进度限制在 99%，但不会把任务写成失败。阻塞解除后由算法继续完成最终判定。

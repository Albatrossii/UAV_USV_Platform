import type { AlgorithmRuntimeFrame } from '@/types/mission'

export interface DeviceActionProgress { label: string; complete: boolean; stage: string }
const stageLabels: Record<string, string> = {
  ASCENDING: '爬升至返航高度', CRUISING: '飞往停机坪', DESCENDING: '对准停机坪下降',
  APPROACHING: '驶向岸边泊位', CHANNEL: '通过进港航道', DOCKING: '低速靠泊',
  WAITING_FOR_CHANNEL: '等待泊位通道', HOLDING_CHANNEL: '通道内驻留',
  TAKING_OFF: '从停机坪起飞', DEPARTING: '飞离岸边基地', ASSEMBLING: '前往任务集结点',
  UNDOCKING: '离开泊位', EXITING: '驶出港区', REJOINING: '正在归队',
}
const commands = new Set(['UAV_HOVER', 'UAV_RESUME', 'UAV_RETURN', 'UAV_LAND', 'USV_HOLD', 'USV_RESUME', 'USV_RETURN', 'USV_STOP'])
function deviceCode(value: unknown) {
  return typeof value === 'string' ? value.toUpperCase().replace(/^(UAV|USV)-?0*(\d+)$/, (_, kind, n) => `${kind}-${n.padStart(3, '0')}`) : ''
}
export function deviceActionProgress(frame: AlgorithmRuntimeFrame | null | undefined, target: string | undefined,
  command: string | undefined, minimumSequence = 0): DeviceActionProgress | null {
  if (!target || !command) return null
  if (!commands.has(command) || !target.startsWith(`${command.slice(0, 3)}-`)) {
    return { label: '设备类型与动作不匹配，请核对回执', complete: false, stage: 'INVALID_COMMAND' }
  }
  if (!frame || !Number.isSafeInteger(frame.sequence) || !Number.isSafeInteger(minimumSequence)
    || minimumSequence < 0 || frame.sequence <= minimumSequence) return { label: '指令已接受，等待新的设备状态', complete: false, stage: 'WAITING_FRAME' }
  const matches = frame.agents.filter(a => deviceCode(a.code) === target)
  if (matches.length > 1) return { label: '目标设备状态重复，请等待状态核对', complete: false, stage: 'CONFLICT' }
  const agent = matches[0]
  if (!agent) return { label: '等待目标设备状态', complete: false, stage: 'UNKNOWN' }
  if (agent.type && agent.type !== target.slice(0, 3)) return { label: '设备编号与类型不一致，请核对状态', complete: false, stage: 'CONFLICT' }
  const states = frame.metrics.deviceControlStates as Record<string, Record<string, unknown>> | undefined
  const state = states?.[target]
  const motion = String(state?.motionState ?? agent.status ?? '')
  const stage = String(state?.returnStage ?? motion)
  const detailedStageMatches = Boolean(
    state?.returnStage && agent.status && state.returnStage === agent.status,
  ) || (state?.motionState === 'REJOINING' && agent.status === 'ASSEMBLING')
  if (state?.motionState && agent.status && state.motionState !== agent.status && !detailedStageMatches) {
    return { label: '设备运动状态不一致，暂不能确认完成', complete: false, stage: 'CONFLICT' }
  }
  if (['UAV_RETURN', 'UAV_LAND', 'USV_RETURN'].includes(command)) {
    if (motion === 'RETURNED') {
      const expectedKind = target.startsWith('UAV-') ? 'HELIPAD' : 'BERTH'
      if (state?.parkingKind !== expectedKind || state?.returnedAtHome === false
        || (state?.parkingSlot != null && deviceCode(state.parkingSlot) !== target)) {
        return { label: '已收到返航状态，停靠位置待核对', complete: false, stage: 'VERIFY_PARKING' }
      }
      return { label: expectedKind === 'HELIPAD' ? '已到停机坪，着陆完成' : '已到岸边泊位，靠泊完成', complete: true, stage: 'ARRIVED' }
    }
    return { label: stageLabels[stage] ?? (motion === 'RETURNING' ? '正在返航' : '返航尚未完成'), complete: false, stage }
  }
  if (command.endsWith('_RESUME')) {
    const done = motion === 'ACTIVE' && (!state?.controlAuthority || state.controlAuthority === 'ALGORITHM')
    return { label: done ? '已归队，恢复算法控制' : motion === 'ACTIVE' ? '设备已活动，控制权交接待核对' : stageLabels[stage] ?? '正在归队', complete: done, stage }
  }
  const done = command === 'USV_STOP' ? motion === 'STOPPED' : motion === 'HOLDING'
  return { label: done ? command === 'UAV_HOVER' ? '已悬停' : command === 'USV_STOP' ? '已停船' : '已驻留' : '等待动作生效', complete: done, stage }
}

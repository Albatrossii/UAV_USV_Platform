import type { AlgorithmRuntimeFrame } from '@/types/mission'

export interface DeviceActionProgress { label: string; complete: boolean; stage: string }
const stageLabels: Record<string, string> = {
  ASCENDING: '爬升至返航高度', CRUISING: '飞往停机坪', DESCENDING: '对准停机坪下降',
  APPROACHING: '驶向岸边泊位', CHANNEL: '通过进港航道', DOCKING: '低速靠泊',
  WAITING_FOR_CHANNEL: '等待泊位通道', HOLDING_CHANNEL: '通道内驻留',
  TAKING_OFF: '从停机坪起飞', UNDOCKING: '离开泊位', EXITING: '驶出港区', REJOINING: '正在归队',
}
export function deviceActionProgress(frame: AlgorithmRuntimeFrame | null | undefined, target: string | undefined,
  command: string | undefined, minimumSequence = 0): DeviceActionProgress | null {
  if (!target || !command) return null
  if (!frame || frame.sequence <= minimumSequence) return { label: '指令已接受，等待新的设备状态', complete: false, stage: 'WAITING_FRAME' }
  const agent = frame.agents.find(a => a.code?.toUpperCase().replace(/^(UAV|USV)-?0*(\d+)$/, (_, kind, n) => `${kind}-${n.padStart(3, '0')}`) === target)
  if (!agent) return { label: '等待目标设备状态', complete: false, stage: 'UNKNOWN' }
  const states = frame.metrics.deviceControlStates as Record<string, Record<string, unknown>> | undefined
  const state = states?.[target]
  const motion = String(state?.motionState ?? agent.status ?? '')
  const stage = String(state?.returnStage ?? motion)
  if (['UAV_RETURN', 'UAV_LAND', 'USV_RETURN'].includes(command)) {
    if (motion === 'RETURNED') return { label: state?.parkingKind === 'HELIPAD' ? '已到停机坪，着陆完成'
      : state?.parkingKind === 'BERTH' ? '已到岸边泊位，靠泊完成' : '已到返航点', complete: true, stage: 'ARRIVED' }
    return { label: stageLabels[stage] ?? (motion === 'RETURNING' ? '正在返航' : '返航尚未完成'), complete: false, stage }
  }
  if (command.endsWith('_RESUME')) return { label: motion === 'ACTIVE' ? '已归队，恢复算法控制' : stageLabels[stage] ?? '正在归队', complete: motion === 'ACTIVE', stage }
  const done = command === 'USV_STOP' ? motion === 'STOPPED' : motion === 'HOLDING'
  return { label: done ? command === 'UAV_HOVER' ? '已悬停' : command === 'USV_STOP' ? '已停船' : '已驻留' : '等待动作生效', complete: done, stage }
}

import type { VoiceExecution, VoiceSequenceExecutionStep, VoiceSequencePlanStep, VoiceRuntimeContext } from '@/types/voiceControl'
import { normalizeVoiceDeviceTerms } from './voiceTranscriptNormalization'
import { voiceDeviceLabel } from './voiceCommandSafety'

export const missionVoiceActions: Record<string, string> = {
  开始任务: 'START', 启动任务: 'START', 执行任务: 'START', 开始执行任务: 'START', 开始当前任务: 'START', 启动当前任务: 'START',
  暂停任务: 'PAUSE', 暂停一下: 'PAUSE', 暂停当前任务: 'PAUSE',
  继续任务: 'RESUME', 继续执行任务: 'RESUME', 恢复任务: 'RESUME', 恢复运行: 'RESUME', 恢复执行: 'RESUME',
  停止任务: 'STOP', 停止当前任务: 'STOP', 停止执行任务: 'STOP', 结束任务: 'STOP', 终止任务: 'STOP',
  现在开始任务: 'START', 暂停一下当前任务: 'PAUSE', 停止当前运行任务: 'STOP',
  结束当前任务: 'STOP', 终止当前任务: 'STOP', 立即停止任务: 'STOP', 马上停止任务: 'STOP',
}
export function splitVoiceSequence(text: string) {
  const normalized = normalizeVoiceDeviceTerms(text.replace(/\s+/gu, '')).replace(/[。！？!?，,]+$/, '')
  const compact = normalized.match(/^(?:请)?(?:先)?(开始(?:执行|当前)?任务|启动(?:当前)?任务|执行任务)[，,。；;]?(?:(?:之后|以后|后|然后|再|接着)[，,。；;]?)?(?:再)?(?:让|请)?(.+)$/u)
  return (compact ? `${compact[1]}，${compact[2]}` : normalized)
    .split(/(?:[，,。；;]*(?:然后|接着|之后|以后|再)[，,。；;]*|[，,。；;]+)/u)
    .map(clause => clause.replace(/^(?:请)?(?:先)?(?:让)?/u, ''))
}
export function sequenceMatchesSpeech(text: string, steps: VoiceSequencePlanStep[],
  deviceValid: (target: string | undefined, command: string | undefined, text: string) => boolean) {
  const clauses = splitVoiceSequence(text)
  const explicitlyOrdered = /^(?:请)?先/u.test(text.replace(/\s+/gu, ''))
  if (clauses.length < 2 || clauses.length > 4 || clauses.length !== steps.length) return false
  let lastTarget: string | undefined
  return steps.every((step, index) => {
    let clause = clauses[index]!
    if (step.index !== index) return false
    if (Boolean(step.waitForPrevious) !== (explicitlyOrdered && index > 0)) return false
    if (step.action === 'WAIT') {
      const wait = clause.match(/^(?:等待|等)([一二三四五六七八九十]|\d{1,2})秒$/u)
      const seconds = wait ? /^\d+$/.test(wait[1]!) ? Number(wait[1]) : '一二三四五六七八九十'.indexOf(wait[1]!) + 1 : 0
      return index > 0 && seconds >= 1 && seconds <= 60 && step.waitSeconds === seconds
    }
    if (step.action !== 'DEVICE_COMMAND') {
      lastTarget = undefined
      return missionVoiceActions[clause] === step.action
    }
    if (lastTarget && /^(?:它)?(?:悬停|驻留|待命|返航|返回|归队|继续|停止|降落|停船)$/u.test(clause)) clause = lastTarget + clause.replace(/^它/u, '')
    lastTarget = step.targetDeviceCode
    // Whole-utterance normalization already ran before splitting. A clause
    // must not gain a new single-command ASR repair by losing its context.
    if (normalizeVoiceDeviceTerms(clause) !== clause) return false
    return deviceValid(step.targetDeviceCode, step.deviceCommandType, clause)
  })
}

export const voiceActionLabels: Record<string, string> = {
  START: '开始任务', PAUSE: '暂停任务', RESUME: '继续任务', STOP: '停止任务',
  UAV_HOVER: '无人机悬停', UAV_RESUME: '无人机归队', UAV_RETURN: '无人机返航', UAV_LAND: '无人机降落',
  USV_HOLD: '无人艇驻留', USV_RESUME: '无人艇归队', USV_RETURN: '无人艇返航', USV_STOP: '无人艇停止',
}
export function voiceStepLabel(step: VoiceSequencePlanStep) {
  return step.action === 'WAIT' ? `等待 ${step.waitSeconds} 秒`
    : step.action === 'DEVICE_COMMAND' ? `${voiceDeviceLabel(step.targetDeviceCode)} · ${voiceActionLabels[step.deviceCommandType ?? ''] ?? step.deviceCommandType}`
      : voiceActionLabels[step.action] ?? step.action
}

const interruptedSequenceStates = new Set(['FAILED', 'REJECTED', 'INVALIDATED', 'TIMED_OUT'])

export function sequenceInterruptionNotice(execution: VoiceExecution | null, context?: Pick<VoiceRuntimeContext, 'runtimeRef' | 'runtimeGeneration' | 'state'> | null) {
  if (!execution || execution.action !== 'SEQUENCE' || !interruptedSequenceStates.has(execution.state)) return ''
  const notice = '顺序指令已中断，后续步骤不会自动继续；已成功步骤不会自动撤销。已下发的并行动作不会自动撤销，请核对设备状态。'
  const reasons: Record<string, string> = {
    GENERATION_MISMATCH: '场景运行代次已变化，原计划不再适用。',
    HEARTBEAT_STALE: '算法心跳失效，不能继续核实执行条件。',
    RUNTIME_UNAVAILABLE: '算法运行暂不可用。',
    SEQUENCE_POLICY_VIOLATION: '后续动作未通过顺序指令安全校验。',
    SEQUENCE_CHILD_MISSING: '子步骤回执缺失，需要核对原请求。',
  }
  let reason = reasons[execution.errorCode ?? ''] ?? ''
  if (execution.errorCode === 'INVALID_STATE') {
    const sameRuntime = context?.runtimeRef === execution.runtimeRef && context?.runtimeGeneration === execution.runtimeGeneration
    const states: Record<string, string> = {
      COMPLETED: '当前任务已完成，未下发的后续步骤不能再执行。',
      STOPPED: '当前任务已停止，未下发的后续步骤不能再执行。',
      CANCELLED: '当前任务已取消，未下发的后续步骤不能再执行。',
      FAILED: '当前任务已失败，不能继续执行后续步骤。',
      LOST: '当前算法进程已丢失，不能继续执行后续步骤。',
      PAUSED: '当前任务已暂停，不满足后续步骤所需状态。',
    }
    reason = (sameRuntime && states[context!.state]) || '执行时的运行状态不满足后续步骤要求，请核对当前任务状态。'
  }
  const skipped = (execution.steps ?? []).filter(step => step.state === 'PENDING' && !step.executionId && !step.startedAt)
  const details = skipped.length ? ` 未下发：${skipped.map(voiceStepLabel).join('；')}。` : ''
  return execution.state === 'TIMED_OUT'
    ? `${notice} 超时步骤结果仍待核对，请勿重复下发整条指令。${details}` : `${reason}${notice}${details}`
}

/** Describe only what the receipt proves; never turn an issued step into "not executed". */
export function sequenceStepHint(execution: VoiceExecution, step: VoiceSequenceExecutionStep): string | null {
  if (execution.action !== 'SEQUENCE') return null
  const interrupted = interruptedSequenceStates.has(execution.state)
  if (step.state === 'PENDING') {
    if (interrupted) return step.executionId || step.startedAt ? '结果待核对' : '未执行（顺序已中断）'
    if (execution.state === 'SUCCEEDED') return '步骤回执待核对'
    if (!execution.steps) return '等待前一步'
    const preceding = execution.steps.filter(item => item.index < step.index && item.state !== 'SUCCEEDED')
    if (preceding.some(item => item.action === 'WAIT')) return '等待计时步骤完成'
    if (step.waitForPrevious || step.action !== 'DEVICE_COMMAND' || preceding.some(item => item.waitForPrevious || item.action !== 'DEVICE_COMMAND')) return '等待前序动作完成'
    if (preceding.some(item => item.targetDeviceCode === step.targetDeviceCode)) return '等待同一设备前序动作完成'
    return '等待调度 · 可与其他设备并行'
  }
  if (interrupted && ['QUEUED', 'DISPATCHED', 'ACCEPTED', 'EXECUTING'].includes(step.state)) {
    // The parent may have timed out while a child is still moving or awaiting a receipt.
    return step.action === 'WAIT' && !step.executionId ? '等待已中断' : '执行结果待核对（不自动续跑）'
  }
  if (step.state === 'EXECUTING' && execution.state === 'EXECUTING') {
    if (step.action === 'WAIT') return '计时等待中'
    if (['UAV_RETURN', 'USV_RETURN', 'UAV_RESUME', 'USV_RESUME', 'UAV_LAND'].includes(step.deviceCommandType ?? '')) {
      return '指令已接受，等待实际到位'
    }
  }
  return null
}

/** Forecast every transition before sending any part of the frozen plan. */
export function sequenceStateValid(steps: VoiceSequencePlanStep[], initialState: string) {
  let state = initialState
  return steps.length >= 2 && steps.length <= 4 && steps.every((step, index) => {
    if (step.index !== index) return false
    if (step.action === 'WAIT') return index > 0 && ['RUNNING', 'PAUSED'].includes(state)
      && Number.isInteger(step.waitSeconds) && step.waitSeconds! >= 1 && step.waitSeconds! <= 60
    if (step.action === 'DEVICE_COMMAND') return state === 'RUNNING'
    const allowed: Record<string, string[]> = { START: ['PREPARED', 'PREVIEW'], PAUSE: ['RUNNING'], RESUME: ['PAUSED'], STOP: ['PREPARED', 'PREVIEW', 'RUNNING', 'PAUSED'] }
    if (!allowed[step.action]?.includes(state)) return false
    state = step.action === 'PAUSE' ? 'PAUSED' : step.action === 'STOP' ? 'STOPPED' : 'RUNNING'
    return true
  })
}

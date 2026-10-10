import type { VoiceSequencePlanStep } from '@/types/voiceControl'
import { normalizeVoiceDeviceTerms } from './voiceTranscriptNormalization'

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
  if (clauses.length < 2 || clauses.length > 4 || clauses.length !== steps.length) return false
  let lastTarget: string | undefined
  return steps.every((step, index) => {
    let clause = clauses[index]!
    if (step.index !== index) return false
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
    : step.action === 'DEVICE_COMMAND' ? `${step.targetDeviceCode} · ${voiceActionLabels[step.deviceCommandType ?? ''] ?? step.deviceCommandType}`
      : voiceActionLabels[step.action] ?? step.action
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

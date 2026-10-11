import type { VoiceSequencePlanStep } from '@/types/voiceControl'

type Command = { action: string; deviceCommandType?: string; steps?: VoiceSequencePlanStep[] }
const highRisk = new Set(['UAV_RETURN', 'UAV_LAND', 'USV_RETURN', 'USV_STOP'])

// Keep aligned with IntentService.NEGATED. Inspect the original utterance before
// noun normalization or parsing so a provider cannot erase a negation.
const negatedAction = /(?:不|没|未|别|无需|无须|禁止|取消|勿|莫|暂缓).{0,24}(?:开始|启动|执行|暂停|继续|恢复|停止|终止|结束|悬停|驻留|待命|保持|返航|返回|归队|降落|停船)/u
export function hasNegatedVoiceAction(text: string): boolean {
  return negatedAction.test(text.replace(/\s+/gu, ''))
}

/** These fields must come from the registered backend context, never a UI hint. */
export interface VoiceAutomationRuntime {
  runtimeRef: string
  runtimeGeneration: string
  runtimeScope: string
  runtimeKind: string
  executionBackend: string
  protocolVersion: string
}

export function isLocalVoiceSimulation(runtime?: VoiceAutomationRuntime | null): boolean {
  return !!runtime?.runtimeRef && !!runtime.runtimeGeneration
    && runtime.runtimeScope === 'MISSION_CENTER'
    && runtime.runtimeKind === 'STANDALONE_ALGORITHM'
    && runtime.executionBackend === 'PYTHON_SIMULATION'
    && runtime.protocolVersion === 'algorithm.command.v1'
}

/** Apply to the complete frozen plan before any step is dispatched. */
export function requiresVoiceConfirmation(command: Command, speechRuntime?: VoiceAutomationRuntime | null): boolean {
  if (command.action === 'SEQUENCE') return !command.steps?.length
    || command.steps.some(step => requiresVoiceConfirmation(step, speechRuntime))
  return !isLocalVoiceSimulation(speechRuntime) && (command.action === 'STOP'
    || (command.action === 'DEVICE_COMMAND' && highRisk.has(command.deviceCommandType ?? '')))
}

export function voiceDeviceLabel(code: string | undefined): string {
  const match = code?.match(/^(UAV|USV)-(\d{3})$/)
  if (!match) return code || '目标未明确'
  const number = Number(match[2])
  const ordinal = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十'][number] ?? String(number)
  return `${code} · ${ordinal}号${match[1] === 'UAV' ? '无人机' : '无人艇'}`
}

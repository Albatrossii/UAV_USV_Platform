import { describe, expect, it } from 'vitest'
import { hasNegatedVoiceAction, isLocalVoiceSimulation, requiresVoiceConfirmation, voiceDeviceLabel } from '../voiceCommandSafety'
import { normalizeVoiceDeviceTerms } from '../voiceTranscriptNormalization'

const simulation = { runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', runtimeScope: 'MISSION_CENTER',
  runtimeKind: 'STANDALONE_ALGORITHM', executionBackend: 'PYTHON_SIMULATION', protocolVersion: 'algorithm.command.v1' }

describe('voice command safety policy', () => {
  it.each([
    '一号无人机暂不返航', '一号无人机不返航', '一号无人机不降落', '一号无人艇不停止',
    '一号无人艇不要返回', '暂不让一号无人鸡返航', '一号无人机不再降落', '先别让一号机停止',
    '一号无人艇无须停船', '一号无人机没有要返航', '开始任务，然后一号无人机不返航',
    '一号无人机不\n返航', '暂缓一号无人机降落',
    '不要第十三号指令', '不要13号命令', '不要执行二十号指令',
    '不要第十三条指令', '别第XIII个命令', '取消第ⅩⅢ号指令', '不要指令XIII',
    '不要第壹拾叁条指令', '不要第１３条指令',
  ])('detects negation before and after device noun normalization: %s', text => {
    expect(hasNegatedVoiceAction(text)).toBe(true)
    expect(hasNegatedVoiceAction(normalizeVoiceDeviceTerms(text))).toBe(true)
  })
  it.each(['一号无人机请返航', '一号无人机马上返航', '请让一号无人机立即降落',
    '开始任务，然后一号无人机返航', '一号无人艇继续', '这不是任务指令'])('does not invent a negated action in %s', text => {
    expect(hasNegatedVoiceAction(text)).toBe(false)
  })
  it.each(['UAV_RETURN', 'UAV_LAND', 'USV_RETURN', 'USV_STOP'])('requires confirmation for %s', deviceCommandType => {
    expect(requiresVoiceConfirmation({ action: 'DEVICE_COMMAND', deviceCommandType })).toBe(true)
  })
  it.each(['UAV_HOVER', 'USV_HOLD', 'UAV_RESUME', 'USV_RESUME'])('preserves low-risk %s automation', deviceCommandType => {
    expect(requiresVoiceConfirmation({ action: 'DEVICE_COMMAND', deviceCommandType })).toBe(false)
  })
  it('checks the entire sequence before any step', () => {
    expect(requiresVoiceConfirmation({ action: 'STOP' })).toBe(true)
    expect(requiresVoiceConfirmation({ action: 'SEQUENCE', steps: [
      { index: 0, action: 'START' }, { index: 1, action: 'WAIT', waitSeconds: 5 },
      { index: 2, action: 'DEVICE_COMMAND', targetDeviceCode: 'USV-001', deviceCommandType: 'USV_RETURN' },
    ] })).toBe(true)
  })
  it.each(['UAV_RETURN', 'UAV_LAND', 'USV_RETURN', 'USV_STOP'])('automates validated simulation speech for %s', deviceCommandType => {
    expect(requiresVoiceConfirmation({ action: 'DEVICE_COMMAND', deviceCommandType }, simulation)).toBe(false)
  })
  it('automates a validated simulation STOP and complete return sequence', () => {
    expect(requiresVoiceConfirmation({ action: 'STOP' }, simulation)).toBe(false)
    expect(requiresVoiceConfirmation({ action: 'SEQUENCE', steps: [
      { index: 0, action: 'START' },
      { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_RETURN' },
    ] }, simulation)).toBe(false)
    expect(requiresVoiceConfirmation({ action: 'SEQUENCE' }, simulation)).toBe(true)
  })
  it('automates presets only in a registered local simulation, including whole sequences', () => {
    const preset = { action: 'ALGORITHM_PRESET' }
    expect(requiresVoiceConfirmation(preset, simulation)).toBe(false)
    expect(requiresVoiceConfirmation(preset)).toBe(true)
    expect(requiresVoiceConfirmation({ action: 'SEQUENCE', steps: [
      { index: 0, action: 'ALGORITHM_PRESET', parameters: { presetId: 13, presetVersion: 'escort-20261020' } },
      { index: 1, action: 'ALGORITHM_PRESET', parameters: { presetId: 20, presetVersion: 'escort-20261020' } },
    ] }, simulation)).toBe(false)
  })
  it.each([
    { runtimeRef: '' }, { runtimeGeneration: '' }, { runtimeScope: 'GATEWAY' },
    { runtimeKind: 'GATEWAY' }, { executionBackend: 'REAL_DEVICE' }, { protocolVersion: 'legacy' },
  ])('retains confirmation without the complete simulation identity %j', changed => {
    const runtime = { ...simulation, ...changed }
    expect(isLocalVoiceSimulation(runtime)).toBe(false)
    expect(requiresVoiceConfirmation({ action: 'STOP' }, runtime)).toBe(true)
    expect(requiresVoiceConfirmation({ action: 'DEVICE_COMMAND', deviceCommandType: 'UAV_RETURN' }, runtime)).toBe(true)
  })
  it('shows different types for the same ordinal without guessing an unknown type', () => {
    expect(voiceDeviceLabel('UAV-001')).toBe('UAV-001 · 一号无人机')
    expect(voiceDeviceLabel('USV-001')).toBe('USV-001 · 一号无人艇')
    expect(voiceDeviceLabel('DEVICE-001')).toBe('DEVICE-001')
    expect(voiceDeviceLabel(undefined)).toBe('目标未明确')
  })
})

import { describe, expect, it } from 'vitest'
import { sequenceMatchesSpeech, sequenceStateValid, splitVoiceSequence } from '../voiceSequence'
import type { VoiceSequencePlanStep } from '@/types/voiceControl'

const four: VoiceSequencePlanStep[] = [
  { index: 0, action: 'START' },
  { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_HOVER' },
  { index: 2, action: 'WAIT', waitSeconds: 5 },
  { index: 3, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_RESUME' },
]
describe('controlled 2–4 step sequences', () => {
  it('keeps four clauses and resolves only the explicit preceding target', () => {
    const text = '开始任务，让一号机悬停，等待5秒，然后归队'
    expect(splitVoiceSequence(text)).toEqual(['开始任务', '一号无人机悬停', '等待5秒', '归队'])
    expect(sequenceMatchesSpeech(text, four, (target, command, clause) => target === 'UAV-001'
      && (command === 'UAV_HOVER' ? clause === '一号无人机悬停' : clause === 'UAV-001归队'))).toBe(true)
    expect(sequenceStateValid(four, 'PREPARED')).toBe(true)
    expect(sequenceStateValid(four, 'RUNNING')).toBe(false)
  })
  it('rejects dropped clauses, oversized waits and impossible state ordering', () => {
    expect(sequenceMatchesSpeech('开始任务，一号机悬停，等待5秒，归队，再驻留', four, () => true)).toBe(false)
    expect(sequenceStateValid([{ index: 0, action: 'START' }, { index: 1, action: 'START' }], 'PREPARED')).toBe(false)
    expect(sequenceStateValid([{ index: 0, action: 'PAUSE' }, { index: 1, action: 'WAIT', waitSeconds: 61 }], 'RUNNING')).toBe(false)
    expect(sequenceStateValid([{ index: 0, action: 'PAUSE' }, { index: 1, action: 'WAIT', waitSeconds: 3 }, { index: 2, action: 'RESUME' }], 'RUNNING')).toBe(true)
  })
})

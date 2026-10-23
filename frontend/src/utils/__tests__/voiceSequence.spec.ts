import { describe, expect, it } from 'vitest'
import { sequenceInterruptionNotice, sequenceStepHint, sequenceMatchesSpeech, sequenceStateValid, splitVoiceSequence } from '../voiceSequence'
import type { VoiceExecution, VoiceSequenceExecutionStep, VoiceSequencePlanStep } from '@/types/voiceControl'

const four: VoiceSequencePlanStep[] = [
  { index: 0, action: 'START' },
  { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_HOVER' },
  { index: 2, action: 'WAIT', waitSeconds: 5 },
  { index: 3, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_RESUME' },
]

function receipt(state: VoiceExecution['state'] = 'EXECUTING'): VoiceExecution {
  return {
    executionId: 'parent', proposalId: 'proposal', commandId: 'command', runtimeRef: 'runtime', runtimeGeneration: 'generation',
    action: 'SEQUENCE', state, outcome: 'UNKNOWN', errorCode: null, timedOutAt: null, presentationStatus: 'NOT_REQUIRED',
    currentStepIndex: 1, sequenceStatus: 'WAITING_STEP_PRECONDITION', createdAt: '', updatedAt: '',
  }
}
const pendingStep: VoiceSequenceExecutionStep = { index: 2, action: 'PAUSE', state: 'PENDING', executionId: null, errorCode: null }

describe('sequence receipt feedback', () => {
  it('shows arrival for every parallel movement, not just the first unfinished step', () => {
    const step: VoiceSequenceExecutionStep = { ...pendingStep, action: 'DEVICE_COMMAND', state: 'EXECUTING', targetDeviceCode: 'USV-002', deviceCommandType: 'USV_RETURN', executionId: 'second-child' }
    expect(sequenceStepHint({ ...receipt(), currentStepIndex: 0, sequenceStatus: 'EXECUTING_DEVICE_COMMAND' }, step)).toBe('指令已接受，等待实际到位')
  })
  it('distinguishes independent dispatch, device conflicts and explicit waits', () => {
    const first: VoiceSequenceExecutionStep = { ...pendingStep, index: 0, action: 'DEVICE_COMMAND', state: 'EXECUTING', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_RESUME', executionId: 'first-child' }
    const next: VoiceSequenceExecutionStep = { ...pendingStep, index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'USV-002', deviceCommandType: 'USV_HOLD' }
    const execution = { ...receipt(), steps: [first, next] }
    expect(sequenceStepHint(execution, next)).toBe('等待调度 · 可与其他设备并行')
    expect(sequenceStepHint(execution, { ...next, targetDeviceCode: 'UAV-001' })).toBe('等待同一设备前序动作完成')
    expect(sequenceStepHint(execution, { ...next, action: 'PAUSE' })).toBe('等待前序动作完成')
    expect(sequenceStepHint({ ...execution, steps: [{ ...first, action: 'WAIT', waitSeconds: 3 }, next] }, next)).toBe('等待计时步骤完成')
  })
  it.each(['COMPLETED', 'STOPPED', 'LOST'] as const)('explains the matching runtime state %s without rolling back successful steps', state => {
    const execution = { ...receipt('INVALIDATED'), errorCode: 'INVALID_STATE', steps: [{ ...pendingStep, state: 'SUCCEEDED' as const, index: 0 }, pendingStep] }
    const notice = sequenceInterruptionNotice(execution, { runtimeRef: 'runtime', runtimeGeneration: 'generation', state })
    expect(notice).toContain({ COMPLETED: '当前任务已完成', STOPPED: '当前任务已停止', LOST: '当前算法进程已丢失' }[state])
    expect(notice).toContain('未下发：暂停任务')
    expect(notice).toContain('已成功步骤不会自动撤销')
  })
  it('never explains an old receipt using a different runtime or generation', () => {
    for (const context of [{ runtimeRef: 'new', runtimeGeneration: 'generation' }, { runtimeRef: 'runtime', runtimeGeneration: 'new' }]) {
      const notice = sequenceInterruptionNotice({ ...receipt('FAILED'), errorCode: 'INVALID_STATE' }, { ...context, state: 'COMPLETED' })
      expect(notice).not.toContain('当前任务已完成')
      expect(notice).toContain('运行状态不满足')
    }
  })
  it('keeps issued steps out of the unissued list and preserves unknown timeout outcomes', () => {
    const notice = sequenceInterruptionNotice({ ...receipt('TIMED_OUT'), steps: [{ ...pendingStep, executionId: 'child' }, { ...pendingStep, startedAt: 'now' }] })
    expect(notice).not.toContain('未下发：')
    expect(notice).toContain('结果仍待核对')
  })
  it.each(['GENERATION_MISMATCH', 'HEARTBEAT_STALE', 'SEQUENCE_CHILD_MISSING'])('adds a known cause for %s', errorCode => {
    expect(sequenceInterruptionNotice({ ...receipt('INVALIDATED'), errorCode })).not.toMatch(/^顺序指令/)
  })
  it.each(['FAILED', 'REJECTED', 'INVALIDATED', 'TIMED_OUT'] as const)('does not suggest pending steps will continue after %s', state => {
    expect(sequenceStepHint(receipt(state), pendingStep)).toBe('未执行（顺序已中断）')
    expect(sequenceInterruptionNotice(receipt(state))).toContain('后续步骤不会自动继续')
    expect(sequenceInterruptionNotice(receipt(state))).toContain('已成功步骤不会自动撤销')
    expect(sequenceStepHint(receipt(state), { ...pendingStep, state: 'SUCCEEDED' })).toBeNull()
  })
  it.each(['QUEUED', 'DISPATCHED', 'ACCEPTED', 'EXECUTING', 'PENDING'] as const)('does not claim a step with an execution ID was never executed (%s)', state => {
    const hint = sequenceStepHint(receipt('TIMED_OUT'), { ...pendingStep, state, executionId: 'child' })
    expect(hint).toContain('结果待核对')
    expect(hint).not.toContain('未执行')
  })
  it('separates a stopped timer from an uncertain device action', () => {
    expect(sequenceStepHint(receipt('INVALIDATED'), { ...pendingStep, action: 'WAIT', state: 'EXECUTING', waitSeconds: 5 })).toBe('等待已中断')
    expect(sequenceStepHint(receipt(), { ...pendingStep, action: 'WAIT', state: 'EXECUTING', waitSeconds: 5 })).toBe('计时等待中')
  })
  it('keeps normal waiting and successful sequence feedback unchanged', () => {
    expect(sequenceStepHint(receipt(), pendingStep)).toBe('等待前一步')
    expect(sequenceInterruptionNotice(receipt())).toBe('')
    expect(sequenceInterruptionNotice(receipt('SUCCEEDED'))).toBe('')
    expect(sequenceStepHint(receipt('SUCCEEDED'), pendingStep)).toBe('步骤回执待核对')
  })
  it('distinguishes accepted movement from arrival and warns against replaying timed-out sequences', () => {
    const step: VoiceSequenceExecutionStep = { ...pendingStep, index: 1, state: 'EXECUTING', action: 'DEVICE_COMMAND', deviceCommandType: 'USV_RETURN', executionId: 'child' }
    expect(sequenceStepHint(receipt(), step)).toBe('指令已接受，等待实际到位')
    expect(sequenceInterruptionNotice(receipt('TIMED_OUT'))).toContain('请勿重复下发整条指令')
  })
})
describe('controlled 2–4 step sequences', () => {
  it('accepts the screenshot boat alias in both device clauses', () => {
    const steps: VoiceSequencePlanStep[] = [
      { index: 0, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_RETURN' },
      { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'USV-002', deviceCommandType: 'USV_HOLD' },
    ]
    expect(sequenceMatchesSpeech('一号无人机返航，然后让二号无人船驻留', steps,
      (target, command, clause) => target === 'UAV-001' ? clause === '一号无人机返航' : clause === '二号无人艇驻留')).toBe(true)
  })
  it('preserves explicit first-then ordering and rejects a dropped or invented dependency', () => {
    const steps: VoiceSequencePlanStep[] = [
      { index: 0, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_RESUME' },
      { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'USV-002', deviceCommandType: 'USV_HOLD', waitForPrevious: true },
    ]
    expect(sequenceMatchesSpeech('先让一号无人机归队，再让二号无人艇驻留', steps, () => true)).toBe(true)
    expect(sequenceMatchesSpeech('一号无人机归队，二号无人艇驻留', steps, () => true)).toBe(false)
    delete steps[1]!.waitForPrevious
    expect(sequenceMatchesSpeech('先让一号无人机归队，再让二号无人艇驻留', steps, () => true)).toBe(false)
    expect(sequenceMatchesSpeech('一号无人机归队，二号无人艇驻留', steps, () => true)).toBe(true)
  })
  it.each(['二号无人船主流', '二号无人听归队', '一号无人挺住留'])('does not widen single-command corrections after splitting: %s', command => {
    const steps: VoiceSequencePlanStep[] = [
      { index: 0, action: 'START' },
      { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'USV-002', deviceCommandType: 'USV_HOLD' },
    ]
    expect(sequenceMatchesSpeech(`开始任务，然后${command}`, steps, () => true)).toBe(false)
  })

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

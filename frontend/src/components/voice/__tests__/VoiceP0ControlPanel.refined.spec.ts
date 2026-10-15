import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'

import VoiceP0ControlPanel from '@/components/voice/VoiceP0ControlPanel.vue'
import VoiceIntelligenceInput from '@/components/voice/VoiceIntelligenceInput.vue'
import { useAuthStore } from '@/stores/auth'
import { useVoiceControlStore } from '@/stores/voiceControl'
import { appendExperimentEvent } from '@/services/experimentArchive'
import type { VoiceExecution, VoiceIntent, VoicePresentationChallenge, VoiceProposal, VoiceRuntimeContext } from '@/types/voiceControl'
import type { AlgorithmRuntimeFrame } from '@/types/mission'

vi.mock('@/services/experimentArchive', () => ({ appendExperimentEvent: vi.fn() }))

const interpretationId = '66666666-6666-4666-8666-666666666666'
const replacementInterpretationId = '77777777-7777-4777-8777-777777777777'
const sixDeviceFleet = ['UAV-001', 'UAV-002', 'UAV-003', 'USV-001', 'USV-002', 'USV-003']

function runtime(): VoiceRuntimeContext {
  return {
    runtimeRef: '11111111-1111-4111-8111-111111111111',
    runtimeGeneration: '22222222-2222-4222-8222-222222222222',
    contextVersion: 3,
    stateVersion: 2,
    runtimeScope: 'MISSION_CENTER',
    runtimeKind: 'STANDALONE_ALGORITHM',
    executionBackend: 'PYTHON_SIMULATION',
    algorithmRunId: '178980000001',
    missionId: null,
    missionRunId: null,
    state: 'RUNNING',
    protocolVersion: 'algorithm.command.v1',
    capabilities: ['START', 'PAUSE', 'RESUME', 'STOP', 'DEVICE_COMMAND'],
    lastHeartbeatReceivedAt: new Date().toISOString(),
    latestFrameSequence: 12,
    sceneReady: true,
  }
}

function frozenProposal(id = interpretationId, proposalId = '99999999-9999-4999-8999-999999999999'): VoiceProposal {
  const context = useVoiceControlStore().context!
  return {
    proposalId,
    interpretationId: id,
    status: 'AWAITING_CONFIRMATION',
    planVersion: 1,
    planHash: `plan-hash-${proposalId}`,
    requiresConfirmation: true,
    createdAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 30_000).toISOString(),
    executionId: null,
    plan: {
      runtimeRef: context.runtimeRef,
      runtimeGeneration: context.runtimeGeneration,
      contextVersion: context.contextVersion,
      stateVersion: context.stateVersion,
      action: 'DEVICE_COMMAND',
      targetDeviceCode: 'UAV-001',
      deviceCommandType: 'UAV_HOVER',
      explicitDeviceCodes: ['UAV-001'],
      policyVersion: 'voice-p0.v1',
    },
  }
}

function receipt(state: VoiceExecution['state'] = 'SUCCEEDED', presentationStatus: VoiceExecution['presentationStatus'] = 'NOT_REQUIRED'): VoiceExecution {
  const context = useVoiceControlStore().context!
  return {
    executionId: '33333333-3333-4333-8333-333333333333',
    proposalId: '99999999-9999-4999-8999-999999999999',
    commandId: '55555555-5555-4555-8555-555555555555',
    runtimeRef: context.runtimeRef,
    runtimeGeneration: context.runtimeGeneration,
    action: 'DEVICE_COMMAND',
    state,
    outcome: state === 'SUCCEEDED' ? 'SUCCESS' : 'UNKNOWN',
    errorCode: null,
    timedOutAt: state === 'TIMED_OUT' ? new Date().toISOString() : null,
    presentationStatus,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  }
}

function createPanel(registered = true, unityConnected = false) {
  const store = useVoiceControlStore()
  const context = runtime()
  store.contexts = [context]
  store.selectedRuntimeRef = context.runtimeRef
  store.expectedAlgorithmRunId = context.algorithmRunId
  store.selectAlgorithmRun = vi.fn().mockResolvedValue(true)
  store.recover = vi.fn().mockResolvedValue(undefined)
  store.refreshContexts = vi.fn().mockResolvedValue(true)
  store.poll = vi.fn().mockResolvedValue(undefined)
  store.takePresentationBinding = vi.fn().mockResolvedValue(undefined)
  store.requestPresentationChallenge = vi.fn().mockResolvedValue(undefined)
  store.propose = vi.fn().mockImplementation(async (_intent, sourceId) => {
    store.proposal = frozenProposal(sourceId)
    store.execution = null
  })
  store.confirm = vi.fn().mockImplementation(async () => {
    store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
    store.execution = receipt()
  })
  store.cancel = vi.fn().mockImplementation(async () => {
    store.proposal = { ...store.proposal!, status: 'CANCELLED' }
  })
  const wrapper = mount(VoiceP0ControlPanel, {
    props: {
      refined: true,
      runtimeHint: {
        algorithmRunId: registered ? context.algorithmRunId : '',
        state: context.state,
        sceneReady: true,
        deviceCodes: ['UAV-001', 'USV-001'],
        latestFrameSequence: context.latestFrameSequence,
      },
      unitySession: { connected: unityConnected, unityInstanceId: 'unity-test', sceneRevision: 1 },
    },
  })
  return { wrapper, store, input: wrapper.getComponent(VoiceIntelligenceInput) }
}

type Panel = ReturnType<typeof createPanel>
const mounted: Panel[] = []

async function panel() {
  const result = createPanel()
  mounted.push(result)
  await flushPromises()
  return result
}

async function preview(input: Panel['input'], id = interpretationId) {
  input.vm.$emit('candidate', 'SINGLE_DEVICE_CONTROL', id)
  await flushPromises()
}

async function voice(input: Panel['input'], intent: VoiceIntent = 'SINGLE_DEVICE_CONTROL', id = interpretationId) {
  input.vm.$emit('voiceCandidate', intent, id, { startedAt: Date.now() - 1200, asrRequestMs: 900, parseMs: 300 })
  await flushPromises()
}

function sequenceProposal(target = 'UAV-001'): VoiceProposal {
  const frozen = frozenProposal()
  return {
    ...frozen,
    plan: {
      ...frozen.plan,
      action: 'SEQUENCE',
      targetDeviceCode: undefined,
      deviceCommandType: undefined,
      explicitDeviceCodes: ['UAV-001', 'USV-001'],
      steps: [
        { index: 0, action: 'START' },
        { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: target,
          deviceCommandType: target.startsWith('UAV-') ? 'UAV_HOVER' : 'USV_HOLD' },
      ],
    },
  }
}

function fleetProposal(action: 'START' | 'PAUSE' | 'RESUME' | 'STOP', id = interpretationId): VoiceProposal {
  const frozen = frozenProposal(id, id)
  return {
    ...frozen,
    plan: {
      ...frozen.plan,
      action,
      targetDeviceCode: undefined,
      deviceCommandType: undefined,
      explicitDeviceCodes: ['UAV-001', 'USV-001'],
    },
  }
}

function delayProposal(store: Panel['store'], proposal = frozenProposal()) {
  let release!: () => void
  store.propose = vi.fn().mockImplementation(async () => {
    await new Promise<void>(resolve => { release = resolve })
    store.proposal = proposal
  })
  return () => release()
}

async function guardedConfirm(wrapper: Panel['wrapper']) {
  const methods = wrapper.vm.$.setupState as unknown as { confirm: () => Promise<void> }
  await methods.confirm()
  await nextTick()
}

async function pendingPresentationProbe() {
  vi.stubEnv('VITE_VOICE_UNITY_PRESENTATION_V1', 'true')
  const result = await panel()
  const bindingId = '88888888-8888-4888-8888-888888888888'
  const runtimeGeneration = result.store.context!.runtimeGeneration
  result.store.presentationBinding = { bindingId, runtimeGeneration }
  await result.wrapper.setProps({ unitySession: { connected: true, unityInstanceId: 'unity-test', sceneRevision: 1 } })
  await flushPromises()
  const setup = result.wrapper.vm.$.setupState as unknown as {
    presentationBridgeReady: boolean
    requestPresentationProbe: () => Promise<void>
  }
  setup.presentationBridgeReady = true
  const challenge: VoicePresentationChallenge = {
    bindingId, runtimeGeneration, kind: 'SCENE_READY', executionId: null,
    requestId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', sequence: 1,
    expiresAt: new Date(Date.now() + 5000).toISOString(),
  }
  let release!: () => void
  result.store.requestPresentationChallenge = vi.fn().mockImplementation(async () => {
    await new Promise<void>(resolve => { release = resolve })
    result.store.presentationChallenge = challenge
    return challenge
  })
  const completion = setup.requestPresentationProbe()
  expect(result.store.requestPresentationChallenge).toHaveBeenCalledExactlyOnceWith('SCENE_READY', null)
  return { ...result, release, completion, challenge }
}

describe('VoiceP0ControlPanel refined controls', () => {
  it('establishes the Unity presentation binding when mounted into an already ready scene', async () => {
    vi.stubEnv('VITE_VOICE_UNITY_PRESENTATION_V1', 'true')
    const result = createPanel(true, true)
    mounted.push(result)
    await flushPromises()
    expect(result.store.takePresentationBinding).toHaveBeenCalledOnce()
  })

  it('does not recover or poll before a runtime is registered, then connects once', async () => {
    const result = createPanel(false)
    mounted.push(result)
    const { wrapper, store } = result
    store.selectedRuntimeRef = ''
    await flushPromises()
    expect(wrapper.text()).toContain('后端准备成功后将自动连接语音控制')
    expect(wrapper.findAll('button').some(button => button.text() === '重新连接')).toBe(false)
    expect(store.selectAlgorithmRun).toHaveBeenCalledOnce()
    expect(store.recover).not.toHaveBeenCalled()
    store.errorCode = 'RUNTIME_CONTEXT_NOT_FOUND'
    store.error = '未找到算法运行 7001 对应的控制上下文。'
    await nextTick()
    expect(wrapper.text()).not.toContain('未找到算法运行')
    store.errorCode = 'LOCAL_RECOVERY_UNAVAILABLE'
    store.error = '恢复记录不可用，请核对原请求'
    await nextTick()
    expect(wrapper.text()).toContain('恢复记录不可用，请核对原请求')
    await vi.advanceTimersByTimeAsync(6000)
    document.dispatchEvent(new Event('visibilitychange'))
    expect(store.refreshContexts).not.toHaveBeenCalled()
    expect(store.poll).not.toHaveBeenCalled()
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), algorithmRunId: runtime().algorithmRunId } })
    await flushPromises()
    expect(store.selectAlgorithmRun).toHaveBeenCalledTimes(2)
    expect(store.recover).toHaveBeenCalledOnce()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('does not start recovery after unmounting during a context lookup', async () => {
    const { wrapper, store } = await panel()
    let finish!: () => void
    vi.mocked(store.selectAlgorithmRun).mockImplementationOnce(() => new Promise<boolean>(resolve => { finish = () => resolve(true) }))
    vi.mocked(store.recover).mockClear()
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), algorithmRunId: 'next-run' } })
    wrapper.unmount()
    finish()
    await flushPromises()
    expect(store.recover).not.toHaveBeenCalled()
  })

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-09T09:00:00.000Z'))
    vi.stubEnv('VITE_VOICE_P1_PREPARATION', 'true')
    vi.stubEnv('VITE_VOICE_P1_BACKEND', 'true')
    vi.stubEnv('VITE_VOICE_P1_MOCK', 'false')
    vi.stubEnv('VITE_VOICE_ASR_ONLY', 'false')
    setActivePinia(createPinia())
    useAuthStore().user = { username: 'admin', role: 'ADMIN' }
  })

  afterEach(() => {
    for (const item of mounted.splice(0)) item.wrapper.unmount()
    vi.useRealTimers()
    vi.unstubAllEnvs()
  })

  it('keeps voice execution and feedback without archiving interpretations or receipts', async () => {
    vi.mocked(appendExperimentEvent).mockClear()
    const { wrapper, store, input } = await panel()
    input.vm.$emit('interpretation', {
      text: '一号无人机悬停', origin: 'speech', result: { requestId: interpretationId },
    })
    await voice(input)
    expect(store.propose).toHaveBeenCalledExactlyOnceWith('SINGLE_DEVICE_CONTROL', interpretationId)
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(store.execution?.state).toBe('SUCCEEDED')
    expect(wrapper.get('.refined-result').text()).toContain('算法执行成功')
    expect(appendExperimentEvent).not.toHaveBeenCalled()
  })

  it('disables view clearing until execution and recovery have settled, and explains that clearing is not cancellation', async () => {
    localStorage.clear()
    const { wrapper, store } = await panel()
    await wrapper.setProps({ refined: false })
    store.execution = receipt('TIMED_OUT')
    await nextTick()
    const clearButton = () => wrapper.findAll('button').find(button => button.text() === '清除已结束回执')!
    expect(clearButton().attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('指令执行或展示结果仍待核对')
    store.execution = receipt()
    await nextTick()
    expect(clearButton().attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).toContain('清除视图不等于取消设备动作')
    store.beginJournal('CONFIRM', '/confirm', { expectedPlanVersion: 1, expectedPlanHash: 'hash' }, 'proposal')
    await nextTick()
    expect(clearButton().attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('存在待核对的控制或展示请求')
    store.updateJournal({ phase: 'RESOURCE_RECEIVED' })
    await nextTick()
    expect(clearButton().attributes('disabled')).toBeUndefined()
    await clearButton().trigger('click')
    expect(store.execution).toBeNull()
    expect(store.loadJournal()?.phase).toBe('RESOURCE_RECEIVED')
    expect(store.cancel).not.toHaveBeenCalled()
    localStorage.clear()
  })

  it.each([true, false])('shows interrupted sequence steps without suggesting they will resume (refined=%s)', async refined => {
    const { wrapper, store } = await panel()
    await wrapper.setProps({ refined })
    store.execution = {
      ...receipt('TIMED_OUT'), action: 'SEQUENCE', currentStepIndex: 1, sequenceStatus: 'FAILED',
      steps: [
        { index: 0, action: 'START', state: 'SUCCEEDED', executionId: 'start', errorCode: null },
        { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'USV-001', deviceCommandType: 'USV_RETURN', state: 'TIMED_OUT', executionId: 'return', errorCode: 'ACK_TIMEOUT' },
        { index: 2, action: 'WAIT', waitSeconds: 5, state: 'PENDING', executionId: null, errorCode: null },
        { index: 3, action: 'PAUSE', state: 'PENDING', executionId: null, errorCode: null },
      ],
    }
    await nextTick()
    const result = wrapper.get(refined ? '.refined-result' : '.result')
    expect(result.text()).toContain('USV-001 · 一号无人艇')
    expect(result.text()).toContain('等待 5 秒')
    expect(result.text()).toContain('暂停任务')
    expect(result.text()).toContain('未执行（顺序已中断）')
    expect(result.text()).not.toContain('等待前一步')
    expect(result.text()).toContain('请勿重复下发整条指令')
    expect(store.confirm).not.toHaveBeenCalled()
    store.contexts[0]!.state = 'COMPLETED'
    store.execution = { ...store.execution!, state: 'INVALIDATED', errorCode: 'INVALID_STATE' }
    await nextTick()
    expect(result.text()).toContain('当前任务已完成')
    expect(result.text()).toContain('未下发：等待 5 秒；暂停任务')
    expect(result.text()).toContain('已成功步骤不会自动撤销')
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it.each([true, false])('offers explicit storage recovery without clearing records or executing commands (refined=%s)', async refined => {
    const { wrapper, store } = await panel()
    await wrapper.setProps({ refined })
    store.recoveryAvailable = false
    await nextTick()
    const recoveryButton = () => wrapper.findAll('button').find(button => button.text() === '检查恢复记录')!
    expect(recoveryButton()).toBeDefined()
    store.recoveryPending = true
    await nextTick()
    expect(recoveryButton().attributes('disabled')).toBeDefined()
    store.recoveryPending = false
    await nextTick()
    const clear = vi.spyOn(store, 'clearActive')
    vi.mocked(store.recover).mockClear()
    await recoveryButton().trigger('click')
    await flushPromises()
    expect(store.recover).toHaveBeenCalledOnce()
    expect(clear).not.toHaveBeenCalled()
    expect(store.propose).not.toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('shows aircraft parking conflicts as unverified until a fresh consistent landing frame arrives', async () => {
    const { wrapper, store } = await panel()
    store.proposal = frozenProposal()
    store.proposal.plan.deviceCommandType = 'UAV_RETURN'
    store.execution = receipt()
    await nextTick()
    const sample = {
      runId: Number(runtime().algorithmRunId), sequence: 2,
      agents: [{ code: 'UAV-001', type: 'UAV', status: 'RETURNED' }],
      metrics: { deviceControlStates: { 'UAV-001': { motionState: 'RETURNED', parkingKind: 'BERTH', returnedAtHome: true } } },
    } as unknown as AlgorithmRuntimeFrame
    await wrapper.setProps({ frame: sample })
    expect(wrapper.get('.refined-result-head').text()).toBe('指令已接受 · 状态待核对')
    expect(wrapper.get('.device-action-progress').text()).toContain('停靠位置待核对')
    expect(wrapper.get('.refined-result-head').find('svg').exists()).toBe(false)
    await wrapper.setProps({ frame: { ...sample, sequence: 3, metrics: {
      deviceControlStates: { 'UAV-001': { motionState: 'RETURNED', parkingKind: 'HELIPAD', parkingSlot: 'UAV-001', returnedAtHome: true } },
    } } })
    expect(wrapper.get('.refined-result-head').text()).toBe('设备动作已完成')
    expect(wrapper.get('.device-action-progress').text()).toBe('已到停机坪，着陆完成')
    expect(wrapper.get('.refined-result-head').find('svg').exists()).toBe(true)
  })

  it('renders the real refined input without file testing, manual actions, or protocol debugging', async () => {
    const { wrapper, input } = await panel()
    expect(input.props('refined')).toBe(true)
    expect(input.props('autoExecuteSpeech')).toBe(true)
    expect(wrapper.find('.refined-mic').exists()).toBe(true)
    expect(wrapper.find('textarea[aria-label="识别文字"]').exists()).toBe(true)
    expect(wrapper.find('input[type="file"]').exists()).toBe(false)
    expect(wrapper.find('.action-grid').exists()).toBe(false)
    expect(wrapper.find('.runtime-card').exists()).toBe(false)
    expect(wrapper.find('.result-details').exists()).toBe(false)
    expect(wrapper.find('.voice-modal').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('清除本地视图')
    expect(wrapper.text()).not.toContain('VOICE / LLM · P0')
  })

  it('embeds compact controls without a repeated heading and keeps permission and recovery notices visible', async () => {
    const { wrapper, store, input } = await panel()
    expect(wrapper.props('compact')).toBe(false)
    expect(wrapper.find('.refined-head').exists()).toBe(true)
    await wrapper.setProps({ compact: true })
    expect(wrapper.get('.voice-p0').classes()).toContain('voice-compact')
    expect(input.props('compact')).toBe(true)
    expect(wrapper.find('.refined-head').exists()).toBe(false)
    expect(wrapper.find('h2').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('一句话，调度海空设备')
    expect(wrapper.get('.refined-capture-title').text()).toBe('点击录音')
    expect(wrapper.text()).toContain('支持返航、降落、停止；文字编辑后需确认')
    useAuthStore().user = { username: 'viewer', role: 'VIEWER' }
    store.responseUnknown = true
    await nextTick()
    expect(wrapper.text()).toContain('当前账号没有控制权限')
    expect(wrapper.text()).toContain('上次请求结果尚未确认')
    expect(wrapper.get('button[aria-label="开始录音"]').attributes('disabled')).toBeDefined()
    expect(wrapper.findAll('button').some(button => button.text() === '核对上次请求')).toBe(true)
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it.each([false, true])('creates the backend frozen plan without executing until the operator confirms (compact=%s)', async compact => {
    const { wrapper, store, input } = await panel()
    await wrapper.setProps({ compact })
    await preview(input)
    expect(store.propose).toHaveBeenCalledExactlyOnceWith('SINGLE_DEVICE_CONTROL', interpretationId)
    expect(store.confirm).not.toHaveBeenCalled()
    expect(wrapper.get('.refined-plan').text()).toContain('UAV-001 · 一号无人机 · 无人机悬停')
    expect(input.props('proposalReady')).toBe(true)
    expect(wrapper.find('.refined-preview').exists()).toBe(false)
    expect(wrapper.get('.refined-confirm').attributes('disabled')).toBeUndefined()
    await wrapper.get('.refined-confirm').trigger('click')
    await flushPromises()
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.get('.refined-result').text()).toContain('算法执行成功')
  })

  it.each([false, true])('automatically submits a newly recognized device command through the frozen proposal (compact=%s)', async compact => {
    const { wrapper, store, input } = await panel()
    await wrapper.setProps({ compact })
    const calls: string[] = []
    store.propose = vi.fn().mockImplementation(async (_intent, id) => {
      calls.push('propose')
      store.proposal = frozenProposal(id)
    })
    store.confirm = vi.fn().mockImplementation(async () => {
      calls.push('confirm')
      expect(store.proposal?.interpretationId).toBe(interpretationId)
      expect(store.proposal?.plan.targetDeviceCode).toBe('UAV-001')
      store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
      store.execution = receipt()
    })
    await voice(input)
    expect(calls).toEqual(['propose', 'confirm'])
    expect(store.propose).toHaveBeenCalledExactlyOnceWith('SINGLE_DEVICE_CONTROL', interpretationId)
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
    expect(wrapper.get('.refined-result').text()).toContain('算法执行成功')
    expect(wrapper.text()).toContain('语音自动执行')
  })

  it('separates a previous receipt from a new draft without erasing execution state', async () => {
    const { wrapper, store, input } = await panel()
    await voice(input)
    const previous = store.execution
    expect(wrapper.text()).toContain('语音指令已自动下发')
    await input.get('textarea').setValue('这句话不是设备指令')
    expect(wrapper.text()).not.toContain('语音指令已自动下发')
    expect(wrapper.get('.previous-execution-label').text()).toContain('非本次识别结果')
    expect(store.execution).toEqual(previous)
    expect(store.confirm).toHaveBeenCalledOnce()
  })

  it('clears previous automatic feedback before a new microphone request fails', async () => {
    const { wrapper, store, input } = await panel()
    await voice(input)
    vi.stubGlobal('MediaRecorder', undefined)
    try {
      await input.get('.refined-mic').trigger('click')
      await flushPromises()
      expect(wrapper.text()).toContain('当前浏览器不支持录音')
      expect(wrapper.text()).not.toContain('语音指令已自动下发')
      expect(wrapper.get('.previous-execution-label').text()).toContain('上一条指令回执')
      expect(store.confirm).toHaveBeenCalledOnce()
    } finally { vi.unstubAllGlobals() }
  })

  it.each(['EXECUTING', 'TIMED_OUT'] as const)('retains %s recovery and dispatch protection when editing', async state => {
    const { wrapper, store, input } = await panel()
    store.proposal = { ...frozenProposal(), status: 'CONFIRMED' }
    store.execution = receipt(state)
    await flushPromises()
    await input.get('textarea').setValue('一号无人艇驻留')
    expect(wrapper.get('.previous-execution-label').text()).toContain('非本次识别结果')
    expect(input.props('submissionDisabled')).toBe(true)
    expect(store.execution?.state).toBe(state)
    await voice(input)
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('does not restore stale automatic success when a confirmation arrives after editing', async () => {
    const { wrapper, store, input } = await panel()
    let release!: () => void
    store.confirm = vi.fn().mockImplementation(async () => {
      await new Promise<void>(resolve => { release = resolve })
      store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
      store.execution = receipt()
    })
    await voice(input)
    expect(store.confirm).toHaveBeenCalledOnce()
    await input.get('textarea').setValue('不要执行新指令')
    release()
    await flushPromises()
    expect(wrapper.text()).not.toContain('语音指令已自动下发')
    expect(wrapper.get('.previous-execution-label').text()).toContain('非本次识别结果')
    expect(store.execution?.state).toBe('SUCCEEDED')
    expect(store.confirm).toHaveBeenCalledOnce()
  })

  it('shows a new matching receipt as current after an earlier receipt was marked previous', async () => {
    const { wrapper, store, input } = await panel()
    await voice(input)
    await input.get('textarea').setValue('二号无人艇驻留')
    expect(wrapper.find('.previous-execution-label').exists()).toBe(true)
    const nextProposalId = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    store.propose = vi.fn().mockImplementation(async (_intent, id) => {
      store.proposal = frozenProposal(id, nextProposalId)
      store.execution = null
    })
    store.confirm = vi.fn().mockImplementation(async () => {
      store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
      store.execution = { ...receipt(), proposalId: nextProposalId }
    })
    await voice(input, 'SINGLE_DEVICE_CONTROL', replacementInterpretationId)
    expect(wrapper.find('.previous-execution-label').exists()).toBe(false)
    expect(wrapper.text()).toContain('语音指令已自动下发')
    expect(store.confirm).toHaveBeenCalledOnce()
  })

  it.each([
    ['UAV-001', 'UAV_RETURN'], ['UAV-001', 'UAV_LAND'],
    ['USV-001', 'USV_RETURN'], ['USV-001', 'USV_STOP'],
  ] as const)('automatically executes a validated simulation %s %s command once', async (targetDeviceCode, deviceCommandType) => {
    const { wrapper, store, input } = await panel()
    store.propose = vi.fn().mockImplementation(async (_intent, id) => {
      const frozen = frozenProposal(id)
      store.proposal = {
        ...frozen,
        plan: { ...frozen.plan, targetDeviceCode, deviceCommandType, explicitDeviceCodes: [targetDeviceCode] },
      }
    })
    await voice(input)
    expect(store.propose).toHaveBeenCalledExactlyOnceWith('SINGLE_DEVICE_CONTROL', interpretationId)
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.text()).not.toContain('高风险确认')
    expect(wrapper.text()).not.toContain('高风险整条确认')
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
    expect(wrapper.get('.refined-result').text()).toContain('算法执行成功')
    await voice(input)
    expect(store.confirm).toHaveBeenCalledOnce()
  })

  it('automatically executes the complete validated START then return simulation sequence', async () => {
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, state: 'PREPARED' }]
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state: 'PREPARED' } })
    store.propose = vi.fn().mockImplementation(async () => {
      const frozen = sequenceProposal('USV-001')
      frozen.plan.steps![1]!.deviceCommandType = 'USV_RETURN'
      store.proposal = frozen
      store.execution = null
    })
    store.confirm = vi.fn().mockImplementation(async () => {
      store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
      store.execution = { ...receipt(), action: 'SEQUENCE' }
    })
    await voice(input, 'COMMAND_SEQUENCE')
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('高风险')
    expect(wrapper.text()).toContain('语音指令已自动下发')
  })

  it('still requires a click for manually previewed return text in the simulation', async () => {
    const { wrapper, store, input } = await panel()
    store.propose = vi.fn().mockImplementation(async (_intent, id) => {
      const frozen = frozenProposal(id)
      frozen.plan.deviceCommandType = 'UAV_RETURN'
      store.proposal = frozen
    })
    await preview(input)
    expect(store.confirm).not.toHaveBeenCalled()
    expect(wrapper.get('.refined-plan').text()).toContain('无人机返航')
    expect(wrapper.text()).not.toContain('高风险确认')
    await wrapper.get('.refined-confirm').trigger('click')
    await flushPromises()
    expect(store.confirm).toHaveBeenCalledOnce()
  })

  it.each(['invalid-date', '', '2020-01-01T00:00:00Z'])('fails closed on a manual plan with invalid/expired expiry %s', async expiresAt => {
    const { wrapper, store, input } = await panel()
    await preview(input)
    store.proposal = { ...store.proposal!, expiresAt }
    await flushPromises()
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
    expect(input.props('reviewStatus')).toContain('有效期无效')
    await guardedConfirm(wrapper)
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it.each(['CANCELLED', 'EXPIRED', 'INVALIDATED'] as const)('explains authoritative %s without leaving a stale confirmation prompt', async status => {
    const { wrapper, store, input } = await panel()
    await preview(input)
    store.proposal = { ...store.proposal!, status }
    await flushPromises()
    expect(input.props('reviewStatus')).toBeTruthy()
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
    expect(wrapper.get('.refined-execution-phase').text()).toBe(input.props('reviewStatus'))
    await input.get('textarea').setValue('二号无人艇驻留')
    expect(input.props('reviewStatus')).toBe('')
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('does not claim cancellation or expiry is definitive while a write result is unknown', async () => {
    const { wrapper, store, input } = await panel()
    await preview(input)
    store.proposal = { ...store.proposal!, expiresAt: 'invalid-date' }
    store.responseUnknown = true
    await flushPromises()
    expect(input.props('reviewStatus')).toBe('')
    expect(wrapper.get('.refined-execution-phase').text()).toContain('结果待核对')
    expect(input.props('submissionDisabled')).toBe(true)
  })

  it('blocks mismatched UAV actions on a USV even after a manual preview', async () => {
    const { wrapper, store, input } = await panel()
    store.propose = vi.fn().mockImplementation(async () => {
      const frozen = frozenProposal()
      frozen.plan.targetDeviceCode = 'USV-001'
      frozen.plan.explicitDeviceCodes = ['USV-001']
      frozen.plan.deviceCommandType = 'UAV_RETURN'
      store.proposal = frozen
    })
    await preview(input)
    expect(wrapper.get('.refined-confirm').attributes('disabled')).toBeDefined()
    await guardedConfirm(wrapper)
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it.each([
    ['MISSION_START', 'START', 'PREPARED'],
    ['MISSION_PAUSE', 'PAUSE', 'RUNNING'],
    ['MISSION_RESUME', 'RESUME', 'PAUSED'],
    ['MISSION_STOP', 'STOP', 'RUNNING'],
  ] as const)('automatically submits an explicit %s voice intent', async (intent, action, state) => {
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, state }]
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state } })
    store.propose = vi.fn().mockImplementation(async () => {
      const frozen = frozenProposal()
      store.proposal = { ...frozen, plan: { ...frozen.plan, action,
        targetDeviceCode: undefined, deviceCommandType: undefined,
        explicitDeviceCodes: ['UAV-001', 'USV-001'] } }
    })
    store.confirm = vi.fn().mockImplementation(async () => {
      store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
      store.execution = { ...receipt(), action }
    })
    await voice(input, intent)
    expect(store.propose).toHaveBeenCalledExactlyOnceWith(intent, interpretationId)
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
  })

  it('automatically STARTs, PAUSEs, then RESUMEs after paused scene evidence expires while heartbeats stay live', async () => {
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, state: 'PREPARED' }]
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state: 'PREPARED' } })
    const actions: string[] = []
    store.propose = vi.fn().mockImplementation(async (intent: VoiceIntent, id: string) => {
      const action = intent.replace('MISSION_', '') as 'START' | 'PAUSE' | 'RESUME'
      store.proposal = fleetProposal(action, id)
      store.execution = null
    })
    store.confirm = vi.fn().mockImplementation(async () => {
      const frozen = store.proposal!
      actions.push(frozen.plan.action)
      store.proposal = { ...frozen, status: 'CONFIRMED' }
      store.execution = { ...receipt(), proposalId: frozen.proposalId, action: frozen.plan.action }
      store.contexts = [{ ...store.context!,
        state: frozen.plan.action === 'PAUSE' ? 'PAUSED' : 'RUNNING',
        stateVersion: store.context!.stateVersion + 1,
        lastHeartbeatReceivedAt: new Date().toISOString(),
      }]
    })

    await voice(input, 'MISSION_START')
    expect(actions).toEqual(['START'])
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state: 'RUNNING' } })
    await voice(input, 'MISSION_PAUSE', replacementInterpretationId)
    expect(actions).toEqual(['START', 'PAUSE'])
    expect(store.context?.state).toBe('PAUSED')

    // A paused algorithm retains its prepared scene and still sends heartbeats;
    // the backend may age out only the short-lived Unity presentation evidence.
    vi.setSystemTime(new Date(Date.now() + 31_000))
    store.contexts = [{ ...store.context!, sceneReady: false,
      lastHeartbeatReceivedAt: new Date().toISOString() }]
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state: 'PAUSED', sceneReady: false } })
    await voice(input, 'MISSION_RESUME', 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')

    expect(actions).toEqual(['START', 'PAUSE', 'RESUME'])
    expect(store.propose).toHaveBeenCalledTimes(3)
    expect(store.confirm).toHaveBeenCalledTimes(3)
    expect(store.context?.state).toBe('RUNNING')
    expect(wrapper.get('.refined-result').text()).toContain('继续任务')
    expect(wrapper.text()).not.toContain('未自动执行')
  })

  it.each(['before-proposal', 'during-proposal'])(
    'automatically resumes a paused local simulation when scene evidence expires %s', async timing => {
      const { wrapper, store, input } = await panel()
      store.contexts = [{ ...store.context!, state: 'PAUSED', sceneReady: timing !== 'before-proposal' }]
      await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state: 'PAUSED' } })
      const release = delayProposal(store, fleetProposal('RESUME'))
      store.confirm = vi.fn().mockImplementation(async () => {
        store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
        store.execution = { ...receipt(), action: 'RESUME', proposalId: store.proposal.proposalId }
      })

      await voice(input, 'MISSION_RESUME')
      expect(store.propose).toHaveBeenCalledExactlyOnceWith('MISSION_RESUME', interpretationId)
      expect(store.confirm).not.toHaveBeenCalled()
      if (timing === 'during-proposal') {
        store.contexts = [{ ...store.context!, sceneReady: false }]
        await nextTick()
      }
      release()
      await flushPromises()

      expect(store.confirm).toHaveBeenCalledOnce()
      expect(wrapper.text()).not.toContain('未自动执行')
    },
  )

  it.each(['running-state', 'prepared-state', 'stale-heartbeat', 'missing-resume-capability'])(
    'still refuses automatic RESUME with expired scene evidence and %s', async fault => {
      const { wrapper, store, input } = await panel()
      store.contexts = [{ ...store.context!, state: fault === 'running-state' ? 'RUNNING'
        : fault === 'prepared-state' ? 'PREPARED' : 'PAUSED', sceneReady: false,
        lastHeartbeatReceivedAt: new Date(Date.now() - (fault === 'stale-heartbeat' ? 6000 : 0)).toISOString(),
        capabilities: fault === 'missing-resume-capability' ? ['START', 'PAUSE', 'STOP'] : store.context!.capabilities }]
      await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state: store.context!.state } })

      await voice(input, 'MISSION_RESUME')

      expect(store.propose).not.toHaveBeenCalled()
      expect(store.confirm).not.toHaveBeenCalled()
      expect(wrapper.text()).toContain('未自动执行')
    },
  )

  it.each(['UAV-001', 'USV-001'])('automatically submits a controlled START then hold sequence for %s', async target => {
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, state: 'PREPARED' }]
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state: 'PREPARED' } })
    store.propose = vi.fn().mockImplementation(async () => { store.proposal = sequenceProposal(target) })
    store.confirm = vi.fn().mockImplementation(async () => {
      store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
      store.execution = { ...receipt('EXECUTING'), action: 'SEQUENCE' }
    })
    await voice(input, 'COMMAND_SEQUENCE')
    expect(store.propose).toHaveBeenCalledExactlyOnceWith('COMMAND_SEQUENCE', interpretationId)
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(store.proposal?.plan.steps).toHaveLength(2)
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
  })

  it.each([
    ['MISSION_START', null],
    ['COMMAND_SEQUENCE', 'UAV-001'],
    ['COMMAND_SEQUENCE', 'USV-001'],
  ] as const)('automatically submits %s for a six-device PREVIEW fleet with target %s', async (intent, target) => {
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, state: 'PREVIEW' }]
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state: 'PREVIEW', deviceCodes: [...sixDeviceFleet] } })
    store.propose = vi.fn().mockImplementation(async () => {
      const frozen = target ? sequenceProposal(target) : frozenProposal()
      store.proposal = {
        ...frozen,
        plan: {
          ...frozen.plan,
          action: target ? 'SEQUENCE' : 'START',
          targetDeviceCode: undefined,
          deviceCommandType: undefined,
          // Backend ordering need not match scene ordering; membership must.
          explicitDeviceCodes: [...sixDeviceFleet].reverse(),
        },
      }
    })
    store.confirm = vi.fn().mockImplementation(async () => {
      store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
      store.execution = { ...receipt(), action: target ? 'SEQUENCE' : 'START' }
    })

    await voice(input, intent)

    expect(input.props('deviceCodes')).toEqual(sixDeviceFleet)
    expect(store.propose).toHaveBeenCalledExactlyOnceWith(intent, interpretationId)
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(store.proposal?.plan.explicitDeviceCodes).toHaveLength(6)
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('未自动执行')
  })

  it.each([
    ['missing-UAV', sixDeviceFleet.filter(code => code !== 'UAV-003')],
    ['missing-USV', sixDeviceFleet.filter(code => code !== 'USV-003')],
    ['extra-UAV', [...sixDeviceFleet, 'UAV-004']],
    ['extra-USV', [...sixDeviceFleet, 'USV-004']],
    ['duplicate-member', [...sixDeviceFleet.slice(0, -1), 'UAV-001']],
  ] satisfies Array<[string, string[]]>)('still blocks automatic START with a %s frozen fleet', async (_fault, members) => {
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, state: 'PREVIEW' }]
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state: 'PREVIEW', deviceCodes: [...sixDeviceFleet] } })
    store.propose = vi.fn().mockImplementation(async () => {
      const frozen = frozenProposal()
      store.proposal = {
        ...frozen,
        plan: {
          ...frozen.plan,
          action: 'START',
          targetDeviceCode: undefined,
          deviceCommandType: undefined,
          explicitDeviceCodes: members,
        },
      }
    })

    await voice(input, 'MISSION_START')

    expect(store.propose).toHaveBeenCalledExactlyOnceWith('MISSION_START', interpretationId)
    expect(store.confirm).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('未自动执行')
  })

  it('does not automatically replay a recovered or previously stored proposal', async () => {
    const { store } = await panel()
    store.proposal = frozenProposal()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(2000)
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('submits a recognized interpretation at most once even after a successful receipt', async () => {
    const { store, input } = await panel()
    await voice(input)
    await voice(input)
    expect(store.propose).toHaveBeenCalledOnce()
    expect(store.confirm).toHaveBeenCalledOnce()
  })

  it('keeps the automatic proposal and confirmation path single-flight', async () => {
    const { wrapper, store, input } = await panel()
    let release!: () => void
    store.confirm = vi.fn().mockImplementation(async () => {
      await new Promise<void>(resolve => { release = resolve })
      store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
      store.execution = receipt()
    })
    await voice(input)
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
    expect(input.props('submissionDisabled')).toBe(true)
    await voice(input, 'SINGLE_DEVICE_CONTROL', replacementInterpretationId)
    await preview(input, replacementInterpretationId)
    expect(store.propose).toHaveBeenCalledOnce()
    expect(store.confirm).toHaveBeenCalledOnce()
    release()
    await flushPromises()
  })

  it('blocks late automatic submission after the operator edits the transcript', async () => {
    const { store, input } = await panel()
    const release = delayProposal(store)
    await voice(input)
    input.vm.$emit('draftChange')
    await nextTick()
    release()
    await flushPromises()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it.each(['contextVersion', 'stateVersion', 'runtimeGeneration'] as const)(
    'blocks late automatic submission after %s changes', async field => {
      const { store, input } = await panel()
      const release = delayProposal(store)
      await voice(input)
      store.contexts = [{ ...store.context!, [field]: field === 'runtimeGeneration'
        ? 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' : (store.context![field] as number) + 1 }]
      await nextTick()
      release()
      await flushPromises()
      expect(store.confirm).not.toHaveBeenCalled()
    },
  )

  it('blocks late automatic submission after the current fleet changes', async () => {
    const { wrapper, store, input } = await panel()
    const release = delayProposal(store)
    await voice(input)
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), deviceCodes: ['UAV-002', 'USV-001'] } })
    release()
    await flushPromises()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('blocks late automatic submission when the frontend switches algorithm runs before context refresh', async () => {
    const { wrapper, store, input } = await panel()
    const release = delayProposal(store)
    await voice(input)
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), algorithmRunId: '178980000002' } })
    release()
    await flushPromises()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('blocks late automatic submission when the Unity scene is replaced', async () => {
    const { wrapper, store, input } = await panel()
    const release = delayProposal(store)
    await voice(input)
    await wrapper.setProps({ unitySession: { ...wrapper.props('unitySession'), sceneRevision: 2 } })
    release()
    await flushPromises()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('rechecks START scene readiness before automatically confirming a sequence', async () => {
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, state: 'PREPARED' }]
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state: 'PREPARED' } })
    const release = delayProposal(store, sequenceProposal())
    await voice(input, 'COMMAND_SEQUENCE')
    store.contexts = [{ ...store.context!, sceneReady: false }]
    await nextTick()
    release()
    await flushPromises()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('rechecks control permission before automatically confirming a prepared plan', async () => {
    const { store, input } = await panel()
    const release = delayProposal(store)
    await voice(input)
    useAuthStore().user = { username: 'admin', role: 'VIEWER' }
    await nextTick()
    release()
    await flushPromises()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('cancels an edited manual preview before automatically submitting a new voice interpretation', async () => {
    const { store, input } = await panel()
    await preview(input)
    input.vm.$emit('draftChange')
    await nextTick()
    const calls: string[] = []
    store.cancel = vi.fn().mockImplementation(async () => {
      calls.push('cancel')
      store.proposal = { ...store.proposal!, status: 'CANCELLED' }
    })
    store.propose = vi.fn().mockImplementation(async (_intent, id) => {
      calls.push('propose')
      store.proposal = frozenProposal(id, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
    })
    store.confirm = vi.fn().mockImplementation(async () => {
      calls.push('confirm')
      store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
      store.execution = { ...receipt(), proposalId: store.proposal.proposalId }
    })
    await voice(input, 'SINGLE_DEVICE_CONTROL', replacementInterpretationId)
    expect(calls).toEqual(['cancel', 'propose', 'confirm'])
    expect(store.confirm).toHaveBeenCalledOnce()
  })

  it('does not automatically authorize a proposal response reusing an old proposal id', async () => {
    const { store, input } = await panel()
    store.proposal = { ...frozenProposal(), status: 'CANCELLED' }
    await nextTick()
    await voice(input, 'SINGLE_DEVICE_CONTROL', replacementInterpretationId)
    expect(store.propose).toHaveBeenCalledOnce()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('does not auto-confirm or retry when the proposal write result is unknown', async () => {
    const { store, input } = await panel()
    store.propose = vi.fn().mockImplementation(async () => { store.responseUnknown = true })
    await voice(input)
    await voice(input, 'SINGLE_DEVICE_CONTROL', replacementInterpretationId)
    expect(store.propose).toHaveBeenCalledOnce()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('blocks late automatic submission after unmounting the panel', async () => {
    const result = await panel()
    const release = delayProposal(result.store)
    await voice(result.input)
    result.wrapper.unmount()
    mounted.splice(mounted.indexOf(result), 1)
    release()
    await flushPromises()
    expect(result.store.confirm).not.toHaveBeenCalled()
  })

  it.each(['VIEWER', 'OPERATOR'])('does not auto-submit for the %s role', async role => {
    const { store, input } = await panel()
    useAuthStore().user = { username: 'other', role }
    await nextTick()
    expect(input.props('autoExecuteSpeech')).toBe(false)
    await voice(input)
    expect(store.propose).not.toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it.each([
    ['executionBackend', 'REAL_DEVICE'], ['runtimeScope', 'GATEWAY'], ['runtimeKind', 'GATEWAY'],
  ])('does not auto-submit when the raw registered %s is %s', async (field, value) => {
    const { store, input } = await panel()
    store.contexts = [{ ...store.context!, [field]: value }]
    await nextTick()
    expect(input.props('autoExecuteSpeech')).toBe(false)
    await voice(input)
    expect(store.propose).not.toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('does not auto-submit commands to a legacy protocol runtime', async () => {
    const { store, input } = await panel()
    store.contexts = [{ ...store.context!, protocolVersion: 'legacy' }]
    await nextTick()
    expect(input.props('autoExecuteSpeech')).toBe(false)
    await voice(input)
    expect(store.propose).not.toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('does not automatically submit a device command before the scene is ready', async () => {
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, sceneReady: false }]
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), sceneReady: false } })
    await nextTick()
    await voice(input)
    expect(store.propose).not.toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('refreshes an expired backend scene proof and continues the same recognised command', async () => {
    vi.stubEnv('VITE_VOICE_UNITY_PRESENTATION_V1', 'true')
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, sceneReady: false }]
    await wrapper.setProps({ unitySession: { ...wrapper.props('unitySession'), connected: true } })
    await flushPromises()
    const setup = wrapper.vm.$.setupState as unknown as { presentationBridgeReady: boolean }
    setup.presentationBridgeReady = true
    store.refreshContexts = vi.fn().mockImplementation(async () => {
      store.contexts = [{ ...store.context!, sceneReady: true }]
      return true
    })
    await voice(input)
    expect(store.refreshContexts).toHaveBeenCalled()
    expect(store.propose).toHaveBeenCalledExactlyOnceWith('SINGLE_DEVICE_CONTROL', interpretationId)
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.text()).not.toContain('仿真场景尚未就绪')
  })

  it('continues after the scene-ready refresh advances the runtime state version', async () => {
    vi.stubEnv('VITE_VOICE_UNITY_PRESENTATION_V1', 'true')
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, sceneReady: false }]
    await wrapper.setProps({ unitySession: { ...wrapper.props('unitySession'), connected: true } })
    await flushPromises()
    const setup = wrapper.vm.$.setupState as unknown as { presentationBridgeReady: boolean }
    setup.presentationBridgeReady = true
    store.refreshContexts = vi.fn().mockImplementation(async () => {
      store.contexts = [{
        ...store.context!,
        sceneReady: true,
        stateVersion: store.context!.stateVersion + 1,
      }]
      return true
    })
    await voice(input)
    expect(store.refreshContexts).toHaveBeenCalled()
    expect(store.propose).toHaveBeenCalledExactlyOnceWith('SINGLE_DEVICE_CONTROL', interpretationId)
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.text()).not.toContain('仿真场景尚未就绪')
  })

  it('re-sends an existing scene challenge that Unity did not report before recognition', async () => {
    vi.stubEnv('VITE_VOICE_UNITY_PRESENTATION_V1', 'true')
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, sceneReady: false }]
    await wrapper.setProps({ unitySession: { ...wrapper.props('unitySession'), connected: true } })
    await flushPromises()
    const bindingId = '88888888-8888-4888-8888-888888888888'
    store.presentationBinding = { bindingId, runtimeGeneration: store.context!.runtimeGeneration }
    store.presentationChallenge = {
      bindingId,
      runtimeGeneration: store.context!.runtimeGeneration,
      kind: 'SCENE_READY',
      executionId: null,
      requestId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      sequence: 7,
      expiresAt: new Date(Date.now() + 5000).toISOString(),
    }
    const setup = wrapper.vm.$.setupState as unknown as { presentationBridgeReady: boolean }
    setup.presentationBridgeReady = true
    store.refreshContexts = vi.fn().mockImplementation(async () => {
      const probes = wrapper.emitted('presentationMessage') ?? []
      if (probes.some(event => (event[0] as { type?: string }).type === 'PRESENTATION_PROBE')) {
        store.contexts = [{ ...store.context!, sceneReady: true }]
      }
      return true
    })
    await voice(input)
    expect(wrapper.emitted('presentationMessage')).toContainEqual([
      expect.objectContaining({
        type: 'PRESENTATION_PROBE',
        requestId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        sequence: 7,
        kind: 'SCENE_READY',
      }),
    ])
    expect(store.requestPresentationChallenge).not.toHaveBeenCalled()
    expect(store.confirm).toHaveBeenCalledOnce()
  })

  it('does not use a local ready hint to bypass an unconfirmed backend scene', async () => {
    vi.stubEnv('VITE_VOICE_UNITY_PRESENTATION_V1', 'true')
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, sceneReady: false }]
    await wrapper.setProps({ unitySession: { ...wrapper.props('unitySession'), connected: true } })
    await flushPromises()
    const setup = wrapper.vm.$.setupState as unknown as { presentationBridgeReady: boolean }
    setup.presentationBridgeReady = true
    store.refreshContexts = vi.fn().mockResolvedValue(false)
    store.requestPresentationChallenge = vi.fn().mockResolvedValue(null)
    input.vm.$emit('voiceCandidate', 'SINGLE_DEVICE_CONTROL', interpretationId,
      { startedAt: Date.now() - 1200, asrRequestMs: 900, parseMs: 300 })
    await vi.advanceTimersByTimeAsync(4100)
    await flushPromises()
    expect(store.propose).not.toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('仿真场景尚未就绪')
  })

  it('blocks automatic submission when the heartbeat is already stale', async () => {
    const { store, input } = await panel()
    store.contexts = [{ ...store.context!, lastHeartbeatReceivedAt: new Date(Date.now() - 6000).toISOString() }]
    await nextTick()
    await voice(input)
    expect(store.propose).not.toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('rechecks heartbeat age immediately before automatic confirmation', async () => {
    const { store, input } = await panel()
    const release = delayProposal(store)
    await voice(input)
    vi.setSystemTime(new Date(Date.now() + 6000))
    release()
    await flushPromises()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('does not replace an unknown or timed-out execution with a new voice command', async () => {
    const { store, input } = await panel()
    store.responseUnknown = true
    await voice(input)
    expect(store.propose).not.toHaveBeenCalled()
    store.responseUnknown = false
    store.execution = receipt('TIMED_OUT')
    await voice(input, 'SINGLE_DEVICE_CONTROL', replacementInterpretationId)
    expect(store.propose).not.toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('does not retry an automatic confirmation with an unknown result', async () => {
    const { wrapper, store, input } = await panel()
    store.confirm = vi.fn().mockImplementation(async () => { store.responseUnknown = true })
    await voice(input)
    await voice(input)
    await voice(input, 'SINGLE_DEVICE_CONTROL', replacementInterpretationId)
    expect(store.propose).toHaveBeenCalledOnce()
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('待核对')
  })

  it('restores an uncertain automatic proposal for explicit confirmation without auto-retrying', async () => {
    const { wrapper, store, input } = await panel()
    store.confirm = vi.fn().mockImplementation(async () => { store.responseUnknown = true })
    await voice(input)
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
    store.recover = vi.fn().mockImplementation(async () => {
      store.proposal = frozenProposal()
      store.responseUnknown = false
    })
    await wrapper.findAll('button').find(button => button.text() === '核对上次请求')!.trigger('click')
    await flushPromises()
    expect(store.recover).toHaveBeenCalled()
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.get('.refined-plan').text()).toContain('恢复的待确认指令')
    expect(wrapper.get('.refined-confirm').attributes('disabled')).toBeUndefined()
    await voice(input)
    expect(store.confirm).toHaveBeenCalledOnce()
    await wrapper.get('.refined-confirm').trigger('click')
    await flushPromises()
    expect(store.confirm).toHaveBeenCalledTimes(2)
  })

  it.each(['expired', 'invalid-expiry', 'wrong-interpretation'])(
    'rejects an automatic frozen proposal with %s', async fault => {
      const { store, input } = await panel()
      store.propose = vi.fn().mockImplementation(async () => {
        const frozen = frozenProposal()
        if (fault === 'expired') frozen.expiresAt = new Date(Date.now() - 1).toISOString()
        if (fault === 'invalid-expiry') frozen.expiresAt = 'invalid-date'
        if (fault === 'wrong-interpretation') frozen.interpretationId = replacementInterpretationId
        store.proposal = frozen
      })
      await voice(input)
      expect(store.confirm).not.toHaveBeenCalled()
    },
  )

  it.each(['unknown-target', 'wrong-device-type', 'different-frozen-target'])(
    'rejects an automatic device plan with %s', async fault => {
      const { store, input } = await panel()
      store.propose = vi.fn().mockImplementation(async () => {
        const frozen = frozenProposal()
        if (fault === 'unknown-target') frozen.plan.targetDeviceCode = 'UAV-999'
        if (fault === 'wrong-device-type') frozen.plan.deviceCommandType = 'USV_HOLD'
        if (fault === 'different-frozen-target') frozen.plan.explicitDeviceCodes = ['USV-001']
        store.proposal = frozen
      })
      await voice(input)
      expect(store.confirm).not.toHaveBeenCalled()
    },
  )

  it.each(['third-step', 'missing-start', 'wrong-index', 'wrong-hold-type', 'missing-fleet-member'])(
    'rejects an automatic sequence plan with %s', async fault => {
      const { wrapper, store, input } = await panel()
      store.contexts = [{ ...store.context!, state: 'PREPARED' }]
      await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state: 'PREPARED' } })
      store.propose = vi.fn().mockImplementation(async () => {
        const frozen = sequenceProposal()
        if (fault === 'third-step') frozen.plan.steps!.push({ index: 2, action: 'START' })
        if (fault === 'missing-start') frozen.plan.steps![0] = { ...frozen.plan.steps![1]!, index: 0 }
        if (fault === 'wrong-index') frozen.plan.steps![1]!.index = 2
        if (fault === 'wrong-hold-type') frozen.plan.steps![1]!.deviceCommandType = 'USV_HOLD'
        if (fault === 'missing-fleet-member') frozen.plan.explicitDeviceCodes = ['UAV-001']
        store.proposal = frozen
      })
      await voice(input, 'COMMAND_SEQUENCE')
      expect(store.propose).toHaveBeenCalledOnce()
      expect(store.confirm).not.toHaveBeenCalled()
    },
  )

  it('previews both frozen sequence steps and starts them only after explicit confirmation', async () => {
    const { wrapper, store, input } = await panel()
    store.contexts = [{ ...store.context!, state: 'PREPARED' }]
    await wrapper.setProps({ runtimeHint: { ...wrapper.props('runtimeHint'), state: 'PREPARED' } })
    store.propose = vi.fn().mockImplementation(async (_intent, sourceId) => {
      const frozen = frozenProposal(sourceId)
      store.proposal = {
        ...frozen,
        plan: {
          ...frozen.plan,
          action: 'SEQUENCE',
          targetDeviceCode: undefined,
          deviceCommandType: undefined,
          explicitDeviceCodes: ['UAV-001', 'USV-001'],
          steps: [
            { index: 0, action: 'START' },
            { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_HOVER' },
          ],
        },
      }
    })
    store.confirm = vi.fn().mockImplementation(async () => {
      store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
      store.execution = {
        ...receipt('EXECUTING'),
        action: 'SEQUENCE',
        currentStepIndex: 1,
        sequenceStatus: 'EXECUTING_DEVICE_COMMAND',
        steps: [
          { index: 0, action: 'START', state: 'SUCCEEDED', executionId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', errorCode: null },
          { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_HOVER', state: 'QUEUED', executionId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', errorCode: null },
        ],
      }
    })
    input.vm.$emit('candidate', 'COMMAND_SEQUENCE', interpretationId)
    await flushPromises()
    expect(store.propose).toHaveBeenCalledExactlyOnceWith('COMMAND_SEQUENCE', interpretationId)
    expect(store.confirm).not.toHaveBeenCalled()
    expect(wrapper.get('.refined-plan').text()).toContain('受控顺序指令')
    expect(wrapper.findAll('.refined-plan li b').map(badge => badge.text())).toEqual(['1', '2'])
    expect(wrapper.findAll('.refined-plan li > span').map(step => step.text())).toEqual(['开始任务', 'UAV-001 · 一号无人机 · 无人机悬停'])
    expect(wrapper.get('.refined-confirm').attributes('disabled')).toBeUndefined()
    await wrapper.get('.refined-confirm').trigger('click')
    await flushPromises()
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.get('.refined-result-head').text()).toContain('算法执行中')
    const progress = wrapper.findAll('.refined-execution-steps li')
    expect(progress).toHaveLength(2)
    expect(progress[0]!.text()).toContain('开始任务')
    expect(progress[0]!.text()).toContain('算法执行成功')
    expect(progress[1]!.text()).toContain('UAV-001 · 一号无人机 · 无人机悬停')
    expect(progress[1]!.text()).toContain('等待调度')
  })

  it('invalidates a frozen plan when the actual editable transcript changes and preserves recovery data', async () => {
    const { wrapper, store, input } = await panel()
    await preview(input)
    const journalKey = store.journalKey()
    localStorage.setItem(journalKey, 'original-recovery-record')
    await input.get('textarea').setValue('让二号无人机悬停')
    expect(input.props('proposalReady')).toBe(false)
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
    expect(wrapper.text()).toContain('旧指令不会执行')
    await guardedConfirm(wrapper)
    expect(store.confirm).not.toHaveBeenCalled()
    expect(localStorage.getItem(journalKey)).toBe('original-recovery-record')
  })

  it('rejects a late proposal after the transcript changed while the proposal was preparing', async () => {
    const { wrapper, store, input } = await panel()
    let release!: () => void
    store.propose = vi.fn().mockImplementation(async () => {
      await new Promise<void>(resolve => { release = resolve })
      store.proposal = frozenProposal()
    })
    input.vm.$emit('candidate', 'SINGLE_DEVICE_CONTROL', interpretationId)
    await nextTick()
    input.vm.$emit('draftChange')
    await nextTick()
    release()
    await flushPromises()
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
    expect(input.props('proposalReady')).toBe(false)
    await guardedConfirm(wrapper)
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('cancels the stale proposal before creating a replacement from new text', async () => {
    const { store, input } = await panel()
    await preview(input)
    const calls: string[] = []
    store.cancel = vi.fn().mockImplementation(async () => {
      calls.push('cancel')
      store.proposal = { ...store.proposal!, status: 'CANCELLED' }
    })
    store.propose = vi.fn().mockImplementation(async (_intent, id) => {
      calls.push('propose')
      store.proposal = frozenProposal(id, 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
    })
    input.vm.$emit('draftChange')
    await nextTick()
    await preview(input, replacementInterpretationId)
    expect(calls).toEqual(['cancel', 'propose'])
    expect(input.props('proposalReady')).toBe(true)
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('does not replace a plan when its cancellation result is unknown and keeps explicit recovery', async () => {
    const { wrapper, store, input } = await panel()
    await preview(input)
    vi.mocked(store.propose).mockClear()
    store.cancel = vi.fn().mockImplementation(async () => { store.responseUnknown = true })
    input.vm.$emit('draftChange')
    await nextTick()
    await preview(input, replacementInterpretationId)
    expect(store.cancel).toHaveBeenCalledOnce()
    expect(store.propose).not.toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
    const recover = wrapper.findAll('button').find(button => button.text() === '核对上次请求')!
    expect(recover.exists()).toBe(true)
    vi.mocked(store.recover).mockClear()
    await recover.trigger('click')
    expect(store.recover).toHaveBeenCalledOnce()
  })

  it('blocks confirmation after expiry, including a click before the display clock updates', async () => {
    const { wrapper, store, input } = await panel()
    await preview(input)
    vi.setSystemTime(new Date(Date.now() + 31_000))
    await guardedConfirm(wrapper)
    expect(store.confirm).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1000)
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
  })

  it('keeps confirmation single-flight even before the store loading flag changes', async () => {
    const { wrapper, store, input } = await panel()
    await preview(input)
    let release!: () => void
    store.confirm = vi.fn().mockImplementation(async () => {
      await new Promise<void>(resolve => { release = resolve })
      store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
      store.execution = receipt()
    })
    await wrapper.get('.refined-confirm').trigger('click')
    expect(wrapper.get('.refined-confirm').attributes('disabled')).toBeDefined()
    expect(input.props('submissionDisabled')).toBe(true)
    await guardedConfirm(wrapper)
    expect(store.confirm).toHaveBeenCalledOnce()
    release()
    await flushPromises()
    expect(wrapper.get('.refined-result').text()).toContain('算法执行成功')
  })

  it('shows a recovered pending plan for explicit confirmation without automatically executing it', async () => {
    const { wrapper, store } = await panel()
    store.responseUnknown = true
    store.recover = vi.fn().mockImplementation(async () => {
      store.proposal = frozenProposal()
      store.responseUnknown = false
    })
    await nextTick()
    await wrapper.findAll('button').find(button => button.text() === '核对上次请求')!.trigger('click')
    await flushPromises()
    expect(wrapper.get('.refined-plan').text()).toContain('恢复的待确认指令')
    expect(wrapper.get('.refined-plan').text()).toContain('UAV-001 · 一号无人机 · 无人机悬停')
    expect(wrapper.get('.refined-confirm').attributes('disabled')).toBeUndefined()
    expect(store.confirm).not.toHaveBeenCalled()
    await wrapper.get('.refined-confirm').trigger('click')
    await flushPromises()
    expect(store.confirm).toHaveBeenCalledOnce()
  })

  it('does not authorize a recovered plan if the draft changed while recovery was pending', async () => {
    const { wrapper, store, input } = await panel()
    let release!: () => void
    store.responseUnknown = true
    store.recover = vi.fn().mockImplementation(async () => {
      await new Promise<void>(resolve => { release = resolve })
      store.proposal = frozenProposal()
      store.responseUnknown = false
    })
    await nextTick()
    await wrapper.findAll('button').find(button => button.text() === '核对上次请求')!.trigger('click')
    input.vm.$emit('draftChange')
    release()
    await flushPromises()
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
    await guardedConfirm(wrapper)
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it.each(['contextVersion', 'stateVersion', 'runtimeGeneration'] as const)(
    'blocks confirmation when %s changes after preview', async field => {
      const { wrapper, store, input } = await panel()
      await preview(input)
      store.contexts = [{ ...store.context!, [field]: field === 'runtimeGeneration'
        ? 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' : (store.context![field] as number) + 1 }]
      await nextTick()
      await guardedConfirm(wrapper)
      expect(store.confirm).not.toHaveBeenCalled()
      expect(input.props('proposalReady')).toBe(false)
    },
  )

  it('blocks confirmation and input after the operator loses the admin role', async () => {
    const { wrapper, store, input } = await panel()
    await preview(input)
    useAuthStore().user = { username: 'admin', role: 'VIEWER' }
    await nextTick()
    await guardedConfirm(wrapper)
    expect(store.confirm).not.toHaveBeenCalled()
    expect(input.props('inputDisabled')).toBe(true)
    expect(wrapper.text()).toContain('当前账号没有控制权限')
  })

  it('blocks replacement while TIMED_OUT remains unresolved and reports the result as unknown', async () => {
    const { wrapper, store, input } = await panel()
    store.execution = receipt('TIMED_OUT')
    await nextTick()
    await preview(input)
    expect(store.propose).not.toHaveBeenCalled()
    expect(input.props('submissionDisabled')).toBe(true)
    expect(wrapper.get('.refined-result').text()).toContain('结果未知（超时，仍在查询）')
    await vi.advanceTimersByTimeAsync(2000)
    expect(store.poll).toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it('retains Unity STALE resynchronization without creating or reexecuting a command', async () => {
    const { wrapper, store } = await panel()
    store.execution = { ...receipt('SUCCEEDED', 'STALE'), action: 'START' }
    await nextTick()
    const resync = wrapper.findAll('button').find(button => button.text() === '重新同步画面')!
    expect(resync.exists()).toBe(true)
    await resync.trigger('click')
    await flushPromises()
    expect(store.propose).not.toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Unity 展示已过期')
  })

  it.each(['COMPLETED', 'STOPPED', 'FAILED'] as const)(
    'shows an ended-run notice instead of a disconnect for %s with an expired heartbeat', async state => {
      const { wrapper, store, input } = await panel()
      store.contexts = [{ ...store.context!, state,
        lastHeartbeatReceivedAt: new Date(Date.now() - 60_000).toISOString() }]
      const setup = wrapper.vm.$.setupState as unknown as { helloAttempts: number }
      setup.helloAttempts = 30
      await nextTick()
      expect(wrapper.text()).toContain('本轮运行已结束，当前画面已保留。重新生成场景后可继续控制。')
      expect(wrapper.text()).not.toContain('设备连接暂时中断')
      expect(wrapper.findAll('button').some(button => button.text() === '重新连接')).toBe(false)
      expect(wrapper.findAll('button').some(button => button.text() === '恢复画面连接')).toBe(false)
      expect(input.props('submissionDisabled')).toBe(true)
      await voice(input)
      expect(store.propose).not.toHaveBeenCalled()
      expect(store.confirm).not.toHaveBeenCalled()
    },
  )

  it('still reports a real stale heartbeat and offers reconnection while RUNNING', async () => {
    const { wrapper, store } = await panel()
    store.contexts = [{ ...store.context!, state: 'RUNNING',
      lastHeartbeatReceivedAt: new Date(Date.now() - 60_000).toISOString() }]
    await nextTick()
    expect(wrapper.text()).toContain('设备连接暂时中断，恢复后可继续控制。')
    expect(wrapper.text()).not.toContain('本轮运行已结束')
    vi.mocked(store.refreshContexts).mockClear()
    const reconnect = wrapper.findAll('button').find(button => button.text() === '重新连接')!
    expect(reconnect.exists()).toBe(true)
    await reconnect.trigger('click')
    await flushPromises()
    expect(store.refreshContexts).toHaveBeenCalledOnce()
    expect(store.propose).not.toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
  })

  it.each(['COMPLETED', 'STOPPED', 'FAILED'] as const)(
    'preserves explicit unknown-write recovery for a %s run without reconnecting or resubmitting', async state => {
      const { wrapper, store } = await panel()
      store.contexts = [{ ...store.context!, state, lastHeartbeatReceivedAt: null }]
      store.responseUnknown = true
      await nextTick()
      expect(wrapper.text()).toContain('本轮运行已结束')
      expect(wrapper.text()).not.toContain('设备连接暂时中断')
      expect(wrapper.findAll('button').some(button => button.text() === '重新连接')).toBe(false)
      expect(wrapper.findAll('button').some(button => button.text() === '恢复画面连接')).toBe(false)
      vi.mocked(store.recover).mockClear()
      const recover = wrapper.findAll('button').find(button => button.text() === '核对上次请求')!
      expect(recover.exists()).toBe(true)
      await recover.trigger('click')
      await flushPromises()
      expect(store.recover).toHaveBeenCalledOnce()
      expect(store.propose).not.toHaveBeenCalled()
      expect(store.confirm).not.toHaveBeenCalled()
    },
  )

  it.each(['null-context', 'new-generation', 'null-binding', 'new-binding', 'new-unity', 'new-scene', 'disconnected', 'terminal', 'unmounted'])(
    'ignores a late presentation challenge safely after %s', async change => {
      const result = await pendingPresentationProbe()
      const { wrapper, store } = result
      if (change === 'null-context') store.contexts = []
      if (change === 'new-generation') store.contexts = [{ ...store.context!, runtimeGeneration: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb' }]
      if (change === 'null-binding') store.presentationBinding = null
      if (change === 'new-binding') store.presentationBinding = { ...store.presentationBinding!, bindingId: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc' }
      if (change === 'new-unity') await wrapper.setProps({ unitySession: { ...wrapper.props('unitySession'), unityInstanceId: 'new-unity' } })
      if (change === 'new-scene') await wrapper.setProps({ unitySession: { ...wrapper.props('unitySession'), sceneRevision: 2 } })
      if (change === 'disconnected') await wrapper.setProps({ unitySession: { ...wrapper.props('unitySession'), connected: false } })
      if (change === 'terminal') store.contexts = [{ ...store.context!, state: 'COMPLETED' }]
      if (change === 'unmounted') {
        wrapper.unmount()
        mounted.splice(mounted.findIndex(item => item.wrapper === wrapper), 1)
      }
      await nextTick()
      result.release()
      await expect(result.completion).resolves.toBeUndefined()
      await flushPromises()
      const probes = (wrapper.emitted('presentationMessage') ?? []).filter(args => (args[0] as { type: string }).type === 'PRESENTATION_PROBE')
      expect(probes).toEqual([])
      expect(store.presentationChallenge).toBeNull()
    },
  )

  it('sends the challenge with the captured identity when the presentation session stays current', async () => {
    const { wrapper, store, release, completion, challenge } = await pendingPresentationProbe()
    const runtimeRef = store.context!.runtimeRef
    release()
    await expect(completion).resolves.toBeUndefined()
    const probes = (wrapper.emitted('presentationMessage') ?? []).filter(args => (args[0] as { type: string }).type === 'PRESENTATION_PROBE')
    expect(probes).toEqual([[{
      type: 'PRESENTATION_PROBE', protocolVersion: 'unity.presentation.v1', runtimeRef,
      runtimeGeneration: challenge.runtimeGeneration, bindingId: challenge.bindingId,
      unityInstanceId: 'unity-test', sceneRevision: 1,
      requestId: challenge.requestId, sequence: challenge.sequence, kind: 'SCENE_READY', executionId: null,
    }]])
  })
})

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'

import VoiceP0ControlPanel from '@/components/voice/VoiceP0ControlPanel.vue'
import VoiceIntelligenceInput from '@/components/voice/VoiceIntelligenceInput.vue'
import { useAuthStore } from '@/stores/auth'
import { useVoiceControlStore } from '@/stores/voiceControl'
import type { VoiceExecution, VoiceIntent, VoicePresentationChallenge, VoiceProposal, VoiceRuntimeContext } from '@/types/voiceControl'

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

function createPanel() {
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
        algorithmRunId: context.algorithmRunId,
        state: context.state,
        sceneReady: true,
        deviceCodes: ['UAV-001', 'USV-001'],
        latestFrameSequence: context.latestFrameSequence,
      },
      unitySession: { connected: false, unityInstanceId: 'unity-test', sceneRevision: 1 },
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

  it('creates the backend frozen plan without executing until the operator confirms', async () => {
    const { wrapper, store, input } = await panel()
    await preview(input)
    expect(store.propose).toHaveBeenCalledExactlyOnceWith('SINGLE_DEVICE_CONTROL', interpretationId)
    expect(store.confirm).not.toHaveBeenCalled()
    expect(wrapper.get('.refined-plan').text()).toContain('UAV-001 · 无人机悬停')
    expect(input.props('proposalReady')).toBe(true)
    expect(wrapper.find('.refined-preview').exists()).toBe(false)
    expect(wrapper.get('.refined-confirm').attributes('disabled')).toBeUndefined()
    await wrapper.get('.refined-confirm').trigger('click')
    await flushPromises()
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.get('.refined-result').text()).toContain('算法执行成功')
  })

  it('automatically submits a newly recognized device command through the frozen proposal', async () => {
    const { wrapper, store, input } = await panel()
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

  it.each([
    ['UAV-001', 'UAV_RETURN'], ['UAV-001', 'UAV_LAND'],
    ['USV-001', 'USV_RETURN'], ['USV-001', 'USV_STOP'],
  ] as const)('auto-confirms a validated %s %s command exactly once', async (targetDeviceCode, deviceCommandType) => {
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
    expect(wrapper.find('.refined-confirm').exists()).toBe(false)
    expect(wrapper.get('.refined-result').text()).toContain('算法执行成功')
    await voice(input)
    expect(store.confirm).toHaveBeenCalledOnce()
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

  it('does not auto-submit for a non-simulation backend', async () => {
    const { store, input } = await panel()
    store.contexts = [{ ...store.context!, executionBackend: 'REAL_DEVICE' as VoiceRuntimeContext['executionBackend'] }]
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
    const { store, input } = await panel()
    store.contexts = [{ ...store.context!, sceneReady: false }]
    await nextTick()
    await voice(input)
    expect(store.propose).not.toHaveBeenCalled()
    expect(store.confirm).not.toHaveBeenCalled()
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
    expect(wrapper.get('.refined-plan').text()).toContain('受控双步骤')
    expect(wrapper.findAll('.refined-plan li b').map(badge => badge.text())).toEqual(['1', '2'])
    expect(wrapper.findAll('.refined-plan li > span').map(step => step.text())).toEqual(['开始任务', 'UAV-001 · 无人机悬停'])
    expect(wrapper.get('.refined-confirm').attributes('disabled')).toBeUndefined()
    await wrapper.get('.refined-confirm').trigger('click')
    await flushPromises()
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(wrapper.get('.refined-result-head').text()).toContain('算法执行中')
    const progress = wrapper.findAll('.refined-execution-steps li')
    expect(progress).toHaveLength(2)
    expect(progress[0]!.text()).toContain('开始任务')
    expect(progress[0]!.text()).toContain('算法执行成功')
    expect(progress[1]!.text()).toContain('UAV-001 · 无人机悬停')
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
    expect(wrapper.get('.refined-plan').text()).toContain('UAV-001 · 无人机悬停')
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

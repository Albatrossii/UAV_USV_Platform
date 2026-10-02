import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

import VoiceP0ControlPanel from '@/components/voice/VoiceP0ControlPanel.vue'
import VoiceIntelligenceInput from '@/components/voice/VoiceIntelligenceInput.vue'
import { useAuthStore } from '@/stores/auth'
import { useVoiceControlStore } from '@/stores/voiceControl'
import type { VoiceExecution, VoiceProposal, VoiceRuntimeContext } from '@/types/voiceControl'

const context: VoiceRuntimeContext = {
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
  capabilities: ['START', 'PAUSE', 'RESUME', 'STOP'],
  lastHeartbeatReceivedAt: new Date().toISOString(),
  latestFrameSequence: 12,
  sceneReady: true,
}

function execution(presentationStatus: VoiceExecution['presentationStatus']): VoiceExecution {
  return {
    executionId: '33333333-3333-4333-8333-333333333333',
    proposalId: '44444444-4444-4444-8444-444444444444',
    commandId: '55555555-5555-4555-8555-555555555555',
    runtimeRef: context.runtimeRef,
    runtimeGeneration: context.runtimeGeneration,
    action: 'START',
    state: 'SUCCEEDED',
    outcome: 'SUCCESS',
    errorCode: null,
    timedOutAt: null,
    presentationStatus,
    createdAt: '2026-09-21T00:00:00.000Z',
    updatedAt: '2026-09-21T00:00:01.000Z',
  }
}

function mountPanel(presentationStatus: VoiceExecution['presentationStatus']) {
  const store = useVoiceControlStore()
  store.contexts = [context]
  store.selectedRuntimeRef = context.runtimeRef
  store.expectedAlgorithmRunId = context.algorithmRunId
  store.execution = execution(presentationStatus)
  store.selectAlgorithmRun = vi.fn().mockResolvedValue(true)
  store.recover = vi.fn().mockResolvedValue(undefined)
  store.refreshContexts = vi.fn().mockResolvedValue(true)
  store.poll = vi.fn().mockResolvedValue(undefined)

  return mount(VoiceP0ControlPanel, {
    props: {
      runtimeHint: {
        algorithmRunId: context.algorithmRunId,
        state: context.state,
        sceneReady: context.sceneReady,
        deviceCodes: ['UAV-001', 'USV-001'],
        latestFrameSequence: context.latestFrameSequence,
      },
      unitySession: { connected: false, unityInstanceId: 'unity-test', sceneRevision: 1 },
    },
  })
}

describe('VoiceP0ControlPanel presentation recovery UI', () => {
  it('shows the confirmed manual device receipt instead of stale automatic voice status', async () => {
    const wrapper = mountPanel('NOT_REQUIRED')
    const store = useVoiceControlStore()
    const proposalId = '99999999-9999-4999-8999-999999999999'
    store.proposal = {
      proposalId,
      status: 'CONFIRMED',
      planVersion: 1,
      planHash: 'device-plan-hash',
      requiresConfirmation: true,
      createdAt: '2026-09-28T00:00:00.000Z',
      expiresAt: '2026-09-28T00:00:30.000Z',
      executionId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
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
    } satisfies VoiceProposal
    store.execution = {
      ...execution('NOT_REQUIRED'),
      proposalId,
      action: 'DEVICE_COMMAND',
    }
    wrapper.vm.$.setupState.voiceAutomationStatus = '仿真动作已自动提交，正在等待算法回执。'
    await nextTick()

    expect(wrapper.text()).toContain('单设备指令：UAV-001 · 无人机悬停 · 算法执行成功。')
    expect(wrapper.text()).not.toContain('仿真动作已自动提交，正在等待算法回执。')
    wrapper.unmount()
  })

  it('clears prior voice execution status when the selected runtime changes', async () => {
    const wrapper = mountPanel('NOT_REQUIRED')
    const store = useVoiceControlStore()
    wrapper.vm.$.setupState.voiceAutomationStatus = '仿真动作已自动提交，正在等待算法回执。'
    await nextTick()
    expect(wrapper.text()).toContain('仿真动作已自动提交，正在等待算法回执。')

    store.contexts = [{
      ...context,
      runtimeRef: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      runtimeGeneration: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      algorithmRunId: '178980000002',
      state: 'PREVIEW',
    }]
    store.selectedRuntimeRef = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    await nextTick()

    expect(wrapper.text()).not.toContain('仿真动作已自动提交，正在等待算法回执。')
    wrapper.unmount()
  })

  it('automatically confirms a validated single-device voice command in local simulation', async () => {
    const wrapper = mountPanel('NOT_REQUIRED')
    const store = useVoiceControlStore()
    const targetedContext = { ...context, capabilities: [...context.capabilities, 'DEVICE_COMMAND'] }
    const interpretationId = '66666666-6666-4666-8666-666666666666'
    const proposalId = '99999999-9999-4999-8999-999999999999'
    store.contexts = [targetedContext]
    store.expectedAlgorithmRunId = targetedContext.algorithmRunId
    store.propose = vi.fn().mockImplementation(async () => {
      store.proposal = {
        proposalId,
        interpretationId,
        status: 'AWAITING_CONFIRMATION',
        planVersion: 1,
        planHash: 'device-plan-hash',
        requiresConfirmation: true,
        createdAt: '2026-09-28T00:00:00.000Z',
        expiresAt: new Date(Date.now() + 30_000).toISOString(),
        executionId: null,
        plan: {
          runtimeRef: targetedContext.runtimeRef,
          runtimeGeneration: targetedContext.runtimeGeneration,
          contextVersion: targetedContext.contextVersion,
          stateVersion: targetedContext.stateVersion,
          action: 'DEVICE_COMMAND',
          targetDeviceCode: 'UAV-001',
          deviceCommandType: 'UAV_HOVER',
          explicitDeviceCodes: ['UAV-001'],
          policyVersion: 'voice-p0.v1',
        },
      } satisfies VoiceProposal
    })
    store.confirm = vi.fn().mockImplementation(async () => {
      store.proposal = { ...store.proposal!, status: 'CONFIRMED' }
      store.execution = {
        ...execution('NOT_REQUIRED'),
        proposalId,
        action: 'DEVICE_COMMAND',
      }
    })

    await wrapper.vm.$.setupState.handleAutomaticVoiceCandidate(
      'SINGLE_DEVICE_CONTROL',
      interpretationId,
      { startedAt: Date.now() - 120, asrRequestMs: 80, parseMs: 30 },
    )

    expect(store.propose).toHaveBeenCalledOnce()
    expect(store.confirm).toHaveBeenCalledOnce()
    expect(store.execution?.action).toBe('DEVICE_COMMAND')
    expect(wrapper.text()).toContain('单设备指令：UAV-001 · 无人机悬停 · 算法执行成功。')
    wrapper.unmount()
  })

  it('parses independently of a LOST runtime while keeping proposal submission blocked', async () => {
    const wrapper = mountPanel('NOT_REQUIRED')
    const store = useVoiceControlStore()
    store.contexts = [{ ...context, state: 'LOST', capabilities: [] }]
    await nextTick()

    const input = wrapper.getComponent(VoiceIntelligenceInput)
    expect(input.props('runtimeContext')).toBeNull()
    expect(input.props('allowedActions')).toEqual(['START', 'PAUSE', 'RESUME', 'STOP'])
    expect(input.props('submissionDisabled')).toBe(true)
    wrapper.unmount()
  })

  it('allows local simulation resume while Unity is recovering, but still requires a ready scene for START', async () => {
    const wrapper = mountPanel('NOT_REQUIRED')
    const store = useVoiceControlStore()
    store.contexts = [{ ...context, state: 'PAUSED', sceneReady: false }]
    await nextTick()

    const input = wrapper.getComponent(VoiceIntelligenceInput)
    const disabledReason = input.props('actionDisabledReason') as (action: string) => string
    expect(disabledReason('RESUME')).toBe('')

    store.contexts = [{ ...context, state: 'PREPARED', sceneReady: false }]
    await nextTick()
    expect(disabledReason('START')).toContain('Unity 场景尚未就绪')
    wrapper.unmount()
  })

  it('parses independently of a legacy runtime while keeping proposal submission blocked', async () => {
    const wrapper = mountPanel('NOT_REQUIRED')
    const store = useVoiceControlStore()
    store.contexts = [{ ...context, protocolVersion: 'legacy', capabilities: [] }]
    await nextTick()

    const input = wrapper.getComponent(VoiceIntelligenceInput)
    expect(input.props('runtimeContext')).toBeNull()
    expect(input.props('allowedActions')).toEqual(['START', 'PAUSE', 'RESUME', 'STOP'])
    expect(input.props('submissionDisabled')).toBe(false)
    expect(input.props('actionDisabledReason')('PAUSE')).toContain('旧协议实例不支持 P0')
    wrapper.unmount()
  })

  it('explains how to recover when no owned runtime is available', async () => {
    const wrapper = mountPanel('NOT_REQUIRED')
    const store = useVoiceControlStore()
    store.contexts = []
    store.execution = null
    await nextTick()
    expect(wrapper.text()).toContain('请重新生成场景')
    expect(wrapper.text()).toContain('管理员清理旧运行')
    wrapper.unmount()
  })

  it('serializes a scene probe and a newly due frame probe', async () => {
    const wrapper = mountPanel('NOT_REQUIRED')
    await nextTick()
    const store = useVoiceControlStore()
    store.execution = { ...execution('NOT_REQUIRED'), action: 'PAUSE' }
    store.presentationBinding = {
      bindingId: '77777777-7777-4777-8777-777777777777',
      runtimeGeneration: context.runtimeGeneration,
    }
    store.takePresentationBinding = vi.fn().mockImplementation(async () => {
      store.presentationBinding = { bindingId: '77777777-7777-4777-8777-777777777777', runtimeGeneration: context.runtimeGeneration }
    })
    let finishScene!: (value: null) => void
    store.requestPresentationChallenge = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finishScene = resolve }))
      .mockResolvedValue(null)
    await nextTick()
    await wrapper.setProps({ unitySession: { connected: true, unityInstanceId: 'unity-test', sceneRevision: 1 } })
    await nextTick()
    await wrapper.vm.handleUnityPresentationMessage({ type: 'PRESENTATION_READY', payload: {
      protocolVersion: 'unity.presentation.v1', runtimeRef: context.runtimeRef, runtimeGeneration: context.runtimeGeneration,
      bindingId: '77777777-7777-4777-8777-777777777777', unityInstanceId: 'unity-test', sceneRevision: 1, scenarioReady: true,
    } })
    void wrapper.vm.$.setupState.requestPresentationProbe()
    await nextTick()
    expect(store.requestPresentationChallenge).toHaveBeenCalledExactlyOnceWith('SCENE_READY', null)
    store.execution = execution('PENDING')
    await wrapper.vm.$.setupState.requestPresentationProbe()
    expect(store.requestPresentationChallenge).toHaveBeenCalledTimes(1)
    finishScene(null)
    await Promise.resolve()
    await wrapper.vm.$.setupState.requestPresentationProbe()
    expect(store.requestPresentationChallenge).toHaveBeenNthCalledWith(2, 'FRAME_APPLIED', store.execution!.executionId)
    wrapper.unmount()
  })

  it('retains one resync click until the Unity handshake becomes ready', async () => {
    const wrapper = mountPanel('STALE')
    const store = useVoiceControlStore()
    const originalExecutionId = store.execution!.executionId
    store.takePresentationBinding = vi.fn().mockImplementation(async () => {
      store.presentationBinding = { bindingId: '77777777-7777-4777-8777-777777777777', runtimeGeneration: context.runtimeGeneration }
    })
    store.requestPresentationChallenge = vi.fn().mockImplementation(async () => {
      const challenge = { runtimeGeneration: context.runtimeGeneration, bindingId: '77777777-7777-4777-8777-777777777777', kind: 'FRAME_APPLIED' as const, executionId: originalExecutionId, requestId: '88888888-8888-4888-8888-888888888888', sequence: 2, expiresAt: new Date(Date.now() + 5000).toISOString() }
      store.presentationChallenge = challenge
      return challenge
    })
    await wrapper.setProps({ unitySession: { connected: true, unityInstanceId: 'unity-test', sceneRevision: 1 } })
    await nextTick()
    await wrapper.get('button.presentation-resync').trigger('click')
    expect(store.requestPresentationChallenge).not.toHaveBeenCalled()
    await wrapper.vm.handleUnityPresentationMessage({ type: 'PRESENTATION_READY', payload: {
      protocolVersion: 'unity.presentation.v1', runtimeRef: context.runtimeRef, runtimeGeneration: context.runtimeGeneration,
      bindingId: '77777777-7777-4777-8777-777777777777', unityInstanceId: 'unity-test', sceneRevision: 1, scenarioReady: true,
    } })
    await vi.advanceTimersByTimeAsync(1000)
    expect(store.requestPresentationChallenge).toHaveBeenCalledExactlyOnceWith('FRAME_APPLIED', originalExecutionId)
    expect(store.execution!.executionId).toBe(originalExecutionId)
    wrapper.unmount()
  })

  it('keeps an in-flight presentation challenge across heartbeat refreshes but clears it for a new scene', async () => {
    const wrapper = mountPanel('NOT_REQUIRED')
    const store = useVoiceControlStore()
    store.takePresentationBinding = vi.fn().mockImplementation(async () => {
      store.presentationBinding = {
        bindingId: '77777777-7777-4777-8777-777777777777',
        runtimeGeneration: context.runtimeGeneration,
      }
    })
    await wrapper.setProps({ unitySession: { connected: true, unityInstanceId: 'unity-test', sceneRevision: 1 } })
    await nextTick()
    await wrapper.vm.handleUnityPresentationMessage({ type: 'PRESENTATION_READY', payload: {
      protocolVersion: 'unity.presentation.v1', runtimeRef: context.runtimeRef, runtimeGeneration: context.runtimeGeneration,
      bindingId: '77777777-7777-4777-8777-777777777777', unityInstanceId: 'unity-test', sceneRevision: 1, scenarioReady: true,
    } })
    const challenge = {
      runtimeGeneration: context.runtimeGeneration,
      bindingId: '77777777-7777-4777-8777-777777777777', kind: 'SCENE_READY' as const,
      executionId: null, requestId: '88888888-8888-4888-8888-888888888888', sequence: 1,
      expiresAt: new Date(Date.now() + 5000).toISOString(),
    }
    store.presentationChallenge = challenge
    const messagesBeforeRefresh = wrapper.emitted('presentationMessage')?.length

    store.contexts = [{ ...context, lastHeartbeatReceivedAt: new Date(Date.now() + 1000).toISOString(), latestFrameSequence: 13 }]
    await nextTick()
    expect(store.presentationChallenge).toEqual(challenge)
    expect(wrapper.emitted('presentationMessage')).toHaveLength(messagesBeforeRefresh!)
    expect(store.takePresentationBinding).toHaveBeenCalledOnce()

    await wrapper.setProps({ unitySession: { connected: true, unityInstanceId: 'unity-test', sceneRevision: 2 } })
    await nextTick()
    expect(store.presentationChallenge).toBeNull()
    expect(store.takePresentationBinding).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })


  it('does not resume presentation probes after STOP succeeds while context is stale', async () => {
    const wrapper = mountPanel('NOT_REQUIRED')
    const store = useVoiceControlStore()
    store.execution = { ...execution('NOT_REQUIRED'), action: 'STOP' }
    store.takePresentationBinding = vi.fn()
    store.requestPresentationChallenge = vi.fn()
    await wrapper.setProps({ unitySession: { connected: true, unityInstanceId: 'unity-test', sceneRevision: 1 } })
    await vi.advanceTimersByTimeAsync(4000)
    expect(store.takePresentationBinding).not.toHaveBeenCalled()
    expect(store.requestPresentationChallenge).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubEnv('VITE_VOICE_P1_PREPARATION', 'true')
    setActivePinia(createPinia())
    useAuthStore().user = { username: 'admin', role: 'ADMIN' }
  })

  it('shows a resync entry when a successful START presentation is STALE', () => {
    const wrapper = mountPanel('STALE')

    expect(wrapper.text()).toContain('Unity 展示已过期')
    expect(wrapper.get('button.presentation-resync').text()).toContain('重新同步画面')
    wrapper.unmount()
  })

  it('does not show the resync entry after Unity has reported the frame applied', () => {
    const wrapper = mountPanel('REPORTED_APPLIED')

    expect(wrapper.text()).toContain('Unity 已应用')
    expect(wrapper.find('button.presentation-resync').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not replace the presentation binding again when the same Unity session reconnects', async () => {
    const wrapper = mountPanel('REPORTED_APPLIED')
    const store = useVoiceControlStore()
    store.takePresentationBinding = vi.fn().mockImplementation(async () => {
      store.presentationBinding = {
        bindingId: '77777777-7777-4777-8777-777777777777',
        runtimeGeneration: context.runtimeGeneration,
      }
    })

    await wrapper.setProps({ unitySession: { connected: true, unityInstanceId: 'unity-test', sceneRevision: 1 } })
    await nextTick()
    await wrapper.setProps({ unitySession: { connected: false, unityInstanceId: 'unity-test', sceneRevision: 1 } })
    await wrapper.setProps({ unitySession: { connected: true, unityInstanceId: 'unity-test', sceneRevision: 1 } })
    await nextTick()

    expect(store.takePresentationBinding).toHaveBeenCalledOnce()
    wrapper.unmount()
  })
})

it.each([
  ['INVALID_REQUEST', '请求格式或版本不受支持，请刷新页面后重试。'],
  ['PLAN_MISMATCH', '提案信息不一致，请重新获取提案。'],
])('I05 displays the actionable message for %s', async (code, message) => {
  setActivePinia(createPinia())
  const wrapper = mountPanel('REPORTED_APPLIED')
  const store = useVoiceControlStore()
  store.errorCode = code!
  store.error = 'request rejected'
  await nextTick()
  expect(wrapper.text()).toContain(message)
  wrapper.unmount()
})

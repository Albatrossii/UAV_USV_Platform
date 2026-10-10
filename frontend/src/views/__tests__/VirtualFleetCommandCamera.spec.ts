import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'

import VirtualFleetConfigView from '@/views/VirtualFleetConfigView.vue'
import { simulationRuntime } from '@/composables/simulationRuntime'
import { useAuthStore } from '@/stores/auth'
import { useVoiceControlStore } from '@/stores/voiceControl'
import type { VoiceExecution, VoiceProposal, VoiceRuntimeContext } from '@/types/voiceControl'
import type { GridScenarioPose } from '@/utils/virtualFleetGridLayout'

vi.mock('@/components/layout/ConsoleLayout.vue', () => ({
  default: { template: '<main><slot /></main>' },
}))
vi.mock('@/components/voice/VoiceP0ControlPanel.vue', () => ({
  default: {
    template: '<aside aria-label="语音输入测试替身" />',
    methods: { handleUnityMessage: vi.fn() },
  },
}))
vi.mock('@/api/algorithm', () => ({
  prepareAlgorithmRun: vi.fn(),
  controlAlgorithmRun: vi.fn(),
  fetchAlgorithmRunStatus: vi.fn(),
  fetchAlgorithmFrames: vi.fn().mockResolvedValue([]),
}))

const runId = 7001
const runtimeRef = '11111111-1111-4111-8111-111111111111'
const runtimeGeneration = '22222222-2222-4222-8222-222222222222'
const proposalId = '33333333-3333-4333-8333-333333333333'

function runtime(): VoiceRuntimeContext {
  return {
    runtimeRef,
    runtimeGeneration,
    contextVersion: 3,
    stateVersion: 2,
    runtimeScope: 'MISSION_CENTER',
    runtimeKind: 'STANDALONE_ALGORITHM',
    executionBackend: 'PYTHON_SIMULATION',
    algorithmRunId: String(runId),
    missionId: null,
    missionRunId: null,
    state: 'PREPARED',
    protocolVersion: 'algorithm.command.v1',
    capabilities: ['START', 'PAUSE', 'RESUME', 'STOP', 'DEVICE_COMMAND'],
    lastHeartbeatReceivedAt: new Date().toISOString(),
    latestFrameSequence: 12,
    sceneReady: true,
  }
}

function proposal(targetDeviceCode = 'UAV-001'): VoiceProposal {
  return {
    proposalId,
    status: 'AWAITING_CONFIRMATION',
    planVersion: 1,
    planHash: 'camera-test-frozen-plan',
    requiresConfirmation: true,
    createdAt: new Date().toISOString(),
    expiresAt: new Date(Date.now() + 30_000).toISOString(),
    executionId: null,
    plan: {
      runtimeRef,
      runtimeGeneration,
      contextVersion: 3,
      stateVersion: 2,
      action: 'DEVICE_COMMAND',
      targetDeviceCode,
      deviceCommandType: targetDeviceCode.startsWith('UAV') ? 'UAV_HOVER' : 'USV_HOLD',
      explicitDeviceCodes: [targetDeviceCode],
      policyVersion: 'voice-p0.v1',
    },
  }
}

function execution(state: VoiceExecution['state'] = 'DISPATCHED'): VoiceExecution {
  return {
    executionId: '44444444-4444-4444-8444-444444444444',
    proposalId,
    commandId: '55555555-5555-4555-8555-555555555555',
    runtimeRef,
    runtimeGeneration,
    action: 'DEVICE_COMMAND',
    state,
    outcome: 'UNKNOWN',
    errorCode: null,
    timedOutAt: null,
    presentationStatus: 'NOT_REQUIRED',
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  }
}

type ViewSetup = {
  unityReady: boolean
  scenarioReadyRunId: number | null
  scenarioLoading: boolean
  plannedScenarioPoses: GridScenarioPose[]
  cameraMode: string
  selectedDevice: string
}

const wrappers: VueWrapper[] = []
async function createView() {
  const store = useVoiceControlStore()
  store.contexts = [runtime()]
  store.selectedRuntimeRef = runtimeRef
  store.expectedAlgorithmRunId = String(runId)
  const postToUnity = vi.fn((_type: string, _payload: Record<string, unknown>) => crypto.randomUUID())
  simulationRuntime.panel.value = {
    postToUnity,
    postPresentationEnvelope: vi.fn().mockReturnValue(true),
    beginViewportTransition: vi.fn(),
    endViewportTransition: vi.fn(),
    syncViewport: vi.fn(),
    reload: vi.fn(),
  } as unknown as NonNullable<typeof simulationRuntime.panel.value>
  const wrapper = mount(VirtualFleetConfigView, {
    global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
  })
  wrappers.push(wrapper)
  // Set only the Unity scene-readiness fixture. All camera watchers, handlers,
  // and the real Pinia store are exercised without preparing a live mission.
  const setup = wrapper.vm.$.setupState as unknown as ViewSetup
  setup.unityReady = true
  setup.scenarioReadyRunId = runId
  setup.scenarioLoading = false
  setup.plannedScenarioPoses = ['UAV-001', 'USV-001'].map(deviceCode => ({
    deviceCode,
    deviceType: deviceCode.startsWith('UAV') ? 'UAV' : 'USV',
    eastM: 0,
    northM: 0,
    upM: 0,
    headingDeg: 0,
    speedMps: 0,
    state: 'READY',
    valid: true,
  }))
  await flushPromises()
  postToUnity.mockClear()
  return { wrapper, store, setup, postToUnity }
}

type View = Awaited<ReturnType<typeof createView>>
function cameraCalls(view: View) {
  return view.postToUnity.mock.calls.filter(([type]) => type === 'setCameraMode' || type === 'selectDevice')
}
async function dispatch(view: View, target = 'UAV-001') {
  const dispatched = execution()
  view.store.proposal = { ...proposal(target), status: 'CONFIRMED', executionId: dispatched.executionId }
  view.store.execution = dispatched
  await nextTick()
}

describe('VirtualFleetConfigView single-device command camera', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-09T09:00:00.000Z'))
    sessionStorage.clear()
    setActivePinia(createPinia())
    useAuthStore().user = { username: 'admin', role: 'ADMIN' }
    simulationRuntime.recovering.value = false
  })

  afterEach(() => {
    for (const wrapper of wrappers.splice(0)) wrapper.unmount()
    simulationRuntime.panel.value = null
    simulationRuntime.events = null
    simulationRuntime.recovering.value = false
    sessionStorage.clear()
    vi.useRealTimers()
  })

  it('does not select or focus a device while only previewing or queueing the command', async () => {
    const view = await createView()
    view.store.proposal = proposal()
    await nextTick()
    expect(cameraCalls(view)).toEqual([])
    const queued = execution('QUEUED')
    view.store.proposal = { ...proposal(), status: 'CONFIRMED', executionId: queued.executionId }
    view.store.execution = queued
    await nextTick()
    expect(cameraCalls(view)).toEqual([])
    expect(view.setup.selectedDevice).toBe('')
  })

  it.each(['UAV-001', 'USV-001'])('briefly focuses dispatched %s then restores the global camera without trajectories', async target => {
    const view = await createView()
    await dispatch(view, target)
    expect(cameraCalls(view)).toEqual([
      ['selectDevice', { deviceCode: target }],
    ])
    expect(view.setup.selectedDevice).toBe(target)
    await vi.advanceTimersByTimeAsync(2499)
    expect(cameraCalls(view)).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(cameraCalls(view)).toEqual([
      ['selectDevice', { deviceCode: target }],
      ['setCameraMode', { mode: 'overview' }],
    ])
    expect(view.setup.cameraMode).toBe('overview')
    expect(view.postToUnity.mock.calls.some(([type, payload]) => type === 'toggleTrajectory' && payload.visible === true)).toBe(false)
  })

  it('does not restart the close-up timer for repeated execution polling updates', async () => {
    const view = await createView()
    await dispatch(view)
    await vi.advanceTimersByTimeAsync(1000)
    view.store.execution = { ...view.store.execution!, state: 'ACCEPTED', updatedAt: new Date().toISOString() }
    await nextTick()
    await vi.advanceTimersByTimeAsync(1500)
    expect(cameraCalls(view)).toEqual([
      ['selectDevice', { deviceCode: 'UAV-001' }],
      ['setCameraMode', { mode: 'overview' }],
    ])
  })

  it('waits for the actual device step in a two-step command before briefly focusing', async () => {
    const view = await createView()
    const receipt: VoiceExecution = {
      ...execution('EXECUTING'),
      action: 'SEQUENCE',
      currentStepIndex: 0,
      steps: [
        { index: 0, action: 'START', state: 'DISPATCHED', executionId: 'start-step', errorCode: null },
        { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'USV-001', deviceCommandType: 'USV_HOLD', state: 'PENDING', executionId: null, errorCode: null },
      ],
    }
    const frozen = proposal('USV-001')
    view.store.proposal = {
      ...frozen,
      status: 'CONFIRMED',
      executionId: receipt.executionId,
      plan: {
        ...frozen.plan,
        action: 'SEQUENCE',
        targetDeviceCode: undefined,
        deviceCommandType: undefined,
        steps: [
          { index: 0, action: 'START' },
          { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'USV-001', deviceCommandType: 'USV_HOLD' },
        ],
      },
    }
    view.store.execution = receipt
    await nextTick()
    expect(cameraCalls(view)).toEqual([])
    // Mutate the real nested execution steps, as a backend polling update does.
    view.store.execution.steps![0]!.state = 'SUCCEEDED'
    view.store.execution.steps![1]!.state = 'DISPATCHED'
    view.store.execution.steps![1]!.executionId = 'device-step'
    view.store.execution.currentStepIndex = 1
    await nextTick()
    expect(cameraCalls(view)).toEqual([['selectDevice', { deviceCode: 'USV-001' }]])
    await vi.advanceTimersByTimeAsync(2500)
    expect(cameraCalls(view)[1]).toEqual(['setCameraMode', { mode: 'overview' }])
  })

  it('keeps the timer running when Unity acknowledges the automatic camera request', async () => {
    const view = await createView()
    await dispatch(view)
    const requestId = view.postToUnity.mock.results[0]!.value as string
    simulationRuntime.events!.message({
      type: 'cameraChanged',
      requestId,
      payload: { mode: 'device-follow', deviceCode: 'UAV-001', success: true },
    })
    await nextTick()
    await vi.advanceTimersByTimeAsync(2500)
    expect(cameraCalls(view)[1]).toEqual(['setCameraMode', { mode: 'overview' }])
  })

  it('cancels the timer when the user changes the camera directly inside Unity', async () => {
    const view = await createView()
    await dispatch(view)
    simulationRuntime.events!.message({
      type: 'cameraChanged',
      payload: { mode: 'device-follow', deviceCode: 'USV-001', success: true },
    })
    await nextTick()
    await vi.advanceTimersByTimeAsync(5000)
    expect(cameraCalls(view)).toEqual([['selectDevice', { deviceCode: 'UAV-001' }]])
    expect(view.setup.selectedDevice).toBe('USV-001')
  })

  it('lets the manual global-view button cancel the pending automatic transition', async () => {
    const view = await createView()
    await dispatch(view)
    await vi.advanceTimersByTimeAsync(1000)
    await view.wrapper.get('button[aria-label="全局视角"]').trigger('click')
    expect(cameraCalls(view)).toHaveLength(2)
    expect(cameraCalls(view)[1]).toEqual(['setCameraMode', { mode: 'overview' }])
    await vi.advanceTimersByTimeAsync(5000)
    expect(cameraCalls(view)).toHaveLength(2)
  })

  it('respects manual continuous follow instead of pulling the operator back to overview', async () => {
    const view = await createView()
    await dispatch(view)
    await vi.advanceTimersByTimeAsync(1000)
    await view.wrapper.get('button[aria-label="跟随设备"]').trigger('click')
    expect(cameraCalls(view)).toEqual([
      ['selectDevice', { deviceCode: 'UAV-001' }],
      ['selectDevice', { deviceCode: 'UAV-001' }],
    ])
    await vi.advanceTimersByTimeAsync(5000)
    expect(cameraCalls(view)).toHaveLength(2)
    expect(view.setup.cameraMode).toBe('device-follow')
  })

  it('does not move the camera for an execution from a different runtime generation', async () => {
    const view = await createView()
    const mismatched = { ...execution(), runtimeGeneration: 'different-generation' }
    view.store.proposal = { ...proposal(), status: 'CONFIRMED', executionId: mismatched.executionId }
    view.store.execution = mismatched
    await nextTick()
    await vi.advanceTimersByTimeAsync(5000)
    expect(cameraCalls(view)).toEqual([])
  })

  it('cancels delayed camera messages when Unity reloads', async () => {
    const view = await createView()
    await dispatch(view)
    simulationRuntime.events!.loading()
    await nextTick()
    view.postToUnity.mockClear()
    await vi.advanceTimersByTimeAsync(5000)
    expect(cameraCalls(view)).toEqual([])
  })

  it('sends no delayed camera message after the view is unmounted', async () => {
    const view = await createView()
    await dispatch(view)
    view.wrapper.unmount()
    view.postToUnity.mockClear()
    await vi.advanceTimersByTimeAsync(5000)
    expect(view.postToUnity).not.toHaveBeenCalled()
  })
})

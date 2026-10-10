import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'

import VirtualFleetConfigView from '@/views/VirtualFleetConfigView.vue'
import { simulationRuntime } from '@/composables/simulationRuntime'
import { useAuthStore } from '@/stores/auth'
import { useVoiceControlStore } from '@/stores/voiceControl'
import { controlAlgorithmRun, fetchAlgorithmRunStatus, prepareAlgorithmRun } from '@/api/algorithm'
import { buildReturnInfrastructure, toGlobalReturnInfrastructure, type ReturnInfrastructure } from '@/utils/virtualReturnInfrastructure'
import { deriveAdaptiveScenarioPlan } from '@/utils/adaptiveScenarioPlan'
import { adaptVirtualAlgorithmFrame } from '@/utils/virtualAlgorithmFrameAdapter'
import type { AlgorithmRuntimeFrame } from '@/types/mission'
import { ApiClientError } from '@/api/http'
import type { VoiceRuntimeContext } from '@/types/voiceControl'

vi.mock('@/components/layout/ConsoleLayout.vue', () => ({ default: { template: '<main><slot /></main>' } }))
vi.mock('@/components/voice/VoiceP0ControlPanel.vue', () => ({
  default: { template: '<aside />', methods: { handleUnityMessage: vi.fn() } },
}))
vi.mock('@/api/algorithm', () => ({
  prepareAlgorithmRun: vi.fn().mockResolvedValue({}),
  controlAlgorithmRun: vi.fn(),
  fetchAlgorithmRunStatus: vi.fn().mockResolvedValue({ state: 'STOPPED', sequence: 0 }),
  fetchAlgorithmFrames: vi.fn().mockResolvedValue([]),
}))

interface ViewSetup {
  state: { algorithm: string; uavCount: number; usvCount: number; runId: number; mission: string; sequence: number }
  currentAlgorithmFrame: AlgorithmRuntimeFrame | null
  scenarioReadyRunId: number | null
  scenarioLoading: boolean
  algorithmPrepared: boolean
  generateScenario: () => Promise<void>
  prepareExternalAlgorithm: (allowWhileLoading?: boolean) => Promise<boolean>
  persistRuntimeRecovery: () => void
  send: (type: string, payload: Record<string, unknown>) => string | undefined
}

const wrappers: VueWrapper[] = []
async function createView() {
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
  await flushPromises()
  const setup = wrapper.vm.$.setupState as unknown as ViewSetup
  return { wrapper, setup, postToUnity }
}

type SavedMissionState = 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'STOPPED' | 'PAUSED' | 'RUNNING'

async function savedFinalPose(state: SavedMissionState) {
  const view = await createView()
  Object.assign(view.setup.state, { algorithm: 'GB_SFLA_CS', uavCount: 1, usvCount: 1 })
  await view.setup.generateScenario()
  const local = buildReturnInfrastructure({ uavCount: 1, usvCount: 1, worldHeight: deriveAdaptiveScenarioPlan(1, 1).worldHeight })
  const frame = {
    runId: view.setup.state.runId,
    sequence: 77,
    timestamp: Date.now(),
    coordinateFrame: 'FLEET_LOCAL_ENU',
    algorithmCode: 'GB_SFLA_CS',
    phase: state,
    agents: local.slots.map(slot => ({
      code: slot.deviceCode, type: slot.kind === 'HELIPAD' ? 'UAV' : 'USV',
      x: slot.eastM, y: slot.northM, z: slot.upM, heading: slot.headingDeg, status: 'RETURNED', role: 'RETURNED',
    })),
    targets: [{ code: 'TARGET-001', type: 'TARGET', x: 20, y: 25, z: 0, heading: 90, visible: true }],
    metrics: { returnInfrastructure: local },
    route: [], obstacles: [], terminalStatus: ['COMPLETED', 'FAILED', 'CANCELLED', 'STOPPED'].includes(state) ? state : null,
  } as AlgorithmRuntimeFrame
  const batch = adaptVirtualAlgorithmFrame(frame, new Map(), { fleetOrigin: { eastM: -360, northM: -285, upM: 0 } }).payload
  view.setup.currentAlgorithmFrame = frame
  Object.assign(view.setup.state, { mission: state, sequence: frame.sequence })
  view.setup.send('applyPoseBatch', { ...batch })
  view.setup.persistRuntimeRecovery()
  return { ...view, frame, batch }
}

function unmountView(wrapper: VueWrapper) {
  wrapper.unmount()
  wrappers.splice(wrappers.indexOf(wrapper), 1)
}

function sceneReady(runId: number) {
  simulationRuntime.events!.message({ type: 'scenarioReady', payload: { success: true, runId } })
}

function poseApplied(runId: number, sequence: number, appliedCount = 3, extra: Record<string, unknown> = {}) {
  simulationRuntime.events!.message({ type: 'poseFrameApplied', payload: {
    success: true, runId, sequence, appliedCount, missingDeviceCodes: [], unknownDeviceCodes: [], ...extra,
  } })
}

function expiredBackend(reason: 'LOST' | '404') {
  if (reason === '404') vi.mocked(fetchAlgorithmRunStatus).mockRejectedValue(new ApiClientError('Runtime not found', 404))
  else vi.mocked(fetchAlgorithmRunStatus).mockResolvedValue({ state: 'LOST', sequence: 77 } as Awaited<ReturnType<typeof fetchAlgorithmRunStatus>>)
}

describe('VirtualFleetConfigView return infrastructure payloads', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-09T09:00:00.000Z'))
    vi.clearAllMocks()
    vi.mocked(fetchAlgorithmRunStatus).mockResolvedValue({ state: 'STOPPED', sequence: 0 } as Awaited<ReturnType<typeof fetchAlgorithmRunStatus>>)
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

  it.each([[1, 1], [3, 3], [9, 17]])('shares one %i UAV / %i USV layout between local prepare and global Unity scene', async (uavCount, usvCount) => {
    const { setup, postToUnity } = await createView()
    Object.assign(setup.state, { algorithm: 'GB_SFLA_CS', uavCount, usvCount })
    await setup.generateScenario()
    const scene = postToUnity.mock.calls.find(([type]) => type === 'loadScenario')?.[1]
    expect(scene).toBeDefined()
    const expectedLocal = buildReturnInfrastructure({
      uavCount, usvCount, worldHeight: deriveAdaptiveScenarioPlan(uavCount, usvCount).worldHeight,
    })
    const expectedGlobal = toGlobalReturnInfrastructure(expectedLocal, { eastM: -360, northM: -285, upM: 0 })
    expect(scene).toMatchObject({
      initialPosesCoordinateFrame: 'GLOBAL_ENU', uavCount, usvCount,
      returnInfrastructure: expectedGlobal,
    })
    expect(await setup.prepareExternalAlgorithm(true)).toBe(true)
    expect(prepareAlgorithmRun).toHaveBeenCalledOnce()
    const [preparedRunId, algorithm, config] = vi.mocked(prepareAlgorithmRun).mock.calls[0]!
    expect(preparedRunId).toBe(scene!.runId)
    expect(algorithm).toBe('GB_SFLA_CS')
    expect(config).toMatchObject({
      coordinateFrame: 'FLEET_LOCAL_ENU',
      fleetOrigin: { eastM: -360, northM: -285, upM: 0 },
      returnInfrastructure: expectedLocal,
    })
    expect((scene!.returnInfrastructure as ReturnInfrastructure).coordinateFrame).toBe('GLOBAL_ENU')
    expect((config!.returnInfrastructure as ReturnInfrastructure).coordinateFrame).toBe('FLEET_LOCAL_ENU')
  })

  it('preserves the exact generated infrastructure in savedScenario and reload recovery', async () => {
    const first = await createView()
    Object.assign(first.setup.state, { algorithm: 'GB_SFLA_CS', uavCount: 9, usvCount: 17 })
    await first.setup.generateScenario()
    const scene = first.postToUnity.mock.calls.find(([type]) => type === 'loadScenario')![1]
    const original = structuredClone(scene.returnInfrastructure)
    first.setup.persistRuntimeRecovery()
    const snapshot = JSON.parse(sessionStorage.getItem('virtual-fleet.runtime-recovery.v1:admin')!)
    expect(snapshot.savedScenario.returnInfrastructure).toEqual(original)
    // Outbound message objects must not remain live aliases of the saved copy.
    const sentInfrastructure = scene.returnInfrastructure as ReturnInfrastructure
    sentInfrastructure.slots[0]!.eastM = 999999
    first.setup.persistRuntimeRecovery()
    expect(JSON.parse(sessionStorage.getItem('virtual-fleet.runtime-recovery.v1:admin')!).savedScenario.returnInfrastructure).toEqual(original)
    first.wrapper.unmount()
    wrappers.splice(wrappers.indexOf(first.wrapper), 1)
    const recovered = await createView()
    expect(recovered.setup.state.runId).toBe(first.setup.state.runId)
    simulationRuntime.events!.loading()
    await simulationRuntime.events!.ready()
    await flushPromises()
    expect(fetchAlgorithmRunStatus).toHaveBeenCalledWith(first.setup.state.runId)
    const restoredScene = recovered.postToUnity.mock.calls.find(([type]) => type === 'loadScenario')![1]
    expect(restoredScene.returnInfrastructure).toEqual(original)
    expect(restoredScene.runId).toBe(first.setup.state.runId)
    expect(prepareAlgorithmRun).not.toHaveBeenCalled()
  })

  it.each([
    ['COMPLETED', 'remount'], ['PAUSED', 'remount'], ['COMPLETED', 'reload'], ['PAUSED', 'reload'],
  ] as const)('restores %s final parked poses during %s without preparing or starting a task', async (state, mode) => {
    const original = await savedFinalPose(state)
    const expected = structuredClone(original.batch)
    expect(expected.returnInfrastructure?.version).toBe('scene-existing-v1')
    let restored: Awaited<ReturnType<typeof createView>> = original
    if (mode === 'remount') {
      unmountView(original.wrapper)
      restored = await createView()
    }
    restored.postToUnity.mockClear()
    vi.mocked(fetchAlgorithmRunStatus).mockResolvedValue({ state, sequence: 77 } as Awaited<ReturnType<typeof fetchAlgorithmRunStatus>>)
    simulationRuntime.events!.loading()
    await simulationRuntime.events!.ready()
    await flushPromises()
    expect(restored.postToUnity.mock.calls.filter(([type]) => type === 'applyPoseBatch')).toEqual([])
    expect(simulationRuntime.recovering.value).toBe(true)
    sceneReady(original.frame.runId)
    await flushPromises()
    const replay = restored.postToUnity.mock.calls.filter(([type]) => type === 'applyPoseBatch')
    expect(replay).toEqual([['applyPoseBatch', expected]])
    expect(simulationRuntime.recovering.value).toBe(true)
    expect(restored.setup.scenarioReadyRunId).toBeNull()
    poseApplied(original.frame.runId, 76)
    await flushPromises()
    expect(simulationRuntime.recovering.value).toBe(true)
    poseApplied(original.frame.runId + 1, 77)
    await flushPromises()
    expect(simulationRuntime.recovering.value).toBe(true)
    poseApplied(original.frame.runId, 77)
    await flushPromises()
    expect(simulationRuntime.recovering.value).toBe(false)
    expect(restored.setup.scenarioReadyRunId).toBe(original.frame.runId)
    expect(restored.setup.state.mission).toBe(state)
    expect(prepareAlgorithmRun).not.toHaveBeenCalled()
    expect(controlAlgorithmRun).not.toHaveBeenCalled()
    expect(restored.postToUnity.mock.calls.some(([type]) => ['missionStart', 'missionResume', 'missionReset'].includes(type))).toBe(false)
  })

  it.each(['COMPLETED', 'PAUSED'] as const)('rebuilds a missing cached pose batch from the saved %s algorithm frame', async state => {
    const original = await savedFinalPose(state)
    const expected = structuredClone(original.batch)
    unmountView(original.wrapper)
    const key = 'virtual-fleet.runtime-recovery.v1:admin'
    const snapshot = JSON.parse(sessionStorage.getItem(key)!)
    snapshot.latestPoseBatch = null
    sessionStorage.setItem(key, JSON.stringify(snapshot))
    const restored = await createView()
    vi.mocked(fetchAlgorithmRunStatus).mockResolvedValue({ state, sequence: 77 } as Awaited<ReturnType<typeof fetchAlgorithmRunStatus>>)
    simulationRuntime.events!.loading()
    await simulationRuntime.events!.ready()
    sceneReady(original.frame.runId)
    await flushPromises()
    expect(restored.postToUnity.mock.calls.filter(([type]) => type === 'applyPoseBatch')).toEqual([['applyPoseBatch', expected]])
    expect(simulationRuntime.recovering.value).toBe(true)
    poseApplied(original.frame.runId, 77)
    await flushPromises()
    expect(simulationRuntime.recovering.value).toBe(false)
    expect(prepareAlgorithmRun).not.toHaveBeenCalled()
    expect(controlAlgorithmRun).not.toHaveBeenCalled()
  })

  it.each(['partial-count', 'missing-device', 'unknown-device', 'failed'])(
    'does not finish parked-pose restoration after a %s application acknowledgement', async fault => {
      const original = await savedFinalPose('COMPLETED')
      vi.mocked(fetchAlgorithmRunStatus).mockResolvedValue({ state: 'COMPLETED', sequence: 77 } as Awaited<ReturnType<typeof fetchAlgorithmRunStatus>>)
      simulationRuntime.events!.loading()
      await simulationRuntime.events!.ready()
      sceneReady(original.frame.runId)
      await flushPromises()
      const extra = fault === 'missing-device' ? { missingDeviceCodes: ['UAV-001'] }
        : fault === 'unknown-device' ? { unknownDeviceCodes: ['UAV-999'] }
          : fault === 'failed' ? { success: false } : {}
      poseApplied(original.frame.runId, 77, fault === 'partial-count' ? 2 : 3, extra)
      await flushPromises()
      expect(simulationRuntime.recovering.value).toBe(true)
      expect(original.setup.scenarioReadyRunId).toBeNull()
      expect(simulationRuntime.recoveryError.value).not.toBe('')
      expect(prepareAlgorithmRun).not.toHaveBeenCalled()
      expect(controlAlgorithmRun).not.toHaveBeenCalled()
    },
  )

  it('never replays a cached pose batch or fallback frame from a different run', async () => {
    const original = await savedFinalPose('COMPLETED')
    unmountView(original.wrapper)
    const key = 'virtual-fleet.runtime-recovery.v1:admin'
    const snapshot = JSON.parse(sessionStorage.getItem(key)!)
    snapshot.latestPoseBatch.runId = original.frame.runId - 1
    snapshot.currentAlgorithmFrame.runId = original.frame.runId - 1
    sessionStorage.setItem(key, JSON.stringify(snapshot))
    const restored = await createView()
    vi.mocked(fetchAlgorithmRunStatus).mockResolvedValue({ state: 'COMPLETED', sequence: 77 } as Awaited<ReturnType<typeof fetchAlgorithmRunStatus>>)
    simulationRuntime.events!.loading()
    await simulationRuntime.events!.ready()
    sceneReady(original.frame.runId)
    await flushPromises()
    expect(restored.postToUnity.mock.calls.filter(([type]) => type === 'applyPoseBatch')).toEqual([])
    expect(prepareAlgorithmRun).not.toHaveBeenCalled()
    expect(controlAlgorithmRun).not.toHaveBeenCalled()
  })

  it.each([
    ['COMPLETED', 'LOST'], ['FAILED', 'LOST'], ['CANCELLED', 'LOST'], ['STOPPED', 'LOST'],
    ['COMPLETED', '404'], ['FAILED', '404'], ['CANCELLED', '404'], ['STOPPED', '404'],
  ] as const)('preserves the authoritative %s final picture when the expired runner reports %s', async (state, reason) => {
    const original = await savedFinalPose(state)
    const expected = structuredClone(original.batch)
    unmountView(original.wrapper)
    const restored = await createView()
    expiredBackend(reason)
    simulationRuntime.events!.loading()
    await simulationRuntime.events!.ready()
    await flushPromises()
    expect(restored.setup.state.mission).toBe(state)
    expect(restored.setup.algorithmPrepared).toBe(false)
    expect(restored.setup.state.runId).toBe(original.frame.runId)
    expect(restored.postToUnity.mock.calls.some(([type]) => type === 'loadScenario')).toBe(true)
    expect(simulationRuntime.recovering.value).toBe(true)
    sceneReady(original.frame.runId)
    await flushPromises()
    expect(restored.postToUnity.mock.calls.filter(([type]) => type === 'applyPoseBatch')).toEqual([['applyPoseBatch', expected]])
    poseApplied(original.frame.runId, 77)
    await flushPromises()
    expect(simulationRuntime.recovering.value).toBe(false)
    expect(restored.setup.state.mission).toBe(state)
    expect(restored.setup.algorithmPrepared).toBe(false)
    expect(prepareAlgorithmRun).not.toHaveBeenCalled()
    expect(controlAlgorithmRun).not.toHaveBeenCalled()
    expect(simulationRuntime.panel.value!.reload).not.toHaveBeenCalled()
    expect(restored.postToUnity.mock.calls.some(([type]) => ['missionStart', 'missionResume', 'missionReset'].includes(type))).toBe(false)
  })

  it.each([['RUNNING', 'LOST'], ['PAUSED', 'LOST'], ['RUNNING', '404'], ['PAUSED', '404']] as const)(
    'continues resetting a stale %s run on %s instead of pretending its snapshot is authoritative completion', async (state, reason) => {
      const original = await savedFinalPose(state)
      unmountView(original.wrapper)
      const restored = await createView()
      expiredBackend(reason)
      simulationRuntime.events!.loading()
      await simulationRuntime.events!.ready()
      await flushPromises()
      expect(restored.setup.state.mission).toBe('STOPPED')
      expect(restored.setup.currentAlgorithmFrame).toBeNull()
      expect(restored.setup.algorithmPrepared).toBe(false)
      expect(restored.postToUnity.mock.calls.some(([type]) => type === 'missionReset')).toBe(true)
      expect(restored.postToUnity.mock.calls.some(([type]) => ['loadScenario', 'applyPoseBatch'].includes(type))).toBe(false)
      expect(simulationRuntime.panel.value!.reload).toHaveBeenCalledOnce()
      expect(prepareAlgorithmRun).not.toHaveBeenCalled()
      expect(controlAlgorithmRun).not.toHaveBeenCalled()
    },
  )

  it.each([
    ['wrong-frame-run', 'LOST'], ['wrong-terminal-status', 'LOST'], ['missing-frame', 'LOST'], ['wrong-scene-run', 'LOST'],
    ['wrong-frame-run', '404'], ['wrong-terminal-status', '404'], ['missing-frame', '404'], ['wrong-scene-run', '404'],
  ] as const)('rejects %s as proof of an expired terminal snapshot on %s', async (fault, reason) => {
    const original = await savedFinalPose('COMPLETED')
    unmountView(original.wrapper)
    const key = 'virtual-fleet.runtime-recovery.v1:admin'
    const snapshot = JSON.parse(sessionStorage.getItem(key)!)
    if (fault === 'wrong-frame-run') snapshot.currentAlgorithmFrame.runId -= 1
    if (fault === 'wrong-terminal-status') snapshot.currentAlgorithmFrame.terminalStatus = 'FAILED'
    if (fault === 'missing-frame') snapshot.currentAlgorithmFrame = null
    if (fault === 'wrong-scene-run') snapshot.savedScenario.runId -= 1
    sessionStorage.setItem(key, JSON.stringify(snapshot))
    const restored = await createView()
    expiredBackend(reason)
    simulationRuntime.events!.loading()
    await simulationRuntime.events!.ready()
    await flushPromises()
    expect(restored.setup.state.mission).toBe('STOPPED')
    expect(restored.setup.algorithmPrepared).toBe(false)
    expect(restored.postToUnity.mock.calls.some(([type]) => type === 'missionReset')).toBe(true)
    expect(restored.postToUnity.mock.calls.some(([type]) => ['loadScenario', 'applyPoseBatch'].includes(type))).toBe(false)
    expect(prepareAlgorithmRun).not.toHaveBeenCalled()
    expect(controlAlgorithmRun).not.toHaveBeenCalled()
  })

  it.each(['COMPLETED', 'RUNNING'] as const)('keeps the last %s picture when voice-context polling subsequently reports LOST', async mission => {
    const original = await savedFinalPose(mission)
    original.postToUnity.mockClear()
    const store = useVoiceControlStore()
    const lost: VoiceRuntimeContext = {
      runtimeRef: '11111111-1111-4111-8111-111111111111',
      runtimeGeneration: '22222222-2222-4222-8222-222222222222',
      algorithmRunId: String(original.frame.runId), state: 'LOST', contextVersion: 1, stateVersion: 99,
      runtimeScope: 'MISSION_CENTER', runtimeKind: 'STANDALONE_ALGORITHM', executionBackend: 'PYTHON_SIMULATION',
      protocolVersion: 'algorithm.command.v1', capabilities: [], missionId: null, missionRunId: null,
      sceneReady: false, lastHeartbeatReceivedAt: null, latestFrameSequence: 77,
    }
    store.contexts = [lost]
    store.selectedRuntimeRef = lost.runtimeRef
    store.expectedAlgorithmRunId = lost.algorithmRunId
    await flushPromises()
    expect(original.setup.state.mission).toBe(mission === 'COMPLETED' ? 'COMPLETED' : 'STOPPED')
    expect(original.setup.currentAlgorithmFrame?.sequence).toBe(77)
    expect(original.setup.algorithmPrepared).toBe(false)
    expect(original.postToUnity.mock.calls.some(([type]) => type === 'missionReset')).toBe(false)
    expect(simulationRuntime.panel.value!.reload).not.toHaveBeenCalled()
  })

  it.each([
    ['RUNNING', 'savedScenario'], ['RUNNING', 'latestPoseBatch'], ['RUNNING', 'currentAlgorithmFrame'],
    ['COMPLETED', 'savedScenario'], ['COMPLETED', 'latestPoseBatch'], ['COMPLETED', 'currentAlgorithmFrame'],
  ] as const)('does not replay a %s snapshot containing retired fixed-shore metadata in %s', async (state, location) => {
    const original = await savedFinalPose(state)
    unmountView(original.wrapper)
    const key = 'virtual-fleet.runtime-recovery.v1:admin'
    const snapshot = JSON.parse(sessionStorage.getItem(key)!)
    // Isolate each metadata location: no other old-version field may hide a
    // missing migration check on the selected path.
    snapshot.savedScenario.returnInfrastructure.version = 'scene-existing-v1'
    snapshot.latestPoseBatch.returnInfrastructure.version = 'scene-existing-v1'
    snapshot.currentAlgorithmFrame.metrics.returnInfrastructure.version = 'scene-existing-v1'
    const owner = location === 'currentAlgorithmFrame' ? snapshot.currentAlgorithmFrame.metrics : snapshot[location]
    owner.returnInfrastructure.version = 'fixed-shore-v1'
    sessionStorage.setItem(key, JSON.stringify(snapshot))
    const fresh = await createView()
    expect(fresh.setup.state.runId).not.toBe(original.frame.runId)
    expect(fresh.setup.currentAlgorithmFrame).toBeNull()
    expect(fresh.setup.algorithmPrepared).toBe(false)
    expect(fresh.setup.state.mission).toBe('STOPPED')
    // Use the deterministic capture preview fixture, without invoking a
    // backend algorithm or starting the discarded active/terminal run.
    fresh.setup.state.algorithm = 'GB_SFLA_CS'
    await vi.advanceTimersByTimeAsync(1)
    simulationRuntime.events!.loading()
    expect(simulationRuntime.recovering.value).toBe(false)
    await simulationRuntime.events!.ready()
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()
    const scenes = fresh.postToUnity.mock.calls.filter(([type]) => type === 'loadScenario')
    expect(scenes).toHaveLength(1)
    expect(scenes[0]![1].runId).not.toBe(original.frame.runId)
    expect(fresh.postToUnity.mock.calls.some(([type]) => type === 'applyPoseBatch')).toBe(false)
    expect(fresh.postToUnity.mock.calls.some(([type]) => ['missionStart', 'missionResume'].includes(type))).toBe(false)
    expect(fetchAlgorithmRunStatus).not.toHaveBeenCalled()
    expect(prepareAlgorithmRun).not.toHaveBeenCalled()
    expect(controlAlgorithmRun).not.toHaveBeenCalled()
  })
})

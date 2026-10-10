import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'

import VirtualFleetConfigView from '@/views/VirtualFleetConfigView.vue'
import VoiceP0ControlPanel from '@/components/voice/VoiceP0ControlPanel.vue'
import { simulationRuntime } from '@/composables/simulationRuntime'
import { useAuthStore } from '@/stores/auth'
import type { VoiceMockRuntimeHint } from '@/types/voiceControl'
import { buildVirtualFleetGridLayout, type GridScenarioPose } from '@/utils/virtualFleetGridLayout'

vi.mock('@/components/layout/ConsoleLayout.vue', () => ({ default: { template: '<main><slot /></main>' } }))
vi.mock('@/components/voice/VoiceP0ControlPanel.vue', () => ({
  default: {
    props: ['runtimeHint'],
    template: '<aside />',
    methods: { handleUnityMessage: vi.fn() },
  },
}))
vi.mock('@/api/algorithm', () => ({
  prepareAlgorithmRun: vi.fn().mockResolvedValue({}),
  controlAlgorithmRun: vi.fn(),
  fetchAlgorithmRunStatus: vi.fn().mockResolvedValue({ state: 'STOPPED', sequence: 0 }),
  fetchAlgorithmFrames: vi.fn().mockResolvedValue([]),
}))

interface ViewSetup {
  state: { algorithm: string; uavCount: number; usvCount: number; runId: number }
  plannedScenarioPoses: GridScenarioPose[]
  generateScenario: () => Promise<void>
  persistRuntimeRecovery: () => void
}

const fleet = ['UAV-001', 'UAV-002', 'UAV-003', 'USV-001', 'USV-002', 'USV-003']
const wrappers: VueWrapper[] = []
async function createView() {
  const postToUnity = vi.fn((_type: string, _payload: Record<string, unknown>) => crypto.randomUUID())
  simulationRuntime.panel.value = {
    postToUnity, postPresentationEnvelope: vi.fn().mockReturnValue(true),
    beginViewportTransition: vi.fn(), endViewportTransition: vi.fn(), syncViewport: vi.fn(), reload: vi.fn(),
  } as unknown as NonNullable<typeof simulationRuntime.panel.value>
  const wrapper = mount(VirtualFleetConfigView, {
    global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
  })
  wrappers.push(wrapper)
  await flushPromises()
  const setup = wrapper.vm.$.setupState as unknown as ViewSetup
  const hint = () => wrapper.getComponent(VoiceP0ControlPanel).props('runtimeHint') as VoiceMockRuntimeHint
  return { wrapper, setup, postToUnity, hint }
}

describe('VirtualFleetConfigView voice fleet membership', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-10T09:00:00.000Z'))
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

  it('passes only the six controllable agents to voice while retaining the capture target in Unity', async () => {
    const view = await createView()
    Object.assign(view.setup.state, { algorithm: 'GB_SFLA_CS', uavCount: 3, usvCount: 3 })
    await view.setup.generateScenario()
    await flushPromises()
    const scene = view.postToUnity.mock.calls.find(([type]) => type === 'loadScenario')![1]
    const poses = scene.initialPoses as GridScenarioPose[]
    expect(poses.map(pose => pose.deviceCode)).toEqual([...fleet, 'TARGET-001'])
    expect(view.hint().state).toBe('PREVIEW')
    expect(view.hint().deviceCodes).toEqual(fleet)
    expect(view.setup.plannedScenarioPoses).toHaveLength(7)
  })

  it.each([true, false])('excludes scene targets on reactive scene changes (capture=%s)', async captureMode => {
    const view = await createView()
    const poses = buildVirtualFleetGridLayout({
      uavCount: 3, usvCount: 3, fleetOrigin: { eastM: -360, northM: -285, upM: 0 },
      uavSpeedMps: 5, usvSpeedMps: 3, captureMode, scenarioId: 7001,
    })
    view.setup.plannedScenarioPoses = poses
    await flushPromises()
    expect(poses.filter(pose => pose.deviceType === 'TARGET').map(pose => pose.deviceCode))
      .toEqual(captureMode ? ['TARGET-001'] : ['PROTECTED-001', 'THREAT-001'])
    expect(view.hint().deviceCodes).toEqual(fleet)
    // Fleet membership is independent of motion state; returned/stopped agents
    // still belong to the backend's frozen fleet and must not disappear here.
    view.setup.plannedScenarioPoses = poses.map(pose => ({ ...pose, state: 'RETURNED', speedMps: 0 }))
    await flushPromises()
    expect(view.hint().deviceCodes).toEqual(fleet)
    view.setup.plannedScenarioPoses = poses.filter(pose => pose.deviceCode !== 'UAV-003')
    await flushPromises()
    expect(view.hint().deviceCodes).toEqual(fleet.filter(code => code !== 'UAV-003'))
  })

  it('filters a restored scene without deleting its cached target or changing its run', async () => {
    const original = await createView()
    Object.assign(original.setup.state, { algorithm: 'GB_SFLA_CS', uavCount: 3, usvCount: 3 })
    await original.setup.generateScenario()
    original.setup.persistRuntimeRecovery()
    const runId = original.setup.state.runId
    original.wrapper.unmount()
    wrappers.splice(wrappers.indexOf(original.wrapper), 1)
    const restored = await createView()
    expect(restored.setup.state.runId).toBe(runId)
    expect(restored.setup.plannedScenarioPoses.map(pose => pose.deviceCode)).toEqual([...fleet, 'TARGET-001'])
    expect(restored.hint().deviceCodes).toEqual(fleet)
    expect(restored.postToUnity).not.toHaveBeenCalled()
  })
})

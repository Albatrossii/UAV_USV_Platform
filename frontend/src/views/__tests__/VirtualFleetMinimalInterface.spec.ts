import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'

import VirtualFleetConfigView from '@/views/VirtualFleetConfigView.vue'
import { simulationRuntime } from '@/composables/simulationRuntime'
import { appendExperimentFrame, archiveError } from '@/services/experimentArchive'
import { useAuthStore } from '@/stores/auth'
import { useUnityBridgeStore } from '@/stores/unityBridge'
import type { AlgorithmRuntimeFrame } from '@/types/mission'

vi.mock('@/components/layout/ConsoleLayout.vue', () => ({ default: { template: '<main><slot /></main>' } }))
vi.mock('@/components/voice/VoiceP0ControlPanel.vue', () => ({
  default: { template: '<aside data-test="voice-control" />', methods: { handleUnityMessage: vi.fn() } },
}))
vi.mock('@/components/voice/ExperimentArchive.vue', () => ({
  default: { template: '<section data-test="experiment-archive">实验记录与回放</section>' },
}))
vi.mock('@/services/experimentArchive', async () => {
  const { ref } = await import('vue')
  return {
    archiveError: ref(''),
    appendExperimentFrame: vi.fn(),
    appendExperimentEvent: vi.fn(),
    listExperiments: vi.fn().mockResolvedValue([]),
  }
})
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
  applyAlgorithmFrame: (frame: AlgorithmRuntimeFrame) => Promise<void>
}

const wrappers: VueWrapper[] = []

async function createView() {
  const postToUnity = vi.fn().mockReturnValue('minimal-ui-test-request')
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
  Object.assign(setup.state, { algorithm: 'GB_SFLA_CS', runId: 7001, mission: 'RUNNING', uavCount: 3, usvCount: 3, sequence: 0 })
  setup.scenarioReadyRunId = 7001
  await flushPromises()
  return { wrapper, setup, postToUnity }
}

function frame(sequence = 1, terminalStatus: AlgorithmRuntimeFrame['terminalStatus'] = null): AlgorithmRuntimeFrame {
  return {
    runId: 7001, sequence, timestamp: Date.now(), coordinateFrame: 'FLEET_LOCAL_ENU',
    algorithmCode: 'GB_SFLA_CS', phase: 'PURSUIT',
    agents: [], targets: [], route: [], obstacles: [], terminalStatus,
    metrics: { missionStage: 'PURSUIT', missionProgress: .25 },
  }
}

describe('VirtualFleetConfigView minimal simulation interface', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-11T09:00:00.000Z'))
    vi.clearAllMocks()
    archiveError.value = ''
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
    archiveError.value = ''
    sessionStorage.clear()
    vi.useRealTimers()
  })

  it('preserves configuration, Unity viewport, mission controls, and voice controls', async () => {
    const { wrapper } = await createView()
    expect(wrapper.get('.vf-config-panel').text()).toContain('场景配置')
    expect(wrapper.get('.vf-config-panel').text()).toContain('生成场景')
    expect(wrapper.get('.vf-config-panel').findAll('input')).toHaveLength(4)
    expect(wrapper.find('.vf-config-panel [data-test="websocket-quality-card"]').exists()).toBe(true)
    expect((wrapper.get('.wsq-details').element as HTMLDetailsElement).open).toBe(false)
    expect(wrapper.find('[data-simulation-viewport]').exists()).toBe(true)
    for (const title of ['开始任务', '暂停任务', '停止任务']) {
      expect(wrapper.find(`button[title="${title}"]`).exists()).toBe(true)
    }
    expect(wrapper.find('[aria-label="语音控制"] [data-test="voice-control"]').exists()).toBe(true)
    expect(wrapper.find('[aria-label="全局视角"]').exists()).toBe(true)
  })

  it('preserves the original algorithm display names and protocol codes', async () => {
    const { wrapper } = await createView()
    expect(wrapper.get('option[value="ESCORT_GUARD"]').text()).toBe('智能粒球仿真护航')
    expect(wrapper.get('option[value="GB_SFLA_CS"]').text()).toBe('GB-SFLA-CS 协同围捕')
  })

  it('does not render experiment archive, replay, or archive errors', async () => {
    const { wrapper } = await createView()
    archiveError.value = 'archive-disabled-sentinel'
    await flushPromises()
    expect(wrapper.find('[data-test="experiment-archive"]').exists()).toBe(false)
    expect(wrapper.text()).not.toMatch(/实验记录|回放|archive-disabled-sentinel/)
  })

  it('does not add a dedicated radio link panel even when a frame carries wireless metrics', async () => {
    const { wrapper, setup } = await createView()
    const sample = frame()
    sample.metrics.communicationLinks = {
      'UAV-001': { type: '5G', qualityPercent: 96, connectionState: 'ONLINE' },
      'USV-001': { type: 'SEMANTIC', bearer: '4G', qualityPercent: 72 },
    }
    setup.currentAlgorithmFrame = sample
    await flushPromises()
    expect(wrapper.text()).not.toMatch(/通信链路|语义通讯|4G|5G|质量|待上报|96%|72%/)
  })

  it('keeps one header connection status without a redundant badge over the scene', async () => {
    const { wrapper } = await createView()
    const bridge = useUnityBridgeStore()
    expect(wrapper.get('.vf-instance-status').text()).toContain('CONNECTING')
    expect(wrapper.find('.vf-unity-state').exists()).toBe(false)
    bridge.channels.VIRTUAL_FLEET.connected = true
    bridge.channels.VIRTUAL_FLEET.platformReady = true
    await flushPromises()
    expect(wrapper.get('.vf-instance-status').text()).toContain('ONLINE')
    expect(wrapper.find('.vf-unity-state').exists()).toBe(false)
    bridge.channels.VIRTUAL_FLEET.connected = false
    await flushPromises()
    expect(wrapper.get('.vf-instance-status').classes()).toContain('offline')
    expect(wrapper.find('.vf-unity-state').exists()).toBe(false)
  })

  it('continues applying live and terminal algorithm frames without writing experiment records', async () => {
    const { wrapper, setup, postToUnity } = await createView()
    const bridge = useUnityBridgeStore()
    bridge.channels.VIRTUAL_FLEET.connected = true
    bridge.channels.VIRTUAL_FLEET.platformReady = true
    await flushPromises()
    await setup.applyAlgorithmFrame(frame())
    await flushPromises()
    expect(setup.state.sequence).toBe(1)
    expect(setup.currentAlgorithmFrame?.sequence).toBe(1)
    expect(wrapper.get('.vf-live-strip').text()).toContain('25%')
    expect(postToUnity).toHaveBeenCalledWith('applyPoseBatch', expect.objectContaining({ runId: 7001 }))
    await setup.applyAlgorithmFrame(frame(2, 'COMPLETED'))
    await flushPromises()
    expect(setup.state.sequence).toBe(2)
    expect(setup.state.mission).toBe('COMPLETING')
    expect(appendExperimentFrame).not.toHaveBeenCalled()
  })
})

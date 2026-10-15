import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'

import VirtualFleetConfigView from '@/views/VirtualFleetConfigView.vue'
import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import VoiceP0ControlPanel from '@/components/voice/VoiceP0ControlPanel.vue'
import { simulationRuntime } from '@/composables/simulationRuntime'
import { appendExperimentFrame, archiveError } from '@/services/experimentArchive'
import { useAuthStore } from '@/stores/auth'
import { useUnityBridgeStore } from '@/stores/unityBridge'
import { controlAlgorithmRun } from '@/api/algorithm'
import type { AlgorithmRuntimeFrame } from '@/types/mission'

const { handleUnityPresentationMessage } = vi.hoisted(() => ({ handleUnityPresentationMessage: vi.fn() }))

vi.mock('@/components/layout/ConsoleLayout.vue', () => ({
  default: { props: { hideSidebar: Boolean }, template: '<main><slot /></main>' },
}))
vi.mock('@/components/layout/ConsoleNavigationRail.vue', () => ({
  default: {
    name: 'ConsoleNavigationRail',
    props: { collapsed: Boolean },
    emits: ['toggle'],
    template: '<nav data-test="navigation-rail"><button :aria-label="collapsed ? \'展开总控\' : \'收起总控\'" @click="$emit(\'toggle\')">总控</button></nav>',
  },
}))
vi.mock('@/components/voice/VoiceP0ControlPanel.vue', () => ({
  default: {
    props: { refined: Boolean, compact: Boolean, runtimeHint: Object, frame: Object, unitySession: Object },
    emits: ['presentation-message'],
    data: () => ({ draft: '' }),
    template: '<aside data-test="voice-control"><textarea v-model="draft" aria-label="语音指令草稿测试" /></aside>',
    methods: { handleUnityMessage: vi.fn(), handleUnityPresentationMessage },
  },
}))
vi.mock('@/components/monitoring/NodeCommunicationPanel.vue', () => ({
  default: {
    name: 'NodeCommunicationPanel',
    props: ['deviceCodes', 'selectedDevice', 'targets', 'targetSource', 'defaultOpen'],
    emits: ['select'],
    template: '<section data-test="node-communication-panel" />',
  },
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
  state: { algorithm: string; uavCount: number; usvCount: number; uavSpeed: number; usvSpeed: number; runId: number; mission: string; sequence: number }
  currentAlgorithmFrame: AlgorithmRuntimeFrame | null
  initialScenarioPoses: Array<{ deviceCode: string; deviceType?: string; valid?: boolean }>
  plannedScenarioPoses: Array<{ deviceCode: string; deviceType?: string; valid?: boolean }>
  scenarioReadyRunId: number | null
  scenarioLoading: boolean
  unityReady: boolean
  algorithmPrepared: boolean
  selectedDevice: string
  cameraMode: string
  leftPanelCollapsed: boolean
  applyAlgorithmFrame: (frame: AlgorithmRuntimeFrame) => Promise<void>
}

const wrappers: VueWrapper[] = []
const scenarioSelector = '.vf-scenario-config:not(.vf-voice-config)'

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
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('preserves configuration, Unity viewport, mission controls, and voice controls', async () => {
    const { wrapper } = await createView()
    expect(wrapper.getComponent(ConsoleLayout).props('hideSidebar')).toBe(true)
    expect(wrapper.findAll('.vf-config-drawer')).toHaveLength(1)
    expect(wrapper.find('.vf-inspector-drawer').exists()).toBe(false)
    expect(wrapper.find('.vf-config-drawer [data-test="navigation-rail"]').exists()).toBe(true)
    expect(wrapper.get('.vf-config-panel').attributes('aria-label')).toBe('任务控制台')
    expect(wrapper.get('.vf-config-panel').text()).toContain('场景配置')
    expect(wrapper.get('.vf-config-panel').text()).toContain('生成场景')
    expect(wrapper.get('.vf-config-panel').findAll('input')).toHaveLength(4)
    expect(wrapper.find('.vf-config-panel [data-test="node-communication-panel"]').exists()).toBe(true)
    expect(wrapper.getComponent({ name: 'NodeCommunicationPanel' }).props('defaultOpen')).toBe(false)
    const modules = wrapper.findAll('.vf-config-panel > details, .vf-config-panel > [data-test="node-communication-panel"]')
    expect(modules.map(module => module.element)).toEqual([
      wrapper.get('.vf-voice-config').element,
      wrapper.get(scenarioSelector).element,
      wrapper.get('[data-test="node-communication-panel"]').element,
    ])
    expect((wrapper.get('.vf-voice-config').element as HTMLDetailsElement).open).toBe(true)
    expect((wrapper.get(scenarioSelector).element as HTMLDetailsElement).open).toBe(true)
    expect(wrapper.find('[data-simulation-viewport]').exists()).toBe(true)
    for (const title of ['开始任务', '暂停任务', '停止任务']) {
      expect(wrapper.find(`button[title="${title}"]`).exists()).toBe(true)
    }
    expect(wrapper.findAll('[data-test="voice-control"]')).toHaveLength(1)
    expect(wrapper.find('.vf-voice-config [data-test="voice-control"]').exists()).toBe(true)
    expect(wrapper.getComponent(VoiceP0ControlPanel).props()).toMatchObject({ refined: true, compact: true })
    expect(wrapper.find('[aria-label="全局视角"]').exists()).toBe(true)
  })

  it('preserves the original algorithm display names and protocol codes', async () => {
    const { wrapper } = await createView()
    expect(wrapper.get('option[value="ESCORT_GUARD"]').text()).toBe('智能粒球仿真护航')
    expect(wrapper.get('option[value="GB_SFLA_CS"]').text()).toBe('GB-SFLA-CS 协同围捕')
  })

  it('keeps entered parameters and input elements when folding the scenario or the whole sidebar', async () => {
    const { wrapper, setup } = await createView()
    setup.state.mission = 'STOPPED'
    await flushPromises()
    const inputs = wrapper.findAll('.vf-scenario-body input')
    const originalInputs = inputs.map(input => input.element)
    await wrapper.get('.vf-scenario-body select').setValue('ESCORT_GUARD')
    for (const [index, value] of [2, 1, 7.5, 2.5].entries()) await inputs[index]!.setValue(value)
    const details = wrapper.get(scenarioSelector).element as HTMLDetailsElement
    const summary = wrapper.get(`${scenarioSelector} > summary`)
    await summary.trigger('click')
    expect(details.open).toBe(false)
    expect(summary.text()).toContain('2 UAV · 1 USV')
    await wrapper.get('button[title="收起总控"]').trigger('click')
    expect(wrapper.get('.vf-config-drawer').classes()).toContain('collapsed')
    await wrapper.get('button[aria-label="展开总控"]').trigger('click')
    expect(details.open).toBe(false)
    await summary.trigger('click')
    expect(details.open).toBe(true)
    expect(wrapper.findAll('.vf-scenario-body input').map(input => input.element)).toEqual(originalInputs)
    expect(setup.state).toMatchObject({ algorithm: 'ESCORT_GUARD', uavCount: 2, usvCount: 1, uavSpeed: 7.5, usvSpeed: 2.5 })
  })

  it('folds voice and scenario modules independently without changing the running mission', async () => {
    const { wrapper, setup, postToUnity } = await createView()
    const voiceDetails = wrapper.get('.vf-voice-config').element as HTMLDetailsElement
    const scenarioDetails = wrapper.get(scenarioSelector).element as HTMLDetailsElement
    const monitoring = wrapper.getComponent({ name: 'NodeCommunicationPanel' }).vm
    const mission = { ...setup.state }
    postToUnity.mockClear()
    vi.mocked(controlAlgorithmRun).mockClear()

    await wrapper.get('.vf-voice-config > summary').trigger('click')
    expect(voiceDetails.open).toBe(false)
    expect(scenarioDetails.open).toBe(true)
    await wrapper.get(`${scenarioSelector} > summary`).trigger('click')
    expect(scenarioDetails.open).toBe(false)
    await wrapper.get('.vf-voice-config > summary').trigger('click')
    expect(voiceDetails.open).toBe(true)
    expect(scenarioDetails.open).toBe(false)
    expect(wrapper.getComponent({ name: 'NodeCommunicationPanel' }).vm).toBe(monitoring)
    expect(setup.state).toEqual(mission)
    expect(postToUnity).not.toHaveBeenCalled()
    expect(controlAlgorithmRun).not.toHaveBeenCalled()
  })

  it('keeps the voice instance, draft, runtime props, and message bindings while the whole console is folded', async () => {
    const { wrapper, setup, postToUnity } = await createView()
    const voice = wrapper.getComponent(VoiceP0ControlPanel)
    const voiceInstance = voice.vm
    const draft = voice.get('textarea')
    await draft.setValue('让 UAV-001 悬停')
    const mission = { ...setup.state }
    const panel = simulationRuntime.panel.value!
    postToUnity.mockClear()
    vi.mocked(controlAlgorithmRun).mockClear()

    await wrapper.get('[data-test="navigation-rail"] button').trigger('click')
    expect(wrapper.get('.vf-config-drawer').classes()).toContain('collapsed')
    expect(wrapper.get('.vf-config-panel').attributes()).toHaveProperty('inert')
    expect(wrapper.getComponent({ name: 'ConsoleNavigationRail' }).props('collapsed')).toBe(true)
    expect(wrapper.get('[data-test="navigation-rail"]').element.closest('[inert]')).toBeNull()
    expect(wrapper.getComponent(VoiceP0ControlPanel).vm).toBe(voiceInstance)
    expect(wrapper.get('[data-simulation-viewport]').exists()).toBe(true)
    expect(setup.state).toEqual(mission)
    expect(postToUnity).not.toHaveBeenCalled()
    expect(controlAlgorithmRun).not.toHaveBeenCalled()

    const sample = frame(3)
    setup.currentAlgorithmFrame = sample
    setup.state.sequence = sample.sequence
    await flushPromises()
    expect(voice.props('frame')).toEqual(sample)
    expect(voice.props('runtimeHint')).toMatchObject({ state: 'RUNNING', latestFrameSequence: 3 })
    expect(voice.props('unitySession')).toHaveProperty('connected')
    const ready = { type: 'PRESENTATION_READY', payload: { scenarioReady: true } }
    simulationRuntime.events!.message(ready)
    expect(handleUnityPresentationMessage).toHaveBeenCalledWith(ready)
    const hello = {
      type: 'PRESENTATION_HELLO', protocolVersion: 'unity.presentation.v1',
      runtimeRef: 'runtime-ref', runtimeGeneration: 'runtime-generation', bindingId: 'binding-id',
      unityInstanceId: 'unity-instance', sceneRevision: 1,
    }
    voice.vm.$emit('presentation-message', hello)
    expect(panel.postPresentationEnvelope).toHaveBeenCalledWith(hello)

    await wrapper.get('button[aria-label="展开总控"]').trigger('click')
    expect(wrapper.get('.vf-config-drawer').classes()).not.toContain('collapsed')
    expect(wrapper.get('.vf-config-panel').attributes()).not.toHaveProperty('inert')
    expect(wrapper.getComponent(VoiceP0ControlPanel).vm).toBe(voiceInstance)
    expect(voice.get('textarea').element).toBe(draft.element)
    expect((draft.element as HTMLTextAreaElement).value).toBe('让 UAV-001 悬停')
    expect(setup.state).toEqual({ ...mission, sequence: sample.sequence })
    expect(postToUnity).not.toHaveBeenCalled()
    expect(controlAlgorithmRun).not.toHaveBeenCalled()
  })

  it('settles Unity viewport resizing when a folded console emits no CSS transition event', async () => {
    const { wrapper } = await createView()
    const panel = simulationRuntime.panel.value!
    await wrapper.get('button[title="收起总控"]').trigger('click')
    expect(panel.beginViewportTransition).toHaveBeenCalledOnce()
    expect(wrapper.get('.vf-workbench').classes()).toContain('panel-transitioning')
    await vi.advanceTimersByTimeAsync(300)
    expect(panel.endViewportTransition).toHaveBeenCalledOnce()
    expect(wrapper.get('.vf-workbench').classes()).not.toContain('panel-transitioning')
  })

  it('requests Unity position tracking after collapsing and reopening without ResizeObserver support', async () => {
    vi.stubGlobal('ResizeObserver', undefined)
    const { wrapper, setup } = await createView()
    const collapsedStates: boolean[] = []
    const track = vi.fn(() => collapsedStates.push(setup.leftPanelCollapsed))
    window.addEventListener('unity-runtime-track', track)
    try {
      await wrapper.get('button[title="收起总控"]').trigger('click')
      expect(track).toHaveBeenCalledOnce()
      expect(collapsedStates).toEqual([true])
      await wrapper.get('button[aria-label="展开总控"]').trigger('click')
      expect(track).toHaveBeenCalledTimes(2)
      expect(collapsedStates).toEqual([true, false])
    } finally {
      window.removeEventListener('unity-runtime-track', track)
    }
  })

  it('tracks control drawer resizing only while active and disconnects its observer on unmount', async () => {
    const observe = vi.fn()
    const disconnect = vi.fn()
    let notifyResize!: () => void
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: () => void) { notifyResize = callback }
      observe = observe
      disconnect = disconnect
    })
    const host = mount({
      components: { VirtualFleetConfigView },
      data: () => ({ visible: true }),
      template: '<KeepAlive><VirtualFleetConfigView v-if="visible" /></KeepAlive>',
    }, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    wrappers.push(host)
    await flushPromises()
    const originalViewId = host.getComponent(VirtualFleetConfigView).vm.$.uid
    expect(observe).toHaveBeenCalledOnce()
    expect(observe).toHaveBeenCalledWith(host.get('.vf-config-drawer').element)

    const track = vi.fn()
    window.addEventListener('unity-runtime-track', track)
    try {
      notifyResize()
      expect(track).toHaveBeenCalledOnce()
      await host.setData({ visible: false })
      notifyResize()
      expect(track).toHaveBeenCalledOnce()
      expect(disconnect).not.toHaveBeenCalled()
      await host.setData({ visible: true })
      expect(host.getComponent(VirtualFleetConfigView).vm.$.uid).toBe(originalViewId)
      notifyResize()
      expect(track).toHaveBeenCalledTimes(2)
      expect(observe).toHaveBeenCalledOnce()

      host.unmount()
      wrappers.splice(wrappers.indexOf(host), 1)
      expect(disconnect).toHaveBeenCalledOnce()
    } finally {
      window.removeEventListener('unity-runtime-track', track)
    }
  })

  it('preserves control disabled conditions and resets the scene without resetting configuration', async () => {
    const { wrapper, setup, postToUnity } = await createView()
    const inputs = wrapper.findAll('.vf-scenario-body input')
    expect(inputs.every(input => (input.element as HTMLInputElement).disabled)).toBe(true)
    expect((wrapper.get('.vf-scenario-body select').element as HTMLSelectElement).disabled).toBe(true)
    expect((wrapper.get('.vf-actions .primary').element as HTMLButtonElement).disabled).toBe(true)
    expect((wrapper.get('.vf-actions button:not(.primary)').element as HTMLButtonElement).disabled).toBe(true)
    setup.unityReady = true
    setup.algorithmPrepared = true
    Object.assign(setup.state, { algorithm: 'ESCORT_GUARD', uavCount: 2, usvCount: 1, uavSpeed: 6.5, usvSpeed: 2.2 })
    setup.initialScenarioPoses = [{ deviceCode: 'UAV-001', deviceType: 'UAV' }]
    setup.selectedDevice = 'UAV-001'
    await flushPromises()
    await wrapper.get('.vf-actions button:not(.primary)').trigger('click')
    await flushPromises()
    expect(controlAlgorithmRun).toHaveBeenCalledWith(7001, 'cancel')
    expect(postToUnity).toHaveBeenCalledWith('missionReset', expect.objectContaining({ runId: 7001 }))
    expect(simulationRuntime.panel.value?.reload).toHaveBeenCalledOnce()
    expect(setup.state).toMatchObject({ algorithm: 'ESCORT_GUARD', uavCount: 2, usvCount: 1, uavSpeed: 6.5, usvSpeed: 2.2, mission: 'STOPPED', sequence: 0 })
    expect(setup.initialScenarioPoses).toEqual([])
    expect(setup.selectedDevice).toBe('')
    expect(inputs.every(input => !(input.element as HTMLInputElement).disabled)).toBe(true)
    setup.scenarioLoading = true
    await flushPromises()
    expect((wrapper.get('.vf-actions .primary').element as HTMLButtonElement).disabled).toBe(true)
  })

  it('uses configuration only before generation and keeps actual scene devices separate from targets', async () => {
    const { wrapper, setup } = await createView()
    const panel = wrapper.getComponent({ name: 'NodeCommunicationPanel' })
    expect(panel.props('deviceCodes')).toEqual(['UAV-001', 'UAV-002', 'UAV-003', 'USV-001', 'USV-002', 'USV-003'])
    setup.state.uavCount = 1
    setup.state.usvCount = 2
    await flushPromises()
    expect(panel.props('deviceCodes')).toEqual(['UAV-001', 'USV-001', 'USV-002'])
    setup.plannedScenarioPoses = [{ deviceCode: 'UAV-003', deviceType: 'UAV' }]
    setup.initialScenarioPoses = [
      { deviceCode: 'UAV-002', deviceType: 'UAV' },
      { deviceCode: 'USV-001', deviceType: 'USV' },
      { deviceCode: 'USV-002', deviceType: 'USV', valid: false },
      { deviceCode: 'TARGET-001', deviceType: 'TARGET' },
      { deviceCode: 'UAV-099', deviceType: 'TARGET' },
    ]
    await flushPromises()
    expect(panel.props('deviceCodes')).toEqual(['UAV-002', 'USV-001'])
    expect(panel.props('targets')).toEqual([])
    const sample = frame()
    sample.targets = [
      { code: 'ESCORT-001', type: 'ESCORT_TARGET', state: 'GUARDING', x: 1, y: 2, z: 0, heading: 0, visible: true },
      { code: 'THREAT-001', type: 'THREAT_TARGET', state: 'CAPTURED', x: 3, y: 4, z: 0, heading: 0, visible: false },
    ]
    setup.currentAlgorithmFrame = sample
    await flushPromises()
    expect(panel.props('targets')).toEqual([
      { id: 'ESCORT-001', label: '护航目标', state: 'GUARDING' },
      { id: 'THREAT-001', label: '敌船', state: 'CAPTURED' },
    ])
    expect(panel.props('targetSource')).toBe('算法仿真 · 当前场景')
    expect(panel.props('deviceCodes')).toEqual(['UAV-002', 'USV-001'])
  })

  it('selects a node without issuing a task or changing the camera', async () => {
    const { wrapper, setup, postToUnity } = await createView()
    const panel = wrapper.getComponent({ name: 'NodeCommunicationPanel' })
    setup.cameraMode = 'overview'
    postToUnity.mockClear()
    vi.mocked(controlAlgorithmRun).mockClear()
    panel.vm.$emit('select', 'USV-002')
    await flushPromises()
    expect(setup.selectedDevice).toBe('USV-002')
    expect(panel.props('selectedDevice')).toBe('USV-002')
    expect(setup.cameraMode).toBe('overview')
    expect(postToUnity).not.toHaveBeenCalled()
    expect(controlAlgorithmRun).not.toHaveBeenCalled()
    panel.vm.$emit('select', 'TARGET-001')
    await flushPromises()
    expect(setup.selectedDevice).toBe('USV-002')
  })

  it('does not render experiment archive, replay, or archive errors', async () => {
    const { wrapper } = await createView()
    archiveError.value = 'archive-disabled-sentinel'
    await flushPromises()
    expect(wrapper.find('[data-test="experiment-archive"]').exists()).toBe(false)
    expect(wrapper.text()).not.toMatch(/实验记录|回放|archive-disabled-sentinel/)
  })

  it('does not reinterpret algorithm wireless metrics as measured node telemetry', async () => {
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

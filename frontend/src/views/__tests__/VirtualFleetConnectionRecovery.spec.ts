import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import View from '../VirtualFleetConfigView.vue'
import { controlAlgorithmRun, fetchAlgorithmFrames, prepareAlgorithmRun } from '@/api/algorithm'
import { useUnityBridgeStore } from '@/stores/unityBridge'
import { simulationRuntime } from '@/composables/simulationRuntime'
vi.mock('@/components/layout/ConsoleLayout.vue', () => ({ default: { template: '<main><slot /></main>' } }))
vi.mock('@/components/layout/ConsoleNavigationRail.vue', () => ({ default: { template: '<nav />' } }))
vi.mock('@/components/voice/VoiceP0ControlPanel.vue', () => ({ default: { template: '<aside />' } }))
vi.mock('@/api/algorithm', () => ({
  prepareAlgorithmRun: vi.fn().mockResolvedValue({}), controlAlgorithmRun: vi.fn(),
  fetchAlgorithmRunStatus: vi.fn().mockResolvedValue({ state: 'STOPPED', sequence: 0 }),
  fetchAlgorithmFrames: vi.fn(),
}))
beforeEach(() => { vi.useFakeTimers(); setActivePinia(createPinia()); sessionStorage.clear(); simulationRuntime.recovering.value = false })
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); simulationRuntime.panel.value = null; simulationRuntime.events = null })
it('backs off failed reads, preserves run identity and recovers without dispatching an action', async () => {
  const wrapper = mount(View, { global: { stubs: { RouterLink: true, ExperimentArchive: true, NodeCommunicationPanel: true } } })
  await flushPromises()
  const setup = wrapper.vm.$.setupState as unknown as {
    state: { mission: string; runId: number; sequence: number }
    unityReady: boolean; algorithmPrepared: boolean; scenarioLoading: boolean
    pollAlgorithmFrame: () => Promise<void>; frameConnectionError: string
  }
  Object.assign(setup.state, { mission: 'RUNNING', runId: 42, sequence: 8 })
  setup.unityReady = true; setup.algorithmPrepared = true; setup.scenarioLoading = false
  vi.mocked(fetchAlgorithmFrames).mockRejectedValueOnce(new Error('offline')).mockResolvedValue([])
  await setup.pollAlgorithmFrame()
  expect(setup.frameConnectionError).toContain('不会重发控制指令')
  await setup.pollAlgorithmFrame()
  expect(fetchAlgorithmFrames).toHaveBeenCalledTimes(1)
  vi.setSystemTime(Date.now() + 500)
  await setup.pollAlgorithmFrame()
  expect(fetchAlgorithmFrames).toHaveBeenCalledTimes(2)
  expect(setup.frameConnectionError).toBe('')
  expect(setup.state.runId).toBe(42)
  expect(setup.state.sequence).toBe(8)
  wrapper.unmount()
})

function connectVirtualRenderer() {
  const bridge = useUnityBridgeStore()
  bridge.setPlatformCapabilitiesFor('VIRTUAL_FLEET', { ready: true, controlsReady: true, cameraReady: true, algorithmReady: true })
  bridge.setConnectedFor('VIRTUAL_FLEET', true)
  return bridge
}

it('publishes voice identity only after preparation succeeds and retains it for terminal receipts', async () => {
  const wrapper = mount(View)
  const setup = wrapper.vm.$.setupState as unknown as {
    state: { runId: number; mission: string }
    voiceRuntimeHint: { algorithmRunId: string }
    algorithmPrepared: boolean
    prepareExternalAlgorithm: () => Promise<boolean>
  }
  try {
    expect(setup.voiceRuntimeHint.algorithmRunId).toBe('')
    vi.mocked(prepareAlgorithmRun).mockRejectedValueOnce(new Error('prepare unavailable'))
    expect(await setup.prepareExternalAlgorithm()).toBe(false)
    expect(setup.voiceRuntimeHint.algorithmRunId).toBe('')
    expect(await setup.prepareExternalAlgorithm()).toBe(true)
    expect(setup.voiceRuntimeHint.algorithmRunId).toBe(String(setup.state.runId))
    setup.algorithmPrepared = false
    setup.state.mission = 'COMPLETED'
    expect(setup.voiceRuntimeHint.algorithmRunId).toBe(String(setup.state.runId))
    setup.state.runId++
    expect(setup.voiceRuntimeHint.algorithmRunId).toBe('')
  } finally { wrapper.unmount() }
})

it('adopts an already-ready renderer without creating or starting a scenario', async () => {
  connectVirtualRenderer()
  const wrapper = mount(View)
  try {
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1)
    expect(wrapper.text()).toContain('独立仿真 WebGL · ONLINE')
    const generate = wrapper.findAll('button').find(button => button.text().includes('生成场景'))!
    expect(generate.attributes('disabled')).toBeUndefined()
    expect(prepareAlgorithmRun).not.toHaveBeenCalled()
    expect(controlAlgorithmRun).not.toHaveBeenCalled()
  } finally { wrapper.unmount() }
})

it('tracks channel loss and recovery without replaying commands', async () => {
  const bridge = connectVirtualRenderer()
  const wrapper = mount(View)
  try {
    bridge.setErrorFor('VIRTUAL_FLEET', 'offline')
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('独立仿真 WebGL · CONNECTING')
    connectVirtualRenderer()
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('独立仿真 WebGL · ONLINE')
    expect(prepareAlgorithmRun).not.toHaveBeenCalled()
    expect(controlAlgorithmRun).not.toHaveBeenCalled()
  } finally { wrapper.unmount() }
})

it('does not use another renderer or transport connection alone as platform readiness', async () => {
  const bridge = useUnityBridgeStore()
  bridge.setPlatformCapabilitiesFor('SYSTEM_OVERVIEW', { ready: true })
  bridge.setConnectedFor('SYSTEM_OVERVIEW', true)
  bridge.setConnectedFor('VIRTUAL_FLEET', true)
  const wrapper = mount(View)
  try {
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('独立仿真 WebGL · CONNECTING')
    expect(wrapper.findAll('button').find(button => button.text().includes('生成场景'))!.attributes('disabled')).toBeDefined()
  } finally { wrapper.unmount() }
})

it('does not let an old view disposal remove a newer runtime subscriber', () => {
  const oldView = mount(View)
  const newView = mount(View)
  const newEvents = simulationRuntime.events
  oldView.unmount()
  expect(simulationRuntime.events).toBe(newEvents)
  expect(simulationRuntime.requested.value).toBe(true)
  newView.unmount()
  expect(simulationRuntime.events).toBeNull()
  expect(simulationRuntime.requested.value).toBe(false)
})

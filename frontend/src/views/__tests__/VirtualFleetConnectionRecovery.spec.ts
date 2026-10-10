import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import View from '../VirtualFleetConfigView.vue'
import { fetchAlgorithmFrames } from '@/api/algorithm'
import { simulationRuntime } from '@/composables/simulationRuntime'
vi.mock('@/components/layout/ConsoleLayout.vue', () => ({ default: { template: '<main><slot /></main>' } }))
vi.mock('@/components/voice/VoiceP0ControlPanel.vue', () => ({ default: { template: '<aside />' } }))
vi.mock('@/api/algorithm', () => ({
  prepareAlgorithmRun: vi.fn().mockResolvedValue({}), controlAlgorithmRun: vi.fn(),
  fetchAlgorithmRunStatus: vi.fn().mockResolvedValue({ state: 'STOPPED', sequence: 0 }),
  fetchAlgorithmFrames: vi.fn(),
}))
beforeEach(() => { vi.useFakeTimers(); setActivePinia(createPinia()); sessionStorage.clear(); simulationRuntime.recovering.value = false })
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); simulationRuntime.panel.value = null; simulationRuntime.events = null })
it('backs off failed reads, preserves run identity and recovers without dispatching an action', async () => {
  const wrapper = mount(View, { global: { stubs: { RouterLink: true, ExperimentArchive: true } } })
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

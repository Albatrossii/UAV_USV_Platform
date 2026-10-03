import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

import DashboardView from '@/views/DashboardView.vue'
import { useMonitoringStore } from '@/stores/monitoring'
import { useRealtimeStore } from '@/stores/realtime'
import { useUnityViewportStore } from '@/stores/unityViewport'

vi.mock('@/api/algorithm', () => ({
  fetchAlgorithms: vi.fn(async () => []),
  prepareAlgorithmRun: vi.fn(),
  controlAlgorithmRun: vi.fn(),
}))
vi.mock('@/api/mission', () => ({
  fetchMissions: vi.fn(async () => ({ records: [] })),
  fetchMission: vi.fn(),
  executeMissionAction: vi.fn(),
}))

afterEach(() => {
  document.body.replaceChildren()
})

describe('DashboardView Unity overlay', () => {
  it('does not mount its Teleport while the simulation workspace has no overview Unity host', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    vi.spyOn(useMonitoringStore(), 'refresh').mockResolvedValue(undefined)
    vi.spyOn(useMonitoringStore(), 'connectEvents').mockImplementation(() => {})
    vi.spyOn(useMonitoringStore(), 'disconnectEvents').mockImplementation(() => {})
    vi.spyOn(useRealtimeStore(), 'connect').mockImplementation(() => {})
    vi.spyOn(useUnityViewportStore(), 'show').mockImplementation(() => {})

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'dashboard', component: { template: '<div />' } }],
    })
    await router.push('/?workspace=simulation')
    await router.isReady()
    const warnings = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const host = document.createElement('div')
    document.body.append(host)

    const wrapper = mount(DashboardView, {
      attachTo: host,
      global: {
        plugins: [pinia, router],
        stubs: {
          ConsoleLayout: { template: '<div><slot /></div>' },
          PointCloudOverviewPanel: true,
          VehicleGlyph: true,
        },
      },
    })
    await flushPromises()
    expect(warnings.mock.calls.flat().join(' ')).not.toContain('Failed to locate Teleport target')
    expect(host.querySelector('.overview-camera-tools')).toBeNull()

    const overlay = document.createElement('div')
    overlay.id = 'unity-runtime-overlay-system-overview-overview-unity-01'
    document.body.append(overlay)
    await router.push('/')
    await nextTick()
    expect(overlay.querySelector('.overview-camera-tools')).not.toBeNull()

    await router.push('/?workspace=simulation')
    await nextTick()
    expect(overlay.querySelector('.overview-camera-tools')).toBeNull()
    wrapper.unmount()
    warnings.mockRestore()
  })
})

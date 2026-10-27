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
  it.each([false, true])('preserves overview content and overlay lifecycle (embedded=%s)', async (embedded) => {
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
      props: { embedded },
      attachTo: host,
      global: {
        plugins: [pinia, router],
        stubs: {
          ConsoleLayout: { template: '<div><slot /></div>' },
          PointCloudOverviewPanel: true,
          VehicleGlyph: true,
          VoiceP0ControlPanel: { template: '<div data-test="overview-voice-control">真实任务语音控制</div>' },
        },
      },
    })
    await flushPromises()
    expect(wrapper.find('.overview-hf-statusbar').exists()).toBe(!embedded)
    expect(wrapper.find('.overview-taskbar').exists()).toBe(true)
    expect(wrapper.find('.overview-device-deck').exists()).toBe(true)
    expect(wrapper.find('[data-unity-runtime-viewport="dashboard"]').exists()).toBe(true)
    expect(wrapper.find('.overview-mission-console').exists()).toBe(false)
    expect(wrapper.get('[aria-label="任务控制台"]').text()).toContain('真实任务语音控制')
    expect(wrapper.get('.overview-voice-control > summary .task-console-section-title').text()).toBe('语音控制')
    expect(wrapper.get('.overview-voice-control > summary .task-console-section-meta').text()).toBe('语音 / 文本')
    expect(wrapper.find('.overview-voice-control > summary .task-console-section-arrow').exists()).toBe(true)
    expect(wrapper.findAll('[data-test="overview-voice-control"]')).toHaveLength(1)
    expect(wrapper.get('[data-test="node-communication-panel"]')).toBeTruthy()
    expect((wrapper.get('[data-test="nodes-panel"]').element as HTMLDetailsElement).open).toBe(false)
    expect(wrapper.emitted('workspace-status')?.slice(-1)[0]?.[0]).toMatchObject({
      deviceContext: '全局态势',
      statuses: expect.arrayContaining([expect.objectContaining({ label: 'ROS 离线' })]),
    })
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

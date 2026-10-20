import { createPinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import WorkspaceHeader from '@/components/layout/WorkspaceHeader.vue'
import DashboardView from '@/views/DashboardView.vue'
import OverviewWorkspaceView from '@/views/OverviewWorkspaceView.vue'
import VirtualFleetConfigView from '@/views/VirtualFleetConfigView.vue'

const lifecycle = vi.hoisted(() => ({
  overviewMounted: 0,
  overviewUnmounted: 0,
  simulationMounted: 0,
  simulationUnmounted: 0,
}))

vi.mock('@/views/DashboardView.vue', async () => {
  const { defineComponent, onMounted, onBeforeUnmount, ref } = await import('vue')
  return {
    default: defineComponent({
      name: 'DashboardView',
      props: { embedded: { type: Boolean, default: false } },
      emits: ['workspace-status'],
      setup(_, { emit }) {
        const draft = ref('')
        onMounted(() => {
          lifecycle.overviewMounted += 1
          emit('workspace-status', {
            deviceCode: 'UAV-01', deviceContext: '全局态势',
            statuses: [{ label: 'ROS 离线', tone: 'offline' }],
          })
        })
        onBeforeUnmount(() => { lifecycle.overviewUnmounted += 1 })
        return { draft }
      },
      template: '<section data-test="overview-content"><input v-model="draft" aria-label="总览状态测试" /></section>',
    }),
  }
})

vi.mock('@/views/VirtualFleetConfigView.vue', async () => {
  const { defineComponent, onMounted, onBeforeUnmount, ref } = await import('vue')
  return {
    default: defineComponent({
      name: 'VirtualFleetConfigView',
      props: { embedded: { type: Boolean, default: false } },
      emits: ['workspace-status'],
      setup(_, { emit }) {
        const draft = ref('')
        onMounted(() => {
          lifecycle.simulationMounted += 1
          emit('workspace-status', {
            deviceCode: 'USV-002', deviceContext: '无人艇',
            statuses: [{ label: '独立仿真 WebGL · ONLINE', tone: 'online' }],
          })
        })
        onBeforeUnmount(() => { lifecycle.simulationUnmounted += 1 })
        return { draft }
      },
      template: '<section data-test="simulation-content"><textarea v-model="draft" aria-label="仿真语音草稿测试" /></section>',
    }),
  }
})

const SIDEBAR_STORAGE_KEY = 'uav-usv:sidebar-collapsed'
const wrappers: VueWrapper[] = []

async function createWorkspace(path = '/') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'dashboard', component: { template: '<div />' } },
      ...['/situation', '/vision', '/radar', '/devices'].map(route => ({
        path: route, component: { template: '<div />' },
      })),
    ],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(OverviewWorkspaceView, {
    global: { plugins: [createPinia(), router] },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}

describe('OverviewWorkspaceView shared outer shell', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
    for (const key of Object.keys(lifecycle) as Array<keyof typeof lifecycle>) lifecycle[key] = 0
  })

  afterEach(() => {
    for (const wrapper of wrappers.splice(0)) wrapper.unmount()
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  it('renders one navigation and one shared header while prewarming embedded simulation content', async () => {
    const { wrapper } = await createWorkspace()
    expect(wrapper.findAllComponents(ConsoleLayout)).toHaveLength(1)
    expect(wrapper.findAllComponents(WorkspaceHeader)).toHaveLength(1)
    expect(wrapper.findAll('[aria-label="主导航"]')).toHaveLength(1)
    expect(wrapper.findAll('.console-brand')).toHaveLength(1)
    expect(wrapper.findAll('.console-sidebar-user')).toHaveLength(1)
    expect(wrapper.getComponent(DashboardView).props('embedded')).toBe(true)
    expect(wrapper.findComponent(VirtualFleetConfigView).exists()).toBe(false)

    await vi.advanceTimersByTimeAsync(1250)
    await flushPromises()

    expect(wrapper.getComponent(VirtualFleetConfigView).props('embedded')).toBe(true)
    expect(wrapper.findAllComponents(ConsoleLayout)).toHaveLength(1)
    expect(wrapper.findAllComponents(WorkspaceHeader)).toHaveLength(1)
    expect(wrapper.findAll('[aria-label="主导航"]')).toHaveLength(1)
    expect(lifecycle.overviewMounted).toBe(1)
    expect(lifecycle.simulationMounted).toBe(1)
    expect(wrapper.get('.overview-pane').attributes('aria-hidden')).toBe('false')
    expect(wrapper.get('.simulation-pane').attributes('aria-hidden')).toBe('true')
    expect(wrapper.get('.overview-pane').attributes()).not.toHaveProperty('inert')
    expect(wrapper.get('.simulation-pane').attributes()).toHaveProperty('inert')
  })

  it('preserves both content instances and entered drafts through repeated workspace switches', async () => {
    const { wrapper, router } = await createWorkspace('/?workspace=simulation')
    const overview = wrapper.getComponent(DashboardView)
    const simulation = wrapper.getComponent(VirtualFleetConfigView)
    const overviewInput = overview.get('input')
    const simulationInput = simulation.get('textarea')
    await overviewInput.setValue('总览原有设备选择')
    await simulationInput.setValue('一号无人机悬停，二号无人艇驻留')

    for (const path of ['/', '/?workspace=simulation', '/', '/?workspace=simulation']) {
      await router.push(path)
      await flushPromises()
    }

    expect(wrapper.getComponent(DashboardView).vm.$.uid).toBe(overview.vm.$.uid)
    expect(wrapper.getComponent(VirtualFleetConfigView).vm.$.uid).toBe(simulation.vm.$.uid)
    expect(wrapper.getComponent(DashboardView).get('input').element).toBe(overviewInput.element)
    expect(wrapper.getComponent(VirtualFleetConfigView).get('textarea').element).toBe(simulationInput.element)
    expect((overviewInput.element as HTMLInputElement).value).toBe('总览原有设备选择')
    expect((simulationInput.element as HTMLTextAreaElement).value).toBe('一号无人机悬停，二号无人艇驻留')
    expect(lifecycle).toEqual({
      overviewMounted: 1, overviewUnmounted: 0,
      simulationMounted: 1, simulationUnmounted: 0,
    })
    expect(wrapper.get('.overview-pane').classes()).toContain('is-inactive')
    expect(wrapper.get('.simulation-pane').classes()).toContain('is-active')
    expect(wrapper.get('.overview-pane').attributes()).toHaveProperty('inert')
    expect(wrapper.get('.simulation-pane').attributes()).not.toHaveProperty('inert')
  })

  it('uses the shared workspace links to change only the active content', async () => {
    const { wrapper, router } = await createWorkspace()
    const switcher = wrapper.get('[aria-label="工作空间切换"]')
    const links = switcher.findAll('a')
    expect(links).toHaveLength(2)
    expect(links.map(link => link.text())).toEqual(['系统总览', '算法仿真'])
    const originalHeader = wrapper.getComponent(WorkspaceHeader).vm.$.uid

    await links[1]!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.workspace).toBe('simulation')
    expect(wrapper.getComponent(WorkspaceHeader).props('activeWorkspace')).toBe('simulation')
    expect(wrapper.get('.simulation-pane').classes()).toContain('is-active')

    await links[0]!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.workspace).toBeUndefined()
    expect(wrapper.get('.overview-pane').classes()).toContain('is-active')
    expect(wrapper.getComponent(WorkspaceHeader).vm.$.uid).toBe(originalHeader)
    expect(lifecycle.overviewUnmounted + lifecycle.simulationUnmounted).toBe(0)
  })

  it('isolates header device and connection state from background workspace updates', async () => {
    const { wrapper, router } = await createWorkspace('/?workspace=simulation')
    const header = wrapper.getComponent(WorkspaceHeader)
    expect(header.props('activeWorkspace')).toBe('simulation')
    expect(header.props('state')).toMatchObject({ deviceCode: 'USV-002', deviceContext: '无人艇' })
    expect(header.text()).toContain('独立仿真 WebGL · ONLINE')
    expect(header.text()).not.toContain('ROS 离线')

    wrapper.getComponent(DashboardView).vm.$emit('workspace-status', {
      deviceCode: 'UAV-03', deviceContext: '设备跟随',
      statuses: [{ label: 'ROS 在线', tone: 'online' }],
    })
    await flushPromises()
    expect(header.props('state')).toMatchObject({ deviceCode: 'USV-002' })
    expect(header.text()).not.toContain('ROS 在线')

    await router.push('/')
    await flushPromises()
    expect(header.props('activeWorkspace')).toBe('overview')
    expect(header.props('state')).toMatchObject({ deviceCode: 'UAV-03', deviceContext: '设备跟随' })
    expect(header.text()).toContain('ROS 在线')
    expect(header.text()).not.toContain('独立仿真 WebGL')

    wrapper.getComponent(VirtualFleetConfigView).vm.$emit('workspace-status', {
      deviceCode: 'UAV-002', deviceContext: '无人机',
      statuses: [{ label: '独立仿真 WebGL · OFFLINE', tone: 'offline' }],
    })
    await flushPromises()
    expect(header.props('state')).toMatchObject({ deviceCode: 'UAV-03' })
    await router.push('/?workspace=simulation')
    await flushPromises()
    expect(header.props('state')).toMatchObject({ deviceCode: 'UAV-002' })
    expect(header.text()).toContain('独立仿真 WebGL · OFFLINE')
  })

  it('shares the expanded navigation preference across workspaces without remounting content', async () => {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, 'false')
    const { wrapper, router } = await createWorkspace('/?workspace=simulation')
    const layout = wrapper.getComponent(ConsoleLayout)
    const originalNavigation = wrapper.get('[aria-label="主导航"]').element
    expect(layout.classes()).not.toContain('sidebar-collapsed')
    expect(wrapper.get('button[aria-label="折叠菜单"]').exists()).toBe(true)

    await wrapper.get('.console-sidebar-toggle').trigger('click')
    expect(layout.classes()).toContain('sidebar-collapsed')
    expect(localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('true')
    await router.push('/')
    await flushPromises()
    expect(wrapper.getComponent(ConsoleLayout).vm.$.uid).toBe(layout.vm.$.uid)
    expect(layout.classes()).toContain('sidebar-collapsed')
    expect(wrapper.get('[aria-label="主导航"]').element).toBe(originalNavigation)

    await wrapper.get('button[aria-label="展开菜单"]').trigger('click')
    await router.push('/?workspace=simulation')
    await flushPromises()
    expect(layout.classes()).not.toContain('sidebar-collapsed')
    expect(localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('false')
    expect(wrapper.findAll('.console-nav-label').map(label => label.text())).toEqual([
      '系统总览', '协同态势', '光电视觉', '雷达态势', '设备管理',
    ])
    expect(lifecycle.overviewMounted).toBe(1)
    expect(lifecycle.simulationMounted).toBe(1)
    expect(lifecycle.overviewUnmounted + lifecycle.simulationUnmounted).toBe(0)
  })
})

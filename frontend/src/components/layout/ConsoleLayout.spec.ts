import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'

const SIDEBAR_STORAGE_KEY = 'uav-usv:sidebar-collapsed'

async function mountLayout(props: Partial<InstanceType<typeof ConsoleLayout>['$props']> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: ['/', '/situation', '/vision', '/radar', '/devices'].map((path) => ({
      path,
      component: { template: '<div />' },
    })),
  })
  await router.push('/')
  await router.isReady()
  return mount(ConsoleLayout, {
    props: { title: '系统总览', eyebrow: '平台监控', ...props },
    slots: { default: '<section data-test="content">页面内容</section>', actions: '<span>页面操作</span>' },
    global: { plugins: [createPinia(), router] },
  })
}

afterEach(() => {
  vi.useRealTimers()
})

describe('ConsoleLayout sidebar visibility', () => {
  it('preserves the default sidebar, header, actions and page content on other pages', async () => {
    const wrapper = await mountLayout()
    expect(wrapper.find('.console-sidebar').exists()).toBe(true)
    expect(wrapper.findAll('.console-navigation a')).toHaveLength(5)
    expect(wrapper.classes()).not.toContain('sidebar-hidden')
    expect(wrapper.classes()).not.toContain('sidebar-collapsed')
    expect(wrapper.classes()).not.toContain('workspace-navigation')
    expect(wrapper.get('.console-title h1').text()).toBe('系统总览')
    expect(wrapper.get('.console-topbar-actions').text()).toContain('页面操作')
    expect(wrapper.get('.console-workspace [data-test="content"]').text()).toBe('页面内容')
  })

  it('hides only the sidebar and enables the single-column workspace when requested', async () => {
    const wrapper = await mountLayout({ hideSidebar: true })
    expect(wrapper.find('.console-sidebar').exists()).toBe(false)
    expect(wrapper.classes()).toContain('sidebar-hidden')
    expect(wrapper.get('.console-title h1').text()).toBe('系统总览')
    expect(wrapper.get('.console-workspace [data-test="content"]').text()).toBe('页面内容')

    await wrapper.setProps({ immersive: true })
    expect(wrapper.find('.console-topbar').exists()).toBe(false)
    expect(wrapper.get('.console-workspace [data-test="content"]').text()).toBe('页面内容')
  })

  it('keeps a stored sidebar preference while hiding and restoring the sidebar', async () => {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, 'true')
    const wrapper = await mountLayout({ hideSidebar: true })
    expect(localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('true')

    await wrapper.setProps({ hideSidebar: false })
    expect(wrapper.classes()).not.toContain('sidebar-hidden')
    expect(wrapper.classes()).toContain('sidebar-collapsed')
    expect(wrapper.get('.console-sidebar').attributes('aria-label')).toBe('折叠导航')

    vi.useFakeTimers()
    await wrapper.get('.console-sidebar-toggle').trigger('click')
    expect(wrapper.classes()).not.toContain('sidebar-collapsed')
    expect(localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('false')
    await vi.runAllTimersAsync()

    await wrapper.setProps({ hideSidebar: true })
    await wrapper.setProps({ hideSidebar: false })
    expect(wrapper.classes()).not.toContain('sidebar-collapsed')
    expect(localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('false')
  })

  it('retains the existing fixed-sidebar option even when storage contains a collapsed preference', async () => {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, 'true')
    const wrapper = await mountLayout({ collapsibleSidebar: false })
    expect(wrapper.find('.console-sidebar-toggle').exists()).toBe(false)
    expect(wrapper.classes()).not.toContain('sidebar-collapsed')
    expect(wrapper.find('.console-sidebar').exists()).toBe(true)
    expect(localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('true')
  })

  it('opts the shared workspaces into expandable navigation without changing their content', async () => {
    const wrapper = await mountLayout({
      workspaceNavigation: true,
      immersive: true,
      defaultSidebarCollapsed: true,
    })
    expect(wrapper.classes()).toContain('workspace-navigation')
    expect(wrapper.classes()).toContain('sidebar-collapsed')
    expect(wrapper.find('.console-topbar').exists()).toBe(false)
    expect(wrapper.findAll('.console-nav-label').map((item) => item.text())).toEqual([
      '系统总览', '协同态势', '光电视觉', '雷达态势', '设备管理',
    ])
    expect(wrapper.get('.console-sidebar-user').text()).toContain('admin')
    expect(wrapper.get('.console-sidebar-footer button').attributes('aria-label')).toBe('退出登录')

    const toggle = wrapper.get('.console-sidebar-toggle')
    expect(toggle.attributes('aria-label')).toBe('展开菜单')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(toggle.attributes('aria-controls')).toBe(wrapper.get('.console-navigation').attributes('id'))

    await toggle.trigger('click')
    expect(wrapper.classes()).not.toContain('sidebar-collapsed')
    expect(toggle.attributes('aria-label')).toBe('折叠菜单')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[data-test="content"]').text()).toBe('页面内容')
  })

  it('uses one saved expansion preference across workspace switches and later mounts', async () => {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, 'false')
    const wrapper = await mountLayout({ workspaceNavigation: true, defaultSidebarCollapsed: true })
    expect(wrapper.classes()).not.toContain('sidebar-collapsed')

    await wrapper.setProps({ title: '算法仿真', eyebrow: '独立仿真', immersive: true })
    expect(wrapper.classes()).not.toContain('sidebar-collapsed')
    expect(localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('false')

    await wrapper.get('.console-sidebar-toggle').trigger('click')
    expect(localStorage.getItem(SIDEBAR_STORAGE_KEY)).toBe('true')
    wrapper.unmount()

    const restored = await mountLayout({ workspaceNavigation: true, defaultSidebarCollapsed: false })
    expect(restored.classes()).toContain('sidebar-collapsed')
    expect(restored.get('.console-sidebar-toggle').attributes('aria-expanded')).toBe('false')
  })

  it('continues to expand navigation when browser storage is unavailable', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('storage unavailable') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage unavailable') })
    const wrapper = await mountLayout({ workspaceNavigation: true, defaultSidebarCollapsed: true })
    expect(wrapper.classes()).toContain('sidebar-collapsed')
    await wrapper.get('.console-sidebar-toggle').trigger('click')
    expect(wrapper.classes()).not.toContain('sidebar-collapsed')
  })

  it('pairs a burst of navigation changes with a single viewport transition', async () => {
    vi.useFakeTimers()
    const dispatch = vi.spyOn(window, 'dispatchEvent')
    const wrapper = await mountLayout({ workspaceNavigation: true })
    await wrapper.get('.console-sidebar-toggle').trigger('click')
    await wrapper.get('.console-sidebar-toggle').trigger('click')

    const events = () => dispatch.mock.calls.map(([event]) => event.type)
      .filter((type) => type.startsWith('uav-usv:viewport-transition-'))
    expect(events()).toEqual(['uav-usv:viewport-transition-start'])
    await vi.advanceTimersByTimeAsync(220)
    expect(events()).toEqual(['uav-usv:viewport-transition-start', 'uav-usv:viewport-transition-end'])
    wrapper.unmount()
    expect(events()).toHaveLength(2)
  })

  it('ends an active viewport transition and cancels its timer when unmounted', async () => {
    vi.useFakeTimers()
    const dispatch = vi.spyOn(window, 'dispatchEvent')
    const wrapper = await mountLayout({ workspaceNavigation: true })
    await wrapper.get('.console-sidebar-toggle').trigger('click')
    wrapper.unmount()
    await vi.runAllTimersAsync()

    expect(dispatch.mock.calls.map(([event]) => event.type)
      .filter((type) => type.startsWith('uav-usv:viewport-transition-'))).toEqual([
      'uav-usv:viewport-transition-start', 'uav-usv:viewport-transition-end',
    ])
  })

  it('does not schedule a transition timer if unmounted before the next render', async () => {
    vi.useFakeTimers()
    const dispatch = vi.spyOn(window, 'dispatchEvent')
    const wrapper = await mountLayout({ workspaceNavigation: true })
    const toggling = wrapper.get('.console-sidebar-toggle').trigger('click')
    wrapper.unmount()
    await toggling
    await vi.runAllTimersAsync()

    expect(dispatch.mock.calls.map(([event]) => event.type)
      .filter((type) => type.startsWith('uav-usv:viewport-transition-'))).toEqual([
      'uav-usv:viewport-transition-start', 'uav-usv:viewport-transition-end',
    ])
  })
})

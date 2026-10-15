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
})

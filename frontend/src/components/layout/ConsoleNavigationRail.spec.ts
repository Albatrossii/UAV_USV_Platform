import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import ConsoleNavigationRail from '@/components/layout/ConsoleNavigationRail.vue'
import { useAuthStore } from '@/stores/auth'
import { useMonitoringStore } from '@/stores/monitoring'

const navigation = [
  ['/', '系统总览'],
  ['/situation', '协同态势'],
  ['/vision', '光电视觉'],
  ['/radar', '雷达态势'],
  ['/devices', '设备管理'],
] as const

async function mountRail(collapsed = false) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      ...navigation.map(([path]) => ({ path, component: { template: '<div />' } })),
      { path: '/login', name: 'login', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  await router.isReady()
  const auth = useAuthStore()
  const monitoring = useMonitoringStore()
  const wrapper = mount(ConsoleNavigationRail, { props: { collapsed }, global: { plugins: [pinia, router] } })
  return { wrapper, router, auth, monitoring }
}

describe('ConsoleNavigationRail', () => {
  it('emits toggle without changing layout state or the global sidebar preference', async () => {
    localStorage.setItem('uav-usv:sidebar-collapsed', 'true')
    const { wrapper } = await mountRail()
    const toggle = wrapper.get('.rail-toggle')
    expect(toggle.attributes('aria-label')).toBe('收起总控')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    await toggle.trigger('click')
    expect(wrapper.emitted('toggle')).toEqual([[]])
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(localStorage.getItem('uav-usv:sidebar-collapsed')).toBe('true')

    await wrapper.setProps({ collapsed: true })
    expect(toggle.attributes('aria-label')).toBe('展开总控')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    await toggle.trigger('click')
    expect(wrapper.emitted('toggle')).toEqual([[], []])
  })

  it('keeps every existing destination accessible by title and navigates with RouterLink', async () => {
    const { wrapper, router } = await mountRail()
    const links = wrapper.findAll('nav a')
    expect(links).toHaveLength(navigation.length)
    for (const [index, [path, title]] of navigation.entries()) {
      expect(links[index]!.attributes('href')).toBe(path)
      expect(links[index]!.attributes('title')).toBe(title)
      expect(links[index]!.attributes('aria-label')).toBe(title)
    }
    expect(wrapper.get('a[href="/"]').classes()).toContain('active')
    await wrapper.get('a[href="/radar"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/radar')
    expect(wrapper.get('a[href="/radar"]').classes()).toContain('active')
    expect(wrapper.get('a[href="/"]').classes()).not.toContain('active')
  })

  it('disconnects monitoring before logout and waits for logout before leaving the page', async () => {
    const { wrapper, router, auth, monitoring } = await mountRail()
    const calls: string[] = []
    let finishLogout!: () => void
    vi.spyOn(monitoring, 'disconnectEvents').mockImplementation(() => { calls.push('disconnect') })
    vi.spyOn(auth, 'logout').mockImplementation(() => {
      calls.push('logout')
      return new Promise<void>((resolve) => { finishLogout = resolve })
    })
    const replace = vi.spyOn(router, 'replace')

    await wrapper.get('button[aria-label="退出登录"]').trigger('click')
    expect(calls).toEqual(['disconnect', 'logout'])
    expect(replace).not.toHaveBeenCalled()
    finishLogout()
    await flushPromises()
    expect(replace).toHaveBeenCalledWith({ name: 'login' })
    expect(router.currentRoute.value.name).toBe('login')
  })

  it('shows the current user and disables logout during an auth request', async () => {
    const { wrapper, auth } = await mountRail()
    auth.user = { username: 'operator', role: 'ADMIN' } as NonNullable<typeof auth.user>
    auth.loading = true
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.rail-user').attributes('title')).toBe('operator · ADMIN')
    expect(wrapper.get('.rail-user').attributes('aria-label')).toBe('当前用户：operator')
    expect(wrapper.get('button[aria-label="退出登录"]').attributes('disabled')).toBeDefined()
  })
})

import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SimulationUnityWebglPanel from './SimulationUnityWebglPanel.vue'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it('shows only trusted finite loader progress and does not declare online at 100%', async () => {
  const wrapper = mount(SimulationUnityWebglPanel)
  try {
    await wrapper.get('iframe').trigger('load')
    const frame = wrapper.get('iframe').element as HTMLIFrameElement
    const progress = async (value: number, origin = window.location.origin) => {
      window.dispatchEvent(new MessageEvent('message', { source: frame.contentWindow, origin,
        data: { type: 'unityProgress', payload: { progress: value } } }))
      await wrapper.vm.$nextTick()
    }
    await progress(0.5)
    expect(wrapper.get('progress').attributes('value')).toBe('50')
    await progress(0.9, 'https://untrusted.invalid')
    await progress(99)
    expect(wrapper.get('progress').attributes('value')).toBe('50')
    await progress(1)
    expect(wrapper.text()).toContain('正在等待 Unity 控制桥就绪')
    expect(wrapper.emitted('unityReady')).toBeUndefined()
  } finally { wrapper.unmount() }
})

it('offers an explicit slow-load retry without dispatching device commands', async () => {
  const wrapper = mount(SimulationUnityWebglPanel)
  try {
    await wrapper.get('iframe').trigger('load')
    const before = wrapper.get('iframe').attributes('src')
    await vi.advanceTimersByTimeAsync(121000)
    expect(wrapper.text()).toContain('不会重置仿真任务')
    await wrapper.get('button').trigger('click')
    expect(wrapper.get('iframe').attributes('src')).not.toBe(before)
    expect(wrapper.get('progress').attributes('value')).toBe('0')
    expect(wrapper.emitted('unityCommand')).toBeUndefined()
  } finally { wrapper.unmount() }
})

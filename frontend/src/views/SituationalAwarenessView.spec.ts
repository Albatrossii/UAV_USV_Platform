import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { controlSan60, fetchSan60State, ingestSan60 } from '@/api/san60'
import SituationalAwarenessView from './SituationalAwarenessView.vue'
import type { San60State } from '@/types/san60'

vi.mock('@/api/san60', () => ({ fetchSan60State: vi.fn(), controlSan60: vi.fn(), ingestSan60: vi.fn() }))
const state = {
  mode: 'idle', message: '已停止', frames: 5, rate_hz: 1, arrival_age_s: 5,
  band: [2460, 2464], latest: null, spatial: { track: [], cells: [], field: [], span: [0, 0, 0], voxel_size: 2 },
  gnss: null, logs: [], capabilities: { default_bag: 'D:/data/record.bag', usb: false },
} as unknown as San60State
const online = { connected: true, state, serviceUrl: 'http://127.0.0.1:8775', message: '已连接' }
const wrappers: ReturnType<typeof mount>[] = []
async function page() {
  const wrapper = mount(SituationalAwarenessView, { global: { stubs: {
    ConsoleLayout: { template: '<div><slot /></div>' }, San60Plots: true,
  } } })
  wrappers.push(wrapper); await flushPromises(); return wrapper
}
beforeEach(() => {
  vi.useFakeTimers()
  vi.mocked(fetchSan60State).mockResolvedValue(online)
  vi.mocked(controlSan60).mockResolvedValue({ ok: true })
  vi.mocked(ingestSan60).mockResolvedValue({ ok: true, accepted: 1 })
})
afterEach(() => { for (const wrapper of wrappers.splice(0)) wrapper.unmount(); vi.useRealTimers(); vi.resetAllMocks() })
describe('situational awareness controls', () => {
  it('applies centimetre settings as metres even while acquisition is stopped', async () => {
    const wrapper = await page()
    await wrapper.get('#san-voxel').setValue('1')
    await wrapper.findAll('button').find(button => button.text() === '应用设置')!.trigger('click'); await flushPromises()
    expect(controlSan60).toHaveBeenCalledWith({ action: 'settings', band: [2460, 2464], voxel: .01 })
    expect(wrapper.text()).toContain('设置已应用')
  })
  it('rejects invalid voxel and band values before sending a command', async () => {
    const wrapper = await page(), apply = wrapper.findAll('button').find(button => button.text() === '应用设置')!
    await wrapper.get('#san-voxel').setValue('0'); await apply.trigger('click')
    expect(wrapper.get('[role="alert"]').text()).toContain('1–2000')
    await wrapper.get('#san-voxel').setValue('200'); await wrapper.get('#san-high').setValue('2400'); await apply.trigger('click')
    expect(wrapper.get('[role="alert"]').text()).toContain('上限须大于下限')
    expect(controlSan60).not.toHaveBeenCalled()
  })
  it('retains the last snapshot during disconnection and recovers without overwriting edits', async () => {
    const wrapper = await page()
    await wrapper.get('#san-voxel').setValue('50')
    vi.mocked(fetchSan60State).mockResolvedValueOnce({ ...online, connected: false, state: null, message: '服务断开' })
    await vi.advanceTimersByTimeAsync(750); await flushPromises()
    expect(wrapper.text()).toContain('当前保留上次测量画面')
    expect(wrapper.getComponent({ name: 'San60Plots' }).props('state')).toEqual(state)
    await vi.advanceTimersByTimeAsync(2500); await flushPromises()
    expect(wrapper.text()).not.toContain('当前保留上次测量画面')
    expect((wrapper.get('#san-voxel').element as HTMLInputElement).value).toBe('50')
  })
  it('stops replay before forwarding external JSON and surfaces engine errors', async () => {
    const wrapper = await page()
    await wrapper.get('#san-json').setValue('{"type":"gnss","valid":false}')
    await wrapper.findAll('button').find(button => button.text() === '发送 JSON')!.trigger('click'); await flushPromises()
    expect(controlSan60).toHaveBeenCalledWith({ action: 'stop' })
    expect(ingestSan60).toHaveBeenCalledWith({ type: 'gnss', valid: false })
    expect(vi.mocked(controlSan60).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(ingestSan60).mock.invocationCallOrder[0]!)
    vi.mocked(controlSan60).mockRejectedValueOnce(new Error('BAG 路径无效'))
    await wrapper.findAll('button').find(button => button.text() === '开始回放')!.trigger('click'); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('BAG 路径无效')
  })
  it('aborts pending fetch and stops polling when leaving the page', async () => {
    const wrapper = await page()
    const signal = vi.mocked(fetchSan60State).mock.calls[0]![0]!
    wrapper.unmount()
    expect(signal.aborted).toBe(true)
    const count = vi.mocked(fetchSan60State).mock.calls.length
    await vi.advanceTimersByTimeAsync(5000)
    expect(fetchSan60State).toHaveBeenCalledTimes(count)
  })
})

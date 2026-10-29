import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'

import CompactNodeViewBar from '@/components/monitoring/CompactNodeViewBar.vue'
import type { NodeCommunicationRow } from '@/utils/nodeCommunication'

function row(code: string, state: NodeCommunicationRow['state'] = 'unknown'): NodeCommunicationRow {
  return {
    code, type: code.startsWith('UAV') ? 'UAV' : 'USV', name: code, state,
    stateLabel: { online: '在线', delayed: '心跳过期', offline: '离线', unknown: '未知' }[state],
    qualityLabel: '待检测', bars: 0, qualityPercent: null, heartbeatAgeSeconds: null,
    source: 'ROS_GATEWAY_V1', endpoint: '192.0.2.1:8765', reason: '测试详情',
  }
}
const rows = ['UAV-001', 'UAV-002', 'UAV-003', 'USV-001', 'USV-002', 'USV-003'].map(code => row(code))
let panelWidth = 1000
let measuredHeading = 26
let resizeCallback: ResizeObserverCallback
const disconnect = vi.fn()
const observe = vi.fn()
const wrappers: VueWrapper[] = []

async function setup(availableHeight = 1000, fixedSizing = false) {
  const wrapper = mount(CompactNodeViewBar, { props: {
    rows, selectedDevice: 'uav-01', followDevice: '', controlsReady: true, availableHeight, fixedSizing,
  } })
  wrappers.push(wrapper)
  await nextTick()
  return wrapper
}
async function measure() {
  resizeCallback([], {} as ResizeObserver)
  await vi.advanceTimersByTimeAsync(16)
  await nextTick()
}
function styleNumber(wrapper: VueWrapper, property: string) {
  return Number((wrapper.element as HTMLElement).style.getPropertyValue(property).replace('px', ''))
}
function grip(wrapper: VueWrapper) { return wrapper.get('[role="separator"]') }
function height(wrapper: VueWrapper) { return Number(grip(wrapper).attributes('aria-valuenow')) }
async function pointer(element: Element, type: string, pointerId: number, clientY = 0) {
  const event = new MouseEvent(type, { bubbles: true, cancelable: true, button: 0, clientY })
  Object.defineProperty(event, 'pointerId', { value: pointerId })
  element.dispatchEvent(event)
  await nextTick()
}

describe('CompactNodeViewBar', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    panelWidth = 1000
    measuredHeading = 26
    vi.clearAllMocks()
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: ResizeObserverCallback) { resizeCallback = callback }
      observe = observe
      disconnect = disconnect
    })
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0))
    vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id))
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
      return this.classList.contains('cnv-bar') ? panelWidth : 0
    })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      return { x: 0, y: 0, top: 0, left: 0, bottom: measuredHeading, right: panelWidth,
        width: panelWidth, height: this.classList.contains('cnv-heading') ? measuredHeading : 0,
        toJSON: () => ({}),
      } as DOMRect
    })
  })
  afterEach(() => {
    wrappers.splice(0).forEach(wrapper => wrapper.unmount())
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('shows compact identity, state and type while allowing a view change for unknown nodes', async () => {
    const wrapper = await setup()
    expect(wrapper.findAll('.cnv-card')).toHaveLength(6)
    expect(wrapper.get('.cnv-view-status').text()).toBe('点击卡片切换视角')
    const card = wrapper.get('[data-node="UAV-001"]')
    expect(card.text()).toContain('未知')
    expect(card.text()).toContain('无人机')
    expect(card.attributes('data-selected')).toBe('true')
    expect(card.attributes('aria-pressed')).toBe('false')
    expect(card.attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).not.toMatch(/192\.0\.2|测试详情|待检测|RTT/)
    await card.trigger('click')
    expect(wrapper.emitted('select')).toEqual([['UAV-001']])
    await wrapper.setProps({ followDevice: 'uav-01' })
    expect(card.attributes('aria-pressed')).toBe('true')
    expect(wrapper.get('.cnv-view-status').text()).toBe('跟随 uav-01')
    await wrapper.setProps({ controlsReady: false })
    expect(wrapper.get('.cnv-view-status').text()).toBe('等待场景就绪')
    expect(card.attributes('disabled')).toBeDefined()
    await card.trigger('click')
    expect(wrapper.emitted('select')).toHaveLength(1)
  })

  it('reflows at the content-width thresholds and grows for a wrapped heading', async () => {
    const wrapper = await setup(2000)
    expect(styleNumber(wrapper, '--node-columns')).toBe(6)
    expect(height(wrapper)).toBe(72)
    panelWidth = 900 // 880px content width after padding
    await measure()
    expect(styleNumber(wrapper, '--node-columns')).toBe(6)
    panelWidth = 899
    await measure()
    expect(styleNumber(wrapper, '--node-columns')).toBe(3)
    expect(styleNumber(wrapper, '--node-rows')).toBe(2)
    panelWidth = 350
    await measure()
    expect(styleNumber(wrapper, '--node-columns')).toBe(3)
    panelWidth = 349
    measuredHeading = 59
    await measure()
    expect(styleNumber(wrapper, '--node-columns')).toBe(2)
    expect(styleNumber(wrapper, '--node-rows')).toBe(3)
    expect(styleNumber(wrapper, 'height')).toBe(59 + 30 + 12 + 3 * 72)
    const resizeEvents = wrapper.emitted('resize') ?? []
    expect(resizeEvents[resizeEvents.length - 1]).toEqual([317])
    await wrapper.setProps({ rows: rows.slice(0, 1) })
    expect(styleNumber(wrapper, '--node-columns')).toBe(1)
    expect(styleNumber(wrapper, '--node-rows')).toBe(1)
  })

  it('caps resizing against stage space, clamps after a viewport change, and resets to automatic', async () => {
    panelWidth = 600
    const wrapper = await setup(800)
    const slider = wrapper.get('input[type="range"]')
    expect(slider.attributes('max')).toBe('105')
    await slider.setValue('105')
    expect(height(wrapper)).toBe(105)
    expect(styleNumber(wrapper, 'height')).toBeLessThanOrEqual(800 * 0.34)
    expect(wrapper.get('.cnv-auto').attributes('aria-pressed')).toBe('false')
    await wrapper.setProps({ availableHeight: 600 })
    expect(height(wrapper)).toBe(71)
    expect(600 - styleNumber(wrapper, 'height')).toBeGreaterThanOrEqual(390)
    await wrapper.setProps({ availableHeight: 2000 })
    expect(height(wrapper)).toBe(105)
    await wrapper.get('.cnv-auto').trigger('click')
    expect(height(wrapper)).toBe(72)
    expect(wrapper.get('.cnv-auto').attributes('aria-pressed')).toBe('true')
    await wrapper.setProps({ availableHeight: 400 })
    expect(height(wrapper)).toBe(52)
    expect(slider.attributes('max')).toBe('52')
  })

  it('supports keyboard adjustment and both reset shortcuts on the separator', async () => {
    const wrapper = await setup(2000)
    await grip(wrapper).trigger('keydown', { key: 'ArrowUp' })
    expect(height(wrapper)).toBe(76)
    await grip(wrapper).trigger('keydown', { key: 'ArrowDown' })
    expect(height(wrapper)).toBe(72)
    await grip(wrapper).trigger('keydown', { key: 'End' })
    expect(height(wrapper)).toBe(120)
    await grip(wrapper).trigger('keydown', { key: 'Home' })
    expect(height(wrapper)).toBe(52)
    await grip(wrapper).trigger('keydown', { key: 'Enter' })
    expect(height(wrapper)).toBe(72)
    expect(wrapper.get('.cnv-auto').attributes('aria-pressed')).toBe('true')
    await grip(wrapper).trigger('keydown', { key: 'ArrowUp' })
    await grip(wrapper).trigger('dblclick')
    expect(height(wrapper)).toBe(72)
    expect(grip(wrapper).attributes('aria-valuetext')).toContain('自动')
  })

  it('hides manual sizing and remains automatic in a fixed shared shell', async () => {
    const wrapper = await setup(2000, true)
    expect(wrapper.find('.cnv-sizing').exists()).toBe(false)
    expect(wrapper.find('[role="separator"]').exists()).toBe(false)
    expect(styleNumber(wrapper, '--node-card-height')).toBe(72)
    await wrapper.setProps({ fixedSizing: false })
    expect(wrapper.find('.cnv-sizing').exists()).toBe(true)
    await wrapper.get('input[type="range"]').setValue('100')
    expect(styleNumber(wrapper, '--node-card-height')).toBe(100)
    await wrapper.setProps({ fixedSizing: true })
    expect(wrapper.find('.cnv-sizing').exists()).toBe(false)
    expect(styleNumber(wrapper, '--node-card-height')).toBe(72)
  })

  it('resizes all rows with pointer capture and releases listeners and capture on unmount', async () => {
    panelWidth = 600
    const wrapper = await setup(2000)
    const separator = grip(wrapper)
    const element = separator.element as HTMLElement
    const setCapture = vi.fn()
    const releaseCapture = vi.fn()
    element.setPointerCapture = setCapture
    element.hasPointerCapture = vi.fn(() => true)
    element.releasePointerCapture = releaseCapture
    await pointer(element, 'pointerdown', 7, 300)
    expect(setCapture).toHaveBeenCalledWith(7)
    expect(wrapper.emitted('resize-start')).toHaveLength(1)
    await pointer(element, 'pointermove', 8, 100)
    expect(height(wrapper)).toBe(72)
    await pointer(element, 'pointermove', 7, 260)
    expect(height(wrapper)).toBe(92) // 40px shared between the two rows
    await pointer(element, 'pointercancel', 7)
    expect(releaseCapture).toHaveBeenCalledWith(7)
    expect(wrapper.emitted('resize-end')).toHaveLength(1)
    expect(wrapper.classes()).not.toContain('resizing')
    await pointer(element, 'pointerdown', 9, 300)
    resizeCallback([], {} as ResizeObserver)
    const removeListener = vi.spyOn(window, 'removeEventListener')
    const cancelFrame = vi.spyOn(globalThis, 'cancelAnimationFrame')
    wrapper.unmount()
    wrappers.splice(wrappers.indexOf(wrapper), 1)
    expect(releaseCapture).toHaveBeenCalledWith(9)
    expect(disconnect).toHaveBeenCalledOnce()
    expect(cancelFrame).toHaveBeenCalledOnce()
    expect(removeListener).toHaveBeenCalledWith('resize', expect.any(Function))
    expect(removeListener).toHaveBeenCalledWith('blur', expect.any(Function))
  })

  it('handles an empty fleet without invalid grid dimensions', async () => {
    const wrapper = await setup()
    await wrapper.setProps({ rows: [] })
    expect(wrapper.findAll('.cnv-card')).toHaveLength(0)
    expect(wrapper.text()).toContain('生成场景后可选择节点视角')
    expect(styleNumber(wrapper, '--node-columns')).toBe(1)
    expect(Number.isFinite(styleNumber(wrapper, 'height'))).toBe(true)
  })
})

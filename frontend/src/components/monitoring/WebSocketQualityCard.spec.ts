import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'

import WebSocketQualityCard from '@/components/monitoring/WebSocketQualityCard.vue'
import { useRealtimeStore } from '@/stores/realtime'

const NOW = 1_800_000_000_000

function setup(rtts: Array<number | null> = []) {
  const store = useRealtimeStore()
  store.connectionState = 'CONNECTED'
  store.linkQuality.now = NOW
  store.linkQuality.samples = rtts.map((rttMs, index) => ({
    sentAt: NOW - (rtts.length - index) * 2000, rttMs,
  }))
  return { store, wrapper: mount(WebSocketQualityCard) }
}

describe('WebSocketQualityCard', () => {
  beforeEach(() => { setActivePinia(createPinia()) })

  it('keeps the approved primary fields visible and diagnostics collapsed initially', () => {
    const { wrapper } = setup([24, 26, 23, 24, 22])
    expect(wrapper.get('.wsq-head').text()).toContain('通信状态')
    expect(wrapper.get('.wsq-protocol').text()).toBe('WebSocket')
    expect(wrapper.get('[data-test="rtt"]').text()).toBe('24ms')
    expect(wrapper.get('.wsq-grade').text()).toBe('优秀')
    expect(wrapper.get('.wsq-chart svg').attributes('aria-label')).toContain('往返延迟')
    expect((wrapper.get('.wsq-details').element as HTMLDetailsElement).open).toBe(false)
    expect(wrapper.get('.wsq-details > summary').text()).toContain('展开详细指标')
    expect(wrapper.get('.wsq-details .wsq-stats').text()).toContain('重连尝试')
  })

  it('supports accessible expand/collapse and preserves the choice across samples', async () => {
    const { store, wrapper } = setup([150, 185])
    const details = wrapper.get('.wsq-details')
    ;(details.element as HTMLDetailsElement).open = true
    await details.trigger('toggle')
    expect(details.get('summary').text()).toContain('收起详细指标')
    store.linkQuality.samples.push({ sentAt: NOW, rttMs: 190 })
    await nextTick()
    expect((details.element as HTMLDetailsElement).open).toBe(true)
    ;(details.element as HTMLDetailsElement).open = false
    await details.trigger('toggle')
    expect(details.get('summary').text()).toContain('展开详细指标')
  })

  it.each([[100, '优秀', 'excellent'], [100.1, '良好', 'good'], [300, '良好', 'good'], [300.1, '差', 'poor']] as const)(
    'shows correct grade for %s ms', (rtt, label, grade) => {
      const { wrapper } = setup([rtt])
      expect(wrapper.get('.wsq-grade').text()).toBe(label)
      expect(wrapper.attributes('data-grade')).toBe(grade)
      expect(wrapper.get('[data-test="rtt"]').text()).toBe(`${rtt}ms`)
    },
  )

  it('never displays a mock zero or excellent grade before measurement', () => {
    const { wrapper } = setup()
    expect(wrapper.get('.wsq-grade').text()).toBe('待检测')
    expect(wrapper.get('[data-test="rtt"]').text()).toBe('—')
    expect(wrapper.get('.wsq-stats').text()).toContain('暂无探测样本')
    expect(wrapper.findAll('.wsq-history .empty')).toHaveLength(30)
    expect(wrapper.get('.wsq-trend').attributes('d')).toBe('')
  })

  it('tolerates an old store shape retained by development hot updates', () => {
    const store = useRealtimeStore()
    store.connectionState = 'CONNECTED'
    Reflect.deleteProperty(store.$state, 'linkQuality')
    const wrapper = mount(WebSocketQualityCard)
    expect(wrapper.get('.wsq-grade').text()).toBe('待检测')
    expect(wrapper.get('[data-test="rtt"]').text()).toBe('—')
  })

  it('prioritizes offline state over old successful RTT, without hiding historical samples', async () => {
    const { wrapper, store } = setup([24, 25])
    store.connectionState = 'CONNECTING'
    store.linkQuality.reconnectCount = 2
    await nextTick()
    expect(wrapper.get('.wsq-connected').text()).toBe('重连中')
    expect(wrapper.get('.wsq-grade').text()).toBe('无连接')
    expect(wrapper.get('[data-test="rtt"]').text()).toBe('—')
    expect(wrapper.get('.wsq-chart').text()).toContain('历史样本')
    expect(wrapper.get('.wsq-trend').attributes('d')).not.toBe('')
  })

  it('shows consecutive probe timeouts as poor, without claiming that the WS disconnected', () => {
    const { wrapper } = setup([24, null, null, null])
    expect(wrapper.get('.wsq-connected').text()).toBe('已连接')
    expect(wrapper.get('.wsq-grade').text()).toBe('差')
    expect(wrapper.get('[data-test="rtt"]').text()).toBe('—')
    expect(wrapper.findAll('.wsq-history .timeout')).toHaveLength(3)
    expect(wrapper.get('.wsq-stats').text()).toContain('75%')
  })

  it('ages recent messages locally and expires old chart samples', async () => {
    const { wrapper, store } = setup([24])
    store.linkQuality.lastMessageAt = NOW - 2500
    await nextTick()
    expect(wrapper.get('.wsq-age').text()).toBe('2.5 秒前')
    store.linkQuality.now = NOW + 61_000
    await nextTick()
    expect(wrapper.get('.wsq-grade').text()).toBe('待检测')
    expect(wrapper.get('.wsq-trend').attributes('d')).toBe('')
    expect(wrapper.get('.wsq-age').text()).toBe('1 分钟前')
  })

  it('does not open its own socket or issue any mission command when mounted', () => {
    const store = useRealtimeStore()
    const connect = vi.spyOn(store, 'connect')
    const wrapper = mount(WebSocketQualityCard)
    expect(connect).not.toHaveBeenCalled()
    expect(wrapper.findAll('button')).toHaveLength(0)
    expect(wrapper.text()).not.toMatch(/示例数据|模拟样本|4G|5G|语义通讯/)
    expect(wrapper.get('.wsq-scope').text()).toContain('浏览器')
  })
})

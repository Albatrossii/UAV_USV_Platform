import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

import { fetchRuntimeNodes } from '@/api/monitoring'
import NodeCommunicationPanel from '@/components/monitoring/NodeCommunicationPanel.vue'
import type { RuntimeNode } from '@/types/monitoring'

vi.mock('@/api/monitoring', () => ({ fetchRuntimeNodes: vi.fn() }))
const NOW = Date.parse('2026-10-15T10:00:00Z')

function node(overrides: Partial<RuntimeNode> = {}): RuntimeNode {
  return {
    id: 1, code: 'uav-01', name: '测试无人机', type: 'UAV', status: 'ONLINE',
    host: '192.0.2.1', port: 8765, endpoint: '192.0.2.1:8765', rosNamespace: '/uav_01',
    lastHeartbeatAt: '2026-10-15T10:00:00', heartbeatAgeSeconds: 0,
    source: 'ROS_GATEWAY_V1', instanceId: 'gateway-1', positionX: null, positionY: null,
    positionZ: null, telemetryStale: false, linkQualityPercent: 100, detail: null, ...overrides,
  }
}

async function setup(nodes: RuntimeNode[] = [], defaultOpen = true) {
  vi.mocked(fetchRuntimeNodes).mockResolvedValue(nodes)
  const wrapper = mount(NodeCommunicationPanel, { props: {
    defaultOpen,
    deviceCodes: ['UAV-001', 'USV-001'], selectedDevice: 'uav-01',
    targets: [{ id: 'THREAT-001', label: '敌船 · 仿真目标', state: 'CAPTURED' }],
  } })
  await flushPromises()
  return wrapper
}

describe('NodeCommunicationPanel', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW)
    vi.clearAllMocks()
    vi.mocked(fetchRuntimeNodes).mockResolvedValue([])
  })
  afterEach(() => { vi.useRealTimers() })

  it('starts folded when no initial state is provided', async () => {
    const wrapper = mount(NodeCommunicationPanel, {
      props: { deviceCodes: ['UAV-001', 'USV-001'] },
    })
    await flushPromises()
    expect((wrapper.get('[data-test="nodes-panel"]').element as HTMLDetailsElement).open).toBe(false)
    wrapper.unmount()
  })

  it('merges targets into one node panel and removes the platform-channel display', async () => {
    const wrapper = await setup([node()])
    const panel = wrapper.get('[data-test="nodes-panel"]')
    expect((panel.element as HTMLDetailsElement).open).toBe(true)
    expect(panel.get('.ncp-head').text()).toContain('1 / 2 在线')
    expect(panel.get('[data-test="tracked-targets"]').text()).toContain('THREAT-001')
    expect(panel.get('[data-test="tracked-targets"]').text()).toContain('已捕获')
    expect(panel.get('[data-test="tracked-targets"]').text()).toContain('算法仿真 · 当前场景')
    expect(panel.get('[data-test="tracked-targets"]').text()).toContain('不计入通信节点')
    expect(wrapper.findAll('.ncp-panel')).toHaveLength(1)
    expect(wrapper.find('[data-test="platform-panel"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="targets-panel"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="websocket-quality-card"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('平台通道')
    expect(wrapper.text()).not.toContain('检测与分级说明')
    expect(wrapper.findAll('details')).toHaveLength(1)
  })

  it('shows unknown without invented measurements before nodes have connected', async () => {
    const wrapper = await setup()
    expect(wrapper.get('.ncp-head').text()).toContain('0 / 2 在线')
    expect(wrapper.findAll('[data-state="unknown"]')).toHaveLength(2)
    expect(wrapper.findAll('.ncp-bars .filled')).toHaveLength(0)
    expect(wrapper.findAll('[data-test="node-quality"]').every(value => value.text() === '—')).toBe(true)
    expect(wrapper.findAll('[data-test="heartbeat-age"]').every(value => value.text() === '尚无有效心跳')).toBe(true)
  })

  it('uses normalized device IDs and labels the derived bars as heartbeat freshness', async () => {
    const wrapper = await setup([node()])
    const row = wrapper.get('[data-node="UAV-001"]')
    expect(row.attributes('data-state')).toBe('online')
    expect(row.get('button').attributes('data-selected')).toBe('true')
    expect(row.findAll('.ncp-bars .filled')).toHaveLength(4)
    expect(row.get('.ncp-bars').attributes('aria-label')).toContain('心跳新鲜度')
    expect(row.get('.ncp-bars').attributes('aria-label')).toContain('非无线信号')
    expect(row.get('.ncp-detail').text()).toContain('节点 RTT—暂未采集')
    expect(row.get('.ncp-reason').text()).toContain('并非实测网络信号')
    expect(wrapper.text()).not.toMatch(/优秀|4G|5G|386/)
  })

  it('does not count Unity simulation heartbeat as a real device connection', async () => {
    const wrapper = await setup([node({ source: 'UNITY_HEARTBEAT' })])
    expect(wrapper.get('[data-node="UAV-001"]').attributes('data-state')).toBe('unknown')
    expect(wrapper.findAll('.ncp-bars .filled')).toHaveLength(0)
  })

  it('expands details and emits only a node selection', async () => {
    const wrapper = await setup([node()])
    const row = wrapper.get('[data-node="UAV-001"] button')
    expect(row.attributes('aria-expanded')).toBe('false')
    await row.trigger('click')
    expect(row.attributes('aria-expanded')).toBe('true')
    expect(wrapper.emitted('select')).toEqual([['UAV-001']])
    expect(wrapper.get('#node-detail-UAV-001').isVisible()).toBe(true)
    await row.trigger('click')
    expect(row.attributes('aria-expanded')).toBe('false')
    expect((wrapper.get('#node-detail-UAV-001').element as HTMLElement).style.display).toBe('none')
  })

  it('retains collapse state and expanded device across monitoring refreshes', async () => {
    const wrapper = await setup([node()])
    await wrapper.get('[data-node="UAV-001"] button').trigger('click')
    const panel = wrapper.get('[data-test="nodes-panel"]')
    ;(panel.element as HTMLDetailsElement).open = false
    await panel.trigger('toggle')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect((panel.element as HTMLDetailsElement).open).toBe(false)
    ;(panel.element as HTMLDetailsElement).open = true
    await panel.trigger('toggle')
    expect(wrapper.get('[data-node="UAV-001"] button').attributes('aria-expanded')).toBe('true')
  })

  it('can start folded without resetting a user expansion on monitoring refresh', async () => {
    const wrapper = await setup([node()], false)
    const panel = wrapper.get('[data-test="nodes-panel"]')
    expect((panel.element as HTMLDetailsElement).open).toBe(false)
    ;(panel.element as HTMLDetailsElement).open = true
    await panel.trigger('toggle')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect((panel.element as HTMLDetailsElement).open).toBe(true)
    expect(fetchRuntimeNodes).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('filters only confirmed anomalies, keeping targets separate from the online count', async () => {
    const wrapper = await setup([node({ status: 'OFFLINE', heartbeatAgeSeconds: 20 })])
    await wrapper.get('input[type="checkbox"]').setValue(true)
    expect(wrapper.findAll('.ncp-entry')).toHaveLength(1)
    expect(wrapper.get('.ncp-entry').attributes('data-node')).toBe('UAV-001')
    expect(wrapper.get('[data-test="tracked-targets"]').text()).toContain('THREAT-001')
    expect(wrapper.get('.ncp-head').text()).toContain('0 / 2 在线')
  })

  it('does not display stale online evidence after a monitoring failure', async () => {
    const wrapper = await setup([node()])
    vi.mocked(fetchRuntimeNodes).mockRejectedValueOnce(new Error('unavailable'))
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(wrapper.get('.ncp-notice').text()).toContain('不据此判断设备离线')
    expect(wrapper.get('[data-node="UAV-001"]').attributes('data-state')).toBe('unknown')
    expect(wrapper.findAll('.ncp-bars .filled')).toHaveLength(0)
  })

  it('updates the list after scene regeneration and clears removed device details', async () => {
    const wrapper = await setup([node()])
    await wrapper.get('[data-node="UAV-001"] button').trigger('click')
    await wrapper.setProps({ deviceCodes: ['USV-001'], targets: [] })
    expect(wrapper.findAll('.ncp-entry')).toHaveLength(1)
    expect(wrapper.find('[data-node="UAV-001"]').exists()).toBe(false)
    expect(wrapper.get('.ncp-head').text()).toContain('0 / 1 在线')
    expect(wrapper.get('[data-test="tracked-targets"]').text()).toContain('等待当前场景目标数据')
    expect(fetchRuntimeNodes).toHaveBeenCalledTimes(1)
  })
})

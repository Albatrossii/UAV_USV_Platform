import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, KeepAlive, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchRuntimeNodes } from '@/api/monitoring'
import {
  NODE_MONITOR_POLL_MS,
  NODE_MONITOR_SNAPSHOT_TTL_MS,
  useNodeMonitoring,
} from '@/composables/useNodeMonitoring'
import type { RuntimeNode } from '@/types/monitoring'
import { buildNodeCommunicationRows } from '@/utils/nodeCommunication'

vi.mock('@/api/monitoring', () => ({ fetchRuntimeNodes: vi.fn() }))

const NOW = Date.parse('2026-10-15T12:00:00Z')
const fetchNodes = vi.mocked(fetchRuntimeNodes)

function node(overrides: Partial<RuntimeNode> = {}): RuntimeNode {
  return {
    id: 1, code: 'uav-01', name: '无人机 1', type: 'UAV', status: 'ONLINE',
    host: '192.0.2.1', port: 8765, endpoint: '192.0.2.1:8765', rosNamespace: '/uav_01',
    lastHeartbeatAt: '2026-10-15T12:00:00', heartbeatAgeSeconds: 0,
    source: 'ROS_GATEWAY_V1', instanceId: 'ros-gateway-v1',
    positionX: null, positionY: null, positionZ: null,
    linkQualityPercent: 100, telemetryStale: false, detail: null,
    ...overrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function setup(keepAlive = false) {
  let monitoring!: ReturnType<typeof useNodeMonitoring>
  const visible = ref(true)
  const Probe = defineComponent({
    setup() {
      monitoring = useNodeMonitoring()
      return () => h('div')
    },
  })
  const wrapper = mount(defineComponent({
    setup: () => () => keepAlive
      ? h(KeepAlive, null, { default: () => visible.value ? h(Probe) : null })
      : h(Probe),
  }))
  return { wrapper, monitoring, visible }
}

describe('useNodeMonitoring', () => {
  let visibility: DocumentVisibilityState

  function changeVisibility(value: DocumentVisibilityState) {
    visibility = value
    document.dispatchEvent(new Event('visibilitychange'))
  }

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] })
    vi.setSystemTime(NOW)
    visibility = 'visible'
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility)
    fetchNodes.mockReset()
    fetchNodes.mockResolvedValue([node()])
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('loads on mount and exposes a usable snapshot only after the request succeeds', async () => {
    const request = deferred<RuntimeNode[]>()
    fetchNodes.mockReturnValueOnce(request.promise)
    const { monitoring } = setup()

    expect(fetchNodes).toHaveBeenCalledExactlyOnceWith()
    expect(monitoring.loading.value).toBe(true)
    expect(monitoring.available.value).toBe(false)
    expect(monitoring.observedAt.value).toBeNull()
    expect(monitoring.nodes.value).toEqual([])

    request.resolve([node()])
    await flushPromises()

    expect(monitoring.loading.value).toBe(false)
    expect(monitoring.available.value).toBe(true)
    expect(monitoring.observedAt.value).toBe(NOW)
    expect(monitoring.now.value).toBe(NOW)
    expect(monitoring.nodes.value).toEqual([node()])
    expect(monitoring.error.value).toBe('')
  })

  it('polls after five seconds and never overlaps a pending request', async () => {
    const request = deferred<RuntimeNode[]>()
    fetchNodes.mockResolvedValueOnce([node()]).mockReturnValueOnce(request.promise)
    const { monitoring } = setup()
    await flushPromises()

    await vi.advanceTimersByTimeAsync(NODE_MONITOR_POLL_MS - 1)
    expect(fetchNodes).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(fetchNodes).toHaveBeenCalledTimes(2)
    expect(monitoring.loading.value).toBe(true)

    await monitoring.refresh()
    changeVisibility('visible')
    await vi.advanceTimersByTimeAsync(NODE_MONITOR_POLL_MS * 2)
    expect(fetchNodes).toHaveBeenCalledTimes(2)

    request.resolve([node({ name: '更新后的节点' })])
    await flushPromises()
    expect(monitoring.loading.value).toBe(false)
    expect(monitoring.nodes.value[0]?.name).toBe('更新后的节点')

    await vi.advanceTimersByTimeAsync(NODE_MONITOR_POLL_MS - 1)
    expect(fetchNodes).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(fetchNodes).toHaveBeenCalledTimes(3)
  })

  it.each([
    { duration: 1000, age: 0, state: 'online', effectiveAge: 1 },
    { duration: 2000, age: 4, state: 'delayed', effectiveAge: 6 },
    { duration: 6000, age: 0, state: 'unknown', effectiveAge: 6 },
  ])('includes a $duration ms request in snapshot and heartbeat age', async ({ duration, age, state, effectiveAge }) => {
    const request = deferred<RuntimeNode[]>()
    fetchNodes.mockReturnValueOnce(request.promise)
    const { monitoring } = setup()
    await vi.advanceTimersByTimeAsync(duration)

    request.resolve([node({ heartbeatAgeSeconds: age })])
    await flushPromises()

    expect(monitoring.observedAt.value).toBe(NOW)
    expect(monitoring.now.value).toBe(NOW + duration)
    const rows = buildNodeCommunicationRows(
      ['uav-01'], monitoring.nodes.value, monitoring.now.value,
      monitoring.observedAt.value, monitoring.available.value,
    )
    expect(rows[0]).toMatchObject({ state, heartbeatAgeSeconds: effectiveAge })
    if (state !== 'online') expect(rows[0]).toMatchObject({ qualityPercent: null, bars: 0 })
  })

  it('recovers from a slow response only when a new timely snapshot arrives', async () => {
    const request = deferred<RuntimeNode[]>()
    fetchNodes.mockReturnValueOnce(request.promise)
    const { monitoring } = setup()
    const rows = () => buildNodeCommunicationRows(
      ['uav-01'], monitoring.nodes.value, monitoring.now.value,
      monitoring.observedAt.value, monitoring.available.value,
    )
    await vi.advanceTimersByTimeAsync(NODE_MONITOR_SNAPSHOT_TTL_MS + 1000)
    request.resolve([node()])
    await flushPromises()
    expect(monitoring.available.value).toBe(false)
    expect(rows()[0]?.state).toBe('unknown')

    await vi.advanceTimersByTimeAsync(NODE_MONITOR_POLL_MS)
    expect(fetchNodes).toHaveBeenCalledTimes(2)
    expect(monitoring.observedAt.value).toBe(Date.now())
    expect(monitoring.available.value).toBe(true)
    expect(rows()[0]?.state).toBe('online')
  })

  it('invalidates previous online evidence after a failure and recovers on the next success', async () => {
    const { monitoring } = setup()
    const rows = () => buildNodeCommunicationRows(
      ['uav-01'], monitoring.nodes.value, monitoring.now.value,
      monitoring.observedAt.value, monitoring.available.value,
    )
    await flushPromises()
    expect(rows()[0]?.state).toBe('online')

    fetchNodes.mockRejectedValueOnce(new Error('monitoring unavailable'))
    await monitoring.refresh()

    expect(monitoring.available.value).toBe(false)
    expect(monitoring.loading.value).toBe(false)
    expect(monitoring.error.value).toContain('节点监测暂不可用')
    expect(rows()[0]).toMatchObject({ state: 'unknown', qualityPercent: null, bars: 0 })

    await vi.advanceTimersByTimeAsync(NODE_MONITOR_POLL_MS)
    expect(fetchNodes).toHaveBeenCalledTimes(3)
    expect(monitoring.available.value).toBe(true)
    expect(monitoring.error.value).toBe('')
    expect(rows()[0]?.state).toBe('online')
  })

  it('clears polling and the visibility listener on unmount', async () => {
    const { wrapper, monitoring } = setup()
    await flushPromises()
    expect(vi.getTimerCount()).toBe(1)

    wrapper.unmount()
    expect(vi.getTimerCount()).toBe(0)
    expect(monitoring.available.value).toBe(false)
    changeVisibility('visible')
    await monitoring.refresh()
    await vi.advanceTimersByTimeAsync(NODE_MONITOR_POLL_MS * 3)
    expect(fetchNodes).toHaveBeenCalledTimes(1)
  })

  it.each(['success', 'failure'] as const)('ignores a late %s after unmount', async (outcome) => {
    const request = deferred<RuntimeNode[]>()
    fetchNodes.mockReturnValueOnce(request.promise)
    const { wrapper, monitoring } = setup()
    wrapper.unmount()

    if (outcome === 'success') request.resolve([node()])
    else request.reject(new Error('late failure'))
    await flushPromises()

    expect(monitoring.nodes.value).toEqual([])
    expect(monitoring.observedAt.value).toBeNull()
    expect(monitoring.available.value).toBe(false)
    expect(monitoring.loading.value).toBe(false)
    expect(monitoring.error.value).toBe('')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('pauses KeepAlive instances and restarts with one timer and one immediate refresh', async () => {
    const { wrapper, monitoring, visible } = setup(true)
    await flushPromises()
    expect(fetchNodes).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(1)

    for (let cycle = 0; cycle < 2; cycle += 1) {
      visible.value = false
      await nextTick()
      expect(monitoring.available.value).toBe(false)
      expect(vi.getTimerCount()).toBe(0)
      changeVisibility('visible')
      await vi.advanceTimersByTimeAsync(NODE_MONITOR_POLL_MS * 2)
      expect(fetchNodes).toHaveBeenCalledTimes(1 + cycle * 2)

      visible.value = true
      await nextTick()
      await flushPromises()
      expect(fetchNodes).toHaveBeenCalledTimes(2 + cycle * 2)
      expect(monitoring.available.value).toBe(true)
      expect(vi.getTimerCount()).toBe(1)

      await vi.advanceTimersByTimeAsync(NODE_MONITOR_POLL_MS)
      expect(fetchNodes).toHaveBeenCalledTimes(3 + cycle * 2)
    }

    wrapper.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each(['success', 'failure'] as const)(
    'ignores a previous activation\'s late %s without unlocking the current request',
    async (outcome) => {
      const previous = deferred<RuntimeNode[]>()
      const current = deferred<RuntimeNode[]>()
      fetchNodes.mockReturnValueOnce(previous.promise).mockReturnValueOnce(current.promise)
      const { monitoring, visible } = setup(true)

      visible.value = false
      await nextTick()
      visible.value = true
      await nextTick()
      expect(fetchNodes).toHaveBeenCalledTimes(2)

      if (outcome === 'success') previous.resolve([node({ name: '旧请求' })])
      else previous.reject(new Error('previous activation failed'))
      await flushPromises()

      expect(monitoring.nodes.value).toEqual([])
      expect(monitoring.observedAt.value).toBeNull()
      expect(monitoring.loading.value).toBe(true)
      expect(monitoring.error.value).toBe('')
      expect(monitoring.available.value).toBe(false)
      await monitoring.refresh()
      await vi.advanceTimersByTimeAsync(NODE_MONITOR_POLL_MS)
      expect(fetchNodes).toHaveBeenCalledTimes(2)

      current.resolve([node({ name: '当前请求' })])
      await flushPromises()
      expect(monitoring.nodes.value[0]?.name).toBe('当前请求')
      expect(monitoring.available.value).toBe(true)
      expect(monitoring.loading.value).toBe(false)
      expect(vi.getTimerCount()).toBe(1)
    },
  )

  it('suspends background polling, expires stale evidence and refreshes when visible', async () => {
    const { monitoring } = setup()
    await flushPromises()
    changeVisibility('hidden')

    await vi.advanceTimersByTimeAsync(NODE_MONITOR_SNAPSHOT_TTL_MS + 1_000)
    expect(fetchNodes).toHaveBeenCalledTimes(1)
    expect(monitoring.available.value).toBe(false)
    expect(monitoring.now.value).toBe(NOW + NODE_MONITOR_SNAPSHOT_TTL_MS + 1_000)

    const request = deferred<RuntimeNode[]>()
    fetchNodes.mockReturnValueOnce(request.promise)
    changeVisibility('visible')
    expect(fetchNodes).toHaveBeenCalledTimes(2)
    expect(monitoring.available.value).toBe(false)
    changeVisibility('visible')
    expect(fetchNodes).toHaveBeenCalledTimes(2)

    request.resolve([node()])
    await flushPromises()
    expect(monitoring.available.value).toBe(true)
    expect(monitoring.observedAt.value).toBe(Date.now())
  })
})

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useRealtimeStore } from '@/stores/realtime'
import { getWebSocketQuality } from '@/utils/websocketQuality'

const api = vi.hoisted(() => ({ fetchRealtimeSnapshot: vi.fn() }))
vi.mock('@/api/realtime', () => api)
vi.mock('@/api/runtimeControl', () => ({ fetchRuntimeCommandLogs: vi.fn().mockResolvedValue([]) }))

class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 3
  static instances: MockWebSocket[] = []
  readyState = MockWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((event: { data: unknown }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  sent: string[] = []

  constructor(readonly url: string) { MockWebSocket.instances.push(this) }
  send(data: string) { this.sent.push(data) }
  open() { this.readyState = MockWebSocket.OPEN; this.onopen?.() }
  close() { this.readyState = MockWebSocket.CLOSED; this.onclose?.() }
  receive(data: unknown) { this.onmessage?.({ data: typeof data === 'string' ? data : JSON.stringify(data) }) }
  latestPing(): { type: string; id: string } { return JSON.parse(this.sent.at(-1)!) }
  pong(id = this.latestPing().id) { this.receive({ type: 'realtime.pong', id }) }
}

const envelope = {
  version: 'v1', type: 'gateway.heartbeat', source: 'gateway', streamId: 'main',
  timestamp: '2000-01-01T00:00:00Z', sequence: 1, payload: {},
}
let store: ReturnType<typeof useRealtimeStore>
let visibility: DocumentVisibilityState

function openConnection() {
  store.connect()
  const ws = MockWebSocket.instances.at(-1)!
  ws.open()
  return ws
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-11T12:00:00Z'))
  visibility = 'visible'
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility)
  vi.stubGlobal('WebSocket', MockWebSocket)
  MockWebSocket.instances = []
  api.fetchRealtimeSnapshot.mockResolvedValue({ latestPoseBatch: null, latestTargetBatch: null, latestMissionStatus: null })
  setActivePinia(createPinia())
  store = useRealtimeStore()
})

afterEach(() => {
  store.disconnect()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('realtime WebSocket probe lifecycle', () => {
  it('uses the existing socket and measures actual monotonic round trips every two seconds', () => {
    const ws = openConnection()
    store.connect()
    expect(MockWebSocket.instances).toHaveLength(1)
    expect(ws.latestPing().type).toBe('realtime.ping')
    vi.advanceTimersByTime(45)
    ws.pong()
    expect(store.linkQuality.samples).toEqual([{ sentAt: Date.now() - 45, rttMs: 45 }])
    vi.advanceTimersByTime(1955)
    expect(ws.sent).toHaveLength(2)
    expect(store.lastEnvelope).toBeNull()
    expect(store.streamSequences).toEqual({})
    expect(store.lastError).toBe('')
  })

  it('does not use wall-clock adjustments as round-trip durations', () => {
    const ws = openConnection()
    vi.advanceTimersByTime(40)
    vi.setSystemTime(Date.now() + 200)
    ws.pong()
    expect(store.linkQuality.samples[0]?.rttMs).toBe(40)
  })

  it('times out once at 1500 ms, ignores late pongs, and never overlaps probes', () => {
    const ws = openConnection()
    const firstId = ws.latestPing().id
    vi.advanceTimersByTime(1499)
    expect(store.linkQuality.samples).toEqual([])
    expect(ws.sent).toHaveLength(1)
    vi.advanceTimersByTime(1)
    expect(store.linkQuality.samples[0]?.rttMs).toBeNull()
    ws.pong(firstId)
    vi.advanceTimersByTime(500)
    expect(ws.sent).toHaveLength(2)
    expect(ws.latestPing().id).not.toBe(firstId)
    ws.pong(firstId)
    expect(store.linkQuality.samples).toHaveLength(1)
    vi.advanceTimersByTime(3500)
    expect(store.linkQuality.samples).toHaveLength(3)
    expect(getWebSocketQuality(store.connectionState, store.linkQuality.samples, store.linkQuality.now))
      .toMatchObject({ grade: 'poor', rttMs: null })
  })

  it('updates message age only on current WebSocket frames, never HTTP snapshots', async () => {
    const ws = openConnection()
    await store.hydrateSnapshot()
    store.ingestSnapshot({ latestPoseBatch: envelope, latestTargetBatch: null, latestMissionStatus: null })
    expect(store.linkQuality.lastMessageAt).toBeNull()
    vi.advanceTimersByTime(70)
    ws.receive(envelope)
    const receivedAt = Date.now()
    expect(store.linkQuality.lastMessageAt).toBe(receivedAt)
    vi.advanceTimersByTime(680)
    expect(store.linkQuality.now - receivedAt).toBe(680)
    store.ingestSnapshot({ latestPoseBatch: { ...envelope, sequence: 2 }, latestTargetBatch: null, latestMissionStatus: null })
    expect(store.linkQuality.lastMessageAt).toBe(receivedAt)
    expect(store.lastEnvelope?.sequence).toBe(2)
  })

  it('filters all probe control packets before business sequence and error handling', () => {
    const ws = openConnection()
    ws.receive({ ...envelope, type: 'realtime.ping', id: 'unrelated' })
    ws.receive({ ...envelope, type: 'realtime.pong', id: 'unrelated' })
    ws.receive({ type: 'realtime.pong', id: 22 })
    store.ingestMessage({ ...envelope, type: 'realtime.pong' })
    expect(store.linkQuality.samples).toEqual([])
    expect(store.streamSequences).toEqual({})
    expect(store.lastEnvelope).toBeNull()
    expect(store.lastError).toBe('')
    ws.receive(envelope)
    expect(store.lastEnvelope?.type).toBe('gateway.heartbeat')
  })

  it('counts actual reconnect attempts cumulatively and clears the measurement window', () => {
    const first = openConnection()
    vi.advanceTimersByTime(30)
    first.pong()
    first.close()
    expect(store.linkQuality.reconnectCount).toBe(0)
    vi.advanceTimersByTime(1000)
    expect(store.linkQuality.reconnectCount).toBe(1)
    expect(store.linkQuality.samples).toEqual([])
    expect(store.linkQuality.lastMessageAt).toBeNull()
    const second = MockWebSocket.instances.at(-1)!
    second.open()
    expect(getWebSocketQuality(store.connectionState, store.linkQuality.samples, store.linkQuality.now).grade).toBe('pending')
    second.close()
    vi.advanceTimersByTime(1000)
    expect(store.linkQuality.reconnectCount).toBe(2)
  })

  it('ignores old socket callbacks and mismatched pong IDs after a new connection opens', () => {
    const first = openConnection()
    const oldId = first.latestPing().id
    first.close()
    vi.advanceTimersByTime(1000)
    const second = MockWebSocket.instances.at(-1)!
    second.open()
    first.pong(oldId)
    first.receive(envelope)
    first.onopen?.()
    first.onclose?.()
    first.onerror?.()
    expect(store.connectionState).toBe('CONNECTED')
    expect(store.linkQuality.lastMessageAt).toBeNull()
    expect(store.lastEnvelope).toBeNull()
    second.pong(oldId)
    expect(store.linkQuality.samples).toEqual([])
    vi.advanceTimersByTime(90)
    second.pong()
    expect(store.linkQuality.samples[0]?.rttMs).toBe(90)
    expect(store.lastError).toBe('')
  })

  it('pauses probes when hidden and resumes with fresh measurement', () => {
    const ws = openConnection()
    const oldId = ws.latestPing().id
    visibility = 'hidden'
    document.dispatchEvent(new Event('visibilitychange'))
    vi.advanceTimersByTime(10000)
    ws.pong(oldId)
    expect(ws.sent).toHaveLength(1)
    expect(store.linkQuality.samples).toEqual([])
    visibility = 'visible'
    document.dispatchEvent(new Event('visibilitychange'))
    expect(ws.sent).toHaveLength(2)
    ws.pong(oldId)
    vi.advanceTimersByTime(25)
    ws.pong()
    expect(store.linkQuality.samples.map(sample => sample.rttMs)).toEqual([25])
  })

  it('discards in-flight measurements after event-loop suspension before accepting a queued pong', () => {
    const ws = openConnection()
    vi.setSystemTime(Date.now() + 5000)
    const clock = vi.spyOn(performance, 'now').mockReturnValue(5000)
    ws.pong()
    expect(store.linkQuality.samples).toEqual([])
    vi.advanceTimersByTime(250)
    expect(ws.sent).toHaveLength(2)
    clock.mockReturnValue(5040)
    ws.pong()
    expect(store.linkQuality.samples.map(sample => sample.rttMs)).toEqual([40])
  })

  it('retains a bounded sixty-second history and refreshes age without a component timer', () => {
    const ws = openConnection()
    for (let index = 0; index < 35; index += 1) {
      vi.advanceTimersByTime(40)
      ws.pong()
      vi.advanceTimersByTime(1960)
    }
    expect(store.linkQuality.samples).toHaveLength(29)
    expect(store.linkQuality.samples.every(sample => store.linkQuality.now - sample.sentAt < 60000)).toBe(true)
    expect(store.linkQuality.now - store.linkQuality.lastMessageAt!).toBe(1960)
  })

  it('removes all tracking and reconnect timers on logout/disconnect', () => {
    const ws = openConnection()
    ws.close()
    expect(vi.getTimerCount()).toBe(2)
    store.disconnect()
    expect(vi.getTimerCount()).toBe(0)
    const frozenTime = store.linkQuality.now
    vi.advanceTimersByTime(10000)
    document.dispatchEvent(new Event('visibilitychange'))
    ws.pong()
    expect(MockWebSocket.instances).toHaveLength(1)
    expect(store.linkQuality).toEqual({ samples: [], lastMessageAt: null, reconnectCount: 0, now: frozenTime })
  })
})

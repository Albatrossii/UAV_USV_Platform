import { describe, expect, it } from 'vitest'

import { getRecentWebSocketSamples, getWebSocketQuality, type WebSocketProbeSample } from '@/utils/websocketQuality'

const now = 100000
const samples = (...rtts: Array<number | null>): WebSocketProbeSample[] => rtts.map((rttMs, index) => ({
  sentAt: now - (rtts.length - index - 1) * 2000,
  rttMs,
}))

describe('WebSocket quality classification', () => {
  it.each(['CONNECTING', 'DISCONNECTED'])('prioritizes %s over historical successes', (state) => {
    expect(getWebSocketQuality(state, samples(25), now)).toMatchObject({ grade: 'offline', label: '无连接', rttMs: null })
  })

  it('waits for a measured sample after connection', () => {
    expect(getWebSocketQuality('CONNECTED', [], now)).toMatchObject({ grade: 'pending', label: '待检测', rttMs: null })
  })

  it.each([
    [0, 'excellent', '优秀'],
    [100, 'excellent', '优秀'],
    [100.1, 'good', '良好'],
    [300, 'good', '良好'],
    [300.1, 'poor', '差'],
  ])('classifies measured RTT %s at the defined boundaries', (rttMs, grade, label) => {
    expect(getWebSocketQuality('CONNECTED', samples(Number(rttMs)), now)).toMatchObject({ grade, label, rttMs })
  })

  it('uses the median of valid results within the last five completed probes', () => {
    expect(getWebSocketQuality('CONNECTED', samples(999, 40, null, 80, 120, 140), now))
      .toMatchObject({ grade: 'excellent', rttMs: 100 })
  })

  it('does not fabricate an RTT from timeouts', () => {
    expect(getWebSocketQuality('CONNECTED', samples(null, null), now)).toMatchObject({ grade: 'pending', rttMs: null })
    expect(getWebSocketQuality('CONNECTED', samples(10, null, null, null), now))
      .toMatchObject({ grade: 'poor', rttMs: null, reason: '连续 3 次探针在 1500 ms 内未收到响应' })
  })

  it('expires stale successful samples even while the socket remains connected', () => {
    expect(getWebSocketQuality('CONNECTED', [{ sentAt: now - 6001, rttMs: 10 }], now))
      .toMatchObject({ grade: 'pending', rttMs: null })
  })

  it('keeps only the latest thirty samples inside sixty seconds', () => {
    const history = Array.from({ length: 100 }, (_, index) => ({ sentAt: now - (99 - index) * 1000, rttMs: index }))
    const recent = getRecentWebSocketSamples(history, now)
    expect(recent).toHaveLength(30)
    expect(recent[0]?.rttMs).toBe(70)
    expect(getRecentWebSocketSamples([{ sentAt: now - 60000, rttMs: 50 }], now)).toEqual([])
  })
})

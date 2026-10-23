export interface WebSocketProbeSample {
  /** Local wall-clock send time, for the rolling history chart. */
  sentAt: number
  /** Monotonic round-trip duration; null denotes an unanswered probe. */
  rttMs: number | null
}

export const WEBSOCKET_PROBE_INTERVAL_MS = 2000
export const WEBSOCKET_PROBE_TIMEOUT_MS = 1500
export const WEBSOCKET_HISTORY_MS = 60000
export const WEBSOCKET_MAX_SAMPLES = 30
export const WEBSOCKET_SAMPLE_FRESHNESS_MS = 6000

export type WebSocketQualityGrade = 'excellent' | 'good' | 'poor' | 'offline' | 'pending'

export interface WebSocketQuality {
  grade: WebSocketQualityGrade
  label: string
  rttMs: number | null
  reason: string
}

export function getRecentWebSocketSamples(samples: WebSocketProbeSample[], now: number) {
  return samples
    .filter(sample => Number.isFinite(sample.sentAt) && sample.sentAt <= now && now - sample.sentAt < WEBSOCKET_HISTORY_MS)
    .slice(-WEBSOCKET_MAX_SAMPLES)
}

export function getWebSocketQuality(
  connectionState: string,
  samples: WebSocketProbeSample[],
  now: number,
): WebSocketQuality {
  if (connectionState !== 'CONNECTED') {
    return { grade: 'offline', label: '无连接', rttMs: null, reason: 'WebSocket 未连接，正在等待连接恢复' }
  }
  const recent = getRecentWebSocketSamples(samples, now).slice(-5)
  const latest = recent[recent.length - 1]
  if (!latest || now - latest.sentAt > WEBSOCKET_SAMPLE_FRESHNESS_MS) {
    return { grade: 'pending', label: '待检测', rttMs: null, reason: '等待新的 WebSocket 往返探针，后台暂停期间不计入延迟' }
  }
  if (recent.length >= 3 && recent.slice(-3).every(sample => sample.rttMs === null)) {
    return { grade: 'poor', label: '差', rttMs: null, reason: '连续 3 次探针在 1500 ms 内未收到响应' }
  }
  const successful = recent
    .map(sample => sample.rttMs)
    .filter((rtt): rtt is number => rtt !== null && Number.isFinite(rtt) && rtt >= 0)
    .sort((left, right) => left - right)
  if (!successful.length) {
    return { grade: 'pending', label: '待检测', rttMs: null, reason: '尚无有效往返样本，连续 3 次超时后判定为差' }
  }
  const middle = Math.floor(successful.length / 2)
  const rttMs = successful.length % 2
    ? successful[middle]!
    : (successful[middle - 1]! + successful[middle]!) / 2
  const reason = '最近 5 次探针中有效 RTT 的中位数：≤100 ms 优秀，≤300 ms 良好，>300 ms 差'
  if (rttMs <= 100) return { grade: 'excellent', label: '优秀', rttMs, reason }
  if (rttMs <= 300) return { grade: 'good', label: '良好', rttMs, reason }
  return { grade: 'poor', label: '差', rttMs, reason }
}

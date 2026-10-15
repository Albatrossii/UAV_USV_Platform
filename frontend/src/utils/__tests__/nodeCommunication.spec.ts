import { describe, expect, it } from 'vitest'

import type { RuntimeNode } from '@/types/monitoring'
import { buildNodeCommunicationRows, normalizeNodeCode } from '@/utils/nodeCommunication'

const now = Date.parse('2026-10-15T12:00:00Z')

function node(overrides: Partial<RuntimeNode> = {}): RuntimeNode {
  return {
    id: 1,
    code: 'uav-01',
    name: '无人机 1',
    type: 'UAV',
    status: 'ONLINE',
    host: '192.0.2.1',
    port: 8765,
    endpoint: '192.0.2.1:8765',
    rosNamespace: '/uav_01',
    lastHeartbeatAt: '2026-10-15T12:00:00',
    heartbeatAgeSeconds: 0,
    source: 'ROS_GATEWAY_V1',
    instanceId: 'ros-gateway-v1',
    positionX: null,
    positionY: null,
    positionZ: null,
    linkQualityPercent: 100,
    telemetryStale: false,
    detail: null,
    ...overrides,
  }
}

function row(overrides: Partial<RuntimeNode> = {}, observedAt: number | null = now, available = true) {
  return buildNodeCommunicationRows(['UAV_001'], [node(overrides)], now, observedAt, available)[0]!
}

describe('node code normalization', () => {
  it.each([
    [' UAV_001 ', 'uav-01'],
    ['UsV-01', 'usv-01'],
    ['uav-1', 'uav-01'],
    ['usv_010', 'usv-10'],
    ['uav-100', 'uav-100'],
    ['uav-000', 'uav-00'],
    ['uav-9007199254740993', 'uav-9007199254740993'],
    ['Unity_001', 'Unity_001'],
    ['uav-a01', 'uav-a01'],
    ['UAV01', 'UAV01'],
    ['uav-01-backup', 'uav-01-backup'],
  ])('normalizes only explicit numbered vehicle code %s', (code, expected) => {
    expect(normalizeNodeCode(code)).toBe(expected)
  })

  it('retains scene order and labels while matching both vehicle types', () => {
    const rows = buildNodeCommunicationRows(['USV_002', 'UAV_001'], [
      node(),
      node({ id: 2, code: 'usv-02', type: 'USV', name: '无人艇 2' }),
    ], now, now, true)
    expect(rows.map(value => [value.code, value.type, value.name, value.state])).toEqual([
      ['USV_002', 'USV', '无人艇 2', 'online'],
      ['UAV_001', 'UAV', '无人机 1', 'online'],
    ])
  })

  it('does not choose one of two runtime records sharing a normalized code', () => {
    const rows = buildNodeCommunicationRows(['uav-01'], [node(), node({ id: 2, code: 'UAV_001' })], now, now, true)
    expect(rows[0]).toMatchObject({ state: 'unknown', qualityPercent: null, bars: 0, endpoint: '' })
    expect(rows[0]!.reason).toContain('多个匹配')
  })

  it('does not reuse one runtime record for ambiguous scene aliases', () => {
    const rows = buildNodeCommunicationRows(['uav-01', 'UAV_001', 'uav-01'], [node()], now, now, true)
    expect(rows).toHaveLength(2)
    expect(rows.every(value => value.state === 'unknown')).toBe(true)
  })

  it('supports exact custom registry identifiers without normalizing their spelling', () => {
    const rows = buildNodeCommunicationRows(['Falcon_A', 'falcon_a', 'unity-client-01'], [
      node({ code: 'Falcon_A' }),
    ], now, now, true)
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ code: 'Falcon_A', type: 'UAV', state: 'online' })
  })

  it.each(['USV', 'UNITY_NODE'] as const)('rejects mismatched node type %s', (type) => {
    expect(row({ type })).toMatchObject({ state: 'unknown', bars: 0, qualityPercent: null })
  })
})

describe('real runtime node evidence', () => {
  it('keeps scene-only vehicles unknown without inventing node measurements', () => {
    expect(buildNodeCommunicationRows(['uav-01'], [], now, now, true)[0]).toMatchObject({
      state: 'unknown', stateLabel: '未知', bars: 0, qualityLabel: '待检测',
      qualityPercent: null, heartbeatAgeSeconds: null, source: '', endpoint: '',
    })
  })

  it.each(['UNITY_HEARTBEAT', 'UNITY_WEBGL', 'UNITY_VIRTUAL', 'ALGORITHM_FRAME', 'BROWSER_WEBSOCKET', 'REGISTRY', 'UNKNOWN', ''])
  ('does not promote source %s to a real node', (source) => {
    expect(row({ source })).toMatchObject({ state: 'unknown', bars: 0, qualityPercent: null, heartbeatAgeSeconds: null })
  })

  it.each(['ROS_WEBSOCKET', 'ROS_GATEWAY_V1'])('accepts current backend vehicle source %s', (source) => {
    expect(row({ source })).toMatchObject({
      state: 'online', stateLabel: '在线', heartbeatAgeSeconds: 0,
      source, endpoint: '192.0.2.1:8765',
    })
  })

  it.each([
    { lastHeartbeatAt: null },
    { lastHeartbeatAt: '' },
    { lastHeartbeatAt: 'not-a-time' },
    { heartbeatAgeSeconds: -1 },
    { heartbeatAgeSeconds: NaN },
    { heartbeatAgeSeconds: Infinity },
    { heartbeatAgeSeconds: 0.5 },
    { heartbeatAgeSeconds: Number.MAX_SAFE_INTEGER + 1 },
  ])('requires a valid heartbeat timestamp and reported age: %s', (overrides) => {
    expect(row(overrides)).toMatchObject({ state: 'unknown', heartbeatAgeSeconds: null, bars: 0 })
  })

  it('keeps never-observed offline registry records unknown', () => {
    expect(row({ source: 'REGISTRY', status: 'OFFLINE', lastHeartbeatAt: null, heartbeatAgeSeconds: -1 }))
      .toMatchObject({ state: 'unknown', heartbeatAgeSeconds: null })
    expect(row({ status: 'OFFLINE', lastHeartbeatAt: null, heartbeatAgeSeconds: -1 }))
      .toMatchObject({ state: 'unknown', heartbeatAgeSeconds: null })
  })

  it('honors explicit backend offline status after a real observation', () => {
    expect(row({ status: 'OFFLINE', heartbeatAgeSeconds: 30 }))
      .toMatchObject({ state: 'offline', stateLabel: '离线', bars: 0, qualityPercent: null, heartbeatAgeSeconds: 30 })
  })

  it('accepts an explicit control-session stop without prior heartbeat', () => {
    expect(row({ source: 'CONTROL_SESSION', status: 'OFFLINE', lastHeartbeatAt: null, heartbeatAgeSeconds: -1 }))
      .toMatchObject({ state: 'offline', bars: 0, qualityPercent: null, heartbeatAgeSeconds: null })
    expect(row({ source: 'CONTROL_SESSION', status: 'ONLINE' }).state).toBe('unknown')
  })

  it.each(['ROS_WEBSOCKET', 'ROS_GATEWAY_V1'])('prioritizes fresh explicit device disconnect for %s over a recent pose heartbeat', (source) => {
    expect(row({ source, controlStateFresh: true, controlConnectionState: 'OFFLINE' }))
      .toMatchObject({ state: 'offline', stateLabel: '离线', bars: 0, qualityPercent: null })
  })

  it('accepts a fresh explicit device disconnect without a previous pose heartbeat', () => {
    expect(row({
      controlStateFresh: true, controlConnectionState: 'OFFLINE',
      lastHeartbeatAt: null, heartbeatAgeSeconds: -1,
    })).toMatchObject({ state: 'offline', heartbeatAgeSeconds: null, bars: 0, qualityPercent: null })
  })

  it.each([false, undefined])('does not let a stale or unconfirmed disconnect override a new heartbeat: %s', (controlStateFresh) => {
    expect(row({ controlStateFresh, controlConnectionState: 'OFFLINE' }))
      .toMatchObject({ state: 'online', qualityPercent: 100 })
  })

  it('still requires a supported real source for an explicit device disconnect', () => {
    expect(row({ source: 'UNITY_HEARTBEAT', controlStateFresh: true, controlConnectionState: 'OFFLINE' }))
      .toMatchObject({ state: 'unknown', bars: 0, qualityPercent: null })
  })

  it('does not preserve an explicit disconnect when the monitoring snapshot expires or fails', () => {
    const disconnect = { controlStateFresh: true, controlConnectionState: 'OFFLINE' }
    expect(row(disconnect, now - 6000).state).toBe('unknown')
    expect(row(disconnect, now, false).state).toBe('unknown')
  })

  it('does not reinterpret unknown or maintenance status as online', () => {
    expect(row({ status: 'UNKNOWN' }).state).toBe('unknown')
    expect(row({ status: 'MAINTENANCE' })).toMatchObject({ state: 'delayed', stateLabel: '维护中', qualityPercent: null })
  })

  it('does not invent a maintenance state for an unsupported backend status', () => {
    expect(row({ status: 'UNRECOGNIZED' as RuntimeNode['status'] }).state).toBe('unknown')
  })
})

describe('monitoring and heartbeat freshness', () => {
  it('advances server age from local request start time without assuming matching timezones', () => {
    expect(row({ lastHeartbeatAt: '2026-10-15T20:00:00', heartbeatAgeSeconds: 2 }, now - 2000))
      .toMatchObject({ state: 'online', heartbeatAgeSeconds: 4 })
  })

  it.each([[4, 'online'], [5, 'online'], [6, 'delayed'], [600, 'delayed']] as const)
  ('respects the backend integer-second heartbeat boundary at %s seconds', (age, state) => {
    expect(row({ heartbeatAgeSeconds: age }).state).toBe(state)
  })

  it('expires a heartbeat even while the last successful monitoring query is fresh', () => {
    expect(row({ heartbeatAgeSeconds: 5 }, now - 1000))
      .toMatchObject({ state: 'delayed', stateLabel: '心跳过期', heartbeatAgeSeconds: 6, bars: 0, qualityPercent: null })
  })

  it('expires the monitoring snapshot without claiming the vehicle went offline', () => {
    expect(row({}, now - 5999).state).toBe('online')
    expect(row({}, now - 6000)).toMatchObject({ state: 'unknown', qualityPercent: null, bars: 0 })
    expect(row({ status: 'OFFLINE' }, now - 6000).state).toBe('unknown')
  })

  it('invalidates stale successes on monitoring query failure', () => {
    expect(row({}, now, false)).toMatchObject({ state: 'unknown', bars: 0, qualityPercent: null, heartbeatAgeSeconds: null })
  })

  it.each([null, NaN, Infinity, now + 1])('requires a valid local snapshot request time: %s', (observedAt) => {
    expect(row({}, observedAt)).toMatchObject({ state: 'unknown', bars: 0, qualityPercent: null })
  })

  it('does not claim online state for an invalid current clock', () => {
    expect(buildNodeCommunicationRows(['uav-01'], [node()], NaN, now, true)[0]?.state).toBe('unknown')
  })
})

describe('backend heartbeat freshness indicator', () => {
  it.each([[0, 0], [1, 1], [25, 1], [26, 2], [50, 2], [51, 3], [75, 3], [76, 4], [100, 4]])
  ('preserves the explicit value %s, including zero, with %s bars', (qualityPercent, bars) => {
    expect(row({ linkQualityPercent: qualityPercent })).toMatchObject({
      state: 'online', qualityPercent, bars, qualityLabel: `${qualityPercent}% 心跳新鲜度`,
    })
  })

  it.each([undefined, null, NaN, Infinity, -1, 101])('does not fabricate quality from invalid value %s', (linkQualityPercent) => {
    expect(row({ linkQualityPercent })).toMatchObject({ state: 'online', qualityPercent: null, bars: 0, qualityLabel: '待检测' })
  })

  it.each([true, undefined])('requires explicit telemetry freshness instead of assuming it from %s', (telemetryStale) => {
    expect(row({ telemetryStale })).toMatchObject({ state: 'online', qualityPercent: null, bars: 0, qualityLabel: '待检测' })
  })

  it('does not coerce string quality values into measurements', () => {
    expect(row({ linkQualityPercent: '80' as unknown as number }))
      .toMatchObject({ qualityPercent: null, bars: 0, qualityLabel: '待检测' })
  })

  it('explains that the backend percentage is an age estimate, not measured radio signal or RTT', () => {
    expect(row().reason).toContain('按查询时的心跳年龄估算')
    expect(row().reason).toContain('并非实测网络信号或节点 RTT')
  })

  it('does not mutate scene codes or monitoring records', () => {
    const codes = Object.freeze(['UAV_001'])
    const records = Object.freeze([Object.freeze(node())])
    expect(() => buildNodeCommunicationRows([...codes], [...records], now, now, true)).not.toThrow()
    expect(records[0]?.code).toBe('uav-01')
  })
})

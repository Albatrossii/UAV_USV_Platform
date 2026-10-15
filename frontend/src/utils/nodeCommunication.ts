import type { RuntimeNode } from '@/types/monitoring'

export interface NodeCommunicationRow {
  code: string
  name: string
  type: 'UAV' | 'USV'
  state: 'online' | 'delayed' | 'offline' | 'unknown'
  stateLabel: string
  qualityLabel: string
  bars: number
  qualityPercent: number | null
  heartbeatAgeSeconds: number | null
  source: string
  endpoint: string
  reason: string
}

// RuntimeStateService uses Duration.getSeconds() <= this configured value.
// application.yml currently sets 5; the API does not expose runtime overrides.
// This is a conservative display limit, not a client-side declaration of outage.
const HEARTBEAT_TIMEOUT_SECONDS = 5
const NODE_SOURCES = new Set(['ROS_WEBSOCKET', 'ROS_GATEWAY_V1'])
const VEHICLE_CODE = /^(uav|usv)[-_](\d+)$/i

/** Normalize only explicit numbered UAV/USV codes; leave other identifiers intact. */
export function normalizeNodeCode(code: string): string {
  const trimmed = code.trim()
  const match = VEHICLE_CODE.exec(trimmed)
  if (!match) return trimmed
  // Avoid Number conversion: long identifiers must not lose significant digits.
  const number = match[2]!.replace(/^0+(?=\d)/, '').padStart(2, '0')
  return `${match[1]!.toLowerCase()}-${number}`
}

function codeType(code: string): NodeCommunicationRow['type'] | null {
  const match = VEHICLE_CODE.exec(code)
  return match ? match[1]!.toUpperCase() as NodeCommunicationRow['type'] : null
}

function isVehicle(node: RuntimeNode): node is RuntimeNode & { type: 'UAV' | 'USV' } {
  return node.type === 'UAV' || node.type === 'USV'
}

function emptyRow(code: string, type: NodeCommunicationRow['type']): NodeCommunicationRow {
  return {
    code,
    name: code,
    type,
    state: 'unknown',
    stateLabel: '未知',
    qualityLabel: '待检测',
    bars: 0,
    qualityPercent: null,
    heartbeatAgeSeconds: null,
    source: '',
    endpoint: '',
    reason: '尚未收到该设备的真实节点心跳',
  }
}

function heartbeatAge(node: RuntimeNode, elapsedSeconds: number): number | null {
  // Backend LocalDateTime has no timezone. Use its reported age, advancing it
  // from the local query start time, rather than comparing server/client clocks.
  if (!node.lastHeartbeatAt || !Number.isFinite(Date.parse(node.lastHeartbeatAt))) return null
  if (!Number.isSafeInteger(node.heartbeatAgeSeconds) || node.heartbeatAgeSeconds < 0) return null
  return node.heartbeatAgeSeconds + elapsedSeconds
}

/**
 * Match scene vehicles to monitoring records. observedAt is the local start
 * time of the last successful nodes query; available must be false on failure.
 * Unknown identifiers without a matching UAV/USV record are omitted because
 * their vehicle type cannot be inferred. Conflicting aliases remain unknown.
 */
export function buildNodeCommunicationRows(
  deviceCodes: string[],
  runtimeNodes: RuntimeNode[],
  now: number,
  observedAt: number | null,
  available: boolean,
): NodeCommunicationRow[] {
  const nodesByCode = new Map<string, RuntimeNode[]>()
  for (const node of runtimeNodes) {
    const key = normalizeNodeCode(node.code)
    const matches = nodesByCode.get(key) ?? []
    matches.push(node)
    nodesByCode.set(key, matches)
  }

  const sceneCodes = [...new Set(deviceCodes.map(code => code.trim()).filter(Boolean))]
  const aliases = new Map<string, number>()
  for (const code of sceneCodes) {
    const key = normalizeNodeCode(code)
    aliases.set(key, (aliases.get(key) ?? 0) + 1)
  }

  const elapsedSeconds = observedAt !== null && Number.isFinite(observedAt)
    && Number.isFinite(now) && now >= observedAt
    ? Math.floor((now - observedAt) / 1000)
    : null
  const rows: NodeCommunicationRow[] = []

  for (const code of sceneCodes) {
    const key = normalizeNodeCode(code)
    const matches = nodesByCode.get(key) ?? []
    const node = matches.length === 1 ? matches[0] : undefined
    const type = codeType(key) ?? (node && isVehicle(node) ? node.type : null)
    if (!type) continue
    const row = emptyRow(code, type)
    rows.push(row)

    if ((aliases.get(key) ?? 0) > 1 || matches.length > 1) {
      row.reason = '设备编码归一化后存在多个匹配，无法确认真实节点'
      continue
    }
    if (!available) {
      row.reason = '节点监测查询不可用，无法确认当前通信状态'
      continue
    }
    if (elapsedSeconds === null) {
      row.reason = '尚无有效的节点监测快照'
      continue
    }
    if (node) {
      row.name = node.name.trim() || code
      row.source = node.source.trim()
      row.endpoint = node.endpoint.trim()
      row.heartbeatAgeSeconds = heartbeatAge(node, elapsedSeconds)
    }
    if (elapsedSeconds > HEARTBEAT_TIMEOUT_SECONDS) {
      row.reason = '节点监测快照已过期，等待重新查询'
      continue
    }
    if (!node) continue
    if (!isVehicle(node) || node.type !== type) {
      row.reason = '监测节点类型与场景设备不一致，无法确认真实节点'
      continue
    }

    const source = row.source.toUpperCase()
    // Explicit backend session-stop records can be offline without a heartbeat.
    if (source === 'CONTROL_SESSION' && node.status === 'OFFLINE') {
      row.state = 'offline'
      row.stateLabel = '离线'
      row.reason = '后端控制会话已明确停止或断开'
      continue
    }
    if (!NODE_SOURCES.has(source)) {
      row.heartbeatAgeSeconds = null
      row.reason = /UNITY|VIRTUAL|ALGORITHM|BROWSER/.test(source)
        ? '该来源属于场景、算法或浏览器链路，不能确认真实节点通信'
        : '尚无受支持的真实节点心跳来源'
      continue
    }
    // Gateway device.status and pose heartbeats are stored independently. A
    // fresh, explicit device disconnect takes precedence over a cached pose.
    if (node.controlStateFresh === true && node.controlConnectionState === 'OFFLINE') {
      row.state = 'offline'
      row.stateLabel = '离线'
      row.reason = '后端收到有效设备状态，节点已明确断开'
      continue
    }
    if (row.heartbeatAgeSeconds === null) continue
    if (node.status === 'OFFLINE') {
      row.state = 'offline'
      row.stateLabel = '离线'
      row.reason = '后端已将曾收到心跳的节点判定为离线'
      continue
    }
    if (node.status !== 'ONLINE' && node.status !== 'MAINTENANCE') {
      row.reason = '后端节点状态未知，无法确认当前通信状态'
      continue
    }
    if (row.heartbeatAgeSeconds > HEARTBEAT_TIMEOUT_SECONDS) {
      row.state = 'delayed'
      row.stateLabel = '心跳过期'
      row.reason = '节点心跳已超过后端默认的 5 秒窗口，等待新的节点状态'
      continue
    }
    if (node.status !== 'ONLINE') {
      row.state = 'delayed'
      row.stateLabel = '维护中'
      row.reason = '后端节点处于维护状态，无法确认正常通信'
      continue
    }

    row.state = 'online'
    row.stateLabel = '在线'
    row.reason = '后端已收到有效节点心跳；尚无可用的心跳新鲜度指标'
    const quality = node.linkQualityPercent
    if (node.telemetryStale === false && typeof quality === 'number'
      && Number.isFinite(quality) && quality >= 0 && quality <= 100) {
      row.qualityPercent = quality
      row.qualityLabel = `${quality}% 心跳新鲜度`
      row.bars = Math.ceil(quality / 25)
      // RuntimeNodeResponse.linkQuality derives this from heartbeat age.
      // It is not a measured radio signal, packet-loss rate, or node RTT.
      row.reason = '后端已收到有效节点心跳；心跳新鲜度按查询时的心跳年龄估算，并非实测网络信号或节点 RTT'
    }
  }
  return rows
}

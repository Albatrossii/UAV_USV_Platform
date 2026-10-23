import { defineStore } from 'pinia'

import { fetchRealtimeSnapshot } from '@/api/realtime'
import type { RealtimeSnapshot } from '@/api/realtime'
import { fetchRuntimeCommandLogs } from '@/api/runtimeControl'
import {
  getRecentWebSocketSamples,
  WEBSOCKET_PROBE_INTERVAL_MS,
  WEBSOCKET_PROBE_TIMEOUT_MS,
  type WebSocketProbeSample,
} from '@/utils/websocketQuality'
import type {
  ControlEventPayload,
  GatewayEnvelope,
  MissionStatusPayload,
  PoseBatchPayload,
  TargetBatchPayload,
} from '@/types/realtime'

type RealtimeConnectionState = 'DISCONNECTED' | 'CONNECTING' | 'CONNECTED'

interface RealtimeLinkQuality {
  samples: WebSocketProbeSample[]
  lastMessageAt: number | null
  reconnectCount: number
  now: number
}

interface RealtimeState {
  connectionState: RealtimeConnectionState
  linkQuality: RealtimeLinkQuality
  hydrating: boolean
  hydrated: boolean
  lastError: string
  runId: string
  streamSequences: Record<string, number>
  poseBatch: GatewayEnvelope<PoseBatchPayload> | null
  targetBatch: GatewayEnvelope<TargetBatchPayload> | null
  missionStatus: GatewayEnvelope<MissionStatusPayload> | null
  controlEvents: GatewayEnvelope<ControlEventPayload>[]
  commandStatuses: Record<string, string>
  lastEnvelope: GatewayEnvelope | null
}

let socket: WebSocket | null = null
let reconnectTimer: number | null = null
let reconnectAttempts = 0
let reconnectEnabled = false
let hydrationPromise: Promise<void> | null = null
let connectionGeneration = 0
let qualityTimer: number | null = null
let visibilityListener: (() => void) | null = null
let probeSequence = 0
let nextProbeAt = 0
let previousQualityTick = 0
let previousQualityWallTime = 0
let pendingProbe: {
  id: string
  sentAt: number
  startedAt: number
  generation: number
  socket: WebSocket
} | null = null

type QualityStore = Pick<RealtimeState, 'connectionState' | 'linkQuality'>

const QUALITY_REFRESH_MS = 250
// A blocked event loop cannot provide a useful network-only timing sample.
const MAX_QUALITY_TICK_GAP_MS = 750

function stopQualityTracking() {
  if (qualityTimer !== null) window.clearInterval(qualityTimer)
  qualityTimer = null
  if (visibilityListener) document.removeEventListener('visibilitychange', visibilityListener)
  visibilityListener = null
  pendingProbe = null
}

function timingWasInterrupted(monotonicNow: number, wallNow: number) {
  return monotonicNow - previousQualityTick > MAX_QUALITY_TICK_GAP_MS
    || wallNow - previousQualityWallTime > MAX_QUALITY_TICK_GAP_MS
    || wallNow < previousQualityWallTime
}

function resetProbeTiming(store: QualityStore, monotonicNow: number, wallNow: number) {
  pendingProbe = null
  nextProbeAt = monotonicNow
  previousQualityTick = monotonicNow
  previousQualityWallTime = wallNow
  store.linkQuality.samples = []
  store.linkQuality.now = wallNow
}

function recordProbe(store: QualityStore, rttMs: number | null) {
  if (!pendingProbe) return
  store.linkQuality.samples = getRecentWebSocketSamples([
    ...store.linkQuality.samples,
    { sentAt: pendingProbe.sentAt, rttMs },
  ], store.linkQuality.now)
  pendingProbe = null
}

function sendProbe(store: QualityStore, monotonicNow: number) {
  if (pendingProbe || document.visibilityState === 'hidden'
    || store.connectionState !== 'CONNECTED' || socket?.readyState !== WebSocket.OPEN) return
  const currentSocket = socket
  const id = `${connectionGeneration.toString(36)}-${(++probeSequence).toString(36)}`
  pendingProbe = {
    id,
    sentAt: Date.now(),
    startedAt: monotonicNow,
    generation: connectionGeneration,
    socket: currentSocket,
  }
  nextProbeAt = monotonicNow + WEBSOCKET_PROBE_INTERVAL_MS
  try {
    currentSocket.send(JSON.stringify({ type: 'realtime.ping', id }))
  } catch {
    // A closing socket can race send(). Its normal close handler owns recovery.
    pendingProbe = null
  }
}

function startQualityTracking(store: QualityStore) {
  stopQualityTracking()
  resetProbeTiming(store, performance.now(), Date.now())
  visibilityListener = () => {
    resetProbeTiming(store, performance.now(), Date.now())
    if (document.visibilityState !== 'hidden') sendProbe(store, performance.now())
  }
  document.addEventListener('visibilitychange', visibilityListener)
  qualityTimer = window.setInterval(() => {
    const monotonicNow = performance.now()
    const wallNow = Date.now()
    store.linkQuality.now = wallNow
    if (timingWasInterrupted(monotonicNow, wallNow)) resetProbeTiming(store, monotonicNow, wallNow)
    previousQualityTick = monotonicNow
    previousQualityWallTime = wallNow
    store.linkQuality.samples = getRecentWebSocketSamples(store.linkQuality.samples, wallNow)
    if (document.visibilityState === 'hidden') {
      pendingProbe = null
      return
    }
    if (pendingProbe && monotonicNow - pendingProbe.startedAt >= WEBSOCKET_PROBE_TIMEOUT_MS) {
      recordProbe(store, null)
    }
    if (monotonicNow >= nextProbeAt) sendProbe(store, monotonicNow)
  }, QUALITY_REFRESH_MS)
}

function isProbeMessage(value: unknown): value is { type: 'realtime.ping' | 'realtime.pong'; id?: unknown } {
  if (!value || typeof value !== 'object') return false
  const type = (value as { type?: unknown }).type
  return type === 'realtime.ping' || type === 'realtime.pong'
}

function receivePong(store: QualityStore, value: { id?: unknown }, currentSocket: WebSocket, generation: number) {
  if (!pendingProbe || pendingProbe.socket !== currentSocket || pendingProbe.generation !== generation
    || typeof value.id !== 'string' || pendingProbe.id !== value.id) return
  const monotonicNow = performance.now()
  if (document.visibilityState === 'hidden' || timingWasInterrupted(monotonicNow, Date.now())) {
    resetProbeTiming(store, monotonicNow, Date.now())
    return
  }
  const elapsed = monotonicNow - pendingProbe.startedAt
  recordProbe(store, elapsed >= 0 && elapsed < WEBSOCKET_PROBE_TIMEOUT_MS ? elapsed : null)
}

function reconnectDelay() {
  return Math.min(1000 * 2 ** reconnectAttempts, 15000)
}

function realtimeUrl() {
  const configured = import.meta.env.VITE_REALTIME_WS_URL as string | undefined
  if (configured) return configured
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/api/v1/realtime`
}

function streamKey(envelope: GatewayEnvelope) {
  const taskScoped = [
    'telemetry.pose_batch', 'telemetry.target_batch', 'mission.status', 'control.ack', 'control.feedback', 'control.result',
  ].includes(envelope.type)
  return taskScoped
    ? `${envelope.runId ?? 'missing-run'}:${envelope.source}:${envelope.streamId}`
    : `${envelope.source}:${envelope.streamId}`
}

function normalizeEnvelope(value: unknown): GatewayEnvelope | null {
  if (!value || typeof value !== 'object') return null
  const candidate = value as Partial<GatewayEnvelope>
  const sequence = Number(candidate.sequence)
  if (
    typeof candidate.type !== 'string'
    || typeof candidate.source !== 'string'
    || typeof candidate.streamId !== 'string'
    || !Number.isFinite(sequence)
  ) {
    return null
  }
  return {
    version: String(candidate.version ?? 'v1'),
    type: candidate.type,
    source: candidate.source,
    timestamp: String(candidate.timestamp ?? ''),
    missionId: candidate.missionId ?? null,
    runId: candidate.runId ?? null,
    streamId: candidate.streamId,
    frameId: candidate.frameId ?? null,
    sequence,
    payload: candidate.payload ?? {},
  }
}

export const useRealtimeStore = defineStore('realtime', {
  state: (): RealtimeState => ({
    connectionState: 'DISCONNECTED',
    linkQuality: { samples: [], lastMessageAt: null, reconnectCount: 0, now: Date.now() },
    hydrating: false,
    hydrated: false,
    lastError: '',
    runId: '',
    streamSequences: {},
    poseBatch: null,
    targetBatch: null,
    missionStatus: null,
    controlEvents: [],
    commandStatuses: {},
    lastEnvelope: null,
  }),
  getters: {
    connected: state => state.connectionState === 'CONNECTED',
    latestSequence: state => (source: string, streamId: string) =>
      state.streamSequences[`${source}:${streamId}`] ?? 0,
  },
  actions: {
    connect() {
      reconnectEnabled = true
      if (socket && socket.readyState !== WebSocket.CLOSED) return
      const generation = ++connectionGeneration
      this.linkQuality.lastMessageAt = null
      startQualityTracking(this)
      this.hydrated = false
      hydrationPromise = null
      this.streamSequences = {}
      this.poseBatch = null
      this.targetBatch = null
      this.missionStatus = null
      void this.hydrateSnapshot()
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      this.connectionState = 'CONNECTING'
      this.lastError = ''
      const currentSocket = new WebSocket(realtimeUrl())
      socket = currentSocket
      currentSocket.onopen = () => {
        if (socket !== currentSocket || generation !== connectionGeneration) return
        this.connectionState = 'CONNECTED'
        this.lastError = ''
        reconnectAttempts = 0
        resetProbeTiming(this, performance.now(), Date.now())
        sendProbe(this, performance.now())
      }
      currentSocket.onmessage = event => {
        if (socket !== currentSocket || generation !== connectionGeneration) return
        this.linkQuality.now = Date.now()
        this.linkQuality.lastMessageAt = this.linkQuality.now
        let parsed: unknown = event.data
        try {
          if (typeof parsed === 'string') parsed = JSON.parse(parsed)
        } catch {
          this.ingestMessage(event.data)
          return
        }
        if (isProbeMessage(parsed)) {
          if (parsed.type === 'realtime.pong') receivePong(this, parsed, currentSocket, generation)
          return
        }
        this.ingestMessage(parsed)
      }
      currentSocket.onerror = () => {
        if (socket !== currentSocket) return
        this.lastError = 'Realtime WebSocket connection error'
      }
      currentSocket.onclose = () => {
        if (socket !== currentSocket || generation !== connectionGeneration) return
        this.connectionState = 'DISCONNECTED'
        pendingProbe = null
        socket = null
        if (reconnectEnabled && reconnectTimer === null) {
          const delay = reconnectDelay()
          reconnectAttempts += 1
          reconnectTimer = window.setTimeout(() => {
            reconnectTimer = null
            this.linkQuality.reconnectCount += 1
            this.connect()
          }, delay)
        }
      }
    },
    hydrateSnapshot() {
      if (this.hydrated) return Promise.resolve()
      if (hydrationPromise) return hydrationPromise

      this.hydrating = true
      const generation = connectionGeneration
      hydrationPromise = fetchRealtimeSnapshot()
        .then((snapshot) => {
          if (generation !== connectionGeneration) return
          this.ingestSnapshot(snapshot)
          this.hydrated = true
        })
        .catch(() => {
          // Snapshot hydration is best-effort; the WebSocket remains authoritative
          // and will continue filling the store with subsequent realtime frames.
        })
        .finally(() => {
          if (generation !== connectionGeneration) return
          this.hydrating = false
          hydrationPromise = null
        })
      return hydrationPromise
    },
    refreshSnapshot() {
      const generation = connectionGeneration
      return (hydrationPromise ?? Promise.resolve())
        .then(() => fetchRealtimeSnapshot())
        .then(snapshot => {
          if (generation === connectionGeneration) this.ingestSnapshot(snapshot)
        })
        .catch(() => {
          // An explicit RUN-boundary refresh is also best-effort. Live WebSocket
          // frames remain authoritative if the debug snapshot is unavailable.
        })
    },
    ingestSnapshot(snapshot: RealtimeSnapshot) {
      if (snapshot.latestPoseBatch) this.ingestMessage(snapshot.latestPoseBatch)
      if (snapshot.latestTargetBatch) this.ingestMessage(snapshot.latestTargetBatch)
      if (snapshot.latestMissionStatus) this.ingestMessage(snapshot.latestMissionStatus)
    },
    disconnect() {
      reconnectEnabled = false
      connectionGeneration += 1
      stopQualityTracking()
      hydrationPromise = null
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      const previousSocket = socket
      socket = null
      previousSocket?.close()
      reconnectAttempts = 0
      this.connectionState = 'DISCONNECTED'
      this.linkQuality = { samples: [], lastMessageAt: null, reconnectCount: 0, now: Date.now() }
      this.clear()
    },
    ingestMessage(raw: unknown) {
      try {
        const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
        if (isProbeMessage(parsed)) return
        const envelope = normalizeEnvelope(parsed)
        if (!envelope || !this.acceptSequence(envelope)) return
        this.lastEnvelope = envelope
        this.runId = envelope.runId ?? this.runId
        if (envelope.type === 'telemetry.pose_batch') {
          this.poseBatch = envelope as GatewayEnvelope<PoseBatchPayload>
        } else if (envelope.type === 'telemetry.target_batch') {
          this.targetBatch = envelope as GatewayEnvelope<TargetBatchPayload>
        } else if (envelope.type === 'mission.status') {
          this.missionStatus = envelope as GatewayEnvelope<MissionStatusPayload>
        } else if (
          envelope.type === 'control.ack'
          || envelope.type === 'control.feedback'
          || envelope.type === 'control.result'
        ) {
          const command = envelope as GatewayEnvelope<ControlEventPayload>
          const commandId = command.payload.commandId
          if (commandId && command.payload.status) {
            this.commandStatuses[commandId] = command.payload.status
          }
          this.controlEvents = [
            command,
            ...this.controlEvents,
          ].slice(0, 50)
        }
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : 'Invalid realtime message'
      }
    },
    acceptSequence(envelope: GatewayEnvelope) {
      const key = streamKey(envelope)
      const previous = this.streamSequences[key] ?? 0
      if (envelope.sequence <= previous) return false
      this.streamSequences[key] = envelope.sequence
      return true
    },
    waitForCommandResult(commandId: string, timeoutMs = 90000): Promise<string> {
      const terminal = new Set(['SUCCEEDED', 'FAILED', 'REJECTED', 'CANCELLED', 'TIMEOUT', 'EXPIRED'])
      return new Promise((resolve) => {
        const startedAt = Date.now()
        const timer = window.setInterval(() => {
          const status = this.commandStatuses[commandId]
          if (status && terminal.has(status)) {
            window.clearInterval(timer)
            resolve(status)
          } else if (Date.now() - startedAt >= timeoutMs) {
            window.clearInterval(timer)
            resolve('TIMEOUT')
          }
        }, 100)
      })
    },
    waitForCommandStart(
      commandId: string,
      timeoutMs = 90000,
      initialStatus?: string,
      expectedRunId?: number | string | null,
    ): Promise<string> {
      const started = new Set(['ACCEPTED', 'EXECUTING'])
      const successfulTerminal = new Set(['SUCCEEDED', 'SUCCESS', 'COMPLETED'])
      const terminal = new Set(['FAILED', 'REJECTED', 'CANCELLED', 'TIMEOUT', 'EXPIRED'])
      const runningStates = new Set(['RUNNING', 'EXECUTING', 'ACTIVE', 'STARTED', 'ENCIRCLING'])
      const runningPhases = new Set([
        'ENCIRCLING',
        'FORMATION_CONVERGING',
        'ENCIRCLEMENT',
        'CAPTURE',
        'CAPTURING',
        'CAPTURED',
        'PURSUIT',
        'TASK_RUNNING',
      ])
      const normalizedInitialStatus = String(initialStatus ?? '').trim().toUpperCase()
      if (started.has(normalizedInitialStatus) || successfulTerminal.has(normalizedInitialStatus)) {
        return Promise.resolve(normalizedInitialStatus)
      }
      if (terminal.has(normalizedInitialStatus)) return Promise.resolve(normalizedInitialStatus)

      return new Promise((resolve) => {
        const startedAt = Date.now()
        let fallbackPending = false
        let lastFallbackAt = 0
        let settled = false
        const finish = (status: string) => {
          if (settled) return
          settled = true
          window.clearInterval(timer)
          resolve(status)
        }
        const refreshFromBackend = () => {
          if (fallbackPending || Date.now() - lastFallbackAt < 1000) return
          fallbackPending = true
          lastFallbackAt = Date.now()
          const numericRunId = Number(expectedRunId)
          void fetchRuntimeCommandLogs({
            ...(Number.isFinite(numericRunId) && numericRunId > 0 ? { runId: numericRunId } : {}),
            limit: 100,
          })
            .then((commands) => {
              const command = commands.find(item => item.commandKey === commandId)
              if (!command) return
              const status = String(command.status ?? '').trim().toUpperCase()
              this.commandStatuses[commandId] = status
              if (started.has(status) || successfulTerminal.has(status) || terminal.has(status)) finish(status)
            })
            .catch(() => {
              // The realtime stream remains the primary source. A transient
              // read-fallback failure must not terminate the start wait early.
            })
            .finally(() => {
              fallbackPending = false
            })
        }
        const timer = window.setInterval(() => {
          const status = String(this.commandStatuses[commandId] ?? '').trim().toUpperCase()
          if (status && started.has(status)) {
            finish(status)
            return
          }
          if (status && successfulTerminal.has(status)) {
            finish(status)
            return
          }
          if (status && terminal.has(status)) {
            finish(status)
            return
          }

          const mission = this.missionStatus?.payload
          const missionEnvelopeRunId = String(this.missionStatus?.runId ?? mission?.runId ?? '').trim()
          const expectedRun = String(expectedRunId ?? '').trim()
          const missionApplies = !expectedRun || !missionEnvelopeRunId || missionEnvelopeRunId === expectedRun
          const state = String(mission?.state ?? '').trim().toUpperCase()
          const phase = String(mission?.phase ?? '').trim().toUpperCase()
          if (missionApplies && (runningStates.has(state) || runningPhases.has(phase))) {
            finish(state || phase)
          } else if (missionApplies && (successfulTerminal.has(state) || successfulTerminal.has(phase))) {
            finish(state || phase)
          } else if (Date.now() - startedAt >= timeoutMs) {
            finish('COMMAND_START_WAIT_TIMEOUT')
          } else {
            refreshFromBackend()
          }
        }, 100)
        refreshFromBackend()
      })
    },
    clear() {
      this.hydrating = false
      this.hydrated = false
      this.runId = ''
      this.streamSequences = {}
      this.poseBatch = null
      this.targetBatch = null
      this.missionStatus = null
      this.controlEvents = []
      this.commandStatuses = {}
      this.lastEnvelope = null
      this.lastError = ''
    },
  },
})

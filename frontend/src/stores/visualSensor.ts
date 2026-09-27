import { defineStore } from 'pinia'

import {
  fetchVisualSensorFrame,
  fetchVisualSensors,
  focusVisualSensor,
} from '@/api/visualSensor'
import type {
  UnityVisualSensorFrame,
  UnityVisualSensorMeta,
  UnityVisualSensorStreamStats,
  VisualSensor,
  VisualSensorOverview,
  VisualSensorRuntimeContext,
  VisualSensorRuntimeScope,
  VisualSensorViewType,
} from '@/types/visualSensor'

const UNITY_FRAME_FRESH_MS = 15_000
const UNITY_STREAM_FRESH_MS = 4_000
const BACKEND_FRAME_FRESH_MS = 4_000
const FRAME_STREAM_RECONNECT_MS = 2_000

const SENSOR_CATALOG: Array<{
  cameraId: string
  deviceCode: string
  deviceType: 'UAV' | 'USV'
  viewType: VisualSensorViewType
  displayName: string
}> = [
  { cameraId: 'uav_01', deviceCode: 'UAV-01', deviceType: 'UAV', viewType: 'DOWN', displayName: 'UAV-01 · 下视相机' },
  { cameraId: 'uav_02', deviceCode: 'UAV-02', deviceType: 'UAV', viewType: 'DOWN', displayName: 'UAV-02 · 下视相机' },
  { cameraId: 'uav_03', deviceCode: 'UAV-03', deviceType: 'UAV', viewType: 'DOWN', displayName: 'UAV-03 · 下视相机' },
  { cameraId: 'usv_01', deviceCode: 'USV-01', deviceType: 'USV', viewType: 'FORWARD', displayName: 'USV-01 · 前视相机' },
  { cameraId: 'usv_02', deviceCode: 'USV-02', deviceType: 'USV', viewType: 'FORWARD', displayName: 'USV-02 · 前视相机' },
  { cameraId: 'usv_03', deviceCode: 'USV-03', deviceType: 'USV', viewType: 'FORWARD', displayName: 'USV-03 · 前视相机' },
]

interface VisualSensorChannelState {
  generation: number
  overview: VisualSensorOverview | null
  frameUrls: Record<string, string>
  frameReceivedAtMs: Record<string, number>
  unityFrames: Record<string, UnityVisualSensorMeta>
  streamStats: UnityVisualSensorStreamStats | null
  unityBridgeReady: boolean
  framesRefreshing: boolean
  unityFocusedCameraId: string
  context: VisualSensorRuntimeContext
}

interface VisualSensorState {
  channels: Record<VisualSensorRuntimeScope, VisualSensorChannelState>
  loading: boolean
  error: string
  frameStreamConnected: boolean
}

let frameStreamSocket: WebSocket | null = null
let frameStreamReconnectTimer: number | undefined
let frameStreamManualClose = false

function fallbackOverview(focusedCameraId = 'uav_01'): VisualSensorOverview {
  return {
    gatewayConnected: false,
    gatewayDetail: '等待 Unity WebGL 视觉桥',
    onlineCount: 0,
    totalCount: SENSOR_CATALOG.length,
    focusedCameraId,
    sensors: SENSOR_CATALOG.map((sensor) => ({
      ...sensor,
      status: 'WAITING',
      source: 'Unity WebGL',
      width: 0,
      height: 0,
      fps: 0,
      latencyMs: -1,
      timestampMs: 0,
      focused: sensor.cameraId === focusedCameraId,
    })),
  }
}

function createContext(runtimeScope: VisualSensorRuntimeScope): VisualSensorRuntimeContext {
  return {
    runtimeScope,
    runtimeInstanceId: runtimeScope === 'SYSTEM_OVERVIEW' ? 'overview-unity-01' : '',
    missionId: null,
    runId: null,
  }
}

function createChannel(runtimeScope: VisualSensorRuntimeScope): VisualSensorChannelState {
  return {
    generation: 0,
    overview: null,
    frameUrls: {},
    frameReceivedAtMs: {},
    unityFrames: {},
    streamStats: null,
    unityBridgeReady: false,
    framesRefreshing: false,
    unityFocusedCameraId: 'uav_01',
    context: createContext(runtimeScope),
  }
}

function decodeJpeg(jpegBase64: string) {
  const binary = window.atob(jpegBase64)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  return new Blob([bytes], { type: 'image/jpeg' })
}

function releaseChannelFrames(channel: VisualSensorChannelState) {
  channel.generation += 1
  channel.framesRefreshing = false
  Object.values(channel.frameUrls).forEach((url) => URL.revokeObjectURL(url))
  channel.frameUrls = {}
  channel.frameReceivedAtMs = {}
  channel.unityFrames = {}
  channel.streamStats = null
}

function frameStreamUrl() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/api/visual-sensors/stream`
}

function contextKey(context: VisualSensorRuntimeContext) {
  return [
    context.runtimeScope,
    context.runtimeInstanceId,
    context.missionId ?? '',
    context.runId ?? '',
  ].join(':')
}

function payloadMatchesContext(
  channel: VisualSensorChannelState,
  payload: Record<string, unknown>,
) {
  if (channel.context.runtimeScope === 'SYSTEM_OVERVIEW') return true
  const payloadInstanceId = String(payload.runtimeInstanceId ?? '')
  if (
    payloadInstanceId
    && channel.context.runtimeInstanceId
    && payloadInstanceId !== channel.context.runtimeInstanceId
  ) return false
  const payloadRunId = payload.runId
  if (
    channel.context.runId !== null
    && payloadRunId !== undefined
    && payloadRunId !== null
    && String(payloadRunId) !== ''
    && Number(payloadRunId) !== channel.context.runId
  ) return false
  return true
}

function buildDisplayOverview(channel: VisualSensorChannelState): VisualSensorOverview {
  const base = channel.overview ?? fallbackOverview(channel.unityFocusedCameraId)
  const fallback = fallbackOverview(channel.unityFocusedCameraId)
  const backendSensors = new Map(base.sensors.map((sensor) => [sensor.cameraId, sensor]))
  const now = Date.now()
  const directStream = channel.streamStats
  const directStreamFresh = !!directStream
    && channel.unityBridgeReady
    && directStream.active
    && directStream.gpuDirect
    && now - directStream.receivedAtMs <= UNITY_STREAM_FRESH_MS
  const sensors = fallback.sensors.map((fallbackSensor): VisualSensor => {
    const sensor = {
      ...fallbackSensor,
      ...backendSensors.get(fallbackSensor.cameraId),
    }
    if (
      directStreamFresh
      && (directStream.displayMode === 'grid' || sensor.cameraId === directStream.focusedCameraId)
    ) {
      return {
        ...sensor,
        status: 'ONLINE',
        source: 'Unity WebGL GPU Direct',
        width: directStream.streamWidth,
        height: directStream.streamHeight,
        fps: directStream.measuredFps || directStream.targetFps,
        latencyMs: Math.max(0, Math.round(directStream.renderMs)),
        timestampMs: directStream.timestampMs,
        focused: sensor.cameraId === channel.unityFocusedCameraId,
      }
    }
    const unity = channel.unityFrames[sensor.cameraId]
    const fresh = !!unity && now - unity.receivedAtMs <= UNITY_FRAME_FRESH_MS
    if (!fresh) {
      return {
        ...sensor,
        focused: sensor.cameraId === channel.unityFocusedCameraId,
      }
    }
    return {
      ...sensor,
      status: 'ONLINE',
      source: unity.source,
      width: unity.width,
      height: unity.height,
      fps: unity.fps,
      latencyMs: Math.max(0, now - unity.timestampMs),
      timestampMs: unity.timestampMs,
      focused: sensor.cameraId === channel.unityFocusedCameraId,
    }
  })
  const unityOnline = sensors.filter((sensor) => sensor.status === 'ONLINE').length
  return {
    ...base,
    gatewayConnected: channel.unityBridgeReady || base.gatewayConnected,
    gatewayDetail: directStreamFresh
      ? `Unity GPU 六路直出 · ${directStream.activeQuality.toUpperCase()}`
      : channel.unityBridgeReady
        ? 'Unity WebGL 六路视觉桥在线'
        : base.gatewayConnected
          ? base.gatewayDetail
          : 'Unity WebGL 设备相机初始化中',
    onlineCount: Math.max(base.onlineCount, unityOnline),
    totalCount: SENSOR_CATALOG.length,
    focusedCameraId: channel.unityFocusedCameraId,
    sensors,
  }
}

export const useVisualSensorStore = defineStore('visual-sensor', {
  state: (): VisualSensorState => ({
    channels: {
      SYSTEM_OVERVIEW: createChannel('SYSTEM_OVERVIEW'),
      MISSION_CENTER: createChannel('MISSION_CENTER'),
      VIRTUAL_FLEET: createChannel('VIRTUAL_FLEET'),
    },
    loading: false,
    error: '',
    frameStreamConnected: false,
  }),
  getters: {
    displayOverview: (state) => buildDisplayOverview(state.channels.SYSTEM_OVERVIEW),
    streamStats: (state) => state.channels.SYSTEM_OVERVIEW.streamStats,
    unityBridgeReady: (state) => state.channels.SYSTEM_OVERVIEW.unityBridgeReady,
    displayOverviewFor: (state) => (scope: VisualSensorRuntimeScope) =>
      buildDisplayOverview(state.channels[scope]),
    streamStatsFor: (state) => (scope: VisualSensorRuntimeScope) =>
      state.channels[scope].streamStats,
    unityBridgeReadyFor: (state) => (scope: VisualSensorRuntimeScope) =>
      state.channels[scope].unityBridgeReady,
    runtimeContextFor: (state) => (scope: VisualSensorRuntimeScope) =>
      state.channels[scope].context,
  },
  actions: {
    bindRuntime(context: VisualSensorRuntimeContext) {
      const channel = this.channels[context.runtimeScope]
      if (contextKey(channel.context) === contextKey(context)) return
      releaseChannelFrames(channel)
      channel.overview = context.runtimeScope === 'SYSTEM_OVERVIEW' ? channel.overview : null
      channel.unityFocusedCameraId = 'uav_01'
      channel.context = { ...context }
    },
    async refreshOverview() {
      if (this.loading) return
      const channel = this.channels.SYSTEM_OVERVIEW
      const generation = channel.generation
      this.loading = true
      try {
        const overview = await fetchVisualSensors()
        if (generation !== channel.generation) return
        channel.overview = overview
        this.error = ''
      } catch (error) {
        if (generation !== channel.generation) return
        channel.overview = fallbackOverview(channel.unityFocusedCameraId)
        this.error = error instanceof Error ? error.message : '视频状态加载失败'
      } finally {
        if (generation === channel.generation) this.loading = false
      }
    },
    async select(cameraId: string) {
      return this.selectFor('SYSTEM_OVERVIEW', cameraId)
    },
    async selectFor(scope: VisualSensorRuntimeScope, cameraId: string) {
      const channel = this.channels[scope]
      channel.unityFocusedCameraId = cameraId
      if (scope !== 'SYSTEM_OVERVIEW') return
      const generation = channel.generation
      try {
        const overview = await focusVisualSensor(cameraId)
        if (generation !== channel.generation || channel.unityFocusedCameraId !== cameraId) return
        channel.overview = overview
        this.error = ''
      } catch {
        // Keep the local Unity selection; backend focus is only a fallback.
      }
    },
    markUnityBridgeReady(
      scope: VisualSensorRuntimeScope,
      ready = true,
      context?: VisualSensorRuntimeContext,
    ) {
      if (context) this.bindRuntime(context)
      const channel = this.channels[scope]
      channel.unityBridgeReady = ready
      if (ready) this.error = ''
    },
    connectFrameStream() {
      if (typeof window === 'undefined') return
      if (
        frameStreamSocket
        && (
          frameStreamSocket.readyState === WebSocket.OPEN
          || frameStreamSocket.readyState === WebSocket.CONNECTING
        )
      ) return
      frameStreamManualClose = false
      if (frameStreamReconnectTimer) {
        window.clearTimeout(frameStreamReconnectTimer)
        frameStreamReconnectTimer = undefined
      }
      const socket = new WebSocket(frameStreamUrl())
      frameStreamSocket = socket
      socket.onopen = () => {
        if (frameStreamSocket !== socket) return
        this.frameStreamConnected = true
        this.error = ''
      }
      socket.onmessage = (event) => {
        if (frameStreamSocket !== socket) return
        try {
          const payload = JSON.parse(String(event.data)) as Record<string, unknown>
          if (payload.type !== 'visualSensorFrame') return
          this.ingestBackendFrame(payload)
        } catch {
          // Ignore malformed stream frames and keep HTTP fallback available.
        }
      }
      socket.onerror = () => {
        if (frameStreamSocket === socket) this.frameStreamConnected = false
      }
      socket.onclose = () => {
        if (frameStreamSocket !== socket) return
        frameStreamSocket = null
        this.frameStreamConnected = false
        if (frameStreamManualClose) return
        frameStreamReconnectTimer = window.setTimeout(() => {
          frameStreamReconnectTimer = undefined
          this.connectFrameStream()
        }, FRAME_STREAM_RECONNECT_MS)
      }
    },
    disconnectFrameStream() {
      frameStreamManualClose = true
      if (frameStreamReconnectTimer) {
        window.clearTimeout(frameStreamReconnectTimer)
        frameStreamReconnectTimer = undefined
      }
      const socket = frameStreamSocket
      frameStreamSocket = null
      this.frameStreamConnected = false
      if (socket && socket.readyState !== WebSocket.CLOSED) {
        socket.close()
      }
    },
    ingestBackendFrame(payload: Record<string, unknown>) {
      const channel = this.channels.SYSTEM_OVERVIEW
      const frame = payload as unknown as UnityVisualSensorFrame
      if (!SENSOR_CATALOG.some((sensor) => sensor.cameraId === frame.cameraId)) return
      if (!frame.jpegBase64) return
      try {
        const now = Date.now()
        const previous = channel.unityFrames[frame.cameraId]
        const interval = previous ? now - previous.receivedAtMs : 0
        const instantFps = interval > 0 ? 1_000 / interval : 0
        const fps = previous?.fps
          ? previous.fps * 0.72 + instantFps * 0.28
          : instantFps
        const nextUrl = URL.createObjectURL(decodeJpeg(frame.jpegBase64))
        const previousUrl = channel.frameUrls[frame.cameraId]
        channel.frameUrls[frame.cameraId] = nextUrl
        channel.frameReceivedAtMs[frame.cameraId] = now
        if (previousUrl) URL.revokeObjectURL(previousUrl)
        channel.unityFrames[frame.cameraId] = {
          width: Number(frame.width) || 0,
          height: Number(frame.height) || 0,
          fps,
          timestampMs: Number(frame.timestampMs) || now,
          receivedAtMs: now,
          sequence: Number(frame.sequence) || 0,
          source: frame.source || 'ROS Gateway v1 media.camera_jpeg',
        }
        this.frameStreamConnected = true
        this.error = ''
      } catch {
        // Keep the last valid stream or HTTP frame during a malformed update.
      }
    },
    ingestUnityFrame(
      scope: VisualSensorRuntimeScope,
      payload: Record<string, unknown>,
      context?: VisualSensorRuntimeContext,
    ) {
      if (context) this.bindRuntime(context)
      const channel = this.channels[scope]
      if (!payloadMatchesContext(channel, payload)) return
      const frame = payload as unknown as UnityVisualSensorFrame
      if (!SENSOR_CATALOG.some((sensor) => sensor.cameraId === frame.cameraId)) return
      if (!frame.jpegBase64 || frame.width <= 0 || frame.height <= 0) return
      try {
        const now = Date.now()
        const previous = channel.unityFrames[frame.cameraId]
        const interval = previous ? now - previous.receivedAtMs : 0
        const instantFps = interval > 0 ? 1_000 / interval : 0
        const fps = previous?.fps
          ? previous.fps * 0.72 + instantFps * 0.28
          : instantFps
        const nextUrl = URL.createObjectURL(decodeJpeg(frame.jpegBase64))
        const previousUrl = channel.frameUrls[frame.cameraId]
        channel.frameUrls[frame.cameraId] = nextUrl
        if (previousUrl) URL.revokeObjectURL(previousUrl)
        channel.unityFrames[frame.cameraId] = {
          width: frame.width,
          height: frame.height,
          fps,
          timestampMs: frame.timestampMs || now,
          receivedAtMs: now,
          sequence: frame.sequence,
          source: frame.source || 'Unity WebGL',
        }
        channel.unityBridgeReady = true
        this.error = ''
      } catch {
        // Ignore a malformed frame and keep the last valid Unity image.
      }
    },
    ingestUnityStreamStats(
      scope: VisualSensorRuntimeScope,
      payload: Record<string, unknown>,
      context?: VisualSensorRuntimeContext,
    ) {
      if (context) this.bindRuntime(context)
      const channel = this.channels[scope]
      if (!payloadMatchesContext(channel, payload)) return
      const stats = payload as unknown as Omit<UnityVisualSensorStreamStats, 'receivedAtMs'>
      if (!stats || typeof stats.active !== 'boolean') return
      channel.streamStats = {
        ...stats,
        receivedAtMs: Date.now(),
      }
      if (stats.focusedCameraId) channel.unityFocusedCameraId = stats.focusedCameraId
      channel.unityBridgeReady = stats.gpuDirect === true || channel.unityBridgeReady
      this.error = ''
    },
    hasFreshUnityFrame(cameraId: string, scope: VisualSensorRuntimeScope = 'SYSTEM_OVERVIEW') {
      const frame = this.channels[scope].unityFrames[cameraId]
      return !!frame && Date.now() - frame.receivedAtMs <= UNITY_FRAME_FRESH_MS
    },
    hasFreshBackendFrame(cameraId: string, scope: VisualSensorRuntimeScope = 'SYSTEM_OVERVIEW') {
      const receivedAtMs = this.channels[scope].frameReceivedAtMs[cameraId] ?? 0
      return receivedAtMs > 0 && Date.now() - receivedAtMs <= BACKEND_FRAME_FRESH_MS
    },
    async refreshFrames(
      focusedOnly = false,
      scope: VisualSensorRuntimeScope = 'SYSTEM_OVERVIEW',
    ) {
      // Mission-center vision is always read from its own live Unity canvas.
      // The global backend JPEG endpoint is not run-scoped and must not be used.
      if (scope !== 'SYSTEM_OVERVIEW') return
      const channel = this.channels[scope]
      if (channel.framesRefreshing) return
      channel.framesRefreshing = true
      const generation = channel.generation
      const sensors = buildDisplayOverview(channel).sensors
      const targets = focusedOnly
        ? sensors.filter((sensor) => sensor.cameraId === channel.unityFocusedCameraId)
        : sensors
      try {
        await Promise.all(targets.map(async (sensor) => {
          if (
            this.hasFreshUnityFrame(sensor.cameraId, scope)
            || this.hasFreshBackendFrame(sensor.cameraId, scope)
          ) return
          try {
            const blob = await fetchVisualSensorFrame(sensor.cameraId)
            if (!blob || generation !== channel.generation) return
            const nextUrl = URL.createObjectURL(blob)
            const previousUrl = channel.frameUrls[sensor.cameraId]
            channel.frameUrls[sensor.cameraId] = nextUrl
            channel.frameReceivedAtMs[sensor.cameraId] = Date.now()
            if (previousUrl) URL.revokeObjectURL(previousUrl)
          } catch {
            // Keep the last valid Unity or ROS frame during a short interruption.
          }
        }))
      } finally {
        if (generation === channel.generation) channel.framesRefreshing = false
      }
    },
    disposeFrames(scope: VisualSensorRuntimeScope = 'SYSTEM_OVERVIEW') {
      const channel = this.channels[scope]
      releaseChannelFrames(channel)
      if (scope === 'SYSTEM_OVERVIEW') this.loading = false
      channel.unityBridgeReady = false
    },
    clearSession() {
      this.disconnectFrameStream()
      for (const scope of ['SYSTEM_OVERVIEW', 'MISSION_CENTER', 'VIRTUAL_FLEET'] as const) {
        this.disposeFrames(scope)
        this.channels[scope].overview = null
      }
      this.error = ''
    },
  },
})

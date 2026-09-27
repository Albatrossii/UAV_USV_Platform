import { defineStore } from 'pinia'

import { acknowledgeRuntimeCommand } from '@/api/runtimeControl'
import type { RuntimeCommandStatus } from '@/api/runtimeControl'

export type UnityRuntimeScope = 'SYSTEM_OVERVIEW' | 'MISSION_CENTER' | 'VIRTUAL_FLEET'

export interface UnityBridgeMessage {
  type: string
  requestId: string
  timestamp: number
  payload: Record<string, unknown>
}

export interface UnityCommandAckResult {
  requestId: string
  success: boolean
  status: string
  deviceCode: string
  commandType: string
  backendStatus?: RuntimeCommandStatus
}

interface UnityBridgeChannel {
  connected: boolean
  controlsReady: boolean
  platformReady: boolean
  cameraReady: boolean
  algorithmReady: boolean
  visualSensorReady: boolean
  buildId: string
  capabilities: string[]
  lastMessage: UnityBridgeMessage | null
  lastOutgoing: UnityBridgeMessage | null
  error: string
  trajectoryVisible: boolean
  trajectoryTogglePending: boolean
  outbox: UnityBridgeMessage[]
  commandKeys: Record<string, string>
  scenarioRunId: number | null
  scenarioAlgorithmCode: string
  scenarioReadyRunId: number | null
  scenarioReadyAlgorithmCode: string
  sentRunId: string | null
  sentSequence: number
  appliedRunId: number | null
  appliedSequence: number
  rejectReason: string
  latestPoseFrame: UnityBridgeMessage | null
}

type PendingCommandAck = {
  resolve: (result: UnityCommandAckResult) => void
  reject: (error: Error) => void
  timer: number
}

const pendingCommandAcks = new Map<string, PendingCommandAck>()

function createChannel(): UnityBridgeChannel {
  return {
    connected: false,
    controlsReady: false,
    platformReady: false,
    cameraReady: false,
    algorithmReady: false,
    visualSensorReady: false,
    buildId: '',
    capabilities: [],
    lastMessage: null,
    lastOutgoing: null,
    error: '',
    trajectoryVisible: false,
    trajectoryTogglePending: false,
    outbox: [],
    commandKeys: {},
    scenarioRunId: null,
    scenarioAlgorithmCode: '',
    scenarioReadyRunId: null,
    scenarioReadyAlgorithmCode: '',
    sentRunId: null,
    sentSequence: 0,
    appliedRunId: null,
    appliedSequence: 0,
    rejectReason: '',
    latestPoseFrame: null,
  }
}

function pendingKey(scope: UnityRuntimeScope, requestId: string) {
  return `${scope}:${requestId}`
}

function removePendingCommandAck(scope: UnityRuntimeScope, requestId: string) {
  const key = pendingKey(scope, requestId)
  const pending = pendingCommandAcks.get(key)
  if (!pending) return undefined
  window.clearTimeout(pending.timer)
  pendingCommandAcks.delete(key)
  return pending
}

function rejectPendingCommandAcks(scope: UnityRuntimeScope, message: string) {
  const prefix = `${scope}:`
  for (const [key, pending] of pendingCommandAcks) {
    if (!key.startsWith(prefix)) continue
    window.clearTimeout(pending.timer)
    pending.reject(new Error(message))
    pendingCommandAcks.delete(key)
  }
}

export const useUnityBridgeStore = defineStore('unityBridge', {
  state: () => ({
    channels: {
      SYSTEM_OVERVIEW: createChannel(),
      MISSION_CENTER: createChannel(),
      VIRTUAL_FLEET: createChannel(),
    } as Record<UnityRuntimeScope, UnityBridgeChannel>,
  }),
  getters: {
    connected: (state) => state.channels.SYSTEM_OVERVIEW.connected,
    lastMessage: (state) => state.channels.SYSTEM_OVERVIEW.lastMessage,
    lastOutgoing: (state) => state.channels.SYSTEM_OVERVIEW.lastOutgoing,
    error: (state) => state.channels.SYSTEM_OVERVIEW.error,
    trajectoryVisible: (state) => state.channels.SYSTEM_OVERVIEW.trajectoryVisible,
    trajectoryTogglePending: (state) => state.channels.SYSTEM_OVERVIEW.trajectoryTogglePending,
    outbox: (state) => state.channels.SYSTEM_OVERVIEW.outbox,
    channel: (state) => (scope: UnityRuntimeScope) => state.channels[scope],
  },
  actions: {
    setConnectedFor(scope: UnityRuntimeScope, connected: boolean) {
      const channel = this.channels[scope]
      channel.connected = connected
      if (connected) channel.error = ''
      else {
        channel.controlsReady = false
        channel.platformReady = false
        channel.cameraReady = false
        channel.algorithmReady = false
        channel.visualSensorReady = false
        channel.buildId = ''
        channel.capabilities = []
        channel.outbox = []
        channel.commandKeys = {}
        channel.scenarioRunId = null
        channel.scenarioAlgorithmCode = ''
        channel.scenarioReadyRunId = null
        channel.scenarioReadyAlgorithmCode = ''
        channel.sentRunId = null
        channel.sentSequence = 0
        channel.appliedRunId = null
        channel.appliedSequence = 0
        channel.rejectReason = ''
        channel.latestPoseFrame = null
        rejectPendingCommandAcks(scope, 'Unity WebGL 连接已断开')
      }
    },
    setConnected(connected: boolean) {
      this.setConnectedFor('SYSTEM_OVERVIEW', connected)
    },
    setErrorFor(scope: UnityRuntimeScope, message: string) {
      const channel = this.channels[scope]
      channel.error = message
      channel.connected = false
      channel.controlsReady = false
      channel.platformReady = false
      channel.cameraReady = false
      channel.outbox = []
      channel.commandKeys = {}
      channel.scenarioRunId = null
      channel.scenarioAlgorithmCode = ''
      channel.scenarioReadyRunId = null
      channel.scenarioReadyAlgorithmCode = ''
      channel.sentRunId = null
      channel.sentSequence = 0
      channel.appliedRunId = null
      channel.appliedSequence = 0
      channel.rejectReason = ''
      channel.latestPoseFrame = null
      rejectPendingCommandAcks(scope, message)
    },
    setControlsReadyFor(scope: UnityRuntimeScope, ready: boolean) {
      this.channels[scope].controlsReady = ready
    },
    setPlatformCapabilitiesFor(scope: UnityRuntimeScope, payload: Record<string, unknown>) {
      const channel = this.channels[scope]
      channel.platformReady = payload.ready === true
      channel.controlsReady = payload.controlsReady === true
      channel.cameraReady = payload.cameraReady === true
      channel.algorithmReady = payload.algorithmReady === true
      channel.visualSensorReady = payload.visualSensorReady === true
      channel.buildId = String(payload.buildId ?? '')
      channel.capabilities = Array.isArray(payload.capabilities)
        ? payload.capabilities.map(item => String(item))
        : []
      const reportedError = String(payload.error ?? '').trim()
      if (channel.platformReady) {
        channel.error = ''
      } else if (reportedError) {
        channel.error = reportedError
      }
    },
    setError(message: string) {
      this.setErrorFor('SYSTEM_OVERVIEW', message)
    },
    noteMessageFor(scope: UnityRuntimeScope, message: UnityBridgeMessage) {
      this.channels[scope].lastMessage = message
    },
    noteMessage(message: UnityBridgeMessage) {
      this.noteMessageFor('SYSTEM_OVERVIEW', message)
    },
    noteOutgoingFor(scope: UnityRuntimeScope, message: UnityBridgeMessage) {
      this.channels[scope].lastOutgoing = message
    },
    noteOutgoing(message: UnityBridgeMessage) {
      this.noteOutgoingFor('SYSTEM_OVERVIEW', message)
    },
    setTrajectoryVisibilityFor(scope: UnityRuntimeScope, visible: boolean) {
      const channel = this.channels[scope]
      channel.trajectoryVisible = visible
      channel.trajectoryTogglePending = false
    },
    setTrajectoryVisibility(visible: boolean) {
      this.setTrajectoryVisibilityFor('SYSTEM_OVERVIEW', visible)
    },
    setTrajectoryTogglePendingFor(scope: UnityRuntimeScope, pending: boolean) {
      this.channels[scope].trajectoryTogglePending = pending
    },
    setTrajectoryTogglePending(pending: boolean) {
      this.setTrajectoryTogglePendingFor('SYSTEM_OVERVIEW', pending)
    },
    markScenarioReadyFor(
      scope: UnityRuntimeScope,
      payload: Record<string, unknown>,
    ) {
      const channel = this.channels[scope]
      const wireRunId = String(payload.runId ?? '')
      const runId = Number(wireRunId)
      if (!wireRunId || !Number.isSafeInteger(runId) || runId <= 0) {
        channel.rejectReason = `scenarioReady invalid runId: ${String(payload.runId ?? '')}`
        return
      }
      if (channel.scenarioRunId !== runId) {
        channel.rejectReason = `scenarioReady run mismatch: expected=${channel.scenarioRunId ?? 'none'}, received=${runId}`
        console.warn('[unityBridge] scenarioReady rejected', { scope, rejectReason: channel.rejectReason })
        return
      }
      channel.scenarioReadyRunId = runId
      channel.scenarioReadyAlgorithmCode = String(
        payload.algorithmCode ?? channel.scenarioAlgorithmCode,
      )
      channel.appliedRunId = null
      channel.appliedSequence = 0
      channel.sentRunId = null
      channel.sentSequence = 0
      const latestPose = channel.latestPoseFrame
      const latestRunId = String(latestPose?.payload.runId ?? '')
      if (latestPose && latestRunId === wireRunId) {
        channel.outbox = channel.outbox.filter(message => message.type !== 'poseFrame')
        channel.outbox.push(latestPose)
      }
    },
    markPoseAppliedFor(
      scope: UnityRuntimeScope,
      payload: Record<string, unknown>,
    ) {
      const channel = this.channels[scope]
      const runId = String(payload.runId ?? '')
      const sequence = Number(payload.sequence)
      const appliedCount = Number(payload.appliedCount)
      if (
        payload.success !== true
        || !runId
        || !Number.isSafeInteger(sequence)
        || sequence <= 0
        || appliedCount <= 0
        || channel.sentRunId !== runId
        || sequence > channel.sentSequence
      ) {
        channel.rejectReason = `poseFrameApplied invalid receipt: runId=${String(payload.runId ?? '')}, sequence=${String(payload.sequence ?? '')}`
        console.warn('[unityBridge] poseFrameApplied rejected', { scope, rejectReason: channel.rejectReason })
        return
      }
      channel.appliedRunId = Number(runId)
      channel.appliedSequence = Math.max(channel.appliedSequence, sequence)
    },
    markPoseSentFor(scope: UnityRuntimeScope, payload: Record<string, unknown>) {
      const runId = String(payload.runId ?? '')
      const sequence = Number(payload.sequence)
      if (!runId || !Number.isSafeInteger(sequence) || sequence <= 0) return
      const channel = this.channels[scope]
      channel.sentRunId = runId
      channel.sentSequence = sequence
    },
    clearPoseFramesFor(scope: UnityRuntimeScope) {
      const channel = this.channels[scope]
      channel.outbox = channel.outbox.filter(message => message.type !== 'poseFrame')
    },
    sendFor(
      scope: UnityRuntimeScope,
      type: string,
      payload: Record<string, unknown> = {},
      commandKey = '',
    ) {
      const requestId = `${type}:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`
      const channel = this.channels[scope]
      if (commandKey) channel.commandKeys[requestId] = commandKey
      const message = { type, requestId, timestamp: Date.now(), payload }
      if (type === 'loadScenario') {
        const runId = Number(payload.runId)
        channel.scenarioRunId = Number.isSafeInteger(runId) && runId > 0 ? runId : null
        channel.scenarioAlgorithmCode = String(payload.algorithmCode ?? '')
        channel.scenarioReadyRunId = null
        channel.scenarioReadyAlgorithmCode = ''
        channel.sentRunId = null
        channel.sentSequence = 0
        channel.appliedRunId = null
        channel.appliedSequence = 0
        channel.rejectReason = ''
        channel.latestPoseFrame = null
        channel.outbox = channel.outbox.filter(
          queued => queued.type !== 'loadScenario' && queued.type !== 'poseFrame',
        )
        // Scenario selection must reach Unity before any command retained in
        // the channel from the previous view state.
        channel.outbox.unshift(message)
        return requestId
      }
      if (type === 'poseFrame') {
        const runId = String(payload.runId ?? '')
        if (!runId) {
          channel.rejectReason = `poseFrame invalid runId: ${String(payload.runId ?? '')}`
          console.warn('[unityBridge] poseFrame rejected', { scope, rejectReason: channel.rejectReason })
          return requestId
        }
        if (String(channel.scenarioRunId ?? '') !== runId) {
          channel.rejectReason = `poseFrame run mismatch: expected=${channel.scenarioRunId ?? 'none'}, received=${runId}`
          console.warn('[unityBridge] poseFrame rejected', { scope, rejectReason: channel.rejectReason })
          return requestId
        }
        channel.latestPoseFrame = message
        // A disconnected/hidden WebGL instance only needs the newest pose.
        // Keeping every 10 Hz sample caused a synchronous burst and the
        // apparent "teleport then freeze" behaviour after returning to 3-D.
        channel.outbox = channel.outbox.filter(
          queued => queued.type !== 'poseFrame',
        )
        if (String(channel.scenarioReadyRunId ?? '') !== runId) return requestId
      }
      if (type === 'spectrumFrame') {
        // A hidden or loading WebGL runtime only needs the newest SAN60 frame.
        channel.outbox = channel.outbox.filter(
          queued => queued.type !== 'spectrumFrame',
        )
      }
      channel.outbox.push(message)
      return requestId
    },
    send(type: string, payload: Record<string, unknown> = {}, commandKey = '') {
      return this.sendFor('SYSTEM_OVERVIEW', type, payload, commandKey)
    },
    sendControlCommand(command: string, deviceCode: string, commandKey: string) {
      return this.sendFor('SYSTEM_OVERVIEW', 'sendControlCommand', { command, deviceCode }, commandKey)
    },
    sendAndWaitFor(
      scope: UnityRuntimeScope,
      type: string,
      payload: Record<string, unknown>,
      commandKey: string,
      timeoutMs = 15000,
    ): Promise<UnityCommandAckResult> {
      const requestId = `${type}:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`
      const channel = this.channels[scope]
      return new Promise((resolve, reject) => {
        const timer = window.setTimeout(() => {
          pendingCommandAcks.delete(pendingKey(scope, requestId))
          delete channel.commandKeys[requestId]
          void acknowledgeRuntimeCommand(commandKey, false, `Unity 指令确认超时：${type}`).catch(() => undefined)
          reject(new Error(`Unity 指令确认超时：${type}`))
        }, timeoutMs)
        pendingCommandAcks.set(pendingKey(scope, requestId), { resolve, reject, timer })
        channel.commandKeys[requestId] = commandKey
        channel.outbox.push({ type, requestId, timestamp: Date.now(), payload })
      })
    },
    sendAndWait(
      type: string,
      payload: Record<string, unknown>,
      commandKey: string,
      timeoutMs = 15000,
    ) {
      return this.sendAndWaitFor('SYSTEM_OVERVIEW', type, payload, commandKey, timeoutMs)
    },
    sendControlCommandAndWaitFor(
      scope: UnityRuntimeScope,
      command: string,
      deviceCode: string,
      commandKey: string,
      timeoutMs = 15000,
    ) {
      return this.sendAndWaitFor(scope, 'sendControlCommand', { command, deviceCode }, commandKey, timeoutMs)
    },
    sendControlCommandAndWait(
      command: string,
      deviceCode: string,
      commandKey: string,
      timeoutMs = 15000,
    ) {
      return this.sendControlCommandAndWaitFor('SYSTEM_OVERVIEW', command, deviceCode, commandKey, timeoutMs)
    },
    peekNextFor(scope: UnityRuntimeScope) {
      return this.channels[scope].outbox[0] ?? null
    },
    peekNext() {
      return this.peekNextFor('SYSTEM_OVERVIEW')
    },
    removeNextFor(scope: UnityRuntimeScope) {
      this.channels[scope].outbox.shift()
    },
    removeNext() {
      this.removeNextFor('SYSTEM_OVERVIEW')
    },
    async handleCommandAckFor(
      scope: UnityRuntimeScope,
      requestId: string,
      payload: Record<string, unknown>,
    ) {
      const channel = this.channels[scope]
      const commandKey = channel.commandKeys[requestId]
      if (!commandKey) return undefined
      delete channel.commandKeys[requestId]
      const success = payload.success === true
      const detail = String(payload.status ?? (success ? 'Unity 已执行' : 'Unity 执行失败'))
      try {
        const command = await acknowledgeRuntimeCommand(commandKey, success, detail)
        removePendingCommandAck(scope, requestId)?.resolve({
          requestId,
          success: success && command.status === 'SUCCEEDED',
          status: detail,
          deviceCode: String(payload.deviceCode ?? ''),
          commandType: String(payload.commandType ?? ''),
          backendStatus: command.status,
        })
        return command
      } catch (error) {
        const reason = error instanceof Error ? error : new Error('后端未能确认 Unity 指令')
        removePendingCommandAck(scope, requestId)?.reject(reason)
        return undefined
      }
    },
    handleCommandAck(requestId: string, payload: Record<string, unknown>) {
      return this.handleCommandAckFor('SYSTEM_OVERVIEW', requestId, payload)
    },
  },
})

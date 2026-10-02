<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { sendIntegrationHeartbeat } from '@/api/integration'
import { useTrajectoryStore } from '@/stores/trajectory'
import { useUnityBridgeStore } from '@/stores/unityBridge'
import { useVisualSensorStore } from '@/stores/visualSensor'
import type { UnityBridgeMessage } from '@/stores/unityBridge'
import type { PointCloudLatest } from '@/types/sensor'
import {
  appendUnityRuntimeParams,
  cloneUnityPayload,
  parseUnityWindowMessage,
} from '@/utils/unityWebglProtocol'
import type { UnityWindowMessage } from '@/utils/unityWebglProtocol'

const RUNTIME_SCOPE = 'SYSTEM_OVERVIEW' as const
const RUNTIME_INSTANCE_ID = 'overview-unity-01'
const WEBGL_SOURCE = import.meta.env.VITE_OVERVIEW_WEBGL_URL ?? '/unity-overview-test/index.html?embedded=1'

const iframeRef = ref<HTMLIFrameElement | null>(null)
const panelRef = ref<HTMLElement | null>(null)
const bridge = useUnityBridgeStore()
const trajectory = useTrajectoryStore()
const visualSensor = useVisualSensorStore()
const loading = ref(true)
const ready = ref(false)
const controlsReady = ref(false)
const errorMessage = ref('')
const loadHint = ref('正在加载系统总览 Unity WebGL')
const reloadToken = ref(Date.now())
const pointCloudBridgeReady = ref(false)

let probeTimer: number | null = null
let heartbeatTimer: number | null = null
let readyEmitted = false
let heartbeatInFlight = false
let pendingHeartbeat: { state: 'ONLINE' | 'OFFLINE' | 'FAILED'; detail: string } | null = null
let latestPointCloudFrame: PointCloudLatest | null = null
let lastPointCloudSentAtMs = 0
let viewportResizeObserver: ResizeObserver | null = null
let lockedViewportWidth = 0
let lockedViewportHeight = 0
let viewportTransitionGeneration = 0

type UnityFrameWindow = Window & {
  uavUsvUnityInstance?: { Quit?: () => Promise<void> }
}

const emit = defineEmits<{
  unityReady: []
  unityMessage: [message: UnityWindowMessage]
  unityError: [message: string]
  unityCommand: [message: UnityBridgeMessage]
}>()

const iframeUrl = computed(() => appendUnityRuntimeParams(WEBGL_SOURCE, {
  v: reloadToken.value,
  scope: RUNTIME_SCOPE,
  instanceId: RUNTIME_INSTANCE_ID,
}))
const statusText = computed(() => ready.value ? 'UNITY WEBGL ONLINE' : 'WAITING FOR PLATFORM')
const visualContext = computed(() => ({
  runtimeScope: RUNTIME_SCOPE,
  runtimeInstanceId: RUNTIME_INSTANCE_ID,
  missionId: null,
  runId: null,
}))

async function reportHeartbeat(state: 'ONLINE' | 'OFFLINE' | 'FAILED', detail: string) {
  if (heartbeatInFlight) {
    pendingHeartbeat = { state, detail }
    return
  }
  heartbeatInFlight = true
  let current: typeof pendingHeartbeat = { state, detail }
  try {
    while (current) {
      try {
        await sendIntegrationHeartbeat({
          componentCode: 'unity-client-01',
          instanceId: RUNTIME_INSTANCE_ID,
          state: current.state,
          detail: current.detail,
          rosConnectionStatus: 'UNKNOWN',
          runtimeScope: RUNTIME_SCOPE,
          controlsReady: controlsReady.value,
          deviceCodes: trajectory.channels.SYSTEM_OVERVIEW.frame?.agents
            .filter(agent => agent.type === 'UAV' || agent.type === 'USV')
            .map(agent => agent.code.toLowerCase()) ?? [],
          trajectorySequence: trajectory.channels.SYSTEM_OVERVIEW.frame?.sequence,
        })
      } catch {
        // Backend heartbeat failure must not take down the local runtime.
      }
      current = pendingHeartbeat
      pendingHeartbeat = null
    }
  } finally {
    heartbeatInFlight = false
  }
}

function markReady(payload: Record<string, unknown>) {
  if (probeTimer !== null) {
    window.clearInterval(probeTimer)
    probeTimer = null
  }
  bridge.setPlatformCapabilitiesFor(RUNTIME_SCOPE, {
    ready: true,
    controlsReady: true,
    cameraReady: true,
    algorithmReady: true,
    visualSensorReady: payload.visualSensorReady === true,
    buildId: String(payload.buildId ?? 'unity-overview-platform'),
    capabilities: Array.isArray(payload.capabilities) ? payload.capabilities : [],
  })
  bridge.setConnectedFor(RUNTIME_SCOPE, true)
  controlsReady.value = true
  loading.value = false
  ready.value = true
  errorMessage.value = ''
  loadHint.value = '系统总览 Unity 平台已初始化'
  flushOutbox()
  void reportHeartbeat('ONLINE', 'SYSTEM_OVERVIEW Unity WebGL 已初始化')
  if (!readyEmitted) {
    readyEmitted = true
    emit('unityReady')
  }
  if (!pointCloudBridgeReady.value) publishPointCloudCapability(false)
  sendLatestPointCloudFrame()
}

function publishPointCloudCapability(supported: boolean, detail = '') {
  window.dispatchEvent(new CustomEvent('uav-usv:unity-pointcloud-capability', {
    detail: { supported, detail },
  }))
}

function sendLatestPointCloudFrame() {
  const value = latestPointCloudFrame
  const target = iframeRef.value?.contentWindow
  if (!value || !target || !ready.value || !pointCloudBridgeReady.value) return
  if (value.receivedAtMs <= lastPointCloudSentAtMs) return

  const requestId = `pointCloudFrame:${value.sequence ?? 'none'}:${value.receivedAtMs}`
  const message = {
    type: 'pointCloudFrame',
    requestId,
    timestamp: Date.now(),
    payload: {
      receivedAtMs: value.receivedAtMs,
      frame: JSON.parse(JSON.stringify(value.frame)),
    },
  }
  const summary = {
    type: message.type,
    requestId,
    timestamp: message.timestamp,
    payload: {
      streamId: value.streamId,
      sequence: value.sequence,
      pointCount: value.frame.data.point_count,
    },
  }
  target.postMessage({
    source: 'vue-console',
    runtimeScope: RUNTIME_SCOPE,
    runtimeInstanceId: RUNTIME_INSTANCE_ID,
    message,
  }, window.location.origin)
  bridge.noteOutgoingFor(RUNTIME_SCOPE, summary)
  emit('unityCommand', summary)
  lastPointCloudSentAtMs = value.receivedAtMs
}

function handlePointCloudFrame(event: Event) {
  const value = (event as CustomEvent<PointCloudLatest>).detail
  if (!value?.frame || value.streamId !== 'usv_01_mid360') return
  if (latestPointCloudFrame && value.receivedAtMs <= latestPointCloudFrame.receivedAtMs) return
  latestPointCloudFrame = value
  sendLatestPointCloudFrame()
}

function markError(message: string) {
  if (probeTimer !== null) {
    window.clearInterval(probeTimer)
    probeTimer = null
  }
  loading.value = false
  ready.value = false
  controlsReady.value = false
  errorMessage.value = message
  bridge.setErrorFor(RUNTIME_SCOPE, message)
  void reportHeartbeat('FAILED', message)
  emit('unityError', message)
}

function handleWindowMessage(event: MessageEvent) {
  if (event.source !== iframeRef.value?.contentWindow) return
  const message = parseUnityWindowMessage(event.data)
  if (!message) return

  if (message.type === 'unityProgress') {
    const progress = Number(message.payload?.progress ?? 0)
    if (Number.isFinite(progress) && progress > 0) {
      loadHint.value = `系统总览 Unity WebGL 加载中 ${(progress * 100).toFixed(0)}%`
    }
  } else if (message.type === 'unityError' || message.type === 'unityBridgeError') {
    markError(String(message.payload?.message ?? '系统总览 Unity WebGL 加载失败'))
  } else if (message.type === 'platformBridgeReady') {
    if (message.payload?.ready !== true) {
      markError('系统总览 Unity platformBridgeReady.ready 不是 true')
    } else {
      bridge.setPlatformCapabilitiesFor(RUNTIME_SCOPE, {
        ready: true,
        controlsReady: message.payload.controlsReady !== false,
        cameraReady: message.payload.cameraReady !== false,
        algorithmReady: message.payload.algorithmReady !== false,
        visualSensorReady: message.payload.visualSensorReady === true,
        buildId: String(message.payload.buildId ?? 'unity-overview-platform'),
        capabilities: Array.isArray(message.payload.capabilities)
          ? message.payload.capabilities
          : [],
      })
      bridge.setConnectedFor(RUNTIME_SCOPE, true)
      // The overview build is not usable until its wrapper reports that
      // InitializePlatform completed. A later compatibility-bridge readiness
      // event only enriches capabilities and must not reopen the loading mask.
      if (!ready.value) {
        loading.value = true
        loadHint.value = 'Unity 桥已连接，正在初始化系统总览平台'
      }
    }
  } else if (message.type === 'platformInitialized') {
    if (message.payload?.success !== true) {
      markError(String(message.payload?.error ?? '系统总览 InitializePlatform 失败'))
    } else {
      markReady(message.payload)
    }
  } else if (message.type === 'pointCloudBridgeReady') {
    pointCloudBridgeReady.value = message.payload?.ready === true
      && message.payload?.protocol === 'mid360-pointcloud-v1'
    publishPointCloudCapability(pointCloudBridgeReady.value, String(message.payload?.status ?? ''))
    if (pointCloudBridgeReady.value) sendLatestPointCloudFrame()
  } else if (message.type === 'pointCloudFrameApplied' && message.payload) {
    window.dispatchEvent(new CustomEvent('uav-usv:unity-pointcloud-ack', {
      detail: { ...message.payload, requestId: message.requestId ?? '' },
    }))
  } else if (message.type === 'trajectoryFrame' && message.payload) {
    trajectory.ingestFor(RUNTIME_SCOPE, message.payload)
  } else if (message.type === 'visualSensorBridgeReady') {
    visualSensor.markUnityBridgeReady(RUNTIME_SCOPE, message.payload?.ready === true, visualContext.value)
  } else if (message.type === 'visualSensorFrame' && message.payload) {
    visualSensor.ingestUnityFrame(RUNTIME_SCOPE, message.payload, visualContext.value)
  } else if (message.type === 'visualSensorStreamStats' && message.payload) {
    visualSensor.ingestUnityStreamStats(RUNTIME_SCOPE, message.payload, visualContext.value)
  } else if (message.type === 'trajectoryVisibilityChanged' && message.payload) {
    bridge.setTrajectoryVisibilityFor(RUNTIME_SCOPE, message.payload.visible === true)
  } else if (message.type === 'scenarioLoaded' && message.payload?.success === true) {
    bridge.markScenarioReadyFor(RUNTIME_SCOPE, {
      ...message.payload,
      algorithmCode: message.payload.algorithmCode ?? message.payload.scenarioId,
    })
  } else if (message.type === 'poseFrameApplied' && message.payload) {
    bridge.markPoseAppliedFor(RUNTIME_SCOPE, message.payload)
  }

  if (
    (message.type === 'commandAck' || message.type === 'cameraChanged'
      || message.type === 'trajectoryVisibilityChanged')
    && message.payload
  ) {
    void bridge.handleCommandAckFor(RUNTIME_SCOPE, message.requestId ?? '', message.payload)
  }
  const auditedPayload = message.type === 'visualSensorFrame'
    ? { ...message.payload, jpegBase64: '' }
    : (message.payload ?? {})
  bridge.noteMessageFor(RUNTIME_SCOPE, {
    type: message.type,
    requestId: message.requestId ?? '',
    timestamp: message.timestamp ?? Date.now(),
    payload: auditedPayload,
  })
  emit('unityMessage', message)
}

function postEnvelope(message: UnityBridgeMessage) {
  const envelope = { ...message, payload: cloneUnityPayload(message.payload) }
  bridge.noteOutgoingFor(RUNTIME_SCOPE, envelope)
  emit('unityCommand', envelope)
  iframeRef.value?.contentWindow?.postMessage({
    source: 'vue-console',
    runtimeScope: RUNTIME_SCOPE,
    runtimeInstanceId: RUNTIME_INSTANCE_ID,
    message: envelope,
  }, window.location.origin)
}

function flushOutbox() {
  const channel = bridge.channels.SYSTEM_OVERVIEW
  if (!ready.value || !channel.connected || !channel.platformReady || !iframeRef.value?.contentWindow) return
  let message = bridge.peekNextFor(RUNTIME_SCOPE)
  while (message) {
    postEnvelope(message)
    if (message.type === 'poseFrame') bridge.markPoseSentFor(RUNTIME_SCOPE, message.payload)
    bridge.removeNextFor(RUNTIME_SCOPE)
    message = bridge.peekNextFor(RUNTIME_SCOPE)
  }
}

function postToUnity(type: string, payload: Record<string, unknown> = {}) {
  const requestId = bridge.sendFor(RUNTIME_SCOPE, type, cloneUnityPayload(payload))
  flushOutbox()
  return requestId
}

function syncViewport() {
  window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
    iframeRef.value?.contentWindow?.dispatchEvent(new Event('resize'))
    iframeRef.value?.contentWindow?.postMessage({
      source: 'vue-console',
      runtimeScope: RUNTIME_SCOPE,
      runtimeInstanceId: RUNTIME_INSTANCE_ID,
      message: { type: 'viewportSettled', payload: { generation: viewportTransitionGeneration } },
    }, window.location.origin)
  }))
}

function beginViewportTransition() {
  viewportTransitionGeneration += 1
  const iframe = iframeRef.value
  const panel = panelRef.value
  if (!iframe || !panel) return
  const frameRect = iframe.getBoundingClientRect()
  lockedViewportWidth = Math.max(1, frameRect.width)
  lockedViewportHeight = Math.max(1, frameRect.height)
  iframe.style.width = `${lockedViewportWidth}px`
  iframe.style.height = `${lockedViewportHeight}px`
  iframe.style.maxWidth = 'none'
  iframe.style.flex = '0 0 auto'
  iframe.style.transformOrigin = 'top left'
  viewportResizeObserver?.disconnect()
  viewportResizeObserver = new ResizeObserver(() => {
    const width = panelRef.value?.getBoundingClientRect().width ?? lockedViewportWidth
    iframe.style.transform = `scaleX(${Math.max(0.01, width / lockedViewportWidth)})`
  })
  viewportResizeObserver.observe(panel)
}

function endViewportTransition() {
  viewportResizeObserver?.disconnect()
  viewportResizeObserver = null
  const iframe = iframeRef.value
  if (iframe) {
    iframe.style.width = ''
    iframe.style.height = ''
    iframe.style.maxWidth = ''
    iframe.style.flex = ''
    iframe.style.transform = ''
    iframe.style.transformOrigin = ''
  }
  lockedViewportWidth = 0
  lockedViewportHeight = 0
  syncViewport()
}

function startProbe() {
  if (probeTimer !== null) window.clearInterval(probeTimer)
  const startedAt = Date.now()
  probeTimer = window.setInterval(() => {
    const frameDocument = iframeRef.value?.contentDocument
    if (!frameDocument) return
    const warning = frameDocument.querySelector('#unity-warning')?.textContent?.trim() ?? ''
    if (/does not support WebGL|WebGL\s*not\s*supported/i.test(warning)) {
      markError('浏览器 WebGL 不可用，请启用硬件加速')
    } else if (Date.now() - startedAt > 120000) {
      loadHint.value = '仍在等待系统总览平台初始化，请检查 Unity 控制台'
    }
  }, 500)
}

function handleIframeLoad() {
  trajectory.clearFor(RUNTIME_SCOPE)
  bridge.setConnectedFor(RUNTIME_SCOPE, false)
  loading.value = true
  ready.value = false
  controlsReady.value = false
  readyEmitted = false
  errorMessage.value = ''
  loadHint.value = '正在加载系统总览 Unity WebGL'
  startProbe()
}

function reload() {
  bridge.setConnectedFor(RUNTIME_SCOPE, false)
  loading.value = true
  ready.value = false
  pointCloudBridgeReady.value = false
  lastPointCloudSentAtMs = 0
  controlsReady.value = false
  readyEmitted = false
  errorMessage.value = ''
  reloadToken.value += 1
}

defineExpose({
  postToUnity,
  reload,
  selectDevice: (deviceCode: string) => postToUnity('selectDevice', { deviceCode }),
  focusDevice: (deviceCode: string) => postToUnity('focusDevice', { deviceCode }),
  switchCamera: (mode: string) => postToUnity('switchCamera', { mode }),
  toggleTrajectory: (visible: boolean) => postToUnity('toggleTrajectory', { visible }),
  sendControlCommand: (command: string, deviceCode?: string) => postToUnity('sendControlCommand', { command, deviceCode }),
  sendPoseFrame: (payload: Record<string, unknown>) => postToUnity('poseFrame', payload),
  beginViewportTransition,
  endViewportTransition,
  syncViewport,
})

watch(
  () => [bridge.channels.SYSTEM_OVERVIEW.connected, bridge.channels.SYSTEM_OVERVIEW.platformReady, bridge.channels.SYSTEM_OVERVIEW.outbox.length],
  flushOutbox,
)
watch(visualContext, context => visualSensor.bindRuntime(context), { immediate: true })

onMounted(() => {
  window.addEventListener('message', handleWindowMessage)
  window.addEventListener('uav-usv:pointcloud-frame', handlePointCloudFrame)
  window.addEventListener('uav-usv:viewport-transition-start', beginViewportTransition)
  window.addEventListener('uav-usv:viewport-transition-end', endViewportTransition)
  heartbeatTimer = window.setInterval(() => {
    void reportHeartbeat(ready.value ? 'ONLINE' : 'OFFLINE', 'SYSTEM_OVERVIEW Unity WebGL 心跳')
  }, 5000)
})

onBeforeUnmount(() => {
  window.removeEventListener('message', handleWindowMessage)
  window.removeEventListener('uav-usv:pointcloud-frame', handlePointCloudFrame)
  window.removeEventListener('uav-usv:viewport-transition-start', beginViewportTransition)
  window.removeEventListener('uav-usv:viewport-transition-end', endViewportTransition)
  viewportResizeObserver?.disconnect()
  if (probeTimer !== null) window.clearInterval(probeTimer)
  if (heartbeatTimer !== null) window.clearInterval(heartbeatTimer)
  bridge.setConnectedFor(RUNTIME_SCOPE, false)
  void reportHeartbeat('OFFLINE', 'SYSTEM_OVERVIEW Unity WebGL 已卸载')
  try {
    const frame = iframeRef.value
    const frameWindow = frame?.contentWindow as UnityFrameWindow | null | undefined
    if (frameWindow?.uavUsvUnityInstance?.Quit) void frameWindow.uavUsvUnityInstance.Quit().catch(() => undefined)
    if (frame) frame.src = 'about:blank'
  } catch {
    // The iframe may already be detached.
  }
})
</script>

<template>
  <div ref="panelRef" class="unity-webgl-panel">
    <iframe
      ref="iframeRef"
      :key="reloadToken"
      class="unity-webgl-frame"
      :src="iframeUrl"
      title="UAV-USV 系统总览 Unity WebGL"
      allow="fullscreen; autoplay; gamepad; xr-spatial-tracking"
      @load="handleIframeLoad"
    ></iframe>
    <div v-if="loading" class="unity-webgl-overlay">
      <strong>Unity WebGL 加载中</strong>
      <div class="unity-webgl-progress"><i></i></div>
      <span>{{ loadHint }}</span>
    </div>
    <div v-if="errorMessage" class="unity-webgl-overlay error">
      <strong>Unity WebGL 加载失败</strong>
      <p>{{ errorMessage }}</p>
      <button type="button" @click="reload">重新加载</button>
    </div>
    <div class="unity-webgl-status" :class="ready ? 'ready' : 'pending'"><i></i>{{ statusText }}</div>
  </div>
</template>

<style scoped>
.unity-webgl-panel { position: relative; min-width: 0; min-height: 0; overflow: hidden; }
.unity-webgl-frame { display: block; width: 100%; height: 100%; background: #061113; border: 0; }
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { useUnityBridgeStore } from '@/stores/unityBridge'
import type { UnityBridgeMessage } from '@/stores/unityBridge'
import {
  appendUnityRuntimeParams,
  cloneUnityPayload,
  createVueConsoleEnvelope,
  parseUnityWindowMessage,
} from '@/utils/unityWebglProtocol'
import type { UnityWindowMessage } from '@/utils/unityWebglProtocol'
import type { UnityPresentationOutgoing } from '@/types/voiceControl'

const RUNTIME_SCOPE = 'VIRTUAL_FLEET' as const
const RUNTIME_INSTANCE_ID = 'virtual-fleet-unity-01'
const WEBGL_SOURCE = '/unity-virtual-fleet/index.html?embedded=1&build=20261010-existing-scene-v1'

const emit = defineEmits<{
  unityReady: []
  unityLoading: []
  unityMessage: [message: UnityWindowMessage]
  unityError: [message: string]
  unityCommand: [message: UnityWindowMessage]
}>()

const iframeRef = ref<HTMLIFrameElement | null>(null)
const panelRef = ref<HTMLElement | null>(null)
const bridge = useUnityBridgeStore()
const loading = ref(true)
const ready = ref(false)
const errorMessage = ref('')
const loadHint = ref('正在加载算法仿真 Unity WebGL')
const reloadToken = ref(Date.now())
const seenPoseReceipts = new Set<string>()

let probeTimer: number | null = null
let readyEmitted = false
let resizeObserver: ResizeObserver | null = null
let resizeTimer: number | null = null
let transitionGeneration = 0

type UnityFrameWindow = Window & {
  uavUsvUnityInstance?: { Quit?: () => Promise<void> }
}

const iframeUrl = computed(() => appendUnityRuntimeParams(WEBGL_SOURCE, {
  v: reloadToken.value,
  scope: RUNTIME_SCOPE,
  instanceId: RUNTIME_INSTANCE_ID,
}))
const statusText = computed(() => ready.value ? 'UNITY WEBGL ONLINE' : 'WAITING FOR WEBGL')

function markReady(payload: Record<string, unknown> = {}) {
  if (probeTimer !== null) {
    window.clearInterval(probeTimer)
    probeTimer = null
  }
  const controlsReady = payload.controlsReady !== false
  bridge.setPlatformCapabilitiesFor(RUNTIME_SCOPE, {
    ready: true,
    controlsReady,
    cameraReady: payload.cameraReady !== false,
    algorithmReady: payload.algorithmReady !== false,
    visualSensorReady: false,
    buildId: String(payload.buildId ?? 'unity-virtual-fleet'),
    capabilities: Array.isArray(payload.capabilities) ? payload.capabilities : [],
  })
  bridge.setConnectedFor(RUNTIME_SCOPE, true)
  loading.value = false
  ready.value = true
  errorMessage.value = ''
  loadHint.value = '算法仿真 Unity WebGL 已连接'
  flushOutbox()
  if (!readyEmitted) {
    readyEmitted = true
    emit('unityReady')
  }
}

function markError(message: string) {
  if (probeTimer !== null) {
    window.clearInterval(probeTimer)
    probeTimer = null
  }
  loading.value = false
  ready.value = false
  errorMessage.value = message
  bridge.setErrorFor(RUNTIME_SCOPE, message)
  emit('unityError', message)
}

function normalizeScenarioReady(message: UnityWindowMessage) {
  const channel = bridge.channels.VIRTUAL_FLEET
  const payload = message.payload ?? {}
  const reportedRunId = Number(payload.runId ?? 0)
  const activeRunId = channel.scenarioRunId
  const runId = Number.isSafeInteger(reportedRunId) && reportedRunId > 0
    ? reportedRunId
    : activeRunId
  if (!runId || runId !== activeRunId) return message
  const normalized: UnityWindowMessage = {
    ...message,
    payload: {
      ...payload,
      success: payload.success !== false,
      runId,
      algorithmCode: payload.algorithmCode ?? payload.scenarioId ?? channel.scenarioAlgorithmCode,
    },
  }
  if (normalized.payload?.success === true) {
    bridge.markScenarioReadyFor(RUNTIME_SCOPE, normalized.payload)
    flushOutbox()
  }
  return normalized
}

function handleWindowMessage(event: MessageEvent) {
  if (event.source !== iframeRef.value?.contentWindow) return
  if (event.origin !== window.location.origin) return
  let message = parseUnityWindowMessage(event.data)
  if (!message) return

  if (message.type === 'unityProgress') {
    const progress = Number(message.payload?.progress ?? 0)
    if (Number.isFinite(progress) && progress > 0) {
      loadHint.value = `算法仿真 Unity WebGL 加载中 ${(progress * 100).toFixed(0)}%`
    }
  } else if (message.type === 'unityError' || message.type === 'unityBridgeError') {
    markError(String(message.payload?.message ?? '算法仿真 Unity WebGL 加载失败'))
  } else if (message.type === 'platformBridgeReady') {
    const platformAvailable = message.payload?.ready === true
      || (
        message.payload?.controlsReady === true
        && message.payload?.cameraReady === true
        && message.payload?.algorithmReady === true
      )
    if (!platformAvailable) {
      markError('算法仿真 Unity 桥接未就绪')
    } else {
      markReady(message.payload)
    }
  } else if (message.type === 'bridgeReady') {
    if (message.payload?.controlsReady === false) markError('算法仿真控制桥未就绪')
    else markReady(message.payload)
  } else if (message.type === 'scenarioReady' || message.type === 'scenarioLoaded') {
    message = normalizeScenarioReady(message)
  } else if (message.type === 'poseFrameApplied' && message.payload) {
    const runId = String(message.payload.runId ?? '')
    const sequence = Number(message.payload.sequence)
    const key = `${runId}:${sequence}`
    if (!seenPoseReceipts.has(key)) {
      seenPoseReceipts.add(key)
      bridge.markPoseAppliedFor(RUNTIME_SCOPE, message.payload)
    }
  } else if (message.type === 'trajectoryVisibilityChanged' && message.payload) {
    bridge.setTrajectoryVisibilityFor(RUNTIME_SCOPE, message.payload.visible === true)
  }

  if (
    (message.type === 'commandAck' || message.type === 'cameraChanged'
      || message.type === 'trajectoryVisibilityChanged')
    && message.payload
  ) {
    void bridge.handleCommandAckFor(RUNTIME_SCOPE, message.requestId ?? '', message.payload)
  }
  bridge.noteMessageFor(RUNTIME_SCOPE, {
    type: message.type,
    requestId: message.requestId ?? '',
    timestamp: message.timestamp ?? Date.now(),
    payload: message.payload ?? {},
  })
  emit('unityMessage', message)
}

function postEnvelope(message: UnityBridgeMessage) {
  const envelope: UnityBridgeMessage = {
    ...message,
    payload: cloneUnityPayload(message.payload),
  }
  bridge.noteOutgoingFor(RUNTIME_SCOPE, envelope)
  emit('unityCommand', envelope)
  iframeRef.value?.contentWindow?.postMessage(
    createVueConsoleEnvelope(RUNTIME_SCOPE, RUNTIME_INSTANCE_ID, envelope),
    window.location.origin,
  )
}

function flushOutbox() {
  const channel = bridge.channels.VIRTUAL_FLEET
  if (!ready.value || !channel.connected || !channel.platformReady || !iframeRef.value?.contentWindow) return
  let message = bridge.peekNextFor(RUNTIME_SCOPE)
  while (message) {
    try {
      postEnvelope(message)
      if (message.type === 'poseFrame' || message.type === 'applyPoseBatch') {
        bridge.markPoseSentFor(RUNTIME_SCOPE, message.payload)
      }
      bridge.removeNextFor(RUNTIME_SCOPE)
      message = bridge.peekNextFor(RUNTIME_SCOPE)
    } catch (error) {
      markError(error instanceof Error ? error.message : '算法仿真消息发送失败')
      break
    }
  }
}

function postToUnity(type: string, payload: Record<string, unknown> = {}) {
  const requestId = bridge.sendFor(RUNTIME_SCOPE, type, cloneUnityPayload(payload))
  flushOutbox()
  return requestId
}

function postPresentationEnvelope(message: UnityPresentationOutgoing) {
  if (!ready.value || !iframeRef.value?.contentWindow) return false
  iframeRef.value.contentWindow.postMessage(
    createVueConsoleEnvelope(RUNTIME_SCOPE, RUNTIME_INSTANCE_ID, message),
    window.location.origin,
  )
  return true
}

function scheduleResize(delay = 80) {
  if (resizeTimer !== null) window.clearTimeout(resizeTimer)
  resizeTimer = window.setTimeout(() => {
    resizeTimer = null
    const frameWindow = iframeRef.value?.contentWindow
    frameWindow?.dispatchEvent(new Event('resize'))
    frameWindow?.postMessage({
      source: 'vue-console',
      runtimeScope: RUNTIME_SCOPE,
      runtimeInstanceId: RUNTIME_INSTANCE_ID,
      message: { type: 'viewportSettled', payload: { generation: transitionGeneration } },
    }, window.location.origin)
  }, delay)
}

function beginViewportTransition() {
  transitionGeneration += 1
}

function endViewportTransition() {
  scheduleResize(120)
}

function syncViewport() {
  scheduleResize(0)
}

function startProbe() {
  if (probeTimer !== null) window.clearInterval(probeTimer)
  const startedAt = Date.now()
  probeTimer = window.setInterval(() => {
    const frameDocument = iframeRef.value?.contentDocument
    const frameWindow = iframeRef.value?.contentWindow as UnityFrameWindow | null | undefined
    if (!frameDocument || !frameWindow) return
    const warning = frameDocument.querySelector('#unity-warning')?.textContent?.trim() ?? ''
    if (/does not support WebGL|WebGL\s*not\s*supported/i.test(warning)) {
      markError('浏览器 WebGL 不可用，请启用硬件加速')
    } else if (Date.now() - startedAt > 120000) {
      loadHint.value = '算法仿真 WebGL 仍在加载，请检查构建包或硬件加速'
    }
  }, 500)
}

function handleIframeLoad() {
  bridge.setConnectedFor(RUNTIME_SCOPE, false)
  seenPoseReceipts.clear()
  loading.value = true
  ready.value = false
  readyEmitted = false
  errorMessage.value = ''
  loadHint.value = '正在加载算法仿真 Unity WebGL'
  emit('unityLoading')
  startProbe()
}

function reload() {
  bridge.setConnectedFor(RUNTIME_SCOPE, false)
  loading.value = true
  ready.value = false
  readyEmitted = false
  errorMessage.value = ''
  emit('unityLoading')
  reloadToken.value += 1
}

defineExpose({
  postToUnity,
  postPresentationEnvelope,
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
  () => [bridge.channels.VIRTUAL_FLEET.connected, bridge.channels.VIRTUAL_FLEET.platformReady, bridge.channels.VIRTUAL_FLEET.outbox.length],
  flushOutbox,
)

onMounted(() => {
  window.addEventListener('message', handleWindowMessage)
  resizeObserver = new ResizeObserver(() => scheduleResize())
  if (panelRef.value) resizeObserver.observe(panelRef.value)
})

onBeforeUnmount(() => {
  window.removeEventListener('message', handleWindowMessage)
  resizeObserver?.disconnect()
  if (probeTimer !== null) window.clearInterval(probeTimer)
  if (resizeTimer !== null) window.clearTimeout(resizeTimer)
  bridge.setConnectedFor(RUNTIME_SCOPE, false)
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
      title="UAV-USV 算法仿真 Unity WebGL"
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

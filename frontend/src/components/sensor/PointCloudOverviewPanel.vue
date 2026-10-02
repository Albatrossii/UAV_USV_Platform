<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import { fetchLatestPointCloudFrame } from '@/api/sensor'
import type { PointCloudFrame, PointCloudLatest } from '@/types/sensor'

const emit = defineEmits<{
  frame: [value: PointCloudLatest]
}>()

const STREAM_ID = 'usv_01_mid360'
const canvasRef = ref<HTMLCanvasElement | null>(null)
const latest = ref<PointCloudLatest | null>(null)
const state = ref<'waiting' | 'online' | 'stale' | 'error'>('waiting')
const error = ref('')
const now = ref(Date.now())
const rateHz = ref<number | null>(null)
const unityBridgeSupported = ref<boolean | null>(null)
const unityAckSequence = ref<number | null>(null)
const unityAckError = ref('')
let pollTimer: number | null = null
let clockTimer: number | null = null
let inFlight = false
let lastReceivedAt = 0
let resizeObserver: ResizeObserver | null = null

const dataAge = computed(() => latest.value ? Math.max(0, now.value - latest.value.receivedAtMs) : null)
const stateText = computed(() => {
  if (state.value === 'online') return '实时接收'
  if (state.value === 'stale') return '数据已超时'
  if (state.value === 'error') return '后端暂不可用'
  return '等待点云'
})
const unityReceiveText = computed(() => {
  if (unityBridgeSupported.value === false) return 'Unity 构建未包含点云接收桥'
  if (unityBridgeSupported.value === null) return 'Unity 点云回执待确认'
  if (unityAckSequence.value === latest.value?.sequence) return `Unity 已确认 seq ${unityAckSequence.value}`
  return '等待 Unity 点云回执'
})

function handleUnityPointCloudCapability(event: Event) {
  const detail = (event as CustomEvent<{ supported?: boolean }>).detail
  unityBridgeSupported.value = detail?.supported === true
}

function handleUnityPointCloudAck(event: Event) {
  const detail = (event as CustomEvent<{
    success?: boolean
    sequence?: number
    streamId?: string
    status?: string
  }>).detail
  if (detail?.streamId !== STREAM_ID || typeof detail.sequence !== 'number') return
  if (detail.success === true) {
    unityAckSequence.value = detail.sequence
    unityAckError.value = ''
  } else {
    unityAckError.value = String(detail.status ?? 'Unity 拒绝了点云帧')
  }
}

function drawFrame(frame: PointCloudFrame | null) {
  const canvas = canvasRef.value
  const context = canvas?.getContext('2d')
  if (!canvas || !context) return

  const rect = canvas.getBoundingClientRect()
  if (!rect.width || !rect.height) return
  const pixelRatio = Math.min(2, window.devicePixelRatio || 1)
  const width = Math.round(rect.width * pixelRatio)
  const height = Math.round(rect.height * pixelRatio)
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width
    canvas.height = height
  }
  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0)
  context.clearRect(0, 0, rect.width, rect.height)

  const pad = 15
  const plotWidth = Math.max(1, rect.width - pad * 2)
  const plotHeight = Math.max(1, rect.height - pad * 2)
  context.strokeStyle = 'rgba(88, 157, 166, 0.22)'
  context.lineWidth = 1
  for (let index = 1; index < 4; index += 1) {
    const x = pad + plotWidth * index / 4
    const y = pad + plotHeight * index / 4
    context.beginPath()
    context.moveTo(x, pad)
    context.lineTo(x, pad + plotHeight)
    context.moveTo(pad, y)
    context.lineTo(pad + plotWidth, y)
    context.stroke()
  }
  context.strokeStyle = 'rgba(92, 224, 208, 0.34)'
  context.strokeRect(pad, pad, plotWidth, plotHeight)
  if (!frame?.data?.xyz?.length) return

  const xyz = frame.data.xyz
  let minX = Number.POSITIVE_INFINITY
  let maxX = Number.NEGATIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  let maxY = Number.NEGATIVE_INFINITY
  for (let index = 0; index + 2 < xyz.length; index += 3) {
    const x = xyz[index]
    const y = xyz[index + 1]
    if (x === undefined || y === undefined) continue
    minX = Math.min(minX, x)
    maxX = Math.max(maxX, x)
    minY = Math.min(minY, y)
    maxY = Math.max(maxY, y)
  }
  if (!Number.isFinite(minX) || !Number.isFinite(minY)) return
  const spanX = Math.max(0.01, maxX - minX)
  const spanY = Math.max(0.01, maxY - minY)
  const scale = Math.min(plotWidth / spanX, plotHeight / spanY) * 0.94
  const drawnWidth = spanX * scale
  const drawnHeight = spanY * scale
  const originX = pad + (plotWidth - drawnWidth) / 2
  const originY = pad + (plotHeight - drawnHeight) / 2
  context.fillStyle = '#65e7d4'
  for (let index = 0; index + 2 < xyz.length; index += 3) {
    const pointX = xyz[index]
    const pointY = xyz[index + 1]
    if (pointX === undefined || pointY === undefined) continue
    const x = originX + (pointX - minX) * scale
    const y = originY + drawnHeight - (pointY - minY) * scale
    context.fillRect(x, y, 1.5, 1.5)
  }
}

function applyLatest(value: PointCloudLatest | null) {
  if (!value?.frame) {
    latest.value = null
    state.value = 'waiting'
    drawFrame(null)
    return
  }

  const frame = value.frame
  const data = frame.data
  if (
    frame.message_type !== 'pointcloud_frame'
    || data.stream_id !== STREAM_ID
    || data.frame_id !== 'map'
    || !Array.isArray(data.xyz)
    || data.xyz.length !== data.point_count * 3
  ) {
    state.value = 'error'
    error.value = '点云帧格式校验失败'
    return
  }

  // Gateway sequence numbers can advance for other message types as well, so
  // sequence deltas do not represent point-cloud frames per second. Measure
  // the interval between distinct backend point-cloud receive timestamps.
  if (value.receivedAtMs > lastReceivedAt) {
    if (lastReceivedAt > 0) {
      const hz = 1000 / (value.receivedAtMs - lastReceivedAt)
      rateHz.value = rateHz.value === null ? hz : rateHz.value * 0.65 + hz * 0.35
    }
    lastReceivedAt = value.receivedAtMs
    emit('frame', value)
  }
  latest.value = value
  error.value = ''
  state.value = value.ageMs <= 1800 ? 'online' : 'stale'
  drawFrame(frame)
}

async function refresh() {
  if (inFlight) return
  inFlight = true
  try {
    applyLatest(await fetchLatestPointCloudFrame(STREAM_ID))
  } catch (cause) {
    state.value = 'error'
    error.value = cause instanceof Error ? cause.message : '获取点云数据失败'
  } finally {
    inFlight = false
  }
}

onMounted(() => {
  window.addEventListener('uav-usv:unity-pointcloud-capability', handleUnityPointCloudCapability)
  window.addEventListener('uav-usv:unity-pointcloud-ack', handleUnityPointCloudAck)
  resizeObserver = new ResizeObserver(() => drawFrame(latest.value?.frame ?? null))
  if (canvasRef.value) resizeObserver.observe(canvasRef.value)
  void refresh()
  // Sample faster than the expected ~3 Hz feed so successive frames are not
  // routinely skipped by the latest-frame endpoint.
  pollTimer = window.setInterval(() => void refresh(), 200)
  clockTimer = window.setInterval(() => {
    now.value = Date.now()
    if (latest.value && now.value - latest.value.receivedAtMs > 1800) state.value = 'stale'
  }, 250)
})

onBeforeUnmount(() => {
  window.removeEventListener('uav-usv:unity-pointcloud-capability', handleUnityPointCloudCapability)
  window.removeEventListener('uav-usv:unity-pointcloud-ack', handleUnityPointCloudAck)
  if (pollTimer !== null) window.clearInterval(pollTimer)
  if (clockTimer !== null) window.clearInterval(clockTimer)
  resizeObserver?.disconnect()
})
</script>

<template>
  <aside class="pointcloud-overview" aria-label="MID360 实时点云">
    <header>
      <span><i :class="state"></i>MID360 · {{ STREAM_ID }}</span>
      <b :class="state">{{ stateText }}</b>
    </header>
    <canvas ref="canvasRef" aria-label="MID360 map 坐标俯视点云"></canvas>
    <footer>
      <span>{{ latest?.frame.data.point_count?.toLocaleString() ?? 0 }} 点</span>
      <span>seq {{ latest?.sequence ?? '--' }}</span>
      <span>接收 {{ rateHz === null ? '--' : `${rateHz.toFixed(1)} Hz` }}</span>
      <span>{{ dataAge === null ? '--' : `${dataAge} ms` }}</span>
    </footer>
    <small class="unity-ack" :class="{ confirmed: unityAckSequence === latest?.sequence && unityAckSequence !== null, unsupported: unityBridgeSupported === false }">
      {{ unityAckError || unityReceiveText }}
    </small>
    <small v-if="error" class="frame-error">{{ error }}</small>
  </aside>
</template>

<style scoped>
.pointcloud-overview { position: absolute; z-index: 45; top: 14px; left: 14px; display: grid; width: min(290px, calc(100% - 28px)); gap: 7px; padding: 9px; color: #c3e5e5; background: rgba(3, 17, 25, .9); border: 1px solid rgba(74, 176, 183, .48); border-radius: 7px; box-shadow: 0 8px 24px rgba(0, 8, 14, .35); backdrop-filter: blur(8px); pointer-events: none; }
header, footer { display: flex; align-items: center; justify-content: space-between; gap: 8px; font: 9px ui-monospace, Consolas, monospace; }
header span { display: flex; min-width: 0; align-items: center; gap: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
header i { width: 6px; height: 6px; flex: 0 0 auto; border-radius: 50%; background: #82979a; }
header i.online { background: #54d8a3; box-shadow: 0 0 7px #54d8a3; }
header i.stale, header i.error { background: #ffbd4a; }
header b { flex: 0 0 auto; color: #91aaad; font-weight: 600; }
header b.online { color: #67e3b4; }
header b.stale, header b.error { color: #ffc45b; }
canvas { display: block; width: 100%; height: 150px; border-radius: 4px; background: radial-gradient(ellipse, rgba(13, 52, 59, .85), rgba(3, 22, 30, .96)); }
footer { color: #8aabad; }
footer span:first-child { color: #dcf8f4; }
small { color: #ffc45b; font: 9px sans-serif; }
.unity-ack { color: #a7bfc0; }
.unity-ack.confirmed { color: #67e3b4; }
.unity-ack.unsupported, .frame-error { color: #ffc45b; }
@media (max-width: 760px) { .pointcloud-overview { width: min(250px, calc(100% - 28px)); } canvas { height: 110px; } }
</style>

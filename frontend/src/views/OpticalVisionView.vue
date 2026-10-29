<script setup lang="ts">
import { Camera, Crosshair, Maximize2, Radio, ScanLine, Zap } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import SpectrumWaterfall3D from '@/components/vision/SpectrumWaterfall3D.vue'
import { useRadarSensorStore } from '@/stores/radarSensor'
import { useUnityBridgeStore } from '@/stores/unityBridge'
import { useVisualSensorStore } from '@/stores/visualSensor'
import { useGimbalMoveStopGuard } from '@/composables/useGimbalMoveStopGuard'
import { createSerialRequestQueue } from '@/composables/createSerialRequestQueue'
import { fetchGimbalCameraFrame, fetchGimbalCameraStatus, sendGimbalCameraCommand } from '@/api/gimbalCamera'
import type { GimbalCameraCommand, GimbalCameraStatus } from '@/types/gimbalCamera'
import type { RadarItem } from '@/types/sensor'
import type { VisualSensor } from '@/types/visualSensor'

const DETECTOR_ID = 'electronic_detector_01'
const TARGET_FPS = 30
const SPECTRUM_HISTORY_MS = 100_000
const SPECTRUM_HISTORY_BINS = 164

interface SpectrumHistoryFrame {
  receivedAt: number
  powersDbm: number[]
}

const store = useVisualSensorStore()
const radarStore = useRadarSensorStore()
const bridge = useUnityBridgeStore()
const activeSourceId = ref('uav_01')
const focusedCameraId = ref('uav_01')
const activeVisionMode = ref<'relay' | 'gimbal'>('relay')
const gimbalStatus = ref<GimbalCameraStatus | null>(null)
const gimbalFrameUrl = ref('')
const gimbalFrameFps = ref(0)
const gimbalSpeed = ref(3)
const yawInput = ref('0.00')
const pitchInput = ref('0.00')
const imageMode = ref('EO1')
const gimbalBusy = ref(false)
const gimbalRecording = ref(false)
const gimbalMessage = ref('等待云台协议网关')
const spectrumHistory = shallowRef<SpectrumHistoryFrame[]>([])
let statusTimer: number | undefined
let spectrumTimer: number | undefined
let gimbalTimer: number | undefined
let gimbalFrameTimer: number | undefined
let gimbalFrameTimestamp = 0
let gimbalFrameRefreshing = false
let gimbalFrameReceivedAt: number[] = []
let gimbalLastFrameReceivedAt = 0
let gimbalCommandsInFlight = 0
const gimbalCommandQueue = createSerialRequestQueue()
let gimbalMoveRenewTimer: number | undefined
let activeGimbalDirection: string | null = null
let radarRefreshing = false
let viewActive = false
let lastSpectrumKey = ''
const gimbalMoveStopGuard = useGimbalMoveStopGuard(() => {
  if (gimbalMoveRenewTimer) window.clearInterval(gimbalMoveRenewTimer)
  gimbalMoveRenewTimer = undefined
  activeGimbalDirection = null
  void sendGimbalCommand('PTZ_STOP')
})
const gimbalMoveActive = gimbalMoveStopGuard.active
let lastSpectrumStream = ''

const overview = computed(() => store.displayOverview)
const sensors = computed(() => overview.value.sensors)
const frames = computed(() => store.channels.SYSTEM_OVERVIEW.frameUrls)
const stats = computed(() => store.streamStats)
const radar = computed(() => radarStore.overview)
const detectorSelected = computed(() => activeSourceId.value === DETECTOR_ID)
const activeCamera = computed(() => sensors.value.find(item => item.cameraId === activeSourceId.value) ?? null)
const totalSources = computed(() => sensors.value.length + 1)
const onlineSources = computed(() => overview.value.onlineCount + (radar.value?.connected ? 1 : 0))
const linkOnline = computed(() => store.unityBridgeReady || overview.value.gatewayConnected || Boolean(radar.value?.connected))
const linkLabel = computed(() => linkOnline.value ? '融合链路在线' : '等待视觉链路')
const detectorTargets = computed(() => (radar.value?.items ?? []).filter(item => item.kind === 'DETECTION'))
const detectorCount = computed(() => detectorTargets.value.length)
const spectrumActive = computed(() => Boolean(
  radar.value?.spectrumConnected && radar.value.spectrumPowersDbm?.length,
))
const spectrumBounds = computed(() => {
  const values = radar.value?.spectrumPowersDbm ?? []
  const min = values.length ? Math.floor(Math.min(...values) / 10) * 10 : -130
  const max = values.length ? Math.ceil(Math.max(...values) / 10) * 10 : -40
  return { min, max: Math.max(max, min + 20) }
})
const spectrumPolyline = computed(() => {
  const values = radar.value?.spectrumPowersDbm ?? []
  const span = spectrumBounds.value.max - spectrumBounds.value.min
  return values.map((value, index) => {
    const x = values.length > 1 ? index / (values.length - 1) * 1000 : 0
    const y = 300 - (value - spectrumBounds.value.min) / span * 270
    return `${x.toFixed(1)},${Math.max(20, Math.min(300, y)).toFixed(1)}`
  }).join(' ')
})
const spectrumAxisStopHz = computed(() => {
  const start = radar.value?.spectrumStartHz
  const bin = radar.value?.spectrumBinHz
  const points = radar.value?.spectrumPowersDbm?.length ?? 0
  return start != null && bin != null && points > 0
    ? start + (points - 1) * bin
    : radar.value?.spectrumStopHz
})
const spectrumPeakPoint = computed(() => {
  const start = radar.value?.spectrumStartHz ?? 0
  const stop = spectrumAxisStopHz.value ?? start
  const peak = radar.value?.spectrumPeakHz ?? start
  const dbm = radar.value?.spectrumPeakDbm ?? spectrumBounds.value.min
  const x = stop > start ? (peak - start) / (stop - start) * 1000 : 0
  const y = 300 - (dbm - spectrumBounds.value.min) / (spectrumBounds.value.max - spectrumBounds.value.min) * 270
  return { x: Math.max(0, Math.min(1000, x)), y: Math.max(20, Math.min(300, y)) }
})
const spectrumHistoryRate = computed(() => {
  const frames = spectrumHistory.value
  if (frames.length < 2) return 0
  const seconds = (frames[frames.length - 1]!.receivedAt - frames[0]!.receivedAt) / 1000
  return seconds > 0 ? (frames.length - 1) / seconds : 0
})
const detectorUpdatedAgo = computed(() => {
  const updatedAt = radar.value?.updatedAt ?? 0
  if (!updatedAt) return '--'
  const delay = Math.max(0, Date.now() - updatedAt)
  return delay < 1000 ? `${delay} ms` : `${(delay / 1000).toFixed(1)} s`
})
const radarRange = computed(() => {
  const ranges = detectorTargets.value
    .map(item => item.range ?? (item.x != null && item.y != null ? Math.hypot(item.x, item.y) : 0))
    .filter(value => Number.isFinite(value) && value > 0)
  return Math.max(100, ...ranges)
})
const radarPoints = computed(() => detectorTargets.value.slice(0, 80).map((item, index) => ({
  item,
  style: radarPointStyle(item),
  label: item.id || `T-${String(index + 1).padStart(3, '0')}`,
})))

function channelLabel(sensor: VisualSensor) {
  return `${sensor.deviceType === 'UAV' ? '空中视角' : '水面视角'} ${sensor.cameraId.slice(-2)}`
}

function shortDeviceLabel(sensor: VisualSensor) {
  return sensor.deviceCode || sensor.cameraId.toUpperCase().replace('_', '-')
}

function formatGhz(value: number | null | undefined, digits = 3) {
  return value == null ? '--' : `${(value / 1_000_000_000).toFixed(digits)} GHz`
}

function radarPointStyle(item: RadarItem) {
  const distance = item.range ?? (item.x != null && item.y != null ? Math.hypot(item.x, item.y) : 0)
  const bearing = item.bearing ?? (item.x != null && item.y != null
    ? Math.atan2(item.x, item.y) * 180 / Math.PI
    : 0)
  const radius = Math.min(43, Math.max(3, distance / radarRange.value * 43))
  const radians = bearing * Math.PI / 180
  return { left: `${50 + Math.sin(radians) * radius}%`, top: `${50 - Math.cos(radians) * radius}%` }
}

function downsampleSpectrum(values: number[]) {
  if (values.length <= SPECTRUM_HISTORY_BINS) return [...values]
  const result: number[] = []
  for (let bin = 0; bin < SPECTRUM_HISTORY_BINS; bin += 1) {
    const start = Math.floor(bin * values.length / SPECTRUM_HISTORY_BINS)
    const stop = Math.max(start + 1, Math.floor((bin + 1) * values.length / SPECTRUM_HISTORY_BINS))
    let peak = Number.NEGATIVE_INFINITY
    for (let index = start; index < stop; index += 1) peak = Math.max(peak, values[index]!)
    result.push(Number.isFinite(peak) ? peak : values[start]!)
  }
  return result
}

function appendSpectrumHistory(frame: NonNullable<typeof radarStore.overview>) {
  if (!frame.spectrumConnected || !frame.spectrumPowersDbm.length) return
  const stream = frame.spectrumStreamId || frame.spectrumSensorId || 'san60'
  const key = `${stream}:${frame.spectrumGatewaySequence ?? ''}:${frame.spectrumSequence ?? ''}:${frame.spectrumCapturedAt ?? ''}`
  if (key === lastSpectrumKey) return
  if (lastSpectrumStream && lastSpectrumStream !== stream) spectrumHistory.value = []
  lastSpectrumStream = stream
  lastSpectrumKey = key
  const capturedAt = frame.spectrumCapturedAt
  const receivedAt = capturedAt == null
    ? Date.now()
    : capturedAt < 10_000_000_000 ? capturedAt * 1000 : capturedAt
  const cutoff = receivedAt - SPECTRUM_HISTORY_MS
  spectrumHistory.value = [
    ...spectrumHistory.value.filter(item => item.receivedAt >= cutoff),
    { receivedAt, powersDbm: downsampleSpectrum(frame.spectrumPowersDbm) },
  ]
}

function subscribe(cameraId = focusedCameraId.value) {
  if (!viewActive) return
  bridge.sendFor('SYSTEM_OVERVIEW', 'visualSensorSubscribe', {
    enabled: true,
    focusedCameraId: cameraId,
    displayMode: 'focus',
    quality: '1080p',
    targetFps: TARGET_FPS,
    gpuDirect: true,
    jpegFallback: true,
    thumbnailFps: 2,
    focusedFps: TARGET_FPS,
  })
}

async function refreshRadar() {
  if (!viewActive || radarRefreshing) return
  radarRefreshing = true
  try {
    await radarStore.refresh(true)
    const frame = radarStore.overview
    if (frame?.spectrumConnected && frame.spectrumPowersDbm.length) {
      appendSpectrumHistory(frame)
      bridge.sendFor('SYSTEM_OVERVIEW', 'spectrumFrame', {
        vehicleId: frame.spectrumVehicleId,
        sensorId: frame.spectrumSensorId,
        streamId: frame.spectrumStreamId,
        gatewaySequence: frame.spectrumGatewaySequence ?? 0,
        sequence: frame.spectrumSequence ?? 0,
        capturedAt: frame.spectrumCapturedAt ?? 0,
        startHz: frame.spectrumStartHz ?? 0,
        stopHz: frame.spectrumStopHz ?? 0,
        binHz: frame.spectrumBinHz ?? 0,
        rbwHz: frame.spectrumRbwHz ?? 0,
        refLevelDbm: frame.spectrumRefLevelDbm ?? 0,
        temperatureC: frame.spectrumTemperatureC ?? 0,
        peakHz: frame.spectrumPeakHz ?? 0,
        peakDbm: frame.spectrumPeakDbm ?? 0,
        powersDbm: frame.spectrumPowersDbm,
      })
    }
  } finally {
    radarRefreshing = false
  }
}

async function selectSource(sourceId: string) {
  if (activeSourceId.value === sourceId) return
  activeSourceId.value = sourceId
  if (sourceId === DETECTOR_ID) return
  focusedCameraId.value = sourceId
  await store.select(sourceId)
  subscribe(sourceId)
  await nextTick()
  window.dispatchEvent(new CustomEvent('unity-runtime-track', { detail: { duration: 700 } }))
  void store.refreshFrames(false)
}

async function openFullscreen() {
  const viewer = document.querySelector<HTMLElement>(activeVisionMode.value === 'gimbal' ? '.gimbal-video-viewer' : '.primary-viewer')
  if (!viewer) return
  if (document.fullscreenElement) await document.exitFullscreen()
  else await viewer.requestFullscreen?.()
}

async function refreshGimbalStatus() {
  try {
    gimbalStatus.value = await fetchGimbalCameraStatus()
    if (!gimbalBusy.value) gimbalMessage.value = gimbalStatus.value.detail
  } catch (error) {
    gimbalMessage.value = error instanceof Error ? error.message : '云台状态读取失败'
  }
}

function canGimbalCommand(command: string) {
  const status = gimbalStatus.value
  if (!status?.configured || !status.connected || !status.controlEnabled || (status.safetyFault && command !== 'PTZ_STOP') || !status.supportedCommands.includes(command)) return false
  if (command === 'EO_ZOOM' || command === 'IR_ZOOM' || command === 'IR_DZOOM') return status.zoomControlEnabled
  return true
}

async function refreshGimbalFrame() {
  if (!viewActive || activeVisionMode.value !== 'gimbal' || gimbalFrameRefreshing) return
  gimbalFrameRefreshing = true
  try {
    const frame = await fetchGimbalCameraFrame(gimbalFrameTimestamp)
    if (!frame) {
      if (gimbalLastFrameReceivedAt && Date.now() - gimbalLastFrameReceivedAt > 1200) {
        gimbalFrameFps.value = 0
        gimbalFrameReceivedAt = []
        gimbalLastFrameReceivedAt = 0
      }
      return
    }
    const receivedAt = Date.now()
    if (gimbalLastFrameReceivedAt && receivedAt - gimbalLastFrameReceivedAt > 1200) {
      gimbalFrameFps.value = 0
      gimbalFrameReceivedAt = []
    }
    gimbalFrameReceivedAt = [...gimbalFrameReceivedAt.filter(timestamp => receivedAt - timestamp <= 5000), receivedAt]
    gimbalLastFrameReceivedAt = receivedAt
    if (gimbalFrameReceivedAt.length > 1) {
      const elapsedMs = receivedAt - gimbalFrameReceivedAt[0]!
      gimbalFrameFps.value = elapsedMs > 0 ? (gimbalFrameReceivedAt.length - 1) * 1000 / elapsedMs : 0
    }
    const nextUrl = URL.createObjectURL(frame.blob)
    const previousUrl = gimbalFrameUrl.value
    gimbalFrameUrl.value = nextUrl
    gimbalFrameTimestamp = frame.timestamp
    if (previousUrl) URL.revokeObjectURL(previousUrl)
  } catch {
    // Keep the last valid image while the Gateway reconnects.
    if (gimbalLastFrameReceivedAt && Date.now() - gimbalLastFrameReceivedAt > 1200) {
      gimbalFrameFps.value = 0
      gimbalFrameReceivedAt = []
      gimbalLastFrameReceivedAt = 0
    }
  } finally {
    gimbalFrameRefreshing = false
  }
}

async function sendGimbalCommand(command: string, parameters: GimbalCameraCommand['parameters'] = {}, refreshStatusAfter = true) {
  if (!canGimbalCommand(command)) {
    gimbalMessage.value = '适配器未启用或未声明此指令，未发送设备命令'
    return false
  }
  if (gimbalBusy.value && command !== 'PTZ_STOP') {
    gimbalMessage.value = '上一条云台指令仍在处理'
    return false
  }
  gimbalCommandsInFlight += 1
  gimbalBusy.value = true
  return gimbalCommandQueue.run(async () => {
    try {
      const result = await sendGimbalCameraCommand({ command, parameters })
      gimbalMessage.value = result.status + ': ' + result.detail
      if (refreshStatusAfter) await refreshGimbalStatus()
      return true
    } catch (error) {
      gimbalMessage.value = error instanceof Error ? error.message : '云台指令发送失败'
      return false
    } finally {
      gimbalCommandsInFlight = Math.max(0, gimbalCommandsInFlight - 1)
      gimbalBusy.value = gimbalCommandsInFlight > 0
    }
  })
}

function startGimbalMove(direction: string) {
  if (gimbalBusy.value || !canGimbalCommand('PTZ_MOVE') || !gimbalMoveStopGuard.start()) return
  activeGimbalDirection = direction
  const renewMove = () => {
    if (!gimbalMoveStopGuard.active.value || !activeGimbalDirection || gimbalBusy.value) return
    void sendGimbalCommand('PTZ_MOVE', { direction: activeGimbalDirection, speed: gimbalSpeed.value }, false)
      .then(success => { if (!success && gimbalMoveStopGuard.active.value) stopGimbalMove() })
  }
  renewMove()
  gimbalMoveRenewTimer = window.setInterval(renewMove, 300)
}

function stopGimbalMove() {
  gimbalMoveStopGuard.stop()
}

async function toggleGimbalRecording() {
  const nextState = !gimbalRecording.value
  if (await sendGimbalCommand(nextState ? 'RECORD_START' : 'RECORD_STOP')) gimbalRecording.value = nextState
}

onMounted(async () => {
  viewActive = true
  gimbalMoveStopGuard.attach()
  store.connectFrameStream()
  await Promise.all([store.refreshOverview(), radarStore.refresh(true)])
  void refreshGimbalStatus()
  if (!viewActive) return
  focusedCameraId.value = overview.value.focusedCameraId || sensors.value[0]?.cameraId || 'uav_01'
  activeSourceId.value = focusedCameraId.value
  await store.select(focusedCameraId.value)
  subscribe(focusedCameraId.value)
  await nextTick()
  window.dispatchEvent(new CustomEvent('unity-runtime-track', { detail: { duration: 900 } }))
  void store.refreshFrames(false)
  statusTimer = window.setInterval(() => {
    void store.refreshOverview()
    void store.refreshFrames()
  }, 2500)
  spectrumTimer = window.setInterval(() => void refreshRadar(), 100)
  gimbalTimer = window.setInterval(() => void refreshGimbalStatus(), 3000)
  gimbalFrameTimer = window.setInterval(() => void refreshGimbalFrame(), 100)
})

watch(
  () => store.unityBridgeReady,
  async (ready) => {
    if (!ready || !viewActive) return
    subscribe(focusedCameraId.value)
    await nextTick()
    window.dispatchEvent(new CustomEvent('unity-runtime-track', { detail: { duration: 900 } }))
    void store.refreshFrames(false)
  },
)

onBeforeUnmount(() => {
  gimbalMoveStopGuard.dispose()
  viewActive = false
  bridge.sendFor('SYSTEM_OVERVIEW', 'visualSensorSubscribe', {
    enabled: false,
    focusedCameraId: focusedCameraId.value,
    displayMode: 'off',
    quality: '1080p',
    targetFps: TARGET_FPS,
    gpuDirect: false,
    jpegFallback: false,
  })
  if (statusTimer) window.clearInterval(statusTimer)
  if (spectrumTimer) window.clearInterval(spectrumTimer)
  if (gimbalTimer) window.clearInterval(gimbalTimer)
  if (gimbalFrameTimer) window.clearInterval(gimbalFrameTimer)
  if (gimbalFrameUrl.value) URL.revokeObjectURL(gimbalFrameUrl.value)
  store.disconnectFrameStream()
  store.disposeFrames('SYSTEM_OVERVIEW')
  store.markUnityBridgeReady('SYSTEM_OVERVIEW', false)
})
</script>

<template>
  <ConsoleLayout title="光电视觉" eyebrow="ELECTRO-OPTICAL VISION">
    <p v-if="store.error" role="alert">{{ store.error }}；连接恢复后自动重新获取视频状态。</p>
    <template #actions>
      <span class="top-chip" :class="{ online: linkOnline }"><Zap :size="14" />{{ linkLabel }}</span>
      <span class="top-chip"><Camera :size="14" />{{ onlineSources }}/{{ totalSources }} 路在线</span>
    </template>

    <section class="vision-stage">
      <header class="stage-header">
        <div class="stage-heading">
          <span class="stage-icon"><ScanLine :size="19" /></span>
          <span><b>{{ activeVisionMode === 'relay' ? '七路协同视觉回传' : '云台相机控制台' }}</b><small>{{ activeVisionMode === 'relay' ? '6 路摄像头 + 1 路电子探测仪，点击右侧视角切换主屏' : '独立云台相机 · 实时视频与设备控制' }}</small></span>
        </div>
        <div class="stage-actions">
          <div class="vision-mode-switch" aria-label="光电视觉画面切换">
            <button type="button" :class="{ active: activeVisionMode === 'relay' }" @click="activeVisionMode = 'relay'">协同回传</button>
            <button type="button" :class="{ active: activeVisionMode === 'gimbal' }" @click="activeVisionMode = 'gimbal'">云台相机</button>
          </div>
          <div v-if="activeVisionMode === 'relay'" class="combined-mode" aria-label="二维与三维联合探测视图">
            <span>2D 实时频谱</span><i>+</i><span>3D 时频趋势</span>
          </div>
          <button class="fullscreen-button" type="button" @click="openFullscreen"><Maximize2 :size="15" />全屏</button>
        </div>
      </header>

      <div v-if="activeVisionMode === 'relay'" class="stage-grid">
        <div class="main-column">
          <section class="primary-viewer" :class="{ 'camera-mode': !detectorSelected }">
            <div class="source-identity">
              <span>{{ detectorSelected ? 'ELECTRONIC DETECTOR · 2D + 3D' : 'OPTICAL CAMERA · LIVE' }}</span>
              <b>{{ detectorSelected ? '电子探测仪 01' : (activeCamera ? channelLabel(activeCamera) : '视觉通道') }}</b>
            </div>

            <div v-if="detectorSelected" class="detector-canvas">
              <template v-if="spectrumActive">
                <aside class="detector-summary spectrum-summary">
                  <article><span>扫描频段</span><b>{{ formatGhz(radar?.spectrumStartHz, 2) }} — {{ formatGhz(radar?.spectrumStopHz, 2) }}</b></article>
                  <article><span>峰值频率</span><b>{{ formatGhz(radar?.spectrumPeakHz) }}</b></article>
                  <article><span>峰值功率</span><b>{{ radar?.spectrumPeakDbm?.toFixed(1) ?? '--' }} dBm</b></article>
                  <article><span>设备 / 时间窗</span><b>{{ radar?.spectrumTemperatureC?.toFixed(1) ?? '--' }} ℃ · 100 s</b></article>
                </aside>
                <section class="spectrum-panel">
                  <header><span>SAN60 · JOINT SPECTRUM VIEW</span><b>{{ radar?.spectrumStreamId || radar?.spectrumSensorId || '电子探测仪 01' }} · RBW {{ radar?.spectrumRbwHz ? `${(radar.spectrumRbwHz / 1000).toFixed(0)} kHz` : '--' }}</b></header>
                  <div class="spectrum-comparison">
                    <section class="spectrum-pane">
                      <header><span>二维实时频谱</span><b>FREQUENCY × POWER</b></header>
                      <div class="spectrum-chart">
                        <svg viewBox="0 0 1000 320" preserveAspectRatio="none" role="img" aria-label="实时二维功率频谱">
                          <defs><linearGradient id="spectrum-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5ce0d0" stop-opacity=".46" /><stop offset="1" stop-color="#5ce0d0" stop-opacity="0" /></linearGradient></defs>
                          <path class="spectrum-area" :d="`M0,300 L${spectrumPolyline.split(' ').join(' L')} L1000,300 Z`" />
                          <polyline class="spectrum-line" :points="spectrumPolyline" />
                          <line class="spectrum-peak-line" :x1="spectrumPeakPoint.x" :x2="spectrumPeakPoint.x" y1="20" y2="300" />
                          <circle class="spectrum-peak-dot" :cx="spectrumPeakPoint.x" :cy="spectrumPeakPoint.y" r="7" />
                        </svg>
                        <span class="axis-label y-max">{{ spectrumBounds.max }} dBm</span><span class="axis-label y-min">{{ spectrumBounds.min }} dBm</span>
                        <span class="axis-label x-start">{{ formatGhz(radar?.spectrumStartHz, 2) }}</span><span class="axis-label x-stop">{{ formatGhz(spectrumAxisStopHz, 2) }}</span>
                        <span class="peak-label" :style="{ left: `${spectrumPeakPoint.x / 10}%` }">PEAK {{ radar?.spectrumPeakDbm?.toFixed(1) }} dBm</span>
                      </div>
                    </section>
                    <section class="spectrum-pane">
                      <header><span>三维时频趋势</span><b>FREQUENCY × 100 SEC × POWER</b></header>
                      <SpectrumWaterfall3D
                        class="spectrum-waterfall"
                        :frames="spectrumHistory"
                        :start-hz="radar?.spectrumStartHz"
                        :stop-hz="spectrumAxisStopHz"
                        :min-dbm="spectrumBounds.min"
                        :max-dbm="spectrumBounds.max"
                        :window-seconds="100"
                      />
                    </section>
                  </div>
                </section>
              </template>
              <template v-else>
                <aside class="detector-summary">
                  <article><span>扫描方位</span><b>000° — 360°</b></article>
                  <article><span>有效半径</span><b>{{ (radarRange / 1000).toFixed(1) }} km</b></article>
                  <article><span>发现目标</span><b>{{ String(detectorCount).padStart(2, '0') }}</b></article>
                </aside>
                <div class="radar-wrap"><div class="radar-scope" :class="{ offline: !radar?.connected }">
                  <span class="bearing north">N · 000°</span><span class="bearing east">090°</span><span class="bearing south">180°</span><span class="bearing west">270°</span><i class="radar-sweep" />
                  <button v-for="point in radarPoints" :key="`${point.item.deviceId}-${point.item.id}`" class="radar-point" type="button" :style="point.style" :title="`${point.label} · ${point.item.range?.toFixed(1) ?? '--'} m`"><span>{{ point.label }}</span></button>
                  <div v-if="!radar?.connected || !radarPoints.length" class="radar-empty"><Radio :size="22" /><b>{{ radar?.connected ? '等待电子探测目标' : '等待电子探测数据' }}</b><small>等待二维电子探测数据</small></div>
                </div></div>
                <aside v-if="radar?.latestTargetId" class="target-alert"><span>目标告警 · TRACKING</span><b>{{ radar.latestTargetId }}</b><small>实时电子探测目标</small></aside>
              </template>
            </div>

            <div v-else class="camera-canvas">
              <!-- The main viewer and its selected card intentionally share the
                   same camera-frame URL. The persistent Unity canvas keeps
                   producing frames off-screen, but must not cover this image
                   with its unrelated presentation/global camera. -->
              <img v-if="activeCamera && frames[activeCamera.cameraId]" :src="frames[activeCamera.cameraId]" :alt="channelLabel(activeCamera)" />
              <div v-else class="camera-empty"><Radio :size="24" /><b>正在连接所选相机</b><small>{{ activeCamera?.displayName || '等待选择摄像头' }}</small></div>
              <div class="camera-reticle" aria-hidden="true" />
              <div class="camera-telemetry"><span>{{ activeCamera?.source || 'ROS / UNITY' }}</span><span>{{ activeCamera?.width || '--' }} × {{ activeCamera?.height || '--' }}</span><span>{{ activeCamera?.viewType === 'DOWN' ? '下视' : '前视' }}</span></div>
            </div>
          </section>

          <footer class="source-metrics">
            <article><span class="metric-icon"><ScanLine :size="15" /></span><p><small>当前信号源</small><b>{{ detectorSelected ? '电子探测仪 01 · 2D + 3D' : activeCamera?.displayName }}</b></p></article>
            <article><span class="metric-icon"><Radio :size="15" /></span><p><small>端到端延迟</small><b>{{ detectorSelected ? detectorUpdatedAgo : `${activeCamera?.latencyMs?.toFixed(0) || '--'} ms` }}</b></p></article>
            <article><span class="metric-icon"><Zap :size="15" /></span><p><small>数据刷新率</small><b>{{ detectorSelected ? (spectrumHistoryRate ? `${spectrumHistoryRate.toFixed(1)} Hz` : (radar?.connected ? '实时数据' : '-- Hz')) : `${activeCamera?.fps?.toFixed(1) || stats?.measuredFps?.toFixed(1) || '--'} FPS` }}</b></p></article>
            <article><span class="metric-icon"><Crosshair :size="15" /></span><p><small>接收状态</small><b>{{ detectorSelected ? (radar?.connected ? '电子探测在线' : '等待数据') : (activeCamera?.status === 'ONLINE' ? '实时接收中' : '等待画面') }}</b></p></article>
          </footer>
        </div>

        <aside class="source-rail">
          <header><b>七路信号源</b><span>点击切换主屏</span></header>
          <div class="source-grid">
            <button class="source-card detector-card" :class="{ active: detectorSelected }" type="button" @click="selectSource(DETECTOR_ID)">
              <span class="card-tag"><i :class="{ online: radar?.connected }" />电子探测 · 2D / 3D</span><span class="mini-radar"><i /></span>
              <footer><span><b>电子探测仪 01</b><small>{{ spectrumActive ? `${formatGhz(radar?.spectrumPeakHz)} · ${radar?.spectrumPeakDbm?.toFixed(1)} dBm` : `${detectorCount} 个目标 · ${radar?.connected ? '正在扫描' : '等待数据'}` }}</small></span><em>{{ radar?.connected ? 'ONLINE' : 'OFFLINE' }}</em></footer>
            </button>
            <button v-for="sensor in sensors" :key="sensor.cameraId" class="source-card camera-card" :class="{ active: activeSourceId === sensor.cameraId }" type="button" @click="selectSource(sensor.cameraId)">
              <img v-if="frames[sensor.cameraId]" :src="frames[sensor.cameraId]" :alt="channelLabel(sensor)" /><span v-else class="camera-grid-placeholder" />
              <span class="card-tag"><i :class="{ online: sensor.status === 'ONLINE' }" />{{ channelLabel(sensor) }}</span>
              <footer><span><b>{{ shortDeviceLabel(sensor) }}</b><small>{{ sensor.viewType === 'DOWN' ? '吊舱相机' : '船艏相机' }} · {{ sensor.status === 'ONLINE' ? '实时' : '等待' }}</small></span><em>{{ sensor.fps ? `${sensor.fps.toFixed(0)} FPS` : '-- FPS' }}</em></footer>
            </button>
          </div>
          <footer class="rail-status"><span>当前选择</span><b>{{ detectorSelected ? '电子探测仪 01' : (activeCamera ? channelLabel(activeCamera) : '--') }}</b></footer>
        </aside>
      </div>

      <div v-else class="gimbal-stage-grid">
        <section class="gimbal-video-viewer">
          <div class="source-identity"><span>GIMBAL CAMERA · LIVE</span><b>{{ gimbalStatus?.deviceName || '独立云台相机' }}</b></div>
          <img v-if="gimbalFrameUrl" :src="gimbalFrameUrl" alt="云台相机实时画面" />
          <video v-else-if="gimbalStatus?.connected && gimbalStatus.playbackUrl" :src="gimbalStatus.playbackUrl" autoplay muted playsinline controls />
          <div v-else class="gimbal-video-empty"><Radio :size="28" /><b>等待云台视频帧</b><small>{{ gimbalStatus?.detail || '正在连接 gimbal_01_a10tr · 1280 × 720' }}</small><small>独立 Gateway WebSocket · image/jpeg</small></div>
          <div class="gimbal-osd"><span>实测 {{ gimbalFrameFps ? `${gimbalFrameFps.toFixed(1)} FPS` : '-- FPS' }}</span><span>EO {{ gimbalStatus?.opticalZoom?.toFixed(2) || '--' }}×</span><span>IR {{ gimbalStatus?.infraredZoom?.toFixed(1) || '--' }}×</span><span>LRF -- m</span></div>
          <div class="gimbal-telemetry"><span>Yaw {{ gimbalStatus?.yawDegrees?.toFixed(2) ?? '--' }}°</span><span>Pitch {{ gimbalStatus?.pitchDegrees?.toFixed(2) ?? '--' }}°</span><span>{{ gimbalStatus?.streamProtocol || '等待视频协议' }}</span></div>
        </section>
        <aside class="gimbal-control-panel">
          <div class="gimbal-speed"><b>控制等级</b><input v-model.number="gimbalSpeed" type="range" min="1" max="10"><b>{{ gimbalSpeed }}</b></div>
          <div class="gimbal-control-pad">
            <button class="ptz-up" type="button" :disabled="!canGimbalCommand('PTZ_MOVE') || (gimbalBusy && !gimbalMoveActive)" aria-label="云台上转" @pointerdown="startGimbalMove('UP')" @pointerup="stopGimbalMove" @pointerleave="stopGimbalMove" @pointercancel="stopGimbalMove">⌃</button>
            <button class="ptz-left" type="button" :disabled="!canGimbalCommand('PTZ_MOVE') || (gimbalBusy && !gimbalMoveActive)" aria-label="云台左转" @pointerdown="startGimbalMove('LEFT')" @pointerup="stopGimbalMove" @pointerleave="stopGimbalMove" @pointercancel="stopGimbalMove">‹</button>
            <button class="ptz-home" type="button" :disabled="!canGimbalCommand('PTZ_HOME') || gimbalBusy" @click="sendGimbalCommand('PTZ_HOME')">回中</button>
            <button class="ptz-right" type="button" :disabled="!canGimbalCommand('PTZ_MOVE') || (gimbalBusy && !gimbalMoveActive)" aria-label="云台右转" @pointerdown="startGimbalMove('RIGHT')" @pointerup="stopGimbalMove" @pointerleave="stopGimbalMove" @pointercancel="stopGimbalMove">›</button>
            <button class="ptz-down" type="button" :disabled="!canGimbalCommand('PTZ_MOVE') || (gimbalBusy && !gimbalMoveActive)" aria-label="云台下转" @pointerdown="startGimbalMove('DOWN')" @pointerup="stopGimbalMove" @pointerleave="stopGimbalMove" @pointercancel="stopGimbalMove">⌄</button>
            <div class="gimbal-quick-actions"><button type="button" :disabled="!canGimbalCommand('PTZ_DOWN') || gimbalBusy" @click="sendGimbalCommand('PTZ_DOWN')">一键朝下</button><button type="button" :disabled="!canGimbalCommand('PTZ_FPV') || gimbalBusy" @click="sendGimbalCommand('PTZ_FPV')">FPV 模式</button><button type="button" :disabled="!canGimbalCommand('COPY_COORDINATES') || gimbalBusy" @click="sendGimbalCommand('COPY_COORDINATES')">复制坐标</button></div>
          </div>
          <div class="gimbal-angle-row"><button type="button" :disabled="!canGimbalCommand('PTZ_GOTO') || gimbalBusy" @click="sendGimbalCommand('PTZ_GOTO', { yaw: Number(yawInput), pitch: Number(pitchInput) })">转到</button><label>偏航角<input v-model="yawInput" type="number" min="-180" max="180" step="0.1"></label><label>俯仰角<input v-model="pitchInput" type="number" min="-90" max="90" step="0.1"></label></div>
          <div class="gimbal-tabs"><button class="active" type="button">图像</button><button type="button">跟踪</button><button type="button">测距</button><button type="button">高级</button></div>
          <label class="gimbal-image-mode">图像源<select v-model="imageMode" :disabled="!canGimbalCommand('IMAGE_MODE') || gimbalBusy" @change="sendGimbalCommand('IMAGE_MODE', { mode: imageMode })"><option value="EO1">可见光 EO1</option><option value="IR1">红外 IR1</option><option value="PIP">EO1 + IR1 PIP</option></select></label>
          <div class="gimbal-adjust-row"><span>EO Zoom</span><button type="button" :disabled="!canGimbalCommand('EO_ZOOM')" title="适配器尚未开放经验证的可见光变倍" @click="sendGimbalCommand('EO_ZOOM', { direction: 'IN', speed: 4 })">＋</button><b>{{ gimbalStatus?.opticalZoom?.toFixed(1) || '--' }}</b><button type="button" :disabled="!canGimbalCommand('EO_ZOOM')" title="适配器尚未开放经验证的可见光变倍" @click="sendGimbalCommand('EO_ZOOM', { direction: 'OUT', speed: 4 })">－</button></div>
          <div class="gimbal-adjust-row"><span>IR DZoom</span><button type="button" :disabled="!canGimbalCommand('IR_DZOOM')" title="适配器尚未开放红外变倍" @click="sendGimbalCommand('IR_DZOOM', { direction: 'IN', speed: 1 })">＋</button><b>{{ gimbalStatus?.infraredZoom?.toFixed(1) || '--' }}</b><button type="button" :disabled="!canGimbalCommand('IR_DZOOM')" title="适配器尚未开放红外变倍" @click="sendGimbalCommand('IR_DZOOM', { direction: 'OUT', speed: 1 })">－</button></div>
          <div class="gimbal-adjust-row"><span>EO Focus</span><button type="button" :disabled="!canGimbalCommand('EO_FOCUS')" @click="sendGimbalCommand('EO_FOCUS', { direction: 'NEAR', speed: 4 })">＋</button><b>AF</b><button type="button" :disabled="!canGimbalCommand('EO_FOCUS')" @click="sendGimbalCommand('EO_FOCUS', { direction: 'FAR', speed: 4 })">－</button></div>
          <div class="gimbal-switches"><label>显示九宫格视图层<input type="checkbox" :disabled="!canGimbalCommand('GRID') || gimbalBusy" @change="sendGimbalCommand('GRID', { enabled: ($event.target as HTMLInputElement).checked })"></label><label>数字罗盘<input type="checkbox" :disabled="!canGimbalCommand('COMPASS') || gimbalBusy" @change="sendGimbalCommand('COMPASS', { enabled: ($event.target as HTMLInputElement).checked })"></label><label>手动聚焦<input type="checkbox" :disabled="!canGimbalCommand('EO_FOCUS') || gimbalBusy" @change="sendGimbalCommand('EO_FOCUS', { manual: ($event.target as HTMLInputElement).checked })"></label><label>KLV<input type="checkbox" :disabled="!canGimbalCommand('KLV') || gimbalBusy" @change="sendGimbalCommand('KLV', { enabled: ($event.target as HTMLInputElement).checked })"></label></div>
          <div class="gimbal-capture"><button type="button" :disabled="!canGimbalCommand('SNAPSHOT') || gimbalBusy" @click="sendGimbalCommand('SNAPSHOT')">拍照 (SD)</button><button type="button" :disabled="!canGimbalCommand(gimbalRecording ? 'RECORD_STOP' : 'RECORD_START') || gimbalBusy" @click="toggleGimbalRecording">{{ gimbalRecording ? '停止录像 (SD)' : '录像 (SD)' }}</button></div>
          <p class="gimbal-message" role="status">{{ gimbalMessage }}</p>
        </aside>
      </div>
    </section>
  </ConsoleLayout>
</template>

<style scoped>
.top-chip{display:flex;align-items:center;gap:6px;padding:8px 10px;border:1px solid #24505a;border-radius:6px;color:#82a6aa;font-size:11px}.top-chip.online{color:#55e5b2}
.vision-mode-switch{display:flex;gap:3px;padding:3px;border:1px solid #24505a;border-radius:7px;background:#061a20}.vision-mode-switch button{height:28px;padding:0 10px;border:0;border-radius:5px;background:transparent;color:#82a6aa;cursor:pointer;font-size:10px}.vision-mode-switch button.active{background:#0d3a40;color:#63e2d4}
.vision-stage{height:max(590px,calc(100dvh - 160px));overflow:hidden;border:1px solid #17424d;border-radius:12px;background:#031319ed;box-shadow:inset 0 0 90px #0006,0 14px 50px #0005;color:#e9fbfa}.stage-header{display:flex;height:70px;align-items:center;justify-content:space-between;gap:18px;padding:0 18px 0 20px;border-bottom:1px solid #123a43}.stage-heading{display:flex;min-width:0;align-items:center;gap:14px}.stage-icon{display:grid;width:38px;height:38px;flex:0 0 auto;place-items:center;border:1px solid #347078;border-radius:9px;background:#0a292e;color:#5ce0d0}.stage-heading b{display:block;font-size:16px}.stage-heading small{display:block;margin-top:4px;color:#72989c;font-size:11px}.stage-actions{display:flex;align-items:center;gap:8px}.combined-mode{display:flex;height:36px;align-items:center;gap:8px;padding:0 11px;border:1px solid #28565e;border-radius:7px;background:#061a20;color:#8ab0b2;font-size:10px;font-weight:800}.combined-mode span:first-child{color:#63e2d4}.combined-mode span:last-child{color:#9ccfff}.combined-mode i{color:#527a7e;font-style:normal}.fullscreen-button{display:flex;height:36px;align-items:center;gap:6px;padding:0 11px;border:1px solid #24505a;border-radius:7px;background:#06171b;color:#a7c4c6;cursor:pointer;font-size:11px}.fullscreen-button:hover{border-color:#5ce0d0;color:#fff}
.stage-grid{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:12px;height:calc(100% - 70px);padding:12px}.main-column{display:grid;grid-template-rows:minmax(0,1fr) 88px;gap:10px;min-width:0;min-height:0}.primary-viewer{position:relative;min-width:0;min-height:0;overflow:hidden;border:1px solid #28616c;border-radius:9px;background:radial-gradient(circle at 50% 45%,#092f35,#041920 62%,#021015);isolation:isolate}.primary-viewer::after{position:absolute;z-index:20;inset:0;border:1px solid #57ded01d;box-shadow:inset 0 0 70px #000a;content:"";pointer-events:none}.primary-viewer:fullscreen{width:100vw;height:100vh;border:0;border-radius:0}.source-identity{position:absolute;z-index:30;top:14px;left:14px;display:flex;align-items:center;gap:9px;padding:7px 10px;border:1px solid #31636a;border-radius:6px;background:#031317e8}.source-identity span{color:#5ce0d0;font-size:9px;font-weight:900;letter-spacing:.08em}.source-identity b{font-size:12px}
.detector-canvas,.camera-canvas{position:absolute;inset:0}.detector-summary{position:absolute;z-index:8;top:80px;left:18px;display:grid;gap:8px;width:158px}.detector-summary article{padding:9px 10px;border-left:2px solid #5ce0d0;background:#04171dcf}.detector-summary span{display:block;color:#72979a;font-size:9px}.detector-summary b{display:block;margin-top:3px;font:12px ui-monospace,Consolas,monospace}.radar-wrap{position:absolute;inset:54px 58px 22px;display:grid;place-items:center}.radar-scope{position:relative;height:min(68vh,680px);max-width:96%;max-height:96%;aspect-ratio:1;overflow:hidden;border:1px solid #3f8990;border-radius:50%;background:repeating-radial-gradient(circle,transparent 0 20%,#3b8f8d55 20.2% 20.6%,transparent 20.8% 40%),linear-gradient(90deg,transparent 49.8%,#4ca4a266 50%,transparent 50.2%),linear-gradient(transparent 49.8%,#4ca4a266 50%,transparent 50.2%),radial-gradient(circle,#0b3839 0,#061d25 69%,#031218 100%);box-shadow:0 0 55px #0c777138,inset 0 0 30px #0009}.radar-scope.offline .radar-sweep{opacity:.25;animation-play-state:paused}.radar-sweep{position:absolute;inset:0;border-radius:50%;background:conic-gradient(from 285deg,transparent 0 315deg,#59dfd344 343deg,#82fff688 357deg,transparent 360deg);animation:sweep 4s linear infinite}.bearing{position:absolute;z-index:3;color:#75a8aa;font:9px ui-monospace,monospace}.bearing.north{top:12px;left:50%;transform:translateX(-50%)}.bearing.east{top:50%;right:12px}.bearing.south{bottom:12px;left:50%;transform:translateX(-50%)}.bearing.west{top:50%;left:12px}.radar-point{position:absolute;z-index:5;width:9px;height:9px;padding:0;transform:translate(-50%,-50%);border:2px solid #ffbd4a;border-radius:50%;background:#352b0c;box-shadow:0 0 0 7px #ffbd4a18,0 0 10px #ffbd4a;cursor:pointer}.radar-point span{position:absolute;top:-18px;left:9px;color:#ffd47f;white-space:nowrap;font:9px ui-monospace,monospace}.radar-empty{position:absolute;z-index:6;top:50%;left:50%;display:grid;width:260px;transform:translate(-50%,-50%);place-items:center;gap:7px;padding:18px;color:#7aa4a6;background:#04181bd9;text-align:center}.radar-empty b{color:#b7d2d1;font-size:13px}.radar-empty small{font-size:9px}.target-alert{position:absolute;z-index:8;right:18px;bottom:18px;width:190px;padding:11px;border:1px solid #9a7130;border-radius:7px;background:#171407e8}.target-alert span{color:#ffbd4a;font-size:9px;font-weight:900}.target-alert b{display:block;margin-top:5px;font-size:13px}.target-alert small{color:#b9a172;font-size:9px}
.spectrum-summary{top:82px;width:210px}.spectrum-summary article:first-child b{font-size:10px}.spectrum-panel{position:absolute;inset:72px 20px 28px 238px;display:grid;grid-template-rows:auto minmax(0,1fr);gap:10px;padding:14px;border:1px solid #245b65;border-radius:10px;background:linear-gradient(145deg,#06252cdd,#031319f2);box-shadow:inset 0 0 48px #0a777021}.spectrum-panel>header{display:flex;align-items:center;justify-content:space-between;color:#79aaa9;font:10px ui-monospace,Consolas,monospace;letter-spacing:.07em}.spectrum-panel>header b{color:#d5efed;font-size:11px;letter-spacing:0}.spectrum-comparison{display:grid;grid-template-columns:minmax(0,.94fr) minmax(0,1.06fr);gap:10px;min-width:0;min-height:0}.spectrum-pane{display:grid;grid-template-rows:25px minmax(0,1fr);min-width:0;min-height:0}.spectrum-pane>header{display:flex;align-items:center;justify-content:space-between;padding:0 2px;color:#cbe3e1;font-size:10px}.spectrum-pane>header span{font-weight:800}.spectrum-pane>header b{color:#648c90;font:8px ui-monospace,Consolas,monospace;letter-spacing:.04em}.spectrum-chart,.spectrum-waterfall{position:relative;min-width:0;min-height:0}.spectrum-chart{overflow:hidden;border:1px solid #1e4c55;border-radius:7px;background:linear-gradient(#54c8c81f 1px,transparent 1px),linear-gradient(90deg,#54c8c81f 1px,transparent 1px),radial-gradient(circle at 52% 44%,#0c3a3e,#04191f 68%);background-size:100% 20%,10% 100%,auto}.spectrum-chart svg{position:absolute;inset:24px 16px 32px 42px;width:calc(100% - 58px);height:calc(100% - 56px);overflow:visible}.spectrum-area{fill:url(#spectrum-fill)}.spectrum-line{fill:none;stroke:#65eadc;stroke-width:2;vector-effect:non-scaling-stroke;filter:drop-shadow(0 0 4px #55e5d7aa)}.spectrum-peak-line{stroke:#ffbd4a;stroke-width:1;stroke-dasharray:6 5;vector-effect:non-scaling-stroke}.spectrum-peak-dot{fill:#ffbd4a;stroke:#fff1bd;stroke-width:2;vector-effect:non-scaling-stroke;filter:drop-shadow(0 0 6px #ffbd4a)}.axis-label{position:absolute;color:#759b9e;font:8px ui-monospace,Consolas,monospace}.axis-label.y-max{top:10px;left:7px}.axis-label.y-min{bottom:27px;left:7px}.axis-label.x-start{bottom:9px;left:42px}.axis-label.x-stop{right:14px;bottom:9px}.peak-label{position:absolute;top:8px;max-width:130px;transform:translateX(-50%);padding:3px 5px;border:1px solid #8a662b;border-radius:4px;background:#181506e8;color:#ffd073;white-space:nowrap;font:8px ui-monospace,Consolas,monospace}
.camera-canvas{background:radial-gradient(circle at 55% 45%,#174450 0,#0a2b35 37%,#04171e 78%)}.camera-canvas::before{position:absolute;inset:0;opacity:.18;background:linear-gradient(#73aab0 1px,transparent 1px),linear-gradient(90deg,#73aab0 1px,transparent 1px);background-size:60px 60px;content:""}.unity-runtime-anchor,.camera-canvas>img{position:absolute;z-index:2;inset:0;width:100%;height:100%}.camera-canvas>img{object-fit:contain;background:#020a0e;image-rendering:auto}.camera-empty{position:absolute;z-index:3;inset:0;display:grid;place-content:center;justify-items:center;gap:7px;color:#6e979b}.camera-empty b{color:#bad1d2;font-size:13px}.camera-empty small{font-size:10px}.camera-reticle{position:absolute;z-index:8;top:50%;left:50%;width:70px;height:70px;transform:translate(-50%,-50%);border:1px solid #bcece977;border-radius:50%;pointer-events:none}.camera-reticle::before,.camera-reticle::after{position:absolute;background:#bcece977;content:""}.camera-reticle::before{top:50%;left:-24px;width:118px;height:1px}.camera-reticle::after{top:-24px;left:50%;width:1px;height:118px}.camera-telemetry{position:absolute;z-index:10;bottom:16px;left:16px;display:flex;gap:6px}.camera-telemetry span{padding:6px 8px;border:1px solid #315b63;border-radius:4px;background:#03151bdc;color:#bad3d4;font:9px ui-monospace,monospace}
.source-metrics{display:grid;grid-template-columns:1.3fr 1fr 1fr 1fr;gap:8px}.source-metrics article{display:flex;min-width:0;align-items:center;gap:10px;padding:0 12px;border:1px solid #1b4650;border-radius:7px;background:#06191f}.metric-icon{display:grid;width:30px;height:30px;flex:0 0 auto;place-items:center;border-radius:6px;background:#0b2a31;color:#5ce0d0}.source-metrics p{min-width:0;margin:0}.source-metrics small{display:block;color:#70969b;font-size:9px}.source-metrics b{display:block;overflow:hidden;margin-top:3px;font-size:11px;white-space:nowrap;text-overflow:ellipsis}
.source-rail{display:grid;grid-template-rows:auto minmax(0,1fr) 50px;min-width:0;min-height:0;border:1px solid #1d4a55;border-radius:9px;background:#04161c}.source-rail>header{display:flex;align-items:center;justify-content:space-between;padding:13px 14px 10px}.source-rail>header b{font-size:14px}.source-rail>header span{color:#70979c;font-size:10px}.source-grid{display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1.08fr repeat(3,minmax(0,1fr));gap:7px;min-height:0;padding:0 10px 10px}.source-card{position:relative;min-width:0;min-height:0;overflow:hidden;padding:0;border:1px solid #285a64;border-radius:7px;background:#082029;color:#e9fbfa;cursor:pointer;text-align:left;transition:border-color .2s ease,transform .2s ease,box-shadow .2s ease}.source-card:hover{transform:translateY(-1px);border-color:#50bdb6}.source-card.active{border-color:#5ce0d0;box-shadow:0 0 0 1px #5ce0d055,0 10px 22px #0008}.detector-card{grid-column:1/-1;background:#08272c}.mini-radar{position:absolute;inset:-42% 8% -54% 34%;border:1px solid #58b7b544;border-radius:50%;background:repeating-radial-gradient(circle,transparent 0 19%,#58b7b533 20% 20.8%,transparent 21% 39%),linear-gradient(90deg,transparent 49.6%,#5fc9c555 50%,transparent 50.4%),linear-gradient(transparent 49.6%,#5fc9c555 50%,transparent 50.4%)}.mini-radar i{position:absolute;top:38%;left:68%;width:7px;height:7px;border-radius:50%;background:#ffbd4a;box-shadow:0 0 9px #ffbd4a}.camera-card>img,.camera-grid-placeholder{position:absolute;inset:0;width:100%;height:100%}.camera-card>img{object-fit:cover}.camera-grid-placeholder{opacity:.7;background:radial-gradient(circle at 60% 35%,#1f4b57,#071d25 63%),linear-gradient(#7ab0b744 1px,transparent 1px),linear-gradient(90deg,#7ab0b744 1px,transparent 1px);background-size:auto,34px 34px,34px 34px}.card-tag{position:absolute;z-index:3;top:7px;left:7px;display:flex;align-items:center;gap:5px;padding:4px 6px;border:1px solid #35636b;border-radius:4px;background:#03151cdd;font-size:9px}.card-tag i{width:5px;height:5px;border-radius:50%;background:#607b7e}.card-tag i.online{background:#54d8a3;box-shadow:0 0 7px #54d8a3}.source-card footer{position:absolute;z-index:3;right:0;bottom:0;left:0;display:flex;align-items:flex-end;justify-content:space-between;padding:20px 8px 7px;background:linear-gradient(transparent,#021116ee)}.source-card footer b{display:block;font-size:10px}.source-card footer small{display:block;margin-top:2px;color:#7ba0a4;font-size:8px}.source-card footer em{color:#88b0b2;font:normal 8px ui-monospace,monospace}.rail-status{display:flex;align-items:center;justify-content:space-between;padding:0 13px;border-top:1px solid #173e47;color:#779a9e;font-size:9px}.rail-status b{color:#cae1e0;font-size:10px}
.gimbal-stage-grid{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:12px;height:calc(100% - 70px);min-height:0;padding:12px}.gimbal-video-viewer{position:relative;min-width:0;min-height:0;overflow:hidden;border:1px solid #303849;border-radius:9px;background:radial-gradient(circle at 45% 45%,#383c43,#16191e 62%,#080a0d);isolation:isolate}.gimbal-video-viewer:fullscreen{width:100vw;height:100vh;border:0;border-radius:0}.gimbal-video-viewer video{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#05070a}.gimbal-video-empty{position:absolute;inset:0;display:grid;align-content:center;justify-items:center;gap:9px;color:#99a4b5;text-align:center}.gimbal-video-empty b{color:#e1e7f2;font-size:15px}.gimbal-video-empty small{max-width:80%;color:#8995a7}.gimbal-osd{position:absolute;top:20px;left:18px;display:grid;gap:4px;color:#38ed4e;font:16px ui-monospace,Consolas,monospace;text-shadow:0 1px 4px #000}.gimbal-telemetry{position:absolute;right:14px;bottom:14px;left:14px;display:flex;gap:8px}.gimbal-telemetry span{padding:6px 9px;border:1px solid #485163;border-radius:4px;background:#10151bd9;color:#d0d7e2;font:10px ui-monospace,Consolas,monospace}.gimbal-control-panel{display:flex;min-height:0;flex-direction:column;gap:8px;overflow:auto;padding:12px;border:1px solid #3b4054;border-radius:11px;background:#1a1d2b;color:#edf1fb}.gimbal-speed{display:flex;align-items:center;gap:10px}.gimbal-speed input{min-width:0;flex:1;accent-color:#4b78f0}.gimbal-control-pad{position:relative;display:grid;height:212px;flex:0 0 212px;place-items:center;border:1px solid #596075;background:#242737}.gimbal-control-pad:before{width:166px;height:166px;border:1px solid #454b60;border-radius:50%;background:radial-gradient(circle,#222638 0 25%,#151927 26% 55%,#292d3c 56%);content:""}.gimbal-control-pad>button{position:absolute;z-index:2;border:0;background:transparent;color:#a9b1c5;font-size:25px;cursor:pointer}.gimbal-control-pad>button:disabled,.gimbal-control-panel button:disabled{opacity:.45;cursor:not-allowed}.gimbal-control-pad .ptz-up{top:28px}.gimbal-control-pad .ptz-down{bottom:28px}.gimbal-control-pad .ptz-left{left:calc(50% - 79px)}.gimbal-control-pad .ptz-right{right:calc(50% - 79px)}.gimbal-control-pad .ptz-home{width:52px;height:52px;border:1px solid #626a81;border-radius:50%;color:#fff;font-size:11px}.gimbal-quick-actions{position:absolute;top:23px;right:9px;display:grid;gap:8px;width:83px}.gimbal-quick-actions button,.gimbal-angle-row>button,.gimbal-adjust-row button{min-height:32px;border:1px solid #535a70;border-radius:5px;background:#555d74;color:#f3f5fa;cursor:pointer;font-size:10px}.gimbal-angle-row{display:grid;grid-template-columns:50px minmax(0,1fr) minmax(0,1fr);gap:6px;align-items:center}.gimbal-angle-row label{display:grid;gap:4px;color:#aeb6c8;font-size:9px}.gimbal-angle-row input,.gimbal-image-mode select{width:100%;min-width:0;padding:7px;border:1px solid #555b70;border-radius:4px;background:#272b3a;color:#fff}.gimbal-tabs{display:grid;grid-template-columns:repeat(4,1fr);overflow:hidden;border-radius:18px;background:#111421}.gimbal-tabs button{padding:9px 4px;border:0;background:transparent;color:#abb3c5;cursor:pointer}.gimbal-tabs button.active{background:#2a2e41;color:#fff}.gimbal-image-mode{display:grid;grid-template-columns:74px 1fr;gap:8px;align-items:center;color:#aeb6c8;font-size:10px}.gimbal-adjust-row{display:grid;grid-template-columns:85px 36px minmax(24px,1fr) 36px;gap:6px;align-items:center;color:#c9d0de;font-size:10px}.gimbal-adjust-row b{padding:7px;border:1px solid #4d5368;border-radius:4px;background:#272b3a;text-align:center}.gimbal-switches{display:grid;gap:7px;padding-top:4px;border-top:1px solid #3c4254}.gimbal-switches label{display:flex;align-items:center;justify-content:space-between;color:#c7cedb;font-size:10px}.gimbal-switches input{accent-color:#3471ee}.gimbal-capture{display:grid;gap:7px}.gimbal-capture button{height:36px;border:0;border-radius:5px;background:#2869ed;color:white;cursor:pointer}.gimbal-capture button+button{background:#555d74}.gimbal-message{margin:0;padding:7px;border:1px solid #3b4355;border-radius:5px;color:#aeb9ce;font-size:9px}
@keyframes sweep{to{transform:rotate(360deg)}}
@media(max-width:1420px){.stage-grid{grid-template-columns:minmax(0,1fr) 290px}.detector-summary{width:140px}.spectrum-summary{width:160px}.spectrum-panel{left:196px;right:14px}.spectrum-pane>header b{display:none}.source-metrics{grid-template-columns:1.2fr 1fr 1fr 1fr}.metric-icon{display:none}}
@media(max-height:820px){.vision-stage{height:max(540px,calc(100dvh - 146px))}.stage-header{height:58px}.stage-grid{height:calc(100% - 58px)}.main-column{grid-template-rows:minmax(0,1fr) 70px}.detector-summary{top:68px}.spectrum-panel{top:60px}.radar-wrap{inset:48px 50px 16px}}
@media(max-width:1100px){.gimbal-stage-grid{grid-template-columns:minmax(0,1fr) 320px}}
@media(max-width:900px){.vision-stage:has(.gimbal-stage-grid){height:auto;min-height:0;overflow:visible}.gimbal-stage-grid{grid-template-columns:1fr;height:auto}.gimbal-video-viewer{height:480px;min-height:400px}.gimbal-control-panel{max-height:720px}}
@container workspace (max-width:900px){.vision-stage{height:auto;min-height:0;overflow:visible}.stage-header{height:auto;min-height:70px;flex-wrap:wrap;padding-block:10px}.stage-grid{grid-template-columns:1fr;height:auto}.main-column{grid-template-rows:520px auto}.source-metrics{grid-template-columns:1fr 1fr}.source-rail{grid-template-rows:auto 650px 50px}.radar-wrap{inset:70px 36px 20px}.spectrum-summary{top:68px;left:12px;width:150px}.spectrum-panel{inset:62px 18px 20px 180px;padding:12px}}
@media(prefers-reduced-motion:reduce){.radar-sweep{animation:none}.source-card{transition:none}}
.gimbal-video-viewer>img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#05070a}
</style>

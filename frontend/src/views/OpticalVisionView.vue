<script setup lang="ts">
import { Camera, Crosshair, Maximize2, Radio, ScanLine, Zap } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'

import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import SpectrumWaterfall3D from '@/components/vision/SpectrumWaterfall3D.vue'
import { useRadarSensorStore } from '@/stores/radarSensor'
import { useUnityBridgeStore } from '@/stores/unityBridge'
import { useVisualSensorStore } from '@/stores/visualSensor'
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
const activeSourceId = ref(DETECTOR_ID)
const focusedCameraId = ref('uav_01')
const spectrumHistory = shallowRef<SpectrumHistoryFrame[]>([])
let statusTimer: number | undefined
let spectrumTimer: number | undefined
let radarRefreshing = false
let viewActive = false
let lastSpectrumKey = ''
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
const detectorCount = computed(() => radar.value?.detectionCount ?? 0)
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
  const ranges = (radar.value?.items ?? [])
    .map(item => item.range ?? (item.x != null && item.y != null ? Math.hypot(item.x, item.y) : 0))
    .filter(value => Number.isFinite(value) && value > 0)
  return Math.max(100, ...ranges)
})
const radarPoints = computed(() => (radar.value?.items ?? []).slice(0, 80).map((item, index) => ({
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
  const viewer = document.querySelector<HTMLElement>('.primary-viewer')
  if (!viewer) return
  if (document.fullscreenElement) await document.exitFullscreen()
  else await viewer.requestFullscreen?.()
}

onMounted(async () => {
  viewActive = true
  store.connectFrameStream()
  await Promise.all([store.refreshOverview(), radarStore.refresh(true)])
  if (!viewActive) return
  focusedCameraId.value = overview.value.focusedCameraId || sensors.value[0]?.cameraId || 'uav_01'
  subscribe(focusedCameraId.value)
  await nextTick()
  window.dispatchEvent(new CustomEvent('unity-runtime-track', { detail: { duration: 900 } }))
  void store.refreshFrames(false)
  statusTimer = window.setInterval(() => {
    void store.refreshOverview()
    void store.refreshFrames()
  }, 2500)
  spectrumTimer = window.setInterval(() => void refreshRadar(), 100)
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
          <span><b>七路协同视觉回传</b><small>6 路摄像头 + 1 路电子探测仪，点击右侧视角切换主屏</small></span>
        </div>
        <div class="stage-actions">
          <div class="combined-mode" aria-label="二维与三维联合探测视图">
            <span>2D 实时频谱</span><i>+</i><span>3D 时频趋势</span>
          </div>
          <button class="fullscreen-button" type="button" @click="openFullscreen"><Maximize2 :size="15" />全屏</button>
        </div>
      </header>

      <div class="stage-grid">
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
    </section>
  </ConsoleLayout>
</template>

<style scoped>
.top-chip{display:flex;align-items:center;gap:6px;padding:8px 10px;border:1px solid #24505a;border-radius:6px;color:#82a6aa;font-size:11px}.top-chip.online{color:#55e5b2}
.vision-stage{height:max(590px,calc(100dvh - 160px));overflow:hidden;border:1px solid #17424d;border-radius:12px;background:#031319ed;box-shadow:inset 0 0 90px #0006,0 14px 50px #0005;color:#e9fbfa}.stage-header{display:flex;height:70px;align-items:center;justify-content:space-between;gap:18px;padding:0 18px 0 20px;border-bottom:1px solid #123a43}.stage-heading{display:flex;min-width:0;align-items:center;gap:14px}.stage-icon{display:grid;width:38px;height:38px;flex:0 0 auto;place-items:center;border:1px solid #347078;border-radius:9px;background:#0a292e;color:#5ce0d0}.stage-heading b{display:block;font-size:16px}.stage-heading small{display:block;margin-top:4px;color:#72989c;font-size:11px}.stage-actions{display:flex;align-items:center;gap:8px}.combined-mode{display:flex;height:36px;align-items:center;gap:8px;padding:0 11px;border:1px solid #28565e;border-radius:7px;background:#061a20;color:#8ab0b2;font-size:10px;font-weight:800}.combined-mode span:first-child{color:#63e2d4}.combined-mode span:last-child{color:#9ccfff}.combined-mode i{color:#527a7e;font-style:normal}.fullscreen-button{display:flex;height:36px;align-items:center;gap:6px;padding:0 11px;border:1px solid #24505a;border-radius:7px;background:#06171b;color:#a7c4c6;cursor:pointer;font-size:11px}.fullscreen-button:hover{border-color:#5ce0d0;color:#fff}
.stage-grid{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:12px;height:calc(100% - 70px);padding:12px}.main-column{display:grid;grid-template-rows:minmax(0,1fr) 88px;gap:10px;min-width:0;min-height:0}.primary-viewer{position:relative;min-width:0;min-height:0;overflow:hidden;border:1px solid #28616c;border-radius:9px;background:radial-gradient(circle at 50% 45%,#092f35,#041920 62%,#021015);isolation:isolate}.primary-viewer::after{position:absolute;z-index:20;inset:0;border:1px solid #57ded01d;box-shadow:inset 0 0 70px #000a;content:"";pointer-events:none}.primary-viewer:fullscreen{width:100vw;height:100vh;border:0;border-radius:0}.source-identity{position:absolute;z-index:30;top:14px;left:14px;display:flex;align-items:center;gap:9px;padding:7px 10px;border:1px solid #31636a;border-radius:6px;background:#031317e8}.source-identity span{color:#5ce0d0;font-size:9px;font-weight:900;letter-spacing:.08em}.source-identity b{font-size:12px}
.detector-canvas,.camera-canvas{position:absolute;inset:0}.detector-summary{position:absolute;z-index:8;top:80px;left:18px;display:grid;gap:8px;width:158px}.detector-summary article{padding:9px 10px;border-left:2px solid #5ce0d0;background:#04171dcf}.detector-summary span{display:block;color:#72979a;font-size:9px}.detector-summary b{display:block;margin-top:3px;font:12px ui-monospace,Consolas,monospace}.radar-wrap{position:absolute;inset:54px 58px 22px;display:grid;place-items:center}.radar-scope{position:relative;height:min(68vh,680px);max-width:96%;max-height:96%;aspect-ratio:1;overflow:hidden;border:1px solid #3f8990;border-radius:50%;background:repeating-radial-gradient(circle,transparent 0 20%,#3b8f8d55 20.2% 20.6%,transparent 20.8% 40%),linear-gradient(90deg,transparent 49.8%,#4ca4a266 50%,transparent 50.2%),linear-gradient(transparent 49.8%,#4ca4a266 50%,transparent 50.2%),radial-gradient(circle,#0b3839 0,#061d25 69%,#031218 100%);box-shadow:0 0 55px #0c777138,inset 0 0 30px #0009}.radar-scope.offline .radar-sweep{opacity:.25;animation-play-state:paused}.radar-sweep{position:absolute;inset:0;border-radius:50%;background:conic-gradient(from 285deg,transparent 0 315deg,#59dfd344 343deg,#82fff688 357deg,transparent 360deg);animation:sweep 4s linear infinite}.bearing{position:absolute;z-index:3;color:#75a8aa;font:9px ui-monospace,monospace}.bearing.north{top:12px;left:50%;transform:translateX(-50%)}.bearing.east{top:50%;right:12px}.bearing.south{bottom:12px;left:50%;transform:translateX(-50%)}.bearing.west{top:50%;left:12px}.radar-point{position:absolute;z-index:5;width:9px;height:9px;padding:0;transform:translate(-50%,-50%);border:2px solid #ffbd4a;border-radius:50%;background:#352b0c;box-shadow:0 0 0 7px #ffbd4a18,0 0 10px #ffbd4a;cursor:pointer}.radar-point span{position:absolute;top:-18px;left:9px;color:#ffd47f;white-space:nowrap;font:9px ui-monospace,monospace}.radar-empty{position:absolute;z-index:6;top:50%;left:50%;display:grid;width:260px;transform:translate(-50%,-50%);place-items:center;gap:7px;padding:18px;color:#7aa4a6;background:#04181bd9;text-align:center}.radar-empty b{color:#b7d2d1;font-size:13px}.radar-empty small{font-size:9px}.target-alert{position:absolute;z-index:8;right:18px;bottom:18px;width:190px;padding:11px;border:1px solid #9a7130;border-radius:7px;background:#171407e8}.target-alert span{color:#ffbd4a;font-size:9px;font-weight:900}.target-alert b{display:block;margin-top:5px;font-size:13px}.target-alert small{color:#b9a172;font-size:9px}
.spectrum-summary{top:82px;width:210px}.spectrum-summary article:first-child b{font-size:10px}.spectrum-panel{position:absolute;inset:72px 20px 28px 238px;display:grid;grid-template-rows:auto minmax(0,1fr);gap:10px;padding:14px;border:1px solid #245b65;border-radius:10px;background:linear-gradient(145deg,#06252cdd,#031319f2);box-shadow:inset 0 0 48px #0a777021}.spectrum-panel>header{display:flex;align-items:center;justify-content:space-between;color:#79aaa9;font:10px ui-monospace,Consolas,monospace;letter-spacing:.07em}.spectrum-panel>header b{color:#d5efed;font-size:11px;letter-spacing:0}.spectrum-comparison{display:grid;grid-template-columns:minmax(0,.94fr) minmax(0,1.06fr);gap:10px;min-width:0;min-height:0}.spectrum-pane{display:grid;grid-template-rows:25px minmax(0,1fr);min-width:0;min-height:0}.spectrum-pane>header{display:flex;align-items:center;justify-content:space-between;padding:0 2px;color:#cbe3e1;font-size:10px}.spectrum-pane>header span{font-weight:800}.spectrum-pane>header b{color:#648c90;font:8px ui-monospace,Consolas,monospace;letter-spacing:.04em}.spectrum-chart,.spectrum-waterfall{position:relative;min-width:0;min-height:0}.spectrum-chart{overflow:hidden;border:1px solid #1e4c55;border-radius:7px;background:linear-gradient(#54c8c81f 1px,transparent 1px),linear-gradient(90deg,#54c8c81f 1px,transparent 1px),radial-gradient(circle at 52% 44%,#0c3a3e,#04191f 68%);background-size:100% 20%,10% 100%,auto}.spectrum-chart svg{position:absolute;inset:24px 16px 32px 42px;width:calc(100% - 58px);height:calc(100% - 56px);overflow:visible}.spectrum-area{fill:url(#spectrum-fill)}.spectrum-line{fill:none;stroke:#65eadc;stroke-width:2;vector-effect:non-scaling-stroke;filter:drop-shadow(0 0 4px #55e5d7aa)}.spectrum-peak-line{stroke:#ffbd4a;stroke-width:1;stroke-dasharray:6 5;vector-effect:non-scaling-stroke}.spectrum-peak-dot{fill:#ffbd4a;stroke:#fff1bd;stroke-width:2;vector-effect:non-scaling-stroke;filter:drop-shadow(0 0 6px #ffbd4a)}.axis-label{position:absolute;color:#759b9e;font:8px ui-monospace,Consolas,monospace}.axis-label.y-max{top:10px;left:7px}.axis-label.y-min{bottom:27px;left:7px}.axis-label.x-start{bottom:9px;left:42px}.axis-label.x-stop{right:14px;bottom:9px}.peak-label{position:absolute;top:8px;max-width:130px;transform:translateX(-50%);padding:3px 5px;border:1px solid #8a662b;border-radius:4px;background:#181506e8;color:#ffd073;white-space:nowrap;font:8px ui-monospace,Consolas,monospace}
.camera-canvas{background:radial-gradient(circle at 55% 45%,#174450 0,#0a2b35 37%,#04171e 78%)}.camera-canvas::before{position:absolute;inset:0;opacity:.18;background:linear-gradient(#73aab0 1px,transparent 1px),linear-gradient(90deg,#73aab0 1px,transparent 1px);background-size:60px 60px;content:""}.unity-runtime-anchor,.camera-canvas>img{position:absolute;z-index:2;inset:0;width:100%;height:100%}.camera-canvas>img{object-fit:contain;background:#020a0e;image-rendering:auto}.camera-empty{position:absolute;z-index:3;inset:0;display:grid;place-content:center;justify-items:center;gap:7px;color:#6e979b}.camera-empty b{color:#bad1d2;font-size:13px}.camera-empty small{font-size:10px}.camera-reticle{position:absolute;z-index:8;top:50%;left:50%;width:70px;height:70px;transform:translate(-50%,-50%);border:1px solid #bcece977;border-radius:50%;pointer-events:none}.camera-reticle::before,.camera-reticle::after{position:absolute;background:#bcece977;content:""}.camera-reticle::before{top:50%;left:-24px;width:118px;height:1px}.camera-reticle::after{top:-24px;left:50%;width:1px;height:118px}.camera-telemetry{position:absolute;z-index:10;bottom:16px;left:16px;display:flex;gap:6px}.camera-telemetry span{padding:6px 8px;border:1px solid #315b63;border-radius:4px;background:#03151bdc;color:#bad3d4;font:9px ui-monospace,monospace}
.source-metrics{display:grid;grid-template-columns:1.3fr 1fr 1fr 1fr;gap:8px}.source-metrics article{display:flex;min-width:0;align-items:center;gap:10px;padding:0 12px;border:1px solid #1b4650;border-radius:7px;background:#06191f}.metric-icon{display:grid;width:30px;height:30px;flex:0 0 auto;place-items:center;border-radius:6px;background:#0b2a31;color:#5ce0d0}.source-metrics p{min-width:0;margin:0}.source-metrics small{display:block;color:#70969b;font-size:9px}.source-metrics b{display:block;overflow:hidden;margin-top:3px;font-size:11px;white-space:nowrap;text-overflow:ellipsis}
.source-rail{display:grid;grid-template-rows:auto minmax(0,1fr) 50px;min-width:0;min-height:0;border:1px solid #1d4a55;border-radius:9px;background:#04161c}.source-rail>header{display:flex;align-items:center;justify-content:space-between;padding:13px 14px 10px}.source-rail>header b{font-size:14px}.source-rail>header span{color:#70979c;font-size:10px}.source-grid{display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1.08fr repeat(3,minmax(0,1fr));gap:7px;min-height:0;padding:0 10px 10px}.source-card{position:relative;min-width:0;min-height:0;overflow:hidden;padding:0;border:1px solid #285a64;border-radius:7px;background:#082029;color:#e9fbfa;cursor:pointer;text-align:left;transition:border-color .2s ease,transform .2s ease,box-shadow .2s ease}.source-card:hover{transform:translateY(-1px);border-color:#50bdb6}.source-card.active{border-color:#5ce0d0;box-shadow:0 0 0 1px #5ce0d055,0 10px 22px #0008}.detector-card{grid-column:1/-1;background:#08272c}.mini-radar{position:absolute;inset:-42% 8% -54% 34%;border:1px solid #58b7b544;border-radius:50%;background:repeating-radial-gradient(circle,transparent 0 19%,#58b7b533 20% 20.8%,transparent 21% 39%),linear-gradient(90deg,transparent 49.6%,#5fc9c555 50%,transparent 50.4%),linear-gradient(transparent 49.6%,#5fc9c555 50%,transparent 50.4%)}.mini-radar i{position:absolute;top:38%;left:68%;width:7px;height:7px;border-radius:50%;background:#ffbd4a;box-shadow:0 0 9px #ffbd4a}.camera-card>img,.camera-grid-placeholder{position:absolute;inset:0;width:100%;height:100%}.camera-card>img{object-fit:cover}.camera-grid-placeholder{opacity:.7;background:radial-gradient(circle at 60% 35%,#1f4b57,#071d25 63%),linear-gradient(#7ab0b744 1px,transparent 1px),linear-gradient(90deg,#7ab0b744 1px,transparent 1px);background-size:auto,34px 34px,34px 34px}.card-tag{position:absolute;z-index:3;top:7px;left:7px;display:flex;align-items:center;gap:5px;padding:4px 6px;border:1px solid #35636b;border-radius:4px;background:#03151cdd;font-size:9px}.card-tag i{width:5px;height:5px;border-radius:50%;background:#607b7e}.card-tag i.online{background:#54d8a3;box-shadow:0 0 7px #54d8a3}.source-card footer{position:absolute;z-index:3;right:0;bottom:0;left:0;display:flex;align-items:flex-end;justify-content:space-between;padding:20px 8px 7px;background:linear-gradient(transparent,#021116ee)}.source-card footer b{display:block;font-size:10px}.source-card footer small{display:block;margin-top:2px;color:#7ba0a4;font-size:8px}.source-card footer em{color:#88b0b2;font:normal 8px ui-monospace,monospace}.rail-status{display:flex;align-items:center;justify-content:space-between;padding:0 13px;border-top:1px solid #173e47;color:#779a9e;font-size:9px}.rail-status b{color:#cae1e0;font-size:10px}
@keyframes sweep{to{transform:rotate(360deg)}}
@media(max-width:1420px){.stage-grid{grid-template-columns:minmax(0,1fr) 290px}.detector-summary{width:140px}.spectrum-summary{width:160px}.spectrum-panel{left:196px;right:14px}.spectrum-pane>header b{display:none}.source-metrics{grid-template-columns:1.2fr 1fr 1fr 1fr}.metric-icon{display:none}}
@media(max-height:820px){.vision-stage{height:max(540px,calc(100dvh - 146px))}.stage-header{height:58px}.stage-grid{height:calc(100% - 58px)}.main-column{grid-template-rows:minmax(0,1fr) 70px}.detector-summary{top:68px}.spectrum-panel{top:60px}.radar-wrap{inset:48px 50px 16px}}
@container workspace (max-width:900px){.vision-stage{height:auto;min-height:0;overflow:visible}.stage-header{height:auto;min-height:70px;flex-wrap:wrap;padding-block:10px}.stage-grid{grid-template-columns:1fr;height:auto}.main-column{grid-template-rows:520px auto}.source-metrics{grid-template-columns:1fr 1fr}.source-rail{grid-template-rows:auto 650px 50px}.radar-wrap{inset:70px 36px 20px}.spectrum-summary{top:68px;left:12px;width:150px}.spectrum-panel{inset:62px 18px 20px 180px;padding:12px}}
@media(prefers-reduced-motion:reduce){.radar-sweep{animation:none}.source-card{transition:none}}
</style>

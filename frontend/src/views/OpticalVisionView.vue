<script setup lang="ts">
import { Camera, Radio, Zap } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import { useRadarSensorStore } from '@/stores/radarSensor'
import { useUnityBridgeStore } from '@/stores/unityBridge'
import { useVisualSensorStore } from '@/stores/visualSensor'
import type { VisualSensor } from '@/types/visualSensor'

const store = useVisualSensorStore()
const radarStore = useRadarSensorStore()
const bridge = useUnityBridgeStore()
const quality = ref<'720p' | '1080p'>('1080p')
const focusedCameraId = ref('')
const switching = ref(false)
const targetFps = 30
const feedElements = new Map<string, HTMLElement>()
let timer: number | undefined
let viewActive = false

const overview = computed(() => store.displayOverview)
const sensors = computed(() => overview.value.sensors)
const frames = computed(() => store.channels.SYSTEM_OVERVIEW.frameUrls)
const stats = computed(() => store.streamStats)
const focused = computed(() =>
  sensors.value.find(item => item.cameraId === focusedCameraId.value) ?? sensors.value[0],
)
const detection = computed(() => radarStore.overview?.latestTargetId
  ? { id: radarStore.overview.latestTargetId, count: radarStore.overview.detectionCount }
  : null)
const online = computed(() => store.unityBridgeReady || overview.value.gatewayConnected)
const linkLabel = computed(() =>
  store.unityBridgeReady
    ? 'Unity 视觉链路在线'
    : overview.value.gatewayConnected
      ? 'ROS 视觉链路在线'
      : '等待视觉链路',
)

function channelLabel(sensor: VisualSensor) {
  return `${sensor.deviceType === 'UAV' ? '空中视角' : '水面视角'} ${sensor.cameraId.slice(-2)}`
}

function setFeedElement(cameraId: string, element: unknown) {
  if (element instanceof HTMLElement) feedElements.set(cameraId, element)
  else feedElements.delete(cameraId)
}

function slotClass(cameraId: string) {
  if (cameraId === focusedCameraId.value) return 'focus'
  const index = sensors.value
    .filter(item => item.cameraId !== focusedCameraId.value)
    .findIndex(item => item.cameraId === cameraId)
  return `slot-${Math.max(0, index) + 1}`
}

function subscribe(cameraId = focused.value?.cameraId || 'uav_01') {
  if (!viewActive) return
  bridge.sendFor('SYSTEM_OVERVIEW', 'visualSensorSubscribe', {
    enabled: true,
    focusedCameraId: cameraId,
    displayMode: 'focus',
    quality: quality.value,
    targetFps,
    gpuDirect: true,
    jpegFallback: true,
    thumbnailFps: 2,
    focusedFps: targetFps,
  })
}

async function refreshFocusedStream(cameraId: string) {
  await store.select(cameraId)
  subscribe(cameraId)
  await nextTick()
  window.dispatchEvent(new CustomEvent('unity-runtime-track', { detail: { duration: 900 } }))
  await store.refreshFrames(false)
}

async function switchFocus(cameraId: string) {
  if (switching.value || cameraId === focusedCameraId.value) return
  const before = new Map<string, DOMRect>()
  feedElements.forEach((element, id) => before.set(id, element.getBoundingClientRect()))

  switching.value = true
  focusedCameraId.value = cameraId
  void store.select(cameraId)
  subscribe(cameraId)
  await nextTick()

  const animations: Animation[] = []
  feedElements.forEach((element, id) => {
    const first = before.get(id)
    if (!first) return
    const last = element.getBoundingClientRect()
    const deltaX = first.left - last.left
    const deltaY = first.top - last.top
    const scaleX = first.width / Math.max(1, last.width)
    const scaleY = first.height / Math.max(1, last.height)
    if (Math.abs(deltaX) < 1 && Math.abs(deltaY) < 1 && Math.abs(scaleX - 1) < .01 && Math.abs(scaleY - 1) < .01) return
    animations.push(element.animate([
      { transformOrigin: 'top left', transform: `translate(${deltaX}px, ${deltaY}px) scale(${scaleX}, ${scaleY})` },
      { transformOrigin: 'top left', transform: 'translate(0, 0) scale(1, 1)' },
    ], { duration: 380, easing: 'cubic-bezier(.22,.8,.22,1)' }))
  })

  window.dispatchEvent(new CustomEvent('unity-runtime-track', { detail: { duration: 480 } }))
  await Promise.allSettled(animations.map(animation => animation.finished))
  switching.value = false
  void store.refreshFrames(false)
}

onMounted(async () => {
  viewActive = true
  store.connectFrameStream()
  await Promise.all([store.refreshOverview(), radarStore.refresh(true)])
  if (!viewActive) return
  focusedCameraId.value = overview.value.focusedCameraId || sensors.value[0]?.cameraId || 'uav_01'
  await refreshFocusedStream(focusedCameraId.value)
  if (!viewActive) return
  timer = window.setInterval(() => {
    void store.refreshOverview()
    void store.refreshFrames()
    void radarStore.refresh(true)
  }, 2500)
})

watch(
  () => store.unityBridgeReady,
  async (ready) => {
    if (!ready || !viewActive) return
    subscribe(focusedCameraId.value || focused.value?.cameraId || 'uav_01')
    await nextTick()
    window.dispatchEvent(new CustomEvent('unity-runtime-track', { detail: { duration: 900 } }))
    void store.refreshFrames(false)
  },
)

onBeforeUnmount(() => {
  viewActive = false
  bridge.sendFor('SYSTEM_OVERVIEW', 'visualSensorSubscribe', {
    enabled: false,
    focusedCameraId: focusedCameraId.value || 'uav_01',
    displayMode: 'off',
    quality: quality.value,
    targetFps,
    gpuDirect: false,
    jpegFallback: false,
  })
  if (timer) window.clearInterval(timer)
  store.disconnectFrameStream()
  store.disposeFrames('SYSTEM_OVERVIEW')
  store.markUnityBridgeReady('SYSTEM_OVERVIEW', false)
})
</script>

<template>
  <ConsoleLayout title="光电视觉" eyebrow="ELECTRO-OPTICAL VISION">
    <p v-if="store.error" role="alert">{{ store.error }}；连接恢复后自动重新获取视频状态。</p>
    <template #actions>
      <span class="top-chip" :class="{ online }"><Zap :size="14" />{{ linkLabel }}</span>
      <span class="top-chip"><Camera :size="14" />{{ overview.onlineCount }}/{{ overview.totalCount }} 路在线</span>
    </template>

    <section class="optical-stage">
      <header class="stage-title">
        <span>LIVE OPTICAL NETWORK</span>
        <b>六路协同视觉回传</b>
        <small>点击任意副视角，与当前关注画面平滑交换</small>
      </header>

      <div class="quality">
        <button :class="{ active: quality === '720p' }" @click="quality = '720p'; subscribe()">720P</button>
        <button :class="{ active: quality === '1080p' }" @click="quality = '1080p'; subscribe()">1080P</button>
      </div>

      <div class="feed-field" :class="{ switching }">
        <button
          v-for="sensor in sensors"
          :key="sensor.cameraId"
          :ref="element => setFeedElement(sensor.cameraId, element)"
          type="button"
          class="feed"
          :class="[
            slotClass(sensor.cameraId),
            {
              focused: sensor.cameraId === focusedCameraId,
              'runtime-active': sensor.cameraId === focusedCameraId && store.unityBridgeReady && !frames[sensor.cameraId],
            },
          ]"
          :aria-label="`切换到${channelLabel(sensor)}`"
          @click="switchFocus(sensor.cameraId)"
        >
          <div
            v-if="sensor.cameraId === focusedCameraId && store.unityBridgeReady"
            class="unity-runtime-anchor"
            data-unity-runtime-viewport="visual-sensors-live"
          />
          <img v-if="frames[sensor.cameraId]" :src="frames[sensor.cameraId]" :alt="channelLabel(sensor)" />
          <div v-else-if="sensor.cameraId !== focusedCameraId || !store.unityBridgeReady" class="empty">
            <Radio :size="20" />
            <span>{{ sensor.cameraId === focusedCameraId ? (store.unityBridgeReady ? '正在连接 Unity 视觉' : '正在加载 ROS 图像') : '等待该通道帧' }}</span>
          </div>
          <div v-else class="focus-status"><span>Unity GPU 实时画面</span></div>

          <span class="feed-label">
            <b v-if="sensor.cameraId === focusedCameraId">当前关注</b>
            {{ channelLabel(sensor) }}
            <i :class="{ online: sensor.status === 'ONLINE' }" />
          </span>

          <aside v-if="sensor.cameraId === focusedCameraId && detection" class="detection">
            <span>智能识别 · TARGET DETECTED</span>
            <b>{{ detection.id }}</b>
            <small>真实检测事件 · 累计 {{ detection.count }}</small>
          </aside>
        </button>
      </div>

      <footer class="stream-metrics">
        <article><span>实时帧率</span><b>{{ (stats?.measuredFps || focused?.fps)?.toFixed(1) || '--' }} FPS</b></article>
        <article><span>帧新鲜度</span><b>{{ focused && focused.latencyMs >= 0 ? focused.latencyMs.toFixed(0) : '--' }} ms</b></article>
        <article><span>在线通道</span><b>{{ overview.onlineCount }}/{{ overview.totalCount }}</b></article>
        <article><span>当前链路</span><b>{{ overview.gatewayDetail }}</b></article>
      </footer>
    </section>
  </ConsoleLayout>
</template>

<style scoped>
.top-chip{display:flex;align-items:center;gap:6px;padding:8px 10px;border:1px solid #24505a;border-radius:6px;color:#82a6aa;font-size:11px}.top-chip.online{color:#55e5b2}
.optical-stage{position:relative;height:calc(100dvh - 174px);min-height:610px;overflow:hidden;border:1px solid #17424d;border-radius:12px;background:radial-gradient(circle at 45% 40%,#092630 0,#041820 46%,#020d13 82%);box-shadow:inset 0 0 90px #0008;color:#e9fbfa}
.stage-title{position:absolute;z-index:8;left:22px;top:18px;display:grid;gap:3px}.stage-title span{color:#51d7e8;font-size:9px;letter-spacing:.17em}.stage-title b{font-size:18px}.stage-title small{color:#70969b}
.quality{position:absolute;z-index:8;right:20px;top:18px;display:flex;padding:3px;border:1px solid #24505a;border-radius:7px;background:#04151c}.quality button{padding:7px 13px;border:0;border-radius:5px;background:transparent;color:#73989d;cursor:pointer}.quality button.active{background:#61dccf;color:#001216;font-weight:800}
.feed-field{position:absolute;z-index:4;inset:88px 22px 82px;display:grid;grid-template-columns:repeat(12,minmax(0,1fr));grid-template-rows:repeat(10,minmax(0,1fr));gap:10px;min-width:0;min-height:0;isolation:isolate}.feed-field.switching{pointer-events:none}
.feed{position:relative;min-width:0;min-height:0;overflow:hidden;padding:0;border:1px solid #286270;border-radius:8px;background:#061821;box-shadow:0 12px 28px #0007;cursor:pointer;contain:layout paint;will-change:transform}.feed:hover{border-color:#59dcd2}.feed.focused{border-color:#58ddd1;box-shadow:0 18px 44px #000a,0 0 0 1px #58ddd133}.feed.runtime-active{background:transparent}
.feed.focus{grid-column:1/7;grid-row:1/7}.feed.slot-1{grid-column:7/13;grid-row:1/4}.feed.slot-2{grid-column:7/13;grid-row:4/7}.feed.slot-3{grid-column:1/5;grid-row:7/11}.feed.slot-4{grid-column:5/9;grid-row:7/11}.feed.slot-5{grid-column:9/13;grid-row:7/11}
.feed img,.unity-runtime-anchor{position:absolute;inset:0;width:100%;height:100%}.feed img{z-index:2;object-fit:cover}.unity-runtime-anchor{z-index:0;pointer-events:none}.empty{position:absolute;z-index:2;inset:0;display:grid;place-content:center;justify-items:center;gap:7px;color:#668c91;background:linear-gradient(145deg,#092630,#041820)}.focus-status{position:absolute;z-index:3;right:10px;bottom:9px;padding:5px 8px;color:#68dcd2;border:1px solid #245762;border-radius:4px;background:#03161dcc;font-size:9px}
.feed-label{position:absolute;z-index:5;left:9px;top:8px;display:flex;align-items:center;gap:6px;padding:5px 8px;color:#e5f7f5;border:1px solid #2a5963;border-radius:5px;background:#03151ddd;font-size:10px}.feed-label b{color:#58e0d4}.feed-label i{width:6px;height:6px;border-radius:50%;background:#5b7377}.feed-label i.online{background:#52e5ae;box-shadow:0 0 7px #52e5ae}
.detection{position:absolute;z-index:5;left:14px;bottom:14px;display:grid;gap:4px;max-width:230px;padding:10px 13px;color:#fff;border:1px solid #ba8d32;border-radius:6px;background:#171407e8;text-align:left}.detection span{color:#ffc64e;font-size:8px;letter-spacing:.08em}.detection small{color:#bba56f;font-size:9px}
.stream-metrics{position:absolute;z-index:8;right:22px;bottom:14px;left:22px;display:grid;grid-template-columns:.75fr .75fr .65fr 1.8fr;gap:8px}.stream-metrics article{display:grid;grid-template-columns:auto 1fr;align-items:center;gap:4px 12px;min-width:0;padding:8px 12px;border:1px solid #1e4b56;border-radius:6px;background:#041820dd}.stream-metrics span{color:#70979c;font-size:9px}.stream-metrics b{overflow:hidden;color:#dff7f4;font-size:11px;white-space:nowrap;text-overflow:ellipsis}
@media(max-width:1280px){.feed-field{inset:84px 16px 78px;gap:8px}.feed.focus{grid-column:1/7}.stream-metrics{right:16px;left:16px}.stage-title{left:16px}.quality{right:16px}}
@media(max-width:900px){.optical-stage{height:auto;min-height:900px}.feed-field{grid-template-columns:repeat(2,minmax(0,1fr));grid-template-rows:repeat(8,100px)}.feed.focus{grid-column:1/3;grid-row:1/4}.feed.slot-1{grid-column:1;grid-row:4/6}.feed.slot-2{grid-column:2;grid-row:4/6}.feed.slot-3{grid-column:1;grid-row:6/8}.feed.slot-4{grid-column:2;grid-row:6/8}.feed.slot-5{grid-column:1/3;grid-row:8/9}.stream-metrics{grid-template-columns:1fr 1fr}}
@media(prefers-reduced-motion:reduce){.feed{will-change:auto}}
/* Keep all six feeds in document flow on narrow workspaces. */
.optical-stage { height: max(480px, calc(100dvh - 156px)); min-height: 480px; }
@container workspace (max-width: 900px) {
  .optical-stage { display: flex; flex-direction: column; gap: 12px; height: auto; min-height: 0; padding: 14px; }
  .stage-title, .quality, .feed-field, .stream-metrics { position: relative; inset: auto; }
  .quality { align-self: flex-start; }
  .feed-field { grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: none; grid-auto-rows: 180px; gap: 8px; }
  .feed.focus { grid-column: 1 / -1; grid-row: auto; }
  .feed.slot-1, .feed.slot-2, .feed.slot-3, .feed.slot-4, .feed.slot-5 { grid-column: auto; grid-row: auto; }
  .stream-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@container workspace (max-width: 460px) {
  .feed-field, .stream-metrics { grid-template-columns: minmax(0, 1fr); }
  .quality { flex-wrap: wrap; }
}
</style>

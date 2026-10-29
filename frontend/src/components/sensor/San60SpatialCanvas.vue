<script setup lang="ts">
import { Maximize2, RotateCcw, ZoomIn, ZoomOut } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { San60SpatialOverview, San60SpatialTrackSample } from '@/types/sensor'

const props = defineProps<{ snapshot: San60SpatialOverview | null }>()
const canvas = ref<HTMLCanvasElement | null>(null)
const yaw = ref(-0.72)
const pitch = ref(0.58)
const zoom = ref(1)
const pan = ref({ x: 0, y: 0 })
const sampleCount = computed(() => props.snapshot?.spatial?.track.length ?? 0)
const cellCount = computed(() => props.snapshot?.spatial?.cells.length ?? 0)
let resizeObserver: ResizeObserver | undefined
let dragPoint: { x: number; y: number; mode: 'rotate' | 'pan' } | null = null
let pulseTimer: number | undefined

function resetView() {
  yaw.value = -0.72
  pitch.value = 0.58
  zoom.value = 1
  pan.value = { x: 0, y: 0 }
  draw()
}

function fitView() {
  zoom.value = 1
  pan.value = { x: 0, y: 0 }
  draw()
}

function changeZoom(delta: number) {
  zoom.value = Math.max(0.55, Math.min(3.5, zoom.value + delta))
  draw()
}

function handleWheel(event: WheelEvent) {
  event.preventDefault()
  changeZoom(event.deltaY < 0 ? 0.12 : -0.12)
}

function powerColor(dbm: number, alpha = 1) {
  const stops = [
    { at: 0, color: [43, 105, 226] },
    { at: 0.42, color: [47, 216, 203] },
    { at: 0.72, color: [244, 213, 76] },
    { at: 1, color: [245, 91, 62] },
  ]
  const t = Math.max(0, Math.min(1, (dbm + 110) / 60))
  const index = t < 0.42 ? 0 : t < 0.72 ? 1 : 2
  const left = stops[index]!
  const right = stops[index + 1]!
  const local = (t - left.at) / (right.at - left.at)
  const rgb = left.color.map((value, channel) => Math.round(value + (right.color[channel]! - value) * local))
  return `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})`
}

function draw() {
  const element = canvas.value
  const context = element?.getContext('2d')
  if (!element || !context) return
  const bounds = element.getBoundingClientRect()
  const width = Math.max(1, bounds.width)
  const height = Math.max(1, bounds.height)
  const ratio = Math.max(1, window.devicePixelRatio || 1)
  element.width = Math.round(width * ratio)
  element.height = Math.round(height * ratio)
  context.setTransform(ratio, 0, 0, ratio, 0, 0)
  context.clearRect(0, 0, width, height)

  const spatial = props.snapshot?.spatial
  const track = spatial?.track ?? []
  const cells = spatial?.cells ?? []
  const field = spatial?.field ?? []
  if (track.length === 0 && cells.length === 0 && field.length === 0) {
    context.fillStyle = '#72979a'
    context.font = '14px sans-serif'
    context.textAlign = 'center'
    context.fillText('等待 BAG 空间测量数据', width / 2, height / 2)
    return
  }

  const positions: number[][] = [
    ...track.map(sample => sample.p),
    ...cells.filter(cell => cell.length >= 4).map(cell => cell.slice(0, 3)),
    ...field.filter(point => point.length >= 4).map(point => point.slice(0, 3)),
  ]
  if (positions.length === 0) return
  const center = [0, 1, 2].map(axis => positions.reduce((total, point) => total + (point[axis] ?? 0), 0) / positions.length)
  const cosYaw = Math.cos(yaw.value)
  const sinYaw = Math.sin(yaw.value)
  const cosPitch = Math.cos(pitch.value)
  const sinPitch = Math.sin(pitch.value)
  const rotate = (point: number[]) => {
    const x = (point[0] ?? 0) - center[0]!
    const y = (point[1] ?? 0) - center[1]!
    const z = (point[2] ?? 0) - center[2]!
    const horizontal = x * cosYaw - y * sinYaw
    const depth = x * sinYaw + y * cosYaw
    return { x: horizontal, y: depth * cosPitch - z * sinPitch, depth: depth * sinPitch + z * cosPitch }
  }
  const rotated = positions.map(rotate)
  const minX = Math.min(...rotated.map(point => point.x))
  const maxX = Math.max(...rotated.map(point => point.x))
  const minY = Math.min(...rotated.map(point => point.y))
  const maxY = Math.max(...rotated.map(point => point.y))
  const span = Math.max(maxX - minX, maxY - minY, 0.1)
  const scale = Math.min((width - 110) / span, (height - 120) / span) * zoom.value
  const originX = width / 2 - (minX + maxX) * scale / 2 + pan.value.x
  const originY = height / 2 + (minY + maxY) * scale / 2 + 10 + pan.value.y
  const project = (point: number[]) => {
    const value = rotate(point)
    return { x: originX + value.x * scale, y: originY - value.y * scale, depth: value.depth }
  }

  context.strokeStyle = 'rgba(93, 190, 190, .28)'
  context.lineWidth = 1
  const origin = center.map((value, index) => value + (index === 2 ? Math.min(...positions.map(point => point[2] ?? 0)) - center[2]! : 0))
  const floorCorners = [
    [Math.min(...positions.map(point => point[0] ?? 0)), Math.min(...positions.map(point => point[1] ?? 0)), origin[2]!],
    [Math.max(...positions.map(point => point[0] ?? 0)), Math.min(...positions.map(point => point[1] ?? 0)), origin[2]!],
    [Math.max(...positions.map(point => point[0] ?? 0)), Math.max(...positions.map(point => point[1] ?? 0)), origin[2]!],
    [Math.min(...positions.map(point => point[0] ?? 0)), Math.max(...positions.map(point => point[1] ?? 0)), origin[2]!],
  ]
  for (let index = 0; index < floorCorners.length; index += 1) {
    const start = project(floorCorners[index]!)
    const end = project(floorCorners[(index + 1) % floorCorners.length]!)
    context.beginPath()
    context.moveTo(start.x, start.y)
    context.lineTo(end.x, end.y)
    context.stroke()
  }
  const gridSteps = 8
  for (let step = 1; step < gridSteps; step += 1) {
    const ratio = step / gridSteps
    const x1 = floorCorners[0]![0]! + (floorCorners[1]![0]! - floorCorners[0]![0]!) * ratio
    const y1 = floorCorners[0]![1]! + (floorCorners[3]![1]! - floorCorners[0]![1]!) * ratio
    const northA = project([x1, floorCorners[0]![1]!, origin[2]!])
    const northB = project([x1, floorCorners[2]![1]!, origin[2]!])
    const eastA = project([floorCorners[0]![0]!, y1, origin[2]!])
    const eastB = project([floorCorners[1]![0]!, y1, origin[2]!])
    context.beginPath()
    context.moveTo(northA.x, northA.y)
    context.lineTo(northB.x, northB.y)
    context.moveTo(eastA.x, eastA.y)
    context.lineTo(eastB.x, eastB.y)
    context.stroke()
  }

  context.fillStyle = 'rgba(147, 210, 211, .68)'
  context.font = '10px ui-monospace, Consolas, monospace'
  context.textAlign = 'center'
  const xMin = floorCorners[0]![0]!
  const xMax = floorCorners[1]![0]!
  const yMin = floorCorners[0]![1]!
  const yMax = floorCorners[3]![1]!
  for (let step = 0; step <= gridSteps; step += 1) {
    const ratio = step / gridSteps
    const x = xMin + (xMax - xMin) * ratio
    const y = yMin + (yMax - yMin) * ratio
    const xPoint = project([x, yMin, origin[2]!])
    const yPoint = project([xMin, y, origin[2]!])
    context.fillText(`${x.toFixed(0)}m`, xPoint.x, xPoint.y + 14)
    context.fillText(`${y.toFixed(0)}m`, yPoint.x - 16, yPoint.y + 4)
  }

  const axisOrigin = project(origin)
  const axisLength = Math.max(span * 0.24, 0.1)
  const axisEndEast = project([origin[0]! + axisLength, origin[1]!, origin[2]!])
  const axisEndWest = project([origin[0]! - axisLength, origin[1]!, origin[2]!])
  const axisEndNorth = project([origin[0]!, origin[1]! + axisLength, origin[2]!])
  const axisEndSouth = project([origin[0]!, origin[1]! - axisLength, origin[2]!])
  const axisEndUp = project([origin[0]!, origin[1]!, origin[2]! + axisLength])
  const axisEndDown = project([origin[0]!, origin[1]!, origin[2]! - axisLength])
  context.font = '11px ui-monospace, Consolas, monospace'
  for (const [end, label, color] of [
    [axisEndEast, 'E · East', '#51d5c9'], [axisEndWest, 'W · West', '#51d5c9'],
    [axisEndNorth, 'N · North', '#83b5ff'], [axisEndSouth, 'S · South', '#83b5ff'],
    [axisEndUp, 'U · Up', '#ffbd4a'], [axisEndDown, 'D · Down', '#ffbd4a'],
  ] as const) {
    context.strokeStyle = color
    context.fillStyle = color
    context.beginPath()
    context.moveTo(axisOrigin.x, axisOrigin.y)
    context.lineTo(end.x, end.y)
    context.stroke()
    context.fillText(label, end.x + (end.x >= axisOrigin.x ? 7 : -52), end.y - 6)
  }

  const orderedField = field.filter(point => point.length >= 4).map(point => ({ value: point, point: project(point) })).sort((a, b) => a.point.depth - b.point.depth)
  for (const { value, point } of orderedField) {
    context.globalAlpha = Math.max(0.18, Math.min(0.72, 0.34 + (point.depth - minY) / Math.max(span, 0.1) * 0.28))
    context.fillStyle = powerColor(value[3] ?? -100, 0.72)
    context.beginPath()
    context.arc(point.x, point.y, 2, 0, Math.PI * 2)
    context.fill()
    context.globalAlpha = 1
  }
  const orderedCells = cells.filter(cell => cell.length >= 4).map(cell => ({ cell, point: project(cell) })).sort((a, b) => a.point.depth - b.point.depth)
  for (const { cell, point } of orderedCells) {
    const depthScale = Math.max(0.62, Math.min(1.24, 0.86 + point.depth / Math.max(span, 0.1) * 0.18))
    context.globalAlpha = Math.max(0.42, Math.min(0.94, depthScale))
    context.fillStyle = powerColor(cell[3] ?? -100, 0.88)
    const size = Math.max(4, Math.min(15, (spatial?.voxel_size ?? 2) * scale * 0.58 * depthScale))
    context.fillRect(point.x - size / 2, point.y - size / 2, size, size)
    context.strokeStyle = 'rgba(255, 255, 255, .16)'
    context.strokeRect(point.x - size / 2, point.y - size / 2, size, size)
    context.globalAlpha = 1
  }

  if (track.length > 1) {
    const colors = ['#5ce0d0', '#63c9ff', '#7d9dff', '#bb83f0', '#ffbd4a']
    for (let index = 1; index < track.length; index += 1) {
      const previous = project(track[index - 1]!.p)
      const current = project(track[index]!.p)
      context.strokeStyle = colors[Math.floor(index / Math.max(1, track.length / colors.length)) % colors.length]!
      context.lineWidth = 2.5 + Math.min(2, index / Math.max(1, track.length) * 1.5)
      context.beginPath()
      context.moveTo(previous.x, previous.y)
      context.lineTo(current.x, current.y)
      context.stroke()
      if (index === track.length - 1 || index % Math.max(3, Math.ceil(track.length / 10)) === 0) {
        const angle = Math.atan2(current.y - previous.y, current.x - previous.x)
        const head = 7
        context.fillStyle = context.strokeStyle
        context.beginPath()
        context.moveTo(current.x, current.y)
        context.lineTo(current.x - Math.cos(angle - 0.55) * head, current.y - Math.sin(angle - 0.55) * head)
        context.lineTo(current.x - Math.cos(angle + 0.55) * head, current.y - Math.sin(angle + 0.55) * head)
        context.closePath()
        context.fill()
      }
    }
    const previous = track[track.length - 2]!
    const last = track[track.length - 1]!
    const dt = Math.max(0.05, (last.time ?? 0) - (previous.time ?? 0))
    const velocity = [0, 1, 2].map(index => ((last.p[index] ?? 0) - (previous.p[index] ?? 0)) / dt)
    const speed = Math.hypot(...velocity)
    if (Number.isFinite(speed) && speed > 0.001) {
      context.setLineDash([7, 6])
      context.strokeStyle = '#ffbd4a'
      context.lineWidth = 1.8
      context.beginPath()
      const start = project(last.p)
      context.moveTo(start.x, start.y)
      const prediction = [0, 1, 2].map(index => (last.p[index] ?? 0) + velocity[index]! * Math.min(5, Math.max(0.8, dt * 5)))
      const end = project(prediction)
      context.lineTo(end.x, end.y)
      context.stroke()
      context.setLineDash([])
    }
  }
  for (const sample of track) {
    const point = project(sample.p)
    context.fillStyle = powerColor(sample.dbm)
    context.beginPath()
    context.arc(point.x, point.y, 4, 0, Math.PI * 2)
    context.fill()
  }
  const lastSample = track.length ? track[track.length - 1] : undefined
  if (lastSample) {
    const point = project(lastSample.p)
    const pulse = 10 + (Math.sin(Date.now() / 230) + 1) * 4
    context.strokeStyle = 'rgba(255, 189, 74, .45)'
    context.lineWidth = 2
    context.beginPath()
    context.arc(point.x, point.y, pulse, 0, Math.PI * 2)
    context.stroke()
    context.fillStyle = '#ffbd4a'
    context.beginPath()
    context.arc(point.x, point.y, 6, 0, Math.PI * 2)
    context.fill()
    context.strokeStyle = '#fff1bd'
    context.stroke()
  }
  const hotspot = spatial?.hotspot?.p
  if (hotspot?.length === 3) {
    const point = project(hotspot)
    context.strokeStyle = '#ff965c'
    context.lineWidth = 2
    context.beginPath()
    context.arc(point.x, point.y, 10, 0, Math.PI * 2)
    context.stroke()
  }
}

function startDrag(event: PointerEvent) {
  event.preventDefault()
  dragPoint = { x: event.clientX, y: event.clientY, mode: event.button === 2 ? 'pan' : 'rotate' }
  canvas.value?.setPointerCapture(event.pointerId)
}

function moveDrag(event: PointerEvent) {
  if (!dragPoint) return
  if (dragPoint.mode === 'pan') {
    pan.value = { x: pan.value.x + event.clientX - dragPoint.x, y: pan.value.y + event.clientY - dragPoint.y }
  } else {
    yaw.value += (event.clientX - dragPoint.x) * 0.009
    pitch.value = Math.max(-1.35, Math.min(1.35, pitch.value + (event.clientY - dragPoint.y) * 0.009))
  }
  dragPoint = { x: event.clientX, y: event.clientY, mode: dragPoint.mode }
  draw()
}

function stopDrag() {
  dragPoint = null
}

watch(() => props.snapshot, draw, { deep: true })
onMounted(() => {
  draw()
  pulseTimer = window.setInterval(draw, 90)
  if (typeof ResizeObserver !== 'undefined' && canvas.value?.parentElement) {
    resizeObserver = new ResizeObserver(draw)
    resizeObserver.observe(canvas.value.parentElement)
  }
})
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  if (pulseTimer) window.clearInterval(pulseTimer)
})
</script>

<template>
  <section class="spatial-view" aria-label="SAN60 三维电磁空间态势">
    <canvas ref="canvas" @pointerdown="startDrag" @pointermove="moveDrag" @pointerup="stopDrag" @pointercancel="stopDrag" @contextmenu.prevent @wheel="handleWheel" />
    <div class="spatial-title">
      <b>SAN60 BAG · 三维电磁空间态势</b>
      <span>{{ sampleCount }} 个轨迹点 · {{ cellCount }} 个实测体素 · {{ Math.round(zoom * 100) }}%</span>
    </div>
    <div class="spatial-toolbar" aria-label="三维视图控制">
      <button type="button" title="放大" aria-label="放大" @click="changeZoom(0.2)"><ZoomIn :size="14" /></button>
      <button type="button" title="缩小" aria-label="缩小" @click="changeZoom(-0.2)"><ZoomOut :size="14" /></button>
      <button type="button" title="适配全部目标" aria-label="适配全部目标" @click="fitView"><Maximize2 :size="14" /></button>
      <button type="button" title="重置视角和缩放" aria-label="重置视角和缩放" @click="resetView"><RotateCcw :size="14" /></button>
    </div>
    <div class="spatial-legend" aria-label="图例">
      <span><i class="low" />低功率</span><span><i class="hot" />高功率</span><span><i class="track" />轨迹</span><span><i class="current" />当前位置</span><span><i class="hotspot" />热点</span>
      <small>滚轮缩放 · 左拖旋转 · 右拖平移</small>
    </div>
    <div v-if="snapshot?.spatial?.reason" class="spatial-reason">{{ snapshot.spatial.reason }}</div>
    <div class="spatial-offline">{{ snapshot?.connected ? '回放数据接收中' : '已接收的空间测量' }} · ENU 相对坐标，单位 m</div>
  </section>
</template>

<style scoped>
.spatial-view{position:absolute;inset:0;overflow:hidden;background:radial-gradient(ellipse at 50% 45%,#0b3037,#03161c 72%);touch-action:none}
.spatial-view canvas{position:absolute;inset:0;width:100%;height:100%;cursor:grab}
.spatial-view canvas:active{cursor:grabbing}
.spatial-title,.spatial-legend,.spatial-reason,.spatial-offline{position:absolute;z-index:3;padding:8px 10px;border:1px solid #24545d;border-radius:5px;background:#03151bdd;color:#90b7b8;font:10px ui-monospace,Consolas,monospace;pointer-events:none}
.spatial-title{top:62px;left:16px;display:grid;gap:4px}
.spatial-title b{color:#ddf5f1;font-size:12px}
.spatial-toolbar{position:absolute;z-index:4;top:16px;right:16px;display:flex;gap:5px}
.spatial-toolbar button{display:grid;width:31px;height:31px;place-items:center;padding:0;border:1px solid #326873;border-radius:5px;background:#041a20e8;color:#b9e5e1;cursor:pointer}
.spatial-toolbar button:hover{border-color:#5ce0d0;background:#0b3338;color:#fff}
.spatial-legend{top:57px;right:16px;display:flex;flex-wrap:wrap;justify-content:flex-end;gap:7px 10px;max-width:min(560px,calc(100% - 32px))}
.spatial-legend span{display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
.spatial-legend i{display:inline-block;width:9px;height:9px;border-radius:50%;background:#2fd8cb}
.spatial-legend i.low{background:#2b69e2}.spatial-legend i.hot{background:#f55b3e}.spatial-legend i.track{width:14px;height:3px;border-radius:0;background:#c6fbf4}.spatial-legend i.current{background:#ffbd4a;box-shadow:0 0 0 2px #fff1bd55}.spatial-legend i.hotspot{background:transparent;border:2px solid #ff965c}
.spatial-legend small{flex-basis:100%;color:#648d90;text-align:right}
.spatial-reason{bottom:16px;left:16px;max-width:55%;color:#b5d1d0}
.spatial-offline{right:16px;bottom:16px;color:#688e92}
@media(max-width:720px){.spatial-title{top:58px;left:12px}.spatial-title b{font-size:10px}.spatial-toolbar{top:12px;right:12px}.spatial-legend{top:53px;right:12px;font-size:9px}.spatial-reason{bottom:12px;left:12px;max-width:70%}.spatial-offline{right:12px;bottom:12px}}
</style>

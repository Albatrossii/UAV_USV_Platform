<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { RadarItem } from '@/types/sensor'

const props = defineProps<{ items: RadarItem[]; selectedId?: string }>()
const emit = defineEmits<{ select: [item: RadarItem] }>()
const canvas = ref<HTMLCanvasElement | null>(null)
const zoom = ref(1)
const autoFocus = ref(true)
const panX = ref(0)
const panY = ref(0)
const viewMode = computed(() => autoFocus.value ? '点云聚焦 · 局部量程' : (zoom.value > 1.01 || Math.hypot(panX.value, panY.value) > 0.01 ? '自由视图 · 局部量程' : '雷达全局量程'))
let animationFrame = 0
let sweepAngle = 0
let lastTime = 0
let baseMaxRange = 100
let dragging = false
let dragged = false
let pointerX = 0
let pointerY = 0
let projected: Array<{ item: RadarItem; x: number; y: number }> = []

function displayBearing(bearing: number) {
  return (360 - bearing) % 360
}

function polar(item: RadarItem) {
  if (item.range != null && item.bearing != null) {
    const angle = displayBearing(item.bearing) * Math.PI / 180
    return { x: Math.sin(angle) * item.range, y: Math.cos(angle) * item.range }
  }
  if (item.x != null && item.y != null) return { x: -item.y, y: item.x }
  return null
}

function pointEntries() {
  return props.items.map(item => ({ item, value: polar(item) })).filter((entry): entry is { item: RadarItem; value: { x: number; y: number } } => Boolean(entry.value))
}

function percentile(values: number[], ratio: number) {
  if (!values.length) return 0
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.floor((sorted.length - 1) * ratio)))]!
}

function fitPointCloud() {
  const cloud = pointEntries().filter(entry => entry.item.kind === 'POINTCLOUD')
  const points = cloud.length >= 4 ? cloud : pointEntries()
  if (!points.length) {
    resetView()
    return
  }
  const lowX = percentile(points.map(entry => entry.value.x), .01)
  const highX = percentile(points.map(entry => entry.value.x), .99)
  const lowY = percentile(points.map(entry => entry.value.y), .01)
  const highY = percentile(points.map(entry => entry.value.y), .99)
  panX.value = (lowX + highX) / 2
  panY.value = (lowY + highY) / 2
  const halfSpan = Math.max(5, (highX - lowX) / 2, (highY - lowY) / 2) * 1.18
  zoom.value = Math.max(1, Math.min(24, baseMaxRange / halfSpan))
  autoFocus.value = true
}

function resetView() {
  zoom.value = 1
  panX.value = 0
  panY.value = 0
  autoFocus.value = false
}

function changeZoom(factor: number) {
  autoFocus.value = false
  zoom.value = Math.max(.75, Math.min(24, zoom.value * factor))
}

function drawGlobalGrid(context: CanvasRenderingContext2D, cx: number, cy: number, radius: number, maxRange: number) {
  context.strokeStyle = 'rgba(74,218,203,.19)'
  context.lineWidth = 1
  for (let ring = 1; ring <= 5; ring += 1) {
    context.beginPath(); context.arc(cx, cy, radius * ring / 5, 0, Math.PI * 2); context.stroke()
    context.fillStyle = 'rgba(148,201,199,.68)'; context.font = '10px sans-serif'; context.textAlign = 'left'
    context.fillText(`${Math.round(maxRange * ring / 5)} m`, cx + 5, cy - radius * ring / 5 + 12)
  }
  for (let degree = 0; degree < 360; degree += 30) {
    const angle = degree * Math.PI / 180
    context.beginPath(); context.moveTo(cx, cy)
    context.lineTo(cx + Math.sin(angle) * radius, cy - Math.cos(angle) * radius); context.stroke()
    context.fillStyle = '#72999b'; context.font = '11px sans-serif'; context.textAlign = 'center'
    context.fillText(`${degree}°`, cx + Math.sin(angle) * (radius + 17), cy - Math.cos(angle) * (radius + 17) + 4)
  }
}

function draw(now = performance.now()) {
  const element = canvas.value
  if (!element) return
  const width = element.clientWidth
  const height = element.clientHeight
  if (!width || !height) return
  const ratio = window.devicePixelRatio || 1
  if (element.width !== Math.round(width * ratio) || element.height !== Math.round(height * ratio)) {
    element.width = Math.round(width * ratio)
    element.height = Math.round(height * ratio)
  }
  const context = element.getContext('2d')
  if (!context) return
  context.setTransform(ratio, 0, 0, ratio, 0, 0)
  context.clearRect(0, 0, width, height)
  const cx = width / 2
  const cy = height / 2
  const radius = Math.min(width, height) * .43
  const points = pointEntries()
  baseMaxRange = Math.max(100, ...points.map(entry => Math.hypot(entry.value.x, entry.value.y)))
  const viewRange = baseMaxRange / zoom.value
  const scale = radius / viewRange
  const focused = autoFocus.value || zoom.value > 1.01 || Math.hypot(panX.value, panY.value) > .01

  drawGlobalGrid(context, cx, cy, radius, viewRange)

  const elapsed = Math.min(32, Math.max(0, now - lastTime)); lastTime = now
  sweepAngle = (sweepAngle + elapsed * .045) % 360
  const sweep = sweepAngle * Math.PI / 180
  const gradient = context.createRadialGradient(cx, cy, 0, cx, cy, radius)
  gradient.addColorStop(0, 'rgba(57,231,190,.24)'); gradient.addColorStop(1, 'rgba(57,231,190,0)')
  context.fillStyle = gradient
  context.beginPath(); context.moveTo(cx, cy); context.arc(cx, cy, radius, sweep - .34, sweep); context.closePath(); context.fill()
  context.strokeStyle = 'rgba(77,242,202,.9)'; context.beginPath(); context.moveTo(cx, cy)
  context.lineTo(cx + Math.cos(sweep) * radius, cy + Math.sin(sweep) * radius); context.stroke()

  projected = []
  for (const entry of points) {
    const x = cx + (entry.value.x - panX.value) * scale
    const y = cy - (entry.value.y - panY.value) * scale
    if (x < -10 || x > width + 10 || y < -10 || y > height + 10) continue
    projected.push({ item: entry.item, x, y })
    const selected = entry.item.id === props.selectedId
    context.fillStyle = entry.item.kind === 'OBSTACLE'
      ? '#ffad45'
      : entry.item.kind === 'RADAR_RETURN'
        ? '#55d8ff'
        : focused ? 'rgba(235,255,252,.88)' : '#62e7bc'
    context.shadowColor = context.fillStyle; context.shadowBlur = selected ? 15 : focused ? 2 : 7
    context.beginPath(); context.arc(x, y, selected ? 6 : focused ? 1.25 : 3, 0, Math.PI * 2); context.fill()
    context.shadowBlur = 0
    if (selected) {
      context.strokeStyle = '#ffca58'; context.lineWidth = 2
      context.strokeRect(x - 11, y - 11, 22, 22)
      const anchorX = x < cx ? 34 : width - 34
      const elbowX = x < cx ? x - 34 : x + 34
      const anchorY = Math.max(32, Math.min(height - 32, y - 52))
      context.beginPath(); context.moveTo(x, y); context.lineTo(elbowX, anchorY); context.lineTo(anchorX, anchorY); context.stroke()
      context.fillStyle = '#ffca58'; context.font = '700 11px sans-serif'; context.textAlign = x < cx ? 'left' : 'right'
      context.fillText(entry.item.id, anchorX, anchorY - 6)
    }
  }

  const markerX = focused ? cx : cx - panX.value * scale
  const markerY = focused ? cy : cy + panY.value * scale
  context.fillStyle = '#65ddcf'; context.beginPath(); context.arc(markerX, markerY, 4, 0, Math.PI * 2); context.fill()
  context.strokeStyle = '#65ddcf'; context.lineWidth = 1
  context.beginPath(); context.moveTo(markerX - 9, markerY); context.lineTo(markerX + 9, markerY); context.moveTo(markerX, markerY - 9); context.lineTo(markerX, markerY + 9); context.stroke()
  animationFrame = window.requestAnimationFrame(draw)
}

function selectPoint(event: MouseEvent) {
  if (dragged) return
  const rect = canvas.value?.getBoundingClientRect()
  if (!rect) return
  const x = event.clientX - rect.left, y = event.clientY - rect.top
  const nearest = projected.map(point => ({ point, distance: Math.hypot(point.x - x, point.y - y) }))
    .filter(entry => entry.distance <= 18).sort((a, b) => a.distance - b.distance)[0]
  if (nearest) emit('select', nearest.point.item)
}

function wheelZoom(event: WheelEvent) {
  const element = canvas.value
  if (!element) return
  const rect = element.getBoundingClientRect()
  const radius = Math.min(rect.width, rect.height) * .43
  const oldScale = radius / (baseMaxRange / zoom.value)
  const cursorWorldX = panX.value + (event.clientX - rect.left - rect.width / 2) / oldScale
  const cursorWorldY = panY.value - (event.clientY - rect.top - rect.height / 2) / oldScale
  autoFocus.value = false
  zoom.value = Math.max(.75, Math.min(24, zoom.value * (event.deltaY < 0 ? 1.22 : 1 / 1.22)))
  const newScale = radius / (baseMaxRange / zoom.value)
  panX.value = cursorWorldX - (event.clientX - rect.left - rect.width / 2) / newScale
  panY.value = cursorWorldY + (event.clientY - rect.top - rect.height / 2) / newScale
}

function pointerDown(event: PointerEvent) {
  dragging = true
  dragged = false
  pointerX = event.clientX
  pointerY = event.clientY
  canvas.value?.setPointerCapture(event.pointerId)
}

function pointerMove(event: PointerEvent) {
  if (!dragging || !canvas.value) return
  const dx = event.clientX - pointerX
  const dy = event.clientY - pointerY
  if (Math.abs(dx) + Math.abs(dy) > 1) dragged = true
  const radius = Math.min(canvas.value.clientWidth, canvas.value.clientHeight) * .43
  const viewRange = baseMaxRange / zoom.value
  panX.value -= dx / radius * viewRange
  panY.value += dy / radius * viewRange
  pointerX = event.clientX
  pointerY = event.clientY
  autoFocus.value = false
}

function pointerUp() {
  dragging = false
  window.setTimeout(() => { dragged = false }, 0)
}

watch(() => props.items, () => {
  const entries = pointEntries()
  baseMaxRange = Math.max(100, ...entries.map(entry => Math.hypot(entry.value.x, entry.value.y)))
  if (autoFocus.value) fitPointCloud()
})

onMounted(() => {
  const entries = pointEntries()
  baseMaxRange = Math.max(100, ...entries.map(entry => Math.hypot(entry.value.x, entry.value.y)))
  if (entries.length) fitPointCloud()
  animationFrame = window.requestAnimationFrame(draw)
})
onBeforeUnmount(() => { window.cancelAnimationFrame(animationFrame) })
</script>

<template>
  <div class="ppi-shell">
    <canvas
      ref="canvas"
      class="radar-ppi"
      aria-label="可缩放雷达与点云俯视图"
      @click="selectPoint"
      @dblclick="fitPointCloud"
      @pointerdown="pointerDown"
      @pointermove="pointerMove"
      @pointerup="pointerUp"
      @pointercancel="pointerUp"
      @wheel.prevent="wheelZoom"
    />
    <div class="view-controls">
      <button type="button" :class="{ active: autoFocus }" @click="fitPointCloud">点云聚焦</button>
      <button type="button" title="缩小" @click="changeZoom(1 / 1.3)">−</button>
      <span>{{ zoom.toFixed(1) }}×</span>
      <button type="button" title="放大" @click="changeZoom(1.3)">＋</button>
      <button type="button" @click="resetView">全局复位</button>
    </div>
    <div class="view-state"><b>{{ viewMode }}</b><span>滚轮缩放 · 拖拽平移 · 双击聚焦</span></div>
  </div>
</template>

<style scoped>
.ppi-shell{position:relative;width:100%;height:100%;overflow:hidden}.radar-ppi{display:block;width:100%;height:100%;cursor:grab;touch-action:none}.radar-ppi:active{cursor:grabbing}.view-controls{position:absolute;z-index:4;top:8px;left:50%;display:flex;align-items:center;gap:4px;transform:translateX(-50%);padding:4px;border:1px solid #285a65;border-radius:7px;background:#03171edb;box-shadow:0 8px 24px #0008}.view-controls button{height:26px;padding:0 8px;border:1px solid transparent;border-radius:4px;background:#08242b;color:#92b7b9;cursor:pointer;font-size:9px}.view-controls button:hover,.view-controls button.active{border-color:#4bd7c9;color:#eafffd;background:#0b3439}.view-controls span{min-width:38px;color:#68dfd2;text-align:center;font:9px ui-monospace,Consolas,monospace}.view-state{position:absolute;z-index:4;right:10px;bottom:10px;display:grid;justify-items:end;gap:2px;padding:6px 8px;border:1px solid #244e57;border-radius:5px;background:#03171ed1;pointer-events:none}.view-state b{color:#72e5d8;font-size:9px}.view-state span{color:#71979b;font-size:8px}
</style>

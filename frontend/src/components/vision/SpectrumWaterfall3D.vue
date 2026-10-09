<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

interface SpectrumHistoryFrame {
  receivedAt: number
  powersDbm: number[]
}

const props = withDefaults(defineProps<{
  frames: SpectrumHistoryFrame[]
  startHz?: number | null
  stopHz?: number | null
  minDbm: number
  maxDbm: number
  windowSeconds?: number
}>(), {
  startHz: null,
  stopHz: null,
  windowSeconds: 100,
})

const canvas = ref<HTMLCanvasElement | null>(null)
const host = ref<HTMLElement | null>(null)
const dragging = ref(false)
const yaw = ref(-0.34)
const pitch = ref(0.72)
const zoom = ref(1)
let pointerX = 0
let pointerY = 0
let resizeObserver: ResizeObserver | undefined
let animationFrame = 0

const historySpan = computed(() => {
  if (props.frames.length < 2) return 0
  return Math.min(props.windowSeconds, (props.frames[props.frames.length - 1]!.receivedAt - props.frames[0]!.receivedAt) / 1000)
})

function scheduleDraw() {
  window.cancelAnimationFrame(animationFrame)
  animationFrame = window.requestAnimationFrame(draw)
}

function colorFor(value: number, alpha = 0.9) {
  const span = Math.max(1, props.maxDbm - props.minDbm)
  const t = Math.max(0, Math.min(1, (value - props.minDbm) / span))
  const hue = 188 - t * 142
  const lightness = 30 + t * 30
  return `hsla(${hue}, 88%, ${lightness}%, ${alpha})`
}

function draw() {
  const element = canvas.value
  const container = host.value
  if (!element || !container) return

  const rect = container.getBoundingClientRect()
  const dpr = Math.min(2, window.devicePixelRatio || 1)
  const width = Math.max(1, rect.width)
  const height = Math.max(1, rect.height)
  if (element.width !== Math.round(width * dpr) || element.height !== Math.round(height * dpr)) {
    element.width = Math.round(width * dpr)
    element.height = Math.round(height * dpr)
  }

  const context = element.getContext('2d')
  if (!context) return
  context.setTransform(dpr, 0, 0, dpr, 0, 0)
  context.clearRect(0, 0, width, height)

  const centerX = width * 0.51
  const centerY = height * 0.59
  const scale = Math.min(width * 0.37, height * 0.48) * zoom.value
  const cosYaw = Math.cos(yaw.value)
  const sinYaw = Math.sin(yaw.value)
  const cosPitch = Math.cos(pitch.value)
  const sinPitch = Math.sin(pitch.value)

  function project(x: number, y: number, z: number) {
    const rotatedX = x * cosYaw - y * sinYaw
    const rotatedY = x * sinYaw + y * cosYaw
    const tiltedY = rotatedY * cosPitch - z * sinPitch
    const tiltedZ = rotatedY * sinPitch + z * cosPitch
    const perspective = 4.4 / (4.4 + tiltedY)
    return {
      x: centerX + rotatedX * scale * perspective,
      y: centerY - tiltedZ * scale * perspective,
      depth: tiltedY,
    }
  }

  const gridStroke = 'rgba(76, 185, 181, .22)'
  context.lineWidth = 1
  context.strokeStyle = gridStroke
  for (let i = 0; i <= 5; i += 1) {
    const x = -1.35 + i / 5 * 2.7
    const from = project(x, -1, 0)
    const to = project(x, 1, 0)
    context.beginPath(); context.moveTo(from.x, from.y); context.lineTo(to.x, to.y); context.stroke()
  }
  for (let i = 0; i <= 5; i += 1) {
    const y = -1 + i / 5 * 2
    const from = project(-1.35, y, 0)
    const to = project(1.35, y, 0)
    context.beginPath(); context.moveTo(from.x, from.y); context.lineTo(to.x, to.y); context.stroke()
  }

  const now = props.frames.length ? props.frames[props.frames.length - 1]!.receivedAt : Date.now()
  const cutoff = now - props.windowSeconds * 1000
  const eligible = props.frames.filter(frame => frame.receivedAt >= cutoff && frame.powersDbm.length > 1)
  const rowLimit = 84
  const rowStep = Math.max(1, Math.ceil(eligible.length / rowLimit))
  const rows = eligible.filter((_, index) => index % rowStep === 0 || index === eligible.length - 1)
  const span = Math.max(1, props.maxDbm - props.minDbm)

  if (rows.length > 1) {
    const cells: Array<{ points: ReturnType<typeof project>[]; value: number; depth: number }> = []
    for (let rowIndex = 0; rowIndex < rows.length - 1; rowIndex += 1) {
      const current = rows[rowIndex]!
      const next = rows[rowIndex + 1]!
      const columns = Math.min(current.powersDbm.length, next.powersDbm.length)
      const columnStep = Math.max(1, Math.ceil(columns / 112))
      const currentY = -1 + Math.max(0, Math.min(1, (current.receivedAt - cutoff) / (props.windowSeconds * 1000))) * 2
      const nextY = -1 + Math.max(0, Math.min(1, (next.receivedAt - cutoff) / (props.windowSeconds * 1000))) * 2
      for (let column = 0; column + columnStep < columns; column += columnStep) {
        const nextColumn = Math.min(columns - 1, column + columnStep)
        const x1 = -1.35 + column / (columns - 1) * 2.7
        const x2 = -1.35 + nextColumn / (columns - 1) * 2.7
        const p00 = current.powersDbm[column]!
        const p10 = current.powersDbm[nextColumn]!
        const p01 = next.powersDbm[column]!
        const p11 = next.powersDbm[nextColumn]!
        const z00 = Math.max(0, Math.min(1, (p00 - props.minDbm) / span)) * 1.18
        const z10 = Math.max(0, Math.min(1, (p10 - props.minDbm) / span)) * 1.18
        const z01 = Math.max(0, Math.min(1, (p01 - props.minDbm) / span)) * 1.18
        const z11 = Math.max(0, Math.min(1, (p11 - props.minDbm) / span)) * 1.18
        const points = [project(x1, currentY, z00), project(x2, currentY, z10), project(x2, nextY, z11), project(x1, nextY, z01)]
        cells.push({ points, value: (p00 + p10 + p01 + p11) / 4, depth: points.reduce((sum, point) => sum + point.depth, 0) / 4 })
      }
    }
    cells.sort((a, b) => b.depth - a.depth)
    for (const cell of cells) {
      context.beginPath()
      context.moveTo(cell.points[0]!.x, cell.points[0]!.y)
      for (let index = 1; index < cell.points.length; index += 1) context.lineTo(cell.points[index]!.x, cell.points[index]!.y)
      context.closePath()
      context.fillStyle = colorFor(cell.value, 0.82)
      context.fill()
      context.strokeStyle = colorFor(cell.value, 0.22)
      context.stroke()
    }
  }

  const axisColor = '#7dded5'
  const oldCorner = project(-1.35, -1, 0)
  const nowCorner = project(-1.35, 1, 0)
  const frequencyEnd = project(1.35, 1, 0)
  const powerEnd = project(-1.35, 1, 1.35)
  context.strokeStyle = axisColor
  context.lineWidth = 1.3
  for (const [from, to] of [[nowCorner, frequencyEnd], [nowCorner, oldCorner], [nowCorner, powerEnd]]) {
    context.beginPath(); context.moveTo(from!.x, from!.y); context.lineTo(to!.x, to!.y); context.stroke()
  }

  context.fillStyle = '#96c9c7'
  context.font = '10px ui-monospace, Consolas, monospace'
  context.fillText(`${((props.startHz ?? 0) / 1e9).toFixed(2)} GHz`, nowCorner.x - 12, nowCorner.y + 18)
  context.fillText(`${((props.stopHz ?? 0) / 1e9).toFixed(2)} GHz  X · 频率`, frequencyEnd.x - 44, frequencyEnd.y + 18)
  context.fillText(`Y · 时间  -${props.windowSeconds}s`, oldCorner.x - 40, oldCorner.y - 10)
  context.fillText('0s', nowCorner.x - 26, nowCorner.y - 5)
  context.fillText(`Z · 功率  ${props.maxDbm} dBm`, powerEnd.x - 42, powerEnd.y - 10)
  context.fillText(`${props.minDbm} dBm`, nowCorner.x - 62, nowCorner.y + 4)

  if (rows.length < 2) {
    context.textAlign = 'center'
    context.fillStyle = '#86aaa9'
    context.font = '12px system-ui, sans-serif'
    context.fillText('正在累积三维频谱时间序列…', width / 2, height / 2)
    context.textAlign = 'start'
  }
}

function pointerDown(event: PointerEvent) {
  dragging.value = true
  pointerX = event.clientX
  pointerY = event.clientY
  canvas.value?.setPointerCapture(event.pointerId)
}

function pointerMove(event: PointerEvent) {
  if (!dragging.value) return
  yaw.value += (event.clientX - pointerX) * 0.007
  pitch.value = Math.max(0.28, Math.min(1.18, pitch.value - (event.clientY - pointerY) * 0.006))
  pointerX = event.clientX
  pointerY = event.clientY
  scheduleDraw()
}

function pointerUp() {
  dragging.value = false
}

function zoomChart(event: WheelEvent) {
  zoom.value = Math.max(0.72, Math.min(1.38, zoom.value - event.deltaY * 0.001))
  scheduleDraw()
}

function resetView() {
  yaw.value = -0.34
  pitch.value = 0.72
  zoom.value = 1
  scheduleDraw()
}

watch(() => [props.frames, props.minDbm, props.maxDbm, props.startHz, props.stopHz], scheduleDraw)

onMounted(() => {
  resizeObserver = new ResizeObserver(scheduleDraw)
  if (host.value) resizeObserver.observe(host.value)
  scheduleDraw()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  window.cancelAnimationFrame(animationFrame)
})
</script>

<template>
  <div ref="host" class="waterfall-shell">
    <canvas
      ref="canvas"
      aria-label="SAN60 三维实时频谱，横轴频率，纵轴时间，高度为功率"
      @dblclick="resetView"
      @pointerdown="pointerDown"
      @pointermove="pointerMove"
      @pointerup="pointerUp"
      @pointercancel="pointerUp"
      @wheel.prevent="zoomChart"
    />
    <div class="chart-hud"><b>100 SEC WATERFALL</b><span>{{ frames.length }} 帧 · 已累积 {{ historySpan.toFixed(1) }} s</span></div>
    <div class="chart-help">拖拽旋转 · 滚轮缩放 · 双击复位</div>
    <div class="color-scale"><span>{{ maxDbm }} dBm</span><i /><span>{{ minDbm }} dBm</span></div>
  </div>
</template>

<style scoped>
.waterfall-shell{position:relative;width:100%;height:100%;overflow:hidden;border:1px solid #1e4c55;border-radius:7px;background:radial-gradient(circle at 56% 42%,#0b343a,#03161c 68%);user-select:none}.waterfall-shell::before{position:absolute;inset:0;opacity:.22;background:linear-gradient(#54c8c81f 1px,transparent 1px),linear-gradient(90deg,#54c8c81f 1px,transparent 1px);background-size:10% 20%;content:"";pointer-events:none}.waterfall-shell canvas{position:absolute;inset:0;width:100%;height:100%;cursor:grab;touch-action:none}.waterfall-shell canvas:active{cursor:grabbing}.chart-hud{position:absolute;top:10px;left:12px;display:grid;gap:3px;padding:6px 8px;border-left:2px solid #5ce0d0;background:#03151bc9;pointer-events:none}.chart-hud b{color:#80e6dc;font:9px ui-monospace,Consolas,monospace;letter-spacing:.1em}.chart-hud span,.chart-help{color:#729b9d;font:9px system-ui,sans-serif}.chart-help{position:absolute;right:12px;bottom:10px;padding:5px 7px;border:1px solid #214a52;border-radius:4px;background:#03151bd1;pointer-events:none}.color-scale{position:absolute;top:14px;right:12px;display:grid;grid-template-columns:auto 58px auto;align-items:center;gap:6px;color:#83a7a8;font:8px ui-monospace,Consolas,monospace;pointer-events:none}.color-scale i{height:5px;border-radius:3px;background:linear-gradient(90deg,hsl(188 88% 30%),hsl(128 88% 45%),hsl(46 88% 60%))}
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { San60State } from '@/types/san60'
import { createView, loadPlotly, plotConfig, scenePlot, spectrumPlot, type Layers, type PlotlyApi } from './plot'

const props = defineProps<{ state: San60State | null; layers: Layers }>()
const simulated = computed(() => props.state?.mode === 'demo' || props.state?.latest?.source === 'simulation')
const scene = ref<HTMLElement>(), spectrum = ref<HTMLElement>()
const error = ref(''), ready = ref(false)
const view = createView()
let plotly: PlotlyApi | undefined, observer: ResizeObserver | undefined
let disposed = false, drawing = false, pending = false, interactionUntil = 0, pointerActive = false, bound = false
let timer: ReturnType<typeof setTimeout> | undefined
let lastScene = '', lastSpectrum = ''
let initialFit = false
async function draw() {
  pending = true
  if (drawing || !plotly || !props.state || disposed) return
  if (pointerActive || performance.now() < interactionUntil) {
    clearTimeout(timer); timer = setTimeout(draw, 100); return
  }
  drawing = true; pending = false
  const state = props.state
  try {
    if (!initialFit && state.spatial?.track.length) {
      // A single first fix must not zoom into a 4 m box while the BAG route spans ~40 m.
      view.fit(state.spatial.track.map(sample => sample.p), 60, 30); initialFit = true
    }
    const sceneKey = JSON.stringify([state.spatial, props.layers, view.scene()])
    const spectrumKey = JSON.stringify([state.latest?.time, state.latest?.sequence, state.band, !!state.latest])
    if (sceneKey !== lastScene && scene.value) {
      const plot = scenePlot(state, props.layers, view)
      await plotly.react(scene.value, plot.traces, plot.layout, plotConfig)
      lastScene = sceneKey
      if (!bound && !disposed) {
        const graph = scene.value as HTMLElement & { on: (event: string, listener: (value: Record<string, unknown>) => void) => void }
        graph.on('plotly_relayout', view.capture); graph.on('plotly_relayouting', view.capture); bound = true
      }
    }
    if (spectrumKey !== lastSpectrum && spectrum.value && !disposed) {
      const plot = spectrumPlot(state)
      await plotly.react(spectrum.value, plot.traces, plot.layout, plotConfig)
      lastSpectrum = spectrumKey
    }
  } catch (reason) { if (!disposed) error.value = reason instanceof Error ? reason.message : '绘图失败' }
  finally {
    drawing = false
    if (disposed) { if (scene.value) plotly.purge(scene.value); if (spectrum.value) plotly.purge(spectrum.value) }
    else if (pending) void draw()
  }
}
function fit() {
  const space = props.state?.spatial
  const points = [...(space?.track || []).map(sample => sample.p), ...(space?.cells || []), ...(space?.field || [])]
  if (space?.source) points.push(space.source.p)
  view.fit(points, Math.max(.05, (space?.voxel_size || 2) * 2))
  lastScene = ''
}
function resetView() { fit(); void draw() }
async function saveImage() {
  if (!plotly || !scene.value || !ready.value) throw new Error('三维图尚未就绪')
  await plotly.downloadImage(scene.value, { format: 'png', filename: 'SAN60_实时空间态势', width: 1800, height: 1100 })
}
function resize() { if (plotly && ready.value) for (const node of [scene.value, spectrum.value]) if (node) plotly.Plots.resize(node) }
function release() { pointerActive = false; interactionUntil = performance.now() + 250; if (pending) void draw() }
function beginInteraction() { pointerActive = true }
function wheelInteraction() { interactionUntil = performance.now() + 300 }
async function initialize() {
  error.value = ''
  try { plotly = await loadPlotly(); if (!disposed) { ready.value = true; await draw() } }
  catch (reason) { if (!disposed) error.value = reason instanceof Error ? reason.message : '绘图库加载失败' }
}
watch(() => [props.state, props.layers], () => { void draw() }, { deep: true })
watch(() => props.state, state => { if (state && !plotly) void initialize() })
onMounted(() => {
  void initialize()
  observer = new ResizeObserver(resize)
  if (scene.value) observer.observe(scene.value)
  if (spectrum.value) observer.observe(spectrum.value)
  window.addEventListener('pointerup', release); window.addEventListener('pointercancel', release); window.addEventListener('blur', release)
  window.addEventListener('uav-usv:viewport-transition-end', resize)
})
onBeforeUnmount(() => {
  disposed = true; clearTimeout(timer); observer?.disconnect()
  window.removeEventListener('pointerup', release); window.removeEventListener('pointercancel', release); window.removeEventListener('blur', release)
  window.removeEventListener('uav-usv:viewport-transition-end', resize)
  if (plotly && !drawing) { if (scene.value) plotly.purge(scene.value); if (spectrum.value) plotly.purge(spectrum.value) }
})
defineExpose({ resetView, saveImage })
</script>

<template>
  <div class="plots">
    <section class="scene-card">
      <header><div><h2>三维电磁空间态势 <small>ENU · cm</small></h2><p>{{ simulated ? '合成轨迹与信号 · 仅用于模拟演示' : '实际测量轨迹与功率分布 · 显示分辨率不代表定位精度' }}</p></div><button @click="resetView">重置视角</button></header>
      <div ref="scene" class="scene" aria-label="三维电磁态势图" @pointerdown="beginInteraction" @wheel.passive="wheelInteraction" />
      <div v-if="!state?.spatial?.track.length" class="empty">等待有效且时间同步的 GNSS 与频谱数据</div>
      <div v-if="error" class="plot-error" role="alert">{{ error }} <button @click="initialize">重试绘图</button></div>
      <footer><span>{{ state?.spatial?.reason || '等待空间测量' }}</span><span>拖动旋转 · 滚轮缩放 · Shift 拖动平移</span></footer>
    </section>
    <section class="spectrum-card"><header><h2>实时二维频谱</h2><span>{{ state?.latest ? `${state.latest.bins} 频点 · ${state.latest.peak_mhz.toFixed(3)} MHz` : '等待信号' }}</span></header><div ref="spectrum" class="spectrum" aria-label="二维频谱图" /></section>
  </div>
</template>

<style scoped>
.plots{display:grid;grid-template-rows:minmax(400px,1fr) 240px;gap:14px;min-width:0;min-height:0}.scene-card,.spectrum-card{position:relative;min-width:0;overflow:hidden;border:1px solid #234550;border-radius:12px;background:radial-gradient(ellipse at 50% 35%,#102e3b,#071b25 75%)}header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 20px}h2{margin:0;color:#e0eeee;font-size:15px;font-weight:600}h2 small{margin-left:10px;color:#69d8cd;font:11px Consolas,monospace}p,header>span{margin:6px 0 0;color:#8eacb8;font-size:11px}button{padding:7px 12px;border:1px solid #365c65;border-radius:6px;background:#14323b;color:#cae5e4;cursor:pointer}.scene{height:calc(100% - 114px);min-height:280px}.scene-card footer{display:flex;justify-content:space-between;gap:12px;padding:12px 18px;border-top:1px solid #1c3b45;color:#9ab6bc;font-size:11px}.scene-card footer span:last-child{color:#678b96}.spectrum-card header{padding-bottom:0}.spectrum{height:185px}.empty{position:absolute;left:50%;top:48%;transform:translate(-50%,-50%);color:#8eacb8;font-size:13px;pointer-events:none;white-space:nowrap}.plot-error{position:absolute;inset:80px 12px auto;padding:14px;background:#3b2731;color:#ffbdc9;border:1px solid #895565;border-radius:8px}@media(max-width:900px){.plots{grid-template-rows:480px 240px}.scene-card footer span:last-child{display:none}}
</style>

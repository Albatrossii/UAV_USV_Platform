<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { Crosshair, Plane, Ship } from '@lucide/vue'

import { normalizeNodeCode, type NodeCommunicationRow } from '@/utils/nodeCommunication'

const props = withDefaults(defineProps<{
  rows: NodeCommunicationRow[]
  selectedDevice: string
  followDevice?: string
  controlsReady: boolean
  /** Height shared by the Unity stage and this bar, excluding other toolbars. */
  availableHeight: number
  /** Keep automatic sizing and hide manual controls inside the unified workspace shell. */
  fixedSizing?: boolean
}>(), { followDevice: '', fixedSizing: false })
const emit = defineEmits<{
  select: [code: string]
  resize: [height: number]
  'resize-start': []
  'resize-end': []
}>()

const MIN_CARD_HEIGHT = 52
const DEFAULT_CARD_HEIGHT = 72
const MAX_CARD_HEIGHT = 120
const GRID_GAP = 6
// 10px vertical padding, 1px borders, and the 8px heading/grid gap.
const PANEL_CHROME = 30
const panel = ref<HTMLElement | null>(null)
const heading = ref<HTMLElement | null>(null)
const contentWidth = ref(880)
const headingHeight = ref(26)
const preferredHeight = ref<number | null>(null)
const resizing = ref(false)
const gridId = `compact-node-grid-${useId()}`
const sliderId = `compact-node-size-${useId()}`

const columns = computed(() => Math.min(
  Math.max(1, props.rows.length),
  contentWidth.value >= 880 ? 6 : contentWidth.value >= 330 ? 3 : 2,
))
const rowCount = computed(() => Math.max(1, Math.ceil(props.rows.length / columns.value)))
const chromeHeight = computed(() => headingHeight.value + PANEL_CHROME + (rowCount.value - 1) * GRID_GAP)
const maximumCardHeight = computed(() => {
  const available = props.availableHeight
  if (!Number.isFinite(available) || available <= 0) return MAX_CARD_HEIGHT
  // Keep the stage dominant where space permits, retaining legible cards on
  // smaller screens even when a 390px stage cannot fit at the same time.
  const maximumPanel = Math.max(
    chromeHeight.value + rowCount.value * MIN_CARD_HEIGHT,
    Math.min(available * 0.34, available - 390),
  )
  return Math.max(MIN_CARD_HEIGHT, Math.min(MAX_CARD_HEIGHT,
    Math.floor((maximumPanel - chromeHeight.value) / rowCount.value),
  ))
})
const cardHeight = computed(() => Math.round(Math.max(MIN_CARD_HEIGHT,
  Math.min(maximumCardHeight.value, preferredHeight.value ?? DEFAULT_CARD_HEIGHT),
)))
const panelHeight = computed(() => chromeHeight.value + rowCount.value * cardHeight.value)
const sizingDescription = computed(() => `卡片高度 ${cardHeight.value} 像素，${preferredHeight.value === null ? '自动' : '手动'}大小`)
const panelStyle = computed(() => ({
  height: `${panelHeight.value}px`,
  flexBasis: `${panelHeight.value}px`,
  '--node-columns': columns.value,
  '--node-rows': rowCount.value,
  '--node-card-height': `${cardHeight.value}px`,
  '--node-icon-size': `${Math.max(21, Math.min(36, cardHeight.value * 0.36))}px`,
  '--node-code-size': `${Math.max(11, Math.min(14, cardHeight.value * 0.17))}px`,
}))

watch(panelHeight, height => emit('resize', height), { immediate: true, flush: 'post' })

function matches(code: string, selected: string) {
  return !!selected && normalizeNodeCode(code) === normalizeNodeCode(selected)
}
function selectNode(code: string) {
  if (props.controlsReady) emit('select', code)
}
function setHeight(value: number) {
  if (props.fixedSizing) return
  if (Number.isFinite(value)) preferredHeight.value = Math.round(Math.max(MIN_CARD_HEIGHT, Math.min(maximumCardHeight.value, value)))
}
function changeSize(event: Event) {
  setHeight(Number((event.target as HTMLInputElement).value))
}
function resetSize() {
  preferredHeight.value = null
}
watch(() => props.fixedSizing, fixed => {
  if (fixed) resetSize()
}, { immediate: true })
function resizeWithKeyboard(event: KeyboardEvent) {
  if (event.key === 'Enter') {
    event.preventDefault()
    resetSize()
    return
  }
  if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  setHeight(event.key === 'Home' ? MIN_CARD_HEIGHT
    : event.key === 'End' ? maximumCardHeight.value
      : cardHeight.value + (event.key === 'ArrowUp' ? 4 : -4))
}

let drag: { pointerId: number; target: HTMLElement; y: number; height: number; rows: number } | null = null
function startResize(event: PointerEvent) {
  if (props.fixedSizing || event.button !== 0 || drag) return
  const target = event.currentTarget as HTMLElement
  if (typeof target.setPointerCapture !== 'function') return
  event.preventDefault()
  drag = { pointerId: event.pointerId, target, y: event.clientY, height: cardHeight.value, rows: rowCount.value }
  try {
    target.setPointerCapture(event.pointerId)
    resizing.value = true
    emit('resize-start')
  } catch {
    drag = null
  }
}
function moveResize(event: PointerEvent) {
  if (!drag || event.pointerId !== drag.pointerId) return
  setHeight(drag.height + (drag.y - event.clientY) / drag.rows)
}
function stopResize(event?: PointerEvent) {
  if (!drag || (event && event.pointerId !== drag.pointerId)) return
  const previous = drag
  drag = null
  resizing.value = false
  if (previous.target.hasPointerCapture?.(previous.pointerId)) {
    previous.target.releasePointerCapture(previous.pointerId)
  }
  emit('resize-end')
}
function stopOnBlur() { stopResize() }

let observer: ResizeObserver | null = null
let layoutFrame: number | null = null
function measureLayout() {
  layoutFrame = null
  if (!panel.value || !heading.value) return
  // clientWidth includes the component's two 10px paddings but not borders.
  if (panel.value.clientWidth > 20) contentWidth.value = panel.value.clientWidth - 20
  const measuredHeading = Math.ceil(heading.value.getBoundingClientRect().height)
  if (measuredHeading > 0) headingHeight.value = measuredHeading
}
function scheduleLayout() {
  if (layoutFrame === null) layoutFrame = requestAnimationFrame(measureLayout)
}
onMounted(() => {
  measureLayout()
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(scheduleLayout)
    observer.observe(panel.value!)
    observer.observe(heading.value!)
  }
  window.addEventListener('resize', scheduleLayout)
  window.addEventListener('blur', stopOnBlur)
})
onBeforeUnmount(() => {
  stopResize()
  observer?.disconnect()
  if (layoutFrame !== null) cancelAnimationFrame(layoutFrame)
  window.removeEventListener('resize', scheduleLayout)
  window.removeEventListener('blur', stopOnBlur)
})
</script>

<template>
  <section ref="panel" class="cnv-bar" :class="{ resizing }" :style="panelStyle" data-test="compact-node-view-bar" aria-label="节点视角">
    <div
      v-if="!props.fixedSizing"
      class="cnv-resize-grip" role="separator" tabindex="0" aria-orientation="horizontal"
      aria-label="调整节点卡片高度" :aria-controls="gridId" :aria-valuemin="MIN_CARD_HEIGHT"
      :aria-valuemax="maximumCardHeight" :aria-valuenow="cardHeight" :aria-valuetext="sizingDescription"
      title="上下拖动调整卡片高度；方向键微调，双击或回车恢复自动"
      @pointerdown="startResize" @pointermove="moveResize" @pointerup="stopResize"
      @pointercancel="stopResize" @lostpointercapture="stopResize"
      @dblclick="resetSize" @keydown="resizeWithKeyboard"
    />
    <header ref="heading" class="cnv-heading">
      <div class="cnv-heading-title">
        <h2>节点视角</h2><span>{{ props.rows.length }} 个节点</span>
        <span class="cnv-view-status" aria-live="polite">{{ !props.controlsReady ? '等待场景就绪' : props.followDevice ? `跟随 ${props.followDevice}` : '点击卡片切换视角' }}</span>
      </div>
      <div v-if="!props.fixedSizing" class="cnv-sizing">
        <label :for="sliderId">大小</label>
        <input :id="sliderId" type="range" :min="MIN_CARD_HEIGHT" :max="maximumCardHeight" :value="cardHeight" aria-label="节点卡片高度" :aria-valuetext="sizingDescription" @input="changeSize" />
        <output :for="sliderId">{{ cardHeight }}px</output>
        <button type="button" class="cnv-auto" :aria-pressed="preferredHeight === null" @click="resetSize">{{ preferredHeight === null ? '自动适配' : '恢复自动' }}</button>
      </div>
    </header>
    <div :id="gridId" class="cnv-grid">
      <button
        v-for="row in props.rows" :key="row.code" type="button" class="cnv-card"
        :class="{ usv: row.type === 'USV', selected: matches(row.code, props.selectedDevice), following: matches(row.code, props.followDevice) }"
        :data-node="row.code" :data-state="row.state" :data-selected="matches(row.code, props.selectedDevice)"
        :disabled="!props.controlsReady" :aria-pressed="matches(row.code, props.followDevice)"
        :aria-label="`跟随 ${row.code}，${row.type === 'UAV' ? '无人机' : '无人艇'}，节点${row.stateLabel}`"
        :title="props.controlsReady ? `跟随 ${row.code}` : `${row.code} · 等待场景就绪`"
        @click="selectNode(row.code)"
      >
        <span class="cnv-card-heading"><strong>{{ row.code }}</strong><span class="cnv-state" :class="row.state"><i aria-hidden="true" />{{ row.stateLabel }}</span></span>
        <span class="cnv-card-main">
          <Plane v-if="row.type === 'UAV'" class="cnv-device-icon" aria-hidden="true" /><Ship v-else class="cnv-device-icon" aria-hidden="true" />
          <span class="cnv-type">{{ row.type === 'UAV' ? '无人机' : '无人艇' }}</span>
          <Crosshair class="cnv-view-icon" :class="{ active: matches(row.code, props.followDevice) }" aria-hidden="true" />
          <span class="cnv-sr">{{ matches(row.code, props.followDevice) ? '当前跟随视角' : '切换视角' }}</span>
        </span>
      </button>
      <p v-if="!props.rows.length" class="cnv-empty">生成场景后可选择节点视角</p>
    </div>
  </section>
</template>

<style scoped>
.cnv-bar { position: relative; box-sizing: border-box; display: flex; flex-direction: column; flex-grow: 0; flex-shrink: 0; gap: 8px; min-width: 0; padding: 10px; border: 1px solid #31525b; border-radius: 7px; background: #0a2028; color: #d8efeb; container-type: inline-size; }
.cnv-heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; flex-shrink: 0; gap: 7px; min-height: 26px; }
.cnv-heading-title { display: flex; align-items: center; gap: 9px; min-width: 0; }.cnv-heading h2 { margin: 0; font-size: 12px; font-weight: 600; white-space: nowrap; }.cnv-heading-title > span { color: #84a8b0; font-size: 10px; white-space: nowrap; }.cnv-view-status { max-width: 25ch; overflow: hidden; text-overflow: ellipsis; }
.cnv-sizing { display: flex; align-items: center; gap: 7px; margin-left: auto; color: #8eafb7; font-size: 10px; white-space: nowrap; }.cnv-sizing label { margin: 0; font-size: inherit; }.cnv-sizing input { width: 78px; min-width: 40px; height: 12px; margin: 0; padding: 0; accent-color: #70dfcf; cursor: ew-resize; }.cnv-sizing output { min-width: 32px; font: 10px Consolas, monospace; color: #b8d8d8; }
.cnv-auto { min-height: 25px; padding: 4px 8px; border: 1px solid #31575d; border-radius: 4px; background: #102e35; color: #a5cfce; font: inherit; cursor: pointer; }.cnv-auto:hover { background: #194049; }
.cnv-resize-grip { position: absolute; inset: -5px 0 auto; z-index: 30; display: flex; align-items: center; justify-content: center; height: 10px; cursor: ns-resize; touch-action: none; }.cnv-resize-grip::before { content: ''; width: 62px; height: 3px; border-radius: 4px; background: #51767b; }.cnv-resize-grip:hover::before, .cnv-resize-grip:focus-visible::before { width: 90px; background: #8be7d7; }.resizing { user-select: none; cursor: ns-resize; }
.cnv-grid { display: grid; flex: 1; min-width: 0; min-height: 0; grid-template-columns: repeat(var(--node-columns), minmax(0, 1fr)); grid-template-rows: repeat(var(--node-rows), var(--node-card-height)); gap: 6px; }
.cnv-card { --device-color: #e6c277; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; align-items: stretch; gap: 4px; min-width: 0; min-height: 0; padding: 5px 8px; overflow: hidden; border: 1px solid #31515a; border-radius: 5px; background: linear-gradient(140deg, #102c34, #0a1e28); color: #d8efeb; cursor: pointer; text-align: left; }.cnv-card.usv { --device-color: #e5a1a0; }.cnv-card:hover:enabled { border-color: #7caeac; background: #16373e; }.cnv-card.selected { border-color: var(--device-color); background: linear-gradient(120deg, #28423c80, #102b34); box-shadow: inset 0 -2px var(--device-color); }.cnv-card:disabled { cursor: not-allowed; opacity: .65; }
.cnv-card-heading { display: flex; align-items: center; justify-content: space-between; gap: 5px; min-width: 0; }.cnv-card-heading > strong { overflow: hidden; color: var(--device-color); font: 600 var(--node-code-size)/1.25 Consolas, monospace; text-overflow: ellipsis; white-space: nowrap; }.cnv-state { display: inline-flex; align-items: center; flex-shrink: 0; gap: 4px; font-size: 10px; line-height: 1.2; color: #91a6af; }.cnv-state.online { color: #70dfcf; }.cnv-state.delayed { color: #f0c16c; }.cnv-state.offline { color: #ef8b85; }.cnv-state i { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.cnv-card-main { display: flex; align-items: center; gap: 7px; min-width: 0; }.cnv-device-icon { flex-shrink: 0; width: var(--node-icon-size); height: var(--node-icon-size); color: var(--device-color); }.cnv-type { color: #9cbec5; font-size: 10px; line-height: 1.3; white-space: nowrap; }.cnv-view-icon { width: 13px; height: 13px; flex-shrink: 0; margin-left: auto; color: #84a9af; }.cnv-view-icon.active { color: #70dfcf; }
.cnv-empty { grid-column: 1 / -1; align-self: center; margin: 0; color: #84a8b0; text-align: center; font-size: 11px; }.cnv-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
.cnv-card:focus-visible, .cnv-auto:focus-visible, .cnv-sizing input:focus-visible, .cnv-resize-grip:focus-visible { outline: 2px solid #70dfcf; outline-offset: 2px; }
@media (max-width: 760px) { .cnv-sizing { gap: 5px; }.cnv-sizing input { width: 54px; }.cnv-sizing output { display: none; }.cnv-card { padding-inline: 6px; }.cnv-card-main { gap: 5px; }.cnv-state { gap: 3px; font-size: 9px; } }
@container (max-width: 650px) { .cnv-view-status { display: none; } }
</style>

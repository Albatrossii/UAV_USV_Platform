<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { archiveError, listExperiments, type ExperimentRecord } from '@/services/experimentArchive'
import { prepareExperimentExport, saveExperimentExport } from '@/utils/experimentExport'
const props = defineProps<{ owner: string }>()
const open = ref(false)
const records = ref<ExperimentRecord[]>([])
const selectedId = ref('')
const index = ref(0)
const error = ref('')
const loading = ref(false)
const exporting = ref(false)
const exportMessage = ref('')
const exportFile = ref<ReturnType<typeof prepareExperimentExport> | null>(null)
const showExportText = ref(false)
let exportVersion = 0
let loadVersion = 0
watch([open, selectedId, () => props.owner], () => {
  exportVersion++
  exporting.value = false
  exportMessage.value = ''
  exportFile.value = null
  showExportText.value = false
}, { flush: 'sync' })
const playing = ref(false)
const dialog = ref<HTMLElement | null>(null)
let previousFocus: HTMLElement | null = null
const selected = computed(() => records.value.find(record => record.id === selectedId.value))
const frame = computed(() => selected.value?.frames[index.value])
let timer: ReturnType<typeof setInterval> | undefined
function stop() { if (timer) clearInterval(timer); timer = undefined; playing.value = false }
watch([open, selectedId, () => props.owner], () => { stop(); index.value = 0 })
watch(open, async value => {
  if (value) {
    previousFocus = document.activeElement as HTMLElement | null
    await nextTick()
    dialog.value?.querySelector<HTMLButtonElement>('button')?.focus()
  } else previousFocus?.focus()
})
function trapFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab' || !dialog.value) return
  const items = Array.from(dialog.value.querySelectorAll<HTMLElement>('button:not(:disabled),select,input,textarea,summary'))
    .filter(el => el.getClientRects().length > 0)
  const first = items[0], last = items[items.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
}
watch(() => props.owner, () => { records.value = []; selectedId.value = ''; open.value = false })
onBeforeUnmount(() => { stop(); exportVersion++; loadVersion++ })
async function show() {
  open.value = true
  error.value = ''
  loading.value = true
  const version = ++loadVersion
  const owner = props.owner
  try {
    const loaded = await listExperiments(owner)
    if (owner !== props.owner || version !== loadVersion || !open.value) return
    records.value = loaded
    selectedId.value = loaded[0]?.id ?? ''
  } catch {
    if (owner === props.owner && version === loadVersion && open.value) error.value = '无法读取浏览器本地记录。请检查存储权限。'
  } finally { if (version === loadVersion) loading.value = false }
}
function togglePlayback() {
  if (playing.value) return stop()
  if (!selected.value?.frames.length) return
  if (index.value >= selected.value.frames.length - 1) index.value = 0
  playing.value = true
  timer = setInterval(() => {
    if (index.value + 1 >= (selected.value?.frames.length ?? 0)) return stop()
    index.value++
  }, 1000)
}
// Fixed bounds across this recording avoid camera jumps while scrubbing.
const bounds = computed(() => {
  const agents = selected.value?.frames.flatMap(f => f.agents) ?? []
  const xs = agents.map(a => a.x).filter(Number.isFinite)
  const ys = agents.map(a => a.y).filter(Number.isFinite)
  const minX = Math.min(0, ...xs), maxX = Math.max(1, ...xs)
  const minY = Math.min(0, ...ys), maxY = Math.max(1, ...ys)
  const scale = Math.min(700 / Math.max(20, maxX - minX), 340 / Math.max(20, maxY - minY))
  return { minX, minY, scale }
})
function position(x: number, y: number) { return `translate(${40 + (x - bounds.value.minX) * bounds.value.scale},${385 - (y - bounds.value.minY) * bounds.value.scale})` }
async function download(format: 'json' | 'csv') {
  if (!selected.value || exporting.value) return
  const version = ++exportVersion
  exporting.value = true
  exportMessage.value = '正在生成文件并请求保存…'
  exportFile.value = null
  showExportText.value = false
  try {
    const file = prepareExperimentExport(selected.value, format)
    exportFile.value = file
    exportMessage.value = '内容已生成，等待浏览器保存结果；若未出现保存窗口，可显示导出内容手动保存。'
    const result = await saveExperimentExport(file)
    if (version === exportVersion) exportMessage.value = result.message
  } catch {
    if (version === exportVersion) exportMessage.value = '导出内容生成失败，原始实验记录仍保留。'
  } finally { if (version === exportVersion) exporting.value = false }
}
</script>

<template>
  <button class="archive-open" type="button" @click="show">实验记录</button>
  <Teleport to="body">
    <section v-if="open" ref="dialog" class="archive-overlay" role="dialog" aria-modal="true" aria-label="实验记录与只读回放" @keydown.esc="open = false" @keydown="trapFocus">
      <div class="archive-dialog">
        <header><div><h2>实验记录与回放</h2><p>本机当前账号 · 约 1 帧/秒 · 最近 20 次，每次最近 1800 帧 / 500 条事件 · 不保存录音</p></div><button autofocus @click="open = false">关闭</button></header>
        <p v-if="error || archiveError" role="alert">{{ error || archiveError }}</p>
        <p v-if="loading" role="status">正在读取本机实验记录…</p>
        <p v-else-if="!records.length && !error">暂无记录，运行仿真后自动记录。回放只展示历史数据，不控制当前设备。</p>
        <template v-if="!loading && records.length">
          <div class="archive-tools"><select v-model="selectedId" aria-label="选择实验"><option v-for="record in records" :key="record.id" :value="record.id">运行 {{ record.runId }} · {{ new Date(record.startedAt).toLocaleString() }}</option></select><button :disabled="exporting" @click="download('json')">导出 JSON</button><button :disabled="exporting" @click="download('csv')">导出 CSV</button></div>
          <p v-if="exportMessage" role="status" aria-live="polite">{{ exportMessage }}</p>
          <template v-if="exportFile">
            <button @click="showExportText = !showExportText">{{ showExportText ? '隐藏导出内容' : '显示导出内容' }}</button>
            <div v-if="showExportText">
              <p>可全选并复制下方内容，在本机以 UTF-8 保存为 {{ exportFile.filename }}。这不是已保存凭证；分享前请检查识别文字。</p>
              <textarea class="export-text" aria-label="导出内容（只读）" readonly :value="exportFile.text" />
            </div>
          </template>
          <p>只读俯视回放，不向 Unity 或设备下发动作。{{ selected?.truncated ? '记录已达到容量上限，仅保留最近部分。' : '' }}</p>
          <svg viewBox="0 0 800 430" role="img" aria-label="无人机无人艇历史位置回放">
            <g v-for="agent in frame?.agents ?? []" :key="agent.code" :transform="position(agent.x, agent.y)">
              <path v-if="agent.type === 'UAV'" d="M0,-8 L8,7 L0,3 L-8,7 Z" fill="#71e2cd" />
              <rect v-else x="-6" y="-8" width="12" height="16" rx="4" fill="#ffce73" />
              <text x="12" y="0" fill="#e4fbf7" font-size="12">{{ agent.code }}</text><text x="12" y="16" fill="#94b7bd" font-size="10">{{ agent.status }} · 高度 {{ agent.z.toFixed(1) }}m</text>
            </g>
          </svg>
          <div class="archive-tools"><button :disabled="!selected?.frames.length" @click="togglePlayback">{{ playing ? '暂停回放' : '播放回放（1 帧/秒）' }}</button><input v-model.number="index" type="range" min="0" :max="Math.max(0, (selected?.frames.length ?? 0) - 1)" aria-label="回放帧" @input="stop"><span>帧 {{ frame?.sequence ?? '—' }} / {{ selected?.frames.length ?? 0 }} 个样本</span></div>
          <details><summary>场景配置</summary><pre>{{ JSON.stringify(selected?.config, null, 2) }}</pre></details>
          <details open><summary>识别文字与执行事件（{{ selected?.events.length }}）</summary><ol><li v-for="event in selected?.events" :key="event.id"><time>{{ new Date(event.at).toLocaleTimeString() }}</time> {{ event.kind }}<pre>{{ JSON.stringify(event.detail, null, 2) }}</pre></li></ol></details>
        </template>
      </div>
    </section>
  </Teleport>
</template>

<style scoped>
.export-text{box-sizing:border-box;width:100%;min-height:180px;background:#102c33;color:#d9f5f0;border:1px solid #30545b;padding:12px}
.archive-open,.archive-dialog button,.archive-dialog select{color:#bdebe4;background:#102c33;border:1px solid #30545b;border-radius:6px;padding:8px 12px;cursor:pointer}.archive-overlay{position:fixed;inset:0;z-index:5000;background:#001015dc;display:grid;place-items:center;padding:24px}.archive-dialog{width:min(1050px,95vw);max-height:90vh;overflow:auto;padding:24px;background:#092028;color:#d9f5f0;border:1px solid #32616a;border-radius:14px}.archive-dialog header,.archive-tools{display:flex;gap:12px;align-items:center;justify-content:space-between}.archive-dialog h2{margin:0}.archive-dialog p{font-size:12px;color:#a3c6cb;line-height:1.6}.archive-dialog svg{width:100%;max-height:45vh;background:#14303f;border-radius:8px}.archive-tools input{flex:1;min-width:60px}.archive-tools{flex-wrap:wrap;margin:12px 0}.archive-dialog details{margin-top:12px}.archive-dialog pre{white-space:pre-wrap;word-break:break-word;font-size:12px}.archive-dialog ol{max-height:220px;overflow:auto}.archive-dialog button:disabled{opacity:.5;cursor:default}
</style>

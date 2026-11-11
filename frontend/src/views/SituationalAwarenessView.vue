<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef } from 'vue'
import { Radio, Download, ImageDown, Play, Square, FlaskConical, RefreshCw } from '@lucide/vue'
import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import San60Plots from '@/components/san60/San60Plots.vue'
import { controlSan60, fetchSan60State, ingestSan60 } from '@/api/san60'
import type { San60State, San60Status } from '@/types/san60'

const status = shallowRef<San60Status | null>(null)
const snapshot = shallowRef<San60State | null>(null)
const plot = ref<InstanceType<typeof San60Plots>>()
const busy = ref(false), notice = ref(''), failure = ref(''), streamMessage = ref('')
const bagPath = ref(''), speed = ref(1), bandLow = ref(2460), bandHigh = ref(2464), voxelCm = ref(200)
const wsUrl = ref('ws://127.0.0.1:9090'), rosbridge = ref(true), jsonInput = ref('')
const layers = reactive({ measured: true, field: true, track: true, opacity: .2 })
let timer: ReturnType<typeof setTimeout> | undefined, disposed = false, fetching = false, initialized = false
let external: WebSocket | null = null, generation = 0, lastIngest = 0, ingesting = false
let externalPending: Promise<unknown> | null = null
let fetchAbort: AbortController | undefined
const connected = computed(() => status.value?.connected === true)
const modeLabels: Record<string, string> = { bag: 'BAG 实测回放', demo: '模拟演示 · 合成数据', external: '外部实时输入', idle: '采集已停止', error: '采集错误' }
const simulated = computed(() => snapshot.value?.mode === 'demo' || snapshot.value?.latest?.source === 'simulation')
const mode = computed(() => snapshot.value ? (snapshot.value.mode === 'idle' && simulated.value ? '采集已停止 · 保留合成数据' : modeLabels[snapshot.value.mode] || snapshot.value.mode) : '等待服务')
const live = computed(() => snapshot.value?.latest)
const space = computed(() => snapshot.value?.spatial)
const gnss = computed(() => snapshot.value?.gnss)
const streaming = computed(() => connected.value && snapshot.value?.arrival_age_s != null && snapshot.value.arrival_age_s < 3)
const fmt = (value: number | null | undefined, decimals = 1) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(decimals) : '—'
const cmPoint = (point?: number[]) => point ? point.slice(0, 3).map(value => fmt(value * 100, 0)).join(' / ') + ' cm' : '—'

async function refresh() {
  if (disposed || fetching) return
  fetching = true; clearTimeout(timer); fetchAbort = new AbortController()
  try {
    const result = await fetchSan60State(fetchAbort.signal)
    if (disposed) return
    status.value = result
    if (result.connected && result.state) {
      snapshot.value = result.state
      if (!initialized) {
        bagPath.value = result.state.capabilities.default_bag || ''
        bandLow.value = result.state.band[0] ?? 2460; bandHigh.value = result.state.band[1] ?? 2464
        voxelCm.value = Math.round((result.state.spatial?.voxel_size || 2) * 100)
        initialized = true
      }
    } else closeExternal()
  } catch (reason) {
    if (!disposed) {
      status.value = { connected: false, state: null, serviceUrl: status.value?.serviceUrl || '', message: reason instanceof Error ? reason.message : '平台连接失败' }
      closeExternal()
    }
  } finally { fetching = false; if (!disposed) timer = setTimeout(refresh, connected.value ? 750 : 2500) }
}
async function operate(action: () => Promise<unknown>, message: string) {
  if (busy.value) return
  busy.value = true; notice.value = ''; failure.value = ''
  try { await action(); if (!disposed) { notice.value = message; await refresh() } }
  catch (reason) { if (!disposed) failure.value = reason instanceof Error ? reason.message : '操作失败' }
  finally { if (!disposed) busy.value = false }
}
function closeExternal() {
  generation++; if (external) { external.onclose = null; external.close(); external = null }
  streamMessage.value = ''; ingesting = false
}
function command(action: string) {
  closeExternal()
  return operate(async () => {
    await externalPending?.catch(() => {})
    if (!disposed) return controlSan60({ action, ...(action === 'bag' ? { path: bagPath.value.trim(), speed: speed.value } : {}) })
  },
    action === 'bag' ? '已启动 BAG 实测回放' : action === 'demo' ? '已启动模拟演示，当前使用合成数据' : action === 'clear' ? '测量已清空' : '采集已停止，历史测量已保留')
}
function applySettings() {
  if (!Number.isFinite(bandLow.value) || !Number.isFinite(bandHigh.value) || bandLow.value <= 0 || bandLow.value >= bandHigh.value) {
    failure.value = '请填写有效频段，上限须大于下限'; return
  }
  if (!Number.isInteger(voxelCm.value) || voxelCm.value < 1 || voxelCm.value > 2000) { failure.value = '体素边长须为 1–2000 cm 的整数'; return }
  return operate(() => controlSan60({ action: 'settings', band: [bandLow.value, bandHigh.value], voxel: voxelCm.value / 100 }),
    '设置已应用；切换频段会清空原频段测量，体素设置不代表定位精度')
}
function preset(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (value !== 'custom') { const center = Number(value); bandLow.value = center - 2; bandHigh.value = center + 2 }
}
function exportJson() {
  if (!snapshot.value) return
  const url = URL.createObjectURL(new Blob([JSON.stringify(snapshot.value, null, 2)], { type: 'application/json' }))
  const anchor = document.createElement('a'); anchor.href = url
  anchor.download = `SAN60_态势_${new Date().toISOString().replace(/[:.]/g, '-')}.json`; anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
function sendJson() {
  return operate(async () => {
    const raw = jsonInput.value.trim(); if (!raw) throw new Error('请先填写 JSON 数据')
    let frames: unknown
    try { frames = JSON.parse(raw) } catch { frames = raw.split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line)) }
    closeExternal(); await externalPending?.catch(() => {}); if (disposed) return
    await controlSan60({ action: 'stop' }); if (!disposed) await ingestSan60(frames)
  }, 'JSON 已接收，已切换到外部输入模式')
}
function connectExternal() {
  return operate(async () => {
    const url = new URL(wsUrl.value)
    if (!['ws:', 'wss:'].includes(url.protocol)) throw new Error('外部地址须以 ws:// 或 wss:// 开头')
    closeExternal(); await externalPending?.catch(() => {}); if (disposed) return
    await controlSan60({ action: 'stop' }); if (disposed) return
    const socket = new WebSocket(url.toString()), current = generation
    external = socket; streamMessage.value = '正在连接外部 WebSocket…'
    socket.onopen = () => {
      if (disposed || current !== generation) return
      streamMessage.value = '外部 WebSocket 已连接'
      if (rosbridge.value) for (const topic of ['/san60/spectrum', '/san60/gnss/status']) socket.send(JSON.stringify({ op: 'subscribe', topic, type: 'std_msgs/String', throttle_rate: 50, queue_length: 1 }))
    }
    socket.onmessage = async event => {
      if (disposed || current !== generation) return
      try {
        const data = JSON.parse(event.data), isGnss = data.topic?.includes('/gnss/') || data.type === 'gnss'
        if (!isGnss && (performance.now() - lastIngest < 100 || ingesting)) return
        if (!isGnss) { lastIngest = performance.now(); ingesting = true }
        const previous = externalPending
        const request = (async () => { await previous?.catch(() => {}); if (!disposed && current === generation) await ingestSan60(data) })()
        externalPending = request
        try { await request } finally { if (externalPending === request) externalPending = null; if (!isGnss && current === generation) ingesting = false }
      } catch (reason) { if (current === generation) streamMessage.value = '外部输入异常：' + (reason instanceof Error ? reason.message : '数据格式错误') }
    }
    socket.onerror = () => { if (current === generation) streamMessage.value = '外部 WebSocket 连接失败，请检查地址与服务' }
    socket.onclose = () => { if (current === generation) { external = null; streamMessage.value = '外部 WebSocket 已断开' } }
  }, '已停止回放，正在连接外部输入')
}
onMounted(() => { void refresh() })
onBeforeUnmount(() => { disposed = true; clearTimeout(timer); fetchAbort?.abort(); closeExternal() })
</script>

<template>
  <ConsoleLayout title="态势感知" eyebrow="ELECTROMAGNETIC SITUATIONAL AWARENESS" :show-refresh="false">
    <div class="san60-workspace">
      <header class="situation-heading">
        <div class="identity"><div class="sensor-icon"><Radio :size="24" /></div><div><h2>SAN60 电磁态势</h2><p>频谱 · 定位 · 三维空间测量</p></div></div>
        <div class="heading-actions"><span class="mode" :class="{ demo: snapshot?.mode === 'demo' }">{{ mode }}</span><span class="connection" :class="{ online: connected }">{{ connected ? (streaming ? '● 正在接收' : '● 服务在线') : '○ 服务未连接' }}</span><button :disabled="!snapshot" @click="exportJson"><Download :size="14" />导出 JSON</button></div>
      </header>
      <div v-if="!connected" class="connection-alert" role="status"><span>{{ status?.message || '正在连接 SAN60 服务…' }}<small v-if="snapshot">当前保留上次测量画面，数据未实时更新。</small></span><button @click="refresh"><RefreshCw :size="14" />重新连接</button></div>
      <div v-if="failure || notice" :class="failure ? 'feedback failure' : 'feedback'" :role="failure ? 'alert' : 'status'">{{ failure || notice }}</div>
      <div class="situation-grid">
        <aside class="controls">
          <section><h3><span>01</span> 信号输入</h3><label for="san-bag">BAG 完整路径（留空使用随包数据）</label><input id="san-bag" v-model="bagPath" placeholder="使用随包 BAG 文件" :disabled="busy" /><div class="control-row"><label for="san-speed">回放倍速</label><select id="san-speed" v-model.number="speed" :disabled="busy"><option :value="1">1×</option><option :value="10">10×</option><option :value="50">50×</option><option :value="100">100×</option></select></div><div class="button-row"><button class="primary" :disabled="!connected || busy" @click="command('bag')"><Play :size="13" />开始回放</button><button :disabled="!connected || busy" @click="command('stop')"><Square :size="12" />停止采集</button></div><p class="hint">回放使用 BAG 中的实测频谱和 GNSS；重新回放会重新积累测量。</p>
            <details><summary>外部 JSON / WebSocket</summary><label for="san-ws">WebSocket / rosbridge 地址</label><input id="san-ws" v-model="wsUrl" /><label class="check"><input v-model="rosbridge" type="checkbox" />订阅 SAN60 频谱与 GNSS 话题</label><button :disabled="!connected || busy" @click="connectExternal">连接外部输入</button><p v-if="streamMessage" class="hint" role="status">{{ streamMessage }}</p><label for="san-json">JSON 对象、数组或逐行 JSON</label><textarea id="san-json" v-model="jsonInput" rows="4" placeholder='{"type":"spectrum", ...}' /><button :disabled="!connected || busy" @click="sendJson">发送 JSON</button><p class="hint">连接或发送前自动停止回放，避免混合输入。</p></details>
            <div class="usb-note">USB 采集桥未安装 · 支持 BAG / 外部输入</div>
          </section>
          <section><h3><span>02</span> GNSS 定位 <em :class="{ valid: gnss?.valid }">{{ gnss?.valid ? '已锁定' : '等待定位' }}</em></h3><dl><dt>纬度</dt><dd>{{ gnss?.valid ? fmt(gnss.latitude, 8) : '—' }}</dd><dt>经度</dt><dd>{{ gnss?.valid ? fmt(gnss.longitude, 8) : '—' }}</dd><dt>海拔</dt><dd>{{ gnss?.valid ? fmt(gnss.altitude_m) + ' m' : '—' }}</dd><dt>卫星 / C/N₀</dt><dd>{{ gnss?.satellites_used ?? '—' }} / {{ gnss?.positioning_snr_avg ?? '—' }}</dd></dl><p class="hint">GNSS 与频谱时间差 ≤ 1.5 秒才生成空间点；无有效定位时保留历史测量。</p></section>
          <section><h3><span>03</span> 频段与体素</h3><label for="san-preset">目标频段</label><select id="san-preset" @change="preset"><option value="2462">2462 ± 2 MHz</option><option value="2412">2412 ± 2 MHz</option><option value="2437">2437 ± 2 MHz</option><option value="2480">2480 ± 2 MHz</option><option value="custom">自定义</option></select><div class="input-row"><div><label for="san-low">下限 MHz</label><input id="san-low" v-model.number="bandLow" type="number" step="any" /></div><div><label for="san-high">上限 MHz</label><input id="san-high" v-model.number="bandHigh" type="number" step="any" /></div></div><label for="san-voxel">体素边长 cm · 1–2000</label><input id="san-voxel" v-model.number="voxelCm" type="number" min="1" max="2000" step="1" /><button class="wide" :disabled="!connected || busy" @click="applySettings">应用设置</button><p class="hint">当前应用：{{ snapshot?.band?.join('–') || '—' }} MHz · {{ fmt((space?.voxel_size || 2) * 100, 0) }} cm</p><label class="check"><input v-model="layers.measured" type="checkbox" />实测强度体素</label><label class="check"><input v-model="layers.field" type="checkbox" />近邻插值体素</label><label class="check"><input v-model="layers.track" type="checkbox" />GNSS 轨迹与测量点</label><label for="san-opacity">插值透明度 {{ layers.opacity.toFixed(2) }}</label><input id="san-opacity" v-model.number="layers.opacity" type="range" min="0.05" max="0.65" step="0.05" /></section>
          <section><h3><span>04</span> 查看与保存</h3><div class="button-row"><button :disabled="!snapshot" @click="plot?.resetView()">重置视角</button><button :disabled="!snapshot || busy" @click="operate(async () => { await plot?.saveImage() }, '图片已保存')"><ImageDown :size="13" />保存图片</button><button :disabled="!connected || busy" @click="command('clear')">清空测量</button><button class="demo-button" :disabled="!connected || busy" @click="command('demo')"><FlaskConical :size="13" />模拟演示</button></div><p class="hint">模拟演示使用合成数据；热点代表高功率测量位置，源估计须满足原算法的覆盖和稳定性条件。</p></section>
        </aside>
        <main class="measurements">
          <div class="metrics"><article><span>输入帧 / 频率</span><strong>{{ (snapshot?.frames || 0).toLocaleString() }}<small>{{ fmt(snapshot?.rate_hz) }} Hz</small></strong></article><article><span>当前峰值</span><strong>{{ fmt(live?.peak_dbm) }}<small>dBm</small></strong></article><article><span>目标频段功率</span><strong>{{ fmt(live?.band_dbm) }}<small>dBm</small></strong></article><article><span>测量 / 体素</span><strong>{{ space?.track.length || 0 }} / {{ space?.cells.length || 0 }}</strong></article></div>
          <San60Plots ref="plot" :state="snapshot" :layers="layers" />
          <section class="diagnostics"><div><span>采集状态</span><b>{{ snapshot?.message || '等待 SAN60 服务' }}</b><small>{{ live ? new Date(live.time * 1000).toLocaleString('zh-CN', { hour12: false }) : '暂无测量时间' }}</small></div><div><span>空间覆盖 E / N / U</span><b>{{ cmPoint(space?.span) }}</b><small>当前热区 {{ cmPoint(space?.hotspot?.p) }}</small></div><div><span>定位模型</span><b>{{ space?.source ? `RMSE ${fmt(space.source.rmse_db)} dB · 稳定性 ${fmt(space.source.stability_m * 100, 0)} cm` : '等待覆盖与稳定性条件满足' }}</b><small>温度 {{ fmt(live?.temperature_c) }} °C · 未配对 {{ snapshot?.unpaired || 0 }} · 跳变 {{ snapshot?.rejected_jumps || 0 }} · 无效帧 {{ snapshot?.invalid_frames || 0 }}</small></div></section>
          <p class="measurement-note">坐标相对首个有效 GNSS 点；接口单位 m，三维显示 cm。厘米显示不代表厘米定位精度，接收功率 dBm 不等同于电场强度。</p>
          <details class="logs"><summary>采集日志</summary><p v-for="(entry, index) in snapshot?.logs.slice(-10)" :key="index">{{ new Date(entry.time * 1000).toLocaleTimeString() }} {{ entry.text }}</p></details>
        </main>
      </div>
    </div>
  </ConsoleLayout>
</template>

<style scoped>
.san60-workspace{max-width:1920px;margin:auto;color:#c6dce0}.situation-heading,.identity,.heading-actions,.button-row,.control-row{display:flex;align-items:center;gap:10px}.situation-heading{justify-content:space-between;padding:6px 0 20px;gap:16px}.identity{gap:12px}.sensor-icon{display:grid;place-items:center;width:48px;height:48px;border:1px solid #30616b;border-radius:12px;color:#72e5d3;background:#14333b}h2{margin:0;font-size:20px;font-weight:600;color:#e5f2f0}.identity p{margin:7px 0 0;color:#83a4ad;font-size:12px}.heading-actions{flex-wrap:wrap;justify-content:flex-end;font-size:11px}.mode{padding:6px 10px;background:#123c40;border:1px solid #286466;border-radius:5px;color:#8ee4d7}.mode.demo{background:#503b1e;border-color:#956d30;color:#ffdb92}.connection{color:#bc8992;white-space:nowrap}.connection.online{color:#7bd6c1}.situation-grid{display:grid;grid-template-columns:286px minmax(0,1fr);gap:18px;align-items:start}.controls{display:flex;flex-direction:column;gap:12px}.controls section{padding:17px 16px;border:1px solid #26434c;border-radius:11px;background:linear-gradient(130deg,#102b34,#0b202a)}h3{display:flex;align-items:center;gap:9px;margin:0 0 16px;font-size:13px;color:#daeae9}h3>span{color:#59bcb1;font:10px Consolas,monospace}h3 em{margin-left:auto;padding:3px 6px;border-radius:4px;background:#2e3033;color:#b2afb0;font-size:10px;font-style:normal}h3 em.valid{background:#164237;color:#7cdec0}label{display:block;margin:12px 0 7px;color:#92aeb7;font-size:11px}input:not([type=checkbox]):not([type=range]),select,textarea{box-sizing:border-box;width:100%;min-width:0;padding:9px 10px;border:1px solid #31515d;border-radius:5px;background:#071b25;color:#d5e8e8;font:12px 'Microsoft YaHei',sans-serif;outline:none}input:focus,select:focus,textarea:focus{border-color:#65cdbd}input[type=range]{width:100%;accent-color:#67d4c2}input[type=checkbox]{accent-color:#67d4c2}.check{display:flex;align-items:center;gap:7px;margin:10px 0}.input-row{display:grid;grid-template-columns:1fr 1fr;gap:8px}.control-row{justify-content:space-between;margin:10px 0}.control-row label{margin:0}.control-row select{width:100px}.button-row{flex-wrap:wrap;margin-top:10px}.button-row button{flex:1;white-space:nowrap}button{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:8px 10px;border:1px solid #315860;border-radius:5px;background:#193a43;color:#d2e6e6;cursor:pointer;font-size:11px;transition:background .15s}button:hover:not(:disabled){background:#28535a}button:disabled{opacity:.4;cursor:not-allowed}button.primary{border-color:#409b8b;background:#287d6f;color:#f0fffc}.demo-button{color:#f1d195}.wide{width:100%;margin-top:10px}.hint{margin:11px 0 0;color:#829fa9;font-size:10px;line-height:1.8}details{margin-top:12px}summary{color:#8bbcb9;font-size:11px;cursor:pointer}.usb-note{margin-top:14px;padding-top:11px;border-top:1px solid #24404a;color:#6e8a95;font-size:10px}dl{display:grid;grid-template-columns:80px 1fr;gap:11px;margin:0;font-size:11px}dt{color:#829fa9}dd{margin:0;text-align:right;color:#c6e5e5;font-family:Consolas,monospace}.measurements{display:flex;flex-direction:column;gap:14px;min-width:0}.metrics{display:grid;grid-template-columns:1.2fr 1fr 1fr 1fr;gap:10px}.metrics article{min-width:0;padding:17px 18px;border:1px solid #264852;border-radius:10px;background:linear-gradient(145deg,#15333e,#0b222c)}.metrics span{display:block;color:#89a8b3;font-size:11px}.metrics strong{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-top:10px;color:#e0efed;font:24px Consolas,monospace}.metrics small{font-size:10px;color:#7da7ac}.diagnostics{display:grid;grid-template-columns:1.1fr 1fr 1.2fr;gap:16px;padding:17px 20px;border:1px solid #264550;border-radius:10px;background:#0b222c}.diagnostics span,.diagnostics b,.diagnostics small{display:block}.diagnostics span{font-size:10px;color:#779fa8}.diagnostics b{margin:9px 0;color:#bad9dc;font-size:11px;line-height:1.6;font-weight:500}.diagnostics small{font-size:10px;color:#799ca7;line-height:1.6}.measurement-note{margin:0;color:#6e929e;font-size:10px;line-height:1.8}.logs{margin-top:0;color:#88a4b0;font-size:10px}.connection-alert,.feedback{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 16px;margin-bottom:14px;border:1px solid #52604b;border-radius:8px;background:#2a3025;color:#d0d3a9;font-size:12px;line-height:1.7}.connection-alert small{display:block}.feedback{border-color:#28584d;background:#15362f;color:#a2dcc5}.failure{border-color:#80515a;background:#36252d;color:#f0b5c3}@media(min-width:1450px){.measurements :deep(.plots){grid-template-rows:540px 240px}}@media(max-width:1150px){.situation-grid{grid-template-columns:250px minmax(0,1fr);gap:12px}.metrics{grid-template-columns:1fr 1fr}.diagnostics{grid-template-columns:1fr}.metrics strong{font-size:21px}.situation-heading{align-items:flex-start}.identity h2{font-size:17px}}@media(max-width:800px){.situation-grid{grid-template-columns:1fr}.controls{display:grid;grid-template-columns:1fr 1fr}.situation-heading{flex-direction:column}.heading-actions{justify-content:flex-start}.measurements{grid-row:1}.diagnostics{grid-template-columns:1fr 1fr}.controls section{min-width:0}}@media(max-width:540px){.controls,.diagnostics{grid-template-columns:1fr}.metrics article{padding:13px}.situation-heading h2{font-size:18px}}
.metrics{margin:0;padding:0;border:0}
</style>

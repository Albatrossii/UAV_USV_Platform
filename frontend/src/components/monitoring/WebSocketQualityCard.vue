<script setup lang="ts">
import { computed, ref } from 'vue'

import { useRealtimeStore } from '@/stores/realtime'
import { getWebSocketQuality } from '@/utils/websocketQuality'

const realtime = useRealtimeStore()
const detailsOpen = ref(false)
// A Vite update may mount this card while Pinia still holds the previous store shape.
// Show an empty measurement instead of breaking the live simulation render.
const linkQuality = computed(() => realtime.linkQuality ?? {
  samples: [], lastMessageAt: null, reconnectCount: 0, now: Date.now(),
})
const quality = computed(() => getWebSocketQuality(
  realtime.connectionState, linkQuality.value.samples, linkQuality.value.now,
))
const samples = computed(() => linkQuality.value.samples.filter(sample => (
  sample.sentAt <= linkQuality.value.now && linkQuality.value.now - sample.sentAt < 60_000
)).slice(-30))
const validRtt = (value: number | null): value is number => value !== null && Number.isFinite(value) && value >= 0
const values = computed(() => samples.value.map(sample => sample.rttMs).filter(validRtt))
const timeoutCount = computed(() => samples.value.filter(sample => sample.rttMs === null).length)
const timeoutRate = computed(() => samples.value.length
  ? Math.round(timeoutCount.value / samples.value.length * 100) : null)
const jitter = computed(() => {
  if (quality.value.rttMs === null) return null
  const differences: number[] = []
  samples.value.slice(1).forEach((sample, index) => {
    const previous = samples.value[index]?.rttMs ?? null
    if (validRtt(sample.rttMs) && validRtt(previous)) differences.push(Math.abs(sample.rttMs - previous))
  })
  return differences.length ? differences.reduce((sum, value) => sum + value, 0) / differences.length : null
})
const scale = computed(() => values.value.length ? Math.max(60, Math.ceil(Math.max(...values.value) / 50) * 50) : 60)
const path = computed(() => {
  let result = ''
  let previousAt: number | null = null
  for (const sample of samples.value) {
    if (!validRtt(sample.rttMs)) { previousAt = null; continue }
    const x = Math.max(0, Math.min(130, (60_000 - (linkQuality.value.now - sample.sentAt)) / 60_000 * 130))
    const y = 52 - sample.rttMs / scale.value * 47
    const contiguous = previousAt !== null && sample.sentAt - previousAt <= 3_000
    result += `${contiguous ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)} `
    // A single successful sample should still be visible before a trend exists.
    if (!contiguous) result += `l.1 0 `
    previousAt = sample.sentAt
  }
  return result
})
const history = computed(() => {
  const slots = Array.from({ length: 30 }, () => null as null | { rttMs: number | null; sentAt: number })
  for (const sample of samples.value) {
    const index = 29 - Math.floor((linkQuality.value.now - sample.sentAt) / 2_000)
    if (index >= 0 && index < 30) slots[index] = sample
  }
  return slots.map(sample => ({
    tone: !sample ? 'empty' : sample.rttMs === null ? 'timeout'
      : sample.rttMs <= 100 ? 'excellent' : sample.rttMs <= 300 ? 'good' : 'poor',
    title: !sample ? '此时间段无探测结果' : sample.rttMs === null ? '探针超时' : `RTT ${formatMs(sample.rttMs)} ms`,
  }))
})
const connectionLabel = computed(() => realtime.connectionState === 'CONNECTED' ? '已连接'
  : realtime.connectionState === 'CONNECTING' ? linkQuality.value.reconnectCount ? '重连中' : '连接中' : '未连接')
const recentMessage = computed(() => {
  const received = linkQuality.value.lastMessageAt
  if (received === null) return '—'
  const seconds = Math.max(0, (linkQuality.value.now - received) / 1000)
  return seconds < 60 ? `${seconds.toFixed(1)} 秒前` : `${Math.floor(seconds / 60)} 分钟前`
})
const chartLabel = computed(() => values.value.length
  ? `最近60秒平台WebSocket往返延迟，${samples.value.length}次探测，${timeoutCount.value}次超时`
  : '暂无有效往返延迟样本')
const latencyCaption = computed(() => quality.value.rttMs !== null ? '近10秒中位数'
  : quality.value.grade === 'offline' ? '等待连接恢复' : quality.value.grade === 'poor' ? '等待探测恢复' : '等待有效样本')
const gradeCaption = computed(() => {
  if (quality.value.rttMs !== null) {
    return quality.value.grade === 'excellent' ? '优秀 · RTT ≤100 ms'
      : quality.value.grade === 'good' ? '良好 · 100 < RTT ≤300 ms' : '差 · RTT >300 ms'
  }
  return quality.value.grade === 'offline' ? '连接不可用 · 等待重新连接'
    : quality.value.grade === 'poor' ? '探测异常 · 连续3次未响应' : '等待有效样本 · 暂不分级'
})
function formatMs(value: number | null) { return value === null ? '—' : String(Number(value.toFixed(1))) }
function toggleDetails(event: Event) { detailsOpen.value = (event.target as HTMLDetailsElement).open }
</script>

<template>
  <section class="wsq-card" :data-grade="quality.grade" aria-label="平台 WebSocket 通信状态" data-test="websocket-quality-card">
    <header class="wsq-head">
      <h4><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 13V9m4 4V6m4 7V3m4 10V1" /></svg>通信状态</h4>
      <span class="wsq-protocol">WebSocket</span>
    </header>
    <div class="wsq-connection" aria-live="polite">
      <span class="wsq-connected"><i />{{ connectionLabel }}</span>
      <span class="wsq-grade" :aria-label="`响应等级：${quality.label}`">{{ quality.label }}</span>
    </div>
    <p class="wsq-scope">浏览器 <span>↔</span> 平台 WS 服务</p>

    <div class="wsq-latency">
      <div>
        <div class="wsq-label">往返延迟 RTT</div>
        <div class="wsq-value" data-test="rtt">{{ formatMs(quality.rttMs) }}<small v-if="quality.rttMs !== null">ms</small></div>
        <div class="wsq-sub">{{ latencyCaption }}</div>
      </div>
      <div class="wsq-chart">
        <div class="wsq-scale"><span>{{ values.length ? `${scale} ms` : '—' }}</span><span>60秒</span></div>
        <svg viewBox="0 0 130 55" role="img" :aria-label="chartLabel">
          <path d="M0 12H130M0 32H130M0 52H130" class="wsq-grid" />
          <path :d="path" class="wsq-trend" />
        </svg>
        <div class="wsq-scale"><span>0</span><span>{{ !values.length ? '未采样' : quality.rttMs === null ? '历史样本' : '实时探测' }}</span></div>
      </div>
    </div>
    <p class="wsq-reason" :title="quality.reason">{{ gradeCaption }}</p>

    <details class="wsq-details" @toggle="toggleDetails">
      <summary><span>{{ detailsOpen ? '收起详细指标' : '展开详细指标' }}</span><span class="wsq-chevron" aria-hidden="true">⌄</span></summary>
      <dl class="wsq-stats">
        <div><dt>RTT 波动</dt><dd>{{ formatMs(jitter) }}<small v-if="jitter !== null">ms</small></dd><span class="wsq-sub">相邻有效样本差值均值</span></div>
        <div><dt>心跳超时率</dt><dd>{{ timeoutRate ?? '—' }}<small v-if="timeoutRate !== null">%</small></dd><span class="wsq-sub">{{ samples.length ? `近60秒 ${timeoutCount} / ${samples.length} 次` : '暂无探测样本' }}</span></div>
        <div><dt>最近消息</dt><dd class="wsq-age">{{ recentMessage }}</dd><span class="wsq-sub">含探针回显 · 本地接收</span></div>
        <div><dt>重连尝试</dt><dd>{{ linkQuality.reconnectCount }}<small>次</small></dd><span class="wsq-sub">当前登录会话累计</span></div>
      </dl>
      <div class="wsq-history-label"><span>最近60秒探测</span><span>每格2秒</span></div>
      <div class="wsq-history" role="img" :aria-label="`最近60秒，${samples.length}次探测，${timeoutCount}次超时`">
        <span v-for="(beat, index) in history" :key="index" :class="beat.tone" :title="beat.title" />
      </div>
      <div class="wsq-scale"><span>60秒前</span><span>现在</span></div>
      <details class="wsq-help">
        <summary>指标口径与边界</summary>
        <p>{{ quality.reason }}</p>
        <p>RTT 是浏览器与平台服务的应用层往返耗时，不是单向时延。探针每2秒一次，超时率不等于网络丢包率；空白格表示未采样。</p>
        <p>此卡不表示 Unity 场景就绪、设备动作完成或无线信号等级。页面在后台时暂停探测，返回后重新采样。</p>
      </details>
      <div class="wsq-rules">
        <h5>分级规则 · 项目建议值</h5>
        <dl>
          <div><dt class="excellent">优秀</dt><dd>RTT ≤100 ms</dd></div>
          <div><dt class="good">良好</dt><dd>100 &lt; RTT ≤300 ms</dd></div>
          <div><dt class="poor">差</dt><dd>RTT &gt;300 ms / 连续超时</dd></div>
          <div><dt class="offline">无连接</dt><dd>连接断开或正在重连</dd></div>
          <div><dt>待检测</dt><dd>已连接，暂无新鲜样本</dd></div>
        </dl>
        <p>取最近5次探针的有效 RTT 中位数分级；断线优先。连续3次超时显示“差”，不沿用旧延迟。阈值为可调整的项目建议，并非通用标准。</p>
      </div>
    </details>
  </section>
</template>

<style scoped>
.wsq-card { --wsq-tone: #91adb3; margin-top: 20px; padding: 12px 10px 10px; border: 1px solid #456068; border-radius: 6px; background: linear-gradient(145deg, #0d282b, #0a1e23); color: #d8ede6; font-size: 10px; min-width: 0; }
.wsq-card[data-grade=excellent] { --wsq-tone: #68d8b9; border-color: #386b63; }
.wsq-card[data-grade=good] { --wsq-tone: #83c9f3; border-color: #365e75; }
.wsq-card[data-grade=poor] { --wsq-tone: #f2b16b; border-color: #786242; }
.wsq-card[data-grade=offline] { --wsq-tone: #ee8b82; border-color: #79504c; }
.wsq-head, .wsq-connection, .wsq-scale, .wsq-history-label { display: flex; align-items: center; justify-content: space-between; gap: 5px; }
.wsq-head h4 { display: flex; align-items: center; gap: 5px; margin: 0; color: #e4f5f1; font-size: 12px; white-space: nowrap; }
.wsq-head svg { width: 13px; height: 13px; fill: none; stroke: #6ce4d5; stroke-width: 1.6; stroke-linecap: round; }
.wsq-protocol { font: 9px/1.5 Consolas, monospace; color: #accdc9; border: 1px solid #3c6264; border-radius: 3px; padding: 1px 4px; }
.wsq-connection { margin-top: 13px; color: var(--wsq-tone); }
.wsq-connected { display: flex; align-items: center; gap: 6px; font-size: 11px; }
.wsq-connected i { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.wsq-grade { border: 1px solid color-mix(in srgb, var(--wsq-tone), transparent 65%); border-radius: 3px; padding: 2px 7px; font-size: 11px; font-weight: 600; }
.wsq-scope { color: #8baaaa; font-size: 9px; margin: 7px 0 0; line-height: 1.6; }
.wsq-scope span { margin: 0 2px; color: #688a88; }
.wsq-latency { display: grid; grid-template-columns: minmax(62px, .85fr) minmax(0, 1fr); align-items: center; gap: 7px; margin: 14px 0 12px; }
.wsq-label { color: #a9c7c1; font-size: 9px; }
.wsq-value { color: var(--wsq-tone); font: 29px/1.3 Consolas, monospace; margin-top: 5px; letter-spacing: -1px; overflow-wrap: anywhere; }
.wsq-card small { margin-left: 3px; color: #8caca6; font: 9px 'Microsoft YaHei', sans-serif; letter-spacing: 0; }
.wsq-sub { display: block; margin-top: 4px; font: 8px/1.65 'Microsoft YaHei', sans-serif; color: #7e9e99; }
.wsq-chart { min-width: 0; }
.wsq-chart svg { width: 100%; height: 55px; display: block; overflow: visible; }
.wsq-grid { fill: none; stroke: #2b4c4d; stroke-width: .6; }
.wsq-trend { fill: none; stroke: var(--wsq-tone); stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.wsq-scale { color: #779c95; font: 8px/1.5 Consolas, 'Microsoft YaHei', monospace; }
.wsq-reason { margin: 0 0 12px; padding-left: 6px; color: var(--wsq-tone); border-left: 2px solid var(--wsq-tone); font-size: 9px; line-height: 1.7; overflow-wrap: anywhere; }
.wsq-details { border-top: 1px solid #2a4c4d; }
.wsq-details > summary { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-height: 33px; padding-top: 8px; cursor: pointer; color: #a7c9c2; font-size: 10px; list-style: none; }
.wsq-details > summary::-webkit-details-marker { display: none; }
.wsq-chevron { font-size: 15px; color: #77b3a5; }
.wsq-details[open] > summary .wsq-chevron { transform: rotate(180deg); }
.wsq-card summary:focus-visible { outline: 2px solid #6ce4d5; outline-offset: 3px; }
.wsq-stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 13px 8px; padding: 0 0 12px; margin: 12px 0; border-bottom: 1px solid #25484a; }
.wsq-stats dt { font-size: 9px; color: #8aaaa8; }
.wsq-stats dd { margin: 5px 0 0; font: 14px/1.2 Consolas, monospace; }
.wsq-stats .wsq-age { font-size: 12px; }
.wsq-history-label { color: #9ebbb7; font-size: 8px; margin-bottom: 7px; }
.wsq-history { display: grid; grid-template-columns: repeat(30, minmax(0, 1fr)); gap: 2px; height: 13px; margin-bottom: 5px; }
.wsq-history span { background: #2e464b; border-radius: 2px; }
.wsq-history .excellent { background: #60bfa4; }
.wsq-history .good { background: #77b5db; }
.wsq-history .poor { background: #d8a061; }
.wsq-history .timeout { background: #c67972; }
.wsq-help { color: #82a9a4; margin-top: 13px; font-size: 9px; line-height: 1.8; }
.wsq-help summary { cursor: pointer; color: #a1c9c1; }
.wsq-help p { margin: 8px 0 0; }
.wsq-rules { border-top: 1px solid #27484a; margin-top: 12px; padding-top: 12px; }
.wsq-rules h5 { margin: 0 0 9px; font-weight: 500; font-size: 10px; color: #b5ccc4; }
.wsq-rules dl { margin: 0; display: grid; gap: 7px; color: #8faaab; font-size: 9px; }
.wsq-rules dl > div { display: flex; justify-content: space-between; gap: 7px; }
.wsq-rules dt { flex-shrink: 0; }
.wsq-rules dd { margin: 0; text-align: right; }
.wsq-rules .excellent { color: #68d8b9; }.wsq-rules .good { color: #83c9f3; }.wsq-rules .poor { color: #f2b16b; }.wsq-rules .offline { color: #ee8b82; }
.wsq-rules p { color: #7d9f9a; font-size: 9px; line-height: 1.8; margin: 11px 0 0; }
</style>

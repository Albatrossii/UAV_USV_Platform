<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import { ChevronRight, Crosshair, Plane, Radio, Ship } from '@lucide/vue'

import { nodeMonitoringKey, useNodeMonitoring } from '@/composables/useNodeMonitoring'
import { buildNodeCommunicationRows, normalizeNodeCode } from '@/utils/nodeCommunication'

const props = withDefaults(defineProps<{
  deviceCodes: string[]
  selectedDevice?: string
  targets?: Array<{ id: string; label: string; state: string }>
  targetSource?: string
  defaultOpen?: boolean
}>(), { selectedDevice: '', targets: () => [], targetSource: '算法仿真 · 当前场景', defaultOpen: true })
const emit = defineEmits<{ select: [code: string] }>()
const monitor = inject(nodeMonitoringKey, null) ?? useNodeMonitoring()
const expandedCode = ref('')
const abnormalOnly = ref(false)
const rows = computed(() => buildNodeCommunicationRows(
  props.deviceCodes, monitor.nodes.value, monitor.now.value, monitor.observedAt.value, monitor.available.value,
))
const onlineCount = computed(() => rows.value.filter(row => row.state === 'online').length)
const delayedCount = computed(() => rows.value.filter(row => row.state === 'delayed').length)
const offlineCount = computed(() => rows.value.filter(row => row.state === 'offline').length)
const unknownCount = computed(() => rows.value.filter(row => row.state === 'unknown').length)
const abnormalCount = computed(() => rows.value.filter(row => row.state === 'delayed' || row.state === 'offline').length)
const visibleRows = computed(() => abnormalOnly.value
  ? rows.value.filter(row => row.state === 'delayed' || row.state === 'offline') : rows.value)
const sourceLabels: Record<string, string> = {
  ROS_WEBSOCKET: 'ROS / Gazebo', ROS_GATEWAY_V1: 'ROS 网关 V1', CONTROL_SESSION: '控制会话',
}

watch(() => props.deviceCodes, () => {
  if (!rows.value.some(row => row.code === expandedCode.value)) expandedCode.value = ''
})

function selectNode(code: string) {
  expandedCode.value = expandedCode.value === code ? '' : code
  emit('select', code)
}
function heartbeatAge(age: number | null) {
  if (age === null) return '尚无有效心跳'
  if (age < 60) return `${Math.floor(age)} 秒前`
  if (age < 3600) return `${Math.floor(age / 60)} 分钟前`
  return `${Math.floor(age / 3600)} 小时前`
}
function targetState(state: string) {
  const labels: Record<string, string> = {
    ACTIVE: '活动中', TRACKING: '跟踪中', DETECTED: '已发现', CAPTURED: '已捕获',
    ESCAPING: '逃逸中', ESCORTING: '护航中', PROTECTED: '受保护', SAFE: '已安全',
    STOPPED: '已停止', COMPLETED: '已完成', LOST: '丢失', UNKNOWN: '未上报',
  }
  return labels[state.toUpperCase()] ?? (state || '未上报')
}
</script>

<template>
  <div class="ncp-panels" data-test="node-communication-panel">
    <details class="ncp-panel" :open="props.defaultOpen" data-test="nodes-panel">
      <summary class="ncp-head">
        <span class="ncp-title"><Radio :size="16" aria-hidden="true" />平台节点</span>
        <span class="ncp-meta">{{ onlineCount }} / {{ rows.length }} 在线<ChevronRight :size="12" class="ncp-arrow" aria-hidden="true" /></span>
      </summary>
      <p class="ncp-caption">按场景编号关联运行节点 · 非 Unity 虚拟状态</p>
      <div class="ncp-counts" aria-live="polite">
        <span class="online"><i />在线 <b>{{ onlineCount }}</b></span>
        <span class="delayed"><i />降级 <b>{{ delayedCount }}</b></span>
        <span class="offline"><i />离线 <b>{{ offlineCount }}</b></span>
        <span class="unknown"><i />未知 <b>{{ unknownCount }}</b></span>
      </div>
      <div class="ncp-toolbar"><span>设备 / 状态 / 心跳</span><label><input v-model="abnormalOnly" type="checkbox" />只看异常<span class="ncp-sr">（{{ abnormalCount }} 台）</span></label></div>
      <p v-if="monitor.error.value" class="ncp-notice" role="status">{{ monitor.error.value }}</p>
      <p v-else-if="monitor.loading.value && monitor.observedAt.value === null" class="ncp-notice" role="status">正在获取节点状态…</p>
      <div v-for="row in visibleRows" :key="row.code" class="ncp-entry" :data-state="row.state" :data-node="row.code">
        <button
          type="button" class="ncp-row" :class="row.state"
          :data-selected="normalizeNodeCode(props.selectedDevice) === normalizeNodeCode(row.code)"
          :aria-expanded="expandedCode === row.code" :aria-controls="`node-detail-${row.code}`"
          :aria-label="`${row.code} ${row.stateLabel}，${expandedCode === row.code ? '收起' : '展开'}详情`"
          @click="selectNode(row.code)"
        >
          <Plane v-if="row.type === 'UAV'" :size="18" aria-hidden="true" /><Ship v-else :size="18" aria-hidden="true" />
          <span class="ncp-name"><strong>{{ row.code }}</strong><small>{{ row.type === 'UAV' ? '无人机' : '无人艇' }}</small></span>
          <span class="ncp-state"><i />{{ row.stateLabel }}</span>
          <span class="ncp-bars" :class="{ unavailable: row.qualityPercent === null }" role="img" :aria-label="`心跳新鲜度：${row.qualityLabel}，非无线信号`" :title="`心跳新鲜度：${row.qualityLabel}，并非无线信号强度`"><i v-for="bar in 4" :key="bar" :class="{ filled: bar <= row.bars }" /></span>
          <ChevronRight :size="11" class="ncp-row-arrow" aria-hidden="true" />
        </button>
        <div v-show="expandedCode === row.code" :id="`node-detail-${row.code}`" class="ncp-detail" role="region" :aria-label="`${row.code} 节点详情`">
          <p class="ncp-device-name">{{ row.name }}<span>{{ sourceLabels[row.source] ?? (row.source || '尚无节点来源') }}</span></p>
          <dl>
            <div><dt>最近心跳</dt><dd data-test="heartbeat-age">{{ heartbeatAge(row.heartbeatAgeSeconds) }}</dd></div>
            <div><dt>心跳新鲜度</dt><dd data-test="node-quality">{{ row.qualityPercent === null ? '—' : `${row.qualityPercent}%` }}</dd></div>
            <div><dt>节点 RTT</dt><dd>—<small>暂未采集</small></dd></div>
            <div><dt>节点超时率</dt><dd>—<small>暂未采集</small></dd></div>
          </dl>
          <p class="ncp-endpoint">通信地址 <span>{{ row.endpoint || '未配置' }}</span></p>
          <p class="ncp-reason">{{ row.reason }}</p>
        </div>
      </div>
      <p v-if="!visibleRows.length" class="ncp-empty">{{ rows.length ? '当前没有已确认的异常节点；未接入不计为离线。' : '生成场景后显示对应设备节点。' }}</p>
      <div class="ncp-legend"><span class="online"><i />在线</span><span class="delayed"><i />降级</span><span class="offline"><i />离线</span><span class="unknown"><i />未知</span></div>
      <section class="ncp-target-section" data-test="tracked-targets" aria-label="场景目标">
        <h4>目标跟踪<span>{{ props.targets.length }} 个目标</span></h4>
        <p class="ncp-caption">{{ props.targetSource }} · 不计入通信节点</p>
        <ul v-if="props.targets.length" class="ncp-targets">
          <li v-for="target in props.targets" :key="target.id"><Crosshair :size="17" aria-hidden="true" /><span class="ncp-name"><strong>{{ target.id }}</strong><small>{{ target.label }}</small></span><span class="ncp-target-state">{{ targetState(target.state) }}</span></li>
        </ul>
        <p v-else class="ncp-empty">等待当前场景目标数据。</p>
      </section>
    </details>

  </div>
</template>

<style scoped>
.ncp-panels { display: flex; flex-direction: column; gap: 10px; min-width: 0; color: #d8efeb; }
.ncp-panel { min-width: 0; border: 1px solid #2e5358; border-radius: 7px; background: #0b2025; overflow: hidden; }
.ncp-head { display: flex; align-items: center; justify-content: space-between; gap: 7px; min-height: 46px; padding: 12px 10px; background: #10282c; cursor: pointer; list-style: none; }
.ncp-head::-webkit-details-marker { display: none; }.ncp-head:hover { background: #153237; }
.ncp-title, .ncp-meta { display: inline-flex; align-items: center; gap: 6px; }
.ncp-title { font-size: 12px; font-weight: 600; }.ncp-title > svg { color: #70dfcf; }
.ncp-meta { font-size: 10px; color: #9bbabf; white-space: nowrap; }.ncp-arrow { flex-shrink: 0; color: #88bfb8; transition: transform .15s; }
.ncp-panel[open] > .ncp-head { border-bottom: 1px solid #2b4b51; }.ncp-panel[open] > .ncp-head .ncp-arrow { transform: rotate(90deg); }
.ncp-caption { color: #84a9ae; font-size: 10px; line-height: 1.7; margin: 10px 10px 8px; }
.ncp-counts, .ncp-legend { display: flex; align-items: center; justify-content: space-between; gap: 5px; padding: 0 10px 10px; font-size: 10px; }
.ncp-counts > span, .ncp-legend > span, .ncp-state { display: inline-flex; align-items: center; gap: 4px; }
.ncp-counts b { font-weight: 600; color: #e1f4ef; }.online { --ncp-tone: #70dfcf; }.delayed { --ncp-tone: #f0c16c; }.offline { --ncp-tone: #ef8b85; }.unknown { --ncp-tone: #91a6af; }
.ncp-counts i, .ncp-state i, .ncp-legend i, .ncp-dot { flex: 0 0 6px; width: 6px; height: 6px; border-radius: 50%; background: var(--ncp-tone, #91a6af); }
.ncp-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 5px; padding: 8px 10px; font-size: 10px; color: #89a8af; border-block: 1px solid #28494e; }
.ncp-toolbar label { display: inline-flex; align-items: center; gap: 4px; color: #adccca; font-size: 10px; cursor: pointer; margin: 0; }
.ncp-toolbar input { width: 12px; height: 12px; padding: 0; margin: 0; accent-color: #70dfcf; }
.ncp-entry + .ncp-entry { border-top: 1px solid #27484d; }
.ncp-row { display: grid; grid-template-columns: 18px minmax(0, 1fr) auto 17px 11px; align-items: center; gap: 5px; width: 100%; min-height: 51px; padding: 10px 8px; border: 0; background: transparent; color: #b2cfd0; cursor: pointer; text-align: left; }
.ncp-row:hover, .ncp-row[aria-expanded=true] { background: #16343a; }.ncp-row[data-selected=true] { box-shadow: inset 2px 0 #70dfcf; }
.ncp-row > svg { justify-self: center; flex-shrink: 0; }.ncp-name { min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.ncp-name strong { font: 11px/1.4 Consolas, monospace; color: #e1f3f0; overflow-wrap: anywhere; }.ncp-name small { font-size: 10px; color: #8baeb4; }
.ncp-state { font-size: 10px; white-space: nowrap; color: var(--ncp-tone); }.ncp-bars { height: 17px; width: 17px; display: inline-flex; align-items: flex-end; gap: 2px; }
.ncp-bars i { width: 3px; background: #35545c; border-radius: 1px; }.ncp-bars i:nth-child(1) { height: 5px; }.ncp-bars i:nth-child(2) { height: 9px; }.ncp-bars i:nth-child(3) { height: 13px; }.ncp-bars i:nth-child(4) { height: 17px; }.ncp-bars .filled { background: var(--ncp-tone); }.ncp-bars.unavailable { opacity: .55; }
.ncp-row-arrow { color: #83b0b4; }.ncp-row[aria-expanded=true] .ncp-row-arrow { transform: rotate(90deg); }
.ncp-detail { padding: 11px; border-top: 1px solid #31535a; background: #081c22; }.ncp-device-name { margin: 0 0 12px; font-size: 11px; overflow-wrap: anywhere; }.ncp-device-name span { display: block; margin-top: 5px; color: #8eafb5; font-size: 10px; }
.ncp-detail dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 8px; margin: 0; }.ncp-detail dt { font-size: 10px; color: #84a5af; }.ncp-detail dd { margin: 4px 0 0; font-size: 12px; font-variant-numeric: tabular-nums; }.ncp-detail dd small { display: block; margin-top: 3px; color: #759aa5; font-size: 10px; }
.ncp-endpoint, .ncp-reason { font-size: 10px; line-height: 1.8; overflow-wrap: anywhere; margin: 12px 0 0; color: #8bb2b9; }.ncp-endpoint span { display: block; color: #bfdbd7; }.ncp-reason { border-top: 1px solid #28494f; padding-top: 9px; }
.ncp-legend { border-top: 1px solid #2b4c51; padding-block: 10px; color: #8faeb4; }.ncp-notice { margin: 8px 10px; font-size: 10px; color: #e1bc7a; line-height: 1.7; }.ncp-empty { padding: 12px; margin: 0; text-align: center; color: #8faeb4; font-size: 11px; line-height: 1.8; }
.ncp-target-section { border-top: 1px solid #2b4c51; }.ncp-target-section h4 { display: flex; align-items: center; justify-content: space-between; gap: 6px; margin: 12px 10px 0; font-size: 11px; font-weight: 500; }.ncp-target-section h4 span { color: #8eaeb3; font-size: 10px; font-weight: 400; }
.ncp-targets { list-style: none; padding: 0; margin: 0; }.ncp-targets li { display: flex; align-items: center; gap: 8px; padding: 11px 10px; border-top: 1px solid #27484e; }.ncp-targets .ncp-name { flex: 1; }.ncp-targets svg, .ncp-target-state { color: #d8b97d; }.ncp-target-state { font-size: 10px; max-width: 45%; overflow-wrap: anywhere; }
.ncp-head:focus-visible, .ncp-row:focus-visible { outline: 2px solid #70dfcf; outline-offset: -3px; }
.ncp-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
@media(prefers-reduced-motion: reduce) { .ncp-arrow { transition: none; } }
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Anchor, CirclePause, CircleStop, Navigation, PlaneLanding, PlaneTakeoff, Play, RotateCcw, ShieldAlert } from '@lucide/vue'
import type { Component } from 'vue'

import type { RuntimeCommandStatus, VehicleCommandType } from '@/api/runtimeControl'
import type { RuntimeNode } from '@/types/monitoring'
import type { VehicleQuickCommand } from './VehicleQuickControl.vue'
import VehicleGlyph from './VehicleGlyph.vue'

const props = withDefaults(defineProps<{
  devices: RuntimeNode[]
  selectedDeviceCode: string
  operationalStates?: Record<string, string | undefined>
  feedback?: Record<string, RuntimeCommandStatus | undefined>
  busy?: boolean
  disabledReason?: string
}>(), {
  operationalStates: () => ({}),
  feedback: () => ({}),
  busy: false,
  disabledReason: '',
})

const emit = defineEmits<{
  select: [deviceCode: string]
  command: [command: VehicleQuickCommand]
}>()

const vehicles = computed(() => props.devices.filter((device) => device.type === 'UAV' || device.type === 'USV'))
const selected = computed(() => vehicles.value.find((device) => device.code.toLowerCase() === props.selectedDeviceCode.toLowerCase()) ?? vehicles.value[0])
const state = computed(() => props.operationalStates[selected.value?.code.toLowerCase() ?? ''] ?? 'UNKNOWN')
const lastFeedback = computed(() => props.feedback[selected.value?.code.toLowerCase() ?? ''])
const freshnessClock = ref(Date.now())
let freshnessTimer: number | null = null
const stateLabel = computed(() => {
  const labels: Record<string, string> = {
    UNKNOWN: '等待遥测', GROUNDED: '地面待命', TAKING_OFF: '起飞中', AIRBORNE: '空中执行',
    HOLDING: '定点保持', RETURNING: '返航中', LANDING: '降落中', MOORED: '靠泊待命',
    DEPARTING: '离泊中', SAILING: '航行中', STOPPED: '已停止', ERROR: '异常',
  }
  return labels[state.value] ?? state.value
})

type Action = { label: string; commandType: VehicleCommandType; icon: Component; danger?: boolean }
const actions = computed<Action[]>(() => selected.value?.type === 'USV'
  ? [
      { label: '离泊', commandType: 'USV_DEPART', icon: Navigation },
      { label: state.value === 'HOLDING' ? '继续' : '定点保持', commandType: state.value === 'HOLDING' ? 'USV_RESUME' : 'USV_HOLD', icon: state.value === 'HOLDING' ? Play : Anchor },
      { label: '返航', commandType: 'USV_RETURN', icon: RotateCcw },
      { label: '停止推进', commandType: 'USV_STOP', icon: CircleStop },
      { label: '紧急停止', commandType: 'USV_EMERGENCY_STOP', icon: ShieldAlert, danger: true },
    ]
  : [
      { label: '起飞', commandType: 'UAV_TAKEOFF', icon: PlaneTakeoff },
      { label: state.value === 'HOLDING' ? '继续' : '悬停', commandType: state.value === 'HOLDING' ? 'UAV_RESUME' : 'UAV_HOVER', icon: state.value === 'HOLDING' ? Play : CirclePause },
      { label: '返航', commandType: 'UAV_RETURN', icon: RotateCcw },
      { label: '降落', commandType: 'UAV_LAND', icon: PlaneLanding },
      { label: '紧急降落', commandType: 'UAV_EMERGENCY_LAND', icon: ShieldAlert, danger: true },
    ])

function isActionAllowed(action: Action) {
  if (action.commandType !== 'UAV_TAKEOFF') return true
  freshnessClock.value
  const device = selected.value
  return device?.controlOperationalState === 'GROUNDED'
    && device.controlStateFresh === true
    && device.controlConnectionState === 'ONLINE'
}

function isUsvSafetyStop(action: Action) {
  return action.commandType === 'USV_STOP' || action.commandType === 'USV_EMERGENCY_STOP'
}

function send(action: Action) {
  if (!selected.value || !isActionAllowed(action)) return
  emit('command', { commandType: action.commandType, deviceCodes: [selected.value.code], label: action.label })
}

function formatPose(value: number | null) {
  return value === null ? '--' : value.toFixed(2)
}

onMounted(() => {
  freshnessTimer = window.setInterval(() => { freshnessClock.value = Date.now() }, 500)
})

onBeforeUnmount(() => {
  if (freshnessTimer !== null) window.clearInterval(freshnessTimer)
})
</script>

<template>
  <section class="unified-control" :class="selected?.type?.toLowerCase()">
    <header><h3>当前设备控制</h3><span class="connection-state" :class="{ online: selected?.status === 'ONLINE' }"><i></i>{{ selected?.status === 'ONLINE' ? '在线' : '离线' }}</span></header>
    <select :value="selected?.code" @change="emit('select', ($event.target as HTMLSelectElement).value)">
      <optgroup label="UAV">
        <option v-for="device in vehicles.filter(item => item.type === 'UAV')" :key="device.code" :value="device.code">{{ device.code.toUpperCase() }} · {{ device.name }}</option>
      </optgroup>
      <optgroup label="USV">
        <option v-for="device in vehicles.filter(item => item.type === 'USV')" :key="device.code" :value="device.code">{{ device.code.toUpperCase() }} · {{ device.name }}</option>
      </optgroup>
    </select>
    <div v-if="selected" class="device-summary">
      <VehicleGlyph :type="selected.type as 'UAV' | 'USV'" size="large" active />
      <div class="device-identity"><strong>{{ selected.code.toUpperCase() }}</strong><span>{{ selected.name }}</span></div>
      <b class="operational-state" :class="state.toLowerCase()">{{ stateLabel }}</b>
      <dl>
        <div><dt>位姿</dt><dd>{{ formatPose(selected.positionX) }}, {{ formatPose(selected.positionY) }}, {{ formatPose(selected.positionZ) }}</dd></div>
        <div><dt>心跳</dt><dd>{{ selected.lastHeartbeatAt ? `${selected.heartbeatAgeSeconds}s` : '--' }}</dd></div>
        <div><dt>速度 / 航向</dt><dd>-- / --</dd></div>
      </dl>
    </div>
    <p v-if="disabledReason" class="disabled-reason">{{ disabledReason }}</p>
    <div class="command-grid">
      <button v-for="action in actions" :key="action.commandType" type="button" :class="{ danger: action.danger }" :disabled="(busy && !isUsvSafetyStop(action)) || !!disabledReason || !selected || !isActionAllowed(action)" @click="send(action)">
        <component :is="action.icon" :size="21" /><span>{{ action.label }}</span>
      </button>
    </div>
    <footer>最近指令：{{ lastFeedback || '暂无反馈' }}</footer>
  </section>
</template>

<style scoped>
.unified-control{border:1px solid rgba(108,228,213,.3);border-radius:8px;padding:14px;background:linear-gradient(145deg,rgba(4,25,35,.96),rgba(2,15,23,.94));color:#d9f4f2}.unified-control.uav{border-color:rgba(255,196,45,.45)}.unified-control.usv{border-color:rgba(255,91,91,.45)}header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}h3{margin:0;font-size:18px}.connection-state{display:inline-flex;align-items:center;gap:6px;color:#82999e;font-size:12px}.connection-state i{width:7px;height:7px;border-radius:50%;background:currentColor;box-shadow:0 0 8px currentColor}.online{color:#55e7a7!important}select{width:100%;height:40px;border:1px solid #244c56;border-radius:6px;background:#071c25;color:#edfafa;padding:0 12px;font-size:14px}.device-summary{display:grid;grid-template-columns:64px minmax(0,1fr) auto;gap:12px;align-items:center;margin:12px 0;padding:14px;border:1px solid rgba(108,228,213,.16);border-radius:7px;background:linear-gradient(90deg,rgba(108,228,213,.035),transparent)}.device-identity strong,.device-identity span{display:block}.device-identity strong{font-size:19px;line-height:1.1;color:#efffff}.device-identity span{margin-top:6px;color:#8fb1b5;font-size:12px}.operational-state{align-self:start;padding:5px 8px;border:1px solid rgba(108,228,213,.22);border-radius:4px;color:#71ddd1;background:rgba(108,228,213,.06);font-size:11px;white-space:nowrap}.operational-state.error{color:#ff7474;border-color:rgba(255,116,116,.35)}dl{grid-column:1/-1;margin:2px 0 0;display:grid;gap:0}dl div{display:flex;justify-content:space-between;border-top:1px solid rgba(108,228,213,.09);padding:8px 0 2px}dt{color:#789aa0}dd{margin:0;color:#d8eeee;font-variant-numeric:tabular-nums}.command-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.command-grid button{min-height:65px;border:1px solid #29505a;border-radius:6px;background:#09202a;color:#b9d0d2;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px}.command-grid button:hover:not(:disabled){border-color:#6ce4d5;color:#6ce4d5}.command-grid .danger{border-color:rgba(255,91,91,.55);color:#ff7474}.command-grid button:disabled{opacity:.42}.disabled-reason{color:#ffca4b;font-size:12px}.unified-control footer{margin-top:11px;padding-top:10px;border-top:1px solid rgba(108,228,213,.12);color:#83a5aa;font-size:12px}
</style>

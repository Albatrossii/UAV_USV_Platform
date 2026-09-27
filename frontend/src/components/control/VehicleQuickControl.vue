<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Anchor,
  CirclePause,
  CircleStop,
  LocateFixed,
  Navigation,
  PlaneLanding,
  PlaneTakeoff,
  Play,
  RotateCcw,
  ShipWheel,
} from '@lucide/vue'

import type { RuntimeCommandStatus, RuntimeCommandType, VehicleCommandType } from '@/api/runtimeControl'
import VehicleGlyph from '@/components/control/VehicleGlyph.vue'
import { normalizeOperationalState } from '@/utils/runtimeOperationalState'

export type QuickControlDevice = {
  code: string
  name: string
  type: 'UAV' | 'USV'
  status?: string | null
  controlOperationalState?: string
  controlStateFresh?: boolean
  controlStateReceivedAt?: string | null
  controlConnectionState?: string
}

export type VehicleQuickCommand = {
  commandType: VehicleCommandType
  deviceCodes: string[]
  label: string
}

const props = withDefaults(
  defineProps<{
    vehicleType: 'UAV' | 'USV'
    devices: QuickControlDevice[]
    selectedDeviceCode?: string
    busy?: boolean
    feedback?: Record<string, RuntimeCommandStatus | undefined>
    operationalStates?: Record<string, string | undefined>
    compact?: boolean
  }>(),
  {
    selectedDeviceCode: '',
    busy: false,
    feedback: () => ({}),
    operationalStates: () => ({}),
    compact: false,
  },
)

const emit = defineEmits<{
  select: [deviceCode: string]
  command: [command: VehicleQuickCommand]
}>()

const groupMode = ref(false)

const typeDevices = computed(() => props.devices.filter((device) => device.type === props.vehicleType))
const selectedDevice = computed(
  () =>
    typeDevices.value.find((device) => normalizeCode(device.code) === normalizeCode(props.selectedDeviceCode)) ??
    typeDevices.value[0] ??
    null,
)

const controlledStates = computed(() => {
  const devices = groupMode.value ? typeDevices.value : selectedDevice.value ? [selectedDevice.value] : []
  return new Set(devices.map((device) => operationalState(device.code)))
})
const shouldResume = computed(() => controlledStates.value.has('HOLDING'))
const hasActiveDevices = computed(() =>
  props.vehicleType === 'UAV'
    ? controlledStates.value.has('AIRBORNE') || controlledStates.value.has('RETURNING')
    : controlledStates.value.has('SAILING') || controlledStates.value.has('RETURNING'),
)
const mixedHoldAndActive = computed(() => groupMode.value && shouldResume.value && hasActiveDevices.value)

type QuickAction = { label: string; commandType: VehicleCommandType; tone?: string; allowedStates: string[] }

const actions = computed<QuickAction[]>(() => {
  if (props.vehicleType === 'UAV') {
    const motionActions: QuickAction[] = mixedHoldAndActive.value
      ? [
          { label: '悬停', commandType: 'UAV_HOVER', allowedStates: ['AIRBORNE', 'RETURNING'] },
          { label: '继续任务', commandType: 'UAV_RESUME', tone: 'primary', allowedStates: ['HOLDING'] },
        ]
      : [
          shouldResume.value
            ? { label: '继续任务', commandType: 'UAV_RESUME', tone: 'primary', allowedStates: ['HOLDING'] }
            : { label: '悬停', commandType: 'UAV_HOVER', allowedStates: ['AIRBORNE', 'RETURNING'] },
        ]
    return [
      { label: '起飞', commandType: 'UAV_TAKEOFF', tone: 'primary', allowedStates: ['GROUNDED'] },
      ...motionActions,
      { label: '返航', commandType: 'UAV_RETURN', allowedStates: ['AIRBORNE', 'HOLDING'] },
      { label: '降落', commandType: 'UAV_LAND', tone: 'warning', allowedStates: ['AIRBORNE', 'HOLDING', 'RETURNING'] },
    ]
  }
  const motionActions: QuickAction[] = mixedHoldAndActive.value
    ? [
        { label: '定点保持', commandType: 'USV_HOLD', allowedStates: ['SAILING', 'RETURNING'] },
        { label: '继续航行', commandType: 'USV_RESUME', tone: 'primary', allowedStates: ['HOLDING'] },
      ]
    : [
        shouldResume.value
          ? { label: '继续航行', commandType: 'USV_RESUME', tone: 'primary', allowedStates: ['HOLDING'] }
          : { label: '定点保持', commandType: 'USV_HOLD', allowedStates: ['SAILING', 'RETURNING'] },
      ]
  return [
    { label: '离泊启动', commandType: 'USV_DEPART', tone: 'primary', allowedStates: ['MOORED', 'STOPPED'] },
    ...motionActions,
    { label: '返航', commandType: 'USV_RETURN', allowedStates: ['SAILING', 'HOLDING'] },
    { label: '停止推进', commandType: 'USV_STOP', tone: 'warning', allowedStates: ['DEPARTING', 'SAILING', 'HOLDING', 'RETURNING'] },
  ]
})

const title = computed(() => (props.vehicleType === 'UAV' ? '无人机快捷控制' : '无人艇快捷控制'))
const typeLabel = computed(() => (props.vehicleType === 'UAV' ? 'UAV' : 'USV'))
const selectedFeedback = computed(() => {
  const code = selectedDevice.value?.code
  return code ? props.feedback[normalizeCode(code)] : undefined
})
const operationalStateLabels: Record<string, string> = {
  UNKNOWN: '等待遥测',
  GROUNDED: '地面待命',
  TAKING_OFF: '起飞中',
  AIRBORNE: '空中执行',
  HOLDING: '安全保持',
  RETURNING: '返航中',
  LANDING: '降落中',
  MOORED: '靠泊待命',
  DEPARTING: '离泊中',
  SAILING: '航行中',
  STOPPED: '已停止',
  ERROR: '异常',
}

watch(
  typeDevices,
  (devices) => {
    if (!devices.length || selectedDevice.value) return
    emit('select', devices[0]!.code)
  },
  { immediate: true },
)

function normalizeCode(code: string) {
  return code.trim().toLowerCase()
}

function statusLabel(status?: string | null) {
  const labels: Record<string, string> = {
    ONLINE: '在线',
    OFFLINE: '离线',
    UNKNOWN: '等待遥测',
    MAINTENANCE: '维护中',
    PENDING: '等待下发',
    DISPATCHED: '等待确认',
    ACCEPTED: 'ROS 已接收',
    EXECUTING: 'ROS 执行中',
    SUCCEEDED: '执行成功',
    FAILED: '执行失败',
    TIMEOUT: '确认超时',
    CANCELLED: '已取消',
  }
  return status ? labels[status] ?? status : '等待遥测'
}

function operationalState(deviceCode: string) {
  return normalizeOperationalState(props.operationalStates[normalizeCode(deviceCode)], props.vehicleType)
}

function availableDevices(action: QuickAction) {
  const candidates = groupMode.value ? typeDevices.value : selectedDevice.value ? [selectedDevice.value] : []
  if (props.vehicleType === 'UAV' && action.commandType === 'UAV_TAKEOFF') {
    return candidates.filter(authoritativeTakeoffAllowed)
  }
  return candidates.filter((device) => action.allowedStates.includes(operationalState(device.code)))
}

function authoritativeTakeoffAllowed(device: QuickControlDevice) {
  return device.controlOperationalState === 'GROUNDED'
    && device.controlStateFresh === true
    && device.controlConnectionState === 'ONLINE'
}

function displayState() {
  const feedback = selectedFeedback.value
  if (feedback && feedback !== 'SUCCEEDED') return statusLabel(feedback)
  if (groupMode.value) {
    const states = new Set(typeDevices.value.map((device) => operationalState(device.code)))
    return states.size === 1 ? operationalStateLabels[[...states][0] ?? ''] ?? '编组待命' : '状态不一致'
  }
  const state = selectedDevice.value ? operationalState(selectedDevice.value.code) : ''
  return operationalStateLabels[state] ?? statusLabel(selectedDevice.value?.status)
}

function issue(action: QuickAction) {
  const deviceCodes = availableDevices(action).map((device) => device.code)
  if (!deviceCodes.length || (props.busy && !isUsvSafetyStop(action.commandType))) return
  emit('command', { commandType: action.commandType, deviceCodes, label: action.label })
}

function isUsvSafetyStop(commandType: RuntimeCommandType) {
  return commandType === 'USV_STOP' || commandType === 'USV_EMERGENCY_STOP'
}

const actionIcons: Partial<Record<RuntimeCommandType, typeof PlaneTakeoff>> = {
  UAV_TAKEOFF: PlaneTakeoff,
  UAV_HOVER: LocateFixed,
  UAV_RESUME: Play,
  UAV_RETURN: RotateCcw,
  UAV_LAND: PlaneLanding,
  USV_DEPART: Navigation,
  USV_HOLD: Anchor,
  USV_RESUME: ShipWheel,
  USV_RETURN: RotateCcw,
  USV_STOP: CircleStop,
}

function actionIcon(commandType: RuntimeCommandType) {
  return actionIcons[commandType] ?? CirclePause
}
</script>

<template>
  <article class="vehicle-quick-control" :class="[vehicleType.toLowerCase(), { compact }]">
    <header>
      <div class="control-identity">
        <VehicleGlyph :type="vehicleType" size="small" :active="!busy" />
        <div>
          <span>{{ typeLabel }}</span>
          <strong>{{ title }}</strong>
        </div>
      </div>
      <button type="button" class="group-toggle" :class="{ active: groupMode }" :disabled="busy" @click="groupMode = !groupMode">
        {{ groupMode ? '编组控制' : '单机控制' }}
      </button>
    </header>

    <div class="device-chips">
      <button
        v-for="device in typeDevices"
        :key="device.code"
        type="button"
        :class="{ active: normalizeCode(device.code) === normalizeCode(selectedDevice?.code || '') }"
        :disabled="busy"
        @click="emit('select', device.code)"
      >
        <VehicleGlyph :type="vehicleType" size="small" :active="normalizeCode(device.code) === normalizeCode(selectedDevice?.code || '')" />
        <span>{{ device.code.toUpperCase() }}</span>
      </button>
    </div>

    <div class="selected-state">
      <span>{{ groupMode ? `${typeDevices.length} 台${vehicleType === 'UAV' ? '无人机' : '无人艇'}` : selectedDevice?.name || '未选择设备' }}</span>
      <b :class="(selectedFeedback || selectedDevice?.status || 'unknown').toLowerCase()">
        {{ displayState() }}
      </b>
    </div>

    <div class="command-grid">
      <button
        v-for="action in actions"
        :key="action.commandType"
        type="button"
        :class="action.tone"
        :disabled="(busy && !isUsvSafetyStop(action.commandType)) || availableDevices(action).length === 0"
        @click="issue(action)"
      >
        <component :is="actionIcon(action.commandType)" class="command-icon" :stroke-width="1.8" />
        <span>{{ action.label }}</span>
        <small>{{ groupMode ? `${availableDevices(action).length}/${typeDevices.length} 台可执行` : selectedDevice?.code.toUpperCase() }}</small>
      </button>
    </div>
  </article>
</template>

<style scoped>
.vehicle-quick-control {
  --accent: #ffca3a;
  --accent-rgb: 255, 202, 58;
  padding: 14px;
  background: linear-gradient(145deg, rgba(7, 24, 31, 0.97), rgba(8, 18, 26, 0.94));
  border: 1px solid rgba(var(--accent-rgb), 0.28);
  border-radius: 8px;
  box-shadow: inset 0 0 28px rgba(var(--accent-rgb), 0.035);
  position: relative;
  overflow: hidden;
}

.vehicle-quick-control::before {
  position: absolute;
  top: 0;
  left: 0;
  width: 72px;
  height: 2px;
  content: '';
  background: linear-gradient(90deg, var(--accent), transparent);
  box-shadow: 0 0 12px rgba(var(--accent-rgb), 0.55);
}

.vehicle-quick-control.usv {
  --accent: #ff665e;
  --accent-rgb: 255, 102, 94;
}

header,
.selected-state {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

header span,
header strong {
  display: block;
}

.control-identity {
  display: flex;
  align-items: center;
  gap: 9px;
}

header span {
  color: var(--accent);
  font-size: 10px;
  font-weight: 900;
  letter-spacing: 0.18em;
}

header strong {
  margin-top: 2px;
  color: #f2fffd;
  font-size: 15px;
}

.group-toggle,
.device-chips button,
.command-grid button {
  color: #dff8f4;
  font: inherit;
  cursor: pointer;
  background: rgba(255, 255, 255, 0.025);
  border: 1px solid rgba(150, 205, 203, 0.2);
  border-radius: 5px;
}

.group-toggle {
  min-height: 28px;
  padding: 0 9px;
  font-size: 10px;
  font-weight: 800;
}

.group-toggle.active,
.device-chips button.active {
  color: var(--accent);
  background: rgba(var(--accent-rgb), 0.14);
  border-color: rgba(var(--accent-rgb), 0.58);
}

.device-chips {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 6px;
  margin-top: 12px;
}

.device-chips button {
  min-height: 31px;
  color: #b8cfcd;
  font-size: 11px;
  font-weight: 900;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
}

.device-chips :deep(.vehicle-glyph) {
  width: 22px;
  height: 22px;
  border: 0;
  background: transparent;
  box-shadow: none;
}

.selected-state {
  min-height: 32px;
  margin-top: 8px;
  color: #89aaa8;
  font-size: 11px;
}

.selected-state b {
  color: #7ce5ab;
}

.selected-state b.failed,
.selected-state b.timeout,
.selected-state b.offline {
  color: #ff746f;
}

.selected-state b.dispatched,
.selected-state b.pending,
.selected-state b.unknown {
  color: #ffd76e;
}

.command-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 7px;
}

.command-grid button {
  min-height: 72px;
  padding: 8px 4px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  text-align: center;
  position: relative;
  overflow: hidden;
  transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease;
}

.command-icon {
  width: 26px;
  height: 26px;
  color: var(--accent);
  filter: drop-shadow(0 0 5px rgba(var(--accent-rgb), 0.45));
}

.command-grid button:hover:not(:disabled),
.command-grid button.primary {
  color: var(--accent);
  background: rgba(var(--accent-rgb), 0.12);
  border-color: rgba(var(--accent-rgb), 0.58);
}

.command-grid button:hover:not(:disabled) {
  color: #061113;
  background: var(--accent);
  border-color: var(--accent);
}

.command-grid button:hover:not(:disabled) .command-icon { color: #071317; filter: none; }

.command-grid button:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 7px 18px rgba(var(--accent-rgb), 0.18);
}

.command-grid button.warning {
  border-color: rgba(255, 112, 105, 0.52);
}

.command-grid button:disabled {
  cursor: not-allowed;
  color: #628083;
  background: rgba(255, 255, 255, 0.018);
  border-color: rgba(125, 171, 174, 0.13);
  opacity: 0.62;
  box-shadow: none;
}

.command-grid button:disabled .command-icon {
  color: #557174;
  filter: none;
}

.command-grid span,
.command-grid small {
  display: block;
}

.command-grid span {
  font-size: 11px;
  font-weight: 900;
}

.command-grid small {
  margin-top: 1px;
  font-size: 8px;
  opacity: 0.68;
}

.compact {
  padding: 11px;
}

.compact .command-grid button {
  min-height: 62px;
}

@media (max-width: 1280px) {
  .command-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>

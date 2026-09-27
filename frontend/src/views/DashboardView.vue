<script setup lang="ts">
import {
  Anchor,
  CircleStop,
  LocateFixed,
  Navigation,
  PlaneLanding,
  PlaneTakeoff,
  Play,
  RotateCcw,
  ShieldAlert,
} from '@lucide/vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'

import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import VehicleGlyph from '@/components/control/VehicleGlyph.vue'
import type { VehicleQuickCommand } from '@/components/control/VehicleQuickControl.vue'
import { controlAlgorithmRun, fetchAlgorithms, prepareAlgorithmRun } from '@/api/algorithm'
import { executeMissionAction, fetchMission, fetchMissions } from '@/api/mission'
import type { MissionAction } from '@/api/mission'
import { issueRuntimeCommand } from '@/api/runtimeControl'
import type { RuntimeCommandStatus, RuntimeCommandType } from '@/api/runtimeControl'
import { issueSingleDeviceCommand } from '@/services/singleDeviceControl'
import { useMonitoringStore } from '@/stores/monitoring'
import { useRealtimeStore } from '@/stores/realtime'
import { useActiveExperimentStore } from '@/stores/activeExperiment'
import { useRealMissionRuntimeStore } from '@/stores/realMissionRuntime'
import { useTrajectoryStore } from '@/stores/trajectory'
import { useUnityBridgeStore } from '@/stores/unityBridge'
import { useUnityViewportStore } from '@/stores/unityViewport'
import {
  buildSystemOverviewUnityPoses,
  isPoseBatchLive,
  poseBatchToTrajectoryPayload,
  poseBatchTimestampMs,
  systemOverviewPoseFrameKey,
} from '@/services/realtimeTrajectoryAdapter'
import type { RuntimeNode } from '@/types/monitoring'
import { normalizeOperationalState } from '@/utils/runtimeOperationalState'
import { resolveActiveRun } from '@/utils/missionRun'
import type { AlgorithmDefinition, MissionDetail, MissionStatus } from '@/types/mission'

type UnityMessage = {
  type: string
  requestId?: string
  timestamp?: number
  payload?: Record<string, unknown>
}

type OverviewRuntimeMode = 'VIRTUAL_SIMULATION' | 'REAL'

class PostMissionAlgorithmRuntimeError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'PostMissionAlgorithmRuntimeError'
  }
}

const monitoringStore = useMonitoringStore()
const realtimeStore = useRealtimeStore()
const activeExperimentStore = useActiveExperimentStore()
const realMissionRuntimeStore = useRealMissionRuntimeStore()
const overviewAlgorithmFallbacks: AlgorithmDefinition[] = [
  {
    id: -1,
    code: 'GB_SFLA_CS',
    name: 'GB-SFLA-CS 协同围捕',
    version: 'v1.1.0',
    missionType: 'COOPERATIVE_ENCIRCLEMENT',
    adapterType: 'PYTHON_PROCESS',
    deviceScale: '3 UAV + 3 USV + 1 Target',
    enabled: true,
    defaultForType: true,
    description: '三机三艇协同围捕算法',
  },
  {
    id: -2,
    code: 'ESCORT_GUARD',
    name: 'SeaShield-ACG 海空异构集群智能护航围控',
    version: 'v1.1.0',
    missionType: 'COOPERATIVE_ESCORT',
    adapterType: 'PYTHON_PROCESS',
    deviceScale: '3 UAV + 3 USV + 1 Escort Target',
    enabled: true,
    defaultForType: true,
    description: '海空异构集群意图识别、协同拦截与智能围控算法',
  },
]
const overviewAlgorithms = ref<AlgorithmDefinition[]>([...overviewAlgorithmFallbacks])
const selectedOverviewAlgorithm = ref('GB_SFLA_CS')
const overviewRuntimeMode = ref<OverviewRuntimeMode>('REAL')
const virtualUavCount = ref(3)
const virtualUsvCount = ref(3)
const virtualScenarioRunId = ref(Date.now())
const enabledOverviewAlgorithms = computed(() => overviewAlgorithms.value.filter(item => item.enabled && ['GB_SFLA_CS','ESCORT_GUARD'].includes(item.code)))
const trajectoryStore = useTrajectoryStore()
const unityBridgeStore = useUnityBridgeStore()
const unityViewportStore = useUnityViewportStore()
const selectedDeviceCode = ref('')
const selectedCameraMode = ref('overview')
const unityConnection = ref('等待 WebGL 构建')
const lastUnityEvent = ref('暂无 Unity 回传事件')
const unityCommandState = ref('等待控制指令')
const commandBusy = ref(false)
const cameraCommandBusy = ref(false)
const cameraZoomPercent = ref(100)
const cameraToolsVisible = ref(false)
const dashboardViewActive = ref(true)
let selectedDeviceSyncedToUnity = ''
let overviewCameraInitialized = false
let lastOverviewRealtimePoseFrameKey = ''
const commandFeedback = ref<Record<string, RuntimeCommandStatus | undefined>>({})
const operationalStates = ref<Record<string, string | undefined>>({
  'uav-01': 'UNKNOWN',
  'uav-02': 'UNKNOWN',
  'uav-03': 'UNKNOWN',
  'usv-01': 'UNKNOWN',
  'usv-02': 'UNKNOWN',
  'usv-03': 'UNKNOWN',
})
const overviewMissionDevices = ref<Array<{ code: string; name: string; type: 'UAV' | 'USV'; status?: string | null }>>([])
const freshnessClock = ref(Date.now())
const overviewMissionId = ref<number | null>(null)
const overviewMissionName = ref('三机三艇协同围捕')
const overviewMissionStatus = ref<MissionStatus>('READY')
const overviewActiveMissionRunId = ref<number | null>(null)
const overviewMissionControlSource = ref('UNKNOWN')
const overviewDeploymentAcknowledged = ref(false)
let poseFrameSequence = 0
let unityPoseRunId = ''
let freshnessTimer: number | null = null
const unityInstanceId = 'overview-unity-01'
let lastRealOverviewScenarioKey = ''

// SYSTEM_REALTIME runtime correlation ID v1: deterministic 31-bit FNV-1a
// over the local Unity instance and mission. It never leaves this Unity channel.
function deriveUnityRuntimeId(missionId: number, runtimeInstanceId = unityInstanceId) {
  const input = `SYSTEM_REALTIME:v1:${runtimeInstanceId}:${missionId}`
  let hash = 0x811c9dc5
  for (let index = 0; index < input.length; index += 1) {
    hash = Math.imul(hash ^ input.charCodeAt(index), 0x01000193)
  }
  return (hash >>> 0) % 0x7ffffffe + 1
}

function currentOverviewRealRunId(missionId = overviewMissionId.value) {
  if (overviewActiveMissionRunId.value) return overviewActiveMissionRunId.value
  if (realMissionRuntimeStore.currentRunId) return realMissionRuntimeStore.currentRunId
  if (!missionId) return null
  return deriveUnityRuntimeId(missionId)
}

function currentOverviewUnityPoseRunId() {
  return overviewRuntimeMode.value === 'VIRTUAL_SIMULATION'
    ? virtualScenarioRunId.value
    : currentOverviewRealRunId()
}

function nextUnityPoseSequence(runtimeId: number) {
  const wireRunId = String(runtimeId)
  if (unityPoseRunId !== wireRunId) {
    unityPoseRunId = wireRunId
    poseFrameSequence = 0
  }
  return ++poseFrameSequence
}

const cameraModes = [
  { label: '全局态势', value: 'overview' },
  { label: '设备跟随', value: 'device-follow' },
]

let trajectoryToggleTimer: number | null = null
let cameraToolsTimer: number | null = null

function revealCameraTools() {
  cameraToolsVisible.value = true
  if (cameraToolsTimer !== null) window.clearTimeout(cameraToolsTimer)
  cameraToolsTimer = window.setTimeout(() => {
    cameraToolsVisible.value = false
    cameraToolsTimer = null
  }, 3200)
}

function sendCameraControl(action: 'fitAll' | 'setZoom', value = 0) {
  if (!unityCameraReady.value) return
  unityBridgeStore.sendFor('SYSTEM_OVERVIEW', 'cameraControl', { action, value })
}

function fitUnityOverview() {
  cameraZoomPercent.value = 100
  selectedCameraMode.value = 'overview'
  revealCameraTools()
  sendCameraControl('fitAll')
}

function setUnityZoom(event: Event) {
  const target = event.target as HTMLInputElement
  const value = Number(target.value)
  if (!Number.isFinite(value)) return
  cameraZoomPercent.value = value
  revealCameraTools()
  sendCameraControl('setZoom', value)
}

const expectedObservationDevices = [
  { code: 'uav-01', name: '协同无人机 1', type: 'UAV' as const },
  { code: 'uav-02', name: '协同无人机 2', type: 'UAV' as const },
  { code: 'uav-03', name: '协同无人机 3', type: 'UAV' as const },
  { code: 'usv-01', name: '协同无人艇 1', type: 'USV' as const },
  { code: 'usv-02', name: '协同无人艇 2', type: 'USV' as const },
  { code: 'usv-03', name: '协同无人艇 3', type: 'USV' as const },
]

function normalizeDeviceCode(code: string) {
  return code.trim().toLowerCase()
}

function hasRuntimePosition(node: RuntimeNode) {
  return node.positionX !== null && node.positionY !== null && node.positionZ !== null
}

const runtimeNodeByCode = computed(() => {
  const map = new Map<string, RuntimeNode>()
  monitoringStore.nodes.forEach((node) => map.set(normalizeDeviceCode(node.code), node))
  return map
})

const displayedNodes = computed(() =>
  monitoringStore.nodes.filter((node) => ['UAV', 'USV', 'ROS_NODE', 'UNITY_NODE', 'LIGHTHOUSE'].includes(node.type)),
)

const selectableDevices = computed(() => {
  return expectedObservationDevices.map((expected, index) => {
    const runtime = runtimeNodeByCode.value.get(normalizeDeviceCode(expected.code))
    if (runtime) return runtime
    return {
      id: -(index + 1),
      code: expected.code,
      name: expected.name,
      type: expected.type,
      status: 'UNKNOWN',
      host: null,
      port: null,
      endpoint: '',
      rosNamespace: null,
      lastHeartbeatAt: null,
      heartbeatAgeSeconds: -1,
      source: 'UNITY_SCENE',
      instanceId: null,
      positionX: null,
      positionY: null,
      positionZ: null,
      latitude: null,
      longitude: null,
      batteryLevel: null,
      linkQualityPercent: null,
      telemetryAt: null,
      telemetrySource: null,
      telemetryStale: true,
      detail: 'Unity 场景可观察，等待实时遥测',
    } as RuntimeNode
  })
})

const quickControlDevices = computed(() => {
  if (overviewMissionDevices.value.length) return overviewMissionDevices.value
  return selectableDevices.value.map((device) => ({
    code: device.code,
    name: device.name,
    type: device.type as 'UAV' | 'USV',
    status: device.status,
  }))
})
const overviewUnityChannel = computed(() => unityBridgeStore.channels.SYSTEM_OVERVIEW)
const unityCameraReady = computed(() =>
  overviewUnityChannel.value.connected
  && overviewUnityChannel.value.platformReady
  && overviewUnityChannel.value.cameraReady,
)
const unityControlReady = computed(() =>
  overviewUnityChannel.value.connected
  && overviewUnityChannel.value.platformReady,
)

function selectedOrDefaultDeviceCode(preferredType?: 'UAV' | 'USV') {
  const devices = selectableDevices.value.filter((device) => !preferredType || device.type === preferredType)
  const selected = devices.find(
    (device) => normalizeDeviceCode(device.code) === normalizeDeviceCode(selectedDeviceCode.value),
  )
  const online = devices.find((device) => device.status === 'ONLINE')
  const fallback = selected ?? online ?? devices[0] ?? selectableDevices.value[0]
  const code = normalizeDeviceCode(fallback?.code ?? (preferredType === 'USV' ? 'usv-01' : 'uav-01'))
  selectedDeviceCode.value = code
  return code
}

function ensureOverviewCamera(force = false) {
  const deviceCode = selectedOrDefaultDeviceCode()
  if (!unityCameraReady.value) return
  if (!force && overviewCameraInitialized) return
  overviewCameraInitialized = true
  selectedCameraMode.value = 'overview'
  cameraZoomPercent.value = 100
  selectedDeviceSyncedToUnity = ''
  unityBridgeStore.send('switchCamera', { mode: 'overview', deviceCode })
  lastUnityEvent.value = 'switchCamera:overview:initial'
}

const realtimeVehicles = computed(() => realtimeStore.poseBatch?.payload.vehicles ?? [])
const rosBridgeOnline = computed(() => realtimeStore.connected && isPoseBatchLive(realtimeStore.poseBatch, freshnessClock.value))
const unityReady = computed(() => unityConnection.value.includes('Unity WebGL 已连接'))
const realtimePoseCount = computed(
  () => rosBridgeOnline.value ? realtimeVehicles.value.filter(
    vehicle => vehicle.fresh !== false && vehicle.positionValid !== false && vehicle.localPositionEnuM,
  ).length : 0,
)
const onlineNodeCount = computed(() => displayedNodes.value.filter((node) => node.status === 'ONLINE').length)
const onlineVehicleCount = computed(() => realtimePoseCount.value)
const taskStateText = computed(() => {
  if (!unityReady.value) return '等待 Unity'
  if (rosBridgeOnline.value && realtimePoseCount.value > 0) return '实时同步'
  if (rosBridgeOnline.value) return '等待位姿'
  return '演示预览'
})
const trajectoryLive = computed(() => {
  freshnessClock.value
  const frame = trajectoryStore.frame
  return unityBridgeStore.connected && !!frame && Date.now() - frame.receivedAt <= 3000
})
const fleetReady = computed(() =>
  quickControlDevices.value.length > 0 && quickControlDevices.value.every(({ code }) => {
    const state = operationalStates.value[normalizeDeviceCode(code)]
    return code.startsWith('uav')
      ? ['AIRBORNE', 'HOLDING'].includes(normalizeOperationalState(state, 'UAV'))
      : ['SAILING', 'HOLDING'].includes(normalizeOperationalState(state, 'USV'))
  }),
)
const readyDeviceCount = computed(() =>
  quickControlDevices.value.filter(({ code }) => {
    const state = operationalStates.value[normalizeDeviceCode(code)]
    return code.startsWith('uav')
      ? ['AIRBORNE', 'HOLDING'].includes(normalizeOperationalState(state, 'UAV'))
      : ['SAILING', 'HOLDING'].includes(normalizeOperationalState(state, 'USV'))
  }).length,
)
const missionGroupProgress = computed(() => {
  if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(overviewMissionStatus.value)) return 0
  if (overviewMissionStatus.value === 'RUNNING' || overviewMissionStatus.value === 'PAUSED') return 72
  return Math.round((readyDeviceCount.value / Math.max(1, quickControlDevices.value.length)) * 48)
})
const missionReadinessText = computed(() =>
  overviewMissionStatus.value === 'RUNNING'
    ? '简单围捕任务执行中'
    : overviewDeploymentAcknowledged.value
      ? `${quickControlDevices.value.length}/${quickControlDevices.value.length} 载具已完成编组部署`
      : `等待部署 ${quickControlDevices.value.length} 台围捕载具`,
)

const overviewFleetCards = computed(() =>
  selectableDevices.value.map((device) => {
    const code = normalizeDeviceCode(device.code)
    const state = normalizeOperationalState(operationalStates.value[code], device.type === 'UAV' ? 'UAV' : 'USV')
    const labels: Record<string, string> = {
      UNKNOWN: '等待遥测', GROUNDED: '地面待命', TAKING_OFF: '起飞中', AIRBORNE: '空中执行',
      HOLDING: '安全保持', RETURNING: '返航中', LANDING: '降落中', MOORED: '靠泊待命',
      DEPARTING: '离泊中', SAILING: '航行中', STOPPED: '已停止', ERROR: '异常',
    }
    return {
      ...device,
      code,
      state,
      stateLabel: labels[state] ?? state,
      feedback: commandFeedback.value[code],
      batteryPercent: clampPercent(device.batteryLevel),
      linkQuality: clampPercent(device.linkQualityPercent),
      hasGeoCoordinate: device.latitude !== null && device.longitude !== null,
    }
  }),
)

type OverviewQuickAction = {
  label: string
  commandType: VehicleQuickCommand['commandType']
  icon: typeof PlaneTakeoff
  danger?: boolean
}

const selectedOverviewDevice = computed(() =>
  overviewFleetCards.value.find((device) => device.code === normalizeDeviceCode(selectedDeviceCode.value))
  ?? overviewFleetCards.value[0],
)
const overviewRuntimeState = computed(() => realMissionRuntimeStore.runtimeState)
const overviewMissionRunning = computed(() => realMissionRuntimeStore.isRunning)
const overviewMissionTerminal = computed(() => realMissionRuntimeStore.isTerminal)
const overviewMissionCanPrepare = computed(() =>
  overviewMissionId.value !== null && overviewMissionStatus.value === 'DRAFT',
)
const overviewRosMissionPhase = computed(() => {
  const payload = realtimeStore.missionStatus?.payload
  return String(payload?.phase ?? payload?.state ?? '').trim().toUpperCase()
})
const overviewRuntimeModeLabel = computed(() =>
  overviewRuntimeMode.value === 'VIRTUAL_SIMULATION' ? '虚拟仿真' : '真实任务',
)
const overviewMissionActionLabel = computed(() =>
  overviewMissionCanPrepare.value
    ? '进入待执行'
    : overviewRuntimeState.value === 'STARTING'
    ? '启动中'
    : overviewRuntimeState.value === 'CANCELLING'
      ? '终止中'
      : overviewMissionTerminal.value
        ? '再次执行'
        : overviewMissionRunning.value ? '终止任务' : '启动任务',
)
const overviewMissionStateText = computed(() => {
  const labels: Record<string, string> = {
    IDLE: '任务未启动',
    READY: '任务待执行',
    STARTING: '启动命令已确认',
    RUNNING: '围捕执行中',
    CANCELLING: '终止中',
    CANCELLED: '任务已终止',
    FAILED: '任务失败',
    COMPLETED: '任务完成',
  }
  return labels[overviewRuntimeState.value] ?? overviewRuntimeState.value
})
const overviewMissionActionDisabled = computed(() => {
  if (commandBusy.value) return true
  if (overviewRuntimeState.value === 'STARTING' || overviewRuntimeState.value === 'CANCELLING') return true
  if (overviewMissionCanPrepare.value) return false
  if (overviewRuntimeMode.value === 'REAL' && overviewRuntimeState.value === 'IDLE' && realValidationReady.value) return false
  if (realMissionRuntimeStore.canRetry) return false
  return !unityControlReady.value || (!realMissionRuntimeStore.canStart && !realMissionRuntimeStore.canCancel)
})

const realValidationReady = computed(() =>
  rosBridgeOnline.value
  && unityControlReady.value
  && realtimePoseCount.value >= 6,
)

function sendVirtualScenario(algorithmCode = selectedOverviewAlgorithm.value) {
  virtualScenarioRunId.value = Date.now()
  return unityBridgeStore.sendFor('SYSTEM_OVERVIEW', 'loadScenario', {
    runtimeMode: 'VIRTUAL_SIMULATION',
    runId: virtualScenarioRunId.value,
    algorithmCode,
    uavCount: virtualUavCount.value,
    usvCount: virtualUsvCount.value,
    targetCount: 1,
    initialSpeedMps: 2,
    initialHeadingDeg: 0,
  })
}

async function selectOverviewAlgorithm(code: string) {
  if (overviewMissionRunning.value) return
  selectedOverviewAlgorithm.value = code
  overviewMissionName.value = overviewAlgorithms.value.find(item => item.code === code)?.name ?? code
  overviewDeploymentAcknowledged.value = false
  if (overviewRuntimeMode.value === 'VIRTUAL_SIMULATION') sendVirtualScenario(code)
}

const overviewSelectedAlgorithm = computed(() =>
  enabledOverviewAlgorithms.value.find(algorithm => algorithm.code === selectedOverviewAlgorithm.value),
)
const selectedOverviewActions = computed<OverviewQuickAction[]>(() => {
  if (selectedOverviewDevice.value?.type === 'USV') {
    return [
      { label: '离泊启动', commandType: 'USV_DEPART', icon: Navigation },
      { label: '定点保持', commandType: 'USV_HOLD', icon: Anchor },
      { label: '返航', commandType: 'USV_RETURN', icon: RotateCcw },
      { label: '停止推进', commandType: 'USV_STOP', icon: CircleStop },
      { label: '紧急停止', commandType: 'USV_EMERGENCY_STOP', icon: ShieldAlert, danger: true },
    ]
  }
  return [
    { label: '起飞', commandType: 'UAV_TAKEOFF', icon: PlaneTakeoff },
    { label: '悬停', commandType: 'UAV_HOVER', icon: LocateFixed },
    { label: '返航', commandType: 'UAV_RETURN', icon: RotateCcw },
    { label: '降落', commandType: 'UAV_LAND', icon: PlaneLanding },
    { label: '紧急降落', commandType: 'UAV_EMERGENCY_LAND', icon: ShieldAlert, danger: true },
  ]
})

function authoritativeUavControlState(device: RuntimeNode | undefined) {
  freshnessClock.value
  const receivedAt = device?.controlStateReceivedAt ? Date.parse(device.controlStateReceivedAt) : 0
  if (device?.controlStateFresh !== true
    || device.controlConnectionState !== 'ONLINE'
    || receivedAt <= 0
    || Date.now() - receivedAt > 2000) return 'UNKNOWN'
  return device.controlOperationalState ?? 'UNKNOWN'
}

function isSelectedOverviewActionAllowed(action: OverviewQuickAction) {
  if (action.commandType !== 'UAV_TAKEOFF') return true
  return true
}

function isUsvSafetyStop(commandType: RuntimeCommandType) {
  return commandType === 'USV_STOP' || commandType === 'USV_EMERGENCY_STOP'
}

function clampPercent(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return null
  return Math.min(100, Math.max(0, Math.round(value)))
}

function formatCoordinate(value: number | null | undefined) {
  return value === null || value === undefined || !Number.isFinite(value) ? '--' : value.toFixed(6)
}

function formatLocalPosition(value: number | null | undefined) {
  return value === null || value === undefined || !Number.isFinite(value) ? '--' : `${value.toFixed(1)}m`
}

function runtimeStatusLabel(status?: string | null) {
  const labels: Record<string, string> = {
    ONLINE: '在线',
    OFFLINE: '离线',
    UNKNOWN: '等待遥测',
    MAINTENANCE: '维护中',
  }
  return labels[status ?? 'UNKNOWN'] ?? status ?? '等待遥测'
}

function telemetryTooltip(device: RuntimeNode) {
  if (!device.telemetryAt) return '尚未收到设备遥测'
  const source = device.telemetrySource || device.source || 'UNKNOWN'
  const timestamp = new Date(device.telemetryAt).toLocaleString('zh-CN', { hour12: false })
  return `${device.telemetryStale ? '遥测已超时' : '实时遥测'} · ${source} · ${timestamp}`
}

function linkBarActive(value: number | null, index: number) {
  if (value === null) return false
  return value >= index * 25
}

async function issueSelectedQuickCommand(action: OverviewQuickAction) {
  const device = selectedOverviewDevice.value
  if (!device) {
    ElMessage.error('没有可控制的设备')
    return
  }
  if (!isSelectedOverviewActionAllowed(action)) return
  await sendVehicleCommand({
    commandType: action.commandType,
    deviceCodes: [device.code],
    label: action.label,
  })
}

async function handleOverviewMissionToggle() {
  if (overviewMissionCanPrepare.value) {
    await retryOverviewMission()
    return
  }
  if (overviewRuntimeMode.value === 'REAL' && overviewRuntimeState.value === 'IDLE') {
    const current = await loadOverviewMission()
    if (!current) {
      ElMessage.error('未找到可执行的真实任务')
      return
    }
    if (overviewMissionStatus.value === 'DRAFT') {
      await retryOverviewMission()
      return
    }
    if (overviewMissionStatus.value === 'READY') {
      await handleMissionGroupAction('start')
      return
    }
    if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(overviewMissionStatus.value)) {
      await retryOverviewMission()
      return
    }
    ElMessage.error(`当前真实任务状态为 ${overviewMissionStatus.value}，不能启动`)
    return
  }
  if (realMissionRuntimeStore.canRetry) {
    await retryOverviewMission()
    return
  }
  if (realMissionRuntimeStore.canCancel) {
    await handleMissionGroupAction('abort')
    return
  }
  if (realMissionRuntimeStore.canStart) {
    await handleMissionGroupAction('start')
  }
}

function operationalStateAfterCommand(commandType: RuntimeCommandType) {
  const states: Partial<Record<RuntimeCommandType, string>> = {
    USV_DEPART: 'SAILING', USV_HOLD: 'HOLDING', USV_RESUME: 'SAILING', USV_RETURN: 'RETURNING', USV_STOP: 'STOPPED',
  }
  return states[commandType]
}

function toUnityPose(node: RuntimeNode) {
  return {
    deviceCode: node.code,
    position: [node.positionX!, node.positionY!, node.positionZ!],
  }
}

function pushPoseFrameToUnity() {
  if (isPoseBatchLive(realtimeStore.poseBatch)) return
  const poses = monitoringStore.nodes
    .filter((node) => ['UAV', 'USV', 'LIGHTHOUSE'].includes(node.type))
    .filter((node) => node.status === 'ONLINE')
    .filter(hasRuntimePosition)
    .map(toUnityPose)

  if (poses.length === 0) return

  const runId = currentOverviewUnityPoseRunId()
  if (!runId) return
  unityBridgeStore.send('poseFrame', {
    runId: String(runId),
    sequence: nextUnityPoseSequence(runId),
    timestamp: Date.now(),
    source: 'vue-ros',
    // Current runtime positions are already in the shared display map ENU.
    coordinateSystem: 'ROS_ENU',
    poses,
  })
}

function pushRealtimePoseFrameToUnity() {
  if (!isPoseBatchLive(realtimeStore.poseBatch)) {
    pushPoseFrameToUnity()
    return
  }
  const realtimePoseBatch = realtimeStore.poseBatch
  if (!realtimePoseBatch) return
  const runtimeId = currentOverviewRealRunId()
  if (!runtimeId) return
  const payload = poseBatchToTrajectoryPayload(realtimePoseBatch, {
    missionId: realMissionRuntimeStore.currentMissionId,
    runId: runtimeId,
    phase: overviewRosMissionPhase.value,
  })
  if (!payload) return
  const frameKey = systemOverviewPoseFrameKey(runtimeId, realtimePoseBatch, realtimeStore.targetBatch)
  if (frameKey === lastOverviewRealtimePoseFrameKey) return
  lastOverviewRealtimePoseFrameKey = frameKey
  trajectoryStore.ingestFor('SYSTEM_OVERVIEW', payload)
  const poses = buildSystemOverviewUnityPoses(
    realtimePoseBatch,
    realtimeStore.targetBatch,
  )
  if (!poses.length) return
  const poseRunId = currentOverviewUnityPoseRunId()
  if (!poseRunId) return
  unityBridgeStore.sendFor('SYSTEM_OVERVIEW', 'poseFrame', {
    runId: String(poseRunId),
    sequence: nextUnityPoseSequence(poseRunId),
    timestamp: poseBatchTimestampMs(realtimePoseBatch) || Date.now(),
    source: 'vue-ros',
    // Current realtime positions are already in the shared display map ENU.
    coordinateSystem: 'ROS_ENU',
    poses,
  })
}

function applyOverviewMissionDetail(detail: MissionDetail) {
  const activeRun = resolveActiveRun(detail)
  overviewActiveMissionRunId.value = activeRun?.id ?? null
  activeExperimentStore.sync(detail)
  const missionChanged = overviewMissionId.value !== null && overviewMissionId.value !== detail.mission.id
  if (missionChanged || ['DRAFT', 'COMPLETED', 'FAILED', 'CANCELLED'].includes(detail.mission.status)) {
    overviewDeploymentAcknowledged.value = false
  }
  overviewMissionId.value = detail.mission.id
  overviewMissionName.value = detail.mission.name
  overviewMissionStatus.value = detail.mission.status
  realMissionRuntimeStore.syncContext({
    missionId: detail.mission.id,
    runId: activeRun?.id ?? null,
    backendMissionStatus: detail.mission.status,
  })
  const currentRunId = activeRun?.id
  const latestControlEvent = detail.events.find((event) =>
    event.runId === currentRunId && /^(MISSION_CONTROL|SYSTEM_OVERVIEW):/.test(event.source ?? ''),
  )
  overviewMissionControlSource.value = latestControlEvent?.source?.split(':', 1)[0] ?? 'UNKNOWN'
  const bindings = detail.devices.filter((item) => item.type === 'UAV' || item.type === 'USV')
  const requiredBindings = bindings.some((item) => item.required) ? bindings.filter((item) => item.required) : bindings
  overviewMissionDevices.value = requiredBindings
    .filter((item) => item.code)
    .map((item) => ({
      code: normalizeDeviceCode(item.code!),
      name: item.name || item.code!.toUpperCase(),
      type: item.type as 'UAV' | 'USV',
      status: item.status,
    }))
}

async function loadOverviewMission() {
  const result = await fetchMissions({
    type: 'COOPERATIVE_ENCIRCLEMENT',
    page: 0,
    size: 50,
  })
  const priority: MissionStatus[] = ['RUNNING', 'PAUSED', 'READY', 'DRAFT', 'CANCELLED', 'FAILED', 'COMPLETED']
  const mission = priority
    .map((status) => result.records.find((item) => item.status === status))
    .find(Boolean) ?? result.records[0]
  if (!mission) {
    overviewMissionId.value = null
    return null
  }
  const detail = await fetchMission(mission.id)
  applyOverviewMissionDetail(detail)
  return detail
}

function missionUnityCommand(action: MissionAction) {
  return {
    start: 'missionStart',
    pause: 'missionPause',
    resume: 'missionResume',
    complete: 'missionComplete',
    fail: 'missionFail',
    cancel: 'missionCancel',
    ready: 'missionResume',
  }[action]
}

function externalAlgorithm(code: string) {
  return code === 'GB_SFLA_CS' || code === 'ESCORT_GUARD'
}

function algorithmConfig(current: MissionDetail) {
  return Object.fromEntries(current.parameters.map(item => [item.key, item.value ?? '']))
}

async function runOverviewMissionAction(action: MissionAction) {
  if (!overviewMissionId.value) {
    await loadOverviewMission()
  }
  if (!overviewMissionId.value) throw new Error('未找到三机三艇协同围捕任务')

  const missionId = overviewMissionId.value
  const result = await executeMissionAction(missionId, action, 'SYSTEM_OVERVIEW')
  applyOverviewMissionDetail(result.detail)

  if (result.command && (result.command.status === 'FAILED' || result.command.status === 'TIMEOUT')) {
    throw new Error(result.command.detail || '任务指令未能下发')
  }

  if (result.command) {
    if (action === 'start') realMissionRuntimeStore.noteStartCommand(result.command.commandKey)
    if (action === 'cancel') realMissionRuntimeStore.noteCancelCommand(result.command.commandKey)
    if (overviewRuntimeMode.value === 'REAL') {
      const rosStatus = action === 'start'
        ? await realtimeStore.waitForCommandStart(
            result.command.commandKey,
            90000,
            result.command.status,
            result.detail.currentRun?.id,
          )
        : await realtimeStore.waitForCommandResult(result.command.commandKey, 90000)
      if (action === 'start') {
        if (rosStatus === 'COMMAND_START_WAIT_TIMEOUT') {
          throw new Error(`COMMAND_START_WAIT_TIMEOUT：90 秒内未确认真实任务启动（commandKey=${result.command.commandKey}）`)
        }
        if (['FAILED', 'REJECTED', 'CANCELLED', 'TIMEOUT', 'EXPIRED'].includes(rosStatus)) {
          throw new Error(`COMMAND_START_FAILED：真实任务启动指令返回 ${rosStatus}（commandKey=${result.command.commandKey}）`)
        }
      } else if (rosStatus !== 'SUCCEEDED') {
        throw new Error(`真实任务指令未收到成功结果：${rosStatus}`)
      }
    } else {
      if (!unityBridgeStore.connected) throw new Error('Unity WebGL 尚未连接，无法确认任务指令')
      const acknowledgement = await unityBridgeStore.sendControlCommandAndWait(
        missionUnityCommand(action),
        '',
        result.command.commandKey,
      )
      if (!acknowledgement.success) {
        throw new Error(acknowledgement.status || 'Unity 未确认任务指令')
      }
    }
  }

  if (action === 'start' && externalAlgorithm(result.detail.mission.algorithmCode)) {
    const newRun = result.detail.currentRun
    if (!newRun) throw new Error('任务启动成功，但响应中没有新创建的 MissionRun')
    try {
      await prepareAlgorithmRun(
        newRun.id,
        result.detail.mission.algorithmCode,
        algorithmConfig(result.detail),
      )
      await controlAlgorithmRun(newRun.id, 'start')
    } catch (error) {
      console.error('[Dashboard] algorithm runtime start failed', {
        missionId,
        runId: newRun.id,
        algorithmCode: result.detail.mission.algorithmCode,
        error,
      })
      const message = error instanceof Error ? error.message : '未知错误'
      const category = /Algorithm initial frame timeout/i.test(message)
        ? 'ALGORITHM_INITIAL_FRAME_TIMEOUT'
        : /算法运行器启动超时/i.test(message)
          ? 'ALGORITHM_RUNNER_READY_TIMEOUT'
          : /后端响应超时|ECONNABORTED|ETIMEDOUT/i.test(message)
            ? 'HTTP_TIMEOUT'
            : 'ALGORITHM_START_FAILED'
      throw new PostMissionAlgorithmRuntimeError(
        `${category}：任务已启动，但算法运行实例启动失败（MissionRun ${newRun.id}）：${message}`,
      )
    }
  }

  const confirmed = await fetchMission(missionId)
  applyOverviewMissionDetail(confirmed)
  return confirmed
}

async function retryOverviewMission() {
  if (commandBusy.value) return
  commandBusy.value = true
  try {
    const missionId = overviewMissionId.value ?? (await loadOverviewMission())?.mission.id
    if (!missionId) throw new Error('未找到可再次执行的真实任务')
    if (overviewMissionStatus.value === 'READY' || realMissionRuntimeStore.backendMissionStatus === 'READY') {
      realMissionRuntimeStore.acknowledgeTerminalForRetry()
      overviewDeploymentAcknowledged.value = true
      syncRealOverviewUnityScene()
      ElMessage.success('任务已处于待执行状态，可重新启动')
      return
    }
    const result = await executeMissionAction(missionId, 'ready', 'SYSTEM_OVERVIEW')
    applyOverviewMissionDetail(result.detail)
    realMissionRuntimeStore.acknowledgeTerminalForRetry()
    overviewDeploymentAcknowledged.value = true
    syncRealOverviewUnityScene()
    ElMessage.success('任务已进入待执行状态，可重新启动')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '再次执行准备失败')
  } finally {
    commandBusy.value = false
  }
}

async function recordRuntimeCommand(
  commandType: RuntimeCommandType,
  detail: string,
  deviceCode = selectedDeviceCode.value,
  payload?: Record<string, unknown>,
) {
  try {
    return await issueRuntimeCommand({
      commandType,
      deviceCode,
      payload: JSON.stringify({ source: 'SYSTEM_OVERVIEW', ...(payload ?? {}) }),
      detail,
      runtimeScope: 'SYSTEM_OVERVIEW',
      runtimeInstanceId: unityInstanceId,
    })
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '控制指令下发失败')
    throw error
  }
}

async function sendTrackedUnityCommand(
  commandType: RuntimeCommandType,
  detail: string,
  bridgeType: string,
  payload: Record<string, unknown>,
  deviceCode = selectedDeviceCode.value,
) {
  if (cameraCommandBusy.value) throw new Error('上一条视角指令仍在确认中')
  if (!unityBridgeStore.connected) throw new Error('Unity WebGL 尚未连接')
  cameraCommandBusy.value = true
  try {
    const result = await recordRuntimeCommand(commandType, detail, deviceCode, payload)
    if (result.status === 'FAILED' || result.status === 'TIMEOUT') {
      throw new Error(result.detail || '后端未能创建 Unity 指令')
    }
    const acknowledgement = await unityBridgeStore.sendAndWait(
      bridgeType,
      payload,
      result.commandKey,
    )
    if (!acknowledgement.success) throw new Error(acknowledgement.status || 'Unity 未确认指令')
    return acknowledgement
  } finally {
    cameraCommandBusy.value = false
  }
}

async function selectDevice(deviceCode: string) {
  const normalizedCode = normalizeDeviceCode(deviceCode) || selectedOrDefaultDeviceCode()
  selectedDeviceCode.value = normalizedCode
  selectedCameraMode.value = 'device-follow'
  if (!unityCameraReady.value) {
    selectedDeviceSyncedToUnity = ''
    lastUnityEvent.value = `selectDevice:${normalizedCode}:waiting`
    return
  }
  unityBridgeStore.send('selectDevice', { deviceCode: normalizedCode })
  selectedDeviceSyncedToUnity = normalizedCode
  lastUnityEvent.value = `selectDevice:${normalizedCode}`
}

async function switchCamera(mode: string) {
  const deviceCode = selectedOrDefaultDeviceCode()
  unityBridgeStore.send('switchCamera', { mode, deviceCode })
  selectedCameraMode.value = mode
  if (mode === 'device-follow') selectedDeviceSyncedToUnity = deviceCode
  lastUnityEvent.value = `switchCamera:${mode}`
}

async function toggleUnityTrajectory() {
  if (!unityBridgeStore.connected || unityBridgeStore.trajectoryTogglePending) return
  const visible = !unityBridgeStore.trajectoryVisible
  unityBridgeStore.setTrajectoryTogglePending(true)
  try {
    unityBridgeStore.send('toggleTrajectory', { visible })
    lastUnityEvent.value = `toggleTrajectory:${visible ? 'show' : 'hide'}`
    if (trajectoryToggleTimer !== null) window.clearTimeout(trajectoryToggleTimer)
    trajectoryToggleTimer = window.setTimeout(() => {
      trajectoryToggleTimer = null
      if (!unityBridgeStore.trajectoryTogglePending) return
      unityBridgeStore.setTrajectoryTogglePending(false)
      ElMessage.error('Unity 轨迹显示指令未返回确认')
    }, 6000)
  } catch {
    unityBridgeStore.setTrajectoryTogglePending(false)
  }
}

type VehicleBatchResult = {
  total: number
  acknowledged: number
  failed: number
  allAcknowledged: boolean
}

async function sendVehicleCommand(
  command: VehicleQuickCommand,
  options: { manageBusy?: boolean; notify?: boolean } = {},
): Promise<VehicleBatchResult> {
  const manageBusy = (options.manageBusy ?? true) && !isUsvSafetyStop(command.commandType)
  const notify = options.notify ?? true
  if (!command.deviceCodes.length) {
    return { total: 0, acknowledged: 0, failed: 0, allAcknowledged: false }
  }
  if (manageBusy) commandBusy.value = true
  try {
    const statuses: RuntimeCommandStatus[] = []
    // Send in order so each ROS result maps back to the correct device button.
    for (const deviceCode of command.deviceCodes) {
      const status = await (async (): Promise<RuntimeCommandStatus> => {
        const key = normalizeDeviceCode(deviceCode)
        commandFeedback.value = { ...commandFeedback.value, [key]: 'PENDING' }
        try {
          const result = await issueSingleDeviceCommand({
            commandType: command.commandType,
            deviceCode: key,
            payload: JSON.stringify({ source: 'SYSTEM_OVERVIEW' }),
            detail: `${command.label} / ${deviceCode}`,
            runtimeScope: 'SYSTEM_OVERVIEW',
            runtimeInstanceId: unityInstanceId,
          })
          if (result.status === 'FAILED' || result.status === 'TIMEOUT') {
            commandFeedback.value = { ...commandFeedback.value, [key]: result.status }
            return result.status
          }
          const rosStatus = await realtimeStore.waitForCommandResult(result.commandKey, 90000)
          const status: RuntimeCommandStatus = rosStatus === 'SUCCEEDED' ? 'SUCCEEDED'
            : rosStatus === 'CANCELLED' ? 'CANCELLED'
              : rosStatus === 'TIMEOUT' || rosStatus === 'EXPIRED' ? 'TIMEOUT' : 'FAILED'
          commandFeedback.value = { ...commandFeedback.value, [key]: status }
          if (status === 'SUCCEEDED') {
            const state = operationalStateAfterCommand(command.commandType)
            if (state) operationalStates.value = { ...operationalStates.value, [key]: state }
          }
          return status
        } catch (error) {
          const status: RuntimeCommandStatus = error instanceof Error && error.message.includes('超时') ? 'TIMEOUT' : 'FAILED'
          commandFeedback.value = { ...commandFeedback.value, [key]: status }
          return status
        }
      })()
      statuses.push(status)
    }
    const acknowledged = statuses.filter((status) => status === 'SUCCEEDED').length
    const failed = statuses.length - acknowledged
    const result = {
      total: statuses.length,
      acknowledged,
      failed,
      allAcknowledged: statuses.length > 0 && acknowledged === statuses.length,
    }
    if (notify) {
      if (result.allAcknowledged) ElMessage.success(`${command.label}：${acknowledged}/${statuses.length} 台已确认`)
      else ElMessage.error(`${command.label}：成功 ${acknowledged}，失败 ${failed}`)
    }
    return result
  } finally {
    if (manageBusy) commandBusy.value = false
  }
}

async function sendFleetPair(
  uavCommand: VehicleQuickCommand['commandType'],
  uavLabel: string,
  usvCommand: VehicleQuickCommand['commandType'],
  usvLabel: string,
) {
  const uav = await sendOverviewFleetCommand('UAV', uavCommand, uavLabel)
  const usv = await sendOverviewFleetCommand('USV', usvCommand, usvLabel)
  return {
    total: uav.total + usv.total,
    acknowledged: uav.acknowledged + usv.acknowledged,
    failed: uav.failed + usv.failed,
    allAcknowledged: uav.allAcknowledged && usv.allAcknowledged,
  }
}

async function sendOverviewFleetCommand(
  vehicleType: 'UAV' | 'USV',
  commandType: VehicleQuickCommand['commandType'],
  label: string,
): Promise<VehicleBatchResult> {
  const allowedStates: Partial<Record<VehicleQuickCommand['commandType'], string[]>> = {
    UAV_TAKEOFF: ['GROUNDED'], UAV_HOVER: ['AIRBORNE', 'RETURNING'], UAV_RESUME: ['HOLDING'],
    UAV_RETURN: ['AIRBORNE', 'HOLDING'], UAV_LAND: ['AIRBORNE', 'HOLDING', 'RETURNING'],
    USV_DEPART: ['MOORED', 'STOPPED'], USV_HOLD: ['SAILING', 'RETURNING'], USV_RESUME: ['HOLDING'],
    USV_RETURN: ['SAILING', 'HOLDING'], USV_STOP: ['DEPARTING', 'SAILING', 'HOLDING', 'RETURNING'],
  }
  const desiredStates: Partial<Record<VehicleQuickCommand['commandType'], string[]>> = {
    UAV_TAKEOFF: ['TAKING_OFF', 'AIRBORNE', 'HOLDING'], UAV_HOVER: ['HOLDING'], UAV_RESUME: ['AIRBORNE'],
    UAV_RETURN: ['RETURNING'], UAV_LAND: ['LANDING', 'GROUNDED'],
    USV_DEPART: ['DEPARTING', 'SAILING', 'HOLDING'], USV_HOLD: ['HOLDING'], USV_RESUME: ['SAILING'],
    USV_RETURN: ['RETURNING'], USV_STOP: ['STOPPED', 'MOORED'],
  }
  const devices = quickControlDevices.value.filter((device) => device.type === vehicleType)
  const isDeploymentCommand = commandType === 'USV_DEPART'
  const eligible: string[] = []
  let alreadySatisfied = 0
  let invalid = 0
  for (const device of devices) {
    if (commandType === 'UAV_TAKEOFF') {
      const runtimeDevice = overviewFleetCards.value.find(item => item.code === normalizeDeviceCode(device.code))
      const controlState = authoritativeUavControlState(runtimeDevice)
      if (controlState === 'AIRBORNE') alreadySatisfied += 1
      else if (controlState === 'GROUNDED') eligible.push(device.code)
      else invalid += 1
      continue
    }
    const state = normalizeOperationalState(operationalStates.value[normalizeDeviceCode(device.code)], vehicleType)
    if ((desiredStates[commandType] ?? []).includes(state)) alreadySatisfied += 1
    else if (isDeploymentCommand || (allowedStates[commandType] ?? []).includes(state)) eligible.push(device.code)
    else invalid += 1
  }
  if (invalid > 0) return { total: devices.length, acknowledged: alreadySatisfied, failed: invalid, allAcknowledged: false }
  if (!eligible.length) {
    return {
      total: devices.length,
      acknowledged: alreadySatisfied,
      failed: 0,
      allAcknowledged: devices.length > 0 && alreadySatisfied === devices.length,
    }
  }
  const issued = await sendVehicleCommand(
    { commandType, deviceCodes: eligible, label },
    { manageBusy: false, notify: false },
  )
  return {
    total: devices.length,
    acknowledged: alreadySatisfied + issued.acknowledged,
    failed: invalid + issued.failed,
    allAcknowledged: alreadySatisfied + issued.acknowledged === devices.length && issued.failed === 0,
  }
}

async function confirmReturn(action: 'return' | 'abort') {
  const abort = action === 'abort'
  await ElMessageBox.confirm(
    abort
      ? '终止后将停止系统总览中的简单围捕演示，不会启动返航，也不会影响任务中心。是否继续？'
      : '将向全部无人机和无人艇下发返航，并在确认后结束当前任务。是否继续？',
    abort ? '终止任务' : '全体返航',
    {
      confirmButtonText: abort ? '确认终止任务' : '确认全体返航',
      cancelButtonText: '取消',
      type: 'warning',
      customClass: 'mission-confirm-message-box',
      center: true,
      closeOnClickModal: false,
      closeOnPressEscape: false,
      distinguishCancelAndClose: true,
    },
  )
}

async function runOverviewDemoCommand(action: 'start' | 'pause' | 'resume' | 'cancel') {
  const commandType: Record<typeof action, RuntimeCommandType> = {
    start: 'START_MISSION',
    pause: 'PAUSE_MISSION',
    resume: 'RESUME_MISSION',
    cancel: 'CANCEL_MISSION',
  }
  const result = await recordRuntimeCommand(
    commandType[action],
    `系统总览独立演示：${action}`,
    '',
    { demo: true },
  )
  const acknowledgement = await unityBridgeStore.sendControlCommandAndWait(
    missionUnityCommand(action),
    '',
    result.commandKey,
  )
  if (!acknowledgement.success) throw new Error(acknowledgement.status || '系统总览 Unity 未确认演示指令')
  overviewMissionStatus.value = {
    start: 'RUNNING',
    pause: 'PAUSED',
    resume: 'RUNNING',
    cancel: 'CANCELLED',
  }[action] as MissionStatus
}

function sendRealOverviewScenario(detail?: MissionDetail | null) {
  const missionId = detail?.mission.id ?? overviewMissionId.value
  if (!missionId) return
  const activeRun = resolveActiveRun(detail)
  if (detail) {
    overviewActiveMissionRunId.value = activeRun?.id ?? null
  }

  const runId = currentOverviewRealRunId(missionId)
  const algorithmCode = detail?.mission.algorithmCode ?? selectedOverviewAlgorithm.value
  if (!runId) return
  const scenarioKey = `${missionId}:${runId}:${algorithmCode}`
  const channel = unityBridgeStore.channels.SYSTEM_OVERVIEW
  if (
    scenarioKey === lastRealOverviewScenarioKey
    && channel.platformReady
    && channel.scenarioRunId === runId
  ) {
    return
  }
  lastRealOverviewScenarioKey = scenarioKey
  unityBridgeStore.sendFor('SYSTEM_OVERVIEW', 'loadScenario', {
    runtimeMode: 'REAL',
    algorithmCode,
    missionId,
    runId,
  })
}

function syncRealOverviewUnityScene() {
  if (overviewRuntimeMode.value !== 'REAL') return
  if (!overviewMissionId.value) return
  sendRealOverviewScenario()
  pushRealtimePoseFrameToUnity()
}

async function triggerRealOverviewMissionStart(detail: MissionDetail) {
  const run = resolveActiveRun(detail)
  if (!run) throw new Error('当前真实任务没有可启动的运行批次')
  const result = await issueRuntimeCommand({
    commandType: 'START_MISSION',
    runId: run.id,
    payload: JSON.stringify({ source: 'SYSTEM_OVERVIEW', operatorStart: true }),
    detail: `启动围捕：${detail.mission.name}`,
    runtimeScope: 'MISSION_CENTER',
    runtimeInstanceId: run.runtimeInstanceId ?? undefined,
  })
  if (result.status === 'FAILED' || result.status === 'TIMEOUT') {
    throw new Error(result.detail || '真实任务启动指令未能下发')
  }
  const rosStatus = await realtimeStore.waitForCommandStart(result.commandKey, 90000, result.status, run.id)
  if (rosStatus === 'COMMAND_START_WAIT_TIMEOUT') {
    throw new Error(`COMMAND_START_WAIT_TIMEOUT：90 秒内未确认真实任务启动（commandKey=${result.commandKey}）`)
  }
  if (['FAILED', 'REJECTED', 'CANCELLED', 'TIMEOUT', 'EXPIRED'].includes(rosStatus)) {
    throw new Error(`COMMAND_START_FAILED：真实任务启动指令返回 ${rosStatus}（commandKey=${result.commandKey}）`)
  }
}

async function startOverviewMission() {
  if (overviewRuntimeMode.value === 'REAL') {
    if (!realValidationReady.value) {
      throw new Error('真实验证链路未就绪：等待 ROS Gateway、Unity 控制桥和 6 路真实位姿数据')
    }
    const current = await loadOverviewMission()
    if (!overviewMissionId.value) throw new Error('未找到真实任务')
    if (overviewMissionStatus.value === 'RUNNING' || overviewMissionStatus.value === 'PAUSED') {
      overviewDeploymentAcknowledged.value = true
      sendRealOverviewScenario(current)
      return
    }
    if (overviewMissionStatus.value !== 'READY') {
      throw new Error(`当前真实任务状态为 ${overviewMissionStatus.value}，不能启动`)
    }
    const confirmed = await runOverviewMissionAction('start')
    sendRealOverviewScenario(confirmed)
    return
  }
  sendVirtualScenario()
  await runOverviewDemoCommand('start')
}

async function pauseOverviewMission() {
  if (overviewRuntimeMode.value === 'REAL') {
    await runOverviewMissionAction('pause')
    return
  }
  const held = await sendFleetPair('UAV_HOVER', '无人机编组悬停', 'USV_HOLD', '无人艇编组定点保持')
  if (!held.allAcknowledged) throw new Error(`暂停失败：${held.failed} 台载具未确认保持`)
  await runOverviewDemoCommand('pause')
}

async function resumeOverviewMission() {
  if (overviewRuntimeMode.value === 'REAL') {
    await runOverviewMissionAction('resume')
    return
  }
  const resumed = await sendFleetPair('UAV_RESUME', '无人机继续任务', 'USV_RESUME', '无人艇继续航行')
  if (!resumed.allAcknowledged) throw new Error(`继续任务失败：${resumed.failed} 台载具未确认恢复`)
  await runOverviewDemoCommand('resume')
}

async function finishOverviewMission(action: 'return' | 'abort') {
  if (overviewRuntimeMode.value === 'REAL') {
    if (action === 'return') {
      const returning = await sendFleetPair('UAV_RETURN', '无人机编组返航', 'USV_RETURN', '无人艇编组返航')
      if (!returning.allAcknowledged) {
        throw new Error(`返航失败：${returning.failed} 台载具未确认返航`)
      }
    }
    await runOverviewMissionAction('cancel')
    overviewDeploymentAcknowledged.value = false
    return
  }
  await runOverviewDemoCommand('cancel')
  overviewDeploymentAcknowledged.value = false
}

function overviewMissionActionError(error: unknown, action: string) {
  const message = error instanceof Error ? error.message : '任务编组操作失败'
  if (action !== 'start' || /^[A-Z_]+：/.test(message)) return message
  if (/后端响应超时|ECONNABORTED|ETIMEDOUT/i.test(message)) return `HTTP_TIMEOUT：${message}`
  return message
}

function isPostMissionAlgorithmRuntimeError(error: unknown, action: string) {
  return action === 'start' && error instanceof PostMissionAlgorithmRuntimeError
}

async function handleMissionGroupAction(action: 'deploy' | 'start' | 'pause' | 'resume' | 'return' | 'abort') {
  if (commandBusy.value) return
  if (!unityControlReady.value) {
    ElMessage.error('Unity 控制桥尚未就绪，任务指令未下发')
    return
  }
  if (action === 'abort' && overviewRuntimeMode.value === 'REAL') {
    try {
      await ElMessageBox.confirm(
        '继续终止将发送 MISSION.CANCEL，并停止当前 ROS 围捕任务。',
        '确认终止任务',
        {
          confirmButtonText: '确认终止',
          cancelButtonText: '取消',
          type: 'warning',
          customClass: 'mission-confirm-message-box',
          center: true,
          closeOnClickModal: false,
          closeOnPressEscape: false,
          distinguishCancelAndClose: true,
        },
      )
    } catch {
      return
    }
  } else if (action === 'return' || action === 'abort') {
    try {
      await confirmReturn(action)
    } catch {
      return
    }
  }

  commandBusy.value = true
  try {
    if (action === 'abort') {
      await finishOverviewMission('abort')
      ElMessage.success(`${overviewRuntimeModeLabel.value}任务已终止`)
      return
    }

    if (action === 'deploy') {
      if (overviewMissionStatus.value === 'RUNNING' || overviewMissionStatus.value === 'PAUSED') {
        ElMessage.success('编组已经部署并处于任务状态，无需重复部署')
        return
      }
      // 这里只建立总览页自己的简单围捕编组。设备运动由后续
      // “开始任务”统一触发，避免逐台命令依赖 Unity 当前设备选择。
      overviewMissionStatus.value = 'READY'
      overviewDeploymentAcknowledged.value = true
      if (overviewRuntimeMode.value === 'REAL') {
        await loadOverviewMission()
        overviewDeploymentAcknowledged.value = overviewMissionId.value !== null
      }
      ElMessage.success(`${overviewRuntimeModeLabel.value}任务已完成准备`)
      return
    }

    if (action === 'start') {
      if (!overviewDeploymentAcknowledged.value && overviewRuntimeMode.value !== 'REAL') {
        throw new Error('请先点击“编组部署”，确认三机三艇加入围捕编组')
      }
      await startOverviewMission()
      if (overviewRuntimeMode.value === 'REAL') {
        const state = realMissionRuntimeStore.runtimeState
        ElMessage.success(
          state === 'COMPLETED'
            ? '围捕条件已满足，任务已完成'
            : state === 'RUNNING'
              ? '围捕执行中'
              : '启动命令已确认，等待围捕执行',
        )
        return
      }
      ElMessage.success(`${overviewMissionName.value}已启动`)
      return
    }

    if (action === 'pause') {
      await pauseOverviewMission()
      ElMessage.success('任务已暂停')
      return
    }

    if (action === 'resume') {
      await resumeOverviewMission()
      ElMessage.success('任务已继续')
      return
    }

    await finishOverviewMission('return')
    ElMessage.success('全体返航已确认，当前任务已结束')
  } catch (error) {
    if (isPostMissionAlgorithmRuntimeError(error, action)) {
      console.error('[Dashboard] mission started; algorithm runtime error remains visible in page state', error)
    } else {
      ElMessage.error(overviewMissionActionError(error, action))
    }
  } finally {
    commandBusy.value = false
  }
}

function handleUnityCommand(message: UnityMessage) {
  lastUnityEvent.value = `vue->unity:${message.type}`
  unityCommandState.value = `已发送：${message.type}`
}

function handleUnityReady() {
  unityConnection.value = 'Unity WebGL 已连接'
  lastUnityEvent.value = 'sceneLoaded'
  pushRealtimePoseFrameToUnity()
}

function handleUnityMessage(message: UnityMessage) {
  const payload = message.payload ?? {}
  unityConnection.value = payload.source === 'mock' ? 'Unity Mock 已接入' : 'Unity WebGL 已连接'
  lastUnityEvent.value = message.type

  if (message.type === 'scenarioLoaded' && payload.success === true) {
    unityPoseRunId = String(payload.runId ?? currentOverviewRealRunId() ?? '')
    poseFrameSequence = 0
  }

  if (message.type === 'vueCommandReceived') {
    const commandType = String(payload.type ?? 'unknown')
    const bridgeSent = payload.bridgeSent === true ? '已送达 Unity' : '等待 Unity 实例'
    unityCommandState.value = `已接收：${commandType} / ${bridgeSent}`
  }

  if (message.type === 'commandAck') {
    const commandType = String(payload.commandType ?? 'unknown')
    const status = String(payload.status ?? 'unknown')
    const success = payload.success === true
    const deviceCode = normalizeDeviceCode(String(payload.deviceCode ?? ''))
    if (deviceCode) {
      commandFeedback.value = {
        ...commandFeedback.value,
        [deviceCode]: success ? 'SUCCEEDED' : 'FAILED',
      }
      if (success) {
        const unityState = normalizeOperationalState(
          status.split(':', 1)[0]?.trim(),
          deviceCode.startsWith('uav') ? 'UAV' : 'USV',
        )
        if (unityState) operationalStates.value = { ...operationalStates.value, [deviceCode]: unityState }
      }
    } else if (success) {
      const missionState = status.split(':', 1)[0]?.trim().toUpperCase()
      if (['RUNNING', 'PAUSED', 'FAILED', 'CANCELLED', 'COMPLETED'].includes(missionState ?? '')) {
        overviewMissionStatus.value = missionState as MissionStatus
      }
    }
    unityCommandState.value = `${success ? '已执行' : '执行失败'}：${commandType} / ${status}`
  }

  if (message.type === 'cameraChanged') {
    const success = payload.success === true
    const status = String(payload.status ?? (success ? '视角切换完成' : '视角切换失败'))
    unityCommandState.value = `${success ? '已切换' : '切换失败'}：${status}`
    if (!success) ElMessage.error(status)
  }

  if (message.type === 'cameraAdjusted') {
    const zoom = Number(payload.zoomPercent)
    if (Number.isFinite(zoom)) cameraZoomPercent.value = Math.round(zoom)
    revealCameraTools()
    const success = payload.success === true
    const status = String(payload.status ?? (success ? '相机观察范围已调整' : '相机调整失败'))
    unityCommandState.value = status
    if (!success) ElMessage.error(status)
  }

  if (message.type === 'cameraInteraction' && payload.status === 'wheel-zoom') {
    const zoom = Number(payload.zoomPercent)
    if (Number.isFinite(zoom)) cameraZoomPercent.value = Math.round(zoom)
    revealCameraTools()
  }

  if (message.type === 'trajectoryVisibilityChanged') {
    const success = payload.success === true
    const visible = payload.visible !== false
    const status = String(payload.status ?? (visible ? 'Unity 轨迹已显示' : 'Unity 轨迹已隐藏'))
    if (trajectoryToggleTimer !== null) {
      window.clearTimeout(trajectoryToggleTimer)
      trajectoryToggleTimer = null
    }
    unityBridgeStore.setTrajectoryTogglePending(false)
    if (success) unityBridgeStore.setTrajectoryVisibility(visible)
    else ElMessage.error(status)
    unityCommandState.value = `${success ? '已执行' : '执行失败'}：${status}`
  }

  if (message.type !== 'cameraChanged' || payload.success === true) {
    if (typeof payload.deviceCode === 'string' && payload.deviceCode.trim()) selectedDeviceCode.value = payload.deviceCode
    if (message.type === 'cameraChanged' && payload.success === true && typeof payload.deviceCode === 'string' && payload.deviceCode.trim()) {
      selectedDeviceSyncedToUnity = normalizeDeviceCode(payload.deviceCode)
    }
    if (typeof payload.mode === 'string' && payload.mode.trim()) selectedCameraMode.value = payload.mode
  }
}

function handleUnityError(message: string) {
  unityConnection.value = 'Unity WebGL 加载失败'
  lastUnityEvent.value = 'unityError'
  ElMessage.error(message)
}

onMounted(() => {
  realtimeStore.connect()
  selectedOrDefaultDeviceCode()
  unityViewportStore.show('dashboard')
  freshnessTimer = window.setInterval(() => { freshnessClock.value = Date.now() }, 500)
  void monitoringStore.refresh({}, true).then(pushPoseFrameToUnity)
  monitoringStore.connectEvents()
  void loadOverviewMission().catch(() => undefined)
  void fetchAlgorithms().then((items) => {
    overviewAlgorithms.value = items.length ? items : [...overviewAlgorithmFallbacks]
    const preferred = items.find(item => item.enabled && item.code === selectedOverviewAlgorithm.value)
      ?? items.find(item => item.enabled && ['GB_SFLA_CS','ESCORT_GUARD'].includes(item.code))
    if (preferred) void selectOverviewAlgorithm(preferred.code)
  }).catch(() => undefined)
})

onActivated(() => {
  dashboardViewActive.value = true
  unityViewportStore.show('dashboard')
  overviewCameraInitialized = false
  ensureOverviewCamera()
})
onDeactivated(() => {
  dashboardViewActive.value = false
  if (unityViewportStore.target === 'dashboard') unityViewportStore.park()
})

onBeforeUnmount(() => {
  if (unityViewportStore.target === 'dashboard') unityViewportStore.park()
  if (freshnessTimer !== null) window.clearInterval(freshnessTimer)
  if (trajectoryToggleTimer !== null) window.clearTimeout(trajectoryToggleTimer)
  if (cameraToolsTimer !== null) window.clearTimeout(cameraToolsTimer)
  monitoringStore.disconnectEvents()
})

watch(
  () => [
    realtimeStore.poseBatch?.runId ?? '',
    realtimeStore.poseBatch?.source ?? '',
    realtimeStore.poseBatch?.sequence ?? 0,
    realtimeStore.poseBatch?.timestamp ?? '',
    realtimeStore.targetBatch?.runId ?? '',
    realtimeStore.targetBatch?.sequence ?? 0,
    realtimeStore.targetBatch?.timestamp ?? '',
    overviewRosMissionPhase.value,
  ] as const,
  () => pushRealtimePoseFrameToUnity(),
  { immediate: true },
)

watch(
  () => [
    realtimeStore.missionStatus?.runId ?? '',
    realtimeStore.missionStatus?.payload?.missionId ?? '',
    realtimeStore.missionStatus?.payload?.runId ?? '',
    realtimeStore.missionStatus?.payload?.state ?? '',
    realtimeStore.missionStatus?.payload?.phase ?? '',
  ] as const,
  () => {
    const envelope = realtimeStore.missionStatus
    if (!envelope || !overviewMissionId.value) return
    const missionId = Number(envelope.payload?.missionId)
    if (!Number.isFinite(missionId) || missionId !== overviewMissionId.value) return
    const runId = Number(envelope.runId ?? envelope.payload?.runId)
    if (!Number.isFinite(runId) || runId <= 0) return
    realMissionRuntimeStore.syncContext({
      missionId,
      runId,
      runScopePolicy: 'ALLOW_MISSING',
    })
  },
  { immediate: true },
)

watch(
  () => [
    overviewRuntimeMode.value,
    overviewMissionId.value ?? 0,
    overviewActiveMissionRunId.value ?? 0,
    realMissionRuntimeStore.currentRunId ?? 0,
    overviewUnityChannel.value.connected,
    overviewUnityChannel.value.platformReady,
  ] as const,
  () => syncRealOverviewUnityScene(),
  { immediate: true },
)

watch(
  () =>
    monitoringStore.nodes
      .map(
        (node) =>
          `${node.code}:${node.status}:${node.positionX ?? '-'}:${node.positionY ?? '-'}:${node.positionZ ?? '-'}:${node.heartbeatAgeSeconds}`,
      )
      .join('|'),
  () => pushPoseFrameToUnity(),
)

watch(
  () => [
    unityCameraReady.value,
    selectableDevices.value.map((device) => normalizeDeviceCode(device.code)).join('|'),
  ] as const,
  ([ready]) => {
    if (!ready) {
      overviewCameraInitialized = false
      selectedDeviceSyncedToUnity = ''
      return
    }
    ensureOverviewCamera()
  },
  { immediate: true },
)

watch(
  () => unityBridgeStore.connected,
  (connected) => {
    if (connected) handleUnityReady()
    else if (!unityBridgeStore.error) unityConnection.value = '等待 WebGL 构建'
  },
  { immediate: true },
)

watch(
  () => unityBridgeStore.lastMessage,
  (message) => {
    if (message) handleUnityMessage(message)
  },
)

watch(
  () => unityBridgeStore.lastOutgoing,
  (message) => {
    if (message) handleUnityCommand(message)
  },
)

watch(
  () => unityBridgeStore.error,
  (message) => {
    if (message) handleUnityError(message)
  },
)

function syncOperationalStatesFromTrajectory() {
  const frame = trajectoryStore.frame
  if (!frame?.agents?.length) return

  const next = { ...operationalStates.value }
  for (const agent of frame.agents) {
    if (agent.type !== 'UAV' && agent.type !== 'USV') continue
    next[normalizeDeviceCode(agent.code)] = normalizeOperationalState(agent.state, agent.type)
  }
  operationalStates.value = next
}

watch(
  () => trajectoryStore.frame?.sequence,
  () => syncOperationalStatesFromTrajectory(),
)
</script>

<template>
  <ConsoleLayout title="系统总览" eyebrow="MISSION OVERVIEW" :show-refresh="false" immersive>
    <section class="overview-console overview-hf" aria-label="海空协同仿真总览">
      <header class="overview-hf-statusbar">
        <div class="overview-current-view">
          <strong>当前观察设备</strong>
          <span>{{ selectedDeviceCode.toUpperCase() }}</span>
          <small>{{ selectedCameraMode === 'device-follow' ? '设备跟随' : '全局态势' }}</small>
        </div>
        <nav class="overview-workspace-switch" aria-label="工作空间切换">
          <span class="active">系统总览</span>
          <RouterLink :to="{ name: 'dashboard', query: { workspace: 'simulation' } }">算法仿真</RouterLink>
        </nav>
        <div class="overview-link-status">
          <b :class="{ online: rosBridgeOnline }"><i></i>ROS {{ rosBridgeOnline ? '在线' : '离线' }}</b>
          <b :class="{ online: unityReady }"><i></i>Unity {{ unityReady ? '在线' : '等待' }}</b>
          <b class="pose"><i></i>{{ onlineVehicleCount }}/6 实时位姿</b>
          <b><i></i>{{ onlineNodeCount }}/{{ displayedNodes.length }} 节点正常</b>
        </div>
      </header>

      <section class="overview-stage-panel">
        <div class="overview-taskbar">
          <div class="overview-taskbar-title">
            <h3>海空协同任务</h3>
            <span :title="overviewMissionName">{{ overviewMissionName }}</span>
          </div>
          <label class="overview-taskbar-algorithm">
            <select aria-label="执行算法" :value="selectedOverviewAlgorithm" :disabled="overviewMissionRunning || commandBusy" @change="selectOverviewAlgorithm(($event.target as HTMLSelectElement).value)">
              <option v-for="algorithm in enabledOverviewAlgorithms" :key="algorithm.code" :value="algorithm.code">{{ algorithm.name }}</option>
            </select>
            <small v-if="overviewSelectedAlgorithm" :title="`完整版本：${overviewSelectedAlgorithm.version}`">v{{ overviewSelectedAlgorithm.version.replace(/^v/i, '').split('-')[0] }}</small>
          </label>
          <div class="overview-taskbar-views" aria-label="Unity 视角切换">
            <button
              v-for="mode in cameraModes"
              :key="mode.value"
              type="button"
              :class="{ active: selectedCameraMode === mode.value }"
              :disabled="cameraCommandBusy || !unityCameraReady"
              @click="switchCamera(mode.value)"
            >
              {{ mode.label }}
            </button>
            <button
              type="button"
              :class="{ active: !unityBridgeStore.trajectoryVisible }"
              :disabled="!unityBridgeStore.connected || unityBridgeStore.trajectoryTogglePending"
              :aria-pressed="!unityBridgeStore.trajectoryVisible"
              @click="toggleUnityTrajectory"
            >
              {{ unityBridgeStore.trajectoryVisible ? '隐藏轨迹' : '显示轨迹' }}
            </button>
          </div>
          <div class="overview-taskbar-action">
            <span class="overview-taskbar-state" :data-state="overviewRuntimeState" role="status"><i aria-hidden="true"></i>{{ overviewMissionStateText }}</span>
            <button
              type="button"
              :class="{ danger: overviewMissionRunning }"
              :disabled="overviewMissionActionDisabled"
              @click="handleOverviewMissionToggle"
            >
              <component :is="overviewMissionRunning ? CircleStop : Play" :size="18" />
              {{ overviewMissionActionLabel }}
            </button>
          </div>
        </div>

        <div class="overview-stage-viewport-shell">
          <div class="overview-unity-stage unity-runtime-viewport" data-unity-runtime-viewport="dashboard">
            <div v-if="!unityBridgeStore.connected" class="unity-runtime-placeholder">
              <strong>Unity WebGL 常驻实例启动中</strong>
              <span>{{ unityBridgeStore.error || '正在加载全局运行实例，请稍候' }}</span>
            </div>
          </div>

          <Teleport
            v-if="dashboardViewActive"
            to="#unity-runtime-overlay-system-overview-overview-unity-01"
          >
            <div
              class="overview-camera-tools"
              :class="{ visible: cameraToolsVisible }"
              aria-label="Unity 相机缩放控制"
            >
              <span>滚轮缩放</span>
              <input
                :value="cameraZoomPercent"
                type="range"
                min="42"
                max="225"
                step="1"
                :disabled="!unityCameraReady"
                aria-label="Unity 相机缩放比例"
                @input="setUnityZoom"
              >
              <b>{{ cameraZoomPercent }}%</b>
              <button type="button" :disabled="!unityCameraReady" @click="fitUnityOverview">
                <RotateCcw :size="14" />
                适配全貌
              </button>
            </div>

            <section
              v-if="selectedOverviewDevice"
              class="overview-selected-control"
              :class="selectedOverviewDevice.type.toLowerCase()"
              aria-label="当前设备快捷控制"
            >
              <header>
                <span>当前设备</span>
                <strong>{{ selectedOverviewDevice.code.toUpperCase() }}</strong>
                <b :class="(selectedOverviewDevice.status || 'UNKNOWN').toLowerCase()">
                  {{ runtimeStatusLabel(selectedOverviewDevice.status) }}
                </b>
              </header>
              <div class="overview-selected-actions">
                <button
                  v-for="action in selectedOverviewActions"
                  :key="action.commandType"
                  type="button"
                  :class="{ danger: action.danger }"
                  :disabled="(commandBusy && !isUsvSafetyStop(action.commandType)) || !isSelectedOverviewActionAllowed(action)"
                  @click="issueSelectedQuickCommand(action)"
                >
                  <component :is="action.icon" :size="19" :stroke-width="1.9" />
                  <span>{{ action.label }}</span>
                </button>
              </div>
            </section>
          </Teleport>
        </div>
      </section>

      <section class="overview-device-deck" aria-label="六载具实时状态">
        <header class="overview-section-title">
          <h3>设备控制</h3>
          <span>点击设备卡片切换观察视角与快捷指令</span>
        </header>
        <div class="overview-fleet-ribbon">
          <button
            v-for="device in overviewFleetCards"
            :key="device.code"
            type="button"
            class="overview-fleet-card"
            :class="[
              device.type.toLowerCase(),
              {
                active: normalizeDeviceCode(selectedDeviceCode) === device.code,
                stale: device.telemetryStale,
              },
            ]"
            :title="telemetryTooltip(device)"
            @click="selectDevice(device.code)"
          >
            <header>
              <strong>{{ device.code.toUpperCase() }}</strong>
              <b :class="(device.feedback || device.status || 'UNKNOWN').toLowerCase()">
                {{ device.feedback === 'SUCCEEDED' ? '执行成功' : runtimeStatusLabel(device.status) }}
              </b>
            </header>

            <div class="overview-card-identity">
              <VehicleGlyph
                class="overview-fleet-symbol"
                :type="device.type === 'UAV' ? 'UAV' : 'USV'"
                size="medium"
                :active="normalizeDeviceCode(selectedDeviceCode) === device.code"
              />
              <div>
                <strong>{{ device.stateLabel }}</strong>
                <span>{{ device.telemetryStale ? '遥测等待/超时' : '实时遥测' }}</span>
              </div>
            </div>

            <dl class="overview-card-coordinates">
              <div>
                <dt>{{ device.hasGeoCoordinate ? '经度' : '东向' }}</dt>
                <dd>{{ device.hasGeoCoordinate ? formatCoordinate(device.longitude) : formatLocalPosition(device.positionX) }}</dd>
              </div>
              <div>
                <dt>{{ device.hasGeoCoordinate ? '纬度' : '北向' }}</dt>
                <dd>{{ device.hasGeoCoordinate ? formatCoordinate(device.latitude) : formatLocalPosition(device.positionZ) }}</dd>
              </div>
            </dl>

            <footer>
              <div class="overview-battery">
                <span>电量</span>
                <i><em :style="{ width: `${device.batteryPercent ?? 0}%` }"></em></i>
                <strong>{{ device.batteryPercent === null ? '--' : `${device.batteryPercent}%` }}</strong>
              </div>
              <div class="overview-signal" title="链路质量基于运行心跳新鲜度">
                <span>链路</span>
                <i>
                  <em
                    v-for="bar in 4"
                    :key="bar"
                    :class="{ active: linkBarActive(device.linkQuality, bar) }"
                  ></em>
                </i>
                <strong>{{ device.linkQuality === null ? '--' : `${device.linkQuality}%` }}</strong>
              </div>
            </footer>
          </button>
        </div>
      </section>

    </section>
  </ConsoleLayout>
</template>

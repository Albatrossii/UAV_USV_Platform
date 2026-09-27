<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import MissionEventDrawer from '@/components/mission/MissionEventDrawer.vue'
import MissionExecutionOverlay from '@/components/mission/MissionExecutionOverlay.vue'
import type { VehicleQuickCommand } from '@/components/control/VehicleQuickControl.vue'
import { executeMissionAction, fetchMission } from '@/api/mission'
import { controlAlgorithmRun, fetchAlgorithmFrames, placeEscortThreat, prepareAlgorithmRun } from '@/api/algorithm'
import type { MissionAction } from '@/api/mission'
import { issueRuntimeCommand } from '@/api/runtimeControl'
import type { RuntimeCommandStatus, RuntimeCommandType } from '@/api/runtimeControl'
import { issueSingleDeviceCommand } from '@/services/singleDeviceControl'
import { useMissionTrajectorySessionStore } from '@/stores/missionTrajectorySession'
import { useMonitoringStore } from '@/stores/monitoring'
import { useRealtimeStore } from '@/stores/realtime'
import { useRealMissionRuntimeStore } from '@/stores/realMissionRuntime'
import { useTrajectoryStore } from '@/stores/trajectory'
import { useUnityBridgeStore } from '@/stores/unityBridge'
import { useUnityViewportStore } from '@/stores/unityViewport'
import { useVisualSensorStore } from '@/stores/visualSensor'
import {
  algorithmFrameToTrajectoryFrame,
  isRealtimeEnvelopeApplicable,
  mergeAuthoritativeFrame,
} from '@/services/realtimeTrajectoryAdapter'
import type { AlgorithmRuntimeFrame, MissionDetail } from '@/types/mission'
import type { RuntimeNode } from '@/types/monitoring'
import type { VisualSensorRuntimeContext } from '@/types/visualSensor'

const route = useRoute()
const router = useRouter()
const monitoringStore = useMonitoringStore()
const realtimeStore = useRealtimeStore()
const realMissionRuntimeStore = useRealMissionRuntimeStore()
const trajectoryStore = useTrajectoryStore()
const unityBridgeStore = useUnityBridgeStore()
const sessionStore = useMissionTrajectorySessionStore()
const unityViewportStore = useUnityViewportStore()
const visualSensorStore = useVisualSensorStore()

const detail = ref<MissionDetail | null>(null)
const selectedDeviceCode = ref('uav-01')
const commandFeedback = ref<Record<string, RuntimeCommandStatus | undefined>>({})
const operationalStates = ref<Record<string, string | undefined>>({})
const busy = ref(false)
const eventVisible = ref(false)
const algorithmFrame = ref<AlgorithmRuntimeFrame | null>(null)
const algorithmPolling = ref(false)
const mode = ref<'2d' | '3d' | 'vision'>('2d')
const visualDisplayMode = ref<'grid' | 'focus'>('grid')
const missionId = computed(() => Number(route.params.missionId))
const runId = computed(() => Number(route.params.runId))
const unityChannel = computed(() => unityBridgeStore.channels.MISSION_CENTER)
const missionVisualStats = computed(() => visualSensorStore.streamStatsFor('MISSION_CENTER'))
const missionVisualConnected = computed(() =>
  visualSensorStore.unityBridgeReadyFor('MISSION_CENTER')
  && missionVisualStats.value?.active === true
  && visualSensorStore.runtimeContextFor('MISSION_CENTER').runId === runId.value,
)
const unityRunSynchronized = computed(() =>
  unityChannel.value.appliedRunId === runId.value
  && unityChannel.value.sentSequence > 0
  && unityChannel.value.appliedSequence === unityChannel.value.sentSequence,
)
const externalAlgorithm = computed(() => !!detail.value && ['GB_SFLA_CS', 'ESCORT_GUARD'].includes(detail.value.mission.algorithmCode))
let algorithmPollTimer: number | null = null
let algorithmAbortController: AbortController | null = null
let loadedScenarioKey = ''
let poseFreshnessTimer: number | null = null
let unityPoseSequence = 0
let unitySenderRunId: number | null = null
let lastMergedPoseFingerprint = ''
const poseFreshnessNow = ref(Date.now())
let algorithmRecoveryPromise: Promise<void> | null = null

function missionVisualContext(): VisualSensorRuntimeContext {
  return {
    runtimeScope: 'MISSION_CENTER',
    runtimeInstanceId: unityViewportStore.missionInstanceId,
    missionId: detail.value?.mission.id ?? (Number.isFinite(missionId.value) ? missionId.value : null),
    runId: detail.value?.currentRun?.id ?? (Number.isFinite(runId.value) ? runId.value : null),
  }
}

function cameraIdForDevice(deviceCode: string) {
  return deviceCode.trim().toLowerCase().replace(/-/g, '_')
}

function sendMissionVisualSubscription(
  enabled: boolean,
  displayMode: 'grid' | 'focus' | 'off' = visualDisplayMode.value,
) {
  const context = missionVisualContext()
  if (context.missionId === null || context.runId === null) return
  visualSensorStore.bindRuntime(context)
  const focusedCameraId = cameraIdForDevice(selectedDeviceCode.value || 'uav-01')
  void visualSensorStore.selectFor('MISSION_CENTER', focusedCameraId)
  unityBridgeStore.sendFor('MISSION_CENTER', 'visualSensorSubscribe', {
    enabled,
    missionId: context.missionId,
    runId: context.runId,
    runtimeInstanceId: context.runtimeInstanceId,
    focusedCameraId,
    displayMode,
    quality: '720p',
    targetFps: 30,
    gpuDirect: true,
    jpegFallback: false,
    thumbnailFps: 0.2,
    focusedFps: 1,
  })
}

function selectDevice(deviceCode: string) {
  selectedDeviceCode.value = deviceCode
  if (mode.value !== 'vision') return
  visualDisplayMode.value = 'focus'
  sendMissionVisualSubscription(true, 'focus')
}

function showVisualGrid() {
  visualDisplayMode.value = 'grid'
  sendMissionVisualSubscription(true, 'grid')
}

function requestedViewMode(): '2d' | '3d' | 'vision' {
  return route.query.view === 'vision'
    ? 'vision'
    : route.query.view === '3d'
      ? '3d'
      : '2d'
}

function currentAlgorithmConfig() {
  return Object.fromEntries((detail.value?.parameters ?? []).map(item => [item.key, item.value ?? '']))
}

async function ensureAlgorithmRuntime() {
  if (!externalAlgorithm.value || !detail.value?.currentRun) return
  if (algorithmRecoveryPromise) return algorithmRecoveryPromise
  algorithmRecoveryPromise = (async () => {
    const mission = detail.value!.mission
    const activeRun = detail.value!.currentRun!
    const runtime = await prepareAlgorithmRun(activeRun.id, mission.algorithmCode, currentAlgorithmConfig())
    if (mission.status === 'RUNNING' && runtime.state !== 'RUNNING') {
      await controlAlgorithmRun(activeRun.id, 'start')
    }
  })()
  try {
    await algorithmRecoveryPromise
  } finally {
    algorithmRecoveryPromise = null
  }
}

function ensureMissionScenarioLoaded() {
  if (!unityChannel.value.controlsReady) {
    loadedScenarioKey = ''
    return
  }
  const mission = detail.value?.mission
  const currentRun = detail.value?.currentRun
  if (!mission || !currentRun || !['GB_SFLA_CS', 'ESCORT_GUARD'].includes(mission.algorithmCode)) return
  const key = `${mission.id}:${currentRun.id}:${mission.algorithmCode}:${unityViewportStore.missionInstanceId}`
  if (loadedScenarioKey === key) return
  unityBridgeStore.clearPoseFramesFor('MISSION_CENTER')
  unityBridgeStore.sendFor('MISSION_CENTER', 'loadScenario', {
    algorithmCode: mission.algorithmCode,
    missionId: mission.id,
    runId: currentRun.id,
  })
  loadedScenarioKey = key
  sendMergedPoseFrameToUnity(true)
}

const applicablePoseBatch = computed(() => {
  const envelope = realtimeStore.poseBatch
  if (!isRealtimeEnvelopeApplicable(envelope, { missionId: detail.value?.mission.id ?? missionId.value, runId: detail.value?.currentRun?.id ?? runId.value }, 'OBSERVATION')) return null
  return envelope
})

const applicableTargetBatch = computed(() => {
  const envelope = realtimeStore.targetBatch
  if (!isRealtimeEnvelopeApplicable(envelope, { missionId: detail.value?.mission.id ?? missionId.value, runId: detail.value?.currentRun?.id ?? runId.value }, 'OBSERVATION')) return null
  return envelope
})

const mergedAuthoritativeFrame = computed(() => mergeAuthoritativeFrame(
  algorithmFrame.value,
  applicablePoseBatch.value,
  applicableTargetBatch.value,
  poseFreshnessNow.value,
  3000,
  { missionId: detail.value?.mission.id ?? missionId.value, runId: detail.value?.currentRun?.id ?? runId.value },
))

const trajectoryFrame = computed(() =>
  algorithmFrameToTrajectoryFrame(mergedAuthoritativeFrame.value),
)

const runtimeNodes = computed<RuntimeNode[]>(() => {
  const frame = trajectoryFrame.value
  if (!frame) return monitoringStore.nodes.filter(node => node.type === 'UAV' || node.type === 'USV')
  return frame.agents
    .filter(agent => agent.type === 'UAV' || agent.type === 'USV')
    .map((agent, index) => {
      const existing = monitoringStore.nodes.find(node => node.code.toLowerCase() === agent.code.toLowerCase())
      return {
        id: existing?.id ?? -(index + 1),
        code: agent.code,
        name: existing?.name ?? `协同${agent.type === 'UAV' ? '无人机' : '无人艇'} ${agent.code.replace(/[^0-9]/g, '')}`,
        type: agent.type as 'UAV' | 'USV',
        status: 'ONLINE',
        host: null,
        port: null,
        endpoint: frame.coordinateSystem === 'ROS_ENU' ? 'ros-gateway-v1://pose-batch' : 'unity://mission-center',
        rosNamespace: null,
        lastHeartbeatAt: new Date(frame.receivedAt).toISOString(),
        heartbeatAgeSeconds: Math.max(0, Math.round((Date.now() - frame.receivedAt) / 1000)),
        source: frame.coordinateSystem === 'ROS_ENU' ? 'ROS_GATEWAY_V1' : 'UNITY_WEBGL',
        instanceId: unityViewportStore.missionInstanceId,
        positionX: agent.x,
        positionY: agent.y,
        positionZ: agent.z,
        controlOperationalState: existing?.controlOperationalState,
        controlStateFresh: existing?.controlStateFresh,
        controlStateReceivedAt: existing?.controlStateReceivedAt,
        controlConnectionState: existing?.controlConnectionState,
        detail: agent.state,
      }
    })
})

watch(trajectoryFrame, frame => {
  if (!frame) return
  const next = { ...operationalStates.value }
  for (const agent of frame.agents) {
    if (agent.type === 'UAV' || agent.type === 'USV') next[agent.code.toLowerCase()] = agent.state
  }
  operationalStates.value = next
  if (!runtimeNodes.value.some(node => node.code.toLowerCase() === selectedDeviceCode.value.toLowerCase())) {
    selectDevice(runtimeNodes.value[0]?.code ?? '')
  }
}, { immediate: true })

function missionUnityCommand(action: MissionAction) {
  return { start: 'missionStart', pause: 'missionPause', resume: 'missionResume', complete: 'missionComplete', fail: 'missionFail', cancel: 'missionCancel', ready: 'missionResume' }[action]
}

function vehicleUnityCommand(commandType: RuntimeCommandType) {
  const commands: Partial<Record<RuntimeCommandType, string>> = {
    UAV_TAKEOFF: 'uavTakeoff', UAV_HOVER: 'uavHover', UAV_RESUME: 'uavResume', UAV_RETURN: 'uavReturn', UAV_LAND: 'uavLand', UAV_EMERGENCY_LAND: 'uavEmergencyLand',
    USV_DEPART: 'usvDepart', USV_HOLD: 'usvHold', USV_RESUME: 'usvResume', USV_RETURN: 'usvReturn', USV_STOP: 'usvStop', USV_EMERGENCY_STOP: 'usvEmergencyStop',
  }
  return commands[commandType] ?? commandType.toLowerCase()
}

async function loadDetail() {
  if (!Number.isFinite(missionId.value) || !Number.isFinite(runId.value)) throw new Error('任务运行地址无效')
  const loaded = await fetchMission(missionId.value)
  const requestedRun = loaded.runs.find(run => run.id === runId.value) ?? (loaded.currentRun?.id === runId.value ? loaded.currentRun : null)
  if (!requestedRun) throw new Error('未找到该任务运行批次')
  loaded.currentRun = requestedRun
  const runChanged = realMissionRuntimeStore.currentRunId !== requestedRun.id
  if (algorithmFrame.value?.runId !== requestedRun.id) {
    algorithmFrame.value = null
    loadedScenarioKey = ''
    unityPoseSequence = 0
    unitySenderRunId = null
    lastMergedPoseFingerprint = ''
    trajectoryStore.clearFor('MISSION_CENTER')
    unityBridgeStore.clearPoseFramesFor('MISSION_CENTER')
  }
  detail.value = loaded
  realMissionRuntimeStore.syncContext({
    missionId: loaded.mission.id,
    runId: requestedRun.id,
    backendMissionStatus: loaded.mission.status,
    runScopePolicy: 'STRICT',
  })
  if (runChanged) await realtimeStore.refreshSnapshot()
  sessionStore.bind(loaded.mission.id, requestedRun.id)
  unityViewportStore.prepareMission(loaded.mission.id, requestedRun.id, requestedRun.runtimeInstanceId)
  visualSensorStore.bindRuntime(missionVisualContext())
}

async function refreshUntilStatus(expected: string) {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    await loadDetail()
    if (detail.value?.mission.status === expected) return
    await new Promise(resolve => window.setTimeout(resolve, 400))
  }
}

function sendMergedPoseFrameToUnity(force = false) {
  const frame = mergedAuthoritativeFrame.value
  const run = detail.value?.currentRun
  if (!frame || !run || !externalAlgorithm.value || !unityChannel.value.controlsReady) return
  const algorithmCode = frame.mode === 'ALGORITHM_MERGED' ? frame.algorithmCode : detail.value?.mission.algorithmCode
  if (!algorithmCode) return
  if (unitySenderRunId !== run.id) {
    unitySenderRunId = run.id
    unityPoseSequence = 0
    lastMergedPoseFingerprint = ''
  }
  const sourcePayload = {
    algorithmCode,
    runId: run.id,
    timestamp: frame.timestamp,
    phase: frame.phase ?? 'WAITING_FOR_ALGORITHM',
    agents: frame.agents,
    targets: frame.targets,
    route: frame.route.map(point => ({ x: point[0], y: point[1] })),
    obstacles: frame.obstacles,
  }
  const fingerprint = JSON.stringify(sourcePayload)
  if (!force && fingerprint === lastMergedPoseFingerprint) return
  lastMergedPoseFingerprint = fingerprint
  unityPoseSequence += 1
  unityBridgeStore.sendFor('MISSION_CENTER', 'poseFrame', {
    ...sourcePayload,
    sequence: unityPoseSequence,
  })
}

function ingestAlgorithmFrame(frame: AlgorithmRuntimeFrame) {
  if (frame.runId !== runId.value || frame.runId !== detail.value?.currentRun?.id) return
  if (algorithmFrame.value && frame.sequence <= algorithmFrame.value.sequence) return
  algorithmFrame.value = frame
}

watch([
  () => unityChannel.value.controlsReady,
  () => detail.value?.mission.algorithmCode,
  () => detail.value?.currentRun?.id,
  () => unityViewportStore.missionInstanceId,
], ensureMissionScenarioLoaded, { immediate: true })

watch([
  mergedAuthoritativeFrame,
  () => unityChannel.value.controlsReady,
  () => detail.value?.currentRun?.id,
], () => sendMergedPoseFrameToUnity(), { immediate: true })

watch(
  () => unityChannel.value.scenarioReadyRunId,
  readyRunId => {
    if (readyRunId === detail.value?.currentRun?.id) sendMergedPoseFrameToUnity(true)
  },
)

async function pollAlgorithmFrames() {
  if (!externalAlgorithm.value || !detail.value?.currentRun || algorithmPolling.value) return
  algorithmPolling.value = true
  algorithmAbortController = new AbortController()
  try {
    const frames = await fetchAlgorithmFrames(
      detail.value.currentRun.id,
      algorithmFrame.value?.sequence ?? 0,
      algorithmAbortController.signal,
    )
    for (const frame of frames) ingestAlgorithmFrame(frame)
  } catch {
    // A transient poll failure must not interrupt the task or flood the UI.
  } finally {
    algorithmAbortController = null
    algorithmPolling.value = false
  }
}

function stopAlgorithmPolling() {
  if (algorithmPollTimer !== null) window.clearInterval(algorithmPollTimer)
  algorithmPollTimer = null
  algorithmAbortController?.abort()
  algorithmAbortController = null
}

function startAlgorithmPolling(forceRunning = false) {
  stopAlgorithmPolling()
  if (!externalAlgorithm.value) return
  void pollAlgorithmFrames()
  if (!forceRunning && detail.value?.mission.status !== 'RUNNING') return
  algorithmPollTimer = window.setInterval(() => void pollAlgorithmFrames(), 500)
}

async function runMissionAction(action: 'pause' | 'resume' | 'complete' | 'cancel') {
  if (!detail.value) return
  if (action === 'cancel') {
    try {
      await ElMessageBox.confirm('确认终止当前任务运行？该操作只影响任务中心，不影响系统总览。', '终止任务', { type: 'warning', confirmButtonText: '确认终止', cancelButtonText: '取消' })
    } catch { return }
  }
  busy.value = true
  try {
    const activeRunId = detail.value.currentRun?.id
    const usesExternalAlgorithm = externalAlgorithm.value
    const result = await executeMissionAction(detail.value.mission.id, action, 'MISSION_CONTROL', unityViewportStore.missionInstanceId)
    if (result.command) {
      if (result.command.status === 'FAILED' || result.command.status === 'TIMEOUT') throw new Error(result.command.detail || '任务指令创建失败')
      const rosStatus = await realtimeStore.waitForCommandResult(result.command.commandKey)
      if (rosStatus !== 'SUCCEEDED') throw new Error(rosStatus)
    }
    let algorithmControlWarning = ''
    if (activeRunId && usesExternalAlgorithm) {
      try {
        if (action === 'pause') await controlAlgorithmRun(activeRunId, 'pause')
        if (action === 'resume') {
          await ensureAlgorithmRuntime()
          await controlAlgorithmRun(activeRunId, 'resume')
        }
        if (action === 'complete') await controlAlgorithmRun(activeRunId, 'stop')
        if (action === 'cancel') await controlAlgorithmRun(activeRunId, 'cancel')
      } catch (error) {
        // Unity/backend acknowledgement is authoritative for mission state. A
        // finished or unavailable algorithm worker must not turn that success
        // into a misleading command failure.
        algorithmControlWarning = error instanceof Error ? error.message : '算法运行实例未响应'
      }
    }
    if (action === 'pause') {
      sessionStore.pause()
      stopAlgorithmPolling()
    }
    if (action === 'resume') {
      sessionStore.resume(trajectoryFrame.value?.sequence ?? 0)
      startAlgorithmPolling(true)
    }
    if (action === 'complete' || action === 'cancel') {
      sessionStore.stop()
      stopAlgorithmPolling()
    }
    const expectedStatus = action === 'pause'
      ? 'PAUSED'
      : action === 'resume'
        ? 'RUNNING'
        : action === 'complete'
          ? 'COMPLETED'
          : 'CANCELLED'
    // The backend applies mission state from the Unity acknowledgement event.
    // Reflect that acknowledged state immediately, then reconcile with the
    // persisted mission record in the background.
    detail.value = {
      ...detail.value,
      mission: { ...detail.value.mission, status: expectedStatus as typeof detail.value.mission.status },
      currentRun: detail.value.currentRun
        ? { ...detail.value.currentRun, status: expectedStatus as typeof detail.value.currentRun.status }
        : null,
    }
    void refreshUntilStatus(expectedStatus).catch(() => undefined)
    if (algorithmControlWarning) ElMessage.warning(`任务状态已更新；算法实例提示：${algorithmControlWarning}`)
    else ElMessage.success(action === 'pause' ? '任务已暂停' : action === 'resume' ? '任务已继续' : action === 'complete' ? '任务已完成' : '任务已终止')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '任务指令执行失败')
  } finally { busy.value = false }
}

async function placeThreat(x: number, y: number) {
  if (detail.value?.mission.algorithmCode !== 'ESCORT_GUARD' || !detail.value.currentRun) return
  try {
    await placeEscortThreat(detail.value.currentRun.id, x, y)
    ElMessage.success(`威胁目标已更新：${x.toFixed(1)}, ${y.toFixed(1)}`)
  } catch (error) { ElMessage.error(error instanceof Error ? error.message : '威胁目标更新失败') }
}

async function sendVehicleCommand(command: VehicleQuickCommand) {
  if (!detail.value?.currentRun || !command.deviceCodes.length) return
  const manageBusy = command.commandType !== 'USV_STOP' && command.commandType !== 'USV_EMERGENCY_STOP'
  if (manageBusy) busy.value = true
  let acknowledged = 0
  try {
    for (const code of command.deviceCodes) {
      const key = code.toLowerCase()
      commandFeedback.value = { ...commandFeedback.value, [key]: 'PENDING' }
      try {
        const result = await issueSingleDeviceCommand({
          commandType: command.commandType,
          runId: detail.value.currentRun.id,
          deviceCode: key,
          payload: JSON.stringify({ source: 'MISSION_CONTROL', action: vehicleUnityCommand(command.commandType) }),
          detail: `${command.label} / ${key}`,
          runtimeScope: 'MISSION_CENTER',
          runtimeInstanceId: unityViewportStore.missionInstanceId,
        })
        if (result.status === 'FAILED' || result.status === 'TIMEOUT') throw new Error(result.detail)
        const rosStatus = await realtimeStore.waitForCommandResult(result.commandKey)
        const status: RuntimeCommandStatus = rosStatus === 'SUCCEEDED' ? 'SUCCEEDED'
          : rosStatus === 'CANCELLED' ? 'CANCELLED'
            : rosStatus === 'TIMEOUT' || rosStatus === 'EXPIRED' ? 'TIMEOUT' : 'FAILED'
        commandFeedback.value = { ...commandFeedback.value, [key]: status }
        if (status === 'SUCCEEDED') acknowledged += 1
      } catch {
        commandFeedback.value = { ...commandFeedback.value, [key]: 'FAILED' }
      }
    }
    if (acknowledged === command.deviceCodes.length) ElMessage.success(`${command.label}：${acknowledged}/${command.deviceCodes.length} 台已确认`)
    else ElMessage.error(`${command.label}：成功 ${acknowledged}，失败 ${command.deviceCodes.length - acknowledged}`)
  } finally { if (manageBusy) busy.value = false }
}

function changeMode(next: '2d' | '3d' | 'vision') {
  const leavingVision = mode.value === 'vision' && next !== 'vision'
  mode.value = next
  if (leavingVision) sendMissionVisualSubscription(false, 'off')
  if (next === 'vision') {
    visualDisplayMode.value = 'grid'
    unityViewportStore.show('mission-execution')
    sendMissionVisualSubscription(true, 'grid')
    return
  }
  if (next === '3d') {
    unityViewportStore.show('mission-execution')
    return
  }
  unityViewportStore.park()
}

async function closeExecution() {
  unityViewportStore.park()
  await router.push({ name: 'missions' })
}

onMounted(async () => {
  poseFreshnessTimer = window.setInterval(() => {
    poseFreshnessNow.value = Date.now()
  }, 500)
  unityViewportStore.park()
  realtimeStore.connect()
  monitoringStore.connectEvents()
  await monitoringStore.refresh({}, true)
  try {
    await loadDetail()
    if (externalAlgorithm.value && ['RUNNING', 'PAUSED'].includes(detail.value?.mission.status ?? '')) {
      await ensureAlgorithmRuntime()
    }
    startAlgorithmPolling()
    changeMode(requestedViewMode())
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '任务运行加载失败')
    await router.replace({ name: 'missions' })
  }
})
onBeforeUnmount(() => {
  stopAlgorithmPolling()
  if (poseFreshnessTimer !== null) window.clearInterval(poseFreshnessTimer)
  poseFreshnessTimer = null
  if (mode.value === 'vision') sendMissionVisualSubscription(false, 'off')
  visualSensorStore.disposeFrames('MISSION_CENTER')
  unityViewportStore.park()
})
</script>

<template>
  <MissionExecutionOverlay
    v-if="detail"
    :detail="detail"
    :nodes="runtimeNodes"
    :trajectory-frame="trajectoryFrame"
    :algorithm-frame="mergedAuthoritativeFrame"
    :session-state="sessionStore.state"
    :session-revision="sessionStore.revision"
    :selected-device-code="selectedDeviceCode"
    :feedback="commandFeedback"
    :operational-states="operationalStates"
    :mode="mode"
    :visual-display-mode="visualDisplayMode"
    :visual-connected="missionVisualConnected"
    :unity-run-synchronized="unityRunSynchronized"
    :busy="busy"
    @close="closeExecution"
    @select="selectDevice"
    @vehicle-command="sendVehicleCommand"
    @mission-action="runMissionAction"
    @events="eventVisible = true"
    @mode-change="changeMode"
    @visual-grid="showVisualGrid"
    @place-threat="placeThreat"
  />
  <MissionEventDrawer v-model="eventVisible" :mission-id="detail?.mission.id ?? null" :run-id="detail?.currentRun?.id" />
</template>

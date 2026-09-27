<script setup lang="ts">
import {
  computed,
  onActivated,
  onBeforeUnmount,
  onDeactivated,
  onMounted,
  ref,
  watch,
} from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute } from 'vue-router'
import {
  Layers3,
} from '@lucide/vue'

import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import AlgorithmTrajectoryMap from '@/components/mission/CooperativeSituationHud.vue'
import {
  fetchAlgorithmFrames,
  placeEscortThreat,
} from '@/api/algorithm'
import { ApiClientError } from '@/api/http'
import { fetchMission } from '@/api/mission'
import { useMissionStore } from '@/stores/mission'
import { useActiveExperimentStore } from '@/stores/activeExperiment'
import { useMissionTrajectorySessionStore } from '@/stores/missionTrajectorySession'
import { useMonitoringStore } from '@/stores/monitoring'
import { useRealtimeStore } from '@/stores/realtime'
import { useRealMissionRuntimeStore } from '@/stores/realMissionRuntime'
import { useTrajectoryStore } from '@/stores/trajectory'
import { useUnityBridgeStore } from '@/stores/unityBridge'
import { useUnityViewportStore } from '@/stores/unityViewport'
import {
  algorithmFrameToTrajectoryFrame,
  isRealtimeEnvelopeApplicable,
  isPoseBatchLive,
  mergeAuthoritativeFrame,
} from '@/services/realtimeTrajectoryAdapter'
import type { AlgorithmRuntimeFrame, Mission, MissionDetail } from '@/types/mission'
import type { RuntimeNode } from '@/types/monitoring'

const route = useRoute()
const missionStore = useMissionStore()
const activeExperimentStore = useActiveExperimentStore()
const monitoringStore = useMonitoringStore()
const realtimeStore = useRealtimeStore()
const realMissionRuntimeStore = useRealMissionRuntimeStore()
const trajectoryStore = useTrajectoryStore()
const unityBridgeStore = useUnityBridgeStore()
const sessionStore = useMissionTrajectorySessionStore()
const unityViewportStore = useUnityViewportStore()

const detail = ref<MissionDetail | null>(null)
const selectedDeviceCode = ref('')
const loading = ref(true)
const algorithmFrame = ref<AlgorithmRuntimeFrame | null>(null)
const algorithmPolling = ref(false)
type AlgorithmFrameState = 'WAITING_FIRST_FRAME' | 'NO_RUNTIME' | 'POLL_FAILED' | 'HISTORY_UNAVAILABLE' | 'LIVE'
const algorithmFrameState = ref<AlgorithmFrameState>('WAITING_FIRST_FRAME')

const unityChannel = computed(() => unityBridgeStore.channels.MISSION_CENTER)
const currentRunId = computed(() => detail.value?.currentRun?.id ?? null)
const activeAlgorithmCode = computed(() => detail.value?.mission.algorithmCode ?? '')
const activeMission = computed(() => detail.value?.mission ?? null)
const activeRun = computed(() => ['RUNNING', 'PAUSED'].includes(activeMission.value?.status ?? ''))
const terminalRun = computed(() =>
  ['CANCELLED', 'COMPLETED', 'FAILED'].includes(detail.value?.currentRun?.status ?? activeMission.value?.status ?? ''),
)
const algorithmFrameStatusText = computed(() => ({
  WAITING_FIRST_FRAME: '等待算法首帧',
  NO_RUNTIME: '当前 RUN 无算法运行实例',
  POLL_FAILED: '算法帧轮询失败',
  HISTORY_UNAVAILABLE: '算法历史帧不可用',
  LIVE: '算法帧实时',
})[algorithmFrameState.value])
const workspaceRuntimeState = computed(() => realMissionRuntimeStore.runtimeState)
const rosOnline = computed(() =>
  realtimeStore.connected && isPoseBatchLive(realtimeStore.poseBatch, poseFreshnessNow.value),
)
const onlineVehicleCount = computed(() =>
  runtimeNodes.value.filter(node => node.status === 'ONLINE').length,
)
const statusLabel = computed(() => {
  const runtimeLabels: Record<string, string> = {
    IDLE: '待执行',
    READY: '待执行',
    STARTING: '启动中',
    RUNNING: '运行中',
    CANCELLING: '终止中',
    CANCELLED: '已终止',
    FAILED: '异常',
    COMPLETED: '已完成',
  }
  if (runtimeLabels[workspaceRuntimeState.value]) return runtimeLabels[workspaceRuntimeState.value]
  const status = activeMission.value?.status
  if (status === 'RUNNING') return '运行中'
  if (status === 'PAUSED') return '已暂停'
  if (status === 'COMPLETED') return '已完成'
  if (status === 'FAILED') return '异常'
  if (status === 'CANCELLED') return '已终止'
  return '待执行'
})
const runSyncText = computed(() => {
  if (!detail.value?.currentRun) return mergedAuthoritativeFrame.value?.mode === 'OBSERVATION_ONLY'
    ? 'OBSERVATION_2D_ONLY_NO_RUN'
    : '等待创建 RUN'
  return `RUN ${detail.value.currentRun.runNo} · 同步帧 ${displayAlgorithmFrame.value?.sequence ?? 0}`
})

let algorithmPollTimer: number | null = null
let algorithmAbortController: AbortController | null = null
let loadedScenarioKey = ''
let poseFreshnessTimer: number | null = null
let unityPoseSequence = 0
let unitySenderRunId: number | null = null
let lastMergedPoseFingerprint = ''
const poseFreshnessNow = ref(Date.now())

const applicablePoseBatch = computed(() => {
  const envelope = realtimeStore.poseBatch
  if (!isRealtimeEnvelopeApplicable(envelope, { missionId: activeMission.value?.id, runId: currentRunId.value }, 'OBSERVATION')) return null
  return envelope
})

const applicableTargetBatch = computed(() => {
  const envelope = realtimeStore.targetBatch
  if (!isRealtimeEnvelopeApplicable(envelope, { missionId: activeMission.value?.id, runId: currentRunId.value }, 'OBSERVATION')) return null
  return envelope
})

const mergedAuthoritativeFrame = computed(() => mergeAuthoritativeFrame(
  algorithmFrame.value,
  applicablePoseBatch.value,
  applicableTargetBatch.value,
  poseFreshnessNow.value,
  3000,
  { missionId: activeMission.value?.id, runId: currentRunId.value },
))

const trajectoryFrame = computed(() =>
  algorithmFrameToTrajectoryFrame(mergedAuthoritativeFrame.value),
)
const displayAlgorithmFrame = mergedAuthoritativeFrame

const runtimeNodes = computed<RuntimeNode[]>(() => {
  const frame = trajectoryFrame.value
  if (!frame) {
    return monitoringStore.nodes.filter(node => node.type === 'UAV' || node.type === 'USV')
  }
  return frame.agents
    .filter(agent => agent.type === 'UAV' || agent.type === 'USV')
    .map((agent, index) => {
      const existing = monitoringStore.nodes.find(
        node => node.code.toLowerCase() === agent.code.toLowerCase(),
      )
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
        detail: agent.state,
      }
    })
})

function queryNumber(value: unknown) {
  const parsed = Number(Array.isArray(value) ? value[0] : value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

function clearRunFrames() {
  algorithmFrame.value = null
  algorithmFrameState.value = 'WAITING_FIRST_FRAME'
  loadedScenarioKey = ''
  unityPoseSequence = 0
  unitySenderRunId = null
  lastMergedPoseFingerprint = ''
  trajectoryStore.clearFor('MISSION_CENTER')
  unityBridgeStore.clearPoseFramesFor('MISSION_CENTER')
}

function sendMergedPoseFrameToUnity(force = false) {
  const frame = mergedAuthoritativeFrame.value
  const run = detail.value?.currentRun
  if (!frame || !run || !unityChannel.value.controlsReady) return
  const algorithmCode = frame.mode === 'ALGORITHM_MERGED' ? frame.algorithmCode : detail.value?.mission.algorithmCode
  if (!algorithmCode || !['GB_SFLA_CS', 'ESCORT_GUARD'].includes(algorithmCode)) return
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

function ensureMissionScenarioLoaded() {
  if (!unityChannel.value.controlsReady) {
    loadedScenarioKey = ''
    return
  }
  const mission = activeMission.value
  const run = detail.value?.currentRun
  if (!mission || !run || !['GB_SFLA_CS', 'ESCORT_GUARD'].includes(mission.algorithmCode)) return
  const key = `${mission.id}:${run.id}:${mission.algorithmCode}:${unityViewportStore.missionInstanceId}`
  if (loadedScenarioKey === key) return
  unityBridgeStore.clearPoseFramesFor('MISSION_CENTER')
  unityBridgeStore.sendFor('MISSION_CENTER', 'loadScenario', {
    algorithmCode: mission.algorithmCode,
    missionId: mission.id,
    runId: run.id,
  })
  loadedScenarioKey = key
  sendMergedPoseFrameToUnity(true)
}

function ingestAlgorithmFrame(frame: AlgorithmRuntimeFrame) {
  if (frame.runId !== currentRunId.value) return
  if (algorithmFrame.value && frame.sequence <= algorithmFrame.value.sequence) return
  algorithmFrame.value = frame
}

async function pollAlgorithmFrames() {
  if (!currentRunId.value || algorithmPolling.value) return
  algorithmPolling.value = true
  const controller = new AbortController()
  algorithmAbortController = controller
  try {
    const frames = await fetchAlgorithmFrames(
      currentRunId.value,
      algorithmFrame.value?.sequence ?? 0,
      controller.signal,
    )
    frames.forEach(ingestAlgorithmFrame)
    algorithmFrameState.value = algorithmFrame.value ? 'LIVE' : 'WAITING_FIRST_FRAME'
  } catch (error) {
    if (controller.signal.aborted) return
    const previousState = algorithmFrameState.value
    if (error instanceof ApiClientError && error.status === 404) {
      algorithmFrameState.value = terminalRun.value ? 'HISTORY_UNAVAILABLE' : 'NO_RUNTIME'
    } else {
      algorithmFrameState.value = 'POLL_FAILED'
    }
    if (previousState !== algorithmFrameState.value) {
      console.error('[MissionWorkspace] algorithm frame polling failed', {
        runId: currentRunId.value,
        state: algorithmFrameState.value,
        error,
      })
    }
    // 短暂轮询失败不应终止 RUN，也不在页面连续弹出错误。
  } finally {
    if (algorithmAbortController === controller) algorithmAbortController = null
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
  if (!currentRunId.value) return
  void pollAlgorithmFrames()
  if (!forceRunning && activeMission.value?.status !== 'RUNNING') return
  algorithmPollTimer = window.setInterval(() => void pollAlgorithmFrames(), 100)
}

async function loadMissionWorkspace(
  mission: Mission,
  requestedRunId?: number | null,
  resumePolling = true,
) {
  stopAlgorithmPolling()
  const loaded = await fetchMission(mission.id)
  if (requestedRunId) {
    const requested = loaded.runs.find(run => run.id === requestedRunId)
    if (requested) loaded.currentRun = requested
  }
  if (loaded.currentRun?.id !== currentRunId.value) clearRunFrames()
  detail.value = loaded
  activeExperimentStore.sync(loaded)
  realMissionRuntimeStore.syncContext({
    missionId: loaded.mission.id,
    runId: loaded.currentRun?.id ?? null,
    backendMissionStatus: loaded.mission.status,
    runScopePolicy: 'ALLOW_MISSING',
  })
  sessionStore.bind(loaded.mission.id, loaded.currentRun?.id ?? null)
  unityViewportStore.prepareMission(
    loaded.mission.id,
    loaded.currentRun?.id ?? null,
    loaded.currentRun?.runtimeInstanceId,
  )
  if (loaded.currentRun && resumePolling) startAlgorithmPolling()
  ensureMissionScenarioLoaded()
}

async function refreshWorkspace(resumePolling = true) {
  loading.value = true
  try {
    await Promise.all([
      missionStore.refresh({ page: 0, size: 100 }),
      monitoringStore.refresh({}, true),
    ])
    const queryMissionId = queryNumber(route.query.missionId)
    const queryRunId = queryNumber(route.query.runId)
    const routedMission = queryMissionId
      ? missionStore.records.find(item => item.id === queryMissionId)
      : null
    const openMission = missionStore.records.find(item => ['RUNNING', 'PAUSED'].includes(item.status))
    const mission = routedMission
      ?? openMission
      ?? missionStore.records[0]
    if (!mission) throw new Error('暂无可观测的任务数据')
    await loadMissionWorkspace(mission, queryRunId, resumePolling)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '任务中心加载失败')
  } finally {
    loading.value = false
  }
}

function selectObservationDevice(deviceCode: string) {
  selectedDeviceCode.value = deviceCode
}

async function placeThreat(x: number, y: number) {
  if (activeAlgorithmCode.value !== 'ESCORT_GUARD' || !currentRunId.value) return
  try {
    await placeEscortThreat(currentRunId.value, x, y)
    ElMessage.success(`威胁目标已更新：${x.toFixed(1)}, ${y.toFixed(1)}`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '威胁目标更新失败')
  }
}

watch(
  [
    () => unityChannel.value.controlsReady,
    activeAlgorithmCode,
    currentRunId,
    () => unityViewportStore.missionInstanceId,
  ],
  ensureMissionScenarioLoaded,
  { immediate: true },
)

watch(
  [
    mergedAuthoritativeFrame,
    () => unityChannel.value.controlsReady,
    currentRunId,
  ],
  () => sendMergedPoseFrameToUnity(),
  { immediate: true },
)

watch(
  () => unityChannel.value.scenarioReadyRunId,
  readyRunId => {
    if (readyRunId === currentRunId.value) sendMergedPoseFrameToUnity(true)
  },
)

onMounted(async () => {
  poseFreshnessTimer = window.setInterval(() => {
    poseFreshnessNow.value = Date.now()
  }, 500)
  unityViewportStore.park()
  realtimeStore.connect()
  monitoringStore.connectEvents()
  await refreshWorkspace()
  unityViewportStore.park()
})

let resumeAfterDeactivation = false

onActivated(async () => {
  if (!resumeAfterDeactivation) return
  resumeAfterDeactivation = false
  realtimeStore.connect()
  monitoringStore.connectEvents()
  const previousRunId = currentRunId.value
  await refreshWorkspace(false)
  const nextRunId = currentRunId.value
  if (nextRunId && previousRunId !== nextRunId) {
    await realtimeStore.refreshSnapshot()
  }
  if (activeRun.value || currentRunId.value) startAlgorithmPolling()
  unityViewportStore.park()
})

onDeactivated(() => {
  resumeAfterDeactivation = true
  stopAlgorithmPolling()
  unityViewportStore.park()
})

onBeforeUnmount(() => {
  stopAlgorithmPolling()
  if (poseFreshnessTimer !== null) window.clearInterval(poseFreshnessTimer)
  poseFreshnessTimer = null
  unityViewportStore.park()
})
</script>

<template>
  <ConsoleLayout title="协同态势" eyebrow="COOPERATIVE SITUATION" :show-refresh="false">
    <template #actions>
      <div class="mission-health">
        <span><i :class="{ online: rosOnline }" />ROS {{ rosOnline ? '在线' : '离线' }}</span>
        <span><i :class="{ online: algorithmFrameState === 'LIVE' }" />{{ algorithmFrameStatusText }}</span>
        <span><i :class="{ online: onlineVehicleCount >= 6 }" />真实设备 {{ onlineVehicleCount }}/6</span>
      </div>
    </template>

    <section class="mission-workspace" :aria-busy="loading">
      <section class="execution-card">
        <header class="run-toolbar">
          <div class="mission-identity">
            <small>{{ activeMission?.code ?? 'MISSION' }} · RUN {{ detail?.currentRun?.runNo ?? '--' }}</small>
            <strong>{{ activeMission?.name ?? '任务中心运行工作台' }}</strong>
          </div>
          <div class="situation-view-label"><Layers3 :size="16" />二维轨迹 · 真实任务RUN实时数据</div>
          <span class="run-sync"><i />{{ runSyncText }}</span>
          <em class="read-only-badge" :class="activeMission?.status.toLowerCase()">{{ statusLabel }}</em>
        </header>

        <main class="execution-stage">
          <AlgorithmTrajectoryMap
            :frame="displayAlgorithmFrame"
            :algorithm-status-text="algorithmFrameStatusText"
            :selected-device-code="selectedDeviceCode"
            @select="selectObservationDevice"
            @place-threat="placeThreat"
          />
        </main>
      </section>

    </section>
  </ConsoleLayout>
</template>

<style scoped>
.situation-view-label {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: #66dfd4;
  font-size: 11px;
  font-weight: 800;
}

.situation-event-dock {
  display: none;
}

.mission-workspace {
  position: relative;
  display: grid;
  grid-template-rows: minmax(0, 1fr);
  gap: 10px;
  width: 100%;
  max-width: 2360px;
  height: calc(100dvh - 156px);
  min-height: 590px;
  margin: 0 auto;
  color: #dff5f3;
}

.mission-entry-cover {
  position: absolute;
  z-index: 110;
  inset: 0;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 10px;
  min-height: 420px;
  color: #dff8f5;
  border: 1px solid rgba(74, 193, 202, .22);
  border-radius: 8px;
  background: rgba(2, 16, 23, .98);
}

.mission-entry-cover small,
.mission-scene-cover span {
  color: #789da2;
}

.mission-loading-mark {
  width: 28px;
  height: 28px;
  border: 2px solid rgba(92, 218, 211, .2);
  border-top-color: #5cdad3;
  border-radius: 50%;
  animation: mission-spin .8s linear infinite;
}

@keyframes mission-spin {
  to { transform: rotate(360deg); }
}

.mission-health,
.algorithm-picker,
.algorithm-summary,
.mode-switch,
.run-actions,
.readiness,
.event-list {
  display: flex;
  align-items: center;
}

.mission-health {
  gap: 9px;
}

.mission-health span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 9px;
  color: #92b0b3;
  border: 1px solid rgba(77, 176, 194, .16);
  border-radius: 5px;
  background: rgba(7, 31, 41, .72);
  font-size: 10px;
}

.mission-health i,
.run-sync i,
.unity-badge i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #64797c;
}

.mission-health i.online,
.run-sync i,
.unity-badge i {
  background: #55e7a7;
  box-shadow: 0 0 7px rgba(85, 231, 167, .8);
}

.algorithm-toolbar,
.execution-card,
.event-dock {
  border: 1px solid rgba(76, 180, 202, .22);
  border-radius: 8px;
  background: linear-gradient(145deg, rgba(5, 27, 37, .97), rgba(3, 17, 25, .97));
}

.algorithm-toolbar {
  display: grid;
  grid-template-columns: minmax(450px, 1.25fr) minmax(280px, .75fr) 150px;
  align-items: center;
  gap: 14px;
  min-height: 66px;
  padding: 10px 14px;
}

.algorithm-picker {
  gap: 9px;
}

.algorithm-picker label {
  flex: 0 0 auto;
  color: #8db0b4;
  font-size: 11px;
  font-weight: 700;
}

.algorithm-picker :deep(.el-select) {
  width: min(390px, 48vw);
}

.algorithm-picker :deep(.el-select__wrapper) {
  min-height: 38px;
  color: #e7f7f6;
  background: #061b24;
  box-shadow: 0 0 0 1px #2a5660 inset;
}

.secondary-button,
.primary-button,
.run-actions button,
.mode-switch button,
.event-dock > button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: #d5ecea;
  border: 1px solid #2a5660;
  border-radius: 5px;
  background: #08232d;
  cursor: pointer;
}

.secondary-button {
  flex: 0 0 auto;
  height: 38px;
  padding: 0 12px;
}

.algorithm-summary {
  justify-content: flex-end;
  gap: 12px;
  color: #cfe4e3;
  font-size: 11px;
}

.algorithm-summary em {
  padding: 5px 9px;
  color: #ffc93e;
  border: 1px solid rgba(255, 201, 62, .38);
  border-radius: 4px;
  background: rgba(255, 201, 62, .07);
  font-style: normal;
}

.algorithm-summary em.running,
.algorithm-summary em.paused {
  color: #55e7a7;
  border-color: rgba(85, 231, 167, .35);
  background: rgba(85, 231, 167, .07);
}

.algorithm-summary em.failed,
.algorithm-summary em.cancelled {
  color: #ff7474;
  border-color: rgba(255, 116, 116, .35);
}

.primary-button {
  height: 42px;
  color: #02232a;
  border-color: #63e6e2;
  background: linear-gradient(135deg, #53e1df, #29bed1);
  font-weight: 900;
}

button:disabled {
  cursor: not-allowed;
  opacity: .42;
}

.execution-card {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: 0;
  overflow: hidden;
}

.run-toolbar {
  display: grid;
  grid-template-columns: minmax(260px, 1fr) auto minmax(190px, .55fr) auto;
  align-items: center;
  gap: 12px;
  min-height: 62px;
  padding: 8px 12px;
  border-bottom: 1px solid rgba(70, 164, 183, .18);
}

.read-only-badge {
  min-width: 70px;
  padding: 6px 10px;
  color: #d9f4f0;
  border: 1px solid #315d65;
  border-radius: 4px;
  background: #0a2630;
  font-size: 10px;
  font-style: normal;
  text-align: center;
}

.read-only-badge.running { color: #55e7a7; border-color: #246b57; }
.read-only-badge.paused { color: #ffd26a; border-color: #705c29; }
.read-only-badge.failed,
.read-only-badge.cancelled { color: #ff7777; border-color: #75383f; }

.mission-identity small,
.mission-identity strong {
  display: block;
}

.mission-identity small {
  color: #4bd2e5;
  font-size: 9px;
  letter-spacing: .04em;
}

.mission-identity strong {
  margin-top: 3px;
  overflow: hidden;
  color: #efffff;
  font-size: 14px;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.mode-switch {
  justify-content: center;
}

.mode-switch button {
  min-width: 126px;
  height: 36px;
  border-radius: 0;
}

.mode-switch button:first-child {
  border-radius: 5px 0 0 5px;
}

.mode-switch button:last-child {
  border-radius: 0 5px 5px 0;
}

.mode-switch button.active {
  color: #56e2df;
  border-color: #53dce0;
  background: #0a3540;
}

.run-sync {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  color: #55e7a7;
  font-size: 9px;
}

.run-actions {
  justify-content: flex-end;
  gap: 6px;
}

.run-actions button {
  min-height: 34px;
  padding: 0 10px;
}

.run-actions button.danger {
  color: #ff7474;
  border-color: #8c3940;
}

.execution-stage {
  position: relative;
  min-height: 0;
  overflow: hidden;
  background: #020f16;
}

.execution-stage :deep(.algorithm-map),
.execution-stage :deep(.mission-trajectory-map) {
  width: 100%;
  height: 100%;
}

.unity-viewport {
  position: absolute;
  inset: 0;
  background: #03131b;
}

.unity-viewport.vision {
  background: #02090d;
}

.mission-scene-cover {
  position: absolute;
  z-index: 100;
  inset: 0;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 10px;
  color: #dff8f5;
  background:
    radial-gradient(circle at 50% 45%, rgba(25, 113, 122, .16), transparent 35%),
    #03131b;
}

.mission-scene-cover svg {
  color: #58d8d2;
}

.unity-badge {
  position: absolute;
  z-index: 2;
  top: 12px;
  right: 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 9px;
  color: #d8f1ee;
  border: 1px solid rgba(92, 201, 202, .35);
  border-radius: 4px;
  background: rgba(2, 20, 28, .86);
  font-size: 9px;
  font-weight: 800;
}

.radar-panel {
  position: absolute;
  inset: 0;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  box-sizing: border-box;
  height: auto;
  padding: 12px;
  overflow: hidden;
  border: 1px solid rgba(76, 185, 197, .24);
  border-radius: 8px;
  background: rgba(5, 24, 31, .82);
}

.radar-panel header,
.radar-metrics,
.radar-row {
  display: grid;
  align-items: center;
}

.radar-panel header {
  grid-template-columns: 1fr auto 9px;
  gap: 12px;
  margin-bottom: 10px;
}

.radar-panel header span {
  color: #4cd6e9;
  font-size: 10px;
  font-weight: 900;
  letter-spacing: .13em;
}

.radar-panel header strong {
  color: #dff9f5;
  font-size: 13px;
}

.radar-panel header i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #607b7e;
}

.radar-panel header i.online {
  background: #5ce7b7;
  box-shadow: 0 0 9px rgba(92, 231, 183, .75);
}

.radar-metrics {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}

.radar-metrics article {
  min-width: 0;
  padding: 10px;
  border: 1px solid rgba(72, 145, 155, .18);
  border-radius: 6px;
  background: #071a21;
}

.radar-metrics span,
.radar-row span {
  color: #769a9b;
  font-size: 10px;
}

.radar-metrics strong {
  display: block;
  margin-top: 4px;
  overflow: hidden;
  color: #e7fffb;
  font-size: 14px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.radar-plot {
  position: relative;
  min-height: 0;
  margin-bottom: 10px;
  overflow: hidden;
  border: 1px solid rgba(72, 145, 155, .18);
  border-radius: 6px;
  background: linear-gradient(rgba(73, 160, 170, .07) 1px, transparent 1px), linear-gradient(90deg, rgba(73, 160, 170, .06) 1px, transparent 1px), #04161d;
  background-size: 24px 24px;
}

.radar-plot-empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: #628487;
  font-size: 11px;
}

.radar-table {
  border: 1px solid rgba(72, 145, 155, .18);
  border-radius: 6px;
  overflow: hidden;
}

.radar-row {
  grid-template-columns: 1.2fr 1fr 1fr 1fr;
  min-height: 32px;
  border-bottom: 1px solid rgba(72, 145, 155, .13);
}

.radar-row:last-child { border-bottom: 0; }

.radar-row span {
  min-width: 0;
  padding: 0 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.radar-row.head { background: rgba(76, 185, 197, .08); }
.radar-row.head span { color: #9ec3c3; font-weight: 900; }

.radar-empty {
  padding: 18px;
  color: #628487;
  font-size: 11px;
  text-align: center;
}

.visual-waiting {
  display: grid;
  height: 100%;
  place-content: center;
  justify-items: center;
  gap: 9px;
  color: #6f9599;
  background:
    radial-gradient(circle at center, rgba(37, 132, 150, .1), transparent 42%),
    #020f16;
}

.visual-waiting strong {
  color: #cfe5e3;
  font-size: 16px;
}

.visual-waiting span {
  font-size: 11px;
}

.event-dock {
  display: none;
  grid-template-columns: auto minmax(320px, 1fr) auto;
  align-items: center;
  gap: 12px;
  min-height: 70px;
  padding: 9px 12px;
}

.readiness {
  gap: 14px;
}

.readiness span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #82a5a8;
  font-size: 9px;
}

.readiness svg {
  color: #55e7a7;
}

.event-list {
  min-width: 0;
  gap: 7px;
}

.event-list article {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 7px;
  padding: 7px 9px;
  border: 1px solid rgba(72, 155, 172, .13);
  border-radius: 5px;
  background: rgba(7, 31, 41, .56);
}

.event-list article svg {
  flex: 0 0 auto;
  color: #4dcfe3;
}

.event-list time,
.event-list b {
  display: block;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.event-list time {
  color: #5f858a;
  font-size: 8px;
}

.event-list b {
  max-width: 180px;
  color: #cfe4e2;
  font-size: 9px;
}

.event-dock > button {
  height: 34px;
  padding: 0 12px;
  color: #54dce0;
}

@media (max-width: 1280px) {
  .algorithm-toolbar {
    grid-template-columns: minmax(390px, 1fr) auto 135px;
  }

  .run-toolbar {
    grid-template-columns: minmax(190px, 1fr) auto auto;
  }

  .run-sync {
    display: none;
  }

  .mode-switch button {
    min-width: 104px;
  }

  .event-dock {
    grid-template-columns: 1fr auto;
  }

  .event-list {
    display: none;
  }
}

@media (max-width: 920px) {
  .algorithm-toolbar {
    grid-template-columns: 1fr auto;
  }

  .algorithm-summary {
    display: none;
  }

  .run-toolbar {
    grid-template-columns: 1fr;
  }

  .mode-switch,
  .run-actions {
    justify-content: flex-start;
  }

}

@media (min-width: 1920px) {
  .mission-workspace {
    gap: 12px;
  }

  .algorithm-toolbar {
    grid-template-columns: minmax(520px, 1.3fr) minmax(320px, .7fr) 168px;
    padding-inline: 18px;
  }

  .run-toolbar {
    padding-inline: 16px;
  }

  .mode-switch button {
    min-width: 142px;
  }
}

@media (min-width: 1920px) and (min-height: 1000px) {
  .mission-workspace {
    grid-template-rows: minmax(0, 1fr);
  }

  .execution-stage {
    min-height: 0;
  }
}
.mission-workspace { max-width: none; min-height: 460px; height: max(460px, calc(100dvh - 156px)); }
.mission-identity { min-width: 0; overflow-wrap: anywhere; }
@container workspace (max-width: 1050px) {
  .run-toolbar { grid-template-columns: minmax(0, 1fr) auto; gap: 10px; }
  .situation-view-label { grid-column: 1 / -1; grid-row: 2; }
  .run-sync { display: none; }
}
@container workspace (max-width: 550px) {
  .run-toolbar { grid-template-columns: minmax(0, 1fr); }
  .situation-view-label { grid-row: auto; flex-wrap: wrap; }
  .read-only-badge { justify-self: start; }
}
</style>

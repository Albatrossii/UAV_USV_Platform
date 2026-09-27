<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import {
  ChevronLeft,
  ChevronRight,
  CircleStop,
  Eye,
  Globe2,
  Maximize2,
  Minimize2,
  Pause,
  Play,
  RefreshCw,
} from '@lucide/vue'

import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import { ApiClientError } from '@/api/http'
import VoiceP0ControlPanel from '@/components/voice/VoiceP0ControlPanel.vue'
import {
  simulationRuntime,
  enqueueTacticalNotices,
  acknowledgeTacticalFrame,
  resetTacticalNotices,
  type SimulationTacticalNotice,
} from '@/composables/simulationRuntime'
import {
  controlAlgorithmRun,
  fetchAlgorithmFrames,
  fetchAlgorithmRunStatus,
  prepareAlgorithmRun,
} from '@/api/algorithm'
import { useAuthStore } from '@/stores/auth'
import { useVoiceControlStore } from '@/stores/voiceControl'
import type { AlgorithmRuntimeFrame } from '@/types/mission'
import type { UnityPresentationOutgoing, VoiceMockRuntimeHint, VoiceRuntimeState } from '@/types/voiceControl'
import {
  adaptVirtualAlgorithmFrame,
  type EnuOrigin,
  type VirtualPoseStateMap,
} from '@/utils/virtualAlgorithmFrameAdapter'
import {
  buildVirtualFleetGridLayout,
  type GridScenarioPose,
} from '@/utils/virtualFleetGridLayout'
import { deriveAdaptiveScenarioPlan } from '@/utils/adaptiveScenarioPlan'

type UnityMessage = {
  type: string
  requestId?: string
  timestamp?: number
  payload?: Record<string, unknown>
}

type ScenarioInitialPose = {
  deviceCode: string
  deviceType?: string
  eastM: number
  northM: number
  upM: number
  headingDeg: number
  speedMps?: number
  state?: string
  valid?: boolean
}

type CaptureGroupMetric = {
  threatCode?: string
  state?: string
  stage?: number
  memberCount?: number
  uavCount?: number
  usvCount?: number
  arrivalRatio?: number
  maxAngularGapDeg?: number
  holdFrames?: number
  holdRequiredFrames?: number
  missionStage?: string
  pursuitDistanceM?: number
  requiredPursuitDistanceM?: number
  captureBlocker?: string
  postGlobalContainmentReady?: boolean
  postGlobalMaxGapDeg?: number
  postGlobalMaxAllowedGapDeg?: number
  globalAvoidanceCount?: number
  ringMemberCount?: number
  arrivedMemberCount?: number
  requiredMemberCount?: number
  detachedParticipantCodes?: string[]
  intent?: string
  intentConfidence?: number
  speedMps?: number
  targetSpeedMps?: number
  maximumSpeedMps?: number
  targetHeadingDeg?: number
  targetTravelDistanceM?: number
  targetSpeedReason?: string
  slowdownReason?: string
  gapCenterDeg?: number
  nearestInterceptorCode?: string
  nearestInterceptorDistanceM?: number
  assignmentStrategy?: string
  assignmentRevision?: number
  reassignmentCount?: number
  recentReassignments?: Array<{
    deviceCode?: string
    previousTarget?: string
    targetCode?: string
    reason?: string
    interceptEtaSec?: number
  }>
  triggerReason?: string
}

type InspectorTab = 'status' | 'voice' | 'protocol' | 'logs'

type TacticalEvent = SimulationTacticalNotice

type VirtualFleetRecoverySnapshot = {
  version: 1
  userScope: string
  savedAt: string
  state: {
    algorithm: string
    uavCount: number
    usvCount: number
    uavSpeed: number
    usvSpeed: number
    mission: string
    runId: number
    sequence: number
  }
  savedScenario: Record<string, unknown>
  latestPoseBatch: Record<string, unknown> | null
  initialScenarioPoses: ScenarioInitialPose[]
  plannedScenarioPoses: GridScenarioPose[]
  currentAlgorithmFrame: AlgorithmRuntimeFrame | null
  selectedDevice: string
  cameraMode: string
  missionElapsedMs: number
}

const authStore = useAuthStore()
const runtimeRecoveryUser = authStore.user?.username ?? ''
const runtimeRecoveryKey = `virtual-fleet.runtime-recovery.v1:${runtimeRecoveryUser}`

function loadRuntimeRecovery(): VirtualFleetRecoverySnapshot | null {
  if (!runtimeRecoveryUser) return null
  try {
    const raw = sessionStorage.getItem(runtimeRecoveryKey)
    if (!raw) return null
    const snapshot = JSON.parse(raw) as VirtualFleetRecoverySnapshot
    const active = ['RUNNING', 'PAUSED', 'COMPLETING', 'COMPLETED', 'STOPPED', 'FAILED', 'CANCELLED'].includes(snapshot.state?.mission)
    const fresh = Date.now() - Date.parse(snapshot.savedAt) < 12 * 60 * 60 * 1000
    return snapshot.version === 1
      && snapshot.userScope === runtimeRecoveryUser
      && active
      && fresh
      && Number.isSafeInteger(snapshot.state.runId)
      && snapshot.state.runId > 0
      && !!snapshot.savedScenario
      ? snapshot
      : null
  } catch {
    return null
  }
}

const restoredRuntime = loadRuntimeRecovery()

const unityPanel = simulationRuntime.panel
const recoveringScene = simulationRuntime.recovering
let savedScenario: Record<string, unknown> | null = restoredRuntime?.savedScenario ?? null
let latestPoseBatch: Record<string, unknown> | null = restoredRuntime?.latestPoseBatch ?? null
let recoveryTimer: number | undefined
let recoveryPoseSequence: number | null = null
let restoredCamera = { mode: 'overview', deviceCode: '' }
const unityReady = ref(false)
const selectedDevice = ref(restoredRuntime?.selectedDevice ?? '')
const cameraMode = ref(restoredRuntime?.cameraMode ?? 'overview')
const scenarioReadyRunId = ref<number | null>(null)
const scenarioLoading = ref(false)
const algorithmPrepared = ref(restoredRuntime !== null)
const algorithmPrepareError = ref('')
const algorithmPreparing = ref(false)
const missionActionMessage = ref('')
const webglExpanded = ref(false)
const leftPanelCollapsed = ref(false)
const rightPanelCollapsed = ref(false)
const panelTransitioning = ref(false)
const inspectorTab = ref<InspectorTab>('status')
const logEntries = ref<string[]>([])
const lastUnityMessage = ref<UnityMessage | null>(null)
const voiceControlPanel = ref<InstanceType<typeof VoiceP0ControlPanel> | null>(null)
const voiceControlStore = useVoiceControlStore()
const presentationUnityInstanceId = ref(crypto.randomUUID().toLowerCase())
const presentationSceneRevision = ref(0)
const currentAlgorithmFrame = ref<AlgorithmRuntimeFrame | null>(restoredRuntime?.currentAlgorithmFrame ?? null)
const tacticalHistory = simulationRuntime.tacticalHistory
const consumedTacticalEventIds = new Set<string>()
const initialScenarioPoses = ref<ScenarioInitialPose[]>(restoredRuntime?.initialScenarioPoses ?? [])
const plannedScenarioPoses = ref<GridScenarioPose[]>(restoredRuntime?.plannedScenarioPoses ?? [])
const sceneLocked = computed(() => (
  state.mission === 'RUNNING'
  || state.mission === 'PAUSED'
  || state.mission === 'COMPLETING'
))
const scenarioPlan = computed(() => deriveAdaptiveScenarioPlan(state.uavCount, state.usvCount))
const configuredTargetCount = computed(() => (
  state.algorithm === 'GB_SFLA_CS'
    ? scenarioPlan.value.threatCount
    : scenarioPlan.value.targetCount
))
const stageCompositionLabel = computed(() => state.algorithm === 'GB_SFLA_CS'
  ? `${state.uavCount} UAV · ${state.usvCount} USV · ${scenarioPlan.value.threatCount} 敌船`
  : `${state.uavCount} UAV · ${state.usvCount} USV · ${scenarioPlan.value.protectedCount} 护航目标 · ${scenarioPlan.value.threatCount} 敌船`)
const missionPhase = computed(() => String(
  currentAlgorithmFrame.value?.metrics?.missionStage
  || currentAlgorithmFrame.value?.phase
  || (state.mission === 'RUNNING' ? 'TRANSIT' : 'READY'),
))
const missionMetrics = computed(() => currentAlgorithmFrame.value?.metrics ?? {})
const stageSubjectThreatCode = computed(() => String(
  missionMetrics.value.stageSubjectThreatCode ?? '',
))
const missionStageLabels: Record<string, string> = {
  PREVIEW: '预演', READY: '就绪', GUARDING: '编队护航', ESCORTING: '编队护航',
  THREAT_DETECTION: '意图识别', GUARD_RECONFIGURATION: '分向守卫', INTERCEPT: '加速拦截', BLOCKING: '阻断攻击',
  ESCAPE: '目标逃逸', PURSUIT: '协同追击', ENCIRCLEMENT: '动态围捕',
  GAP_REPAIR: '动态围捕', STABLE_CONTAINMENT: '稳定闭环',
  SAFE_GATE_TRANSIT: '通过安全门', COMPLETED: '完成',
}
const missionPhaseLabel = computed(() => {
  const label = missionStageLabels[missionPhase.value.toUpperCase()] ?? missionPhase.value
  return stageSubjectThreatCode.value && missionPhase.value !== 'COMPLETED'
    ? `${label} · ${stageSubjectThreatCode.value}`
    : label
})
const visibleTargetCount = computed(() => currentAlgorithmFrame.value?.targets.filter(target => target.visible !== false).length ?? configuredTargetCount.value)
const displayMissionProgress = computed(() => {
  const raw = Math.max(0, Math.min(1, Number(
    missionMetrics.value.missionProgress ?? missionMetrics.value.progress ?? 0,
  )))
  const completed = state.mission === 'COMPLETED'
  // The terminal state is committed only after Unity acknowledges the final
  // pose frame. Treat it as authoritative: the preceding metrics frame can
  // legitimately still contain the non-terminal 0.99 sentinel.
  return completed ? 100 : Math.round(Math.min(raw, 0.99) * 100)
})
const escortProgress = computed(() => Math.round(Number(missionMetrics.value.escortProgress ?? 0) * 100))
const captureProgress = computed(() => Math.round(Number(missionMetrics.value.captureProgress ?? 0) * 100))
const postMissionFormationReadyCount = computed(() => Number(
  missionMetrics.value.postMissionFormationReadyCount ?? 0,
))
const postMissionFormationRequiredCount = computed(() => Number(
  missionMetrics.value.postMissionFormationRequiredCount ?? 0,
))
const postMissionFormationProgress = computed(() => Math.round(Number(
  missionMetrics.value.postMissionFormationProgress ?? 0,
) * 100))
const postMissionStableFrames = computed(() => Number(
  missionMetrics.value.convoySupportStableFrames ?? 0,
))
const postMissionRequiredStableFrames = computed(() => Number(
  missionMetrics.value.convoySupportRequiredStableFrames ?? 12,
))
const closeGuardCount = computed(() => Number(missionMetrics.value.closeGuardCount ?? 0))
const captureAssignedCount = computed(() => Number(missionMetrics.value.captureAssignedCount ?? 0))
const mobileSupportCount = computed(() => Number(missionMetrics.value.mobileSupportCount ?? 0))
const terminalBlockerLabel = computed(() => {
  const blocker = String(missionMetrics.value.terminalBlocker ?? '')
  if (!blocker || blocker === 'NONE' || blocker === 'MISSION_IN_PROGRESS') return ''
  if (blocker === 'THREATS_UNRESOLVED') return '仍有敌船未完成围捕'
  if (blocker === 'STABLE_CONTAINMENT_CONFIRMING') return '稳定闭环正在确认'
  if (blocker === 'SAFE_GATE_TRANSIT') return '护航目标正在通过安全门'
  if (blocker === 'PROTECTED_TARGET_NOT_SAFE') return '护航目标尚未通过安全门'
  if (blocker === 'CONTAINMENT_RECONFIGURING') return '围捕闭环正在重新稳定'
  if (blocker === 'POST_MISSION_STABILIZING') return '归队编组正在稳定确认'
  if (blocker.startsWith('POST_MISSION_FORMATION:')) {
    return `${blocker.slice(blocker.indexOf(':') + 1)} 尚未到达终态槽位`
  }
  if (blocker === 'POST_MISSION_FORMATION') return '归队编组尚未到位'
  return blocker
})
const missionElapsedMs = ref(restoredRuntime?.missionElapsedMs ?? 0)
const missionClockNow = ref(Date.now())
const missionClockStartedAt = ref<number | null>(null)
let missionClockTimer: number | null = null
const missionElapsedSeconds = computed(() => Math.max(0, Math.floor(
  (missionElapsedMs.value + (
    missionClockStartedAt.value === null
      ? 0
      : missionClockNow.value - missionClockStartedAt.value
  )) / 1000,
)))
function formatElapsedSeconds(totalSeconds: number) {
  const total = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  return hours > 0
    ? `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
    : `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`
}
const missionElapsedLabel = computed(() => formatElapsedSeconds(missionElapsedSeconds.value))
const simulationElapsedSeconds = computed(() => Number(
  missionMetrics.value.simulationElapsedSeconds
  ?? Math.max(0, state.sequence - 1) * 0.1,
))
const simulationElapsedLabel = computed(() => formatElapsedSeconds(simulationElapsedSeconds.value))
const captureGroups = computed(() => Array.isArray(missionMetrics.value.captureGroups)
  ? missionMetrics.value.captureGroups as CaptureGroupMetric[]
  : [])
const intentLabels: Record<string, string> = {
  UNCLASSIFIED: '意图研判', ATTACK_INTENT: '攻击意图', ESCAPE_INTENT: '逃逸意图',
  ATTACKING: '逼近攻击', FLANKING: '侧翼试探', FLANKING_BREAKTHROUGH: '侧翼突破',
  ESCAPING: '持续逃逸', BREAKOUT: '寻找缺口', EVADING_GUARD: '规避拦截',
  CLEARING_CONVOY: '脱离护航队', CLEARING_CONTAINMENT_SPACE: '腾挪闭环空间',
  CONTAINED: '受控减速', CAPTURED: '已被控制',
  COAST_AVOID: '规避岸线', ESCAPE: '持续逃逸', CRUISE: '巡航观察',
}
const speedReasonLabels: Record<string, string> = {
  ESCAPE_CRUISE: '保持逃逸巡航', GAP_BREAKOUT: '缺口突破加速',
  COAST_AVOID: '岸线规避转向', EXECUTED_CONTAINMENT_DECEL: '实际闭环后受控减速',
  CONTAINMENT_PRESSURE: '围捕压力下减速', CONTAINMENT_DECEL: '稳定闭环后减速',
  ESCAPE_CORRIDOR_BLOCKED: '逃逸通道受阻 · 减速转向',
  NONE: '自主航行',
}
const assignmentReasonLabels: Record<string, string> = {
  PREDICTED_ETA_GAIN: '预测截击时间更短', STALLED_RELIEF: '原成员停滞，启用机动替补',
  PREDICTED_INTERCEPT_GAIN: '预测截击收益更高', STALLED_OVERRIDE: '原成员停滞，启用机动替补',
  TARGET_URGENCY: '目标威胁升级', GAP_ALIGNMENT: '更适合封堵当前缺口',
}
function normalizeHeading(value: unknown) {
  const heading = Number(value ?? 0)
  return ((heading % 360) + 360) % 360
}
function compassDirection(value: unknown) {
  const directions = ['北', '东北', '东', '东南', '南', '西南', '西', '西北']
  return directions[Math.round(normalizeHeading(value) / 45) % directions.length]
}
function intentLabel(value: unknown) {
  const key = String(value ?? '').toUpperCase()
  return intentLabels[key] ?? (key || '态势评估中')
}
function speedReasonLabel(group: CaptureGroupMetric) {
  const key = String(group.targetSpeedReason ?? group.slowdownReason ?? '').toUpperCase()
  return speedReasonLabels[key] ?? (key || '自主航行')
}
function groupSpeed(group: CaptureGroupMetric) {
  return Number(group.targetSpeedMps ?? group.speedMps ?? 0)
}
function latestReassignment(group: CaptureGroupMetric) {
  const changes = group.recentReassignments
  return changes?.length ? changes[changes.length - 1] : null
}
function reassignmentLabel(group: CaptureGroupMetric) {
  const change = latestReassignment(group)
  if (!change) return ''
  const reason = assignmentReasonLabels[String(change.reason ?? '').toUpperCase()]
    ?? change.reason
    ?? '动态重分配'
  return `${change.deviceCode ?? '设备'}：${change.previousTarget ?? '待命'} → ${change.targetCode ?? group.threatCode}（${reason}）`
}
const pendingTerminalSequence = ref<number | null>(null)
const pendingTerminalStatus = ref<string | null>(null)

function setLeftPanelCollapsed(collapsed: boolean) {
  panelTransitioning.value = true
  unityPanel.value?.beginViewportTransition()
  leftPanelCollapsed.value = collapsed
}

function setRightPanelCollapsed(collapsed: boolean) {
  panelTransitioning.value = true
  unityPanel.value?.beginViewportTransition()
  rightPanelCollapsed.value = collapsed
}

function handleWorkbenchTransitionEnd(event: TransitionEvent) {
  if (event.propertyName !== 'grid-template-columns') return
  panelTransitioning.value = false
  unityPanel.value?.endViewportTransition()
}

function handleWorkbenchTransitionCancel(event: TransitionEvent) {
  if (event.propertyName !== 'grid-template-columns') return
  panelTransitioning.value = false
  unityPanel.value?.endViewportTransition()
}
const displayCaptureStage = (stage: unknown) => {
  const value = Number(stage ?? 0)
  if (state.algorithm === 'GB_SFLA_CS') {
    // The capture adapter already exposes its user-facing stages as 1/2/3.
    return Math.min(3, Math.max(1, value || 1))
  }
  // Escort capture groups retain the legacy internal 0/1/2 convention.
  return Math.min(3, Math.max(1, value + 1))
}
const roleSummary = computed(() => {
  const roles = missionMetrics.value.roles
  if (!roles || typeof roles !== 'object') return ''
  return Object.entries(roles as Record<string, unknown>)
    .map(([role, count]) => `${role} ${Number(count)}`)
    .join(' · ')
})
const visibleTargets = computed(() => (
  currentAlgorithmFrame.value?.targets.filter(target => target.visible !== false) ?? []
))
const selectedFrameItem = computed(() => {
  if (!selectedDevice.value || !currentAlgorithmFrame.value) return null
  return currentAlgorithmFrame.value.agents.find(item => item.code === selectedDevice.value)
    ?? currentAlgorithmFrame.value.targets.find(item => item.code === selectedDevice.value)
    ?? null
})
const phaseSteps = computed(() => state.algorithm === 'ESCORT_GUARD'
  ? ['编队护航', '意图识别', '分向守卫', '协同拦截', '追逃压制', '动态围捕', '稳定闭环', '完成']
  : ['目标逃逸', '协同追击', '截击部署', '动态围捕', '稳定闭环', '完成'])
const activePhaseIndex = computed(() => {
  const phase = missionPhase.value.toUpperCase()
  if (state.mission === 'COMPLETED') return phaseSteps.value.length - 1
  if (phase === 'COMPLETED') {
    if (state.algorithm !== 'GB_SFLA_CS') return phaseSteps.value.length - 1
    const rawProgress = Number(missionMetrics.value.missionProgress ?? missionMetrics.value.progress ?? 0)
    const capturedTargets = Number(missionMetrics.value.capturedTargetCount ?? 0)
    // Defensive consistency gate: a stale aggregate stage must never light
    // the terminal step while progress or any executed global ring is open.
    if (rawProgress >= 1 && capturedTargets >= scenarioPlan.value.threatCount) {
      return phaseSteps.value.length - 1
    }
    return phaseSteps.value.length - 2
  }
  if (state.algorithm === 'ESCORT_GUARD') {
    if (phase === 'COMPLETED') return 7
    if (phase === 'SAFE_GATE_TRANSIT' || phase === 'STABLE_CONTAINMENT') return 6
    if (phase === 'GAP_REPAIR' || phase === 'ENCIRCLEMENT') return 5
    if (phase === 'PURSUIT' || phase === 'ESCAPE') return 4
    if (phase === 'BLOCKING') return 3
    if (phase === 'INTERCEPT') return 3
    if (phase === 'GUARD_RECONFIGURATION') return 2
    if (phase === 'THREAT_DETECTION') return 1
    if (phase === 'GUARDING' || phase === 'ESCORTING') return 0
    return 0
  }
  return ({ ESCAPE: 0, PURSUIT: 1, INTERCEPT: 2, ENCIRCLEMENT: 3, GAP_REPAIR: 3, STABLE_CONTAINMENT: 4, COMPLETED: 5 } as Record<string, number>)[phase] ?? 0
})
const protocolSnapshot = computed(() => JSON.stringify(
  lastUnityMessage.value ?? {
    type: 'waitingForUnity',
    payload: { ready: unityReady.value, runId: state.runId },
  },
  null,
  2,
))
let previousAlgorithmPoses: VirtualPoseStateMap = new Map()
let algorithmPollTimer: number | null = null
let algorithmPollInFlight = false
let algorithmPreparePromise: Promise<boolean> | null = null

function startMissionClock(resume: boolean) {
  if (!resume) missionElapsedMs.value = 0
  missionClockStartedAt.value = Date.now()
  missionClockNow.value = missionClockStartedAt.value
  if (missionClockTimer !== null) window.clearInterval(missionClockTimer)
  missionClockTimer = window.setInterval(() => {
    missionClockNow.value = Date.now()
  }, 250)
}

function pauseMissionClock() {
  if (missionClockStartedAt.value !== null) {
    missionElapsedMs.value += Date.now() - missionClockStartedAt.value
    missionClockStartedAt.value = null
  }
  if (missionClockTimer !== null) {
    window.clearInterval(missionClockTimer)
    missionClockTimer = null
  }
}

function resetMissionClock() {
  pauseMissionClock()
  missionElapsedMs.value = 0
  missionClockNow.value = Date.now()
}
const fleetOriginEnu: EnuOrigin = {
  // Centre the experiment in open water instead of beside the island base.
  // The capture target occupies this origin and both containment rings are
  // generated around it, so the complete mission moves as one formation.
  eastM: -360,
  northM: -285,
  upM: 0,
}

const state = reactive(restoredRuntime?.state ?? {
  algorithm: 'ESCORT_GUARD',
  uavCount: 3,
  usvCount: 3,
  uavSpeed: 5,
  usvSpeed: 3,
  mission: 'STOPPED',
  runId: 7001,
  sequence: 0,
})

let runtimeRecoveryWriteTimer: number | undefined

function clearRuntimeRecovery() {
  window.clearTimeout(runtimeRecoveryWriteTimer)
  runtimeRecoveryWriteTimer = undefined
  if (!runtimeRecoveryUser) return
  try { sessionStorage.removeItem(runtimeRecoveryKey) } catch { /* recovery is best-effort */ }
}

function persistRuntimeRecovery() {
  runtimeRecoveryWriteTimer = undefined
  if (!runtimeRecoveryUser || authStore.user?.username !== runtimeRecoveryUser || !savedScenario
    || !['RUNNING', 'PAUSED', 'COMPLETING', 'COMPLETED', 'STOPPED', 'FAILED', 'CANCELLED'].includes(state.mission)) {
    clearRuntimeRecovery()
    return
  }
  const snapshot: VirtualFleetRecoverySnapshot = {
    version: 1,
    userScope: runtimeRecoveryUser,
    savedAt: new Date().toISOString(),
    state: { ...state },
    savedScenario,
    latestPoseBatch,
    initialScenarioPoses: initialScenarioPoses.value,
    plannedScenarioPoses: plannedScenarioPoses.value,
    currentAlgorithmFrame: currentAlgorithmFrame.value,
    selectedDevice: selectedDevice.value,
    cameraMode: cameraMode.value,
    missionElapsedMs: missionElapsedMs.value + (missionClockStartedAt.value === null
      ? 0
      : Date.now() - missionClockStartedAt.value),
  }
  try { sessionStorage.setItem(runtimeRecoveryKey, JSON.stringify(snapshot)) } catch { /* best-effort */ }
}

function scheduleRuntimeRecovery() {
  if (runtimeRecoveryWriteTimer !== undefined) return
  runtimeRecoveryWriteTimer = window.setTimeout(persistRuntimeRecovery, 500)
}

watch(
  () => [
    state.algorithm, state.uavCount, state.usvCount, state.uavSpeed, state.usvSpeed,
    state.mission, state.runId, state.sequence, selectedDevice.value, cameraMode.value,
  ],
  scheduleRuntimeRecovery,
)

const voiceRuntimeHint = computed<VoiceMockRuntimeHint>(() => {
  const stateMap: Record<string, VoiceRuntimeState> = {
    STOPPED: algorithmPrepared.value ? 'PREPARED' : 'PREVIEW',
    RUNNING: 'RUNNING',
    PAUSED: 'PAUSED',
    COMPLETING: 'RUNNING',
    COMPLETED: 'COMPLETED',
    FAILED: 'FAILED',
  }
  const deviceCodes = plannedScenarioPoses.value
    .map(pose => pose.deviceCode)
    .filter((code): code is string => Boolean(code))
  return {
    algorithmRunId: String(state.runId),
    state: stateMap[state.mission] ?? 'PREVIEW',
    sceneReady: unityReady.value && scenarioReadyRunId.value === state.runId,
    deviceCodes,
    latestFrameSequence: state.sequence,
  }
})
const voiceUnitySession = computed(() => ({
  connected: unityReady.value,
  unityInstanceId: presentationUnityInstanceId.value,
  sceneRevision: presentationSceneRevision.value,
}))

const algorithmDescription = computed(() => state.algorithm === 'GB_SFLA_CS'
  ? '算法负责目标分配、围捕航点、设备速度方向和捕获状态。'
  : '算法负责护航编队、意图识别、掩护撤离、协同拦截与动态围控。')

const speedValid = computed(() =>
  state.uavSpeed >= 0
  && state.uavSpeed <= 15
  && state.usvSpeed >= 0
  && state.usvSpeed <= 4)

function addLog(message: string) {
  const time = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  logEntries.value = [`${time}  ${message}`, ...logEntries.value].slice(0, 80)
}

function clearTacticalNotices() {
  resetTacticalNotices()
  consumedTacticalEventIds.clear()
}

function consumeTacticalEvents(frame: AlgorithmRuntimeFrame) {
  const fresh: TacticalEvent[] = []
  const events = Array.isArray(frame.metrics.tacticalEvents)
    ? frame.metrics.tacticalEvents as Array<Record<string, unknown>>
    : []
  for (const raw of events) {
    const eventId = String(raw.eventId ?? '').trim()
    if (!eventId || consumedTacticalEventIds.has(eventId)) continue
    consumedTacticalEventIds.add(eventId)
    const notice: TacticalEvent = {
      eventId,
      type: String(raw.type ?? 'TACTICAL_UPDATE'),
      threatCode: String(raw.threatCode ?? '') || undefined,
      title: String(raw.title ?? '态势决策更新'),
      message: String(raw.message ?? ''),
      confidence: Number(raw.confidence ?? 0),
      sequence: Number(raw.sequence ?? frame.sequence),
    }
    fresh.push(notice)
    tacticalHistory.value = [notice, ...tacticalHistory.value].slice(0, 40)
    addLog(`${notice.title}${notice.threatCode ? ` / ${notice.threatCode}` : ''}: ${notice.message}`)
  }
  enqueueTacticalNotices(fresh)
}

function send(type: string, payload: Record<string, unknown> = {}) {
  if (type === 'loadScenario') {
    presentationSceneRevision.value += 1
    savedScenario = JSON.parse(JSON.stringify(payload))
    latestPoseBatch = null
  }
  if (type === 'applyPoseBatch') {
    latestPoseBatch = JSON.parse(JSON.stringify(payload))
    scheduleRuntimeRecovery()
    // An in-flight HTTP frame can arrive during an iframe reload. Cache it,
    // but don't send it into an unacknowledged scene or advance Unity blindly.
    if (recoveringScene.value || scenarioReadyRunId.value !== state.runId) return
  }
  const requestId = unityPanel.value?.postToUnity(type, payload)
  addLog(`${type}${requestId ? ` / ${requestId}` : ''}`)
  return requestId
}

function sendPresentationMessage(message: UnityPresentationOutgoing) {
  const sent = unityPanel.value?.postPresentationEnvelope(message) === true
  addLog(`${message.type}: ${sent ? 'sent' : 'Unity bridge unavailable'}`)
}

function finalizeTerminalMission(status: string, sequence: number) {
  if (pendingTerminalSequence.value !== sequence) return
  pendingTerminalSequence.value = null
  pendingTerminalStatus.value = null
  state.mission = status
  pauseMissionClock()
  stopAlgorithmPolling()
  algorithmPrepared.value = false
  persistRuntimeRecovery()
  algorithmPreparePromise = null
  addLog(`mission terminal applied by Unity: ${status} sequence=${sequence}`)
  send('missionStop', {
    runtimeMode: 'VIRTUAL_SIMULATION',
    runId: state.runId,
    terminalStatus: status,
    appliedSequence: sequence,
  })
}

function onUnityLoading() {
  unityReady.value = false
  presentationUnityInstanceId.value = crypto.randomUUID().toLowerCase()
  presentationSceneRevision.value = 0
  scenarioReadyRunId.value = null
  clearTimeout(recoveryTimer)
  recoveryPoseSequence = null
  if (!recoveringScene.value) {
    restoredCamera = { mode: cameraMode.value, deviceCode: selectedDevice.value }
  }
  recoveringScene.value = savedScenario !== null
  scenarioLoading.value = recoveringScene.value
  simulationRuntime.recoveryError.value = ''
  if (recoveringScene.value) {
    recoveryTimer = window.setTimeout(() => failSceneRecovery('WebGL 加载超时，请重试恢复。原任务未重置。'), 120000)
  }
}

function failSceneRecovery(message: string) {
  clearTimeout(recoveryTimer)
  scenarioReadyRunId.value = null
  simulationRuntime.recoveryError.value = message
  missionActionMessage.value = message
  addLog(message)
}

function finishSceneRecovery() {
  clearTimeout(recoveryTimer)
  recoveryPoseSequence = null
  recoveringScene.value = false
  scenarioLoading.value = false
  scenarioReadyRunId.value = state.runId
  simulationRuntime.recoveryError.value = ''
  if (restoredCamera.deviceCode) send('selectDevice', { deviceCode: restoredCamera.deviceCode })
  send('setCameraMode', restoredCamera)
  missionActionMessage.value = ''
  unityPanel.value?.syncViewport()
  algorithmPrepared.value = true
  if (state.mission === 'RUNNING' || state.mission === 'COMPLETING') {
    startMissionClock(true)
    startAlgorithmPolling()
  } else {
    pauseMissionClock()
  }
  scheduleRuntimeRecovery()
  addLog(`场景恢复完成：原 runId=${state.runId}，序列=${state.sequence}`)
}

function restoreLatestPose() {
  if (latestPoseBatch) {
    recoveryPoseSequence = Number(latestPoseBatch.sequence)
    unityPanel.value?.postToUnity('applyPoseBatch', latestPoseBatch)
  } else {
    finishSceneRecovery()
  }
}

async function onUnityReady() {
  unityReady.value = true
  addLog('platformBridgeReady: Unity WebGL 已连接')
  send('initializePlatform', {
    runtimeMode: 'VIRTUAL_SIMULATION',
    protocolVersion: '2.0',
    buildId: 'vue-virtual-fleet-v2-compatible',
  })
  if (recoveringScene.value && savedScenario) {
    try {
      const runtime = await fetchAlgorithmRunStatus(state.runId)
      const runtimeState = runtime.state.toUpperCase()
      if (!['RUNNING', 'PAUSED', 'PREPARED', 'STOPPED', 'COMPLETED', 'FAILED', 'CANCELLED'].includes(runtimeState)) {
        failSceneRecovery(`算法运行状态为 ${runtimeState}，不能恢复原运行场景。`)
        clearRuntimeRecovery()
        return
      }
      state.mission = runtimeState
      algorithmPrepared.value = true
      addLog(`algorithm runtime recovered: ${runtimeState} runId=${state.runId}`)
    } catch (error) {
      if (error instanceof ApiClientError && error.status === 404) {
        // An expired server runtime cannot be recovered by reloading WebGL.
        // Recreate the preview only; starting a mission remains an explicit action.
        algorithmPrepared.value = false
        addLog('原算法运行已不存在，清理恢复记录并重新生成仿真预览')
        await resetMission()
        return
      }
      failSceneRecovery(`无法核对原算法运行：${error instanceof Error ? error.message : String(error)}`)
      return
    }
    clearTimeout(recoveryTimer)
    recoveryTimer = window.setTimeout(() => failSceneRecovery('场景或设备位置未确认，不能将连接在线视为恢复成功。请重试。'), 45000)
    // Restore only the renderer, never prepare/start a second algorithm run.
    send('loadScenario', savedScenario)
    return
  }
  // The page should open with a real, validated default preview instead of
  // exposing Unity's bootstrap placeholders or leaving an empty ocean.  Use
  // the same loadScenario path as the Generate button so the default 3+3
  // fleet, target and island obey the current safety/layout validation.
  window.setTimeout(() => {
    if (
      unityReady.value
      && !scenarioLoading.value
      && scenarioReadyRunId.value === null
      && plannedScenarioPoses.value.length === 0
    ) {
      generateScenario()
    }
  }, 0)
}

function onUnityError(message: string) {
  unityReady.value = false
  if (recoveringScene.value) failSceneRecovery(message)
  else scenarioLoading.value = false
  addLog(`Unity 错误: ${message}`)
}

function onUnityMessage(message: UnityMessage) {
  lastUnityMessage.value = message
  if (message.type.startsWith('PRESENTATION_')) {
    void voiceControlPanel.value?.handleUnityPresentationMessage(message)
  }
  if (message.type === 'vueCommandReceived' && message.payload?.type === 'loadScenario') {
    addLog(
      `bridge loadScenario: sent=${message.payload.bridgeSent === true}`
      + ` queued=${message.payload.queued === true}`
      + ` fallback=${String(message.payload.fallback ?? '') || '-'}`,
    )
  }
  if (message.type === 'unityBridgeError') {
    addLog(
      `Unity bridge error: ${String(message.payload?.message ?? 'unknown')}`
      + ` type=${String(message.payload?.requestedType ?? '-')}`,
    )
  }
  if (message.type === 'platformBridgeReady') {
    unityReady.value = message.payload?.ready === true
      || (
        message.payload?.controlsReady === true
        && message.payload?.cameraReady === true
        && message.payload?.algorithmReady === true
      )
  }
  if (message.type === 'scenarioReady' || message.type === 'scenarioLoaded') {
    const readyRunId = Number(message.payload?.runId ?? 0)
    const success = message.payload?.success === true
    // Some compatible Unity builds omit runId from scenarioReady. Accept a
    // missing id for the currently loading scenario, but never accept a
    // positive id belonging to an older scenario.
    const runIdMatches = readyRunId === state.runId || readyRunId === 0
    if (!runIdMatches) return
    if (recoveringScene.value) {
      if (!success) failSceneRecovery('Unity 场景恢复失败，请重试；原任务未重置。')
      else if (recoveryPoseSequence === null) restoreLatestPose()
      return
    }
    // Both scenarioLoaded and scenarioReady may acknowledge the same scene.
    if (success && scenarioReadyRunId.value === state.runId && !scenarioLoading.value) return
    scenarioReadyRunId.value = success && runIdMatches
      ? (readyRunId || state.runId)
      : null
    if (success && runIdMatches) {
      const returnedPoses = Array.isArray(message.payload?.initialPoses)
        ? message.payload.initialPoses
          .filter((pose): pose is ScenarioInitialPose => (
            typeof pose === 'object'
            && pose !== null
            && typeof (pose as Record<string, unknown>).deviceCode === 'string'
          ))
          .map(pose => ({
            deviceCode: pose.deviceCode,
            deviceType: pose.deviceType,
            eastM: Number(pose.eastM),
            northM: Number(pose.northM),
            upM: Number(pose.upM),
            headingDeg: Number(pose.headingDeg ?? 0),
            speedMps: Number(pose.speedMps ?? 0),
            state: pose.state,
            valid: pose.valid !== false,
          }))
          .filter(pose => (
            Number.isFinite(pose.eastM)
            && Number.isFinite(pose.northM)
            && Number.isFinite(pose.upM)
            && Number.isFinite(pose.headingDeg)
          ))
        : []
      initialScenarioPoses.value = returnedPoses.length > 0
        ? returnedPoses
        : plannedScenarioPoses.value
      addLog(`scenario initial poses: ${initialScenarioPoses.value.length}`)
      // Frame the validated preview only after Unity has created every
      // scenario object. Sending overview while loadScenario is still in
      // flight can focus the bootstrap origin and produce an empty/default
      // view depending on machine timing.
      window.setTimeout(() => setOverviewCamera(), 80)
    }
    if (runIdMatches) scenarioLoading.value = false
    if (success && runIdMatches) void prepareExternalAlgorithm()
    addLog(
      `${message.type}: ${success ? 'success' : 'failed'}`
      + ` runId=${readyRunId || '-'}`,
    )
  }
  if (message.type === 'poseFrameApplied') {
    const success = message.payload?.success === true
    const appliedSequence = Number(message.payload?.sequence ?? -1)
    const appliedRunId = Number(message.payload?.runId ?? state.runId)
    if (appliedRunId !== state.runId) return
    if (success) acknowledgeTacticalFrame(appliedSequence)
    if (recoveringScene.value && recoveryPoseSequence !== null) {
      if (appliedSequence !== recoveryPoseSequence) return
      if (!success) {
        failSceneRecovery('恢复位置帧被 Unity 拒绝，请重试。')
        return
      }
      const expectedCount = [latestPoseBatch?.vehicles, latestPoseBatch?.targets]
        .reduce<number>((count, poses) => count + (Array.isArray(poses) ? poses.length : 0), 0)
      if (Number(message.payload?.appliedCount ?? expectedCount) < expectedCount
        || (Array.isArray(message.payload?.missingDeviceCodes) && message.payload.missingDeviceCodes.length > 0)
        || (Array.isArray(message.payload?.unknownDeviceCodes) && message.payload.unknownDeviceCodes.length > 0)) {
        failSceneRecovery('恢复帧中有设备缺失，不能视为恢复成功，请重试。')
        return
      }
      if (latestPoseBatch && Number(latestPoseBatch.sequence) > appliedSequence) {
        restoreLatestPose()
        return
      }
      finishSceneRecovery()
    }
    if (
      success
      && pendingTerminalSequence.value !== null
      && appliedSequence === pendingTerminalSequence.value
    ) {
      finalizeTerminalMission(
        pendingTerminalStatus.value ?? 'COMPLETED',
        appliedSequence,
      )
    }
    const code = String(message.payload?.code ?? '')
    const trackedDeviceCode = String(message.payload?.trackedDeviceCode ?? '')
    const unityPosition = trackedDeviceCode
      ? ` ${trackedDeviceCode} unity=(${Number(message.payload?.unityPositionX ?? 0).toFixed(2)},`
        + `${Number(message.payload?.unityPositionY ?? 0).toFixed(2)},`
        + `${Number(message.payload?.unityPositionZ ?? 0).toFixed(2)})`
        + ` heading=${Number(message.payload?.unityHeadingDeg ?? 0).toFixed(1)}`
        + ` model=(${Number(message.payload?.transformPositionX ?? 0).toFixed(2)},`
        + `${Number(message.payload?.transformPositionY ?? 0).toFixed(2)},`
        + `${Number(message.payload?.transformPositionZ ?? 0).toFixed(2)})`
        + ` heading=${Number(message.payload?.transformHeadingDeg ?? 0).toFixed(1)}`
      : ''
    addLog(
      `poseFrameApplied: sequence=${message.payload?.sequence ?? '-'}`
      + ` ${success ? 'success' : code || 'failed'}`
      + trackedDeviceCode
      + unityPosition,
    )
  }
  if (message.type === 'cameraChanged') {
    if (recoveringScene.value) return
    const mode = String(message.payload?.mode ?? '').trim().toLowerCase()
    const deviceCode = String(message.payload?.deviceCode ?? '').trim()
    if (mode) cameraMode.value = mode
    if (mode === 'overview') selectedDevice.value = ''
    else if (deviceCode) selectedDevice.value = deviceCode
    addLog(`cameraChanged: ${deviceCode || '-'} / ${mode || '-'}`)
  }
}

function validateSpeed(value: number, max: number) {
  return Math.max(0, Math.min(max, Number.isFinite(value) ? value : 0))
}

function validateFleetCount(value: number) {
  return Math.max(1, Math.min(128, Math.trunc(Number.isFinite(value) ? value : 1)))
}

async function generateScenario() {
  if (sceneLocked.value || scenarioLoading.value) return
  state.uavSpeed = validateSpeed(state.uavSpeed, 15)
  state.usvSpeed = validateSpeed(state.usvSpeed, 4)
  state.uavCount = validateFleetCount(state.uavCount)
  state.usvCount = validateFleetCount(state.usvCount)
  // The Unity scene has a verified open-water operating box southwest of
  // Catalina.  Keep the algorithm's complete local safety domain inside it:
  // global east [-510,-210], north [-435,-135].  Scaling the origin with the
  // UI "world size" previously moved fleets onto the decorative outer coast.
  fleetOriginEnu.eastM = -360
  fleetOriginEnu.northM = -285
  // Standalone virtual simulation uses one isolated ID across Unity and the
  // algorithm process. It does not require a MissionRun database record.
  state.runId = Date.now()
  state.sequence = 0
  state.mission = 'STOPPED'
  pendingTerminalSequence.value = null
  pendingTerminalStatus.value = null
  missionActionMessage.value = ''
  algorithmPrepared.value = false
  algorithmPreparePromise = null
  stopAlgorithmPolling()
  clearTacticalNotices()
  previousAlgorithmPoses = new Map()
  currentAlgorithmFrame.value = null
  plannedScenarioPoses.value = buildVirtualFleetGridLayout({
    uavCount: state.uavCount,
    usvCount: state.usvCount,
    fleetOrigin: fleetOriginEnu,
    uavSpeedMps: state.uavSpeed,
    usvSpeedMps: state.usvSpeed,
    captureMode: state.algorithm === 'GB_SFLA_CS',
    scenarioId: state.runId,
  })
  initialScenarioPoses.value = plannedScenarioPoses.value
  scenarioReadyRunId.value = null
  scenarioLoading.value = true
  const generationRunId = state.runId

  if (state.algorithm === 'ESCORT_GUARD') {
    addLog(`authoritative escort preview pending: runId=${state.runId}`)
    const prepared = await prepareExternalAlgorithm(true, [])
    if (state.runId !== generationRunId) return
    if (!prepared) {
      scenarioLoading.value = false
      missionActionMessage.value = algorithmPrepareError.value
        ? `护航权威首帧准备失败：${algorithmPrepareError.value}`
        : '护航权威首帧未就绪，本次场景未加载。'
      return
    }
    try {
      const frames = await fetchAlgorithmFrames(state.runId, 0)
      const firstFrame = [...frames]
        .filter(frame => frame.sequence > 0)
        .sort((left, right) => left.sequence - right.sequence)[0]
      if (!firstFrame) throw new Error('算法未返回初始帧')
      const adapted = adaptVirtualAlgorithmFrame(
        firstFrame,
        new Map(),
        { fleetOrigin: fleetOriginEnu },
      )
      plannedScenarioPoses.value = [
        ...adapted.payload.vehicles.map(pose => ({ ...pose, deviceType: pose.deviceType ?? 'UAV' })),
        ...adapted.payload.targets.map(pose => ({ ...pose, deviceType: 'TARGET' as const })),
      ] as GridScenarioPose[]
      initialScenarioPoses.value = plannedScenarioPoses.value
      previousAlgorithmPoses = adapted.nextState
      currentAlgorithmFrame.value = firstFrame
      // Keep polling anchored at zero until mission start. The exact same
      // sequence-one coordinates are loaded into Unity now and re-applied as
      // an idempotent synchronization check before execution.
      state.sequence = 0
      consumeTacticalEvents(firstFrame)
      addLog(`authoritative escort preview ready: sequence=${firstFrame.sequence}`)
    } catch (error) {
      scenarioLoading.value = false
      algorithmPrepared.value = false
      algorithmPrepareError.value = error instanceof Error ? error.message : String(error)
      missionActionMessage.value = `护航权威首帧获取失败：${algorithmPrepareError.value}`
      return
    }
  }
  addLog(`loadScenario pending: runId=${state.runId}`)
  send('loadScenario', {
    runtimeMode: 'VIRTUAL_SIMULATION',
    algorithmCode: state.algorithm,
    runId: state.runId,
    uavCount: state.uavCount,
    usvCount: state.usvCount,
    targetCount: configuredTargetCount.value,
    layoutVersion: state.algorithm === 'ESCORT_GUARD' ? 'ADAPTIVE_MULTI_TARGET_V2' : 'ADAPTIVE_MULTI_CAPTURE_V2',
    initialPosesCoordinateFrame: 'GLOBAL_ENU',
    initialPoses: plannedScenarioPoses.value,
    initialSpeedMps: state.algorithm === 'GB_SFLA_CS' ? state.uavSpeed : state.usvSpeed,
  })
}

function buildAlgorithmPrepareConfig(initialPoses: ScenarioInitialPose[]) {
  return {
    uavCount: state.uavCount,
    usvCount: state.usvCount,
    targetCount: configuredTargetCount.value,
    protectedCount: state.algorithm === 'ESCORT_GUARD' ? scenarioPlan.value.protectedCount : 0,
    threatCount: scenarioPlan.value.threatCount,
    simultaneousThreats: scenarioPlan.value.simultaneousThreats,
    worldWidth: scenarioPlan.value.worldWidth,
    worldHeight: scenarioPlan.value.worldHeight,
    adaptiveMultiTarget: state.algorithm === 'ESCORT_GUARD',
    uavSpeedMps: state.uavSpeed,
    usvSpeedMps: state.usvSpeed,
    coordinateFrame: 'FLEET_LOCAL_ENU',
    initialPosesCoordinateFrame: 'GLOBAL_ENU',
    fleetOrigin: fleetOriginEnu,
    initialPoses,
    targetBehavior: 'MOVING',
    previewEnabled: state.algorithm === 'GB_SFLA_CS',
    threatMinDistanceM: state.algorithm === 'GB_SFLA_CS' ? 90 : 170,
    standaloneVirtualSimulation: true,
  }
}

function prepareExternalAlgorithm(
  allowWhileScenarioLoading = false,
  initialPoses: ScenarioInitialPose[] = initialScenarioPoses.value,
): Promise<boolean> {
  if (algorithmPrepared.value) return Promise.resolve(true)
  if (scenarioLoading.value && !allowWhileScenarioLoading) return Promise.resolve(false)
  if (algorithmPreparePromise) return algorithmPreparePromise

  const prepareRunId = state.runId
  algorithmPreparing.value = true
  algorithmPrepareError.value = ''
  algorithmPreparePromise = (async () => {
    try {
      await prepareAlgorithmRun(
        prepareRunId,
        state.algorithm,
        buildAlgorithmPrepareConfig(initialPoses),
      )
      if (state.runId !== prepareRunId) return false
      algorithmPrepared.value = true
      // PREVIEW keeps producing frames after prepare. Start at zero so the
      // first poll applies the authoritative ambient positions instead of
      // skipping directly to the latest sequence number reported by status.
      state.sequence = 0
      addLog(`algorithm prepared: ${state.algorithm} runId=${prepareRunId}`)
      if (state.algorithm === 'GB_SFLA_CS') startAlgorithmPolling()
      return true
    } catch (error) {
      algorithmPrepared.value = false
      algorithmPrepareError.value = error instanceof Error ? error.message : String(error)
      addLog(`algorithm prepare failed: ${algorithmPrepareError.value}`)
      return false
    } finally {
      algorithmPreparing.value = false
      algorithmPreparePromise = null
    }
  })()
  return algorithmPreparePromise
}

async function startMission() {
  missionActionMessage.value = ''
  if (
    !unityReady.value
    || !speedValid.value
    || scenarioLoading.value
    || scenarioReadyRunId.value !== state.runId
  ) {
    missionActionMessage.value = scenarioLoading.value || scenarioReadyRunId.value !== state.runId
      ? '场景仍在等待 Unity 确认，请重新生成场景后再试。'
      : !unityReady.value
        ? 'Unity WebGL 尚未就绪，暂时不能启动算法。'
        : '速度配置无效，请修正后再启动。'
    addLog(`missionStart blocked: ${missionActionMessage.value} runId=${state.runId}`)
    return
  }
  if (!algorithmPrepared.value) {
    addLog(`missionStart: preparing algorithm runId=${state.runId}`)
    if (!(await prepareExternalAlgorithm())) {
      missionActionMessage.value = algorithmPrepareError.value
        ? `算法准备失败：${algorithmPrepareError.value}`
        : '算法准备失败，请稍后重试。'
      return
    }
  }
  const initialFrameSynced = await synchronizeInitialAlgorithmFrame()
  if (!initialFrameSynced) {
    missionActionMessage.value = '算法首帧未返回，任务未启动，请稍后重试。'
    addLog(`missionStart blocked: ${missionActionMessage.value}`)
    return
  }
  try {
    const resuming = state.mission === 'PAUSED'
    await controlAlgorithmRun(state.runId, 'start')
    state.mission = 'RUNNING'
    startMissionClock(resuming)
    missionActionMessage.value = '算法已启动。'
    addLog(
      `algorithm coordinates: FLEET_LOCAL_ENU`
      + ` origin=(${fleetOriginEnu.eastM},${fleetOriginEnu.northM},${fleetOriginEnu.upM})`,
    )
    send('missionStart', { runtimeMode: 'VIRTUAL_SIMULATION', runId: state.runId })
    startAlgorithmPolling()
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    if (/停止|stop|not found|不存在/i.test(message)) {
      algorithmPrepared.value = false
      algorithmPreparePromise = null
      addLog(`algorithm process unavailable, rebuilding runId=${state.runId}`)
      if (await prepareExternalAlgorithm()) {
        const retryInitialFrameSynced = await synchronizeInitialAlgorithmFrame()
        if (!retryInitialFrameSynced) {
          addLog(`missionStart retry blocked: algorithm sequence=1 is not available`)
          return
        }
        try {
          const resuming = state.mission === 'PAUSED'
          await controlAlgorithmRun(state.runId, 'start')
          state.mission = 'RUNNING'
          startMissionClock(resuming)
          send('missionStart', { runtimeMode: 'VIRTUAL_SIMULATION', runId: state.runId })
          startAlgorithmPolling()
          return
        } catch (retryError) {
          addLog(
            `missionStart retry failed: ${
              retryError instanceof Error ? retryError.message : String(retryError)
            }`,
          )
          return
        }
      }
    }
    missionActionMessage.value = `启动失败：${message}`
    addLog(`missionStart failed: ${message}`)
  }
}

async function pauseMission() {
  if (state.mission !== 'RUNNING') return
  try {
    await controlAlgorithmRun(state.runId, 'pause')
    state.mission = 'PAUSED'
    pauseMissionClock()
    stopAlgorithmPolling()
    send('missionPause', { runtimeMode: 'VIRTUAL_SIMULATION', runId: state.runId })
  } catch (error) {
    addLog(`missionPause failed: ${error instanceof Error ? error.message : String(error)}`)
  }
}

function toggleWebglExpanded() {
  webglExpanded.value = !webglExpanded.value
}

async function stopMission() {
  pendingTerminalSequence.value = null
  pendingTerminalStatus.value = null
  try {
    if (algorithmPrepared.value) await controlAlgorithmRun(state.runId, 'stop')
    state.mission = 'STOPPED'
    pauseMissionClock()
    algorithmPrepared.value = false
    clearRuntimeRecovery()
    algorithmPreparePromise = null
    stopAlgorithmPolling()
    currentAlgorithmFrame.value = null
    send('missionStop', { runtimeMode: 'VIRTUAL_SIMULATION', runId: state.runId })
  } catch (error) {
    addLog(`missionStop failed: ${error instanceof Error ? error.message : String(error)}`)
  }
}

async function resetMission() {
  clearTimeout(recoveryTimer)
  clearTacticalNotices()
  savedScenario = null
  latestPoseBatch = null
  clearRuntimeRecovery()
  recoveringScene.value = false
  simulationRuntime.recoveryError.value = ''
  stopAlgorithmPolling()
  if (algorithmPrepared.value) {
    try {
      await controlAlgorithmRun(state.runId, 'cancel')
    } catch (error) {
      addLog(`algorithm reset warning: ${error instanceof Error ? error.message : String(error)}`)
    }
  }
  state.mission = 'STOPPED'
  resetMissionClock()
  pendingTerminalSequence.value = null
  pendingTerminalStatus.value = null
  state.sequence = 0
  algorithmPrepared.value = false
  algorithmPreparePromise = null
  previousAlgorithmPoses = new Map()
  currentAlgorithmFrame.value = null
  plannedScenarioPoses.value = []
  initialScenarioPoses.value = []
  scenarioReadyRunId.value = null
  selectedDevice.value = ''
  addLog('missionReset: 清理算法、轨迹和 Unity 运行实例')
  send('missionReset', { runtimeMode: 'VIRTUAL_SIMULATION', runId: state.runId })
  unityPanel.value?.reload()
}

async function applyAlgorithmFrame(
  frame: AlgorithmRuntimeFrame,
  force = false,
) {
  if (!force && frame.sequence <= state.sequence) return
  const adapted = adaptVirtualAlgorithmFrame(
    frame,
    previousAlgorithmPoses,
    { fleetOrigin: fleetOriginEnu },
  )
  previousAlgorithmPoses = adapted.nextState
  currentAlgorithmFrame.value = frame
  consumeTacticalEvents(frame)
  state.sequence = frame.sequence
  const trackedPose = adapted.payload.vehicles.find((pose) => pose.deviceCode === 'UAV-001')
  const targetPose = adapted.payload.targets[0]
  addLog(
    `algorithm frame: sequence=${frame.sequence}`
    + (trackedPose
      ? ` UAV-001 pos=(${trackedPose.eastM.toFixed(2)},`
        + `${trackedPose.northM.toFixed(2)},${trackedPose.upM.toFixed(2)})`
        + ` heading=${trackedPose.headingDeg.toFixed(1)}`
      : '')
    + (targetPose
      ? ` ${targetPose.deviceCode} pos=(${targetPose.eastM.toFixed(2)},`
        + `${targetPose.northM.toFixed(2)},${targetPose.upM.toFixed(2)})`
      : ''),
  )
  send('applyPoseBatch', { ...adapted.payload, runId: state.runId })
  if (frame.terminalStatus) {
    const terminal = frame.terminalStatus.toUpperCase()
    pendingTerminalSequence.value = frame.sequence
    pendingTerminalStatus.value = terminal
    state.mission = 'COMPLETING'
    stopAlgorithmPolling()
    addLog(
      `mission terminal pending Unity apply: ${terminal}`
      + ` sequence=${frame.sequence}`
      + ` ${String(frame.metrics.terminalReason ?? '')}`,
    )
  }
}

async function synchronizeInitialAlgorithmFrame(): Promise<boolean> {
  try {
    const frames = await fetchAlgorithmFrames(state.runId, 0)
    const firstFrame = [...frames]
      .filter(frame => frame.sequence > 0)
      .sort((left, right) => right.sequence - left.sequence)[0]

    if (!firstFrame) {
      addLog(`algorithm initial frame unavailable: runId=${state.runId}`)
      return false
    }

    await applyAlgorithmFrame(firstFrame, true)
    const targetPose = adaptVirtualAlgorithmFrame(
      firstFrame,
      new Map(),
      { fleetOrigin: fleetOriginEnu },
    ).payload.targets[0]
    addLog(
      `latest preview pose synchronized before missionStart`
      + (targetPose
        ? ` ${targetPose.deviceCode}=(${targetPose.eastM.toFixed(2)},${targetPose.northM.toFixed(2)},${targetPose.upM.toFixed(2)})`
        : ' mission target=missing'),
    )
    return true
  } catch (error) {
    addLog(
      `algorithm initial frame failed: ${
        error instanceof Error ? error.message : String(error)
      }`,
    )
    return false
  }
}

async function pollAlgorithmFrame() {
  if (
    algorithmPollInFlight
    || (
      state.mission !== 'RUNNING'
      && !(state.algorithm === 'GB_SFLA_CS' && state.mission === 'STOPPED')
    )
    || !algorithmPrepared.value
    || !unityReady.value
    || scenarioLoading.value
    || recoveringScene.value
  ) return
  algorithmPollInFlight = true
  try {
    const frames = await fetchAlgorithmFrames(state.runId, state.sequence)
    for (const frame of frames) {
      await applyAlgorithmFrame(frame)
    }
  } catch (error) {
    addLog(`algorithm frame failed: ${error instanceof Error ? error.message : String(error)}`)
  } finally {
    algorithmPollInFlight = false
  }
}

function startAlgorithmPolling() {
  stopAlgorithmPolling()
  void pollAlgorithmFrame()
  algorithmPollTimer = window.setInterval(() => {
    void pollAlgorithmFrame()
  }, 100)
}

function stopAlgorithmPolling() {
  if (algorithmPollTimer !== null) {
    window.clearInterval(algorithmPollTimer)
    algorithmPollTimer = null
  }
}

function setOverviewCamera() {
  cameraMode.value = 'overview'
  selectedDevice.value = ''
  send('setCameraMode', { mode: 'overview' })
}

function followSelectedDevice() {
  if (!selectedDevice.value) return
  cameraMode.value = 'device-follow'
  send('setCameraMode', {
    mode: 'device-follow',
    deviceCode: selectedDevice.value,
  })
}

// Registration lasts as long as the cached business view, not its activation.
simulationRuntime.events = { ready: onUnityReady, loading: onUnityLoading, message: onUnityMessage, error: onUnityError }
simulationRuntime.requested.value = true
watch(webglExpanded, () => window.dispatchEvent(new CustomEvent('unity-runtime-track')))
let lastVoiceVisualStateVersion = -1
watch(
  () => [
    voiceControlStore.context?.algorithmRunId ?? '',
    voiceControlStore.context?.stateVersion ?? -1,
    voiceControlStore.context?.state ?? '',
  ] as const,
  async ([algorithmRunId, stateVersion, runtimeState]) => {
    if (algorithmRunId !== String(state.runId) || stateVersion === lastVoiceVisualStateVersion) return
    lastVoiceVisualStateVersion = stateVersion
    if (runtimeState === 'RUNNING') {
      const resuming = state.mission === 'PAUSED'
      state.mission = 'RUNNING'
      algorithmPrepared.value = true
      await synchronizeInitialAlgorithmFrame()
      startMissionClock(resuming)
      send('missionStart', { runtimeMode: 'VIRTUAL_SIMULATION', runId: state.runId })
      startAlgorithmPolling()
      addLog(`voice runtime synchronized: RUNNING stateVersion=${stateVersion}`)
    } else if (runtimeState === 'PAUSED') {
      state.mission = 'PAUSED'
      pauseMissionClock()
      stopAlgorithmPolling()
      send('missionPause', { runtimeMode: 'VIRTUAL_SIMULATION', runId: state.runId })
      addLog(`voice runtime synchronized: PAUSED stateVersion=${stateVersion}`)
    } else if (['STOPPED', 'COMPLETED', 'FAILED'].includes(runtimeState)) {
      state.mission = runtimeState === 'COMPLETED' ? 'COMPLETED' : runtimeState === 'FAILED' ? 'FAILED' : 'STOPPED'
      pauseMissionClock()
      stopAlgorithmPolling()
      send('missionStop', { runtimeMode: 'VIRTUAL_SIMULATION', runId: state.runId })
      addLog(`voice runtime synchronized: ${runtimeState} stateVersion=${stateVersion}`)
    }
  },
  { immediate: true },
)
window.addEventListener('pagehide', persistRuntimeRecovery)

onBeforeUnmount(() => {
  window.removeEventListener('pagehide', persistRuntimeRecovery)
  persistRuntimeRecovery()
  clearTimeout(recoveryTimer)
  clearTacticalNotices()
  simulationRuntime.events = null
  simulationRuntime.requested.value = false
  recoveringScene.value = false
  stopAlgorithmPolling()
  pauseMissionClock()
})
</script>

<template>
  <ConsoleLayout
    title="算法仿真"
    eyebrow="VIRTUAL FLEET / UNITY BRIDGE V3"
    :show-refresh="false"
    :default-sidebar-collapsed="true"
    immersive
  >
    <div class="virtual-fleet-page">
      <header class="vf-app-header">
        <div class="vf-app-title">
          <span>UAV-USV 协同仿真平台</span>
          <strong>算法仿真</strong>
        </div>
        <nav class="vf-workspace-switch" aria-label="工作空间切换">
          <RouterLink :to="{ name: 'dashboard' }">系统总览</RouterLink>
          <span class="active">算法仿真</span>
        </nav>
        <div class="vf-instance-status" :class="{ offline: !unityReady }">
          <i></i>
          独立仿真 WebGL · {{ unityReady ? 'ONLINE' : 'CONNECTING' }}
        </div>
      </header>

      <div
        class="vf-workbench"
        :class="{
          'left-collapsed': leftPanelCollapsed,
          'right-collapsed': rightPanelCollapsed,
          'panel-transitioning': panelTransitioning,
        }"
        @transitionend="handleWorkbenchTransitionEnd"
        @transitioncancel="handleWorkbenchTransitionCancel"
      >
        <aside class="vf-config-drawer" :class="{ collapsed: leftPanelCollapsed }">
          <button
            class="vf-drawer-reopen"
            type="button"
            title="展开场景配置"
            :tabindex="leftPanelCollapsed ? 0 : -1"
            @click="setLeftPanelCollapsed(false)"
          >
            <ChevronRight :size="18" />
            <span>场景配置</span>
          </button>
          <section class="vf-panel vf-config-panel">
            <div class="vf-panel-head">
              <div><h3>场景配置</h3><span>V3 PROTOCOL</span></div>
              <button type="button" title="收起场景配置" @click="setLeftPanelCollapsed(true)">
                <ChevronLeft :size="17" />
              </button>
            </div>
            <label>算法
              <select v-model="state.algorithm" :disabled="sceneLocked">
                <option value="ESCORT_GUARD" title="智能粒球仿真护航算法">智能粒球仿真护航</option>
                <option value="GB_SFLA_CS">GB-SFLA-CS 协同围捕（模拟）</option>
              </select>
            </label>
            <p class="vf-description">{{ algorithmDescription }}</p>
            <div class="vf-plan-summary">
              <strong v-if="state.algorithm === 'ESCORT_GUARD'">{{ scenarioPlan.protectedCount }} 护航目标 · {{ scenarioPlan.threatCount }} 敌船</strong>
              <strong v-else>{{ scenarioPlan.threatCount }} 艘围捕目标敌船</strong>
              <span>{{ state.uavCount }} UAV · {{ state.usvCount }} USV · 世界 {{ scenarioPlan.worldWidth }}×{{ scenarioPlan.worldHeight }} m</span>
              <small v-if="state.algorithm === 'ESCORT_GUARD'">规划预览 · 同时来袭 {{ scenarioPlan.simultaneousThreats }} 艘 · {{ scenarioPlan.realtimeTier === 'PHASE_TWO_REALTIME' ? '实时仿真' : '容量模式' }}</small>
              <small v-else>规划预览 · 自动拆分协同围捕编组 · {{ scenarioPlan.realtimeTier === 'PHASE_TWO_REALTIME' ? '实时仿真' : '容量模式' }}</small>
            </div>
            <div class="vf-two-col">
              <label>UAV 数量
                <input v-model.number="state.uavCount" type="number" min="1" max="128" :disabled="sceneLocked">
              </label>
              <label>USV 数量
                <input v-model.number="state.usvCount" type="number" min="1" max="128" :disabled="sceneLocked">
              </label>
              <label>UAV 巡航速度 m/s
                <input v-model.number="state.uavSpeed" type="number" min="0" max="15" step="0.1" :disabled="sceneLocked">
                <small>上限 15 m/s</small>
              </label>
              <label>USV 巡航速度 m/s
                <input v-model.number="state.usvSpeed" type="number" min="0" max="4" step="0.1" :disabled="sceneLocked">
                <small>上限 4 m/s</small>
              </label>
            </div>
            <div class="vf-actions">
              <button class="vf-button primary" type="button" :disabled="sceneLocked || !unityReady || scenarioLoading" @click="generateScenario">
                <RefreshCw :size="15" /> 生成场景
              </button>
              <button class="vf-button" type="button" :disabled="!unityReady" @click="resetMission">
                <CircleStop :size="15" /> 重置
              </button>
            </div>
            <p v-if="!speedValid" class="vf-error">速度超过协议上限，请修正后再开始任务。</p>
            <p v-if="missionActionMessage" class="vf-action-message">{{ missionActionMessage }}</p>
          </section>
        </aside>

        <section class="vf-stage-panel" :class="{ expanded: webglExpanded }">
          <div class="vf-stage-head">
            <div>
              <h3>算法仿真</h3>
            </div>
            <div class="vf-stage-actions">
              <strong>{{ stageCompositionLabel }}</strong>
              <span class="vf-unity-state" :class="{ online: unityReady }">
                <i></i>{{ unityReady ? 'UNITY WEBGL ONLINE' : 'UNITY WEBGL LOADING' }}
              </span>
              <button class="vf-expand" type="button" @click="toggleWebglExpanded">
                <Minimize2 v-if="webglExpanded" :size="16" />
                <Maximize2 v-else :size="16" />
                <span>{{ webglExpanded ? '退出放大' : '放大画面' }}</span>
              </button>
            </div>
          </div>
          <div class="vf-unity-stage" data-simulation-viewport></div>
          <div class="vf-live-strip">
            <span><i></i>阶段 <strong>{{ missionPhaseLabel }}</strong></span>
            <span>综合进度 <strong>{{ displayMissionProgress }}%</strong></span>
            <span>可见目标 <strong>{{ visibleTargetCount }}</strong></span>
            <span v-if="state.algorithm === 'GB_SFLA_CS'">行动距离 <strong>{{ Number(missionMetrics.targetTravelDistanceM ?? 0).toFixed(0) }} m</strong></span>
            <span v-else>已捕获 <strong>{{ Number(missionMetrics.capturedThreatCount ?? 0) }}/{{ scenarioPlan.threatCount }}</strong></span>
            <span v-if="state.algorithm !== 'GB_SFLA_CS' && postMissionFormationRequiredCount > 0">
              机动余量归队 <strong>{{ postMissionFormationReadyCount }}/{{ postMissionFormationRequiredCount }}</strong>
            </span>
            <span>仿真时长 <strong>{{ simulationElapsedLabel }}</strong></span>
          </div>
          <div class="vf-command-bar">
            <div class="vf-command-actions">
              <button class="vf-button success" type="button" title="开始任务" :disabled="state.mission === 'RUNNING' || !unityReady || !speedValid || scenarioLoading || algorithmPreparing || scenarioReadyRunId !== state.runId" @click="startMission">
                <Play :size="15" /> <span>{{ algorithmPreparing ? '准备中' : '开始' }}</span>
              </button>
              <button class="vf-button" type="button" title="暂停任务" :disabled="state.mission !== 'RUNNING'" @click="pauseMission">
                <Pause :size="15" /> <span>暂停</span>
              </button>
              <button class="vf-button danger" type="button" title="停止任务" :disabled="state.mission === 'STOPPED'" @click="stopMission">
                <CircleStop :size="15" /> <span>停止</span>
              </button>
            </div>
            <ol class="vf-phase-stepper">
              <li
                v-for="(step, index) in phaseSteps"
                :key="step"
                :title="step"
                :class="{ active: index === activePhaseIndex, done: index < activePhaseIndex }"
              >
                <span>{{ index + 1 }}</span><em>{{ step }}</em>
              </li>
            </ol>
            <div class="vf-camera-actions">
              <button type="button" :disabled="!selectedDevice || !unityReady" @click="followSelectedDevice">
                <Eye :size="15" /><span>跟随设备</span>
              </button>
              <button type="button" :class="{ active: cameraMode === 'overview' }" :disabled="!unityReady" @click="setOverviewCamera">
                <Globe2 :size="15" /><span>全局视角</span>
              </button>
            </div>
          </div>
        </section>

        <aside class="vf-inspector-drawer" :class="{ collapsed: rightPanelCollapsed }">
          <button
            class="vf-drawer-reopen right"
            type="button"
            title="展开任务检查区"
            :tabindex="rightPanelCollapsed ? 0 : -1"
            @click="setRightPanelCollapsed(false)"
          >
            <ChevronLeft :size="18" />
            <span>任务态势</span>
          </button>
          <section class="vf-panel vf-inspector-panel">
            <div class="vf-inspector-tabs">
              <button :class="{ active: inspectorTab === 'voice' }" type="button" @click="inspectorTab = 'voice'">语音控制</button>
              <button :class="{ active: inspectorTab === 'status' }" type="button" @click="inspectorTab = 'status'">任务态势</button>
              <button :class="{ active: inspectorTab === 'protocol' }" type="button" @click="inspectorTab = 'protocol'">协议状态</button>
              <button :class="{ active: inspectorTab === 'logs' }" type="button" @click="inspectorTab = 'logs'">运行日志</button>
              <button class="collapse" type="button" title="收起检查区" @click="setRightPanelCollapsed(true)"><ChevronRight :size="17" /></button>
            </div>

            <div v-if="inspectorTab === 'status'" class="vf-inspector-content">
              <article class="vf-status-card">
                <span>任务状态</span>
                <strong :class="state.mission.toLowerCase()">{{ state.mission }}</strong>
                <small>序列 {{ state.sequence }} · 阶段 {{ missionPhaseLabel }}</small>
              </article>

              <section class="vf-inspector-section">
                <h4>目标概览 <span>{{ visibleTargets.length }}</span></h4>
                <div v-if="visibleTargets.length" class="vf-target-list">
                  <article v-for="target in visibleTargets.slice(0, 6)" :key="target.code">
                    <div><strong>{{ target.code }}</strong><small>{{ target.type }}</small></div>
                    <span>{{ target.state === 'CENTER_DEFENSE' ? '居中防御' : target.state === 'COVERED_WITHDRAWAL' ? '掩护撤离中' : target.state || 'VISIBLE' }}</span>
                  </article>
                  <p v-if="visibleTargets.length > 6" class="vf-list-overflow">另有 {{ visibleTargets.length - 6 }} 个目标，任务指标仍按全部目标统计</p>
                </div>
                <p v-else class="vf-empty">生成场景后显示目标状态</p>
              </section>

              <section class="vf-inspector-section">
                <h4>任务指标</h4>
                <dl class="vf-metric-list">
                  <div><dt>综合进度</dt><dd>{{ displayMissionProgress }}%</dd></div>
                  <div><dt>可见目标</dt><dd>{{ visibleTargetCount }}</dd></div>
                  <template v-if="state.algorithm === 'GB_SFLA_CS'">
                    <div><dt>行动距离</dt><dd>{{ Number(missionMetrics.targetTravelDistanceM ?? 0).toFixed(0) }} m</dd></div>
                    <div><dt>闭环置信</dt><dd>{{ Math.round(Number(missionMetrics.containmentConfidence ?? 0) * 100) }}%</dd></div>
                    <div><dt>敌船速度</dt><dd>{{ Number(missionMetrics.targetSpeedMps ?? 0).toFixed(1) }} m/s</dd></div>
                    <div><dt>全局避障</dt><dd>{{ Number(missionMetrics.globalAvoidanceCount ?? 0) }}</dd></div>
                  </template>
                  <template v-else>
                    <div><dt>安全通航进度</dt><dd>{{ escortProgress }}%</dd></div>
                    <div><dt>围捕完成度</dt><dd>{{ captureProgress }}%</dd></div>
                    <div><dt>已捕获</dt><dd>{{ Number(missionMetrics.capturedThreatCount ?? 0) }}/{{ scenarioPlan.threatCount }}</dd></div>
                    <div>
                      <dt>兵力分工</dt>
                      <dd>近卫 {{ closeGuardCount }} · 围捕 {{ captureAssignedCount }} · 机动支援 {{ mobileSupportCount }}</dd>
                    </div>
                    <div v-if="postMissionFormationRequiredCount > 0">
                      <dt>机动余量归队</dt>
                      <dd>{{ postMissionFormationReadyCount }}/{{ postMissionFormationRequiredCount }} · {{ postMissionFormationProgress }}%</dd>
                    </div>
                    <div v-if="postMissionFormationRequiredCount > 0">
                      <dt>终态稳定</dt>
                      <dd>{{ postMissionStableFrames }}/{{ postMissionRequiredStableFrames }}</dd>
                    </div>
                    <div v-if="terminalBlockerLabel"><dt>完成阻塞</dt><dd>{{ terminalBlockerLabel }}</dd></div>
                  </template>
                  <div><dt>避障修正</dt><dd>{{ Number(missionMetrics.avoidanceCount ?? 0) }}</dd></div>
                  <div><dt>实际耗时</dt><dd>{{ missionElapsedLabel }}</dd></div>
                </dl>
              </section>

              <section v-if="tacticalHistory.length" class="vf-inspector-section">
                <h4>意图事件链 <span>{{ tacticalHistory.length }}</span></h4>
                <ol class="vf-tactical-history">
                  <li v-for="event in tacticalHistory.slice(0, 8)" :key="event.eventId">
                    <i></i>
                    <div>
                      <strong>{{ event.title }}</strong>
                      <span>{{ event.threatCode ? `${event.threatCode} · ` : '' }}{{ event.message }}</span>
                    </div>
                    <b v-if="event.type.endsWith('INTENT_CONFIRMED') && event.confidence">{{ Math.round(event.confidence * 100) }}%</b>
                  </li>
                </ol>
              </section>

              <section v-if="captureGroups.length" class="vf-inspector-section">
                <h4>
                  <span>围捕目标</span>
                  <span>实时闭环 {{ Number(missionMetrics.capturedThreatCount ?? missionMetrics.capturedTargetCount ?? 0) }}/{{ scenarioPlan.threatCount }}</span>
                </h4>
                <div class="vf-capture-groups">
                  <article v-for="group in captureGroups.slice(0, 4)" :key="group.threatCode">
                    <header class="vf-capture-group-head">
                      <strong>{{ group.threatCode }}</strong>
                      <b>{{ intentLabel(group.intent) }} · {{ Math.round(Number(group.intentConfidence ?? 0) * 100) }}%</b>
                    </header>
                    <span>阶段 {{ displayCaptureStage(group.stage) }}/3 · 当前编组 {{ group.uavCount }} UAV + {{ group.usvCount }} USV</span>
                    <dl class="vf-target-intelligence">
                      <div><dt>航速</dt><dd>{{ groupSpeed(group).toFixed(1) }}<small v-if="group.maximumSpeedMps">/{{ Number(group.maximumSpeedMps).toFixed(1) }}</small> m/s</dd></div>
                      <div><dt>航向</dt><dd>{{ normalizeHeading(group.targetHeadingDeg).toFixed(0) }}° · {{ compassDirection(group.targetHeadingDeg) }}</dd></div>
                      <div><dt>已移动</dt><dd>{{ Number(group.targetTravelDistanceM ?? group.pursuitDistanceM ?? 0).toFixed(0) }} m</dd></div>
                      <div><dt>当前决策</dt><dd>{{ speedReasonLabel(group) }}</dd></div>
                      <div><dt>逃逸缺口</dt><dd>{{ Number(group.postGlobalMaxGapDeg ?? group.maxAngularGapDeg ?? 360).toFixed(0) }}°<template v-if="group.gapCenterDeg !== undefined"> · 朝 {{ compassDirection(group.gapCenterDeg) }}</template></dd></div>
                      <div><dt>最近拦截</dt><dd>{{ group.nearestInterceptorCode || '暂无' }}<template v-if="group.nearestInterceptorDistanceM !== undefined"> · {{ Number(group.nearestInterceptorDistanceM).toFixed(0) }} m</template></dd></div>
                    </dl>
                    <small v-if="reassignmentLabel(group)" class="vf-decision-event">智能换组：{{ reassignmentLabel(group) }}</small>
                    <small>
                      执行槽位到位 {{ Number(group.arrivedMemberCount ?? Math.round(Number(group.arrivalRatio ?? 0) * Number(group.ringMemberCount ?? group.memberCount ?? 0))) }}/{{ Number(group.requiredMemberCount ?? group.ringMemberCount ?? group.memberCount ?? 0) }}
                      ({{ Math.round(Number(group.arrivalRatio ?? 0) * 100) }}%)
                      · 规划槽位缺口 {{ Number(group.maxAngularGapDeg ?? 360).toFixed(0) }}°
                    </small>
                    <small>稳定闭环 {{ group.holdFrames ?? 0 }}/{{ group.holdRequiredFrames ?? 25 }}</small>
                    <small v-if="state.algorithm === 'GB_SFLA_CS'">
                      实际闭环 {{ group.postGlobalContainmentReady ? '是' : '否' }}
                      · 执行环缺口 {{ Number(group.postGlobalMaxGapDeg ?? 0).toFixed(0) }}°（阈值 ≤ {{ Number(group.postGlobalMaxAllowedGapDeg ?? 0).toFixed(0) }}°）
                      · 分组避障 {{ Number(group.globalAvoidanceCount ?? 0) }}
                    </small>
                    <small v-if="group.detachedParticipantCodes?.length">待到位：{{ group.detachedParticipantCodes.join('、') }}</small>
                    <small v-if="group.captureBlocker && group.captureBlocker !== 'NONE'">阻塞：{{ group.captureBlocker }}</small>
                  </article>
                </div>
              </section>

              <section class="vf-inspector-section vf-selected-summary">
                <h4>选中设备</h4>
                <strong>{{ selectedDevice || '无' }}</strong>
                <span v-if="selectedFrameItem">{{ selectedFrameItem.type }} · {{ 'role' in selectedFrameItem ? selectedFrameItem.role : selectedFrameItem.state }}</span>
                <small v-if="roleSummary">角色分工：{{ roleSummary }}</small>
              </section>
            </div>

            <div v-else-if="inspectorTab === 'protocol'" class="vf-inspector-content">
              <article class="vf-protocol-health">
                <span><i :class="{ online: unityReady }"></i>Unity WebGL</span><strong>{{ unityReady ? 'ONLINE' : 'CONNECTING' }}</strong>
                <span>协议版本</span><strong>V3</strong>
                <span>运行模式</span><strong>VIRTUAL_SIMULATION</strong>
                <span>场景确认</span><strong>{{ scenarioReadyRunId === state.runId ? 'READY' : 'WAITING' }}</strong>
              </article>
              <pre>{{ protocolSnapshot }}</pre>
            </div>

            <div v-else-if="inspectorTab === 'logs'" class="vf-inspector-content vf-runtime-log">
              <p v-if="!logEntries.length" class="vf-empty">暂无运行日志</p>
              <ol v-else>
                <li v-for="entry in logEntries" :key="entry">{{ entry }}</li>
              </ol>
            </div>

            <div v-else class="vf-inspector-content">
              <VoiceP0ControlPanel
                ref="voiceControlPanel"
                :runtime-hint="voiceRuntimeHint"
                :unity-session="voiceUnitySession"
                @presentation-message="sendPresentationMessage"
              />
            </div>
          </section>
        </aside>
      </div>
    </div>
  </ConsoleLayout>
</template>

<style scoped>
.virtual-fleet-page { display: flex; height: calc(100dvh - 70px); min-height: 0; gap: 12px; overflow: hidden; flex-direction: column; }
.vf-app-header { display: grid; min-height: 58px; padding: 0 16px; align-items: center; color: #eafffb; background: rgba(5, 20, 25, .97); border: 1px solid rgba(108, 228, 213, .17); border-radius: 8px; grid-template-columns: minmax(220px, 1fr) auto minmax(220px, 1fr); }
.vf-app-title { display: flex; align-items: baseline; gap: 14px; }
.vf-app-title span { color: #8eb8b5; font-size: 11px; font-weight: 800; letter-spacing: .05em; }
.vf-app-title strong { font-size: 19px; }
.vf-workspace-switch { display: flex; align-items: center; padding: 3px; background: #06171c; border: 1px solid rgba(108, 228, 213, .14); border-radius: 6px; }
.vf-workspace-switch a, .vf-workspace-switch span { min-width: 108px; padding: 8px 16px; color: #789d9b; font-size: 12px; font-weight: 800; text-align: center; text-decoration: none; border-radius: 4px; }
.vf-workspace-switch .active { color: #effffd; background: rgba(108, 228, 213, .12); box-shadow: inset 0 -2px #6ce4d5; }
.vf-instance-status { display: flex; justify-self: end; align-items: center; gap: 7px; color: #9fe8df; font-size: 11px; font-weight: 800; }
.vf-instance-status i, .vf-unity-state i, .vf-live-strip i, .vf-protocol-health i { width: 7px; height: 7px; background: #62e4c9; border-radius: 50%; box-shadow: 0 0 9px rgba(98, 228, 201, .75); }
.vf-instance-status.offline { color: #8aa8a5; }
.vf-instance-status.offline i { background: #718987; box-shadow: none; }
.vf-workbench { --vf-left-width: clamp(238px, 15vw, 288px); --vf-right-width: clamp(248px, 15.6vw, 300px); --vf-current-left: var(--vf-left-width); --vf-current-right: var(--vf-right-width); display: grid; min-height: 0; overflow: hidden; flex: 1; gap: 12px; grid-template-rows: minmax(0, 1fr); grid-template-columns: var(--vf-current-left) minmax(0, 1fr) var(--vf-current-right); transition: grid-template-columns 240ms cubic-bezier(.22,.8,.3,1); }
.vf-workbench.left-collapsed { --vf-current-left: 44px; }
.vf-workbench.right-collapsed { --vf-current-right: 44px; }
.vf-config-drawer, .vf-inspector-drawer { position: relative; min-width: 0; min-height: 0; overflow: hidden; contain: layout paint; }
.vf-config-panel, .vf-inspector-panel, .vf-drawer-reopen { position: absolute; inset: 0; transition: opacity 150ms ease, transform 220ms cubic-bezier(.22,.8,.3,1), visibility 0s linear 0s; }
.vf-config-drawer:not(.collapsed) .vf-config-panel, .vf-inspector-drawer:not(.collapsed) .vf-inspector-panel { opacity: 1; visibility: visible; transform: translateX(0); pointer-events: auto; }
.vf-config-drawer:not(.collapsed) .vf-drawer-reopen, .vf-inspector-drawer:not(.collapsed) .vf-drawer-reopen { opacity: 0; visibility: hidden; pointer-events: none; }
.vf-config-drawer.collapsed .vf-config-panel { opacity: 0; visibility: hidden; transform: translateX(-12px); pointer-events: none; }
.vf-inspector-drawer.collapsed .vf-inspector-panel { opacity: 0; visibility: hidden; transform: translateX(12px); pointer-events: none; }
.vf-config-drawer.collapsed .vf-drawer-reopen, .vf-inspector-drawer.collapsed .vf-drawer-reopen { opacity: 1; visibility: visible; transform: translateX(0); pointer-events: auto; transition-delay: 90ms; }
.vf-panel, .vf-stage-panel { min-width: 0; color: #dff8f4; background: rgba(8, 25, 30, .94); border: 1px solid rgba(108, 228, 213, .18); border-radius: 8px; }
.vf-config-panel, .vf-inspector-panel { width: 100%; height: 100%; overflow: auto; }
.vf-config-panel { padding: 15px; }
.vf-panel-head, .vf-stage-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.vf-panel-head { margin-bottom: 13px; }
.vf-panel-head > div { display: flex; align-items: baseline; gap: 9px; }
.vf-panel-head button, .vf-inspector-tabs .collapse { display: grid; width: 28px; height: 28px; padding: 0; color: #83aaa6; cursor: pointer; place-items: center; background: transparent; border: 1px solid transparent; border-radius: 4px; }
.vf-panel-head button:hover, .vf-inspector-tabs .collapse:hover { color: #6ce4d5; border-color: rgba(108, 228, 213, .28); }
.vf-panel-head h3, .vf-stage-head h3 { margin: 0; color: #effffd; font-size: 15px; }
.vf-panel-head span, .vf-stage-head span { color: #6f9697; font-size: 10px; }
.vf-drawer-reopen { display: flex; width: 100%; height: 100%; padding: 12px 0; align-items: center; gap: 12px; flex-direction: column; color: #91b8b4; cursor: pointer; background: rgba(8,25,30,.94); border: 1px solid rgba(108,228,213,.18); border-radius: 8px; }
.vf-drawer-reopen span { font-size: 11px; letter-spacing: .15em; writing-mode: vertical-rl; }
.vf-drawer-reopen:hover { color: #6ce4d5; border-color: rgba(108,228,213,.4); }
.vf-panel label { display: grid; gap: 6px; margin-top: 11px; color: #9cc1bd; font-size: 11px; letter-spacing: 0; text-transform: none; }
.vf-panel input, .vf-panel select { min-height: 36px; padding: 0 9px; color: #eafffb; background: #07171c; border: 1px solid #28515a; border-radius: 4px; }
.vf-panel input:disabled, .vf-panel select:disabled { opacity: .55; }
.vf-panel small { color: #6f9697; font-size: 10px; }
.vf-two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 9px; }
.vf-description, .vf-note { margin-top: 10px; color: #8fb4b2; font-size: 11px; line-height: 1.6; }
.vf-plan-summary { display: grid; gap: 5px; margin-top: 10px; padding: 10px; border: 1px solid rgba(99,217,231,.24); border-radius: 5px; background: rgba(99,217,231,.05); }
.vf-plan-summary strong { color: #eafffb; font-size: 12px; }
.vf-plan-summary span, .vf-plan-summary small { color: #78aaa9; font-size: 10px; }
.vf-actions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 13px; }
.vf-button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 34px; padding: 0 10px; color: #dff8f4; background: rgba(108, 228, 213, .06); border: 1px solid rgba(108, 228, 213, .24); border-radius: 4px; cursor: pointer; }
.vf-button, .vf-expand, .vf-camera-actions button { white-space: nowrap; }
.vf-button:hover:not(:disabled) { border-color: #6ce4d5; color: #6ce4d5; }
.vf-button.primary { color: #061113; background: #6ce4d5; border-color: #6ce4d5; font-weight: 800; }
.vf-button.success { color: #68e6a8; border-color: rgba(104, 230, 168, .45); }
.vf-button.danger { color: #ff8179; border-color: rgba(255, 129, 121, .44); }
.vf-button.capture { color: #ffcf72; border-color: rgba(255,207,114,.5); }
.vf-button:disabled { cursor: not-allowed; opacity: .4; }
.vf-error { margin-top: 10px; color: #ff8179; font-size: 11px; }
.vf-action-message { margin: 10px 0 0; color: #9fe8df; font-size: 11px; line-height: 1.5; }
.vf-stage-panel { position: relative; display: flex; height: 100%; min-height: 0; padding: 0; overflow: hidden; flex-direction: column; }
.vf-stage-head { padding: 14px 15px; border-bottom: 1px solid rgba(108, 228, 213, .15); }
.vf-stage-head > div:first-child { display: grid; gap: 3px; }
.vf-stage-head strong { color: #ffcf72; font-size: 12px; }
.vf-stage-actions { display: flex; align-items: center; gap: 10px; }
.vf-unity-state { display: inline-flex; align-items: center; gap: 6px; padding: 6px 8px; color: #7c9997; background: #06171c; border: 1px solid rgba(108,228,213,.15); border-radius: 4px; font-size: 9px; font-weight: 800; }
.vf-unity-state i { background: #718987; box-shadow: none; }
.vf-unity-state.online { color: #c7fff6; }
.vf-unity-state.online i { background: #62e4c9; box-shadow: 0 0 9px rgba(98,228,201,.75); }
.vf-expand { display: inline-flex; align-items: center; gap: 6px; min-height: 30px; padding: 0 9px; color: #dff8f4; background: #092027; border: 1px solid rgba(108,228,213,.3); border-radius: 4px; cursor: pointer; }
.vf-stage-panel.expanded { position: fixed; inset: 10px; z-index: 2100; display: flex; flex-direction: column; background: #031015; box-shadow: 0 0 0 100vmax rgba(0,0,0,.82); }
.vf-stage-panel.expanded .vf-unity-stage { flex: 1; min-height: 0; }
.vf-stage-panel.expanded .vf-unity-stage :deep(.unity-webgl-panel) { height: 100%; }
.vf-unity-stage { height: 100%; min-height: 0; flex: 1; overflow: hidden; background: #031015; }
.vf-unity-stage :deep(.unity-webgl-panel) { width: 100%; height: 100%; min-height: 0; }
.vf-tactical-history { display: grid; margin: 0; padding: 0; gap: 7px; list-style: none; }
.vf-tactical-history li { display: grid; padding: 7px 0; align-items: start; gap: 7px; border-bottom: 1px solid rgba(108,228,213,.1); grid-template-columns: auto minmax(0,1fr) auto; }
.vf-tactical-history li:last-child { border-bottom: 0; }
.vf-tactical-history i { width: 7px; height: 7px; margin-top: 4px; background: #6ce4d5; border-radius: 50%; box-shadow: 0 0 8px rgba(108,228,213,.75); }
.vf-tactical-history div { display: grid; min-width: 0; gap: 2px; }
.vf-tactical-history strong { color: #eafffb; font-size: 10px; }
.vf-tactical-history span { color: #7fa7a5; font-size: 9px; line-height: 1.45; }
.vf-tactical-history b { color: #ffcf72; font-size: 10px; }
.vf-live-strip { display: flex; min-height: 34px; padding: 0 13px; align-items: center; flex-wrap: wrap; gap: 8px 18px; color: #7ea7a5; background: #06191f; border-top: 1px solid rgba(108,228,213,.16); border-bottom: 1px solid rgba(108,228,213,.1); font-size: 10px; }
.vf-live-strip span { display: inline-flex; align-items: center; gap: 5px; }
.vf-live-strip strong { color: #eafffb; font-size: 11px; }
.vf-command-bar { display: grid; min-height: 94px; padding: 9px 12px; align-items: center; gap: 8px 14px; background: #06151a; grid-template-columns: auto 1fr; grid-template-areas: 'commands cameras' 'steps steps'; }
.vf-command-actions { grid-area: commands; }
.vf-camera-actions { grid-area: cameras; justify-self: end; }
.vf-phase-stepper { grid-area: steps; }
.vf-command-actions, .vf-camera-actions { display: flex; align-items: center; gap: 7px; }
.vf-command-actions .vf-button { margin: 0; }
.vf-camera-actions button { display: inline-flex; min-height: 32px; padding: 0 9px; align-items: center; gap: 5px; color: #b8d8d4; cursor: pointer; background: #081e24; border: 1px solid rgba(108,228,213,.22); border-radius: 4px; font-size: 10px; }
.vf-camera-actions button.active, .vf-camera-actions button:hover:not(:disabled) { color: #6ce4d5; border-color: rgba(108,228,213,.5); }
.vf-camera-actions button:disabled { cursor: not-allowed; opacity: .38; }
.vf-phase-stepper { display: flex; min-width: 0; overflow-x: auto; margin: 0; padding: 0 2px; align-items: center; justify-content: center; list-style: none; }
.vf-phase-stepper li { display: flex; min-width: 70px; align-items: center; gap: 6px; color: #617e7c; font-size: 10px; font-weight: 800; }
.vf-phase-stepper li:not(:last-child)::after { height: 1px; min-width: 16px; margin: 0 6px; flex: 1; content: ''; background: #274044; }
.vf-phase-stepper li span { display: grid; width: 22px; height: 22px; flex: 0 0 auto; place-items: center; border: 1px solid #385155; border-radius: 50%; }
.vf-phase-stepper li em { font-style: normal; white-space: nowrap; }
.vf-phase-stepper li.done { color: #76cfc4; }
.vf-phase-stepper li.done span { border-color: #43b8aa; }
.vf-phase-stepper li.active { color: #ffcf72; }
.vf-phase-stepper li.active span { color: #061113; background: #ffcf72; border-color: #ffcf72; }
.vf-inspector-panel { padding: 0; }
.vf-inspector-tabs { position: sticky; top: 0; z-index: 2; display: flex; min-height: 46px; padding: 0 8px; align-items: stretch; background: #07191e; border-bottom: 1px solid rgba(108,228,213,.16); }
.vf-inspector-tabs > button:not(.collapse) { position: relative; padding: 0 8px; color: #789c99; cursor: pointer; background: transparent; border: 0; font-size: 11px; font-weight: 800; }
.vf-inspector-tabs > button.active { color: #effffd; }
.vf-inspector-tabs > button.active::after { position: absolute; right: 8px; bottom: 0; left: 8px; height: 2px; content: ''; background: #6ce4d5; }
.vf-inspector-tabs .collapse { margin: auto 0 auto auto; }
.vf-inspector-content { display: grid; gap: 0; }
.vf-status-card, .vf-inspector-section { padding: 15px; border-bottom: 1px solid rgba(108,228,213,.12); }
.vf-status-card { display: grid; gap: 6px; }
.vf-status-card > span { color: #86aaa7; font-size: 10px; }
.vf-status-card > strong { color: #ffcf72; font-size: 20px; }
.vf-status-card > strong.completed { color: #66e4ad; }
.vf-status-card > strong.failed, .vf-status-card > strong.timeout { color: #ff8179; }
.vf-status-card small { color: #6f9693; font-size: 10px; }
.vf-inspector-section h4 { display: flex; margin: 0 0 10px; align-items: center; justify-content: space-between; color: #eafffb; font-size: 12px; }
.vf-inspector-section h4 span { color: #6ce4d5; font-size: 10px; }
.vf-target-list, .vf-capture-groups { display: grid; gap: 7px; }
.vf-target-list article, .vf-capture-groups article { display: flex; padding: 9px; align-items: center; justify-content: space-between; gap: 8px; background: rgba(3,16,20,.58); border: 1px solid rgba(108,228,213,.12); border-radius: 4px; }
.vf-target-list article div, .vf-capture-groups article { display: grid; }
.vf-target-list strong, .vf-capture-groups strong { color: #ffcf72; font-size: 10px; }
.vf-target-list small, .vf-capture-groups span, .vf-capture-groups small { color: #789c99; font-size: 9px; }
.vf-target-list article > span { color: #dff8f4; font-size: 9px; }
.vf-capture-group-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.vf-capture-group-head b { color: #6ce4d5; font-size: 9px; font-weight: 800; }
.vf-target-intelligence { display: grid; margin: 2px 0 1px; gap: 4px 8px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
.vf-target-intelligence div { display: grid; min-width: 0; padding: 4px 5px; gap: 2px; background: rgba(21, 67, 70, .18); border-radius: 3px; }
.vf-target-intelligence dt { color: #648b88; font-size: 8px; }
.vf-target-intelligence dd { min-width: 0; margin: 0; overflow: hidden; color: #d8f5f1; font-size: 9px; font-weight: 700; text-overflow: ellipsis; white-space: nowrap; }
.vf-target-intelligence dd small { color: #9fc0bd; }
.vf-capture-groups .vf-decision-event { padding: 5px 6px; color: #b8f4e9; background: rgba(63, 190, 169, .1); border-left: 2px solid #55d9c7; }
.vf-list-overflow { margin: 1px 0 0; color: #6f9693; font-size: 9px; line-height: 1.45; }
.vf-metric-list { display: grid; margin: 0; gap: 0; }
.vf-metric-list div { display: flex; padding: 7px 0; align-items: center; justify-content: space-between; border-bottom: 1px solid rgba(108,228,213,.07); }
.vf-metric-list dt { color: #789c99; font-size: 10px; }
.vf-metric-list dd { margin: 0; color: #eafffb; font-size: 11px; font-weight: 800; }
.vf-selected-summary { display: grid; gap: 5px; }
.vf-selected-summary h4 { margin-bottom: 4px; }
.vf-selected-summary > strong { color: #ffcf72; font-size: 12px; }
.vf-selected-summary > span, .vf-selected-summary > small { overflow: hidden; color: #789c99; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; }
.vf-empty { margin: 0; color: #668885; font-size: 10px; }
.vf-protocol-health { display: grid; padding: 15px; align-items: center; gap: 9px 8px; color: #789c99; grid-template-columns: 1fr auto; font-size: 10px; border-bottom: 1px solid rgba(108,228,213,.12); }
.vf-protocol-health span { display: flex; align-items: center; gap: 6px; }
.vf-protocol-health strong { color: #dff8f4; font-size: 9px; }
.vf-protocol-health i { background: #718987; box-shadow: none; }
.vf-protocol-health i.online { background: #62e4c9; box-shadow: 0 0 9px rgba(98,228,201,.75); }
.vf-inspector-content pre { max-height: 470px; overflow: auto; margin: 12px; padding: 10px; color: #bde8e0; background: #061116; border: 1px solid #203c43; border-radius: 4px; font: 9px/1.55 Consolas, monospace; white-space: pre-wrap; word-break: break-word; }
.vf-runtime-log { padding: 12px; }
.vf-runtime-log ol { display: grid; max-height: 650px; overflow: auto; margin: 0; padding: 0; gap: 5px; list-style: none; }
.vf-runtime-log li { padding: 7px 8px; color: #86aaa7; background: rgba(3,16,20,.55); border-left: 2px solid rgba(108,228,213,.25); font: 9px/1.45 Consolas, monospace; word-break: break-all; }
@media (max-width: 1500px) {
  .vf-workbench { --vf-left-width: 220px; --vf-right-width: 232px; gap: 9px; }
  .vf-workbench.left-collapsed { --vf-current-left: 42px; }
  .vf-workbench.right-collapsed { --vf-current-right: 42px; }
  .vf-app-title span { display: none; }
  .vf-app-header { min-height: 52px; }
  .vf-workspace-switch a, .vf-workspace-switch span { min-width: 90px; padding: 7px 12px; }
  .vf-command-bar { min-height: 86px; padding: 7px 9px; gap: 8px; grid-template-columns: auto 1fr; grid-template-areas: 'commands cameras' 'steps steps'; }
  .vf-command-actions, .vf-camera-actions { gap: 5px; }
  .vf-command-actions .vf-button { width: 32px; padding: 0; }
  .vf-command-actions .vf-button span { display: none; }
  .vf-camera-actions button { width: 32px; padding: 0; justify-content: center; }
  .vf-camera-actions button span { display: none; }
  .vf-stage-actions > strong, .vf-unity-state, .vf-expand span { display: none; }
  .vf-expand { width: 32px; padding: 0; justify-content: center; }
  .vf-phase-stepper li { min-width: 52px; }
  .vf-phase-stepper li:not(:last-child)::after { min-width: 8px; margin: 0 3px; }
  .vf-unity-stage, .vf-unity-stage :deep(.unity-webgl-panel) { min-height: clamp(300px, calc(100vh - 330px), 650px); }
}
@media (max-width: 1500px) and (min-width: 1201px) {
  .vf-unity-stage, .vf-unity-stage :deep(.unity-webgl-panel) { min-height: 0; }
}
@media (max-width: 1200px) {
  .virtual-fleet-page { height: auto; min-height: calc(100dvh - 70px); overflow: visible; }
  .vf-app-header { grid-template-columns: 1fr auto; }
  .vf-workspace-switch { display: none; }
  .vf-workbench, .vf-workbench.left-collapsed, .vf-workbench.right-collapsed, .vf-workbench.left-collapsed.right-collapsed { overflow: visible; grid-template-columns: minmax(210px, 240px) minmax(440px, 1fr); grid-template-rows: auto; }
  .vf-inspector-drawer { grid-column: 1 / -1; min-height: 320px; }
  .vf-inspector-panel { max-height: 420px; }
  .vf-camera-actions button { width: auto; padding: 0 9px; }
  .vf-camera-actions button span { display: inline; }
}
@media (max-width: 800px) {
  .vf-app-header { grid-template-columns: 1fr; gap: 8px; padding: 10px 12px; }
  .vf-instance-status { justify-self: start; }
  .vf-workbench, .vf-workbench.left-collapsed, .vf-workbench.right-collapsed, .vf-workbench.left-collapsed.right-collapsed { grid-template-columns: 1fr; }
  .vf-config-drawer, .vf-inspector-drawer { grid-column: auto; }
  .vf-drawer-reopen { min-height: 42px; flex-direction: row; justify-content: center; }
  .vf-drawer-reopen span { writing-mode: horizontal-tb; }
  .vf-unity-stage, .vf-unity-stage :deep(.unity-webgl-panel) { min-height: 380px; }
  .vf-two-col { grid-template-columns: 1fr; }
  .vf-stage-actions strong, .vf-unity-state { display: none; }
  .vf-command-bar { min-height: 126px; grid-template-columns: 1fr; grid-template-areas: 'commands' 'cameras' 'steps'; }
  .vf-command-actions, .vf-camera-actions { justify-content: center; }
  .vf-command-actions .vf-button { width: auto; padding: 0 10px; }
  .vf-command-actions .vf-button span { display: inline; }
  .vf-camera-actions { grid-column: auto; justify-self: center; }
  .vf-phase-stepper { overflow-x: auto; justify-content: flex-start; }
}
@media (max-width: 1400px) and (min-width: 801px) {
  .vf-phase-stepper li { min-width: 34px; }
  .vf-phase-stepper li em { display: none; }
}
@media (max-height: 850px) and (min-width: 1201px) {
  .virtual-fleet-page { height: calc(100dvh - 70px); min-height: 0; gap: 8px; }
  .vf-app-header { min-height: 46px; }
  .vf-workbench { gap: 8px; }
  .vf-config-panel { padding: 12px; }
  .vf-panel-head { margin-bottom: 8px; }
  .vf-panel label { gap: 4px; margin-top: 7px; }
  .vf-panel input, .vf-panel select { min-height: 31px; }
  .vf-description { margin: 7px 0 0; line-height: 1.4; }
  .vf-plan-summary { margin-top: 7px; padding: 7px; }
  .vf-actions { margin-top: 9px; }
  .vf-stage-head { padding: 9px 12px; }
  .vf-unity-stage, .vf-unity-stage :deep(.unity-webgl-panel) { min-height: 0; }
  .vf-live-strip { min-height: 29px; }
  .vf-command-bar { min-height: 86px; padding-top: 5px; padding-bottom: 5px; }
  .vf-status-card, .vf-inspector-section { padding: 11px; }
  .vf-target-list article, .vf-capture-groups article { padding: 6px; }
  .vf-metric-list div { padding: 5px 0; }
}
@media (min-width: 2200px) {
  .vf-workbench { --vf-left-width: 310px; --vf-right-width: 320px; gap: 16px; }
  .vf-app-header { min-height: 64px; padding-right: 22px; padding-left: 22px; }
  .vf-config-panel { padding: 18px; }
  .vf-stage-head { padding: 16px 18px; }
  .vf-command-bar { min-height: 72px; padding-right: 16px; padding-left: 16px; }
  .vf-camera-actions button { min-height: 36px; padding: 0 12px; font-size: 11px; }
}
@container workspace (max-width: 1050px) {
  .virtual-fleet-page { height: auto; min-height: calc(100dvh - 40px); overflow: visible; }
  .vf-app-header { grid-template-columns: minmax(0, 1fr) auto; gap: 8px; padding-block: 10px; }
  .vf-workspace-switch { display: flex; }
  .vf-instance-status { grid-column: 1 / -1; justify-self: start; }
  .vf-workbench, .vf-workbench.left-collapsed, .vf-workbench.right-collapsed, .vf-workbench.left-collapsed.right-collapsed {
    overflow: visible; grid-template-columns: var(--vf-current-left) minmax(0, 1fr); grid-template-rows: auto;
  }
  .vf-inspector-drawer { grid-column: 1 / -1; min-height: 0; }
  .vf-inspector-panel { position: relative; height: auto; max-height: 420px; }
  .vf-inspector-drawer.collapsed .vf-inspector-panel { position: absolute; }
  .vf-inspector-drawer.collapsed .vf-drawer-reopen { position: relative; height: 42px; }
  .vf-unity-stage, .vf-unity-stage :deep(.unity-webgl-panel) { min-height: 340px; }
  .vf-inspector-drawer.collapsed .vf-drawer-reopen { min-height: 42px; flex-direction: row; justify-content: center; }
  .vf-inspector-drawer.collapsed .vf-drawer-reopen span { writing-mode: horizontal-tb; }
  .vf-stage-actions { flex-wrap: wrap; }
}
@container workspace (max-width: 700px) {
  .vf-app-header { grid-template-columns: minmax(0, 1fr); }
  .vf-workbench, .vf-workbench.left-collapsed, .vf-workbench.right-collapsed, .vf-workbench.left-collapsed.right-collapsed { grid-template-columns: minmax(0, 1fr); }
  .vf-config-drawer, .vf-inspector-drawer { grid-column: 1; }
  .vf-config-panel { position: relative; height: auto; max-height: 560px; }
  .vf-config-drawer.collapsed .vf-config-panel { position: absolute; }
  .vf-config-drawer.collapsed .vf-drawer-reopen { position: relative; height: 42px; }
  .vf-drawer-reopen { min-height: 42px; flex-direction: row; justify-content: center; }
  .vf-drawer-reopen span { writing-mode: horizontal-tb; }
  .vf-command-bar { grid-template-columns: minmax(0, 1fr); grid-template-areas: 'commands' 'cameras' 'steps'; }
  .vf-camera-actions, .vf-command-actions { justify-self: stretch; flex-wrap: wrap; justify-content: center; }
  .vf-phase-stepper { justify-content: flex-start; }
  .vf-phase-stepper li { flex-shrink: 0; min-width: 76px; }
  .vf-stage-head { flex-wrap: wrap; gap: 8px; }
}
</style>

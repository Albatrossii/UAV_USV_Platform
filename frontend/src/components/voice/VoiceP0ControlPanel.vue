<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { AudioLines, Check, RefreshCw, ShieldCheck, X } from '@lucide/vue'

import {
  configureVoiceP0MockRuntime,
  setVoiceP0MockOutcome,
  voiceP0MockEnabled,
} from '@/api/voiceControl'
import { useVoiceControlStore } from '@/stores/voiceControl'
import { useAuthStore } from '@/stores/auth'
import type {
  UnityPresentationIncoming,
  UnityPresentationOutgoing,
  VoiceAction,
  VoiceIntent,
  VoiceFrozenPlan,
  VoiceMockOutcome,
  VoiceMockRuntimeHint,
} from '@/types/voiceControl'
import type { UnityWindowMessage } from '@/utils/unityWebglProtocol'
import { unwrapUnityPresentationMessage } from '@/utils/unityWebglProtocol'
import VoiceIntelligenceInput from './VoiceIntelligenceInput.vue'
import { sequenceStateValid, voiceStepLabel } from '@/utils/voiceSequence'
import { deviceActionProgress } from '@/utils/deviceActionProgress'
import { appendExperimentEvent } from '@/services/experimentArchive'
import type { VoiceParseResult } from '@/types/voiceIntelligence'
import type { AlgorithmRuntimeFrame } from '@/types/mission'
import LocalAsrInput from './LocalAsrInput.vue'

interface UnityPresentationSession {
  connected: boolean
  unityInstanceId: string
  sceneRevision: number
}

const props = withDefaults(defineProps<{
  runtimeHint: VoiceMockRuntimeHint
  unitySession: UnityPresentationSession
  refined?: boolean
  frame?: AlgorithmRuntimeFrame | null
}>(), { refined: false })
const emit = defineEmits<{ presentationMessage: [message: UnityPresentationOutgoing] }>()
const store = useVoiceControlStore()
const authStore = useAuthStore()
const {
  context, proposal, execution, presentationBinding, loading, error, errorCode,
  recoveryPending, recoveryAvailable,
  responseUnknown,
  presentationChallenge,
} = storeToRefs(store)
const now = ref(Date.now())
const dialogOpen = ref(false)
const chosenMockOutcome = ref<VoiceMockOutcome>('SUCCESS')
const presentationPendingSince = ref<number | null>(null)
const presentationBridgeEnabled = import.meta.env.VITE_VOICE_UNITY_PRESENTATION_V1 === 'true'
const voiceP1PreparationEnabled = import.meta.env.VITE_VOICE_P1_PREPARATION === 'true'
const voiceP1BackendEnabled = import.meta.env.VITE_VOICE_P1_BACKEND === 'true'
const asrOnly = import.meta.env.VITE_VOICE_ASR_ONLY === 'true'
const voiceAutomationStatus = ref('')
const automaticProposalId = ref<string | null>(null)
const deviceActionStartSequence = ref(0)
function recordInterpretation(event: { text: string; origin: string; result: VoiceParseResult }) {
  appendExperimentEvent(authStore.user?.username ?? '', Number(props.runtimeHint.algorithmRunId), {
    id: `interpretation:${event.result.requestId}`, at: new Date().toISOString(), kind: '语义解析',
    detail: { text: event.text, origin: event.origin, ...event.result },
  })
}
watch(() => execution.value && JSON.stringify(execution.value), () => {
  const item = execution.value
  if (!item || item.runtimeRef !== context.value?.runtimeRef) return
  appendExperimentEvent(authStore.user?.username ?? '', Number(props.runtimeHint.algorithmRunId), {
    id: `${item.executionId}:${item.state}:${item.currentStepIndex ?? 0}:${item.sequenceStatus ?? ''}`,
    at: new Date().toISOString(), kind: '执行回执',
    detail: { executionId: item.executionId, action: item.action, state: item.state,
      steps: item.steps, outcome: item.outcome, errorCode: item.errorCode,
      targetDeviceCode: proposal.value?.plan.targetDeviceCode, deviceCommandType: proposal.value?.plan.deviceCommandType },
  })
})
watch(() => execution.value?.executionId, () => { deviceActionStartSequence.value = props.frame?.sequence ?? 0 })
const deviceProgress = computed(() => execution.value?.state === 'SUCCEEDED'
  && execution.value.action === 'DEVICE_COMMAND'
  && execution.value.proposalId === proposal.value?.proposalId
  && String(props.frame?.runId) === props.runtimeHint.algorithmRunId
    ? deviceActionProgress(props.frame, proposal.value?.plan.targetDeviceCode, proposal.value?.plan.deviceCommandType, deviceActionStartSequence.value) : null)
const voiceActionStartedAt = ref<number | null>(null)
const voiceActionFinishedAt = ref<number | null>(null)
const voiceActionIncludesRecognition = ref(false)
const voicePipelineMetrics = ref<{
  asrRequestMs: number
  parseMs: number
  proposalMs: number | null
  submitAndAckMs: number | null
  totalMs: number | null
} | null>(null)
let voiceSubmitStartedAt: number | null = null
const voiceAutomationDisplay = computed(() => {
  const result = execution.value
  const elapsedEnd = voiceActionFinishedAt.value ?? now.value
  const elapsed = voiceActionStartedAt.value === null ? 0 : Math.max(0, Math.floor((elapsedEnd - voiceActionStartedAt.value) / 1000))
  const isCurrentManualDeviceExecution = Boolean(
    result
    && result.proposalId === proposal.value?.proposalId
    && proposal.value.plan.action === 'DEVICE_COMMAND'
  )
  if (isCurrentManualDeviceExecution) {
    const command = proposal.value!.plan.deviceCommandType ?? ''
    const target = proposal.value!.plan.targetDeviceCode ?? '目标设备'
    return `单设备指令：${target} · ${actionLabels[command] ?? command} · ${executionLabels[result!.state] ?? result!.state}。`
  }
  if (!automaticProposalId.value || result?.proposalId !== automaticProposalId.value) {
    return voiceAutomationStatus.value && voiceActionStartedAt.value !== null
      ? `${voiceAutomationStatus.value}（录音结束后累计 ${elapsed} 秒）`
      : voiceAutomationStatus.value
  }
  const metrics = voicePipelineMetrics.value
  if (voiceActionFinishedAt.value !== null && voiceActionIncludesRecognition.value && metrics?.totalMs !== null && metrics?.totalMs !== undefined) {
    const parts = [`上传/识别请求 ${formatSeconds(metrics.asrRequestMs)}`, `指令解析 ${formatSeconds(metrics.parseMs)}`]
    if (metrics.proposalMs !== null) parts.push(`提案 ${formatSeconds(metrics.proposalMs)}`)
    if (metrics.submitAndAckMs !== null) parts.push(`提交至页面收到回执 ${formatSeconds(metrics.submitAndAckMs)}`)
    return `仿真动作：${executionLabels[result.state] ?? result.state}；录音结束至算法回执 ${formatSeconds(metrics.totalMs)}（${parts.join(' · ')}）；Unity ${presentationLabels[result.presentationStatus] ?? result.presentationStatus}。`
  }
  const durationLabel = voiceActionFinishedAt.value === null ? '处理中'
    : voiceActionIncludesRecognition.value ? '录音结束至算法回执' : '算法回执耗时'
  return `仿真动作：${executionLabels[result.state] ?? result.state}；${durationLabel} ${elapsed} 秒；Unity ${presentationLabels[result.presentationStatus] ?? result.presentationStatus}。`
})

function formatSeconds(milliseconds: number) {
  return `${(milliseconds / 1000).toFixed(2)} 秒`
}
const runtimeEnded = computed(() => !!context.value && (
  ['STOPPED', 'CANCELLED', 'COMPLETED', 'FAILED', 'LOST'].includes(context.value.state)
  || (execution.value?.runtimeRef === context.value.runtimeRef
    && execution.value.runtimeGeneration === context.value.runtimeGeneration
    && execution.value.action === 'STOP' && execution.value.state === 'SUCCEEDED')
))
const activeVoiceContext = computed(() => context.value && !runtimeEnded.value ? context.value : null)
const presentationBridgeReady = ref(false)
const presentationBridgeStatus = ref(presentationBridgeEnabled ? '等待展示绑定' : 'E03 未启用')
const presentationBridgeDisplay = computed(() => execution.value?.state === 'SUCCEEDED'
  && execution.value.presentationStatus === 'REPORTED_APPLIED'
  && execution.value.runtimeRef === context.value?.runtimeRef
  && execution.value.runtimeGeneration === context.value?.runtimeGeneration
  ? '展示证据已提交'
  : presentationBridgeStatus.value)
const helloAttempts = ref(0)
const lastSceneProbeAt = ref<number | null>(null)
const lastAutoBindingKey = ref('')
let pendingFrameResync = false
let presentationRequestInFlight = false
let presentationBindingInFlight = false
let autoResyncExecutionId = ''
let autoResyncAttempts = 0
let lastAutoResyncAt: number | null = null
let timer: number | undefined
let pollTick = 0

const actions: Array<{ action: VoiceAction; intent: VoiceIntent; label: string }> = [
  { action: 'START', intent: 'MISSION_START', label: '开始' },
  { action: 'PAUSE', intent: 'MISSION_PAUSE', label: '暂停' },
  { action: 'RESUME', intent: 'MISSION_RESUME', label: '继续' },
  { action: 'STOP', intent: 'MISSION_STOP', label: '停止' },
]
const stateRules: Record<VoiceAction, string[]> = {
  START: ['PREPARED', 'PREVIEW'], PAUSE: ['RUNNING'], RESUME: ['PAUSED'], STOP: ['PREPARED', 'PREVIEW', 'RUNNING', 'PAUSED'],
}
const actionLabels: Record<string, string> = {
  START: '开始任务', PAUSE: '暂停任务', RESUME: '继续任务', STOP: '停止任务', DEVICE_COMMAND: '单设备控制', SEQUENCE: '受控顺序指令',
  UAV_HOVER: '无人机悬停', UAV_RESUME: '无人机归队', UAV_RETURN: '无人机返航', UAV_LAND: '无人机降落',
  USV_HOLD: '无人艇驻留', USV_RESUME: '无人艇归队', USV_RETURN: '无人艇返航', USV_STOP: '无人艇停止',
}
const executionLabels: Record<string, string> = {
  QUEUED: '等待调度', DISPATCHED: '已下发', ACCEPTED: '算法已接收', EXECUTING: '算法执行中',
  SUCCEEDED: '算法执行成功', REJECTED: '算法拒绝', FAILED: '算法执行失败',
  INVALIDATED: '执行已失效', TIMED_OUT: '结果未知（超时，仍在查询）',
}
const presentationLabels: Record<string, string> = {
  NOT_REQUIRED: '无需 Unity 同步', PENDING: 'Unity 同步中', REPORTED_APPLIED: 'Unity 已应用', STALE: 'Unity 展示已过期',
}
const errorLabels: Record<string, string> = {
  CONTEXT_CHANGED: '运行上下文、设备集合或展示绑定已变化，请刷新后重新发起。',
  GENERATION_MISMATCH: '场景已重新生成，旧代次不能继续使用。',
  INVALID_REQUEST: '请求格式或版本不受支持，请刷新页面后重试。',
  PLAN_MISMATCH: '提案信息不一致，请重新获取提案。',
  INVALID_STATE: '当前算法状态不接受该动作。',
  HEARTBEAT_STALE: '算法心跳已失效，暂时不能下发动作。',
  RUNTIME_UNAVAILABLE: '算法进程当前不可用。',
  SCENE_NOT_READY: 'Unity 场景新鲜就绪证据不足。',
  EXECUTION_IN_PROGRESS: '当前实例已有未决执行，未知结果仍会占用执行槽。',
  RUNTIME_BUSY: '独立算法运行槽正在被占用。',
  IDEMPOTENCY_CONFLICT: '幂等键与原请求内容不一致，已停止重试。',
  PROPOSAL_EXPIRED: '提案已过期，请重新创建。',
  PROPOSAL_CANCELLED: '提案已经取消。',
  PROPOSAL_INVALIDATED: '运行条件发生变化，提案已经失效。',
  ALREADY_CONFIRMED: '提案已经确认，不能再取消。',
  UNSUPPORTED_CAPABILITY: '算法实例不支持该动作能力。',
  PROTOCOL_UNSUPPORTED: '当前算法协议不支持 P0 控制。',
  VOICE_CONTROL_DISABLED: '后端尚未启用语音控制 P0 功能开关。',
  ACK_TIMEOUT: '算法回执超时，最终结果仍未知，页面会继续查询。',
}

const secondsLeft = computed(() => proposal.value
  ? Math.max(0, Math.ceil((Date.parse(proposal.value.expiresAt) - now.value) / 1000))
  : 0)
const activeProposal = computed(() => proposal.value?.status === 'AWAITING_CONFIRMATION')
const refinedDraftRevision = ref(0)
const refinedAcceptedRevision = ref(-1)
const refinedAcceptedProposalId = ref('')
const refinedPreparing = ref(false)
const refinedConfirming = ref(false)
const refinedCancelling = ref(false)
const refinedRestoredProposalId = ref('')
const refinedPrepareError = ref('')
const refinedAutomaticRunning = ref(false)
const refinedAutomaticPlanId = ref('')
const automaticInterpretations = new Set<string>()
let panelActive = true
const commandInFlight = computed(() => !!execution.value
  && !['SUCCEEDED', 'REJECTED', 'FAILED', 'INVALIDATED'].includes(execution.value.state))
const refinedProposalStale = computed(() => proposal.value?.proposalId !== refinedAcceptedProposalId.value
  || refinedDraftRevision.value !== refinedAcceptedRevision.value)
const refinedProposalReady = computed(() => !!activeProposal.value && !refinedProposalStale.value && secondsLeft.value > 0)
const refinedSubmissionDisabled = computed(() => loading.value || refinedPreparing.value || refinedConfirming.value || refinedCancelling.value
  || recoveryPending.value || responseUnknown.value || !recoveryAvailable.value
  || !activeVoiceContext.value || commandInFlight.value)
const refinedPlanSteps = computed(() => {
  const plan = proposal.value?.plan
  if (!plan) return []
  if (plan.action === 'SEQUENCE') return (plan.steps ?? []).map(step => ({
    index: step.index,
    label: voiceStepLabel(step),
  }))
  return [{ index: 0, label: plan.action === 'DEVICE_COMMAND'
    ? `${plan.targetDeviceCode} · ${actionLabels[plan.deviceCommandType ?? ''] ?? plan.deviceCommandType}`
    : actionLabels[plan.action] ?? plan.action }]
})
const refinedConfirmReason = computed(() => {
  const plan = proposal.value?.plan
  if (authStore.user?.role !== 'ADMIN' || authStore.loading) return '当前账号没有控制权限'
  if (refinedSubmissionDisabled.value) return responseUnknown.value ? '上次请求结果待核对，请勿重复执行' : '正在同步指令状态，请稍候'
  if (!activeProposal.value || !plan) return '请先预览指令'
  if (secondsLeft.value === 0) return '指令已过期，请重新预览'
  if (refinedProposalStale.value) return '文字或场景已变化，请重新预览指令'
  if (plan.runtimeRef !== context.value?.runtimeRef || plan.runtimeGeneration !== context.value?.runtimeGeneration
    || plan.contextVersion !== context.value?.contextVersion || plan.stateVersion !== context.value?.stateVersion) {
    return '运行状态已变化，请重新预览指令'
  }
  if (plan.explicitDeviceCodes.some(code => !props.runtimeHint.deviceCodes.includes(code))) return '目标设备已变化，请重新预览'
  if (plan.action === 'DEVICE_COMMAND') {
    if (!context.value?.capabilities.includes('DEVICE_COMMAND') || context.value.state !== 'RUNNING') return '当前运行不接受单设备控制'
    if (!heartbeatFresh.value) return '设备连接暂时中断，请稍后重试'
    return ''
  }
  if (plan.action === 'SEQUENCE') {
    const first = plan.steps?.[0]?.action
    return first === 'DEVICE_COMMAND' ? context.value?.state === 'RUNNING' ? '' : '任务尚未运行'
      : first && first !== 'WAIT' ? disabledReason(first, true) : '顺序指令不能以等待开始'
  }
  return disabledReason(plan.action, true)
})
const displayError = computed(() => errorLabels[errorCode.value] ?? error.value)
const heartbeatFresh = computed(() => {
  const heartbeat = context.value?.lastHeartbeatReceivedAt
  return !!heartbeat && now.value - Date.parse(heartbeat) <= 5000
})
const presentationWaitExpired = computed(() => execution.value?.state === 'SUCCEEDED'
  && execution.value.presentationStatus === 'PENDING'
  && presentationPendingSince.value !== null
  && now.value - presentationPendingSince.value >= 30_000)
const presentationCanResync = computed(() => execution.value?.state === 'SUCCEEDED'
  && ['START', 'RESUME'].includes(execution.value.action)
  && execution.value.presentationStatus === 'STALE')
const contextSummary = computed(() => context.value
  ? `运行 ${context.value.algorithmRunId} · ${context.value.state} · 帧 ${context.value.latestFrameSequence} · 心跳${heartbeatFresh.value ? '正常' : '失效'}`
  : '未发现可控制的算法实例，请重新生成场景；若提示运行被占用，请联系管理员清理旧运行。')

watch(() => [execution.value?.proposalId, execution.value?.state], ([proposalId, state]) => {
  if (proposalId !== automaticProposalId.value || !state) return
  if (['SUCCEEDED', 'REJECTED', 'FAILED', 'INVALIDATED'].includes(state)) {
    markVoicePipelineFinished()
  }
})
// Text parsing can run without a P0-capable runtime. Bind a candidate to a runtime
// only when the context speaks the frozen command protocol; legacy contexts remain
// visible to disabledReason() and therefore cannot create or execute a proposal.
const parserRuntimeContext = computed(() => activeVoiceContext.value?.protocolVersion === 'algorithm.command.v1'
  ? activeVoiceContext.value
  : null)
const allowedIntelligenceActions = computed<VoiceAction[]>(() => {
  const knownActions = actions.map(item => item.action)
  return parserRuntimeContext.value
    ? knownActions.filter(action => parserRuntimeContext.value?.capabilities.includes(action))
    : knownActions
})
const intelligenceRuntimeContext = computed(() => parserRuntimeContext.value ? {
  runtimeRef: parserRuntimeContext.value.runtimeRef,
  runtimeGeneration: parserRuntimeContext.value.runtimeGeneration,
  contextVersion: parserRuntimeContext.value.contextVersion,
} : null)
const operatorScope = computed(() => `${authStore.user?.username ?? ''}:${authStore.user?.role ?? ''}`)
const autoExecuteSimulationVoice = computed(() => voiceP1PreparationEnabled
  && voiceP1BackendEnabled
  && !asrOnly
  && !voiceP0MockEnabled
  && authStore.user?.role === 'ADMIN'
  && context.value?.runtimeScope === 'MISSION_CENTER'
  && context.value.runtimeKind === 'STANDALONE_ALGORITHM'
  && context.value.executionBackend === 'PYTHON_SIMULATION'
  && context.value.protocolVersion === 'algorithm.command.v1')

function isLocalSimulationResume(action: VoiceAction | null) {
  return action === 'RESUME'
    && context.value?.runtimeKind === 'STANDALONE_ALGORITHM'
    && context.value.executionBackend === 'PYTHON_SIMULATION'
}

function disabledReason(action: VoiceAction, ignoreActiveProposal = false) {
  if (recoveryPending.value || responseUnknown.value) return '请先核对上一次写请求的权威结果'
  if (!context.value) return '没有后端登记的运行实例'
  if (context.value.protocolVersion !== 'algorithm.command.v1') return '旧协议实例不支持 P0 指令'
  if (!context.value.capabilities.includes(action)) return `实例未声明 ${action} 能力`
  if (!stateRules[action].includes(context.value.state)) return `状态 ${context.value.state} 不允许此动作`
  if (!heartbeatFresh.value) return '算法心跳超过 5 秒或尚未建立'
  if (props.runtimeHint.deviceCodes.length === 0) return '尚未生成可冻结的设备集合'
  if (props.runtimeHint.deviceCodes.length > 200) return '设备集合超过 P0 上限 200'
  if (action === 'START' && !context.value.sceneReady) return 'Unity 场景尚未就绪'
  if (action === 'RESUME' && !isLocalSimulationResume(action) && !context.value.sceneReady) return 'Unity 场景尚未就绪'
  if ((!ignoreActiveProposal && activeProposal.value) || commandInFlight.value) return '请先处理当前指令'
  return ''
}

function refinedActionDisabledReason(action: VoiceAction) {
  return disabledReason(action, refinedProposalStale.value || secondsLeft.value === 0)
}

function invalidateRefinedDraft() {
  if (!props.refined) return
  refinedDraftRevision.value++
  refinedPrepareError.value = ''
}

async function recoverRefinedRequest() {
  const revision = refinedDraftRevision.value
  const operator = operatorScope.value
  await store.recover()
  const recovered = proposal.value
  if (revision !== refinedDraftRevision.value || operator !== operatorScope.value
    || !recovered || recovered.status !== 'AWAITING_CONFIRMATION'
    || recovered.plan.runtimeRef !== context.value?.runtimeRef
    || recovered.plan.runtimeGeneration !== context.value?.runtimeGeneration) return
  refinedAcceptedProposalId.value = recovered.proposalId
  refinedAcceptedRevision.value = revision
  refinedRestoredProposalId.value = recovered.proposalId
  // Recovery may return a proposal whose confirmation never reached the server.
  // Show it for explicit recovery; do not silently resume automatic execution.
  refinedAutomaticPlanId.value = ''
}

watch([
  operatorScope,
  () => context.value?.runtimeRef,
  () => context.value?.runtimeGeneration,
  () => context.value?.contextVersion,
  () => context.value?.stateVersion,
  () => props.runtimeHint.algorithmRunId,
  () => props.unitySession.unityInstanceId,
  () => props.unitySession.sceneRevision,
  () => props.runtimeHint.deviceCodes.join('|'),
], invalidateRefinedDraft, { flush: 'sync' })

async function prepareRefinedCandidate(intent: VoiceIntent, interpretationId?: string) {
  if (refinedSubmissionDisabled.value || authStore.user?.role !== 'ADMIN' || authStore.loading) return
  const revision = refinedDraftRevision.value
  const operator = operatorScope.value
  refinedPreparing.value = true
  refinedPrepareError.value = ''
  try {
    // Editing invalidates the UI immediately; cancel the old frozen proposal
    // with its existing guard before requesting a replacement. Never clear
    // recovery journals or manufacture a new key for an unknown write result.
    if (activeProposal.value && secondsLeft.value > 0) {
      if (!refinedProposalStale.value) return
      await store.cancel()
      if (proposal.value?.status !== 'CANCELLED' || responseUnknown.value) return
    }
    if (revision !== refinedDraftRevision.value || operator !== operatorScope.value) return
    const previousId = proposal.value?.proposalId
    await propose(intent, interpretationId)
    const expectedAction = intent === 'SINGLE_DEVICE_CONTROL' ? 'DEVICE_COMMAND'
      : intent === 'COMMAND_SEQUENCE' ? 'SEQUENCE' : intent.replace('MISSION_', '')
    const frozen = proposal.value
    if (revision !== refinedDraftRevision.value || operator !== operatorScope.value) return
    if (!frozen || frozen.proposalId === previousId || frozen.status !== 'AWAITING_CONFIRMATION'
      || frozen.plan.action !== expectedAction
      || frozen.plan.runtimeRef !== context.value?.runtimeRef
      || frozen.plan.runtimeGeneration !== context.value?.runtimeGeneration
      || (interpretationId ? frozen.interpretationId !== interpretationId : !voiceP0MockEnabled)) {
      refinedPrepareError.value = displayError.value || '没有获得匹配的确认计划，请重新预览。'
      return
    }
    refinedAcceptedProposalId.value = frozen.proposalId
    refinedAcceptedRevision.value = revision
    refinedRestoredProposalId.value = ''
  } finally {
    refinedPreparing.value = false
  }
}

async function propose(intent: VoiceIntent, interpretationId?: string, automaticVoice = false) {
  await store.propose(intent, interpretationId)
  const expectedAction = intent === 'SINGLE_DEVICE_CONTROL'
    ? 'DEVICE_COMMAND'
    : intent === 'COMMAND_SEQUENCE' ? 'SEQUENCE' : intent.replace('MISSION_', '')
  const proposalMatchesVoice = Boolean(interpretationId
    && proposal.value?.status === 'AWAITING_CONFIRMATION'
    && proposal.value.interpretationId === interpretationId
    && proposal.value.plan.action === expectedAction
    && proposal.value.plan.runtimeRef === context.value?.runtimeRef
    && proposal.value.plan.runtimeGeneration === context.value?.runtimeGeneration)
  dialogOpen.value = !props.refined && !automaticVoice && proposal.value?.status === 'AWAITING_CONFIRMATION'
  return !automaticVoice || proposalMatchesVoice
}

async function confirm() {
  if (props.refined && (refinedConfirmReason.value || !proposal.value
    || Date.parse(proposal.value.expiresAt) <= Date.now())) return
  if (props.refined) refinedConfirming.value = true
  try {
    await store.confirm()
    if (execution.value) dialogOpen.value = false
  } finally { refinedConfirming.value = false }
}

async function cancel() {
  if (props.refined && (refinedSubmissionDisabled.value || !activeProposal.value)) return
  if (secondsLeft.value === 0) {
    dialogOpen.value = false
    return
  }
  if (props.refined) refinedCancelling.value = true
  try {
    await store.cancel()
    if (proposal.value?.status === 'CANCELLED') dialogOpen.value = false
  } finally { refinedCancelling.value = false }
}

async function handleVoiceCandidate(intent: VoiceIntent, interpretationId?: string) {
  if (refinedAutomaticRunning.value || !panelActive) return
  refinedAutomaticPlanId.value = ''
  voiceAutomationStatus.value = ''
  if (props.refined) await prepareRefinedCandidate(intent, interpretationId)
  else await propose(intent, interpretationId)
}

function automaticDeviceTargetValid(target: string | undefined, command: string | undefined) {
  if (!target || !props.runtimeHint.deviceCodes.includes(target)) return false
  return target.startsWith('UAV-')
    ? ['UAV_HOVER', 'UAV_RESUME', 'UAV_RETURN', 'UAV_LAND'].includes(command ?? '')
    : target.startsWith('USV-') && ['USV_HOLD', 'USV_RESUME', 'USV_RETURN', 'USV_STOP'].includes(command ?? '')
}

function automaticFrozenPlanValid(plan: VoiceFrozenPlan) {
  if (plan.action === 'DEVICE_COMMAND') {
    return automaticDeviceTargetValid(plan.targetDeviceCode, plan.deviceCommandType)
      && plan.explicitDeviceCodes.length === 1
      && plan.explicitDeviceCodes[0] === plan.targetDeviceCode
  }
  // Fleet actions must retain exactly the frozen current fleet. The backend
  // remains authoritative and checks the same plan hash/version at submission.
  const members = props.runtimeHint.deviceCodes
  if (!members.length || plan.explicitDeviceCodes.length !== members.length
    || new Set(plan.explicitDeviceCodes).size !== members.length
    || plan.explicitDeviceCodes.some(code => !members.includes(code))) return false
  if (plan.action !== 'SEQUENCE') return ['START', 'PAUSE', 'RESUME', 'STOP'].includes(plan.action)
  const steps = plan.steps
  if (!steps || !sequenceStateValid(steps, context.value?.state ?? '')) return false
  return steps.every((step, index) => step.index === index && (step.action === 'DEVICE_COMMAND'
    ? !!context.value?.capabilities.includes('DEVICE_COMMAND') && automaticDeviceTargetValid(step.targetDeviceCode, step.deviceCommandType)
    : step.action === 'WAIT' ? index > 0 && Number.isInteger(step.waitSeconds) && step.waitSeconds! >= 1 && step.waitSeconds! <= 60
      : ['START', 'PAUSE', 'RESUME', 'STOP'].includes(step.action) && !step.targetDeviceCode && !step.deviceCommandType))
}

async function handleRefinedAutomaticVoiceCandidate(
  intent: VoiceIntent,
  interpretationId: string,
  sequenceFirstAction = 'START',
) {
  // Only a newly recognised utterance enters this path. Polling/recovery and
  // manually edited text never auto-confirm a stored proposal.
  if (!panelActive || !autoExecuteSimulationVoice.value || !interpretationId
    || refinedAutomaticRunning.value || refinedSubmissionDisabled.value) return
  const runtime = context.value!
  const key = `${operatorScope.value}:${runtime.runtimeRef}:${runtime.runtimeGeneration}:${interpretationId}`
  if (automaticInterpretations.has(key)) return
  automaticInterpretations.add(key)
  if (automaticInterpretations.size > 128) automaticInterpretations.delete(automaticInterpretations.values().next().value!)

  const revision = refinedDraftRevision.value
  const operator = operatorScope.value
  const stillCurrent = () => panelActive && autoExecuteSimulationVoice.value
    && revision === refinedDraftRevision.value && operator === operatorScope.value
    && context.value?.runtimeRef === runtime.runtimeRef
    && context.value?.runtimeGeneration === runtime.runtimeGeneration
    && context.value?.contextVersion === runtime.contextVersion
    && context.value?.stateVersion === runtime.stateVersion
  const freshHeartbeat = () => {
    const received = Date.parse(context.value?.lastHeartbeatReceivedAt ?? '')
    return Number.isFinite(received) && Date.now() - received <= 5000
  }
  const action = intent === 'COMMAND_SEQUENCE' ? sequenceFirstAction === 'DEVICE_COMMAND' ? null : sequenceFirstAction as VoiceAction
    : intent === 'SINGLE_DEVICE_CONTROL' ? null : intent.replace('MISSION_', '') as VoiceAction
  const unavailable = action ? refinedActionDisabledReason(action)
    : runtime.state !== 'RUNNING' || !runtime.capabilities.includes('DEVICE_COMMAND')
      ? '当前运行不接受单设备控制' : ''
  // Match the backend/manual RESUME policy: display evidence has a short TTL,
  // but a live paused local runner can resume while Unity catches up. Recheck
  // the current context after proposal creation, not a captured readiness flag.
  const sceneAllowsAction = () => !!context.value
    && (context.value.sceneReady || isLocalSimulationResume(action))
  if (!sceneAllowsAction() || !freshHeartbeat() || unavailable) {
    voiceAutomationStatus.value = `未自动执行：${!sceneAllowsAction() ? '仿真场景尚未就绪' : unavailable || '设备连接暂时中断'}。请检查后重新录音。`
    return
  }

  refinedAutomaticRunning.value = true
  voiceAutomationStatus.value = '语音已识别，正在校验设备与动作并自动执行…'
  try {
    const previousProposalId = proposal.value?.proposalId
    await prepareRefinedCandidate(intent, interpretationId)
    const frozen = proposal.value
    if (frozen?.interpretationId === interpretationId) refinedAutomaticPlanId.value = frozen.proposalId
    if (!stillCurrent() || !sceneAllowsAction() || !freshHeartbeat() || refinedConfirmReason.value
      || !frozen || frozen.proposalId === previousProposalId || frozen.interpretationId !== interpretationId
      || !Number.isFinite(Date.parse(frozen.expiresAt)) || Date.parse(frozen.expiresAt) <= Date.now()
      || !automaticFrozenPlanValid(frozen.plan)) {
      voiceAutomationStatus.value = responseUnknown.value
        ? '指令结果待核对，已停止自动执行；请核对上次请求，不要重复下发。'
        : `未自动执行：${refinedPrepareError.value || refinedConfirmReason.value || '目标、动作或运行状态校验未通过'}。`
      return
    }
    automaticProposalId.value = frozen.proposalId
    await confirm()
    const currentExecution = execution.value
    const matched = currentExecution?.proposalId === frozen.proposalId
      && currentExecution.runtimeRef === frozen.plan.runtimeRef
      && currentExecution.runtimeGeneration === frozen.plan.runtimeGeneration
      && currentExecution.action === frozen.plan.action
    voiceAutomationStatus.value = matched
      ? '语音指令已自动下发，执行结果见下方。'
      : responseUnknown.value
        ? '执行结果待核对，已停止自动重试；请核对上次请求。'
        : `自动执行未提交：${displayError.value || '请检查当前运行状态后重新录音'}。`
  } catch {
    // An unknown write is never retried automatically with a new key.
    voiceAutomationStatus.value = '自动执行未完成，请核对上次请求结果，不要重复下发。'
  } finally {
    refinedAutomaticRunning.value = false
  }
}

function markVoicePipelineFinished() {
  const finishedAt = Date.now()
  voiceActionFinishedAt.value ??= finishedAt
  if (voicePipelineMetrics.value && voiceActionStartedAt.value !== null) {
    voicePipelineMetrics.value.totalMs = finishedAt - voiceActionStartedAt.value
    if (voiceSubmitStartedAt !== null) voicePipelineMetrics.value.submitAndAckMs = finishedAt - voiceSubmitStartedAt
  }
}

async function handleAutomaticVoiceCandidate(
  intent: VoiceIntent,
  interpretationId: string,
  timing: { startedAt: number; asrRequestMs: number; parseMs: number; sequenceFirstAction?: string },
) {
  if (props.refined) {
    await handleRefinedAutomaticVoiceCandidate(intent, interpretationId, timing.sequenceFirstAction)
    return
  }
  automaticProposalId.value = null
  voiceActionStartedAt.value = timing.startedAt
  voiceActionFinishedAt.value = null
  voiceActionIncludesRecognition.value = true
  voiceSubmitStartedAt = null
  voicePipelineMetrics.value = {
    asrRequestMs: timing.asrRequestMs,
    parseMs: timing.parseMs,
    proposalMs: null,
    submitAndAckMs: null,
    totalMs: null,
  }
  if (intent === 'SINGLE_DEVICE_CONTROL') {
    const runtime = context.value
    if (!runtime
      || runtime.runtimeKind !== 'STANDALONE_ALGORITHM'
      || runtime.executionBackend !== 'PYTHON_SIMULATION'
      || !runtime.capabilities.includes('DEVICE_COMMAND')
      || !runtime.runtimeRef
      || !runtime.runtimeGeneration) {
      voiceAutomationStatus.value = '已识别单设备指令，但当前运行实例未就绪或不支持单设备控制；没有下发命令。'
      markVoicePipelineFinished()
      return
    }
    voiceAutomationStatus.value = '已识别单设备指令，正在校验目标并自动提交至本地仿真。'
    const proposalStartedAt = performance.now()
    await propose(intent, interpretationId, true)
    if (voicePipelineMetrics.value) voicePipelineMetrics.value.proposalMs = Math.round(performance.now() - proposalStartedAt)
    const targetedProposal = proposal.value
    const target = targetedProposal?.plan.targetDeviceCode
    const command = targetedProposal?.plan.deviceCommandType
    const targetMatchesRuntime = Boolean(target
      && props.runtimeHint.deviceCodes.some(code => code.toUpperCase() === target.toUpperCase()))
    const commandMatchesTarget = Boolean(command && target
      && ((command.startsWith('UAV_') && target.toUpperCase().startsWith('UAV-'))
        || (command.startsWith('USV_') && target.toUpperCase().startsWith('USV-'))))
    const proposalMatchesVoice = Boolean(targetedProposal
      && targetedProposal.status === 'AWAITING_CONFIRMATION'
      && targetedProposal.interpretationId === interpretationId
      && targetedProposal.plan.action === 'DEVICE_COMMAND'
      && targetedProposal.plan.runtimeRef === runtime.runtimeRef
      && targetedProposal.plan.runtimeGeneration === runtime.runtimeGeneration
      && targetMatchesRuntime
      && commandMatchesTarget)
    if (!proposalMatchesVoice) {
      voiceAutomationStatus.value = store.responseUnknown
        ? '单设备提案结果待核对；没有自动下发命令，请先核对上次请求。'
        : `单设备确认提案未能通过目标校验${displayError.value ? `：${displayError.value}` : '；没有下发命令。'}`
      markVoicePipelineFinished()
      return
    }
    automaticProposalId.value = targetedProposal!.proposalId
    voiceSubmitStartedAt = Date.now()
    voiceAutomationStatus.value = `目标已校验，正在自动提交：${target} · ${actionLabels[command!] ?? command}。`
    await confirm()
    const executionMatchesProposal = execution.value?.proposalId === targetedProposal!.proposalId
      && execution.value.runtimeRef === targetedProposal!.plan.runtimeRef
      && execution.value.runtimeGeneration === targetedProposal!.plan.runtimeGeneration
      && execution.value.action === 'DEVICE_COMMAND'
    voiceAutomationStatus.value = executionMatchesProposal
      ? `单设备命令已自动提交：${target} · ${actionLabels[command!] ?? command}，正在等待算法回执。`
      : store.responseUnknown
        ? '单设备执行结果待核对，已停止自动重试；请检查上次请求。'
        : `单设备命令未能完成提交${displayError.value ? `：${displayError.value}` : '，请检查运行状态后重试。'}`
    if (!executionMatchesProposal) markVoicePipelineFinished()
    else if (execution.value && ['SUCCEEDED', 'REJECTED', 'FAILED', 'INVALIDATED'].includes(execution.value.state)) markVoicePipelineFinished()
    return
  }
  const action = intent.replace('MISSION_', '') as VoiceAction
  if (!autoExecuteSimulationVoice.value || disabledReason(action)) {
    voiceAutomationStatus.value = '已识别语音，但当前仿真或运行条件不允许自动执行；识别文字已保留，请检查后重试。'
    markVoicePipelineFinished()
    return
  }
  voiceAutomationStatus.value = '语音已识别，正在创建并自动提交当前仿真提案。'
  const proposalStartedAt = performance.now()
  const proposalReady = await propose(intent, interpretationId, true)
  if (voicePipelineMetrics.value) voicePipelineMetrics.value.proposalMs = Math.round(performance.now() - proposalStartedAt)
  if (!proposalReady) {
    voiceAutomationStatus.value = store.responseUnknown
      ? '提案结果待核对，已停止自动重试；请先核对上次请求。'
      : `自动执行未提交${displayError.value ? `：${displayError.value}` : '，请检查识别文字后重试。'}`
    markVoicePipelineFinished()
    return
  }
  const submittedProposal = proposal.value
  if (!submittedProposal || submittedProposal.status !== 'AWAITING_CONFIRMATION') {
    voiceAutomationStatus.value = '提案状态无法核实，已停止自动提交；请检查识别文字后重试。'
    markVoicePipelineFinished()
    return
  }
  automaticProposalId.value = submittedProposal.proposalId
  voiceSubmitStartedAt = Date.now()
  await confirm()
  const executionMatchesProposal = execution.value?.proposalId === submittedProposal.proposalId
    && execution.value.runtimeRef === submittedProposal.plan.runtimeRef
    && execution.value.runtimeGeneration === submittedProposal.plan.runtimeGeneration
    && execution.value.action === submittedProposal.plan.action
  voiceAutomationStatus.value = executionMatchesProposal
    ? '仿真动作已自动提交，正在等待算法回执。'
    : store.responseUnknown
      ? '执行确认结果待核对，已停止自动重试；请先核对上次请求。'
      : `自动执行未完成${displayError.value ? `：${displayError.value}` : '，请检查识别文字后重试。'}`
  if (!executionMatchesProposal) markVoicePipelineFinished()
  else if (execution.value && ['SUCCEEDED', 'REJECTED', 'FAILED', 'INVALIDATED'].includes(execution.value.state)) markVoicePipelineFinished()
}

function presentationIdentityMatches(message: UnityPresentationIncoming) {
  return !!context.value
    && !!presentationBinding.value?.bindingId
    && message.protocolVersion === 'unity.presentation.v1'
    && message.runtimeRef === context.value.runtimeRef
    && message.runtimeGeneration === context.value.runtimeGeneration
    && message.bindingId === presentationBinding.value.bindingId
    && message.unityInstanceId === props.unitySession.unityInstanceId
    && message.sceneRevision === props.unitySession.sceneRevision
}

function emitHello() {
  if (runtimeEnded.value || !presentationBridgeEnabled || !context.value || !presentationBinding.value?.bindingId
    || !props.unitySession.connected || props.unitySession.sceneRevision < 1) return
  emit('presentationMessage', {
    type: 'PRESENTATION_HELLO', protocolVersion: 'unity.presentation.v1',
    runtimeRef: context.value.runtimeRef, runtimeGeneration: context.value.runtimeGeneration,
    bindingId: presentationBinding.value.bindingId,
    unityInstanceId: props.unitySession.unityInstanceId,
    sceneRevision: props.unitySession.sceneRevision,
  })
  helloAttempts.value += 1
  presentationBridgeStatus.value = `等待 Unity READY（${helloAttempts.value}/30）`
}

async function requestPresentationProbe(forceFrameResync = false) {
  if (!panelActive || runtimeEnded.value || !presentationBridgeEnabled || !presentationBridgeReady.value || presentationRequestInFlight
    || !props.unitySession.connected || props.unitySession.sceneRevision < 1
    || presentationChallenge.value || !context.value || !presentationBinding.value?.bindingId) return
  const identity = {
    runtimeRef: context.value.runtimeRef,
    runtimeGeneration: context.value.runtimeGeneration,
    bindingId: presentationBinding.value.bindingId,
    unityInstanceId: props.unitySession.unityInstanceId,
    sceneRevision: props.unitySession.sceneRevision,
  }
  const frameRequired = execution.value?.state === 'SUCCEEDED'
    && ['START', 'RESUME'].includes(execution.value.action)
    && (execution.value.presentationStatus === 'PENDING'
      || (forceFrameResync && execution.value.presentationStatus === 'STALE'))
  if (!frameRequired && lastSceneProbeAt.value !== null
    && Date.now() - lastSceneProbeAt.value < 3000) return
  presentationRequestInFlight = true
  try {
    const kind = frameRequired ? 'FRAME_APPLIED' : 'SCENE_READY'
    const challenge = await store.requestPresentationChallenge(kind, frameRequired ? execution.value!.executionId : null)
    if (!challenge) return
    if (!panelActive || runtimeEnded.value || !presentationBridgeReady.value || !props.unitySession.connected
      || context.value?.runtimeRef !== identity.runtimeRef
      || context.value?.runtimeGeneration !== identity.runtimeGeneration
      || presentationBinding.value?.bindingId !== identity.bindingId
      || presentationBinding.value?.runtimeGeneration !== identity.runtimeGeneration
      || props.unitySession.unityInstanceId !== identity.unityInstanceId
      || props.unitySession.sceneRevision !== identity.sceneRevision
      || challenge.runtimeGeneration !== identity.runtimeGeneration || challenge.bindingId !== identity.bindingId) {
      // Discard only this stale response, never a newer session's challenge.
      if (store.presentationChallenge?.requestId === challenge.requestId) store.presentationChallenge = null
      return
    }
    emit('presentationMessage', {
      type: 'PRESENTATION_PROBE', protocolVersion: 'unity.presentation.v1',
      ...identity,
      requestId: challenge.requestId, sequence: challenge.sequence,
      kind: challenge.kind, executionId: challenge.executionId,
    })
    presentationBridgeStatus.value = `等待 Unity ${kind} 回执`
    if (!frameRequired) lastSceneProbeAt.value = Date.now()
  } finally { presentationRequestInFlight = false }
}

async function resyncPresentation() {
  pendingFrameResync = true
  if (presentationRequestInFlight) return
  if (!presentationBridgeEnabled) {
    presentationBridgeStatus.value = '展示桥未启用，无法重新同步画面'
    return
  }
  if (!presentationBinding.value?.bindingId) {
    await takePresentationBinding()
  }
  if (!presentationBridgeReady.value) {
    emitHello()
    presentationBridgeStatus.value = '正在重新连接 Unity，请就绪后再次同步'
    return
  }
  // A stale or expired challenge cannot be reused. Explicit recovery always
  // asks the backend for a fresh one-time FRAME_APPLIED challenge.
  if (!store.clearStalePresentationRecovery()) {
    pendingFrameResync = false
    return
  }
  await requestPresentationProbe(true)
  pendingFrameResync = presentationChallenge.value?.kind === 'FRAME_APPLIED'
}

async function handleUnityPresentationMessage(message: UnityWindowMessage) {
  if (!presentationBridgeEnabled || runtimeEnded.value) return
  const unwrapped = unwrapUnityPresentationMessage(message)
  if (!unwrapped) return
  const incoming = unwrapped as unknown as UnityPresentationIncoming
  if (!presentationIdentityMatches(incoming)) return
  if (incoming.type === 'PRESENTATION_READY' || incoming.type === 'PRESENTATION_HEARTBEAT') {
    presentationBridgeReady.value = true
    presentationBridgeStatus.value = incoming.scenarioReady ? 'Unity 展示会话已就绪' : 'Unity 会话在线，场景未就绪'
    return
  }
  if (incoming.type !== 'PRESENTATION_REPORT') return
  const report = incoming
  const challenge = presentationChallenge.value
  if (!challenge || report.requestId !== challenge.requestId
    || report.sequence !== challenge.sequence || report.kind !== challenge.kind
    || report.executionId !== challenge.executionId || Date.parse(challenge.expiresAt) <= Date.now()) return
  const accepted = await store.submitPresentationReport({
    runtimeGeneration: report.runtimeGeneration,
    bindingId: report.bindingId,
    kind: report.kind,
    executionId: report.executionId,
    requestId: report.requestId,
    sequence: report.sequence,
    frameSequence: report.frameSequence,
    applied: report.applied,
  })
  presentationBridgeStatus.value = accepted
    ? (report.applied ? '展示证据已提交' : 'Unity 报告未应用')
    : '展示报告提交失败'
  if (accepted && report.kind === 'FRAME_APPLIED') pendingFrameResync = false
}

async function takePresentationBinding() {
  lastAutoBindingKey.value = ''
  await establishPresentationBinding('')
}

async function establishPresentationBinding(autoBindingKey: string) {
  if (presentationBindingInFlight) return
  if (autoBindingKey && autoBindingKey === lastAutoBindingKey.value && presentationBinding.value?.bindingId) {
    emitHello()
    return
  }
  presentationBindingInFlight = true
  if (autoBindingKey) lastAutoBindingKey.value = autoBindingKey
  try {
    await store.takePresentationBinding()
    presentationBridgeReady.value = false
    helloAttempts.value = 0
    if (presentationBinding.value?.bindingId) emitHello()
    else if (autoBindingKey === lastAutoBindingKey.value) lastAutoBindingKey.value = ''
  } finally {
    presentationBindingInFlight = false
  }
}

function setMockOutcome(event: Event) {
  chosenMockOutcome.value = (event.target as HTMLSelectElement).value as VoiceMockOutcome
  setVoiceP0MockOutcome(chosenMockOutcome.value)
}

watch(() => props.runtimeHint, (hint) => {
  configureVoiceP0MockRuntime(hint)
}, { deep: true })

watch(() => props.runtimeHint.algorithmRunId, algorithmRunId => {
  void store.selectAlgorithmRun(algorithmRunId)
}, { immediate: true })

watch([
  () => context.value?.runtimeRef,
  () => context.value?.runtimeGeneration,
], ([runtimeRef, runtimeGeneration], [previousRuntimeRef, previousRuntimeGeneration]) => {
  if (!previousRuntimeRef || (runtimeRef === previousRuntimeRef && runtimeGeneration === previousRuntimeGeneration)) return
  automaticProposalId.value = null
  voiceAutomationStatus.value = ''
  voiceActionStartedAt.value = null
  voiceActionFinishedAt.value = null
  voiceActionIncludesRecognition.value = false
  voicePipelineMetrics.value = null
  voiceSubmitStartedAt = null
})

watch([
  () => context.value?.runtimeRef,
  () => context.value?.runtimeGeneration,
  () => props.unitySession.connected,
  () => props.unitySession.unityInstanceId,
  () => props.unitySession.sceneRevision,
], async () => {
  pendingFrameResync = false
  presentationBridgeReady.value = false
  helloAttempts.value = 0
  store.presentationChallenge = null
  if (!runtimeEnded.value && presentationBridgeEnabled && context.value && props.unitySession.connected && props.unitySession.sceneRevision > 0) {
    const autoBindingKey = [
      context.value.runtimeRef,
      context.value.runtimeGeneration,
      props.unitySession.unityInstanceId,
      props.unitySession.sceneRevision,
    ].join(':')
    await establishPresentationBinding(autoBindingKey)
  }
})

watch(() => [execution.value?.state, execution.value?.presentationStatus], ([state, presentation]) => {
  if (state === 'SUCCEEDED' && presentation === 'PENDING') presentationPendingSince.value ??= Date.now()
  else presentationPendingSince.value = null
}, { immediate: true })

watch(() => execution.value?.executionId, executionId => {
  pendingFrameResync = false
  autoResyncExecutionId = executionId ?? ''
  autoResyncAttempts = 0
  lastAutoResyncAt = null
})

watch(() => execution.value?.presentationStatus, status => {
  if (status !== 'REPORTED_APPLIED') return
  pendingFrameResync = false
  autoResyncAttempts = 0
  lastAutoResyncAt = null
  presentationBridgeStatus.value = '展示证据已提交'
})

watch(runtimeEnded, ended => {
  if (!ended) return
  store.presentationChallenge = null
  presentationBridgeReady.value = false
  presentationBridgeStatus.value = '运行已结束，已停止展示探测'
})

function onVisibilityChange() {
  if (document.visibilityState === 'visible') {
    void store.refreshContexts()
    void store.poll()
  }
}

onMounted(async () => {
  configureVoiceP0MockRuntime(props.runtimeHint)
  await store.selectAlgorithmRun(props.runtimeHint.algorithmRunId)
  if (props.refined) await recoverRefinedRequest()
  else await store.recover()
  timer = window.setInterval(() => {
    pollTick += 1
    if (document.visibilityState !== 'visible') return
    const currentTime = Date.now()
    const oneSecondTick = pollTick % 4 === 0
    if (oneSecondTick) now.value = currentTime
    if (pollTick % 12 === 0) void store.refreshContexts()
    const timedOutSeconds = execution.value?.timedOutAt
      ? Math.floor((currentTime - Date.parse(execution.value.timedOutAt)) / 1000)
      : 0
    const state = execution.value?.state
    const awaitingCommandReceipt = ['QUEUED', 'DISPATCHED', 'ACCEPTED', 'EXECUTING'].includes(state ?? '')
    const executionDue = awaitingCommandReceipt
      || (state === 'TIMED_OUT'
        ? pollTick % (timedOutSeconds < 30 ? 8 : 40) === 0
        : oneSecondTick)
    if (executionDue) void store.poll()
    if (oneSecondTick && presentationBridgeEnabled && !runtimeEnded.value) {
      if (!presentationBridgeReady.value && helloAttempts.value < 30) emitHello()
      else if (!presentationBridgeReady.value && helloAttempts.value >= 30) presentationBridgeStatus.value = 'Unity 展示握手超时，请重新接管'
      else {
        if (presentationChallenge.value && Date.parse(presentationChallenge.value.expiresAt) <= Date.now()) {
          store.presentationChallenge = null
        }
        if (execution.value?.state === 'SUCCEEDED' && execution.value.presentationStatus === 'PENDING') {
          pendingFrameResync = false
          void requestPresentationProbe()
        } else if (pendingFrameResync) void resyncPresentation()
        else if (presentationCanResync.value && execution.value) {
          if (autoResyncExecutionId !== execution.value.executionId) {
            autoResyncExecutionId = execution.value.executionId
            autoResyncAttempts = 0
            lastAutoResyncAt = null
          }
          if (autoResyncAttempts < 3
            && (lastAutoResyncAt === null || Date.now() - lastAutoResyncAt >= 5000)) {
            autoResyncAttempts += 1
            lastAutoResyncAt = Date.now()
            presentationBridgeStatus.value = `Unity 展示过期，正在自动恢复（${autoResyncAttempts}/3）`
            void resyncPresentation()
          }
        } else void requestPresentationProbe()
      }
    }
  }, 250)
  document.addEventListener('visibilitychange', onVisibilityChange)
})

defineExpose({ handleUnityPresentationMessage })
onActivated(() => { panelActive = true })
onDeactivated(() => { panelActive = false; invalidateRefinedDraft() })
onBeforeUnmount(() => {
  panelActive = false
  invalidateRefinedDraft()
  window.clearInterval(timer)
  document.removeEventListener('visibilitychange', onVisibilityChange)
})
</script>

<template>
  <section v-if="refined" class="voice-p0 voice-refined" aria-label="语音控制">
    <header class="refined-head"><h2>语音控制</h2><AudioLines :size="20" aria-hidden="true" /></header>
    <div class="refined-content">
      <p v-if="voiceP0MockEnabled" class="refined-notice">当前为本地演示模式，不连接真实设备。</p>
      <p v-if="authStore.user?.role !== 'ADMIN'" class="refined-notice" role="alert">当前账号没有控制权限，请使用管理员账号。</p>
      <p v-else-if="!context" class="refined-notice" role="status">请先生成场景，连接可控制的无人机与无人艇。</p>
      <p v-else-if="runtimeEnded" class="refined-notice" role="status">本轮运行已结束，当前画面已保留。重新生成场景后可继续控制。</p>
      <p v-else-if="!heartbeatFresh" class="refined-notice" role="status">设备连接暂时中断，恢复后可继续控制。</p>
      <p v-if="recoveryPending" class="refined-notice" role="status">正在核对上一次请求，请稍候。</p>
      <p v-else-if="responseUnknown" class="refined-notice" role="alert">上次请求结果尚未确认。请核对结果，不要重复下发指令。</p>
      <p v-else-if="!recoveryAvailable" class="refined-notice" role="alert">无法保存指令恢复信息，已暂停下发。请检查浏览器存储后重试。</p>

      <LocalAsrInput v-if="asrOnly" :operator-scope="operatorScope" :disabled="authStore.user?.role !== 'ADMIN' || authStore.loading" />
      <VoiceIntelligenceInput
        v-else-if="voiceP1PreparationEnabled"
        refined
        :proposal-ready="refinedProposalReady"
        :allowed-actions="allowedIntelligenceActions"
        :device-codes="runtimeHint.deviceCodes"
        :runtime-context="intelligenceRuntimeContext"
        :operator-scope="operatorScope"
        :input-disabled="authStore.user?.role !== 'ADMIN' || authStore.loading"
        :auto-execute-speech="autoExecuteSimulationVoice"
        :allow-mock-submission="voiceP0MockEnabled"
        :submission-disabled="refinedSubmissionDisabled || refinedAutomaticRunning"
        :action-disabled-reason="refinedActionDisabledReason"
        @draft-change="invalidateRefinedDraft"
        @candidate="handleVoiceCandidate"
        @voice-candidate="handleAutomaticVoiceCandidate"
        @interpretation="recordInterpretation"
      >
        <section v-if="activeProposal && proposal && !refinedAutomaticRunning && proposal.proposalId !== refinedAutomaticPlanId" class="refined-plan" aria-label="指令预览">
          <div class="refined-section-title"><h3>{{ proposal.proposalId === refinedRestoredProposalId ? '恢复的待确认指令' : '指令预览' }}</h3><span>{{ proposal.plan.action === 'SEQUENCE' ? '受控顺序指令' : '单步指令' }}</span></div>
          <ol>
            <li v-for="step in refinedPlanSteps" :key="step.index"><b>{{ step.index + 1 }}</b><span>{{ step.label }}</span></li>
          </ol>
          <p v-if="refinedProposalStale" class="refined-notice">文字或场景已变化，请重新预览。旧指令不会执行。</p>
          <p v-else-if="secondsLeft === 0" class="refined-notice">指令已过期，请重新预览。</p>
          <p v-else class="refined-plan-note">{{ proposal.plan.action === 'SEQUENCE' ? '前一步成功，才继续下一步。' : '请核对目标设备与动作。' }}<span>{{ secondsLeft }} 秒内有效</span></p>
          <button v-if="refinedProposalReady" class="refined-confirm" type="button" :disabled="!!refinedConfirmReason" :title="refinedConfirmReason" @click="confirm"><Check :size="16" />确认执行</button>
          <p v-if="refinedProposalReady && refinedConfirmReason" class="refined-notice" role="status">{{ refinedConfirmReason }}</p>
          <button v-if="secondsLeft > 0" class="refined-cancel" type="button" :disabled="refinedSubmissionDisabled" @click="cancel">取消此指令</button>
        </section>
        <p v-if="refinedPreparing && !refinedAutomaticRunning" class="refined-notice" role="status">正在核对设备与动作，生成确认计划…</p>
        <p v-if="voiceAutomationStatus" class="refined-notice" role="status">{{ voiceAutomationStatus }}</p>
      </VoiceIntelligenceInput>
      <p v-else class="refined-notice">语音控制尚未启用，请检查服务配置。</p>

      <article v-if="execution" class="refined-result" :class="execution.outcome.toLowerCase()" role="status" aria-live="polite">
        <div class="refined-result-head"><Check v-if="execution.state === 'SUCCEEDED'" :size="16" /><strong>{{ deviceProgress ? deviceProgress.complete ? '设备动作已完成' : '指令已接受 · 设备执行中' : executionLabels[execution.state] ?? execution.state }}</strong></div>
        <p v-if="deviceProgress" class="device-action-progress">{{ deviceProgress.label }}</p>
        <ol v-if="execution.action === 'SEQUENCE' && execution.steps" class="refined-execution-steps">
          <li v-for="step in execution.steps" :key="step.index"><span>{{ voiceStepLabel(step) }}</span><small>{{ step.state === 'PENDING' ? '等待前一步' : executionLabels[step.state] ?? step.state }}</small></li>
        </ol>
        <p v-else>{{ execution.action === 'DEVICE_COMMAND' ? `${proposal?.plan.targetDeviceCode ?? ''} · ${actionLabels[proposal?.plan.deviceCommandType ?? ''] ?? '单设备控制'}` : actionLabels[execution.action] }}</p>
        <p v-if="execution.errorCode" class="refined-notice">{{ errorLabels[execution.errorCode] ?? execution.errorCode }}</p>
        <small v-if="execution.presentationStatus !== 'NOT_REQUIRED'">{{ presentationLabels[execution.presentationStatus] }}</small>
        <p v-if="presentationWaitExpired" class="refined-notice">算法已执行，但画面尚未确认同步；请勿重复下发动作。</p>
        <button v-if="presentationCanResync" class="refined-recovery" type="button" :disabled="loading || presentationRequestInFlight" @click="resyncPresentation"><RefreshCw :size="13" />重新同步画面</button>
      </article>
      <p v-if="refinedPrepareError || displayError" class="refined-notice refined-error" role="alert">{{ refinedPrepareError || displayError }}</p>
      <div v-if="responseUnknown || (!runtimeEnded && (!context || !heartbeatFresh || (!presentationBridgeReady && helloAttempts >= 30)))" class="refined-recovery-actions">
        <button v-if="responseUnknown" class="refined-recovery" type="button" :disabled="recoveryPending || loading" @click="recoverRefinedRequest">核对上次请求</button>
        <button v-else-if="!runtimeEnded && (!context || !heartbeatFresh)" class="refined-recovery" type="button" :disabled="loading" @click="store.refreshContexts()"><RefreshCw :size="13" />重新连接</button>
        <button v-if="!runtimeEnded && context && !presentationBridgeReady && helloAttempts >= 30" class="refined-recovery" type="button" :disabled="loading || responseUnknown" @click="takePresentationBinding">恢复画面连接</button>
      </div>
    </div>
    <p class="refined-footer">{{ autoExecuteSimulationVoice ? '语音自动执行 · 2–4 步依次完成 · 文字编辑后需确认' : '先核对设备编号与动作，再确认执行' }}</p>
  </section>
  <section v-else class="voice-p0">
    <header class="voice-head">
      <div>
        <span class="voice-icon"><AudioLines :size="17" /></span>
        <span class="voice-title"><strong>语音任务控制</strong><small>VOICE / LLM · P0</small></span>
      </div>
      <span :class="voiceP0MockEnabled ? 'mock' : 'real'">{{ voiceP0MockEnabled ? '本地 MOCK' : '真实 API' }}</span>
    </header>

    <article class="runtime-card">
      <p class="context-line" :title="contextSummary">
        <i :class="{ online: !!context && heartbeatFresh }"></i>{{ contextSummary }}
      </p>
      <dl>
        <div><dt>任务状态</dt><dd>{{ context?.state ?? '未连接' }}</dd></div>
        <div><dt>场景状态</dt><dd>{{ context?.sceneReady ? 'READY' : 'WAITING' }}</dd></div>
        <div><dt>接入设备</dt><dd>{{ runtimeHint.deviceCodes.length }} 台</dd></div>
      </dl>
    </article>
    <p class="scope-note">
      {{ asrOnly ? '当前仅将语音转成文字（本地或阿里云识别）；手工任务控制独立使用，识别结果不执行动作。' : voiceP1PreparationEnabled
        ? autoExecuteSimulationVoice
        ? '本地仿真会自动提交通过校验的整队或单设备语音命令；Gateway 实机链路不走此自动流程。'
          : '已启用语音文本输入；本地仿真中的明确单设备命令会自动提交。'
        : '当前验证整队任务控制链路；麦克风、模型解析和单设备控制将在后续阶段接入。' }}
    </p>
    <p v-if="voiceAutomationDisplay" class="scope-note" role="status">{{ voiceAutomationDisplay }}</p>
    <p v-if="recoveryPending" class="recovery-note">正在使用原请求内容和原幂等键核对上次未确认的响应……</p>
    <p v-else-if="responseUnknown" class="recovery-note">上次写请求结果未知，不能换新幂等键重发。</p>
    <p v-else-if="!recoveryAvailable" class="error">本地恢复日志不可用，写操作已阻止。</p>
    <p v-if="presentationBinding?.bindingId" class="scope-note" :title="presentationBinding.bindingId">展示绑定已建立。</p>
    <p v-if="presentationBridgeEnabled" class="scope-note">展示桥：{{ presentationBridgeDisplay }}</p>

    <LocalAsrInput v-if="asrOnly" :operator-scope="operatorScope" :disabled="authStore.user?.role !== 'ADMIN' || authStore.loading" />
    <VoiceIntelligenceInput
      v-else-if="voiceP1PreparationEnabled"
      :allowed-actions="allowedIntelligenceActions"
      :device-codes="runtimeHint.deviceCodes"
      :runtime-context="intelligenceRuntimeContext"
      :operator-scope="operatorScope"
      :input-disabled="authStore.user?.role !== 'ADMIN'"
      :auto-execute-speech="autoExecuteSimulationVoice"
      :allow-mock-submission="voiceP0MockEnabled"
      :submission-disabled="loading || recoveryPending || responseUnknown || !activeVoiceContext"
      :action-disabled-reason="disabledReason"
      @candidate="handleVoiceCandidate"
      @voice-candidate="handleAutomaticVoiceCandidate"
      @interpretation="recordInterpretation"
    />

    <div class="action-grid">
      <button
        v-for="item in actions"
        :key="item.action"
        type="button"
        :disabled="!!disabledReason(item.action) || loading"
        :title="disabledReason(item.action) || `创建${item.label}提案`"
        @click="propose(item.intent)"
      >
        <strong>{{ item.label }}任务</strong>
        <small>{{ disabledReason(item.action) || '创建并核对指令提案' }}</small>
      </button>
    </div>

    <label v-if="voiceP0MockEnabled" class="mock-scenario">
      演示结果
      <select :value="chosenMockOutcome" @change="setMockOutcome">
        <option value="SUCCESS">执行成功</option>
        <option value="REJECTED">算法拒绝</option>
        <option value="FAILED">执行失败</option>
        <option value="TIMEOUT_LATE_SUCCESS">超时后迟到成功</option>
      </select>
    </label>

    <article v-if="execution" class="result" :class="execution.outcome.toLowerCase()">
      <div><strong>{{ executionLabels[execution.state] ?? execution.state }}</strong><span>{{ execution.action === 'DEVICE_COMMAND' ? `${proposal?.plan.targetDeviceCode ?? ''} · ${actionLabels[proposal?.plan.deviceCommandType ?? ''] ?? '单设备控制'}` : actionLabels[execution.action] }}</span></div>
      <ol v-if="execution.action === 'SEQUENCE' && execution.steps" class="sequence-steps">
        <li v-for="step in execution.steps" :key="step.index" :class="step.state.toLowerCase()">
          <span>{{ step.action === 'START' ? '开始任务' : `${step.targetDeviceCode} · ${actionLabels[step.deviceCommandType ?? '']}` }}</span>
          <strong>{{ executionLabels[step.state] ?? step.state }}</strong>
        </li>
      </ol>
      <p v-if="execution.errorCode">{{ errorLabels[execution.errorCode] ?? execution.errorCode }}</p>
      <small>算法结果：{{ execution.outcome }} · 展示状态：{{ presentationLabels[execution.presentationStatus] }}</small>
      <p v-if="presentationWaitExpired">算法动作已成功，但 30 秒内暂未收到画面确认；未修改服务端展示状态。</p>
      <button
        v-if="presentationCanResync"
        class="presentation-resync"
        type="button"
        :disabled="loading || presentationRequestInFlight"
        @click="resyncPresentation"
      >
        <RefreshCw :size="12" />重新同步画面
      </button>
    </article>
    <article v-else-if="proposal && proposal.status !== 'AWAITING_CONFIRMATION'" class="result">
      提案状态：{{ proposal.status }}
    </article>
    <p v-if="displayError" class="error">{{ displayError }}</p>

    <footer>
      <button type="button" @click="store.refreshContexts()"><RefreshCw :size="12" />刷新上下文</button>
      <button v-if="responseUnknown" type="button" @click="store.recover()">核对上次请求</button>
      <button v-if="context" type="button" :title="presentationBinding?.bindingId ?? '尚未建立绑定'" @click="takePresentationBinding">
        {{ presentationBinding?.bindingId ? '重新接管展示' : '建立展示绑定' }}
      </button>
      <button v-if="proposal || execution" type="button" @click="store.clearActive()">清除本地视图</button>
    </footer>
  </section>

  <div v-if="!refined && dialogOpen && proposal" class="voice-modal" role="dialog" aria-modal="true" aria-label="确认冻结指令计划">
    <section>
      <header><ShieldCheck :size="18" /><strong>确认冻结计划</strong></header>
      <p>此提案等待确认。计划内容不可在此修改。</p>
      <dl>
        <div><dt>动作</dt><dd>{{ actionLabels[proposal.plan.action] }}</dd></div>
        <div v-if="proposal.plan.action === 'DEVICE_COMMAND'"><dt>目标设备</dt><dd>{{ proposal.plan.targetDeviceCode }}</dd></div>
        <div v-if="proposal.plan.action === 'DEVICE_COMMAND'"><dt>设备动作</dt><dd>{{ actionLabels[proposal.plan.deviceCommandType ?? ''] ?? proposal.plan.deviceCommandType }}</dd></div>
        <div v-if="proposal.plan.action === 'SEQUENCE'"><dt>执行步骤</dt><dd>
          <ol class="sequence-steps compact">
            <li v-for="step in proposal.plan.steps" :key="step.index">
              {{ step.action === 'START' ? '开始任务' : `${step.targetDeviceCode} · ${actionLabels[step.deviceCommandType ?? '']}` }}
            </li>
          </ol>
        </dd></div>
        <div><dt>当前仿真</dt><dd>运行 {{ context?.algorithmRunId ?? '-' }} / {{ context?.state ?? '-' }}</dd></div>
        <div><dt>设备快照</dt><dd>{{ proposal.plan.explicitDeviceCodes.length }} 个：{{ proposal.plan.explicitDeviceCodes.join('、') }}</dd></div>
      </dl>
      <details>
        <summary>核对协议身份、版本与哈希</summary>
        <dl>
          <div><dt>运行实例</dt><dd>{{ proposal.plan.runtimeRef }}</dd></div>
          <div><dt>运行代际</dt><dd>{{ proposal.plan.runtimeGeneration }}</dd></div>
          <div><dt>计划版本</dt><dd>v{{ proposal.planVersion }} / {{ proposal.plan.policyVersion }}</dd></div>
          <div><dt>计划哈希</dt><dd class="hash">{{ proposal.planHash }}</dd></div>
        </dl>
      </details>
      <p class="expires">{{ secondsLeft > 0 ? `${secondsLeft} 秒后过期` : '提案已过期，请关闭后重新创建' }}</p>
      <footer>
        <button type="button" :disabled="loading" @click="cancel"><X :size="14" />{{ secondsLeft === 0 ? '关闭' : '取消提案' }}</button>
        <button class="confirm" type="button" :disabled="loading || secondsLeft === 0" @click="confirm"><Check :size="14" />确认执行</button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.voice-p0 { display:grid; min-height:100%; align-content:start; gap:12px; padding:16px; color:#bddad6; background:linear-gradient(160deg,rgba(10,36,41,.98),rgba(4,19,24,.99)); font-size:11px; }
.voice-p0 header,.voice-p0 header > div,.voice-p0 footer,.result div { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.sequence-steps { display:grid; gap:5px; margin:7px 0 0; padding-left:20px; color:#aedad4; }.sequence-steps li { padding-left:2px; }.sequence-steps li::marker { color:#6ce4d5; }.sequence-steps li strong { float:right; color:#8fcac2; }.sequence-steps.compact { margin:0; padding-left:16px; }
.voice-head .voice-icon { display:grid; width:30px; height:30px; padding:0; color:#70e5d6; place-items:center; background:rgba(108,228,213,.08); border:1px solid rgba(108,228,213,.2); border-radius:5px; }
.voice-head .voice-title { display:grid; gap:2px; padding:0; border:0; border-radius:0; }
.voice-head strong { color:#f0fffd; font-size:13px; }.voice-head small { color:#608d88; font-size:9px; letter-spacing:.08em; }
.voice-head > span { padding:2px 6px; border:1px solid; border-radius:10px; font-size:9px; font-weight:800; }
.voice-head .mock { color:#ffd58a; border-color:#725b2e; }.voice-head .real { color:#78eadb; border-color:#286c65; }
.runtime-card { display:grid; gap:10px; margin:0; padding:11px; background:#082329; border:1px solid rgba(108,228,213,.16); border-radius:5px; }
.runtime-card dl { display:grid; margin:0; grid-template-columns:repeat(3,1fr); }.runtime-card dl div { display:grid; gap:3px; padding-left:8px; border-left:1px solid #204148; }
.runtime-card dt { color:#668f8a; font-size:9px; }.runtime-card dd { overflow:hidden; margin:0; color:#d9f1ed; font-size:10px; text-overflow:ellipsis; white-space:nowrap; }
.context-line,.scope-note,.error,.recovery-note,.result p { margin:0; }.context-line { overflow:hidden; color:#9bc9c3; text-overflow:ellipsis; white-space:nowrap; }
.context-line i { display:inline-block; width:6px; height:6px; margin-right:6px; background:#6a7d7b; border-radius:50%; }.context-line i.online { background:#45db8b; box-shadow:0 0 7px #45db8b; }
.scope-note { color:#6f9691; line-height:1.5; }.action-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:7px; }
.action-grid button,.voice-p0 footer button,.voice-modal button,.mock-scenario select { padding:7px; color:#bce8e1; cursor:pointer; background:#0a282e; border:1px solid #285159; border-radius:4px; font-size:10px; }
.action-grid button { display:grid; min-height:56px; gap:4px; text-align:left; }.action-grid button strong { color:inherit; font-size:12px; }.action-grid button small { overflow:hidden; color:#709792; font-size:9px; text-overflow:ellipsis; white-space:nowrap; }
.action-grid button:hover:not(:disabled) { color:#061a1e; background:#6ce4d5; }.action-grid button:disabled { cursor:not-allowed; opacity:.35; }
.action-grid button:hover:not(:disabled) small { color:#164c4c; }
.mock-scenario { display:flex; align-items:center; justify-content:space-between; color:#8fb5b0; }.mock-scenario select { padding:4px 6px; }
.result { padding:9px; border:1px solid #315158; border-radius:4px; background:#081e23; }.result strong { color:#f1fffd; }.result span,.result small { color:#739d98; }.result p,.error { padding-top:5px; color:#ff9d91; line-height:1.45; }
.result .presentation-resync { display:inline-flex; align-items:center; gap:5px; margin-top:8px; padding:5px 8px; color:#78e4d6; cursor:pointer; background:#0a282e; border:1px solid #28645e; border-radius:4px; font-size:10px; }.result .presentation-resync:disabled { cursor:not-allowed; opacity:.4; }
.result.success { border-color:#2a7052; }.voice-p0 footer button { padding:3px 0; background:transparent; border:0; color:#78aaa4; }
.recovery-note { color:#ffd58a; line-height:1.45; }
.voice-modal { position:fixed; inset:0; z-index:1200; display:grid; padding:20px; place-items:center; background:rgba(0,8,11,.78); backdrop-filter:blur(4px); }
.voice-modal > section { width:min(560px,100%); padding:20px; color:#b9d8d4; background:#071b20; border:1px solid #3d746f; border-radius:7px; box-shadow:0 24px 80px #000; }
.voice-modal header,.voice-modal footer { display:flex; align-items:center; gap:9px; }.voice-modal header { color:#effffd; }.voice-modal p { color:#86aca7; font-size:12px; line-height:1.5; }
.voice-modal dl { display:grid; gap:1px; margin:15px 0; background:#18353a; border:1px solid #18353a; }.voice-modal dl div { display:grid; padding:8px 10px; background:#0a2429; grid-template-columns:92px 1fr; }
.voice-modal dt { color:#779e99; }.voice-modal dd { min-width:0; margin:0; color:#d8eeeb; word-break:break-all; }.voice-modal .hash { font:10px Consolas,monospace; }
.voice-modal details { margin:10px 0; }.voice-modal summary { color:#79beb5; cursor:pointer; font-size:11px; }.voice-modal details dl { margin-top:8px; }
.voice-modal .expires { color:#ffd58a; }.voice-modal footer { justify-content:flex-end; }.voice-modal button.confirm { color:#04191b; background:#6ce4d5; border-color:#6ce4d5; }.voice-modal button:disabled { opacity:.4; }
.voice-p0.voice-refined { display:flex; min-height:100%; padding:0; gap:0; flex-direction:column; color:#ebf5f4; background:#0d1e23; font-size:12px; }
.voice-refined .refined-head { display:flex; align-items:center; justify-content:space-between; gap:12px; min-height:65px; padding:18px 52px 18px 22px; border-bottom:1px solid #243c42; }
.refined-head h2 { margin:0; font-size:17px; font-weight:600; letter-spacing:.02em; }
.refined-head svg { color:#73e2cd; }
.refined-content { display:flex; min-width:0; padding:20px 22px; gap:14px; flex:1; flex-direction:column; }
.refined-plan { margin-top:4px; padding-top:16px; border-top:1px solid #243c42; }
.refined-section-title { display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:12px; }
.refined-section-title h3 { margin:0; font-size:12px; font-weight:500; color:#d8e7e5; }
.refined-section-title > span { color:#8fa8ac; font-size:10px; }
.refined-plan ol { display:grid; gap:12px; list-style:none; margin:0; padding:0; }
.refined-plan li { display:flex; align-items:center; gap:10px; min-width:0; font-size:12px; }
.refined-plan li b { display:grid; width:24px; height:24px; place-items:center; flex-shrink:0; font:11px Bahnschrift,Consolas,monospace; color:#73e2cd; background:#15322d; border:1px solid #34534e; border-radius:6px; }
.refined-plan li > span { overflow-wrap:anywhere; }
.refined-plan-note { display:flex; flex-wrap:wrap; justify-content:space-between; gap:5px; margin:11px 0 14px; color:#8fa8ac; font-size:10px; line-height:1.65; }
.refined-confirm { display:flex; align-items:center; justify-content:center; gap:8px; width:100%; min-height:43px; margin-top:13px; border:1px solid #73e2cd; border-radius:8px; color:#082b24; background:#73e2cd; font-family:inherit; font-size:13px; font-weight:600; cursor:pointer; }
.refined-confirm:hover:not(:disabled) { background:#96eddc; }
.refined-confirm:disabled { opacity:.45; cursor:not-allowed; }
.refined-cancel,.refined-recovery { display:inline-flex; align-items:center; justify-content:center; gap:6px; padding:6px 0; color:#a6c6c3; border:0; background:transparent; font-size:11px; cursor:pointer; }
.refined-cancel { display:block; margin:7px auto 0; }
.refined-cancel:disabled,.refined-recovery:disabled { cursor:not-allowed; opacity:.45; }
.refined-recovery { color:#73e2cd; }
.refined-notice { margin:0; color:#dfbd85; font-size:11px; line-height:1.7; overflow-wrap:anywhere; }
.refined-plan > .refined-notice { margin-top:11px; }
.refined-notice.refined-error { color:#ffaaa2; }
.refined-result { padding:13px; color:#d0e6e0; background:#122a2d; border:1px solid #345052; border-radius:9px; }
.refined-result.success { background:#13332b; border-color:#315d4f; }
.refined-result.failed,.refined-result.rejected { border-color:#734842; }
.refined-result-head { display:flex; align-items:center; gap:8px; color:#bde8dc; }
.refined-result-head strong { font-size:12px; font-weight:500; }
.refined-result p { margin:6px 0 0; font-size:11px; line-height:1.65; overflow-wrap:anywhere; }
.refined-result > small { display:block; color:#a0bbb8; font-size:10px; margin-top:6px; }
.refined-execution-steps { display:grid; gap:8px; margin:10px 0 0; padding:0; list-style:none; }
.refined-execution-steps li { display:flex; flex-wrap:wrap; justify-content:space-between; gap:4px 8px; font-size:11px; }
.refined-execution-steps small { color:#a0bbb8; font-size:10px; }
.refined-footer { margin:0; padding:12px 16px; color:#91aab0; background:#0b1b20; border-top:1px solid #243c42; text-align:center; font-size:10px; }
.voice-refined button:focus-visible { outline:2px solid #73e2cd; outline-offset:3px; }
@media (max-height:850px) and (min-width:801px) { .voice-refined .refined-head { min-height:53px; padding-block:14px; } .refined-content { padding:16px 18px; gap:12px; } }
@media (max-width:500px) { .refined-content { padding:18px; } }
</style>

<script setup lang="ts">
import { presetLabel, presetMatchesText, repairAlgorithmSpeech } from '@/services/algorithmPresets'
import { computed, onBeforeUnmount, onDeactivated, onMounted, ref, shallowRef, watch } from 'vue'
import { ArrowRight, Cloud, Mic, PencilLine, Square, WandSparkles } from '@lucide/vue'
import { createVoiceIntelligenceAdapter } from '@/services/voiceIntelligence'
import { voiceRecoveryInfo } from '@/services/voiceIntelligenceRecovery'
import { normalizeVoiceAudioType, VOICE_AUDIO_MAX_BYTES } from '@/api/voiceIntelligence'
import { evaluateSpeechRecording, SPEECH_RECORDING_HINT, SPEECH_RECORDING_MAX_MS } from '@/utils/speechRecordingPolicy'
import { monitorMicrophoneLevel } from '@/utils/microphoneLevel'
import { normalizeVoiceDeviceTerms, repairContextualUsvHold } from '@/utils/voiceTranscriptNormalization'
import { sequenceMatchesSpeech, splitVoiceSequence, voiceStepLabel } from '@/utils/voiceSequence'
import { hasNegatedVoiceAction, isLocalVoiceSimulation, requiresVoiceConfirmation, voiceDeviceLabel, type VoiceAutomationRuntime } from '@/utils/voiceCommandSafety'
import type { VoiceIntent } from '@/types/voiceControl'
import type {
  VoiceAction,
} from '@/types/voiceControl'
import type {
  VoiceAudioInput, VoiceParseRequest, VoiceInputStage, VoiceIntelligenceAdapter,
  VoiceInterpretationRuntimeContext, VoiceParseResult,
  VoiceIntentCandidate,
} from '@/types/voiceIntelligence'

const props = withDefaults(defineProps<{
  adapter?: VoiceIntelligenceAdapter
  allowedActions: VoiceAction[]
  deviceCodes: string[]
  runtimeContext?: VoiceInterpretationRuntimeContext | null
  automationRuntime?: VoiceAutomationRuntime | null
  operatorScope?: string
  inputDisabled?: boolean
  autoExecuteSpeech?: boolean
  directExecuteInput?: boolean
  allowMockSubmission?: boolean
  submissionDisabled?: boolean
  allowCaptureWithoutContext?: boolean
  refined?: boolean
  compact?: boolean
  proposalReady?: boolean
  algorithmPresetsEnabled?: boolean
  reviewStatus?: string
  actionDisabledReason?: (action: VoiceAction) => string
}>(), {
  adapter: undefined, runtimeContext: null, automationRuntime: null, operatorScope: '', inputDisabled: false,
  autoExecuteSpeech: false, allowMockSubmission: false, submissionDisabled: false, allowCaptureWithoutContext: false, actionDisabledReason: undefined,
  refined: false, compact: false, proposalReady: false, algorithmPresetsEnabled: false,
})
const emit = defineEmits<{
  candidate: [intent: VoiceIntent, interpretationId?: string]
  voiceCandidate: [intent: VoiceIntent, interpretationId: string, timing: { startedAt: number; asrRequestMs: number; parseMs: number; sequenceFirstAction?: string }]
  executeCandidate: [intent: VoiceIntent, interpretationId: string, sequenceFirstAction?: string]
  draftChange: []
  interpretation: [event: { text: string; origin: string; result: VoiceParseResult }]
}>()
const adapter = props.adapter ?? createVoiceIntelligenceAdapter()
const draft = ref('')
const transcriptProvider = ref('')
const stage = ref<VoiceInputStage>('IDLE')
const result = ref<VoiceParseResult | null>(null)
const message = ref('输入文字或录音，识别结果可编辑后再解析。')
const stageElapsed = ref(0)
const stageStartedAt = ref(performance.now())
let audioPipelineStartedAt: number | null = null
let audioRequestElapsedMs = 0
let parseElapsedMs = 0
let elapsedTimer: number | undefined
type Pending = { kind: 'audio'; input: VoiceAudioInput; autoExecute?: boolean }
  | { kind: 'text'; input: VoiceParseRequest; origin: 'speech' | 'manual' }
// Keep the exact original body and key in memory for explicit recovery.
const pending = shallowRef<Pending | null>(null)
const automaticallySubmitted = ref(false)
// A speech result is handled automatically. Manual execution belongs to an
// explicitly edited/typed draft, not a second confirmation of the recording.
const automaticSpeechDraft = ref(false)
const emittedSpeechRequests = new Set<string>()
const failed = ref(false)
const retryable = ref(false)
const speechFallbackAvailable = ref(false)
const coolingDown = ref(false)
const rateLimited = ref(false)
const cooldownSeconds = ref(0)
let cooldownUntil = 0
const accessDenied = ref(false)
const permissionPending = ref(false)
let cooldownTimer: number | undefined
let recordingTimer: number | undefined
let stream: MediaStream | null = null
let recorder: MediaRecorder | null = null
let stopLevelMonitor: (() => void) | undefined
const microphoneLevel = ref<number | null>(null)
const microphoneName = ref('')
const microphoneDevices = ref<MediaDeviceInfo[]>([])
const selectedMicrophone = ref('')
const microphoneProcessing = ref(true)
const microphoneDeviceMessage = ref('')
const microphoneTest = ref(false)
const microphoneTestUrl = ref('')
let microphoneDeviceEpoch = 0
let controller: AbortController | null = null
let epoch = 0
const recoveryLabel = computed(() => rateLimited.value
  ? coolingDown.value ? `等待 ${cooldownSeconds.value} 秒后重试` : '重试原请求'
  : '使用原请求恢复查询')

const textLength = computed(() => [...draft.value].length)
const automaticSpeech = computed(() => props.autoExecuteSpeech)
const simulationSpeechEnabled = computed(() => automaticSpeech.value && isLocalVoiceSimulation(props.automationRuntime))
const simulationSpeechRuntime = computed(() => automaticSpeech.value
  && props.runtimeContext?.runtimeRef === props.automationRuntime?.runtimeRef
  && props.runtimeContext?.runtimeGeneration === props.automationRuntime?.runtimeGeneration
  && isLocalVoiceSimulation(props.automationRuntime) ? props.automationRuntime : null)
const busy = computed(() => permissionPending.value || ['TRANSCRIBING', 'PARSING'].includes(stage.value))
const blocked = computed(() => props.inputDisabled || accessDenied.value)
const captureBlocked = computed(() => props.submissionDisabled && !props.allowCaptureWithoutContext)
const candidate = computed(() => result.value?.status === 'CANDIDATE' ? result.value : null)
const candidateDisabledReason = computed(() => {
  if (blocked.value) return '当前账号没有语音控制权限'
  if (adapter.mode === 'MOCK' && !props.allowMockSubmission) return props.refined
    ? '当前是本地演示，不能向实际控制服务提交指令。'
    : '本地解析 MOCK 无后端来源记录，仅可与 P0 MOCK 演示'
  if (candidate.value?.action === 'DEVICE_COMMAND' || candidate.value?.action === 'ALGORITHM_PRESET') return ''
  if (candidate.value?.action === 'SEQUENCE') {
    const first = candidate.value.steps?.[0]?.action
    return first && ['START', 'PAUSE', 'RESUME', 'STOP'].includes(first) && props.actionDisabledReason
      ? props.actionDisabledReason(first as VoiceAction) : ''
  }
  return candidate.value && props.actionDisabledReason ? props.actionDisabledReason(candidate.value.action) : ''
})
const statusLabel = computed(() => ({
  IDLE: '等待输入', RECORDING: '正在录音', TRANSCRIBING: '正在识别', READY_TO_PARSE: '待解析',
  PARSING: '正在解析', CANDIDATE: '候选指令', NEEDS_CLARIFICATION: '需要澄清',
  UNSUPPORTED: '暂不支持', ERROR: '处理失败',
}[stage.value]))
const exactVoiceCommands: Record<string, VoiceAction> = {
  '开始任务': 'START', '开始执行任务': 'START', '执行任务': 'START',
  '请开始任务': 'START', '请开始执行任务': 'START', '现在开始任务': 'START',
  '开始当前任务': 'START', '启动任务': 'START', '启动当前任务': 'START',
  '暂停任务': 'PAUSE', '暂停一下': 'PAUSE', '暂停当前任务': 'PAUSE',
  '暂停一下当前任务': 'PAUSE', '请暂停任务': 'PAUSE',
  '继续任务': 'RESUME', '继续执行任务': 'RESUME', '恢复任务': 'RESUME',
  '恢复运行': 'RESUME', '恢复执行': 'RESUME', '请继续任务': 'RESUME',
  '停止任务': 'STOP', '停止执行任务': 'STOP', '结束任务': 'STOP',
  '停止当前任务': 'STOP', '停止当前运行任务': 'STOP', '结束当前任务': 'STOP',
  '终止任务': 'STOP', '终止当前任务': 'STOP', '请停止任务': 'STOP',
  '请停止当前任务': 'STOP', '立即停止任务': 'STOP', '马上停止任务': 'STOP',
  '请立即停止任务': 'STOP',
}
const exactFastPath = computed(() => {
  const parsed = result.value
  if (parsed?.status !== 'CANDIDATE' || parsed.provider !== 'local-rules' || parsed.model !== 'rules-v1') return false
  const exactText = parsed.normalizedText.trim().replace(/[。！？!?，,]+$/, '')
  return exactVoiceCommands[exactText] === parsed.action
})
const adapterLabel = computed(() => adapter.mode === 'MOCK' ? '本地解析 MOCK'
  : result.value?.provider === 'test-fixture' ? '后端测试适配器' : adapter.name === 'unconfigured' ? '待配置' : '平台接口')
const speechSource = computed(() => {
  if (adapter.mode === 'MOCK') return { label: '本地语音演示', detail: '非真实识别' }
  if (transcriptProvider.value === 'aliyun-asr') return { label: '阿里云语音识别', detail: '最近一次识别来源' }
  if (transcriptProvider.value === 'local-asr') return { label: '本地语音识别', detail: '最近一次识别来源' }
  if (transcriptProvider.value === 'test-fixture') return { label: '语音测试样例', detail: '非真实识别' }
  return { label: '平台语音识别', detail: adapter.name === 'unconfigured' ? '待配置' : '录音后确认来源' }
})
const captureTitle = computed(() => permissionPending.value ? '正在请求麦克风权限'
  : stage.value === 'RECORDING' ? microphoneTest.value ? '正在测试麦克风' : '正在聆听你的指令'
    : stage.value === 'TRANSCRIBING' ? '正在转换为文字'
      : stage.value === 'PARSING' ? '正在理解指令'
        : draft.value.trim() ? '文字已就绪' : '点击录音')
const recordingSecondsLeft = computed(() => Math.ceil(evaluateSpeechRecording({ elapsedMs: stageElapsed.value * 1000 }).remainingMs / 1000))
const captureNote = computed(() => stage.value === 'RECORDING' ? `说完再次点击结束；停顿不会截断 · 剩余 ${recordingSecondsLeft.value} 秒`
  : stage.value === 'TRANSCRIBING' ? '请稍候，无需重复录音'
    : permissionPending.value ? '请在浏览器中允许使用麦克风'
      : simulationSpeechEnabled.value ? '说完点击结束，自动执行'
        : automaticSpeech.value ? '常规明确指令自动执行；返航、降落、停止需确认' : '点击麦克风说话，核对文字后预览指令')
const examples = computed(() => props.algorithmPresetsEnabled ? [
  { label: '开始围捕', text: '开始围捕。' },
  { label: '无人机分组', text: '将一二三号无人机分配给一号敌船。' },
  { label: '恢复护航', text: '恢复护航。' },
] : [
  { label: '开始任务 → 一号机悬停', text: '开始任务，然后让一号无人机悬停。' },
  { label: '二号艇驻留', text: '二号无人艇驻留。' },
  { label: '一号机归队', text: '一号无人机安全归队。' },
])
const microphoneStatus = computed(() => microphoneLevel.value === null ? '音量检测不可用'
  : microphoneLevel.value < 20 ? '输入很弱，请检查麦克风' : '已采到声音')

watch(draft, () => {
  if (!busy.value && !pending.value && stage.value !== 'RECORDING') automaticSpeechDraft.value = false
  automaticallySubmitted.value = false
  emit('draftChange')
  result.value = null
  if (!busy.value && !pending.value && stage.value !== 'RECORDING') {
    stage.value = draft.value.trim() ? 'READY_TO_PARSE' : 'IDLE'
    // A fresh editable draft supersedes any warning about the previous context
    // or interpretation. ASR/request messages remain intact while processing.
    if (props.refined) message.value = ''
  }
}, { flush: 'sync' })

function stopCapture() {
  window.clearTimeout(recordingTimer)
  stopLevelMonitor?.()
  stopLevelMonitor = undefined
  const previous = recorder
  recorder = null
  if (previous?.state === 'recording') previous.stop()
  stream?.getTracks().forEach(track => track.stop())
  stream = null
}
function clearMicrophoneTest() {
  if (microphoneTestUrl.value) URL.revokeObjectURL(microphoneTestUrl.value)
  microphoneTestUrl.value = ''
}
async function refreshMicrophones() {
  const ticket = ++microphoneDeviceEpoch
  if (!navigator.mediaDevices?.enumerateDevices) return
  try {
    const devices = await navigator.mediaDevices.enumerateDevices()
    if (ticket !== microphoneDeviceEpoch) return
    microphoneDevices.value = devices.filter(device => device.kind === 'audioinput'
      && device.deviceId && !['default', 'communications'].includes(device.deviceId))
    microphoneDeviceMessage.value = ''
  } catch {
    if (ticket === microphoneDeviceEpoch) microphoneDeviceMessage.value = '无法读取设备列表，可先点击测试授予麦克风权限。'
  }
}
function resetInput() {
  automaticSpeechDraft.value = false
  emit('draftChange')
  epoch++
  controller?.abort()
  controller = null
  stopCapture()
  clearMicrophoneTest()
  permissionPending.value = false
  window.clearTimeout(cooldownTimer)
  coolingDown.value = false
  rateLimited.value = false
  cooldownSeconds.value = 0
  cooldownUntil = 0
  pending.value = null
  automaticallySubmitted.value = false
  failed.value = false
  speechFallbackAvailable.value = false
  result.value = null
  transcriptProvider.value = ''
  audioPipelineStartedAt = null
  audioRequestElapsedMs = 0
  parseElapsedMs = 0
  draft.value = ''
  stage.value = 'IDLE'
}
watch([
  () => props.operatorScope,
  () => props.inputDisabled,
  () => props.runtimeContext?.runtimeRef,
  () => props.runtimeContext?.runtimeGeneration,
  () => props.runtimeContext?.contextVersion,
], () => {
  resetInput()
  accessDenied.value = false
  message.value = '操作员已切换或运行上下文变化，请重新输入指令。'
}, { flush: 'sync' })
watch(stage, () => { stageStartedAt.value = performance.now(); stageElapsed.value = 0 }, { flush: 'sync' })

// Missing task context may allow capture-only use, while a real dispatch lock
// still invalidates speech in flight so it cannot submit after a later unlock.
watch(captureBlocked, disabled => {
  if (!disabled || !(permissionPending.value || stage.value === 'RECORDING'
    || pending.value?.kind === 'audio' || (pending.value?.kind === 'text' && pending.value.origin === 'speech'))) return
  epoch++
  controller?.abort()
  controller = null
  stopCapture()
  permissionPending.value = false
  pending.value = null
  result.value = null
  failed.value = false
  speechFallbackAvailable.value = false
  stage.value = draft.value.trim() ? 'READY_TO_PARSE' : 'IDLE'
  message.value = '当前指令仍在处理，已停止本次语音自动提交；请稍后重新录音。'
}, { flush: 'sync' })

const missionIntents: Record<VoiceAction, VoiceIntent> = {
  START: 'MISSION_START', PAUSE: 'MISSION_PAUSE', RESUME: 'MISSION_RESUME', STOP: 'MISSION_STOP',
}
const deviceCommands = new Set(['UAV_HOVER', 'UAV_RESUME', 'UAV_RETURN', 'UAV_LAND', 'USV_HOLD', 'USV_RESUME', 'USV_RETURN', 'USV_STOP'])
const taskClause = /(?:开始|启动|执行|暂停|继续|恢复|停止|终止|结束)(?:执行|当前|运行)?任务/u
function normalizedSpeech(text: string) {
  return normalizeVoiceDeviceTerms(repairAlgorithmSpeech(text)).replace(/[。！？!?，,]+$/, '')
}
function validDeviceTarget(target: string | undefined, command: string | undefined, text: string) {
  if (!target || !command || !props.deviceCodes.includes(target) || !deviceCommands.has(command)
    || !target.startsWith(`${command.slice(0, 3)}-`)) return false
  const normalizedText = normalizeVoiceDeviceTerms(text)
  const mentioned = new Set<string>()
  const ordinal = (value: string) => /^\d+$/.test(value) ? Number(value) : '一二三四五六七八九十'.indexOf(value) + 1
  const add = (type: string, number: string) => mentioned.add(`${type}-${String(ordinal(number)).padStart(3, '0')}`)
  for (const match of normalizedText.matchAll(/(UAV|USV)[-_]?(\d+)|第?([一二三四五六七八九十]|\d+)号?(?:架|艘)?(无人机|无人艇)|(无人机|无人艇)[-_]?([一二三四五六七八九十]|\d+)/giu)) {
    if (match[1]) add(match[1].toUpperCase(), match[2]!)
    else add((match[4] ?? match[5]) === '无人机' ? 'UAV' : 'USV', (match[3] ?? match[6])!)
  }
  const actions: Array<[string, string]> = target.startsWith('UAV-')
    ? [['悬停', 'UAV_HOVER'], ['暂停', 'UAV_HOVER'], ['返航', 'UAV_RETURN'], ['返回', 'UAV_RETURN'], ['归队', 'UAV_RESUME'], ['继续', 'UAV_RESUME'], ['停止', 'UAV_LAND'], ['降落', 'UAV_LAND']]
    : [['驻留', 'USV_HOLD'], ['待命', 'USV_HOLD'], ['保持', 'USV_HOLD'], ['暂停', 'USV_HOLD'], ['返航', 'USV_RETURN'], ['返回', 'USV_RETURN'], ['归队', 'USV_RESUME'], ['继续', 'USV_RESUME'], ['停止', 'USV_STOP'], ['停船', 'USV_STOP']]
  const matches = actions.filter(([word]) => normalizedText.includes(word))
  return mentioned.size === 1 && mentioned.has(target) && matches.length === 1 && matches[0]![1] === command
}
function safeSpeechCandidate(parsed: VoiceIntentCandidate, input: VoiceParseRequest) {
  if (adapter.mode !== 'BACKEND' || parsed.requestId !== input.requestId
    || normalizedSpeech(parsed.normalizedText) !== normalizedSpeech(input.text)
    || hasNegatedVoiceAction(input.text) || hasNegatedVoiceAction(parsed.normalizedText)
    || /(不要|别|无需|不用|禁止|不想|不能|不必|取消|如果|是否|能否|可不可以|能不能|也许|或者|还是|吗|么|？|\?)/u.test(input.text)
    || !['local-rules', 'local-llm'].includes(parsed.provider)) return false
  if (parsed.action === 'DEVICE_COMMAND') {
    return parsed.intent === 'SINGLE_DEVICE_CONTROL' && parsed.provider === 'local-rules'
      && !taskClause.test(normalizedSpeech(input.text))
      && parsed.model === 'rules-v1' && validDeviceTarget(parsed.targetDeviceCode, parsed.deviceCommandType, input.text)
  }
  if (parsed.action === 'SEQUENCE') {
    const steps = parsed.steps
    return props.refined && parsed.intent === 'COMMAND_SEQUENCE' && parsed.provider === 'local-rules'
      && parsed.model === 'rules-sequence-v1' && !!steps
      && sequenceMatchesSpeech(input.text, steps, validDeviceTarget)
  }
  if (parsed.action === 'ALGORITHM_PRESET') return parsed.intent === 'ALGORITHM_PRESET'
    && parsed.provider === 'local-rules' && parsed.model === 'rules-v1'
    && presetMatchesText(input.text, parsed.parameters)
  // Automatic fleet commands must be explicit; in particular an inferred STOP
  // must never broaden an ambiguous utterance into a whole-fleet shutdown.
  return props.allowedActions.includes(parsed.action) && parsed.intent === missionIntents[parsed.action]
    && exactVoiceCommands[normalizedSpeech(input.text)] === parsed.action
    && (parsed.provider !== 'local-rules' || parsed.model === 'rules-v1')
    && (parsed.action !== 'STOP' || parsed.provider === 'local-rules')
}
function rejectNegatedInput(text: string, origin: 'speech' | 'manual') {
  if (!hasNegatedVoiceAction(text)) return false
  result.value = null
  stage.value = 'NEEDS_CLARIFICATION'
  speechFallbackAvailable.value = origin === 'speech'
  message.value = '检测到否定表达，整条指令未下发。请重新明确要执行的动作；不会执行被否定的动作。'
  return true
}
function handleParsed(parsed: VoiceParseResult, input: VoiceParseRequest, origin: 'speech' | 'manual') {
  if (parsed.requestId !== input.requestId) {
    result.value = null
    stage.value = 'ERROR'
    speechFallbackAvailable.value = origin === 'speech'
    message.value = '解析响应与本次请求编号不一致，已阻止提交。请核对文字后重新预览。'
    return
  }
  if (rejectNegatedInput(input.text, origin) || rejectNegatedInput(parsed.normalizedText, origin)) return
  emit('interpretation', { text: input.text, origin, result: parsed })
  result.value = parsed
  stage.value = parsed.status === 'NOT_ACTIONABLE' ? 'NEEDS_CLARIFICATION' : parsed.status
  if (parsed.status !== 'CANDIDATE') {
    speechFallbackAvailable.value = origin === 'speech'
    const clauses = splitVoiceSequence(input.text).filter(Boolean)
    message.value = parsed.reason === 'AMBIGUOUS_ACTION' && /2[–-]4/u.test(parsed.message) && clauses.length > 4
      ? `本句识别为 ${clauses.length} 步，当前最多支持 4 步；整条指令未下发，本条指令中的动作均未执行。请拆分为多次输入，每次最多 4 步；等待前一条执行完成后再说下一条，每次都请写明设备编号。`
      : parsed.message
    return
  }
  if (origin === 'manual') {
    if (props.submissionDisabled || candidateDisabledReason.value) {
      message.value = candidateDisabledReason.value || '当前指令仍在处理，请稍后重新预览。'
    } else {
      if (props.directExecuteInput && simulationSpeechRuntime.value && adapter.mode === 'BACKEND') {
        result.value = null
        automaticallySubmitted.value = true
        message.value = '正在执行…'
        emit('executeCandidate', parsed.intent, parsed.requestId, parsed.steps?.[0]?.action)
        return
      }
      message.value = props.refined ? '请核对下方设备与动作，确认后执行。' : '请核对识别文字与动作，再生成待确认提案。'
      if (props.refined) submitCandidate()
    }
    return
  }
  const safeAutomaticCandidate = origin === 'speech' && automaticSpeech.value && safeSpeechCandidate(parsed, input)
    && !blocked.value && !props.submissionDisabled && !candidateDisabledReason.value
    && draft.value === input.text && !emittedSpeechRequests.has(parsed.requestId)
  if (safeAutomaticCandidate) {
    emittedSpeechRequests.add(parsed.requestId)
    if (requiresVoiceConfirmation(parsed, simulationSpeechRuntime.value)) {
      speechFallbackAvailable.value = false
      message.value = '包含高风险动作，尚未下发。请核对完整计划中的设备编号与动作后确认。'
      submitCandidate()
      return
    }
    result.value = null
    automaticallySubmitted.value = true
    speechFallbackAvailable.value = false
    const repairedHold = repairContextualUsvHold(input.text)
    const repairedDevice = normalizeVoiceDeviceTerms(input.text)
    message.value = parsed.action === 'DEVICE_COMMAND'
      ? repairedHold !== input.text
        ? `已按完整驻留口令纠错为“${normalizedSpeech(repairedHold)}”；正在校验并提交至 ${parsed.targetDeviceCode}。`
        : repairedDevice !== input.text
          ? `已按设备指令上下文纠错为“${normalizedSpeech(repairedDevice)}”；正在校验并提交至 ${voiceDeviceLabel(parsed.targetDeviceCode)}。`
        : `单设备指令已识别，正在校验并提交至 ${voiceDeviceLabel(parsed.targetDeviceCode)}。`
      : parsed.action === 'ALGORITHM_PRESET' && repairAlgorithmSpeech(input.text) !== input.text
        ? `已按口令纠正为“${repairAlgorithmSpeech(input.text).replace(/[。！!，,]+$/u, '')}”，正在执行。`
      : parsed.action === 'SEQUENCE' ? '多步指令已识别：不同设备可并行；明确先后、等待、同设备冲突和任务级动作按顺序执行。失败后不再下发剩余动作。'
        : '明确口令已识别，正在自动提交仿真动作并等待回执。'
    emit('voiceCandidate', parsed.intent, parsed.requestId, {
      startedAt: origin === 'speech' ? audioPipelineStartedAt ?? Date.now() : Date.now(),
      asrRequestMs: origin === 'speech' ? audioRequestElapsedMs : 0,
      parseMs: parseElapsedMs,
      sequenceFirstAction: parsed.steps?.[0]?.action,
    })
  } else {
    speechFallbackAvailable.value = origin === 'speech'
    message.value = parsed.intent === 'MISSION_STOP' && !exactFastPath.value
      ? '语音识别对应停止任务，但不是明确停止口令；已阻止自动执行，请核对或修改文字。'
      : candidateDisabledReason.value || (props.submissionDisabled ? '当前指令仍在处理，请稍后重新输入。'
        : '未匹配到明确指令，请重新说出设备、动作和目标。')
  }
}

async function runRequest() {
  if (!pending.value || blocked.value || busy.value || coolingDown.value) return
  if (captureBlocked.value && (pending.value.kind === 'audio' || pending.value.origin === 'speech')) return
  const current = pending.value
  if (current.kind === 'text' && rejectNegatedInput(current.input.text, current.origin)) {
    pending.value = null
    return
  }
  const ticket = epoch
  const active = new AbortController()
  controller = active
  failed.value = false
  rateLimited.value = false
  result.value = null
  stage.value = current.kind === 'audio' ? 'TRANSCRIBING' : 'PARSING'
  message.value = current.kind === 'audio'
    ? '正在识别录音，可能需要数十秒，请等待；无需重复录音。'
    : '正在解析指令，请等待。'
  try {
    if (current.kind === 'audio') {
      const asrStartedAt = performance.now()
      const transcript = await adapter.transcribe({ ...current.input, signal: active.signal })
      if (ticket !== epoch || active.signal.aborted) return
      audioRequestElapsedMs += Math.round(performance.now() - asrStartedAt)
      transcriptProvider.value = transcript.provider
      draft.value = transcript.text
      pending.value = null
      // Only real ASR providers enter the speech-to-intent flow. Test fixtures
      // and the local mock must remain transcription-only in this UI.
      const transcriptIsRealAsr = adapter.mode === 'BACKEND' && transcript.requestId === current.input.requestId
        && (transcript.provider === 'local-asr' || transcript.provider === 'aliyun-asr')
      if (!transcript.text.trim()) {
        speechFallbackAvailable.value = true
        stage.value = 'ERROR'
        message.value = '没有识别到有效语音，请重新录音或改为输入文字。'
        return
      }
      const recordingWasCutOff = current.autoExecute === false
      automaticSpeechDraft.value = !!simulationSpeechRuntime.value && transcriptIsRealAsr && !recordingWasCutOff
      if (recordingWasCutOff) speechFallbackAvailable.value = true
      message.value = recordingWasCutOff
        ? '已达到录音时长上限，指令可能未说完；请核对文字后预览确认，不会自动执行。'
        : transcript.provider === 'test-fixture'
        ? '后端测试适配器返回的固定样例，请核对文字；未调用真实 ASR。'
        : adapter.mode === 'MOCK' ? '本地录音演示返回固定文字，不是实际语音识别。'
          : transcriptIsRealAsr && automaticSpeech.value
            ? '语音已识别，正在解析指令。'
            : '请核对识别文字后再解析。'
      if (automaticSpeech.value && !recordingWasCutOff && transcriptIsRealAsr && transcript.text.trim() && !captureBlocked.value) {
        if (rejectNegatedInput(transcript.text, 'speech')) return
        stage.value = 'PARSING'
        const parseInput: VoiceParseRequest = {
          requestId: crypto.randomUUID(),
          text: transcript.text,
          locale: 'zh-CN',
          allowedActions: [...props.allowedActions],
          availableDeviceCodes: [...props.deviceCodes],
          runtimeContext: props.runtimeContext ? { ...props.runtimeContext } : null,
        }
        // Preserve the interpretation key/body if the second stage times out.
        pending.value = { kind: 'text', input: parseInput, origin: 'speech' }
        const parseStartedAt = performance.now()
        const parsed = await adapter.parse({ ...parseInput, signal: active.signal })
        if (ticket !== epoch || active.signal.aborted) return
        parseElapsedMs += Math.round(performance.now() - parseStartedAt)
        pending.value = null
        handleParsed(parsed, parseInput, 'speech')
        return
      }
      stage.value = 'READY_TO_PARSE'
    } else {
      const parseStartedAt = performance.now()
      const parsed = await adapter.parse({ ...current.input, signal: active.signal })
      if (ticket !== epoch || active.signal.aborted) return
      parseElapsedMs += Math.round(performance.now() - parseStartedAt)
      pending.value = null
      handleParsed(parsed, current.input, current.origin)
    }
    pending.value = null
  } catch (error) {
    if (ticket !== epoch || active.signal.aborted) return
    const info = voiceRecoveryInfo(error)
    if (info.forbidden) {
      resetInput()
      accessDenied.value = true
    } else {
      failed.value = true
      speechFallbackAvailable.value = automaticSpeech.value && (current.kind === 'audio' || current.origin === 'speech')
      retryable.value = info.retryable
      rateLimited.value = info.rateLimited
      // A definitive empty transcript has no unknown execution to recover.
      // Release the finished recording so recording and text editing remain available.
      if (current.kind === 'audio' && info.code === 'VOICE_NO_SPEECH') pending.value = null
      coolingDown.value = info.retryAfter > 0
      cooldownSeconds.value = info.retryAfter
      cooldownUntil = Date.now() + info.retryAfter * 1000
      window.clearTimeout(cooldownTimer)
      if (coolingDown.value) cooldownTimer = window.setTimeout(() => {
        coolingDown.value = false
        cooldownSeconds.value = 0
      }, info.retryAfter * 1000)
    }
    stage.value = 'ERROR'
    message.value = info.message
  } finally {
    if (controller === active) controller = null
  }
}
function cancelWaiting() {
  controller?.abort()
  stage.value = 'ERROR'
  failed.value = true
  retryable.value = true
  message.value = '已停止浏览器等待，后端可能仍在处理。可使用原请求恢复查询。'
}
async function retryRateLimitedAsNewRequest() {
  if (!rateLimited.value || !pending.value || coolingDown.value || busy.value || blocked.value || props.submissionDisabled) return
  // Only a definitive 429 exposes this explicit, potentially billable action.
  // Unknown/time-out requests must retain their original key for recovery.
  const previous = pending.value
  pending.value = { ...previous, input: { ...previous.input, requestId: crypto.randomUUID() } } as Pending
  await runRequest()
}
function discardPending() {
  if (coolingDown.value || busy.value) return
  resetInput()
  message.value = '已清除本页请求；后续操作将作为新请求，可能产生新的调用费用。'
}
function switchToText() {
  automaticSpeechDraft.value = false
  epoch++
  controller?.abort()
  controller = null
  pending.value = null
  failed.value = false
  speechFallbackAvailable.value = false
  retryable.value = false
  rateLimited.value = false
  stage.value = draft.value.trim() ? 'READY_TO_PARSE' : 'IDLE'
  message.value = '修改文字后点击执行。'
}

function preferredAudioType() {
  return ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/webm']
    .find(type => MediaRecorder.isTypeSupported(type)) ?? ''
}
async function startRecording(testOnly = false) {
  if (blocked.value || captureBlocked.value || busy.value || coolingDown.value || pending.value || recorder) return
  emit('draftChange')
  automaticallySubmitted.value = false
  message.value = ''
  const ticket = epoch
  transcriptProvider.value = ''
  speechFallbackAvailable.value = false
  permissionPending.value = true
  result.value = null
  microphoneTest.value = testOnly
  clearMicrophoneTest()
  try {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      throw new Error('当前浏览器不支持录音，请使用 HTTPS / localhost 或改用文字。')
    }
    const acquired = await navigator.mediaDevices.getUserMedia({
      audio: {
        ...(selectedMicrophone.value ? { deviceId: { exact: selectedMicrophone.value } } : {}),
        echoCancellation: microphoneProcessing.value,
        noiseSuppression: microphoneProcessing.value,
        autoGainControl: microphoneProcessing.value,
      },
    })
    if (ticket !== epoch || blocked.value || captureBlocked.value) { acquired.getTracks().forEach(track => track.stop()); return }
    stream = acquired
    microphoneName.value = acquired.getAudioTracks?.()[0]?.label || '默认麦克风'
    void refreshMicrophones()
    microphoneLevel.value = null
    stopLevelMonitor = monitorMicrophoneLevel(acquired, level => {
      if (ticket === epoch) microphoneLevel.value = level
    })
    const mimeType = preferredAudioType()
    const active = mimeType ? new MediaRecorder(acquired, { mimeType }) : new MediaRecorder(acquired)
    recorder = active
    const chunks: Blob[] = []
    let size = 0
    let stoppedAtLimit = false
    active.addEventListener('dataavailable', event => {
      if (ticket !== epoch) return
      chunks.push(event.data)
      size += event.data.size
      if (size > VOICE_AUDIO_MAX_BYTES && active.state === 'recording') active.stop()
    })
    active.addEventListener('stop', () => {
      acquired.getTracks().forEach(track => track.stop())
      if (ticket !== epoch) return
      stopLevelMonitor?.()
      stopLevelMonitor = undefined
      window.clearTimeout(recordingTimer)
      if (recorder === active) recorder = null
      if (stream === acquired) stream = null
      stage.value = 'IDLE'
      if (blocked.value || captureBlocked.value) {
        message.value = '当前指令仍在处理，请稍后重新录音。'
        return
      }
      if (size === 0 || size > VOICE_AUDIO_MAX_BYTES) {
        stage.value = 'ERROR'
        message.value = size === 0 ? '音频为空，请重新录音。' : '音频超过 5 MiB，请缩短录音。'
        return
      }
      const audio = new Blob(chunks, { type: active.mimeType || 'audio/webm' })
      if (testOnly) {
        microphoneTestUrl.value = URL.createObjectURL(audio)
        stage.value = 'READY_TO_PARSE'
        message.value = '测试录音已完成，请播放检查声音。未上传、未执行指令。'
        return
      }
      audioPipelineStartedAt = Date.now()
      audioRequestElapsedMs = 0
      parseElapsedMs = 0
      pending.value = { kind: 'audio', autoExecute: !stoppedAtLimit, input: {
        audio,
        requestId: crypto.randomUUID(), locale: 'zh-CN',
      } }
      void runRequest()
    }, { once: true })
    active.addEventListener('error', () => {
      if (ticket !== epoch) return
      resetInput()
      stage.value = 'ERROR'
      message.value = '录音设备异常，请重新录音或使用文字。'
    })
    active.start(1000)
    // Start each recording with a clean transcript so an older candidate
    // cannot look like the result of the new utterance while ASR is pending.
    draft.value = ''
    result.value = null
    stage.value = 'RECORDING'
    message.value = SPEECH_RECORDING_HINT
    recordingTimer = window.setTimeout(() => {
      if (active.state === 'recording') {
        stoppedAtLimit = true
        active.stop()
      }
    }, SPEECH_RECORDING_MAX_MS)
  } catch (error) {
    if (ticket !== epoch) return
    stopCapture()
    stage.value = 'ERROR'
    message.value = error instanceof DOMException && error.name === 'NotAllowedError'
      ? '麦克风权限被拒绝，请授权或使用文字。'
      : error instanceof DOMException && ['NotFoundError', 'OverconstrainedError'].includes(error.name)
        ? '所选麦克风不可用，请刷新设备列表并重新选择。'
        : error instanceof Error ? error.message : '无法录音。'
  } finally {
    if (ticket === epoch) permissionPending.value = false
  }
}
function stopRecording() {
  if (recorder?.state === 'recording') recorder.stop()
}
function chooseAudioFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || blocked.value || captureBlocked.value || busy.value || coolingDown.value || pending.value || stage.value === 'RECORDING') return
  emit('draftChange')
  automaticallySubmitted.value = false
  result.value = null
  const extensionType = /\.wav$/i.test(file.name) ? 'audio/wav'
    : /\.mp3$/i.test(file.name) ? 'audio/mpeg'
      : /\.mp4$|\.m4a$/i.test(file.name) ? 'audio/mp4'
        : /\.ogg$/i.test(file.name) ? 'audio/ogg' : /\.webm$/i.test(file.name) ? 'audio/webm' : ''
  const type = normalizeVoiceAudioType(file.type || extensionType)
  if (file.size === 0 || file.size > VOICE_AUDIO_MAX_BYTES
    || !['audio/wav', 'audio/mpeg', 'audio/mp4', 'audio/ogg', 'audio/webm'].includes(type)) {
    stage.value = 'ERROR'
    message.value = file.size > VOICE_AUDIO_MAX_BYTES
      ? '录音文件超过 5 MiB 限制。' : '文件测试支持 WAV、MP3、MP4/M4A、Ogg 和 WebM 音频。'
    return
  }
  speechFallbackAvailable.value = false
  audioPipelineStartedAt = Date.now()
  audioRequestElapsedMs = 0
  parseElapsedMs = 0
  pending.value = { kind: 'audio', input: {
    audio: new Blob([file], { type }), requestId: crypto.randomUUID(), locale: 'zh-CN',
  } }
  void runRequest()
}
async function presetText(text: string) {
  if (busy.value || blocked.value || captureBlocked.value || props.proposalReady) return
  draft.value = text
  await parseDraft()
}
defineExpose({ presetText })

async function parseDraft() {
  if (busy.value || coolingDown.value || pending.value || blocked.value || stage.value === 'RECORDING' || !draft.value.trim() || textLength.value > 200
    || (props.refined && (captureBlocked.value || props.proposalReady || automaticallySubmitted.value))) return
  parseElapsedMs = 0
  pending.value = { kind: 'text', origin: 'manual', input: {
    requestId: crypto.randomUUID(), text: draft.value, locale: 'zh-CN',
    allowedActions: [...props.allowedActions], availableDeviceCodes: [...props.deviceCodes],
    runtimeContext: props.runtimeContext ? { ...props.runtimeContext } : null,
  } }
  await runRequest()
}
function chooseExample(text: string) {
  if (blocked.value || busy.value || pending.value || stage.value === 'RECORDING') return
  draft.value = text
}
function submitCandidate() {
  if (!candidate.value || props.submissionDisabled || candidateDisabledReason.value) return
  if (rejectNegatedInput(draft.value, 'manual') || rejectNegatedInput(candidate.value.normalizedText, 'manual')) return
  if (adapter.mode === 'MOCK') emit('candidate', candidate.value.intent)
  else emit('candidate', candidate.value.intent, candidate.value.requestId)
  result.value = null
}
onBeforeUnmount(resetInput)
onDeactivated(resetInput)
onMounted(() => {
  void refreshMicrophones()
  elapsedTimer = window.setInterval(() => {
    if (coolingDown.value) cooldownSeconds.value = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000))
    if (['RECORDING', 'TRANSCRIBING', 'PARSING'].includes(stage.value)) {
      stageElapsed.value = Math.floor((performance.now() - stageStartedAt.value) / 1000)
    }
  }, 250)
})
onBeforeUnmount(() => window.clearInterval(elapsedTimer))
onBeforeUnmount(() => { microphoneDeviceEpoch++ })
</script>

<template>
  <section class="intelligence-input" :class="{ 'is-refined': refined, 'is-compact': compact }" aria-label="语音与文本指令输入">
    <template v-if="refined">
      <div v-if="!compact" class="refined-provider" aria-live="polite">
        <span><Cloud :size="15" aria-hidden="true" />{{ speechSource.label }}</span>
        <small>{{ speechSource.detail }}</small>
      </div>
      <section class="refined-capture" :class="{ recording: stage === 'RECORDING' }" aria-label="语音录入">
        <div class="refined-mic-orbit">
          <button class="refined-mic" type="button" :aria-label="stage === 'RECORDING' ? '停止录音' : '开始录音'"
            :aria-pressed="stage === 'RECORDING'" :disabled="stage !== 'RECORDING' && (busy || coolingDown || !!pending || blocked || captureBlocked)"
            @click="stage === 'RECORDING' ? stopRecording() : startRecording()">
            <Square v-if="stage === 'RECORDING'" :size="compact ? 16 : 27" aria-hidden="true" />
            <Mic v-else :size="compact ? 18 : 29" aria-hidden="true" />
          </button>
        </div>
        <div class="refined-capture-copy" aria-live="polite">
          <strong class="refined-capture-title">{{ captureTitle }}</strong>
          <p class="refined-capture-note">{{ captureNote }}</p>
        </div>
        <div v-if="stage === 'RECORDING'" class="refined-audio-level">
          <small :title="microphoneName">{{ microphoneName }}</small>
          <meter v-if="microphoneLevel !== null" min="0" max="100" :value="microphoneLevel" aria-label="麦克风输入音量" />
          <span>{{ microphoneStatus }}</span>
        </div>
      </section>
      <details class="microphone-settings">
        <summary>麦克风设置与测试</summary>
        <label>输入设备
          <select v-model="selectedMicrophone" aria-label="录音输入设备"
            :disabled="busy || stage === 'RECORDING' || !!pending">
            <option value="">系统默认</option>
            <option v-if="selectedMicrophone && !microphoneDevices.some(device => device.deviceId === selectedMicrophone)"
              :value="selectedMicrophone" disabled>所选设备已不可用</option>
            <option v-for="(device, index) in microphoneDevices" :key="device.deviceId" :value="device.deviceId">
              {{ device.label || `麦克风 ${index + 1}` }}
            </option>
          </select>
        </label>
        <label class="microphone-processing"><input v-model="microphoneProcessing" type="checkbox"
          :disabled="busy || stage === 'RECORDING' || !!pending" />降噪与回声消除</label>
        <div class="microphone-test-actions">
          <button type="button" :disabled="busy || stage === 'RECORDING'" @click="refreshMicrophones">刷新设备</button>
          <button type="button" :disabled="busy || !!pending || blocked || captureBlocked || coolingDown || stage === 'RECORDING'"
            @click="startRecording(true)">仅录音测试</button>
        </div>
        <p v-if="microphoneDeviceMessage">{{ microphoneDeviceMessage }}</p>
        <p v-if="stage === 'RECORDING' && microphoneTest">说完点击上方结束，仅在本页回放。</p>
        <audio v-if="microphoneTestUrl" :src="microphoneTestUrl" controls aria-label="麦克风测试回放" />
      </details>
      <section class="refined-composer">
        <label class="refined-section-label">
          <span>识别文字<small v-if="!compact">{{ directExecuteInput ? '可编辑' : simulationSpeechEnabled ? '仿真语音自动执行 · 编辑后需确认' : automaticSpeech ? '常规语音自动执行 · 高风险与编辑后需确认' : '可编辑后再执行' }}</small></span>
          <div class="refined-transcript-box">
            <textarea v-model="draft" rows="3" :placeholder="algorithmPresetsEnabled ? '如：开始围捕 / 一号无人机追击二号敌船' : compact ? '如：一号无人机悬停' : '试着说：开始任务，然后让一号无人机悬停。'"
              aria-label="识别文字" :disabled="busy || !!pending || blocked || stage === 'RECORDING'" />
            <div class="refined-text-footer"><span><PencilLine :size="12" aria-hidden="true" />{{ compact ? '可直接输入' : '也可以直接输入指令' }}</span><span>{{ textLength }} / 200</span></div>
          </div>
        </label>
        <p v-if="textLength > 200" class="refined-error" role="alert">识别文字共 {{ textLength }} 字，请编辑至 200 字以内；文字未截断。</p>
        <div v-if="!compact" class="refined-examples" aria-label="示例指令">
          <button v-for="example in examples" :key="example.text" type="button"
            :disabled="busy || !!pending || blocked || stage === 'RECORDING'" @click="chooseExample(example.text)">{{ example.label }}</button>
        </div>
      </section>
      <p v-if="(reviewStatus || message) && stage !== 'IDLE' && stage !== 'RECORDING'" class="refined-status" :class="{ 'refined-error': ['ERROR', 'UNSUPPORTED', 'NEEDS_CLARIFICATION'].includes(stage) }" role="status">
        {{ reviewStatus || message }}<span v-if="['TRANSCRIBING', 'PARSING'].includes(stage)"> · {{ stageElapsed }} 秒</span>
        <span v-if="coolingDown"> · {{ cooldownSeconds }} 秒后可重试</span>
      </p>
      <div v-if="(busy && pending) || (failed && pending) || speechFallbackAvailable" class="refined-recovery">
        <button v-if="busy && pending" type="button" @click="cancelWaiting">停止等待（不保证后端取消）</button>
        <button v-if="speechFallbackAvailable" type="button" :disabled="busy || blocked" @click="switchToText">重新编辑文字</button>
        <template v-if="failed && pending">
          <button type="button" :disabled="!retryable || coolingDown || busy || blocked" @click="runRequest">{{ recoveryLabel }}</button>
          <button v-if="rateLimited" type="button" :disabled="coolingDown || busy || blocked || submissionDisabled" @click="retryRateLimitedAsNewRequest">重新发起识别（可能产生新费用）</button>
          <button v-if="!speechFallbackAvailable" type="button" :disabled="busy || blocked" @click="switchToText">改为输入文字</button>
          <button type="button" :disabled="busy || coolingDown" @click="discardPending">放弃恢复（新请求可能重复计费）</button>
        </template>
      </div>
      <slot />
      <div v-if="!proposalReady && !automaticallySubmitted && !automaticSpeechDraft && (!compact || draft.trim())" class="refined-action-area">
        <button class="refined-preview" type="button"
          :disabled="busy || coolingDown || !!pending || blocked || captureBlocked || stage === 'RECORDING' || !draft.trim() || textLength > 200" @click="parseDraft">
          <span>{{ stage === 'PARSING' ? '正在处理…' : directExecuteInput ? '执行' : '预览指令' }}</span><ArrowRight :size="17" aria-hidden="true" />
        </button>
      </div>
    </template>
    <template v-else>
    <header>
      <strong>语音 / 文本指令</strong>
      <span :class="adapter.mode.toLowerCase()">{{ adapterLabel }}</span>
    </header>
    <textarea v-model="draft" rows="3" placeholder="录音未能明确识别时，可在此修改指令后重试" :disabled="busy || !!pending || blocked || stage === 'RECORDING'" />
    <p v-if="textLength > 200" role="alert">识别文字共 {{ textLength }} 字，请编辑至 200 字以内再解析；文字未截断。</p>
    <div class="controls">
      <button v-if="stage !== 'RECORDING'" type="button" :disabled="busy || coolingDown || !!pending || blocked || captureBlocked" @click="startRecording()">
        <Mic :size="13" />开始录音
      </button>
      <button v-else class="recording" type="button" @click="stopRecording"><Square :size="12" />停止录音</button>
      <button type="button" :disabled="busy || coolingDown || !!pending || blocked || stage === 'RECORDING' || !draft.trim() || textLength > 200" @click="parseDraft">
        <WandSparkles :size="13" />解析指令
      </button>
    </div>
    <label v-if="autoExecuteSpeech" class="audio-file-test">
      选择录音文件进行仿真链路测试
      <input type="file" accept="audio/wav,audio/mpeg,audio/mp4,audio/ogg,audio/webm,.wav,.mp3,.m4a,.mp4,.ogg,.webm" :disabled="busy || coolingDown || !!pending || blocked || captureBlocked || stage === 'RECORDING'" @change="chooseAudioFile" />
    </label>
    <p v-if="autoExecuteSpeech" class="file-test-note">文件会经过真实识别和指令解析；明确口令可能自动启动本地仿真任务。</p>
    <p class="status"><b>{{ statusLabel }}<template v-if="['RECORDING', 'TRANSCRIBING', 'PARSING'].includes(stage)"> · {{ stageElapsed }} 秒</template></b><span>{{ message }}<template v-if="coolingDown"> · {{ cooldownSeconds }} 秒后可重试</template></span></p>
    <details v-if="result" class="result-details">
      <summary>{{ result.provider === 'local-llm' ? '本地离线模型' : result.provider === 'local-rules' ? '本地规则解析' : '解析信息' }}</summary>
      <div>模型：{{ result.model }}</div>
      <div>请求编号：{{ result.requestId }}</div>
    </details>
    <button v-if="busy && pending" type="button" @click="cancelWaiting">停止等待（不保证后端取消）</button>
    <button v-if="speechFallbackAvailable" type="button" :disabled="busy || blocked" @click="switchToText">
      {{ draft.trim() ? '编辑识别文字并重新解析' : '识别失败，重新输入文字' }}
    </button>
    <div v-if="failed && pending" class="controls">
      <button type="button" :disabled="!retryable || coolingDown || busy || blocked" @click="runRequest">{{ recoveryLabel }}</button>
      <button v-if="rateLimited" type="button" :disabled="coolingDown || busy || blocked || submissionDisabled" @click="retryRateLimitedAsNewRequest">重新发起识别（可能产生新费用）</button>
      <button v-if="!speechFallbackAvailable" type="button" :disabled="busy || blocked" @click="switchToText">识别失败，改为输入文字</button>
      <button type="button" :disabled="busy || coolingDown" @click="discardPending">放弃本页恢复（新请求可能重复计费）</button>
    </div>
    <article v-if="candidate" class="candidate">
      <div><strong>{{ candidate.action === 'ALGORITHM_PRESET' ? presetLabel(candidate.parameters) : candidate.action === 'DEVICE_COMMAND' ? candidate.targetDeviceCode : candidate.action === 'SEQUENCE' ? '受控顺序指令' : candidate.action }}</strong><small>{{ candidate.action === 'DEVICE_COMMAND' ? candidate.deviceCommandType : candidate.intent }}</small></div>
      <ol v-if="candidate.action === 'SEQUENCE'" class="sequence-preview">
        <li v-for="step in candidate.steps" :key="step.index">
          {{ voiceStepLabel(step) }}
        </li>
      </ol>
      <p>{{ candidate.action === 'DEVICE_COMMAND' ? '请核对设备类型、编号和动作；修改后的文字需确认执行。' : candidate.action === 'SEQUENCE' ? '各步骤严格串行执行；任何一步失败都会停止后续步骤。' : '请核对识别文字与动作；不符合预期时修改文字后重新解析。' }}</p>
      <button type="button" :disabled="submissionDisabled || !!candidateDisabledReason" :title="candidateDisabledReason" @click="submitCandidate">
        {{ candidateDisabledReason || '生成待确认提案' }}
      </button>
    </article>
    </template>
  </section>
</template>

<style scoped>
.intelligence-input { display:grid; gap:8px; padding:10px; background:#071e23; border:1px solid rgba(108,228,213,.2); border-radius:5px; }
.intelligence-input header,.controls,.candidate div { display:flex; align-items:center; justify-content:space-between; gap:7px; }
.intelligence-input header strong { color:#eafffc; font-size:11px; }.intelligence-input header span { padding:2px 5px; border:1px solid #42615f; border-radius:8px; color:#8db0ac; font-size:8px; }
.intelligence-input header span.mock { color:#ffd58a; border-color:#725b2e; }
textarea { box-sizing:border-box; width:100%; resize:vertical; padding:8px; color:#d9f1ed; background:#06191e; border:1px solid #28484e; border-radius:4px; font:10px/1.5 inherit; }
textarea:focus { outline:1px solid #58bfb3; border-color:#58bfb3; }.controls button,.candidate button { display:inline-flex; align-items:center; justify-content:center; gap:4px; padding:6px 8px; color:#aedad4; cursor:pointer; background:#0a282e; border:1px solid #285159; border-radius:4px; font-size:9px; }
.controls button { flex:1; }.controls button.recording { color:#ffb3aa; border-color:#8b453f; }.controls button:disabled,.candidate button:disabled { cursor:not-allowed; opacity:.35; }
.audio-file-test { display:grid; gap:4px; padding:6px 8px; border:1px dashed #42615f; border-radius:4px; color:#aedad4; font-size:9px; }.audio-file-test input { max-width:100%; color:#86aca7; font:inherit; font-size:9px; }
.file-test-note { margin:0; color:#b9966b; font-size:9px; line-height:1.4; }
.sequence-preview { display:grid; gap:3px; margin:0; padding-left:20px; color:#bfe7e1; font-size:9px; }
.status { display:grid; gap:2px; margin:0; color:#709792; line-height:1.4; }.status b { color:#93c4be; font-size:9px; }.status span { font-size:9px; }
.result-details { color:#86aca7; font-size:9px; line-height:1.5; overflow-wrap:anywhere; }.result-details summary { cursor:pointer; color:#aedad4; }
.candidate { display:grid; gap:6px; padding:8px; background:#082329; border:1px solid #2a7052; border-radius:4px; }.candidate strong { color:#78eadb; }.candidate small { color:#709792; }.candidate p { margin:0; color:#86aca7; font-size:9px; line-height:1.4; }.candidate button { color:#04191b; background:#6ce4d5; border-color:#6ce4d5; }
.intelligence-input.is-refined { display:flex; flex-direction:column; gap:21px; min-width:0; padding:0; border:0; border-radius:0; color:#ebf5f4; background:transparent; }
.refined-provider { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:5px 8px; font-size:11px; color:#8fa8ac; }
.refined-provider > span { display:flex; align-items:center; gap:7px; color:#bccecf; }.refined-provider svg { color:#73e2cd; }.refined-provider small { font-size:10px; }
.refined-capture { position:relative; padding:3px 0 0; text-align:center; }
.refined-mic-orbit { position:relative; display:grid; place-items:center; width:124px; height:124px; margin:0 auto 12px; border:1px solid #2c514d; border-radius:50%; background:radial-gradient(circle,#71e3cd10,transparent 70%); }
.refined-mic-orbit::before { position:absolute; content:""; inset:10px; border:1px solid #31544e; border-radius:50%; pointer-events:none; }
.refined-mic { position:relative; display:grid; place-items:center; width:76px; height:76px; padding:0; border:1px solid #a3ecd9; border-radius:50%; background:#73e2cd; color:#06291f; box-shadow:0 0 24px #73e2cd14; cursor:pointer; transition:transform .2s,background .2s; }
.refined-mic:hover:not(:disabled) { transform:scale(1.045); background:#91edd8; }.refined-mic:disabled { opacity:.45; cursor:not-allowed; }
.refined-capture-title { display:block; margin:0; color:#eff8f5; font-size:15px; font-weight:500; }.refined-capture-note { margin:5px 0 0; color:#8fa8ac; font-size:11px; line-height:1.6; }
.refined-audio-level { grid-column:1 / -1; display:grid; gap:4px; width:100%; margin-top:8px; color:#8fa8ac; font-size:11px; }
.refined-audio-level small { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.refined-audio-level meter { width:100%; height:8px; accent-color:#73e2cd; }
.microphone-settings { margin:8px 0; color:#8fa8ac; font-size:11px; }
.microphone-settings summary { cursor:pointer; }
.microphone-settings label { display:block; margin-top:8px; }
.microphone-settings select { display:block; width:100%; margin-top:4px; background:#10282e; color:#dcece8; border:1px solid #315359; border-radius:5px; padding:5px; }
.microphone-settings .microphone-processing { display:flex; align-items:center; gap:5px; }
.microphone-test-actions { display:flex; gap:6px; margin-top:8px; }
.microphone-test-actions button { background:#10282e; color:#dcece8; border:1px solid #315359; border-radius:5px; padding:5px; cursor:pointer; }
.microphone-test-actions button:disabled { opacity:.5; cursor:default; }
.microphone-settings audio { width:100%; height:32px; margin-top:8px; }
.recording .refined-mic-orbit { border-color:#73e2cd; }.recording .refined-mic { background:#d4f4e9; }
.refined-section-label { display:block; margin:0; color:#d8e7e5; font-size:12px; }.refined-section-label > span { display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:9px; }.refined-section-label small { color:#8fa8ac; font-size:10px; }
.refined-transcript-box { border:1px solid #345158; border-radius:10px; background:#08181e; transition:border-color .2s; }.refined-transcript-box:focus-within { border-color:#73e2cd; }
.refined-transcript-box textarea { display:block; width:100%; min-height:99px; max-height:180px; padding:13px 14px 6px; border:0; border-radius:10px; outline:none; resize:vertical; color:#e5f1ee; background:transparent; font-family:inherit; font-size:14px; line-height:1.7; }.refined-transcript-box textarea::placeholder { color:#78959c; }.refined-transcript-box textarea:disabled { opacity:.55; }
.refined-text-footer { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:5px; padding:4px 12px 10px; color:#8eaab1; font-size:10px; }.refined-text-footer span { display:flex; align-items:center; gap:5px; }
.refined-examples { display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }.refined-examples button { padding:5px 8px; color:#a7bec1; background:#142a30; border:1px solid #2c444b; border-radius:6px; font-size:10px; line-height:1.5; text-align:left; cursor:pointer; }.refined-examples button:hover:not(:disabled) { color:#73e2cd; border-color:#518075; }
.refined-status { margin:0; color:#8fa8ac; font-size:11px; line-height:1.65; overflow-wrap:anywhere; }.refined-error { margin:8px 0 0; color:#ffaaa2; font-size:11px; line-height:1.65; }
.refined-recovery { display:flex; flex-wrap:wrap; gap:7px; }.refined-recovery button { padding:6px 8px; border:1px solid #365159; border-radius:6px; color:#b4cacb; background:#12282e; font-size:10px; cursor:pointer; }
.refined-action-area { margin-top:auto; }.refined-preview { display:flex; width:100%; min-height:43px; align-items:center; justify-content:center; gap:8px; padding:10px; border:1px solid #73e2cd; border-radius:8px; background:#73e2cd; color:#082b24; font-size:13px; font-weight:600; cursor:pointer; }.refined-preview:hover:not(:disabled) { background:#96eddc; }
.refined-preview:disabled,.refined-examples button:disabled,.refined-recovery button:disabled { cursor:not-allowed; opacity:.45; }
.is-refined button:focus-visible { outline:2px solid #73e2cd; outline-offset:4px; }
.intelligence-input.is-refined.is-compact { gap:11px; }
.is-compact .refined-provider { gap:3px 6px; font-size:10px; line-height:1.5; }
.is-compact .refined-provider > span { gap:5px; }
.is-compact .refined-provider small { font-size:9px; }
.is-compact .refined-capture { display:grid; grid-template-columns:38px minmax(0,1fr); align-items:start; gap:8px; padding:0; text-align:left; }
.is-compact .refined-mic-orbit { width:38px; height:38px; margin:0; border:0; background:none; }
.is-compact .refined-mic-orbit::before { content:none; }
.is-compact .refined-mic { width:38px; height:38px; border-radius:10px; box-shadow:none; }
.is-compact .refined-capture-copy { min-width:0; padding-top:1px; }
.is-compact .refined-capture-title { font-size:12px; line-height:1.5; overflow-wrap:anywhere; }
.is-compact .refined-capture-note { margin-top:3px; font-size:10px; line-height:1.6; overflow-wrap:anywhere; }
.is-compact .refined-section-label { font-size:11px; }
.is-compact .refined-section-label > span { flex-wrap:wrap; gap:3px 7px; margin-bottom:6px; }
.is-compact .refined-section-label small { font-size:9px; }
.is-compact .refined-transcript-box { border-radius:7px; }
.is-compact .refined-transcript-box textarea { height:82px; min-height:76px; padding:9px 10px 4px; border-radius:7px; font-size:12px; line-height:1.6; }
.is-compact .refined-text-footer { gap:4px; padding:3px 9px 7px; font-size:9px; }
.is-compact .refined-examples { gap:5px; margin-top:7px; }
.is-compact .refined-examples button { min-width:0; max-width:100%; padding:4px 6px; font-size:9px; overflow-wrap:anywhere; }
.is-compact .refined-status,.is-compact .refined-error { font-size:10px; line-height:1.6; }
.is-compact .refined-recovery { gap:5px; }
.is-compact .refined-recovery button { min-width:0; max-width:100%; text-align:left; overflow-wrap:anywhere; }
.is-compact .refined-action-area { margin-top:0; }
.is-compact .refined-preview { min-height:36px; padding:8px; font-size:12px; border-radius:7px; }
@media (prefers-reduced-motion:reduce) { .refined-mic { transition:none; } }
</style>

<script setup lang="ts">
import { computed, onBeforeUnmount, onDeactivated, onMounted, ref, shallowRef, watch } from 'vue'
import { Mic, Square, WandSparkles } from '@lucide/vue'
import { createVoiceIntelligenceAdapter } from '@/services/voiceIntelligence'
import { voiceRecoveryInfo } from '@/services/voiceIntelligenceRecovery'
import { VOICE_AUDIO_MAX_BYTES } from '@/api/voiceIntelligence'
import type { VoiceIntent } from '@/types/voiceControl'
import type {
  VoiceAction,
} from '@/types/voiceControl'
import type {
  VoiceAudioInput, VoiceParseRequest, VoiceInputStage, VoiceIntelligenceAdapter,
  VoiceInterpretationRuntimeContext, VoiceParseResult,
} from '@/types/voiceIntelligence'

const props = withDefaults(defineProps<{
  adapter?: VoiceIntelligenceAdapter
  allowedActions: VoiceAction[]
  deviceCodes: string[]
  runtimeContext?: VoiceInterpretationRuntimeContext | null
  operatorScope?: string
  inputDisabled?: boolean
  autoExecuteSpeech?: boolean
  allowMockSubmission?: boolean
  submissionDisabled?: boolean
  actionDisabledReason?: (action: VoiceAction) => string
}>(), {
  adapter: undefined, runtimeContext: null, operatorScope: '', inputDisabled: false,
  autoExecuteSpeech: false, allowMockSubmission: false, submissionDisabled: false, actionDisabledReason: undefined,
})
const emit = defineEmits<{
  candidate: [intent: VoiceIntent, interpretationId?: string]
  voiceCandidate: [intent: VoiceIntent, interpretationId: string]
}>()
const adapter = props.adapter ?? createVoiceIntelligenceAdapter()
const draft = ref('')
const stage = ref<VoiceInputStage>('IDLE')
const result = ref<VoiceParseResult | null>(null)
const message = ref('输入文字或录音，识别结果可编辑后再解析。')
const stageElapsed = ref(0)
const stageStartedAt = ref(performance.now())
let elapsedTimer: number | undefined
type Pending = { kind: 'audio'; input: VoiceAudioInput } | { kind: 'text'; input: VoiceParseRequest }
// Keep the exact original body and key in memory for explicit recovery.
const pending = shallowRef<Pending | null>(null)
const failed = ref(false)
const retryable = ref(false)
const speechFallbackAvailable = ref(false)
const coolingDown = ref(false)
const accessDenied = ref(false)
const permissionPending = ref(false)
let cooldownTimer: number | undefined
let recordingTimer: number | undefined
let stream: MediaStream | null = null
let recorder: MediaRecorder | null = null
let controller: AbortController | null = null
let epoch = 0
const recordingMaxMs = 60_000

const textLength = computed(() => [...draft.value].length)
const busy = computed(() => permissionPending.value || ['TRANSCRIBING', 'PARSING'].includes(stage.value))
const blocked = computed(() => props.inputDisabled || accessDenied.value)
const candidate = computed(() => result.value?.status === 'CANDIDATE' ? result.value : null)
const candidateDisabledReason = computed(() => {
  if (blocked.value) return '当前账号没有语音控制权限'
  if (adapter.mode === 'MOCK' && !props.allowMockSubmission) return '本地解析 MOCK 无后端来源记录，仅可与 P0 MOCK 演示'
  return candidate.value && props.actionDisabledReason ? props.actionDisabledReason(candidate.value.action) : ''
})
const statusLabel = computed(() => ({
  IDLE: '等待输入', RECORDING: '正在录音', TRANSCRIBING: '正在识别', READY_TO_PARSE: '待解析',
  PARSING: '正在解析', CANDIDATE: '候选指令', NEEDS_CLARIFICATION: '需要澄清',
  UNSUPPORTED: '暂不支持', ERROR: '处理失败',
}[stage.value]))
const exactFastPath = computed(() => result.value?.provider === 'local-rules'
  && result.value.model === 'rules-v1'
  && ['开始任务', '暂停任务', '继续任务', '停止任务'].includes(result.value.normalizedText.replace(/[。！？!?，,]+$/, '')))
const adapterLabel = computed(() => adapter.mode === 'MOCK' ? '本地解析 MOCK'
  : result.value?.provider === 'test-fixture' ? '后端测试适配器' : adapter.name === 'unconfigured' ? '待配置' : '平台接口')

watch(draft, () => {
  result.value = null
  if (!busy.value && !pending.value && stage.value !== 'RECORDING') {
    stage.value = draft.value.trim() ? 'READY_TO_PARSE' : 'IDLE'
  }
}, { flush: 'sync' })

function stopCapture() {
  window.clearTimeout(recordingTimer)
  const previous = recorder
  recorder = null
  if (previous?.state === 'recording') previous.stop()
  stream?.getTracks().forEach(track => track.stop())
  stream = null
}
function resetInput() {
  epoch++
  controller?.abort()
  controller = null
  stopCapture()
  permissionPending.value = false
  window.clearTimeout(cooldownTimer)
  coolingDown.value = false
  pending.value = null
  failed.value = false
  speechFallbackAvailable.value = false
  result.value = null
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

async function runRequest() {
  if (!pending.value || blocked.value || busy.value || coolingDown.value) return
  const current = pending.value
  const ticket = epoch
  const active = new AbortController()
  controller = active
  failed.value = false
  result.value = null
  stage.value = current.kind === 'audio' ? 'TRANSCRIBING' : 'PARSING'
  message.value = current.kind === 'audio'
    ? '本地模型正在识别录音，可能需要数十秒，请等待；无需重复录音。'
    : '正在解析指令，请等待。'
  try {
    if (current.kind === 'audio') {
      const transcript = await adapter.transcribe({ ...current.input, signal: active.signal })
      if (ticket !== epoch || active.signal.aborted) return
      draft.value = transcript.text
      pending.value = null
      const transcriptIsLocal = transcript.provider === 'local-asr'
      if (!transcript.text.trim()) {
        speechFallbackAvailable.value = true
        stage.value = 'ERROR'
        message.value = '没有识别到有效语音，请重新录音或改为输入文字。'
        return
      }
      message.value = transcript.provider === 'test-fixture'
        ? '后端测试适配器返回的固定样例，请核对文字；未调用真实 ASR。'
        : adapter.mode === 'MOCK' ? '本地录音演示返回固定文字，不是实际语音识别。'
          : transcriptIsLocal && props.autoExecuteSpeech
            ? '语音已识别，正在由本地离线模型判断动作。'
            : '请核对识别文字后再解析。'
      if (props.autoExecuteSpeech && transcriptIsLocal && transcript.text.trim()) {
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
        pending.value = { kind: 'text', input: parseInput }
        const parsed = await adapter.parse({ ...parseInput, signal: active.signal })
        if (ticket !== epoch || active.signal.aborted) return
        pending.value = null
        result.value = parsed
        stage.value = parsed.status === 'CANDIDATE' ? 'CANDIDATE'
          : parsed.status === 'NOT_ACTIONABLE' ? 'NEEDS_CLARIFICATION' : parsed.status
        if (parsed.status === 'CANDIDATE'
          && (parsed.provider === 'local-llm' || exactFastPath.value)
          && !props.submissionDisabled
          && !candidateDisabledReason.value) {
          result.value = null
          message.value = exactFastPath.value
            ? '明确口令已由本地规则快速确认，正在自动提交仿真动作。'
            : '识别成功，正在自动提交仿真动作并等待回执。'
          emit('voiceCandidate', parsed.intent, parsed.requestId)
        } else if (parsed.status === 'CANDIDATE') {
          speechFallbackAvailable.value = true
          message.value = parsed.provider === 'local-llm' || exactFastPath.value
            ? candidateDisabledReason.value || '当前仿真条件未通过自动执行检查，请核对识别文字。'
            : '解析来源不是本地离线模型；已保留识别文字，请核对后重新解析。'
        } else {
          speechFallbackAvailable.value = true
          message.value = parsed.message
        }
        return
      }
      stage.value = 'READY_TO_PARSE'
    } else {
      const parsed = await adapter.parse({ ...current.input, signal: active.signal })
      if (ticket !== epoch || active.signal.aborted) return
      result.value = parsed
      stage.value = parsed.status === 'NOT_ACTIONABLE' ? 'NEEDS_CLARIFICATION' : parsed.status
      message.value = parsed.status === 'CANDIDATE'
        ? '已生成候选；请核对识别文字与动作后提交。' : parsed.message
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
      speechFallbackAvailable.value = props.autoExecuteSpeech && current.kind === 'audio'
      retryable.value = info.retryable
      coolingDown.value = info.retryAfter > 0
      window.clearTimeout(cooldownTimer)
      if (coolingDown.value) cooldownTimer = window.setTimeout(() => { coolingDown.value = false }, info.retryAfter * 1000)
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
function discardPending() {
  resetInput()
  message.value = '已清除本页请求；后续操作将作为新请求，可能产生新的调用费用。'
}
function switchToText() {
  epoch++
  controller?.abort()
  controller = null
  pending.value = null
  failed.value = false
  speechFallbackAvailable.value = false
  retryable.value = false
  stage.value = draft.value.trim() ? 'READY_TO_PARSE' : 'IDLE'
  message.value = '请在上方输入任务指令，核对后再解析。'
}

function preferredAudioType() {
  return ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4', 'audio/webm']
    .find(type => MediaRecorder.isTypeSupported(type)) ?? ''
}
async function startRecording() {
  if (blocked.value || busy.value || pending.value || recorder) return
  const ticket = epoch
  speechFallbackAvailable.value = false
  permissionPending.value = true
  result.value = null
  try {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      throw new Error('当前浏览器不支持录音，请使用 HTTPS / localhost 或改用文字。')
    }
    const acquired = await navigator.mediaDevices.getUserMedia({ audio: true })
    if (ticket !== epoch) { acquired.getTracks().forEach(track => track.stop()); return }
    stream = acquired
    const mimeType = preferredAudioType()
    const active = mimeType ? new MediaRecorder(acquired, { mimeType }) : new MediaRecorder(acquired)
    recorder = active
    const chunks: Blob[] = []
    let size = 0
    active.addEventListener('dataavailable', event => {
      if (ticket !== epoch) return
      chunks.push(event.data)
      size += event.data.size
      if (size > VOICE_AUDIO_MAX_BYTES && active.state === 'recording') active.stop()
    })
    active.addEventListener('stop', () => {
      acquired.getTracks().forEach(track => track.stop())
      if (ticket !== epoch) return
      window.clearTimeout(recordingTimer)
      if (recorder === active) recorder = null
      if (stream === acquired) stream = null
      stage.value = 'IDLE'
      if (size === 0 || size > VOICE_AUDIO_MAX_BYTES) {
        stage.value = 'ERROR'
        message.value = size === 0 ? '音频为空，请重新录音。' : '音频超过 5 MiB，请缩短录音。'
        return
      }
      pending.value = { kind: 'audio', input: {
        audio: new Blob(chunks, { type: active.mimeType || 'audio/webm' }),
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
    stage.value = 'RECORDING'
    message.value = '只录任务口令，说完立即点击停止录音，可减少识别等待时间。'
    recordingTimer = window.setTimeout(() => {
      if (active.state === 'recording') active.stop()
    }, recordingMaxMs)
  } catch (error) {
    if (ticket !== epoch) return
    stopCapture()
    stage.value = 'ERROR'
    message.value = error instanceof DOMException && error.name === 'NotAllowedError'
      ? '麦克风权限被拒绝，请授权或使用文字。' : error instanceof Error ? error.message : '无法录音。'
  } finally {
    if (ticket === epoch) permissionPending.value = false
  }
}
function stopRecording() {
  if (recorder?.state === 'recording') recorder.stop()
}
async function parseDraft() {
  if (busy.value || pending.value || blocked.value || !draft.value.trim() || textLength.value > 200) return
  pending.value = { kind: 'text', input: {
    requestId: crypto.randomUUID(), text: draft.value, locale: 'zh-CN',
    allowedActions: [...props.allowedActions], availableDeviceCodes: [...props.deviceCodes],
    runtimeContext: props.runtimeContext ? { ...props.runtimeContext } : null,
  } }
  await runRequest()
}
function submitCandidate() {
  if (!candidate.value || props.submissionDisabled || candidateDisabledReason.value) return
  if (adapter.mode === 'MOCK') emit('candidate', candidate.value.intent)
  else emit('candidate', candidate.value.intent, candidate.value.requestId)
  result.value = null
}
onBeforeUnmount(resetInput)
onDeactivated(resetInput)
onMounted(() => {
  elapsedTimer = window.setInterval(() => {
    if (['RECORDING', 'TRANSCRIBING', 'PARSING'].includes(stage.value)) {
      stageElapsed.value = Math.floor((performance.now() - stageStartedAt.value) / 1000)
    }
  }, 250)
})
onBeforeUnmount(() => window.clearInterval(elapsedTimer))
</script>

<template>
  <section class="intelligence-input" aria-label="语音与文本指令输入">
    <header>
      <strong>语音 / 文本指令</strong>
      <span :class="adapter.mode.toLowerCase()">{{ adapterLabel }}</span>
    </header>
    <textarea v-model="draft" rows="3" placeholder="录音未能明确识别时，可在此修改指令后重试" :disabled="busy || !!pending || blocked || stage === 'RECORDING'" />
    <p v-if="textLength > 200" role="alert">识别文字共 {{ textLength }} 字，请编辑至 200 字以内再解析；文字未截断。</p>
    <div class="controls">
      <button v-if="stage !== 'RECORDING'" type="button" :disabled="busy || !!pending || blocked" @click="startRecording">
        <Mic :size="13" />开始录音
      </button>
      <button v-else class="recording" type="button" @click="stopRecording"><Square :size="12" />停止录音</button>
      <button type="button" :disabled="busy || !!pending || blocked || stage === 'RECORDING' || !draft.trim() || textLength > 200" @click="parseDraft">
        <WandSparkles :size="13" />解析指令
      </button>
    </div>
    <p class="status"><b>{{ statusLabel }}<template v-if="['RECORDING', 'TRANSCRIBING', 'PARSING'].includes(stage)"> · {{ stageElapsed }} 秒</template></b><span>{{ message }}</span></p>
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
      <button type="button" :disabled="!retryable || coolingDown || busy || blocked" @click="runRequest">使用原请求恢复查询</button>
      <button v-if="!speechFallbackAvailable" type="button" :disabled="busy || blocked" @click="switchToText">识别失败，改为输入文字</button>
      <button type="button" :disabled="busy" @click="discardPending">放弃本页恢复（新请求可能重复计费）</button>
    </div>
    <article v-if="candidate" class="candidate">
      <div><strong>{{ candidate.action }}</strong><small>{{ candidate.intent }}</small></div>
      <p>请核对识别文字与动作；不符合预期时修改文字后重新解析。</p>
      <button type="button" :disabled="submissionDisabled || !!candidateDisabledReason" :title="candidateDisabledReason" @click="submitCandidate">
        {{ candidateDisabledReason || '生成待确认提案' }}
      </button>
    </article>
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
.status { display:grid; gap:2px; margin:0; color:#709792; line-height:1.4; }.status b { color:#93c4be; font-size:9px; }.status span { font-size:9px; }
.result-details { color:#86aca7; font-size:9px; line-height:1.5; overflow-wrap:anywhere; }.result-details summary { cursor:pointer; color:#aedad4; }
.candidate { display:grid; gap:6px; padding:8px; background:#082329; border:1px solid #2a7052; border-radius:4px; }.candidate strong { color:#78eadb; }.candidate small { color:#709792; }.candidate p { margin:0; color:#86aca7; font-size:9px; line-height:1.4; }.candidate button { color:#04191b; background:#6ce4d5; border-color:#6ce4d5; }
</style>

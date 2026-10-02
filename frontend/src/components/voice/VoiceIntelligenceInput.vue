<script setup lang="ts">
import { computed, onBeforeUnmount, onDeactivated, onMounted, ref, shallowRef, watch } from 'vue'
import { Mic, Square, WandSparkles } from '@lucide/vue'
import { createVoiceIntelligenceAdapter } from '@/services/voiceIntelligence'
import { voiceRecoveryInfo } from '@/services/voiceIntelligenceRecovery'
import { normalizeVoiceAudioType, VOICE_AUDIO_MAX_BYTES } from '@/api/voiceIntelligence'
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
  voiceCandidate: [intent: VoiceIntent, interpretationId: string, timing: { startedAt: number; asrRequestMs: number; parseMs: number }]
}>()
const adapter = props.adapter ?? createVoiceIntelligenceAdapter()
const draft = ref('')
const stage = ref<VoiceInputStage>('IDLE')
const result = ref<VoiceParseResult | null>(null)
const message = ref('输入文字或录音，识别结果可编辑后再解析。')
const stageElapsed = ref(0)
const stageStartedAt = ref(performance.now())
let audioPipelineStartedAt: number | null = null
let audioRequestElapsedMs = 0
let parseElapsedMs = 0
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
let audioContext: AudioContext | null = null
let audioMeterTimer: number | undefined
let controller: AbortController | null = null
let epoch = 0
// Voice control utterances are deliberately short. Keeping the browser open for a
// full minute after the level meter misses end-of-speech turns a one-second command
// into a very slow Whisper request and leaves subsequent attempts behind the ASR lock.
const recordingMaxMs = 12_000
const utteranceMaxMs = 6_000
const speechEndSilenceMs = 700

const textLength = computed(() => [...draft.value].length)
const busy = computed(() => permissionPending.value || ['TRANSCRIBING', 'PARSING'].includes(stage.value))
const blocked = computed(() => props.inputDisabled || accessDenied.value)
const candidate = computed(() => result.value?.status === 'CANDIDATE' ? result.value : null)
const candidateDisabledReason = computed(() => {
  if (blocked.value) return '当前账号没有语音控制权限'
  if (adapter.mode === 'MOCK' && !props.allowMockSubmission) return '本地解析 MOCK 无后端来源记录，仅可与 P0 MOCK 演示'
  if (candidate.value?.action === 'DEVICE_COMMAND') return ''
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

watch(draft, () => {
  result.value = null
  if (!busy.value && !pending.value && stage.value !== 'RECORDING') {
    stage.value = draft.value.trim() ? 'READY_TO_PARSE' : 'IDLE'
  }
}, { flush: 'sync' })

function stopCapture() {
  window.clearTimeout(recordingTimer)
  window.clearInterval(audioMeterTimer)
  audioMeterTimer = undefined
  if (audioContext) void audioContext.close().catch(() => {})
  audioContext = null
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
    ? '正在识别录音，可能需要数十秒，请等待；无需重复录音。'
    : '正在解析指令，请等待。'
  try {
    if (current.kind === 'audio') {
      const asrStartedAt = performance.now()
      const transcript = await adapter.transcribe({ ...current.input, signal: active.signal })
      audioRequestElapsedMs += Math.round(performance.now() - asrStartedAt)
      if (ticket !== epoch || active.signal.aborted) return
      draft.value = transcript.text
      pending.value = null
      // Only real ASR providers enter the speech-to-intent flow. Test fixtures
      // and the local mock must remain transcription-only in this UI.
      const transcriptIsRealAsr = transcript.provider === 'local-asr' || transcript.provider === 'aliyun-asr'
      if (!transcript.text.trim()) {
        speechFallbackAvailable.value = true
        stage.value = 'ERROR'
        message.value = '没有识别到有效语音，请重新录音或改为输入文字。'
        return
      }
      message.value = transcript.provider === 'test-fixture'
        ? '后端测试适配器返回的固定样例，请核对文字；未调用真实 ASR。'
        : adapter.mode === 'MOCK' ? '本地录音演示返回固定文字，不是实际语音识别。'
          : transcriptIsRealAsr && props.autoExecuteSpeech
            ? '语音已识别，正在解析指令。'
            : '请核对识别文字后再解析。'
      if (props.autoExecuteSpeech && transcriptIsRealAsr && transcript.text.trim()) {
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
        const parseStartedAt = performance.now()
        const parsed = await adapter.parse({ ...parseInput, signal: active.signal })
        parseElapsedMs += Math.round(performance.now() - parseStartedAt)
        if (ticket !== epoch || active.signal.aborted) return
        pending.value = null
        result.value = parsed
        stage.value = parsed.status === 'CANDIDATE' ? 'CANDIDATE'
          : parsed.status === 'NOT_ACTIONABLE' ? 'NEEDS_CLARIFICATION' : parsed.status
        const targetedDeviceCommand = parsed.status === 'CANDIDATE'
          && parsed.action === 'DEVICE_COMMAND'
          && parsed.provider === 'local-rules'
        const safeToAutoExecute = parsed.status === 'CANDIDATE'
          && (targetedDeviceCommand
            || exactFastPath.value
            || (parsed.provider === 'local-llm' && parsed.intent !== 'MISSION_STOP'))
        if (parsed.status === 'CANDIDATE'
          && safeToAutoExecute
          && !props.submissionDisabled
          && !candidateDisabledReason.value) {
          result.value = null
          message.value = targetedDeviceCommand
            ? '单设备指令已识别，正在校验目标和动作并自动提交至本地仿真。'
            : exactFastPath.value
            ? '明确口令已由本地规则快速确认，正在自动提交仿真动作。'
            : '识别成功，正在自动提交仿真动作并等待回执。'
          emit('voiceCandidate', parsed.intent, parsed.requestId, {
            startedAt: audioPipelineStartedAt ?? Date.now(),
            asrRequestMs: audioRequestElapsedMs,
            parseMs: parseElapsedMs,
          })
        } else if (parsed.status === 'CANDIDATE') {
          speechFallbackAvailable.value = true
          message.value = parsed.intent === 'MISSION_STOP' && !exactFastPath.value
            ? '语音识别对应停止任务，但不是明确停止口令；已阻止自动执行，请核对或修改文字。'
            : parsed.provider === 'local-llm' || exactFastPath.value
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
      const parseStartedAt = performance.now()
      const parsed = await adapter.parse({ ...current.input, signal: active.signal })
      if (ticket !== epoch || active.signal.aborted) return
      result.value = parsed
      stage.value = parsed.status === 'NOT_ACTIONABLE' ? 'NEEDS_CLARIFICATION' : parsed.status
      const targetedDeviceCommand = parsed.status === 'CANDIDATE'
        && parsed.action === 'DEVICE_COMMAND'
        && parsed.provider === 'local-rules'
      const safeToAutoExecute = parsed.status === 'CANDIDATE'
        && (targetedDeviceCommand
          || exactFastPath.value
          || (parsed.provider === 'local-llm' && parsed.intent !== 'MISSION_STOP'))
      if (props.autoExecuteSpeech
        && safeToAutoExecute
        && !props.submissionDisabled
        && !candidateDisabledReason.value) {
        result.value = null
        message.value = targetedDeviceCommand
          ? '单设备文字指令已识别，正在校验目标和动作并自动提交至本地仿真。'
          : exactFastPath.value
            ? '明确文字口令已由本地规则快速确认，正在自动提交仿真动作。'
            : '文字指令解析成功，正在自动提交仿真动作并等待回执。'
        emit('voiceCandidate', parsed.intent, parsed.requestId, {
          startedAt: Date.now(),
          asrRequestMs: 0,
          parseMs: Math.round(performance.now() - parseStartedAt),
        })
      } else if (parsed.status === 'CANDIDATE') {
        message.value = parsed.intent === 'MISSION_STOP' && !exactFastPath.value
          ? '解析结果对应停止任务，但不是明确停止口令；已阻止自动执行，请核对或修改文字。'
          : '已生成候选；请核对识别文字与动作后提交。'
      } else {
        message.value = parsed.message
      }
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
      // A definitive empty transcript has no unknown execution to recover.
      // Release the finished recording so recording and text editing remain available.
      if (current.kind === 'audio' && info.code === 'VOICE_NO_SPEECH') pending.value = null
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
    const acquired = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    if (ticket !== epoch) { acquired.getTracks().forEach(track => track.stop()); return }
    stream = acquired
    const mimeType = preferredAudioType()
    const active = mimeType ? new MediaRecorder(acquired, { mimeType }) : new MediaRecorder(acquired)
    recorder = active
    const context = new AudioContext()
    audioContext = context
    await context.resume()
    const analyser = context.createAnalyser()
    analyser.fftSize = 1024
    context.createMediaStreamSource(acquired).connect(analyser)
    const samples = new Float32Array(analyser.fftSize)
    let voiceStartedAt = 0
    let quietStartedAt = 0
    let noiseFloor = 0.002
    let loudFrames = 0
    const chunks: Blob[] = []
    let size = 0
    active.addEventListener('dataavailable', event => {
      if (ticket !== epoch) return
      chunks.push(event.data)
      size += event.data.size
      if (size > VOICE_AUDIO_MAX_BYTES && active.state === 'recording') active.stop()
    })
    active.addEventListener('stop', () => {
      window.clearInterval(audioMeterTimer)
      audioMeterTimer = undefined
      if (audioContext === context) {
        void context.close().catch(() => {})
        audioContext = null
      }
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
      audioPipelineStartedAt = Date.now()
      audioRequestElapsedMs = 0
      parseElapsedMs = 0
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
    // Start each recording with a clean transcript so an older candidate
    // cannot look like the result of the new utterance while ASR is pending.
    draft.value = ''
    result.value = null
    stage.value = 'RECORDING'
    message.value = '请说出任务口令；停顿约 0.7 秒会自动识别，单次录音最长 12 秒。'
    audioMeterTimer = window.setInterval(() => {
      if (ticket !== epoch || active.state !== 'recording') return
      analyser.getFloatTimeDomainData(samples)
      let sum = 0
      for (let index = 0; index < samples.length; index++) sum += samples[index]! * samples[index]!
      const rms = Math.sqrt(sum / samples.length)
      const now = performance.now()
      const voiceThreshold = Math.max(0.014, noiseFloor * 2.8)
      const silenceThreshold = Math.max(0.011, noiseFloor * 1.8)
      if (!voiceStartedAt && rms < voiceThreshold) {
        noiseFloor = noiseFloor * 0.94 + rms * 0.06
      }
      if (rms >= voiceThreshold) {
        loudFrames++
        if (!voiceStartedAt && loudFrames >= 2) voiceStartedAt = now
        quietStartedAt = 0
      } else {
        loudFrames = 0
        if (voiceStartedAt && rms <= silenceThreshold && !quietStartedAt) quietStartedAt = now
        if (voiceStartedAt && rms > silenceThreshold) quietStartedAt = 0
        if (voiceStartedAt && quietStartedAt && now - quietStartedAt >= speechEndSilenceMs
          && now - voiceStartedAt >= 180) {
          active.stop()
          return
        }
      }
      if (voiceStartedAt && now - voiceStartedAt >= utteranceMaxMs) active.stop()
    }, 50)
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
function chooseAudioFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || blocked.value || busy.value || pending.value) return
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
    <label v-if="autoExecuteSpeech" class="audio-file-test">
      选择录音文件进行仿真链路测试
      <input type="file" accept="audio/wav,audio/mpeg,audio/mp4,audio/ogg,audio/webm,.wav,.mp3,.m4a,.mp4,.ogg,.webm" :disabled="busy || !!pending || blocked || stage === 'RECORDING'" @change="chooseAudioFile" />
    </label>
    <p v-if="autoExecuteSpeech" class="file-test-note">文件会经过真实识别和指令解析；明确口令可能自动启动本地仿真任务。</p>
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
      <div><strong>{{ candidate.action === 'DEVICE_COMMAND' ? candidate.targetDeviceCode : candidate.action }}</strong><small>{{ candidate.action === 'DEVICE_COMMAND' ? candidate.deviceCommandType : candidate.intent }}</small></div>
      <p>{{ candidate.action === 'DEVICE_COMMAND' ? '本地仿真中，语音或文字指令通过校验后会自动提交，且仅作用于所示设备。' : '请核对识别文字与动作；不符合预期时修改文字后重新解析。' }}</p>
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
.audio-file-test { display:grid; gap:4px; padding:6px 8px; border:1px dashed #42615f; border-radius:4px; color:#aedad4; font-size:9px; }.audio-file-test input { max-width:100%; color:#86aca7; font:inherit; font-size:9px; }
.file-test-note { margin:0; color:#b9966b; font-size:9px; line-height:1.4; }
.status { display:grid; gap:2px; margin:0; color:#709792; line-height:1.4; }.status b { color:#93c4be; font-size:9px; }.status span { font-size:9px; }
.result-details { color:#86aca7; font-size:9px; line-height:1.5; overflow-wrap:anywhere; }.result-details summary { cursor:pointer; color:#aedad4; }
.candidate { display:grid; gap:6px; padding:8px; background:#082329; border:1px solid #2a7052; border-radius:4px; }.candidate strong { color:#78eadb; }.candidate small { color:#709792; }.candidate p { margin:0; color:#86aca7; font-size:9px; line-height:1.4; }.candidate button { color:#04191b; background:#6ce4d5; border-color:#6ce4d5; }
</style>

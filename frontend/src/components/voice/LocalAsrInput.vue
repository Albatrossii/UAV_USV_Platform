<script setup lang="ts">
import { computed, onBeforeUnmount, onDeactivated, ref, shallowRef, watch } from 'vue'
import { transcribeLocalAudio, normalizeVoiceAudioType, VOICE_AUDIO_MAX_BYTES, LOCAL_ASR_TIMEOUT_MS } from '@/api/voiceIntelligence'
import { voiceRecoveryInfo } from '@/services/voiceIntelligenceRecovery'
import type { VoiceAudioInput, VoiceTranscript } from '@/types/voiceIntelligence'

const props = withDefaults(defineProps<{
  operatorScope: string; disabled?: boolean
  transcribe?: (input: VoiceAudioInput) => Promise<VoiceTranscript>
}>(), { disabled: false, transcribe: undefined })
// Deliberately independent of P0, runtime context, parser and candidate events.
const text = ref('')
const message = ref('仅录音转文字，不解析意图、不执行控制。刷新不恢复音频，旧请求可能已处理。')
const result = shallowRef<VoiceTranscript | null>(null)
const pending = shallowRef<VoiceAudioInput | null>(null)
const phase = ref<'idle' | 'permission' | 'recording' | 'encoding' | 'waiting'>('idle')
const retryable = ref(false)
const cooldown = ref(false)
const denied = ref(false)
const elapsed = ref(0)
const recognitionElapsed = ref(0)
const completedRecognitionElapsedMs = ref<number | null>(null)
const recognitionStatus = computed(() => phase.value === 'waiting'
  ? `${message.value} 已等待 ${recognitionElapsed.value} 秒。` : message.value)
const locked = computed(() => props.disabled || denied.value)
const inputLocked = computed(() => locked.value || phase.value !== 'idle' || !!pending.value)
const adapterLabel = computed(() => !result.value ? '等待真实识别'
  : result.value.provider === 'local-asr' ? '本地 ASR' : '测试/非本地适配器（不计入验收）')
let epoch = 0
let activeRequest: AbortController | null = null
let recorder: MediaRecorder | null = null
let stream: MediaStream | null = null
let started = 0
let submitted = 0
let recordTimer: number | undefined
let expireTimer: number | undefined
let cooldownTimer: number | undefined
let requestTimer: number | undefined
let recognitionTimer: number | undefined
let recognitionStarted = 0
const RECOVERY_MS = 600_000

function releaseCapture() {
  window.clearInterval(recordTimer)
  const previous = recorder
  recorder = null
  if (previous?.state === 'recording') previous.stop()
  stream?.getTracks().forEach(track => track.stop())
  stream = null
}
function clear() {
  epoch++
  activeRequest?.abort()
  activeRequest = null
  releaseCapture()
  window.clearTimeout(expireTimer)
  window.clearTimeout(cooldownTimer)
  window.clearTimeout(requestTimer)
  window.clearInterval(recognitionTimer)
  pending.value = null
  text.value = ''
  result.value = null
  phase.value = 'idle'
  retryable.value = false
  cooldown.value = false
  elapsed.value = 0
  recognitionElapsed.value = 0
  completedRecognitionElapsedMs.value = null
  submitted = 0
}
function discard() {
  clear()
  message.value = '已清除本页内容；后台可能仍在处理。新录音是独立请求，请勿用来重复恢复旧请求。'
}
watch(() => [props.operatorScope, props.disabled], () => {
  clear()
  denied.value = false
  message.value = '账号或权限已变化，已清除本页录音和文字。'
}, { flush: 'sync' })
function expire() {
  clear()
  message.value = '首次提交已超过10分钟，恢复材料已清除。请联系联调人员核对；不会自动重新提交。'
}
async function send() {
  if (!pending.value || locked.value || phase.value !== 'idle' || cooldown.value) return
  if (performance.now() - submitted >= RECOVERY_MS) { expire(); return }
  const ticket = epoch
  const controller = new AbortController()
  activeRequest = controller
  phase.value = 'waiting'
  recognitionStarted = performance.now()
  completedRecognitionElapsedMs.value = null
  recognitionElapsed.value = 0
  window.clearInterval(recognitionTimer)
  recognitionTimer = window.setInterval(() => {
    recognitionElapsed.value = Math.floor((performance.now() - recognitionStarted) / 1000)
  }, 250)
  retryable.value = false
  message.value = '正在本地识别，最多等待140秒。停止等待不代表模型停止。'
  requestTimer = window.setTimeout(() => {
    if (activeRequest === controller) stopWaiting('等待已超过140秒，后台可能仍在处理，可手动使用原请求恢复。')
  }, LOCAL_ASR_TIMEOUT_MS)
  try {
    const response = await (props.transcribe ?? transcribeLocalAudio)({ ...pending.value, signal: controller.signal })
    if (ticket !== epoch || controller.signal.aborted) return
    result.value = response
    completedRecognitionElapsedMs.value = Math.round(performance.now() - recognitionStarted)
    text.value = response.text
    pending.value = null
    window.clearTimeout(expireTimer)
    message.value = response.provider === 'local-asr'
      ? '识别完成，请核对并编辑文字；不会执行任何任务动作。'
      : '返回来自测试或非本地适配器，不作为真实本地识别通过证据。'
  } catch (error) {
    if (ticket !== epoch || controller.signal.aborted) return
    const info = voiceRecoveryInfo(error)
    message.value = info.code === 'VOICE_TRANSCRIPT_TOO_LONG' ? '识别文字超过500个字符，请缩短录音重试。' : info.message
    if (info.forbidden) {
      clear()
      denied.value = true
    } else {
      retryable.value = info.retryable && info.code !== 'VOICE_TRANSCRIPT_TOO_LONG'
      cooldown.value = info.retryAfter > 0
      if (cooldown.value) cooldownTimer = window.setTimeout(() => { cooldown.value = false }, info.retryAfter * 1000)
    }
  } finally {
    if (activeRequest === controller) {
      window.clearTimeout(requestTimer)
      window.clearInterval(recognitionTimer)
      activeRequest = null
      phase.value = 'idle'
    }
  }
}
function stopWaiting(note = '已停止等待，后台可能仍在处理；可在首次提交10分钟内手动恢复原请求。') {
  activeRequest?.abort()
  activeRequest = null
  window.clearTimeout(requestTimer)
  window.clearInterval(recognitionTimer)
  phase.value = 'idle'
  retryable.value = true
  message.value = note
}
function submitAudio(audio: Blob) {
  if (audio.size === 0 || audio.size > VOICE_AUDIO_MAX_BYTES) {
    message.value = audio.size === 0 ? '没有有效音频，请重新录制。' : '音频超过5 MiB，请缩短后重试。'
    return
  }
  text.value = ''
  result.value = null
  pending.value = { audio, requestId: crypto.randomUUID(), locale: 'zh-CN' }
  submitted = performance.now()
  expireTimer = window.setTimeout(expire, RECOVERY_MS)
  void send()
}
function stopRecording() {
  if (recorder?.state === 'recording') {
    phase.value = 'encoding'
    window.clearInterval(recordTimer)
    recorder.stop() // Wait for the final dataavailable and stop event before uploading.
  }
}
async function startRecording() {
  if (inputLocked.value) return
  const ticket = epoch
  phase.value = 'permission'
  try {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined'
        || !MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
      throw new Error('请使用支持WebM/Opus的Chrome/Edge，在localhost或HTTPS页面录音。')
    }
    const acquired = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: { ideal: 1 }, sampleRate: { ideal: 48000 } } })
    if (epoch !== ticket) { acquired.getTracks().forEach(track => track.stop()); return }
    stream = acquired
    const capture = new MediaRecorder(acquired, { mimeType: 'audio/webm;codecs=opus', audioBitsPerSecond: 64000 })
    recorder = capture
    const chunks: Blob[] = []
    let bytes = 0
    capture.addEventListener('dataavailable', event => {
      if (ticket !== epoch) return
      bytes += event.data.size
      if (bytes <= VOICE_AUDIO_MAX_BYTES) chunks.push(event.data)
      else stopRecording()
    })
    capture.addEventListener('stop', () => {
      acquired.getTracks().forEach(track => track.stop())
      if (ticket !== epoch) return
      releaseCapture()
      phase.value = 'idle'
      if (bytes > VOICE_AUDIO_MAX_BYTES) { message.value = '音频超过5 MiB，请缩短重录。'; return }
      submitAudio(new Blob(chunks, { type: 'audio/webm;codecs=opus' }))
    }, { once: true })
    capture.addEventListener('error', () => {
      if (ticket !== epoch) return
      clear()
      message.value = '录音设备异常，请检查麦克风后重录。'
    })
    capture.start(500)
    phase.value = 'recording'
    started = performance.now()
    elapsed.value = 0
    message.value = '正在录音；最长录制约58秒，文件上限60秒。'
    recordTimer = window.setInterval(() => {
      elapsed.value = Math.floor((performance.now() - started) / 1000)
      if (performance.now() - started >= 58000) stopRecording()
    }, 200)
  } catch (error) {
    if (ticket !== epoch) return
    releaseCapture()
    phase.value = 'idle'
    if (error instanceof DOMException && error.name === 'NotAllowedError') {
      message.value = '麦克风权限被拒绝，请在浏览器中授权后再试。'
    } else if (error instanceof DOMException && error.name === 'NotFoundError') {
      message.value = '未检测到可用麦克风，请连接设备后重试。'
    } else if (error instanceof DOMException && error.name === 'NotReadableError') {
      message.value = '无法读取麦克风，请检查设备是否被其他程序占用。'
    } else {
      message.value = error instanceof Error ? error.message : '无法录音，请检查设备。'
    }
  }
}
function pickFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || inputLocked.value) return
  if (!['audio/webm', 'audio/ogg', 'audio/mp4', 'audio/wav', 'audio/mpeg'].includes(normalizeVoiceAudioType(file.type))) {
    message.value = '文件测试仅支持WebM/Opus、Ogg/Opus、MP4/AAC、WAV/PCM和MP3。'
    return
  }
  submitAudio(file)
}
onBeforeUnmount(clear)
onDeactivated(clear)
</script>

<template>
  <section class="local-asr" aria-label="本地语音识别">
    <header><strong>本地语音识别</strong><span>{{ adapterLabel }}</span></header>
    <p>只转文字 · 不需要生成场景 · 不执行控制</p>
    <div class="asr-actions">
      <button v-if="phase === 'recording'" @click="stopRecording">停止录音（{{ elapsed }}秒）</button>
      <button v-else :disabled="inputLocked" @click="startRecording">{{ phase === 'permission' ? '等待麦克风授权' : '开始录音' }}</button>
      <label>音频文件测试<input aria-label="音频文件测试" type="file" accept="audio/webm,audio/ogg,audio/mp4,audio/wav,audio/mpeg,.webm,.ogg,.m4a,.mp4,.wav,.mp3" :disabled="inputLocked" @change="pickFile" /></label>
    </div>
    <small>Chrome/Edge · 最长录制约58秒，文件上限60秒／5 MiB</small>
    <p role="status" aria-live="polite">{{ recognitionStatus }}</p>
    <p v-if="locked" role="alert">当前账号不可识别，请确认登录及ADMIN权限。</p>
    <textarea v-model="text" aria-label="识别文字" rows="6" placeholder="真实识别结果将在这里显示，可人工编辑。" :disabled="locked || phase !== 'idle' || !!pending" />
    <small>{{ [...text].length }} 字符（不会按旧意图解析200字上限截断）</small>
    <p v-if="result" class="metadata">识别耗时 {{ ((completedRecognitionElapsedMs ?? 0) / 1000).toFixed(1) }} 秒 · 音频 {{ result.durationMs }} ms · {{ result.provider }} · {{ result.model }}</p>
    <button v-if="phase === 'waiting'" @click="stopWaiting()">停止等待（不保证后台取消）</button>
    <div v-if="pending && phase === 'idle'" class="asr-actions">
      <button :disabled="!retryable || cooldown || locked" @click="send">{{ cooldown ? '请等待重试冷却' : '使用原请求恢复' }}</button>
      <button @click="discard">放弃恢复并清除</button>
    </div>
    <button v-if="!pending && phase === 'idle' && text" @click="discard">清除文字</button>
  </section>
</template>

<style scoped>
.local-asr { display:grid; gap:12px; padding:18px; border:1px solid #285159; border-radius:8px; background:#071e23; color:#cee8e3; }
header,.asr-actions { display:flex; gap:10px; align-items:center; flex-wrap:wrap; } header { justify-content:space-between; } header span,small,.metadata { color:#8db7b0; font-size:12px; } p { margin:0; line-height:1.6; }
button,label { font:inherit; font-size:12px; } button { padding:9px 12px; background:#10373b; border:1px solid #34756f; border-radius:5px; color:#bceee5; cursor:pointer; } button:disabled { opacity:.45; cursor:not-allowed; } label { display:grid; gap:5px; } input { max-width:200px; }
textarea { width:100%; box-sizing:border-box; padding:12px; background:#06191e; color:#e6faf5; border:1px solid #285159; border-radius:5px; font:inherit; resize:vertical; } textarea:focus { outline:1px solid #6ce4d5; } .metadata { overflow-wrap:anywhere; }
</style>

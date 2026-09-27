import { fetchCsrfToken } from './auth'
import { ApiClientError, http } from './http'
import type { ApiResponse } from '@/types/api'
import type {
  VoiceAudioInput,
  VoiceIntentCandidate,
  VoiceIntentNotReady,
  VoiceParseRequest,
  VoiceParseResult,
  VoiceTranscript,
} from '@/types/voiceIntelligence'
import { VoiceIntelligenceError } from '@/types/voiceIntelligence'

export const VOICE_AUDIO_MAX_BYTES = 5 * 1024 * 1024
// Java permits up to 120 seconds for CPU ASR. Keep the browser alive through
// that deadline, with time for upload and response delivery on the local host.
export const VOICE_TRANSCRIPTION_TIMEOUT_MS = 140_000
export const LOCAL_ASR_TIMEOUT_MS = VOICE_TRANSCRIPTION_TIMEOUT_MS
// Allow the local CPU model's 60-second budget plus validation and delivery.
export const VOICE_PARSE_TIMEOUT_MS = 75_000
const supportedAudioTypes = new Set(['audio/webm', 'audio/ogg', 'audio/mp4', 'audio/wav', 'audio/mpeg'])
const actions = new Set(['START', 'PAUSE', 'RESUME', 'STOP'])
const intents = new Set(['MISSION_START', 'MISSION_PAUSE', 'MISSION_RESUME', 'MISSION_STOP'])
const statuses = new Set(['NEEDS_CLARIFICATION', 'UNSUPPORTED', 'NOT_ACTIONABLE'])
const reasons = new Set([
  'AMBIGUOUS_ACTION', 'NEGATED_ACTION', 'NO_SUPPORTED_ACTION',
  'UNSUPPORTED_CAPABILITY', 'UNSUPPORTED_TARGETING',
])
const intentForAction: Record<string, string> = {
  START: 'MISSION_START', PAUSE: 'MISSION_PAUSE', RESUME: 'MISSION_RESUME', STOP: 'MISSION_STOP',
}
const reasonsForStatus: Record<string, Set<string>> = {
  NEEDS_CLARIFICATION: new Set(['AMBIGUOUS_ACTION', 'NO_SUPPORTED_ACTION']),
  UNSUPPORTED: new Set(['UNSUPPORTED_CAPABILITY', 'UNSUPPORTED_TARGETING']),
  NOT_ACTIONABLE: new Set(['NEGATED_ACTION']),
}
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

function hasOnlyKeys(value: object, expected: string[]) {
  const actual = Object.keys(value).sort()
  return actual.length === expected.length && actual.every((key, index) => key === [...expected].sort()[index])
}

function boundedString(value: unknown, min: number, max: number): value is string {
  return typeof value === 'string' && [...value].length >= min && [...value].length <= max
}

function audioType(blob: Blob) {
  return blob.type.toLowerCase().split(';', 1)[0] ?? ''
}

function assertRequestActive(signal?: AbortSignal) {
  if (signal?.aborted) throw new VoiceIntelligenceError('请求已取消。', 'VOICE_REQUEST_CANCELLED')
}

function commonResult(value: unknown): value is {
  requestId: string; normalizedText: string; provider: string; model: string
} {
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return typeof item.requestId === 'string' && uuidPattern.test(item.requestId)
    && boundedString(item.normalizedText, 1, 200)
    && boundedString(item.provider, 1, 64)
    && boundedString(item.model, 1, 96)
}

function isTranscript(value: unknown): value is VoiceTranscript {
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return hasOnlyKeys(item, ['requestId', 'text', 'locale', 'durationMs', 'provider', 'model'])
    && typeof item.requestId === 'string' && uuidPattern.test(item.requestId)
    && boundedString(item.text, 1, 500) && item.locale === 'zh-CN'
    && Number.isInteger(item.durationMs) && (item.durationMs as number) >= 1 && (item.durationMs as number) <= 60000
    && boundedString(item.provider, 1, 64) && boundedString(item.model, 1, 96)
}

function isCandidate(value: unknown): value is VoiceIntentCandidate {
  if (!commonResult(value)) return false
  const item = value as unknown as Record<string, unknown>
  return hasOnlyKeys(item, [
    'status', 'requestId', 'intent', 'action', 'normalizedText', 'confidence', 'provider', 'model',
  ]) && item.status === 'CANDIDATE' && typeof item.action === 'string' && actions.has(item.action)
    && typeof item.intent === 'string' && intents.has(item.intent)
    && intentForAction[item.action] === item.intent
    && (item.confidence === null || (typeof item.confidence === 'number' && item.confidence >= 0 && item.confidence <= 1))
}

function isNotReady(value: unknown): value is VoiceIntentNotReady {
  if (!commonResult(value)) return false
  const item = value as unknown as Record<string, unknown>
  return hasOnlyKeys(item, ['status', 'requestId', 'reason', 'message', 'normalizedText', 'provider', 'model'])
    && typeof item.status === 'string' && statuses.has(item.status)
    && typeof item.reason === 'string' && reasons.has(item.reason)
    && reasonsForStatus[item.status]?.has(item.reason) === true
    && boundedString(item.message, 1, 256)
}

function mapRequestError(error: unknown, operation: 'transcription' | 'parse'): never {
  if (error instanceof VoiceIntelligenceError) throw error
  if (error instanceof ApiClientError) {
    if (['ERR_CANCELED', 'ABORT_ERR'].includes(error.code ?? '')) {
      throw new VoiceIntelligenceError('请求已取消。', 'VOICE_REQUEST_CANCELLED')
    }
    if (['ECONNABORTED', 'ETIMEDOUT'].includes(error.code ?? '')) {
      throw new VoiceIntelligenceError(
        '浏览器等待超时，后端可能仍在处理；请使用原请求恢复查询。',
        operation === 'transcription' ? 'VOICE_TRANSCRIPTION_TIMEOUT' : 'VOICE_PARSE_TIMEOUT',
      )
    }
    throw error
  }
  throw new VoiceIntelligenceError('语音智能服务暂不可用。', 'VOICE_PROVIDER_UNAVAILABLE')
}

async function writeHeaders(requestId: string) {
  const csrf = await fetchCsrfToken()
  return {
    [csrf.headerName]: csrf.token,
    'Idempotency-Key': requestId,
    'X-Request-ID': requestId,
  }
}

export async function transcribeVoiceAudio(input: VoiceAudioInput, timeoutMs = VOICE_TRANSCRIPTION_TIMEOUT_MS): Promise<VoiceTranscript> {
  assertRequestActive(input.signal)
  if (!uuidPattern.test(input.requestId) || input.locale !== 'zh-CN') {
    throw new VoiceIntelligenceError('语音识别请求标识或区域无效。', 'VOICE_INVALID_REQUEST')
  }
  if (input.audio.size === 0) throw new VoiceIntelligenceError('没有录制到有效音频。', 'VOICE_AUDIO_EMPTY')
  if (input.audio.size > VOICE_AUDIO_MAX_BYTES) {
    throw new VoiceIntelligenceError('录音超过 5 MiB 限制，请缩短后重试。', 'VOICE_AUDIO_TOO_LARGE')
  }
  const contentType = audioType(input.audio)
  if (!supportedAudioTypes.has(contentType)) {
    throw new VoiceIntelligenceError(`暂不支持音频格式 ${contentType || 'unknown'}。`, 'VOICE_AUDIO_FORMAT_UNSUPPORTED')
  }
  try {
    const headers = await writeHeaders(input.requestId)
    assertRequestActive(input.signal)
    const form = new FormData()
    form.append('requestId', input.requestId)
    form.append('locale', input.locale)
    form.append('audio', input.audio, `voice-command.${contentType.split('/')[1]}`)
    const response = await http.post<ApiResponse<VoiceTranscript>>('/voice/intelligence/transcriptions', form, {
      headers, signal: input.signal, timeout: timeoutMs,
    })
    if (timeoutMs === LOCAL_ASR_TIMEOUT_MS && response.data.code !== 'SUCCESS') {
      throw new VoiceIntelligenceError('本地识别未返回成功响应。', 'VOICE_MALFORMED_RESPONSE')
    }
    if (!isTranscript(response.data.data) || response.data.data.requestId !== input.requestId) {
      throw new VoiceIntelligenceError('语音识别响应格式或请求标识无效。', 'VOICE_MALFORMED_RESPONSE')
    }
    return response.data.data
  } catch (error) {
    return mapRequestError(error, 'transcription')
  }
}

/** D1 uses the Java endpoint, never a mock fallback or direct Python call. */
export function transcribeLocalAudio(input: VoiceAudioInput) {
  if (!['audio/webm', 'audio/mpeg'].includes(audioType(input.audio))) {
    return Promise.reject(new VoiceIntelligenceError('D1仅支持WebM/Opus和MP3。', 'VOICE_AUDIO_FORMAT_UNSUPPORTED'))
  }
  return transcribeVoiceAudio(input, LOCAL_ASR_TIMEOUT_MS)
}

export async function interpretVoiceText(input: VoiceParseRequest): Promise<VoiceParseResult> {
  assertRequestActive(input.signal)
  const text = input.text
  if (!uuidPattern.test(input.requestId) || input.locale !== 'zh-CN' || !text.trim()) {
    throw new VoiceIntelligenceError('意图解析请求标识、区域或文本无效。', 'VOICE_INVALID_REQUEST')
  }
  if ([...text].length > 200) throw new VoiceIntelligenceError('指令文字不能超过 200 个字符，请编辑后再解析。', 'VOICE_TEXT_TOO_LONG')
  try {
    const headers = await writeHeaders(input.requestId)
    assertRequestActive(input.signal)
    const body = {
      requestId: input.requestId,
      text,
      locale: input.locale,
      allowedActions: input.allowedActions,
      availableDeviceCodes: input.availableDeviceCodes,
      runtimeContext: input.runtimeContext,
    }
    const response = await http.post<ApiResponse<VoiceParseResult>>('/voice/intelligence/interpretations', body, {
      headers, signal: input.signal, timeout: VOICE_PARSE_TIMEOUT_MS,
    })
    const result = response.data.data
    if ((!isCandidate(result) && !isNotReady(result)) || result.requestId !== input.requestId) {
      throw new VoiceIntelligenceError('意图解析响应格式或请求标识无效。', 'VOICE_MALFORMED_RESPONSE')
    }
    return result
  } catch (error) {
    return mapRequestError(error, 'parse')
  }
}

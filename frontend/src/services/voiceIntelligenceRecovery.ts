import { ApiClientError } from '@/api/http'

const messages: Record<string, string> = {
  VOICE_REQUEST_IN_PROGRESS: '原请求正在处理，请稍后使用原请求恢复查询。',
  VOICE_REQUEST_OUTCOME_UNKNOWN: '后端无法确认上游结果，请保留原请求编号并联系联调人员核对；不要换键重发。',
  VOICE_REQUEST_EXPIRED: '原请求结果已过期，请人工核对后再决定是否发起新请求。',
  IDEMPOTENCY_CONFLICT: '同一请求编号对应了不同内容，已阻止重试。',
  VOICE_CONTEXT_CHANGED: '运行上下文已变化，请重新核对场景并输入指令。',
  VOICE_INTERPRETATION_INVALID: '解析来源已失效，请重新核对并解析。',
  VOICE_INTELLIGENCE_DISABLED: '后端尚未启用语音智能接口。',
  VOICE_PROVIDER_UNAVAILABLE: '本地语音识别服务未就绪，或 ASR 令牌 / 模型版本配置不匹配；请检查识别服务配置后再录音。',
  VOICE_PROVIDER_INVALID_RESPONSE: '本地语音识别服务响应格式或模型版本不匹配，请检查 ASR 配置。',
  VOICE_AUDIO_EMPTY: '音频文件为空，请重新录制或选择有效文件。',
  VOICE_AUDIO_TOO_LARGE: '音频超过5 MiB，请缩短后重试。',
  VOICE_AUDIO_FORMAT_UNSUPPORTED: '音频格式或文件内容无效，请使用Chrome/Edge录制的WebM/Opus或有效MP3。',
  VOICE_AUDIO_TOO_LONG: '音频超过 60 秒，请缩短后重试。',
  VOICE_NO_SPEECH: '未识别到语音，请重新录音。',
  VOICE_RATE_LIMITED: '请求过于频繁，请等待后重试原请求。',
  VOICE_BUDGET_EXCEEDED: '本阶段调用预算已耗尽，请联系管理员。',
  VOICE_UPLOAD_TIMEOUT: '上传超时，请使用原请求恢复查询。',
  VOICE_TRANSCRIPTION_TIMEOUT: '语音识别超时，请使用原请求核对结果。',
  VOICE_PARSE_TIMEOUT: '意图解析超时，请使用原请求核对结果。',
  VOICE_INTERPRETATION_TIMEOUT: '语音已转成文字，但本地模型解析动作超时。请恢复原请求或编辑文字后重新解析，无需重新录音。',
  VOICE_INVALID_REQUEST: '指令解析参数无效，请刷新运行上下文后重新解析已识别文字。',
}

export function voiceRecoveryInfo(error: unknown) {
  const code = error && typeof error === 'object' && 'code' in error ? String(error.code) : ''
  return {
    code,
    message: messages[code] ?? (error instanceof Error ? error.message : '请求失败，请使用原请求核对结果。'),
    forbidden: error instanceof ApiClientError && [401, 403].includes(error.status ?? 0),
    retryable: !['VOICE_REQUEST_EXPIRED', 'IDEMPOTENCY_CONFLICT', 'VOICE_REQUEST_OUTCOME_UNKNOWN',
      'VOICE_CONTEXT_CHANGED', 'VOICE_INTERPRETATION_INVALID', 'VOICE_INVALID_REQUEST',
      'VOICE_AUDIO_TOO_LARGE', 'VOICE_AUDIO_EMPTY', 'VOICE_AUDIO_FORMAT_UNSUPPORTED',
      'VOICE_AUDIO_TOO_LONG', 'VOICE_NO_SPEECH', 'VOICE_TEXT_TOO_LONG', 'VOICE_BUDGET_EXCEEDED'].includes(code),
    retryAfter: error instanceof ApiClientError ? error.retryAfterSeconds ?? (code === 'VOICE_REQUEST_IN_PROGRESS' ? 2 : 0) : 0,
  }
}

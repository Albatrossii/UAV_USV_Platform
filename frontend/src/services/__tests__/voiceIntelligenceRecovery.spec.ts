import { describe, expect, it } from 'vitest'
import { ApiClientError } from '@/api/http'
import { voiceRecoveryInfo } from '@/services/voiceIntelligenceRecovery'
import { VoiceIntelligenceError } from '@/types/voiceIntelligence'

describe('voice intelligence failure recovery', () => {
  it.each([
    [503, 'VOICE_PROVIDER_UNAVAILABLE'],
    [502, 'VOICE_PROVIDER_INVALID_RESPONSE'],
    [504, 'VOICE_TRANSCRIPTION_TIMEOUT'],
  ])('does not offer a futile same-ID retry after a cached server %i response', (status, code) => {
    const info = voiceRecoveryInfo(new ApiClientError('provider failed', status, code, 2))
    expect(info.retryable).toBe(false)
    expect(info.message).toContain('重复提交只会返回同一结果')
    expect(info.retryAfter).toBe(2)
  })

  it('does not confuse a pre-acceptance rate rejection with a cached provider failure', () => {
    const info = voiceRecoveryInfo(new ApiClientError('busy', 429, 'VOICE_RATE_LIMITED', 17))
    expect(info.rateLimited).toBe(true)
    expect(info.retryable).toBe(true)
    expect(info.retryAfter).toBe(17)
    expect(info.message).toContain('重试原请求')
    expect(info.message).not.toContain('已有失败回执')
    expect(info.message).not.toContain('联系管理员')
  })

  it('provides a short default cooldown for an explicit rate rejection without Retry-After', () => {
    expect(voiceRecoveryInfo(new ApiClientError('busy', 429, 'VOICE_RATE_LIMITED')).retryAfter).toBe(2)
  })

  it('keeps same-ID recovery available when only the browser timed out', () => {
    const info = voiceRecoveryInfo(new VoiceIntelligenceError('browser timed out', 'VOICE_TRANSCRIPTION_TIMEOUT'))
    expect(info.retryable).toBe(true)
    expect(info.message).toContain('后端可能仍在处理')
  })

  it('never suggests a new request for an unknown accepted outcome', () => {
    const info = voiceRecoveryInfo(new ApiClientError('unknown', 409, 'VOICE_REQUEST_OUTCOME_UNKNOWN'))
    expect(info.retryable).toBe(false)
    expect(info.rateLimited).toBe(false)
    expect(info.message).toContain('不要换键重发')
  })
})

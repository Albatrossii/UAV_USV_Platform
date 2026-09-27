import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  fetchCsrfToken: vi.fn(),
}))

vi.mock('@/api/auth', () => ({ fetchCsrfToken: mocks.fetchCsrfToken }))
vi.mock('@/api/http', () => ({
  http: { post: mocks.post },
  ApiClientError: class ApiClientError extends Error {
    constructor(message: string, public status?: number, public code?: string) {
      super(message)
      this.name = 'ApiClientError'
    }
  },
}))

import {
  interpretVoiceText,
  transcribeVoiceAudio,
  transcribeLocalAudio,
  LOCAL_ASR_TIMEOUT_MS,
  VOICE_PARSE_TIMEOUT_MS,
  VOICE_TRANSCRIPTION_TIMEOUT_MS,
} from '@/api/voiceIntelligence'
import { VoiceIntelligenceError } from '@/types/voiceIntelligence'
import { ApiClientError } from '@/api/http'

const requestId = '11111111-1111-4111-8111-111111111111'

describe('voice intelligence backend API adapter', () => {
  it('D1 uses the existing Java endpoint with 140 seconds and no interpretation call', async () => {
    mocks.post.mockResolvedValue({ data: { code: 'SUCCESS', data: {
      requestId, text: '停止任务', locale: 'zh-CN', durationMs: 5784, provider: 'local-asr', model: 'small-r1',
    } } })
    await transcribeLocalAudio({ requestId, locale: 'zh-CN', audio: new Blob(['audio'], { type: 'audio/mpeg' }) })
    expect(LOCAL_ASR_TIMEOUT_MS).toBe(140000)
    expect(mocks.post).toHaveBeenCalledOnce()
    expect(mocks.post.mock.calls[0]![0]).toBe('/voice/intelligence/transcriptions')
    expect(mocks.post.mock.calls[0]![2].timeout).toBe(140000)
  })
  it('E1 accepts the five approved MIME types and rejects other formats before HTTP', async () => {
    mocks.post.mockResolvedValue({ data: { code: 'SUCCESS', data: {
      requestId, text: '停止任务', locale: 'zh-CN', durationMs: 1000, provider: 'local-asr', model: 'small-r1',
    } } })
    for (const type of ['audio/webm', 'audio/ogg', 'audio/mp4', 'audio/wav', 'audio/mpeg']) {
      await expect(transcribeLocalAudio({ requestId, locale: 'zh-CN', audio: new Blob(['audio'], { type }) }))
        .resolves.toMatchObject({ requestId })
    }
    mocks.post.mockClear()
    await expect(transcribeLocalAudio({ requestId, locale: 'zh-CN', audio: new Blob(['audio'], { type: 'audio/flac' }) }))
      .rejects.toMatchObject({ code: 'VOICE_AUDIO_FORMAT_UNSUPPORTED' })
    expect(mocks.post).not.toHaveBeenCalled()
  })
  it('D1 does not treat a failed envelope as success', async () => {
    mocks.post.mockResolvedValue({ data: { code: 'FAILURE', data: {
      requestId, text: '停止任务', locale: 'zh-CN', durationMs: 1000, provider: 'local-asr', model: 'small-r1',
    } } })
    await expect(transcribeLocalAudio({ requestId, locale: 'zh-CN', audio: new Blob(['audio'], { type: 'audio/mpeg' }) }))
      .rejects.toMatchObject({ code: 'VOICE_MALFORMED_RESPONSE' })
  })
  it.each([null, 0, 60001, 1.5])('rejects invalid durationMs %s', async durationMs => {
    mocks.post.mockResolvedValue({ data: { data: {
      requestId, text: '暂停', locale: 'zh-CN', durationMs, provider: 'test-fixture', model: 'fixed-v1',
    } } })
    await expect(transcribeVoiceAudio({ requestId, locale: 'zh-CN', audio: new Blob(['x'], { type: 'audio/webm' }) }))
      .rejects.toMatchObject({ code: 'VOICE_MALFORMED_RESPONSE' })
  })
  beforeEach(() => {
    mocks.post.mockReset()
    mocks.fetchCsrfToken.mockReset()
    mocks.fetchCsrfToken.mockResolvedValue({ headerName: 'X-CSRF-TOKEN', token: 'csrf-test' })
  })

  it('uploads supported audio with request identity, CSRF and no manual multipart content type', async () => {
    mocks.post.mockResolvedValue({ data: { data: {
      requestId, text: '暂停任务', locale: 'zh-CN', durationMs: 1000,
      provider: 'configured-backend', model: 'revision-1',
    } } })

    const result = await transcribeVoiceAudio({
      requestId, locale: 'zh-CN', audio: new Blob(['voice'], { type: 'audio/webm;codecs=opus' }),
    })

    expect(result.text).toBe('暂停任务')
    expect(mocks.post).toHaveBeenCalledOnce()
    const [path, body, config] = mocks.post.mock.calls[0]!
    expect(path).toBe('/voice/intelligence/transcriptions')
    expect(body).toBeInstanceOf(FormData)
    expect(body.get('requestId')).toBe(requestId)
    expect(config).toMatchObject({
      timeout: VOICE_TRANSCRIPTION_TIMEOUT_MS,
      headers: { 'X-CSRF-TOKEN': 'csrf-test', 'Idempotency-Key': requestId, 'X-Request-ID': requestId },
    })
    expect(config.headers['Content-Type']).toBeUndefined()
  })

  it('rejects oversized audio before requesting CSRF or contacting the backend', async () => {
    const audio = new Blob([new Uint8Array(5 * 1024 * 1024 + 1)], { type: 'audio/webm' })
    await expect(transcribeVoiceAudio({ requestId, locale: 'zh-CN', audio }))
      .rejects.toMatchObject({ code: 'VOICE_AUDIO_TOO_LARGE' })
    expect(mocks.fetchCsrfToken).not.toHaveBeenCalled()
    expect(mocks.post).not.toHaveBeenCalled()
  })

  it('sends only the frozen provider-neutral interpretation fields', async () => {
    mocks.post.mockResolvedValue({ data: { data: {
      status: 'CANDIDATE', requestId, intent: 'MISSION_PAUSE', action: 'PAUSE',
      normalizedText: '暂停任务', confidence: 0.96, provider: 'configured-backend', model: 'revision-1',
    } } })
    const runtimeContext = {
      runtimeRef: '22222222-2222-4222-8222-222222222222',
      runtimeGeneration: '33333333-3333-4333-8333-333333333333',
      contextVersion: 4,
    }

    const result = await interpretVoiceText({
      requestId, text: ' 暂停任务 ', locale: 'zh-CN', allowedActions: ['PAUSE'],
      availableDeviceCodes: ['UAV-001'], runtimeContext,
    })

    expect(result).toMatchObject({ status: 'CANDIDATE', action: 'PAUSE' })
    expect(mocks.post).toHaveBeenCalledWith('/voice/intelligence/interpretations', {
      requestId, text: ' 暂停任务 ', locale: 'zh-CN', allowedActions: ['PAUSE'],
      availableDeviceCodes: ['UAV-001'], runtimeContext,
    }, expect.objectContaining({ timeout: VOICE_PARSE_TIMEOUT_MS }))
  })

  it('does not send a request that was already cancelled', async () => {
    const controller = new AbortController()
    controller.abort()
    await expect(interpretVoiceText({
      requestId, text: '暂停任务', locale: 'zh-CN', allowedActions: ['PAUSE'],
      availableDeviceCodes: [], runtimeContext: null, signal: controller.signal,
    })).rejects.toMatchObject({ code: 'VOICE_REQUEST_CANCELLED' })
    expect(mocks.fetchCsrfToken).not.toHaveBeenCalled()
  })

  it('maps an adapter timeout without automatically retrying a paid POST', async () => {
    mocks.post.mockRejectedValue(new ApiClientError('timeout', undefined, 'ECONNABORTED'))
    await expect(transcribeVoiceAudio({
      requestId, locale: 'zh-CN', audio: new Blob(['voice'], { type: 'audio/webm' }),
    })).rejects.toMatchObject({ code: 'VOICE_TRANSCRIPTION_TIMEOUT' })
    expect(mocks.post).toHaveBeenCalledOnce()
  })

  it('rejects a mismatched request id instead of showing a stale response', async () => {
    mocks.post.mockResolvedValue({ data: { data: {
      status: 'CANDIDATE', requestId: '99999999-9999-4999-8999-999999999999',
      intent: 'MISSION_STOP', action: 'STOP', normalizedText: '停止任务', confidence: null,
      provider: 'configured-backend', model: 'revision-1',
    } } })
    await expect(interpretVoiceText({
      requestId, text: '停止任务', locale: 'zh-CN', allowedActions: ['STOP'],
      availableDeviceCodes: [], runtimeContext: null,
    })).rejects.toEqual(expect.objectContaining<Partial<VoiceIntelligenceError>>({ code: 'VOICE_MALFORMED_RESPONSE' }))
  })
})

import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import LocalAsrInput from '../LocalAsrInput.vue'
import { ApiClientError } from '@/api/http'
import type { VoiceAudioInput, VoiceTranscript } from '@/types/voiceIntelligence'

function transcript(input: VoiceAudioInput, text = '停止任务'): VoiceTranscript {
  return { requestId: input.requestId, text, locale: 'zh-CN', durationMs: 1000, provider: 'local-asr', model: 'small-test' }
}
const wrappers: ReturnType<typeof mount>[] = []
function setup(transcribe = vi.fn(async (input: VoiceAudioInput) => transcript(input))) {
  const wrapper = mount(LocalAsrInput, { props: { operatorScope: 'admin:ADMIN', transcribe } })
  wrappers.push(wrapper)
  return { wrapper, transcribe }
}
async function upload(wrapper: ReturnType<typeof mount>, type = 'audio/mpeg', content = 'sample') {
  const input = wrapper.get('input[type=file]')
  Object.defineProperty(input.element, 'files', { configurable: true, value: [new File([content], 'sample.mp3', { type })] })
  await input.trigger('change')
  await flushPromises()
}
function button(wrapper: ReturnType<typeof mount>, label: string) {
  return wrapper.findAll('button').find(b => b.text().includes(label))!
}
beforeEach(() => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'performance'] }))
afterEach(() => {
  wrappers.splice(0).forEach(w => w.unmount())
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('D1 isolated ASR UI', () => {
  it('displays 500 codepoints without a parser/candidate or emitted action', async () => {
    const { wrapper, transcribe } = setup(vi.fn(async input => transcript(input, '字'.repeat(500))))
    await upload(wrapper)
    expect(transcribe).toHaveBeenCalledOnce()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toHaveLength(500)
    expect(wrapper.text()).not.toContain('解析指令')
    expect(wrapper.find('.candidate').exists()).toBe(false)
    expect(wrapper.emitted('candidate')).toBeUndefined()
    await wrapper.get('textarea').setValue('人工编辑')
    expect(transcribe).toHaveBeenCalledOnce()
  })
  it('never labels a test fixture as real ASR', async () => {
    const { wrapper } = setup(vi.fn(async input => ({ ...transcript(input), provider: 'test-fixture' })))
    await upload(wrapper)
    expect(wrapper.text()).toContain('不计入验收')
  })
  it('accepts Aliyun as real ASR while remaining transcription-only', async () => {
    const { wrapper, transcribe } = setup(vi.fn(async input => ({
      ...transcript(input), provider: 'aliyun-asr', model: 'paraformer-realtime',
    })))
    await upload(wrapper)
    expect(transcribe).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('阿里云 ASR')
    expect(wrapper.text()).toContain('不会执行任何任务动作')
    expect(wrapper.text()).not.toContain('不计入验收')
    expect(wrapper.find('.candidate').exists()).toBe(false)
    expect(wrapper.emitted('candidate')).toBeUndefined()
  })
  it('reuses the exact key and Blob after loss; cooldown is respected', async () => {
    const transcribe = vi.fn().mockRejectedValueOnce(new ApiClientError('busy', 409, 'VOICE_REQUEST_IN_PROGRESS', 2))
      .mockImplementation(async (input: VoiceAudioInput) => transcript(input))
    const { wrapper } = setup(transcribe)
    await upload(wrapper)
    expect(button(wrapper, '冷却').attributes('disabled')).toBeDefined()
    await vi.advanceTimersByTimeAsync(2000)
    await button(wrapper, '使用原请求恢复').trigger('click')
    await flushPromises()
    expect(transcribe.mock.calls[1]![0].requestId).toBe(transcribe.mock.calls[0]![0].requestId)
    expect(transcribe.mock.calls[1]![0].audio).toBe(transcribe.mock.calls[0]![0].audio)
  })
  it('expires 10 minutes from first submit, not the retry', async () => {
    const { wrapper, transcribe } = setup(vi.fn().mockRejectedValue(new Error('lost')))
    await upload(wrapper)
    await vi.advanceTimersByTimeAsync(590000)
    await button(wrapper, '使用原请求恢复').trigger('click')
    await flushPromises()
    expect(transcribe).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(10000)
    expect(wrapper.text()).toContain('首次提交已超过10分钟')
    expect(button(wrapper, '使用原请求恢复')).toBeUndefined()
    expect(transcribe).toHaveBeenCalledTimes(2)
  })
  it('stops waiting at 140 seconds without automatic resubmission', async () => {
    const { wrapper, transcribe } = setup(vi.fn(() => new Promise<VoiceTranscript>(() => {})))
    await upload(wrapper)
    await vi.advanceTimersByTimeAsync(140000)
    expect(wrapper.text()).toContain('等待已超过140秒')
    expect(transcribe.mock.calls[0]![0].signal?.aborted).toBe(true)
    expect(transcribe).toHaveBeenCalledOnce()
  })
  it('ignores a late success after cancel', async () => {
    let resolve!: (value: VoiceTranscript) => void
    const { wrapper, transcribe } = setup(vi.fn(() => new Promise<VoiceTranscript>(done => { resolve = done })))
    await upload(wrapper)
    await button(wrapper, '停止等待').trigger('click')
    resolve(transcript(transcribe.mock.calls[0]![0]))
    await flushPromises()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    expect(button(wrapper, '使用原请求恢复')).toBeDefined()
  })
  it('clears audio and ignores previous operator responses', async () => {
    let resolve!: (value: VoiceTranscript) => void
    const { wrapper, transcribe } = setup(vi.fn(() => new Promise<VoiceTranscript>(done => { resolve = done })))
    await upload(wrapper)
    await wrapper.setProps({ operatorScope: 'other:ADMIN' })
    resolve(transcript(transcribe.mock.calls[0]![0]))
    await flushPromises()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    expect(transcribe.mock.calls[0]![0].signal?.aborted).toBe(true)
  })
  it('blocks inputs after server permission revocation', async () => {
    const { wrapper } = setup(vi.fn().mockRejectedValue(new ApiClientError('revoked', 403, 'FORBIDDEN')))
    await upload(wrapper)
    expect(button(wrapper, '开始录音').attributes('disabled')).toBeDefined()
    expect(button(wrapper, '使用原请求恢复')).toBeUndefined()
  })
  it.each(['VOICE_NO_SPEECH', 'VOICE_TRANSCRIPT_TOO_LONG', 'VOICE_AUDIO_TOO_LONG',
    'VOICE_AUDIO_FORMAT_UNSUPPORTED', 'VOICE_REQUEST_OUTCOME_UNKNOWN', 'VOICE_REQUEST_EXPIRED',
    'IDEMPOTENCY_CONFLICT'])('does not retry deterministic %s', async code => {
    const { wrapper, transcribe } = setup(vi.fn().mockRejectedValue(new ApiClientError('failure', 422, code)))
    await upload(wrapper)
    expect(button(wrapper, '使用原请求恢复').attributes('disabled')).toBeDefined()
    expect(transcribe).toHaveBeenCalledOnce()
  })
  it('explains an invalid MP3 instead of showing the generic request failure', async () => {
    const { wrapper } = setup(vi.fn().mockRejectedValue(
      new ApiClientError('语音请求未完成，请检查输入或联系管理员', 415, 'VOICE_AUDIO_FORMAT_UNSUPPORTED'),
    ))
    await upload(wrapper)
    expect(wrapper.text()).toContain('音频格式或文件内容无效')
    expect(wrapper.text()).not.toContain('语音请求未完成')
    expect(button(wrapper, '使用原请求恢复').attributes('disabled')).toBeDefined()
  })
  it('rejects empty files and unsupported types before API', async () => {
    const { wrapper, transcribe } = setup()
    for (const type of ['audio/webm', 'audio/ogg', 'audio/mp4', 'audio/wav', 'audio/mpeg']) await upload(wrapper, type)
    expect(transcribe).toHaveBeenCalledTimes(5)
    transcribe.mockClear()
    await upload(wrapper, 'audio/flac')
    await upload(wrapper, 'audio/mpeg', '')
    expect(transcribe).not.toHaveBeenCalled()
  })
  it('clears results when component is remounted', async () => {
    const { wrapper } = setup()
    await upload(wrapper)
    wrapper.unmount()
    const second = setup()
    expect((second.wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
  })
})

class Recorder extends EventTarget {
  static isTypeSupported = () => true
  static latest: Recorder
  state = 'inactive'
  constructor(..._: unknown[]) { super(); Recorder.latest = this }
  start() { this.state = 'recording' }
  stop() {
    this.state = 'inactive'
    // Emulate the final encoded tail arriving after stop() has been called.
    queueMicrotask(() => {
      this.dispatchEvent(Object.assign(new Event('dataavailable'), { data: new Blob(['tail']) }))
      this.dispatchEvent(new Event('stop'))
    })
  }
}
describe('D1 recorder lifecycle', () => {
  function media(getUserMedia?: ReturnType<typeof vi.fn>) {
    const stop = vi.fn()
    vi.stubGlobal('MediaRecorder', Recorder)
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      getUserMedia: getUserMedia ?? vi.fn().mockResolvedValue({ getTracks: () => [{ stop }] }),
    } })
    return stop
  }
  it('stops at 58 seconds and submits only after the final tail', async () => {
    const stop = media()
    const { wrapper, transcribe } = setup()
    await button(wrapper, '开始录音').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(57999)
    expect(transcribe).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(201)
    await flushPromises()
    expect(transcribe).toHaveBeenCalledOnce()
    expect(transcribe.mock.calls[0]![0].audio.size).toBe(4)
    expect(stop).toHaveBeenCalled()
  })
  it('never uploads after microphone permission denial', async () => {
    media(vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError')))
    const { wrapper, transcribe } = setup()
    await button(wrapper, '开始录音').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('权限被拒绝')
    expect(transcribe).not.toHaveBeenCalled()
  })
  it.each([
    ['NotFoundError', '未检测到可用麦克风'],
    ['NotReadableError', '无法读取麦克风'],
  ])('shows a local device message for %s without uploading', async (name, expected) => {
    media(vi.fn().mockRejectedValue(new DOMException('browser message', name)))
    const { wrapper, transcribe } = setup()
    await button(wrapper, '开始录音').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain(expected)
    expect(wrapper.text()).not.toContain('browser message')
    expect(transcribe).not.toHaveBeenCalled()
  })
  it('stops tracks without uploading on unmount', async () => {
    const stop = media()
    const { wrapper, transcribe } = setup()
    await button(wrapper, '开始录音').trigger('click')
    await flushPromises()
    wrapper.unmount()
    await flushPromises()
    expect(stop).toHaveBeenCalled()
    expect(transcribe).not.toHaveBeenCalled()
  })
})

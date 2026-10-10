import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import VoiceIntelligenceInput from '@/components/voice/VoiceIntelligenceInput.vue'
import type { VoiceIntelligenceAdapter } from '@/types/voiceIntelligence'

class FakeMediaRecorder extends EventTarget {
  static instances: FakeMediaRecorder[] = []
  static isTypeSupported() { return true }

  state: RecordingState = 'inactive'
  readonly mimeType = 'audio/webm;codecs=opus'
  start = vi.fn(() => { this.state = 'recording' })
  stop = vi.fn(() => {
    if (this.state !== 'recording') throw new Error('Recorder stopped more than once')
    this.state = 'inactive'
    // A native recorder dispatches these events asynchronously, after stop().
    queueMicrotask(() => {
      this.dispatchEvent(Object.assign(new Event('dataavailable'), {
        data: new Blob(['recorded complete command'], { type: this.mimeType }),
      }))
      this.dispatchEvent(new Event('stop'))
    })
  })

  constructor() {
    super()
    FakeMediaRecorder.instances.push(this)
  }
}

function setupRecording(text = '开始任务。') {
  const stopTrack = vi.fn()
  const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] })
  vi.stubGlobal('navigator', {
    userAgent: navigator.userAgent,
    mediaDevices: { getUserMedia },
  })
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  // The absence of Web Audio must not prevent MediaRecorder capture. There is
  // intentionally no fake analyser to make a silence timeout accidentally pass.
  vi.stubGlobal('AudioContext', undefined)
  vi.stubGlobal('webkitAudioContext', undefined)

  const transcribe = vi.fn<VoiceIntelligenceAdapter['transcribe']>(async input => ({
    requestId: input.requestId, text, locale: 'zh-CN', durationMs: 30_000,
    provider: 'aliyun-asr', model: 'test-recording-asr',
  }))
  const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(async input => text.includes('无人机') ? {
    status: 'CANDIDATE', requestId: input.requestId, action: 'DEVICE_COMMAND', intent: 'SINGLE_DEVICE_CONTROL',
    targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_HOVER',
    normalizedText: input.text, confidence: 1, provider: 'local-rules', model: 'rules-v1',
  } : {
    status: 'CANDIDATE', requestId: input.requestId, action: 'START', intent: 'MISSION_START',
    normalizedText: input.text, confidence: 1, provider: 'local-rules', model: 'rules-v1',
  })
  const wrapper = mount(VoiceIntelligenceInput, { props: {
    refined: true, autoExecuteSpeech: true,
    adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe, parse },
    allowedActions: ['START', 'PAUSE', 'RESUME', 'STOP'],
    deviceCodes: ['UAV-001', 'USV-001'], operatorScope: 'admin',
    runtimeContext: { runtimeRef: 'runtime-recording', runtimeGeneration: 'generation-recording', contextVersion: 1 },
  } })
  return { wrapper, stopTrack, getUserMedia, transcribe, parse }
}

async function beginRecording(wrapper: ReturnType<typeof setupRecording>['wrapper']) {
  await wrapper.get('button[aria-label="开始录音"]').trigger('click')
  await flushPromises()
  expect(wrapper.get('button[aria-label="停止录音"]').attributes('aria-pressed')).toBe('true')
  expect(FakeMediaRecorder.instances).toHaveLength(1)
  return FakeMediaRecorder.instances[0]!
}

describe('voice recording duration and explicit completion', () => {
  beforeEach(() => {
    FakeMediaRecorder.instances = []
    vi.useFakeTimers({ toFake: ['Date', 'performance', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('records without AudioContext and does not stop at former silence, speech or total-duration limits', async () => {
    const { wrapper, getUserMedia, stopTrack, transcribe } = setupRecording()
    const recorder = await beginRecording(wrapper)
    expect(getUserMedia).toHaveBeenCalledOnce()
    expect(wrapper.get('.refined-capture-note').text()).toContain('停顿不会截断')
    expect(wrapper.get('.refined-capture-note').text()).toContain('59 秒')

    let previousTime = 0
    for (const elapsedMs of [700, 6_000, 12_000, 30_000]) {
      await vi.advanceTimersByTimeAsync(elapsedMs - previousTime)
      previousTime = elapsedMs
      expect(recorder.state).toBe('recording')
      expect(recorder.stop).not.toHaveBeenCalled()
      expect(transcribe).not.toHaveBeenCalled()
      expect(stopTrack).not.toHaveBeenCalled()
    }
    expect(wrapper.get('.refined-capture-note').text()).toContain('29 秒')
    wrapper.unmount()
    await flushPromises()
  })

  it('submits exactly one ASR and automatic action when the user explicitly finishes a long command', async () => {
    const { wrapper, stopTrack, transcribe, parse } = setupRecording()
    const recorder = await beginRecording(wrapper)
    await vi.advanceTimersByTimeAsync(30_000)
    await wrapper.get('button[aria-label="停止录音"]').trigger('click')
    await flushPromises()

    expect(recorder.stop).toHaveBeenCalledOnce()
    expect(stopTrack).toHaveBeenCalledOnce()
    expect(transcribe).toHaveBeenCalledOnce()
    expect(parse).toHaveBeenCalledOnce()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('开始任务。')
    expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
    expect(wrapper.emitted('voiceCandidate')![0]![0]).toBe('MISSION_START')

    await vi.advanceTimersByTimeAsync(60_000)
    expect(recorder.stop).toHaveBeenCalledOnce()
    expect(transcribe).toHaveBeenCalledOnce()
    expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
    wrapper.unmount()
  })

  it.each(['开始任务。', '一号无人机悬停。'])(
    'stops at exactly 59 seconds but never executes a potentially truncated transcript (%s)',
    async (text) => {
      const { wrapper, stopTrack, transcribe, parse } = setupRecording(text)
      const recorder = await beginRecording(wrapper)
      await vi.advanceTimersByTimeAsync(58_999)
      expect(recorder.stop).not.toHaveBeenCalled()

      await vi.advanceTimersByTimeAsync(1)
      await flushPromises()
      expect(recorder.stop).toHaveBeenCalledOnce()
      expect(stopTrack).toHaveBeenCalledOnce()
      expect(transcribe).toHaveBeenCalledOnce()
      expect(parse).not.toHaveBeenCalled()
      expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(text)
      expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
      expect(wrapper.emitted('candidate')).toBeUndefined()
      expect(wrapper.text()).toContain('录音时长上限')
      expect(wrapper.text()).toContain('不会自动执行')
      expect(wrapper.get('.refined-preview').attributes('disabled')).toBeUndefined()

      await vi.advanceTimersByTimeAsync(60_000)
      expect(recorder.stop).toHaveBeenCalledOnce()
      expect(transcribe).toHaveBeenCalledOnce()
      expect(parse).not.toHaveBeenCalled()
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
      wrapper.unmount()
    },
  )

  it('allows a manual finish just before the hard limit without a duplicate timer stop', async () => {
    const { wrapper, transcribe } = setupRecording()
    const recorder = await beginRecording(wrapper)
    await vi.advanceTimersByTimeAsync(58_999)
    await wrapper.get('button[aria-label="停止录音"]').trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(1_000)
    expect(recorder.stop).toHaveBeenCalledOnce()
    expect(transcribe).toHaveBeenCalledOnce()
    expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
    wrapper.unmount()
  })

  it('cancels and releases a recording on unmount without uploading its final audio event', async () => {
    const { wrapper, stopTrack, transcribe, parse } = setupRecording()
    const recorder = await beginRecording(wrapper)
    await vi.advanceTimersByTimeAsync(12_000)
    wrapper.unmount()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(70_000)
    expect(recorder.stop).toHaveBeenCalledOnce()
    expect(stopTrack).toHaveBeenCalled()
    expect(transcribe).not.toHaveBeenCalled()
    expect(parse).not.toHaveBeenCalled()
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
  })
})

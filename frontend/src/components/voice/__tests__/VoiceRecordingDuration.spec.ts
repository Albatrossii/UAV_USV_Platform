import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import VoiceIntelligenceInput from '@/components/voice/VoiceIntelligenceInput.vue'
import type { VoiceIntelligenceAdapter } from '@/types/voiceIntelligence'
import { algorithmPresets } from '@/services/algorithmPresets'

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

function setupRecording(text = '开始任务。', parseOverride?: VoiceIntelligenceAdapter['parse']) {
  const stopTrack = vi.fn()
  const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] })
  vi.stubGlobal('navigator', {
    userAgent: navigator.userAgent,
    mediaDevices: { getUserMedia, enumerateDevices: vi.fn().mockResolvedValue([
      { kind: 'audioinput', deviceId: 'headset-1', label: 'Headset Microphone' },
      { kind: 'audiooutput', deviceId: 'speaker-1', label: 'Speaker' },
    ]) },
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
  const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(parseOverride ?? (async input => text.includes('无人机') ? {
    status: 'CANDIDATE', requestId: input.requestId, action: 'DEVICE_COMMAND', intent: 'SINGLE_DEVICE_CONTROL',
    targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_HOVER',
    normalizedText: input.text, confidence: 1, provider: 'local-rules', model: 'rules-v1',
  } : {
    status: 'CANDIDATE', requestId: input.requestId, action: 'START', intent: 'MISSION_START',
    normalizedText: input.text, confidence: 1, provider: 'local-rules', model: 'rules-v1',
  }))
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
    algorithmPresets.value = [{ id: 14, command: 'bind T2 S1 S2 S3', label: '将一二三号无人艇分配给二号敌船', effect: '设置固定分组' }]
    FakeMediaRecorder.instances = []
    vi.useFakeTimers({ toFake: ['Date', 'performance', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
  })

  afterEach(() => {
    algorithmPresets.value = []
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  const simulation = { runtimeRef: 'runtime-recording', runtimeGeneration: 'generation-recording',
    runtimeScope: 'MISSION_CENTER', runtimeKind: 'STANDALONE_ALGORITHM',
    executionBackend: 'PYTHON_SIMULATION', protocolVersion: 'algorithm.command.v1' }
  it.each([
    ['一号无人机追击一号d穿。', '一号无人机追击一号敌船', 1, true],
    ['一号无人机追击二号di chuan。', '一号无人机追击二号敌船', 7, true],
    ['将一二三号无人艇分配给二号地船。', '将一二三号无人艇分配给二号敌船', 14, true],
    ['一号无人机追击一号d穿。', '一号无人机追击二号敌船', 7, false],
    ['一号无人机追击一号d穿。', '一号无人艇追击一号敌船', 2, false],
    ['不要一号无人机追击一号d穿', '一号无人机追击一号敌船', 1, false],
    ['一号无人机追击一号d穿吗？', '一号无人机追击一号敌船', 1, false],
    ['一号无人机追击二号的喘。', '一号无人机追击二号敌船', 7, true],
    ['一号无人机追击二号的喘。', '一号无人机追击一号敌船', 1, false],
    ['一号无人机追击二号的喘。', '一号无人机追击二号敌船', 1, false],
    ['不要一号无人机追击二号的喘', '一号无人机追击二号敌船', 7, false],
    ['一号无人机追击二号的喘吗？', '一号无人机追击二号敌船', 7, false],
    ['让一架无人机追击一号敌船。', '一号无人机追击一号敌船', 1, true],
    ['请让一架无人机追击一号敌船。', '一号无人机追击一号敌船', 1, true],
    ['让一架无人机追击一号敌船。', '二号无人机追击一号敌船', 1, false],
    ['让一架无人机追击一号敌船。', '一号无人机追击二号敌船', 1, false],
    ['不要让一架无人机追击一号敌船', '一号无人机追击一号敌船', 1, false],
    ['让一架无人机追击一号敌船吗？', '一号无人机追击一号敌船', 1, false],
    ['任选一架无人机追击一号敌船', '一号无人机追击一号敌船', 1, false],
    ['让两架无人机追击一号敌船', '一号无人机追击一号敌船', 1, false],
    ['一号无人机追击一号机船。', '一号无人机追击一号敌船', 1, true],
    ['一号无人机追击一号的船。', '一号无人机追击一号敌船', 1, true],
    ['恢复首位。', '恢复守卫', 19, true],
    ['请恢复首位！', '请恢复守卫', 19, true],
    ['将一二三号无人艇分配给二号的穿。', '将一二三号无人艇分配给二号敌船', 14, true],
    ['将一二三号无人艇分配给二号的船。', '将一二三号无人艇分配给二号敌船', 14, true],
    ['将一二三号无人艇分配给二号机船。', '将一二三号无人艇分配给二号敌船', 14, true],
    ['将一二三号无人艇分配给二号的穿。', '将一二三号无人机分配给二号敌船', 14, false],
    ['将一二三号无人艇分配给二号的穿。', '将一二三号无人艇分配给一号敌船', 14, false],
    ['不要将一二三号无人艇分配给二号的穿', '将一二三号无人艇分配给二号敌船', 14, false],
    ['将一二三号无人艇分配给二号的穿吗？', '将一二三号无人艇分配给二号敌船', 14, false],
    ['一号无人机追击一号机船。', '二号无人机追击一号敌船', 1, false],
    ['一号无人机追击一号机船。', '一号无人机追击二号敌船', 1, false],
    ['一号无人机追击一号机船。', '一号无人机追击一号敌船', 7, false],
    ['恢复首位。', '恢复护航', 19, false],
    ['不要恢复首位', '恢复守卫', 19, false],
    ['恢复首位吗？', '恢复守卫', 19, false],
  ])('checks actual backend normalized text before speech execution: %s → %s', async (text, normalizedText, presetId, accepted) => {
    const { wrapper } = setupRecording(text as string, async input => ({
      status: 'CANDIDATE', requestId: input.requestId, normalizedText: normalizedText as string,
      action: 'ALGORITHM_PRESET', intent: 'ALGORITHM_PRESET',
      parameters: { presetId, presetVersion: 'escort-20261020' },
      confidence: null, provider: 'local-rules', model: 'rules-v1',
    }))
    await wrapper.setProps({ automationRuntime: simulation })
    await beginRecording(wrapper)
    await wrapper.get('button[aria-label="停止录音"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('voiceCandidate')?.length ?? 0).toBe(accepted ? 1 : 0)
    expect(wrapper.emitted('candidate')).toBeUndefined()
    expect(wrapper.find('.refined-preview').exists()).toBe(false)
    wrapper.unmount()
  })
  it.each([
    ['开始围捕。', 20], ['恢复护航', 0], ['形成守卫队形', 19],
    ['一号无人机追击二号敌船', 7], ['1号无人机追击第2号目标', 7],
    ['一号无人机追击一号机船。', 1], ['请让1号无人机追击第1号机船', 1],
    ['一号无人机追击一号的船。', 1], ['请让1号无人机追击第1号的船', 1],
    ['二号无人艇追击二号的船', 4],
    ['第3号无人艇追击1号敌方目标', 12],
  ])('automatically submits spoken content as its internal preset: %s', async (text, presetId) => {
    const { wrapper } = setupRecording(text as string, async input => ({
      status: 'CANDIDATE', requestId: input.requestId, normalizedText: input.text,
      action: 'ALGORITHM_PRESET', intent: 'ALGORITHM_PRESET',
      parameters: { presetId, presetVersion: 'escort-20261020' },
      confidence: null, provider: 'local-rules', model: 'rules-v1',
    }))
    await wrapper.setProps({ automationRuntime: simulation })
    await beginRecording(wrapper)
    await wrapper.get('button[aria-label="停止录音"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
    expect(wrapper.emitted('voiceCandidate')![0]![0]).toBe('ALGORITHM_PRESET')
    expect(wrapper.emitted('candidate')).toBeUndefined()
    expect(wrapper.find('.refined-preview').exists()).toBe(false)
    wrapper.unmount()
  })
  it.each(['执行第十三条指令', '第13号指令', '指令XIII', '执行第ⅩⅢ条命令', '请帮我执行第壹拾叁条指令吧'])(
    'automatically submits an unedited spoken preset once: %s', async text => {
      const { wrapper, transcribe } = setupRecording(text, async input => ({
        status: 'CANDIDATE', requestId: input.requestId, normalizedText: input.text,
        action: 'ALGORITHM_PRESET', intent: 'ALGORITHM_PRESET',
        parameters: { presetId: 13, presetVersion: 'escort-20261020' },
        confidence: null, provider: 'local-rules', model: 'rules-v1',
      }))
      await wrapper.setProps({ automationRuntime: simulation })
      await beginRecording(wrapper)
      await wrapper.get('button[aria-label="停止录音"]').trigger('click')
      await flushPromises()
      expect(transcribe).toHaveBeenCalledOnce()
      expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
      expect(wrapper.emitted('voiceCandidate')![0]![0]).toBe('ALGORITHM_PRESET')
      expect(wrapper.emitted('candidate')).toBeUndefined()
      wrapper.unmount()
    },
  )

  it('automatically submits a verified 13→14→20 spoken sequence as one plan', async () => {
    const text = '执行第十三条指令，然后执行第十四条指令，再执行第二十条指令'
    const { wrapper } = setupRecording(text, async input => ({
      status: 'CANDIDATE', requestId: input.requestId, normalizedText: input.text,
      action: 'SEQUENCE', intent: 'COMMAND_SEQUENCE',
      steps: [13, 14, 20].map((presetId, index) => ({
        index, action: 'ALGORITHM_PRESET', parameters: { presetId, presetVersion: 'escort-20261020' },
      })),
      confidence: null, provider: 'local-rules', model: 'rules-sequence-v1',
    }))
    await wrapper.setProps({ automationRuntime: simulation })
    await beginRecording(wrapper)
    await wrapper.get('button[aria-label="停止录音"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('voiceCandidate')![0]![0]).toBe('COMMAND_SEQUENCE')
    expect(wrapper.emitted('voiceCandidate')![0]![2]).toMatchObject({ sequenceFirstAction: 'ALGORITHM_PRESET' })
    expect(wrapper.emitted('candidate')).toBeUndefined()
    wrapper.unmount()
  })

  it.each([
    ['执行围捕观察全过程。', true],
    ['执行围捕，观察全过程。', true],
    ['请开始围捕，查看整个过程', true],
    ['执行围捕，停止任务', false],
    ['观察围捕全过程', false],
    ['如果执行围捕，观察全过程', false],
    ['不要执行围捕，观察全过程', false],
    ['执行围捕，观察全过程吗？', false],
  ])('validates the entire capture-and-watch utterance: %s', async (text, accepted) => {
    const { wrapper } = setupRecording(text, async input => ({
      status: 'CANDIDATE', requestId: input.requestId, normalizedText: input.text,
      action: 'ALGORITHM_PRESET', intent: 'ALGORITHM_PRESET',
      parameters: { presetId: 20, presetVersion: 'escort-20261020' },
      confidence: null, provider: 'local-rules', model: 'rules-v1',
    }))
    await wrapper.setProps({ automationRuntime: simulation })
    await beginRecording(wrapper)
    await wrapper.get('button[aria-label="停止录音"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('voiceCandidate')?.length ?? 0).toBe(accepted ? 1 : 0)
    wrapper.unmount()
  })

  it.each(['不要执行第十三条指令', '执行第十四条指令', '执行第十三条指令吗'])(
    'does not automatically dispatch a negated, mismatched or questioning preset: %s', async text => {
      const { wrapper } = setupRecording(text, async input => ({
        status: 'CANDIDATE', requestId: input.requestId, normalizedText: input.text,
        action: 'ALGORITHM_PRESET', intent: 'ALGORITHM_PRESET',
        parameters: { presetId: 13, presetVersion: 'escort-20261020' },
        confidence: null, provider: 'local-rules', model: 'rules-v1',
      }))
      await wrapper.setProps({ automationRuntime: simulation })
      await beginRecording(wrapper)
      await wrapper.get('button[aria-label="停止录音"]').trigger('click')
      await flushPromises()
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
      wrapper.unmount()
    },
  )

  it('requires an explicit click for edited text, then requests execution without a preview', async () => {
    const { wrapper } = setupRecording('开始任务。')
    await wrapper.setProps({ automationRuntime: simulation, directExecuteInput: true, compact: true })
    expect(wrapper.find('.refined-preview').exists()).toBe(false)
    await wrapper.get('textarea').setValue('开始任务')
    expect(wrapper.emitted('executeCandidate')).toBeUndefined()
    await wrapper.get('.refined-preview').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('executeCandidate')![0]![0]).toBe('MISSION_START')
    expect(wrapper.emitted('candidate')).toBeUndefined()
    wrapper.unmount()
  })

  it('uses the explicitly selected microphone and allows disabling capture processing', async () => {
    const { wrapper, getUserMedia } = setupRecording()
    await flushPromises()
    const select = wrapper.get('select[aria-label="录音输入设备"]')
    expect(select.text()).toContain('Headset Microphone')
    expect(select.text()).not.toContain('Speaker')
    await select.setValue('headset-1')
    await wrapper.get('.microphone-processing input').setValue(false)
    await beginRecording(wrapper)
    expect(getUserMedia).toHaveBeenCalledExactlyOnceWith({ audio: {
      deviceId: { exact: 'headset-1' }, echoCancellation: false, noiseSuppression: false, autoGainControl: false,
    } })
    wrapper.unmount()
    await flushPromises()
  })

  it('keeps test audio local and releases its playback URL without ASR, parsing or command dispatch', async () => {
    const create = vi.fn(() => 'blob:microphone-test')
    const revoke = vi.fn()
    const OriginalURL = URL
    vi.stubGlobal('URL', class extends OriginalURL {
      static createObjectURL = create
      static revokeObjectURL = revoke
    })
    const { wrapper, transcribe, parse, stopTrack } = setupRecording()
    await wrapper.get('.microphone-test-actions button:last-child').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('正在测试麦克风')
    await vi.advanceTimersByTimeAsync(5000)
    await wrapper.get('button[aria-label="停止录音"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('audio').attributes('src')).toBe('blob:microphone-test')
    expect(wrapper.text()).toContain('未上传、未执行指令')
    expect(transcribe).not.toHaveBeenCalled()
    expect(parse).not.toHaveBeenCalled()
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(stopTrack).toHaveBeenCalled()
    wrapper.unmount()
    expect(revoke).toHaveBeenCalledExactlyOnceWith('blob:microphone-test')
  })

  it('never falls back to another device when the selected microphone is unavailable', async () => {
    const { wrapper, getUserMedia, transcribe } = setupRecording()
    await flushPromises()
    await wrapper.get('select[aria-label="录音输入设备"]').setValue('headset-1')
    getUserMedia.mockRejectedValueOnce(new DOMException('unplugged', 'OverconstrainedError'))
    await wrapper.get('button[aria-label="开始录音"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('所选麦克风不可用')
    expect(getUserMedia).toHaveBeenCalledOnce()
    expect(transcribe).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('records without AudioContext and does not stop at former silence, speech or total-duration limits', async () => {
    const { wrapper, getUserMedia, stopTrack, transcribe } = setupRecording()
    const recorder = await beginRecording(wrapper)
    expect(getUserMedia).toHaveBeenCalledOnce()
    expect(wrapper.get('.refined-capture-note').text()).toContain('停顿不会截断')
    expect(wrapper.get('.refined-capture-note').text()).toContain('59 秒')
    expect(wrapper.get('.refined-audio-level').text()).toContain('音量检测不可用')
    expect(wrapper.find('.refined-wave').exists()).toBe(false)

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

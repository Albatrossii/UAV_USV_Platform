import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClientError } from '@/api/http'
import type { VoiceIntelligenceAdapter } from '@/types/voiceIntelligence'

import VoiceIntelligenceInput from '@/components/voice/VoiceIntelligenceInput.vue'
import { createMockVoiceIntelligenceAdapter } from '@/services/voiceIntelligence'
import { SPEECH_RECORDING_MAX_MS } from '@/utils/speechRecordingPolicy'

function mountInput() {
  return mount(VoiceIntelligenceInput, {
    props: {
      adapter: createMockVoiceIntelligenceAdapter(),
      allowedActions: ['START', 'PAUSE', 'RESUME', 'STOP'],
      deviceCodes: ['UAV-001', 'USV-001'],
      runtimeContext: null,
      operatorScope: 'admin',
      allowMockSubmission: true,
      actionDisabledReason: () => '',
    },
  })
}

describe('VoiceIntelligenceInput', () => {
  describe.each([false, true])('manual input with speech automation enabled (refined=%s)', refined => {
    it.each([
      ['开始任务', 'START', 'MISSION_START'], ['暂停任务', 'PAUSE', 'MISSION_PAUSE'],
      ['继续任务', 'RESUME', 'MISSION_RESUME'], ['停止任务', 'STOP', 'MISSION_STOP'],
    ] as const)('keeps %s in the confirmation flow', async (text, action, intent) => {
      const parse = vi.fn(async input => ({
        status: 'CANDIDATE' as const, requestId: input.requestId, normalizedText: input.text,
        action, intent, confidence: 1, provider: 'local-rules', model: 'rules-v1',
      }))
      const wrapper = mount(VoiceIntelligenceInput, { props: {
        adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe: vi.fn(), parse },
        allowedActions: ['START', 'PAUSE', 'RESUME', 'STOP'], deviceCodes: [], operatorScope: 'admin',
        autoExecuteSpeech: true, refined,
      } })
      try {
        await wrapper.get('textarea').setValue(text)
        await wrapper.findAll('button').find(button => button.text() === (refined ? '预览指令' : '解析指令'))!.trigger('click')
        await flushPromises()
        expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
        if (!refined) {
          expect(wrapper.emitted('candidate')).toBeUndefined()
          await wrapper.findAll('button').find(button => button.text() === '生成待确认提案')!.trigger('click')
        }
        expect(wrapper.emitted('candidate')).toEqual([[intent, parse.mock.calls[0]![0].requestId]])
        expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
      } finally { wrapper.unmount() }
    })

    it('rejects a mismatched interpretation ID before exposing a candidate or recording an interpretation', async () => {
      const wrapper = mount(VoiceIntelligenceInput, { props: {
        adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe: vi.fn(), parse: async input => ({
          status: 'CANDIDATE', requestId: 'another-request', normalizedText: input.text,
          action: 'START', intent: 'MISSION_START', confidence: 1, provider: 'local-rules', model: 'rules-v1',
        }) }, allowedActions: ['START'], deviceCodes: [], autoExecuteSpeech: true, refined,
      } })
      try {
        await wrapper.get('textarea').setValue('开始任务')
        await wrapper.findAll('button').find(button => button.text() === (refined ? '预览指令' : '解析指令'))!.trigger('click')
        await flushPromises()
        expect(wrapper.text()).toContain('请求编号不一致')
        expect(wrapper.emitted('candidate')).toBeUndefined()
        expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
        expect(wrapper.emitted('interpretation')).toBeUndefined()
        expect(wrapper.find('.candidate').exists()).toBe(false)
        expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('开始任务')
      } finally { wrapper.unmount() }
    })
  })

  it('replaces an old confirmation prompt with the parent authoritative review outcome', async () => {
    const wrapper = mountInput()
    try {
      await wrapper.setProps({ refined: true })
      await wrapper.get('textarea').setValue('开始任务')
      await wrapper.get('.refined-preview').trigger('click')
      await flushPromises()
      expect(wrapper.get('.refined-status').text()).toContain('确认后执行')
      await wrapper.setProps({ reviewStatus: '此指令已取消，未下发。' })
      expect(wrapper.get('.refined-status').text()).toBe('此指令已取消，未下发。')
      expect(wrapper.text()).not.toContain('确认后执行')
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    } finally { wrapper.unmount() }
  })

  it('unlocks recording and text input after a definitive no-speech result', async () => {
    const parse = vi.fn()
    const wrapper = mount(VoiceIntelligenceInput, { props: {
      adapter: { name: 'platform-backend', mode: 'BACKEND',
        transcribe: vi.fn().mockRejectedValue(new ApiClientError('no speech', 422, 'VOICE_NO_SPEECH')), parse },
      allowedActions: ['START'], deviceCodes: [], operatorScope: 'admin', autoExecuteSpeech: true,
    } })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { configurable: true,
      value: [new File(['sample'], 'sample.wav', { type: 'audio/wav' })] })
    await input.trigger('change')
    await flushPromises()
    expect(wrapper.text()).toContain('未识别到语音')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)
    expect(wrapper.findAll('button').find(button => button.text() === '开始录音')!.attributes('disabled')).toBeUndefined()
    expect(parse).not.toHaveBeenCalled()
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(wrapper.emitted('draftChange')).toBeDefined()
    wrapper.unmount()
  })
  function backendInput(parse: VoiceIntelligenceAdapter['parse']) {
    return mount(VoiceIntelligenceInput, { props: {
      adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe: vi.fn(), parse },
      allowedActions: ['PAUSE'], deviceCodes: [], operatorScope: 'admin',
    } })
  }
  const successfulParse: VoiceIntelligenceAdapter['parse'] = async input => ({
    status: 'CANDIDATE', requestId: input.requestId, action: 'PAUSE', intent: 'MISSION_PAUSE',
    normalizedText: input.text, confidence: null, provider: 'test-fixture', model: 'fixed-v1',
  })
  it('recovers a pre-acceptance 429 with the original body and ID only after the cooldown and an explicit click', async () => {
    vi.useFakeTimers()
    const parse = vi.fn().mockRejectedValueOnce(new ApiClientError('busy', 429, 'VOICE_RATE_LIMITED', 3))
      .mockImplementation(successfulParse)
    const wrapper = backendInput(parse)
    try {
      await wrapper.get('textarea').setValue('暂停任务')
      await wrapper.findAll('button')[1]!.trigger('click')
      await flushPromises()
      const retry = () => wrapper.findAll('button').find(button => /等待 .* 秒后重试|^重试原请求$/.test(button.text()))!
      expect(retry().attributes('disabled')).toBeDefined()
      expect(wrapper.text()).not.toContain('已有失败回执')
      expect(wrapper.text()).not.toContain('联系管理员')
      await retry().trigger('click')
      await vi.advanceTimersByTimeAsync(2999)
      expect(parse).toHaveBeenCalledOnce()
      await vi.advanceTimersByTimeAsync(1)
      expect(retry().attributes('disabled')).toBeUndefined()
      expect(parse).toHaveBeenCalledOnce()
      await retry().trigger('click')
      await flushPromises()
      const [first, second] = parse.mock.calls.map(call => call[0])
      expect(second.requestId).toBe(first.requestId)
      expect(second.text).toBe(first.text)
      expect(second.runtimeContext).toEqual(first.runtimeContext)
      expect(wrapper.find('.candidate').exists()).toBe(true)
    } finally { wrapper.unmount(); vi.useRealTimers() }
  })

  it('permits an explicit new request after a repeated definitive rate failure without silently changing keys', async () => {
    vi.useFakeTimers()
    const rateFailure = new ApiClientError('provider busy', 429, 'VOICE_RATE_LIMITED', 2)
    const parse = vi.fn().mockRejectedValueOnce(rateFailure).mockRejectedValueOnce(rateFailure)
      .mockImplementation(successfulParse)
    const wrapper = backendInput(parse)
    try {
      await wrapper.get('textarea').setValue('暂停任务')
      await wrapper.findAll('button')[1]!.trigger('click')
      await flushPromises()
      const newRequest = () => wrapper.findAll('button').find(button => button.text() === '重新发起识别（可能产生新费用）')!
      expect(newRequest().attributes('disabled')).toBeDefined()
      await vi.advanceTimersByTimeAsync(2000)
      await wrapper.findAll('button').find(button => button.text() === '重试原请求')!.trigger('click')
      await flushPromises()
      expect(parse.mock.calls[1]![0].requestId).toBe(parse.mock.calls[0]![0].requestId)
      await vi.advanceTimersByTimeAsync(2000)
      expect(parse).toHaveBeenCalledTimes(2)
      await newRequest().trigger('click')
      await flushPromises()
      expect(parse).toHaveBeenCalledTimes(3)
      expect(parse.mock.calls[2]![0].requestId).not.toBe(parse.mock.calls[0]![0].requestId)
      expect(parse.mock.calls[2]![0].text).toBe('暂停任务')
      expect(wrapper.find('.candidate').exists()).toBe(true)
    } finally { wrapper.unmount(); vi.useRealTimers() }
  })

  it.each([false, true])('does not bypass Retry-After by discarding or switching to text (refined=%s)', async refined => {
    vi.useFakeTimers()
    const parse = vi.fn().mockRejectedValue(new ApiClientError('busy', 429, 'VOICE_RATE_LIMITED', 3))
    const wrapper = backendInput(parse)
    try {
      await wrapper.setProps({ refined })
      await wrapper.get('textarea').setValue('暂停任务')
      const parseButton = () => wrapper.findAll('button').find(button => button.text() === (refined ? '预览指令' : '解析指令'))!
      await parseButton().trigger('click')
      await flushPromises()
      const discard = wrapper.findAll('button').find(button => button.text().startsWith('放弃'))!
      expect(discard.attributes('disabled')).toBeDefined()
      await discard.trigger('click')
      expect(wrapper.text()).toContain('等待 3 秒后重试')
      await wrapper.findAll('button').find(button => button.text().endsWith('改为输入文字'))!.trigger('click')
      expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)
      await wrapper.get('textarea').setValue('暂停当前任务')
      expect(parseButton().attributes('disabled')).toBeDefined()
      await parseButton().trigger('click')
      await vi.advanceTimersByTimeAsync(2999)
      expect(parse).toHaveBeenCalledOnce()
      expect(parseButton().attributes('disabled')).toBeDefined()
      await vi.advanceTimersByTimeAsync(1)
      expect(parseButton().attributes('disabled')).toBeUndefined()
      expect(parse).toHaveBeenCalledOnce()
    } finally { wrapper.unmount(); vi.useRealTimers() }
  })

  it('never exposes the definitive-rate new-request action for an unknown accepted result', async () => {
    const parse = vi.fn().mockRejectedValue(new ApiClientError('unknown', 409, 'VOICE_REQUEST_OUTCOME_UNKNOWN'))
    const wrapper = backendInput(parse)
    try {
      await wrapper.get('textarea').setValue('暂停任务')
      await wrapper.findAll('button')[1]!.trigger('click')
      await flushPromises()
      expect(wrapper.text()).toContain('不要换键重发')
      expect(wrapper.text()).not.toContain('重新发起识别（可能产生新费用）')
      expect(wrapper.findAll('button').find(button => button.text() === '使用原请求恢复查询')!.attributes('disabled')).toBeDefined()
      expect(parse).toHaveBeenCalledOnce()
    } finally { wrapper.unmount() }
  })

  it('recovers with the same body and ID and emits the backend interpretation ID', async () => {
    const parse = vi.fn().mockRejectedValueOnce(new Error('network lost')).mockImplementation(successfulParse)
    const wrapper = backendInput(parse)
    await wrapper.get('textarea').setValue(' 暂停任务 ')
    await wrapper.findAll('button')[1]!.trigger('click')
    await flushPromises()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(true)
    await wrapper.findAll('button').find(b => b.text() === '使用原请求恢复查询')!.trigger('click')
    await flushPromises()
    const [first, second] = parse.mock.calls.map(call => call[0])
    expect(first.requestId).toBe(second.requestId)
    expect(first.text).toBe(second.text)
    expect(first.runtimeContext).toEqual(second.runtimeContext)
    expect(wrapper.text()).toContain('后端测试适配器')
    await wrapper.get('.candidate button').trigger('click')
    expect(wrapper.emitted('candidate')).toEqual([['MISSION_PAUSE', first.requestId]])
    wrapper.unmount()
  })
  it('ignores a late result from the previous operator', async () => {
    let resolve!: () => void
    const parse = vi.fn(input => new Promise<any>(done => { resolve = async () => done(await successfulParse(input)) }))
    const wrapper = backendInput(parse)
    await wrapper.get('textarea').setValue('暂停')
    await wrapper.findAll('button')[1]!.trigger('click')
    await wrapper.setProps({ operatorScope: 'operator-b' })
    resolve()
    await flushPromises()
    expect(wrapper.find('.candidate').exists()).toBe(false)
    expect(parse.mock.calls[0]![0].signal.aborted).toBe(true)
    wrapper.unmount()
  })
  it('clears candidates when the runtime generation changes', async () => {
    const wrapper = backendInput(successfulParse)
    await wrapper.get('textarea').setValue('暂停')
    await wrapper.findAll('button')[1]!.trigger('click')
    await flushPromises()
    await wrapper.setProps({ runtimeContext: { runtimeRef: 'new-ref', runtimeGeneration: 'new-generation', contextVersion: 0 } })
    expect(wrapper.find('.candidate').exists()).toBe(false)
    wrapper.unmount()
  })
  it('keeps a candidate when a contexts refresh replaces the object but not its identity/version', async () => {
    const runtimeContext = { runtimeRef: 'same-ref', runtimeGeneration: 'same-generation', contextVersion: 3 }
    const wrapper = backendInput(successfulParse)
    await wrapper.setProps({ runtimeContext })
    await wrapper.get('textarea').setValue('暂停')
    await wrapper.findAll('button')[1]!.trigger('click')
    await flushPromises()

    await wrapper.setProps({ runtimeContext: { ...runtimeContext } })
    expect(wrapper.find('.candidate').exists()).toBe(true)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('暂停')
    wrapper.unmount()
  })
  it('blocks new input after server revocation', async () => {
    const wrapper = backendInput(vi.fn().mockRejectedValue(new ApiClientError('撤权', 403, 'FORBIDDEN')))
    await wrapper.get('textarea').setValue('暂停')
    await wrapper.findAll('button')[1]!.trigger('click')
    await flushPromises()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(true)
    expect(wrapper.find('.candidate').exists()).toBe(false)
    wrapper.unmount()
  })
  it('blocks local parsing mock from submitting to real P0', async () => {
    const wrapper = mountInput()
    await wrapper.setProps({ allowMockSubmission: false })
    await wrapper.get('textarea').setValue('暂停')
    await wrapper.findAll('button')[1]!.trigger('click')
    await flushPromises()
    expect((wrapper.get('.candidate button').element as HTMLButtonElement).disabled).toBe(true)
    wrapper.unmount()
  })
  it('preserves overlong text and requires editing', async () => {
    const parse = vi.fn(successfulParse)
    const wrapper = backendInput(parse)
    await wrapper.get('textarea').setValue('字'.repeat(201))
    expect(wrapper.text()).toContain('201 字')
    await wrapper.findAll('button')[1]!.trigger('click')
    expect(parse).not.toHaveBeenCalled()
    wrapper.unmount()
  })
  it('keeps the transcript editable and emits only after candidate review', async () => {
    const wrapper = mountInput()
    await wrapper.get('textarea').setValue('暂停当前任务')
    await wrapper.findAll('button')[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('候选指令')
    expect(wrapper.text()).toContain('PAUSE')
    expect(wrapper.emitted('candidate')).toBeUndefined()

    await wrapper.get('.candidate button').trigger('click')
    expect(wrapper.emitted('candidate')).toEqual([['MISSION_PAUSE']])
  })

  it('sends a selected WAV through real speech automation and emits the parsed simulation action', async () => {
    const transcribe = vi.fn(async input => ({
      requestId: input.requestId, text: '开始任务', locale: 'zh-CN', durationMs: 2360,
      provider: 'local-asr', model: 'whisper-small-cpu-int8-r1',
    }))
    const parse = vi.fn(async input => ({
      status: 'CANDIDATE' as const, requestId: input.requestId, action: 'START' as const,
      intent: 'MISSION_START' as const, normalizedText: input.text, confidence: 1,
      provider: 'local-llm', model: 'qwen-local',
    }))
    const wrapper = mount(VoiceIntelligenceInput, { props: {
      adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe, parse },
      allowedActions: ['START'], deviceCodes: ['UAV-001'], operatorScope: 'admin',
      runtimeContext: { runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', contextVersion: 1 },
      autoExecuteSpeech: true,
    } })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      configurable: true, value: [new File(['wav-sample'], 'start.wav', { type: 'audio/wav' })],
    })

    await input.trigger('change')
    await flushPromises()

    expect(transcribe).toHaveBeenCalledOnce()
    expect(parse).toHaveBeenCalledOnce()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('开始任务')
    expect(wrapper.emitted('voiceCandidate')).toEqual([['MISSION_START', expect.any(String), expect.objectContaining({
      startedAt: expect.any(Number), asrRequestMs: expect.any(Number), parseMs: expect.any(Number),
    })]])
    wrapper.unmount()
  })

  it('parses an Aliyun transcript through the existing confirmation flow', async () => {
    const transcribe = vi.fn(async input => ({
      requestId: input.requestId, text: '停止任务', locale: 'zh-CN', durationMs: 900,
      provider: 'aliyun-asr', model: 'paraformer-realtime',
    }))
    const parse = vi.fn(async input => ({
      status: 'CANDIDATE' as const, requestId: input.requestId, action: 'STOP' as const,
      intent: 'MISSION_STOP' as const, normalizedText: input.text, confidence: 1,
      provider: 'local-rules', model: 'rules-v1',
    }))
    const wrapper = mount(VoiceIntelligenceInput, { props: {
      adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe, parse },
      allowedActions: ['STOP'], deviceCodes: [], operatorScope: 'admin', autoExecuteSpeech: true,
    } })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { configurable: true,
      value: [new File(['wav-sample'], 'stop.wav', { type: 'audio/wav' })] })
    await input.trigger('change')
    await flushPromises()

    expect(parse).toHaveBeenCalledOnce()
    expect(parse.mock.calls[0]![0].text).toBe('停止任务')
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(wrapper.emitted('candidate')).toEqual([['MISSION_STOP', expect.any(String)]])
    wrapper.unmount()
  })

  it('keeps test fixture audio as editable text without automatically parsing', async () => {
    const transcribe = vi.fn(async input => ({
      requestId: input.requestId, text: '停止任务', locale: 'zh-CN', durationMs: 900,
      provider: 'test-fixture', model: 'fixed-v1',
    }))
    const parse = vi.fn()
    const wrapper = mount(VoiceIntelligenceInput, { props: {
      adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe, parse },
      allowedActions: ['STOP'], deviceCodes: [], operatorScope: 'admin', autoExecuteSpeech: true,
    } })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { configurable: true,
      value: [new File(['wav-sample'], 'stop.wav', { type: 'audio/wav' })] })
    await input.trigger('change')
    await flushPromises()

    expect(parse).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('未调用真实 ASR')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('停止任务')
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps an LLM-inferred STOP from a short ASR transcript for review instead of auto-executing', async () => {
    const transcribe = vi.fn(async input => ({
      requestId: input.requestId, text: '全停', locale: 'zh-CN', durationMs: 2360,
      provider: 'local-asr', model: 'whisper-small-cpu-int8-r1',
    }))
    const parse = vi.fn(async input => ({
      status: 'CANDIDATE' as const, requestId: input.requestId, action: 'STOP' as const,
      intent: 'MISSION_STOP' as const, normalizedText: input.text, confidence: 0.99,
      provider: 'local-llm', model: 'qwen-local',
    }))
    const wrapper = mount(VoiceIntelligenceInput, { props: {
      adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe, parse },
      allowedActions: ['STOP'], deviceCodes: ['UAV-001'], operatorScope: 'admin',
      runtimeContext: { runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', contextVersion: 1 },
      autoExecuteSpeech: true,
    } })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      configurable: true, value: [new File(['wav-sample'], 'uav.wav', { type: 'audio/wav' })],
    })

    await input.trigger('change')
    await flushPromises()

    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('全停')
    expect(wrapper.text()).toContain('已阻止自动执行，请核对或修改文字')
    expect(wrapper.find('.candidate').exists()).toBe(true)
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    wrapper.unmount()
  })

  it('requires confirmation even for the exact local-rules STOP phrase', async () => {
    const transcribe = vi.fn(async input => ({
      requestId: input.requestId, text: '停止任务', locale: 'zh-CN', durationMs: 2360,
      provider: 'local-asr', model: 'whisper-small-cpu-int8-r1',
    }))
    const parse = vi.fn(async input => ({
      status: 'CANDIDATE' as const, requestId: input.requestId, action: 'STOP' as const,
      intent: 'MISSION_STOP' as const, normalizedText: input.text, confidence: 1,
      provider: 'local-rules', model: 'rules-v1',
    }))
    const wrapper = mount(VoiceIntelligenceInput, { props: {
      adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe, parse },
      allowedActions: ['STOP'], deviceCodes: ['UAV-001'], operatorScope: 'admin',
      runtimeContext: { runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', contextVersion: 1 },
      autoExecuteSpeech: true,
    } })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      configurable: true, value: [new File(['wav-sample'], 'stop.wav', { type: 'audio/wav' })],
    })

    await input.trigger('change')
    await flushPromises()

    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(wrapper.emitted('candidate')).toEqual([['MISSION_STOP', expect.any(String)]])
    expect(wrapper.text()).toContain('高风险')
    wrapper.unmount()
  })

  it('automatically creates a confirmation proposal candidate for a targeted device voice command', async () => {
    const transcribe = vi.fn(async input => ({
      requestId: input.requestId, text: 'USV-001 暂停', locale: 'zh-CN', durationMs: 1800,
      provider: 'local-asr', model: 'whisper-small-cpu-int8-r1',
    }))
    const parse = vi.fn(async input => ({
      status: 'CANDIDATE' as const, requestId: input.requestId, action: 'DEVICE_COMMAND' as const,
      intent: 'SINGLE_DEVICE_CONTROL' as const, targetDeviceCode: 'USV-001', deviceCommandType: 'USV_HOLD' as const,
      normalizedText: input.text, confidence: null, provider: 'local-rules', model: 'rules-v1',
    }))
    const wrapper = mount(VoiceIntelligenceInput, { props: {
      adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe, parse },
      allowedActions: ['START', 'PAUSE', 'RESUME', 'STOP'], deviceCodes: ['UAV-001', 'USV-001'], operatorScope: 'admin',
      runtimeContext: { runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', contextVersion: 1 },
      autoExecuteSpeech: true,
    } })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      configurable: true, value: [new File(['wav-sample'], 'single-device.wav', { type: 'audio/wav' })],
    })

    await input.trigger('change')
    await flushPromises()

    expect(parse).toHaveBeenCalledOnce()
    expect(wrapper.emitted('voiceCandidate')).toEqual([['SINGLE_DEVICE_CONTROL', expect.any(String), expect.objectContaining({
      startedAt: expect.any(Number), asrRequestMs: expect.any(Number), parseMs: expect.any(Number),
    })]])
    expect(wrapper.text()).toContain('正在校验并提交至 USV-001 · 一号无人艇')
    expect(wrapper.find('.candidate').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps a controlled sequence for explicit review and shows both frozen steps', async () => {
    const transcribe = vi.fn(async input => ({
      requestId: input.requestId, text: '开始任务后，一号无人机悬停', locale: 'zh-CN', durationMs: 2100,
      provider: 'aliyun-asr', model: 'aliyun-shiyinshi-v1',
    }))
    const parse = vi.fn(async input => ({
      status: 'CANDIDATE' as const, requestId: input.requestId, action: 'SEQUENCE' as const,
      intent: 'COMMAND_SEQUENCE' as const,
      steps: [
        { index: 0 as const, action: 'START' as const },
        { index: 1 as const, action: 'DEVICE_COMMAND' as const, targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_HOVER' as const },
      ],
      normalizedText: input.text, confidence: null, provider: 'local-rules', model: 'rules-sequence-v1',
    }))
    const wrapper = mount(VoiceIntelligenceInput, { props: {
      adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe, parse },
      allowedActions: ['START', 'PAUSE', 'RESUME', 'STOP'], deviceCodes: ['UAV-001'], operatorScope: 'admin',
      runtimeContext: { runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', contextVersion: 1 },
      autoExecuteSpeech: true,
    } })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      configurable: true, value: [new File(['wav-sample'], 'sequence.wav', { type: 'audio/wav' })],
    })

    await input.trigger('change')
    await flushPromises()

    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(wrapper.text()).toContain('受控顺序指令')
    expect(wrapper.text()).toContain('开始任务')
    expect(wrapper.text()).toContain('UAV-001 · 一号无人机 · 无人机悬停')
    expect(wrapper.text()).toContain('任何一步失败都会停止后续步骤')

    await wrapper.get('.candidate button').trigger('click')
    expect(wrapper.emitted('candidate')).toEqual([['COMMAND_SEQUENCE', expect.any(String)]])
    wrapper.unmount()
  })

  it('requires candidate review for a validated single-device text command even with speech automation enabled', async () => {
    const parse = vi.fn(async input => ({
      status: 'CANDIDATE' as const, requestId: input.requestId, action: 'DEVICE_COMMAND' as const,
      intent: 'SINGLE_DEVICE_CONTROL' as const, targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_HOVER' as const,
      normalizedText: input.text, confidence: 1, provider: 'local-rules', model: 'rules-v1',
    }))
    const wrapper = mount(VoiceIntelligenceInput, { props: {
      adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe: vi.fn(), parse },
      allowedActions: ['START', 'PAUSE', 'RESUME', 'STOP'], deviceCodes: ['UAV-001'], operatorScope: 'admin',
      runtimeContext: { runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', contextVersion: 1 },
      autoExecuteSpeech: true,
    } })
    await wrapper.get('textarea').setValue('一号无人机悬停')
    await wrapper.findAll('button')[1]!.trigger('click')
    await flushPromises()

    expect(parse).toHaveBeenCalledOnce()
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(wrapper.emitted('candidate')).toBeUndefined()
    expect(wrapper.find('.candidate').exists()).toBe(true)
    await wrapper.get('.candidate button').trigger('click')
    expect(wrapper.emitted('candidate')).toEqual([['SINGLE_DEVICE_CONTROL', parse.mock.calls[0]![0].requestId]])
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    wrapper.unmount()
  })

  it('automatically accepts the exact local-rule transcript 执行任务 only for START', async () => {
    const transcribe = vi.fn(async input => ({
      requestId: input.requestId, text: '执行任务', locale: 'zh-CN', durationMs: 2360,
      provider: 'local-asr', model: 'whisper-small-cpu-int8-r1',
    }))
    const parse = vi.fn(async input => ({
      status: 'CANDIDATE' as const, requestId: input.requestId, action: 'START' as const,
      intent: 'MISSION_START' as const, normalizedText: input.text, confidence: 1,
      provider: 'local-rules', model: 'rules-v1',
    }))
    const wrapper = mount(VoiceIntelligenceInput, { props: {
      adapter: { name: 'platform-backend', mode: 'BACKEND', transcribe, parse },
      allowedActions: ['START'], deviceCodes: ['UAV-001'], operatorScope: 'admin',
      runtimeContext: { runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', contextVersion: 1 },
      autoExecuteSpeech: true,
    } })
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      configurable: true, value: [new File(['wav-sample'], 'start.wav', { type: 'audio/wav' })],
    })

    await input.trigger('change')
    await flushPromises()

    expect(parse).toHaveBeenCalledOnce()
    expect(wrapper.emitted('voiceCandidate')).toEqual([['MISSION_START', expect.any(String), expect.objectContaining({
      startedAt: expect.any(Number), asrRequestMs: expect.any(Number), parseMs: expect.any(Number),
    })]])
    wrapper.unmount()
  })

  it('blocks negated commands before proposal creation', async () => {
    const wrapper = mountInput()
    await wrapper.get('textarea').setValue('不要停止任务')
    await wrapper.findAll('button')[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('检测到否定表达')
    expect(wrapper.find('.candidate').exists()).toBe(false)
    expect(wrapper.emitted('candidate')).toBeUndefined()
  })

  it('does not downgrade a single-device command to a fleet action', async () => {
    const wrapper = mountInput()
    await wrapper.get('textarea').setValue('暂停一号无人艇')
    await wrapper.findAll('button')[1]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('暂不支持指定单台设备')
    expect(wrapper.find('.candidate').exists()).toBe(false)
  })

  it('clears unsubmitted text when the operator changes', async () => {
    const wrapper = mountInput()
    await wrapper.get('textarea').setValue('暂停当前任务')
    await wrapper.setProps({ operatorScope: 'operator-b' })

    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    expect(wrapper.text()).toContain('操作员已切换')
  })
})

describe('VoiceIntelligenceInput refined mode', () => {
  afterEach(() => vi.unstubAllGlobals())

  const parseCandidate: VoiceIntelligenceAdapter['parse'] = async input => ({
    status: 'CANDIDATE', requestId: input.requestId, action: 'DEVICE_COMMAND', intent: 'SINGLE_DEVICE_CONTROL',
    targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_HOVER', normalizedText: input.text,
    confidence: 1, provider: 'local-rules', model: 'rules-v1',
  })
  function mountRefined(adapter: VoiceIntelligenceAdapter = {
    name: 'platform-backend', mode: 'BACKEND', transcribe: vi.fn(), parse: vi.fn(parseCandidate),
  }) {
    return mount(VoiceIntelligenceInput, { props: {
      refined: true, adapter, allowedActions: ['START', 'PAUSE', 'RESUME', 'STOP'],
      deviceCodes: ['UAV-001', 'USV-002'], operatorScope: 'admin', autoExecuteSpeech: true,
      runtimeContext: { runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', contextVersion: 1 },
      automationRuntime: { runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', runtimeScope: 'MISSION_CENTER',
        runtimeKind: 'STANDALONE_ALGORITHM', executionBackend: 'PYTHON_SIMULATION', protocolVersion: 'algorithm.command.v1' },
    }, slots: { default: '<section class="frozen-plan">冻结的指令预览</section>' } })
  }
  function installMicrophone() {
    const stopTrack = vi.fn()
    const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] })
    vi.stubGlobal('navigator', Object.create(navigator, { mediaDevices: { value: { getUserMedia } } }))
    class Recorder extends EventTarget {
      static isTypeSupported() { return true }
      state = 'inactive'
      mimeType = 'audio/webm'
      start() { this.state = 'recording' }
      stop() {
        this.state = 'inactive'
        this.dispatchEvent(Object.assign(new Event('dataavailable'), { data: new Blob(['recorded audio']) }))
        this.dispatchEvent(new Event('stop'))
      }
    }
    vi.stubGlobal('MediaRecorder', Recorder)
    vi.stubGlobal('AudioContext', class {
      state = 'running'
      resume = vi.fn().mockResolvedValue(undefined)
      close = vi.fn().mockResolvedValue(undefined)
      createAnalyser = () => ({ fftSize: 1024, getFloatTimeDomainData: vi.fn(), disconnect: vi.fn() })
      createMediaStreamSource = () => ({ connect: vi.fn(), disconnect: vi.fn() })
    })
    return { getUserMedia, stopTrack }
  }
  function speechAdapter(text: string, parse: VoiceIntelligenceAdapter['parse'] = parseCandidate,
    provider = 'aliyun-asr', mode: VoiceIntelligenceAdapter['mode'] = 'BACKEND'): VoiceIntelligenceAdapter {
    return { name: 'platform-backend', mode, parse, transcribe: vi.fn(async input => ({
      requestId: input.requestId, text, locale: 'zh-CN', durationMs: 1000, provider, model: 'test-asr-model',
    })) }
  }
  async function recordSpeech(wrapper: ReturnType<typeof mountRefined>) {
    await wrapper.get('button[aria-label="开始录音"]').trigger('click')
    await flushPromises()
    await wrapper.get('button[aria-label="停止录音"]').trigger('click')
    await flushPromises()
  }

  it('retries rate-limited speech using the same audio and ID without re-recording or duplicate execution', async () => {
    vi.useFakeTimers()
    installMicrophone()
    const adapter = speechAdapter('一号无人机悬停')
    const originalTranscribe = adapter.transcribe
    const transcribe = vi.fn().mockRejectedValueOnce(new ApiClientError('busy', 429, 'VOICE_RATE_LIMITED', 2))
      .mockImplementation(originalTranscribe)
    const wrapper = mountRefined({ ...adapter, transcribe })
    try {
      await recordSpeech(wrapper)
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
      await vi.advanceTimersByTimeAsync(2000)
      expect(transcribe).toHaveBeenCalledOnce()
      await wrapper.findAll('button').find(button => button.text() === '重试原请求')!.trigger('click')
      await flushPromises()
      const [first, second] = transcribe.mock.calls.map(call => call[0])
      expect(second.requestId).toBe(first.requestId)
      expect(second.audio).toBe(first.audio)
      expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
      expect(wrapper.find('.refined-preview').exists()).toBe(false)
    } finally { wrapper.unmount(); vi.useRealTimers() }
  })

  it('retries only intent parsing after ASR succeeded but the parse stage was rate limited', async () => {
    vi.useFakeTimers()
    installMicrophone()
    const parse = vi.fn().mockRejectedValueOnce(new ApiClientError('busy', 429, 'VOICE_RATE_LIMITED', 2))
      .mockImplementation(parseCandidate)
    const adapter = speechAdapter('一号无人机悬停', parse)
    const wrapper = mountRefined(adapter)
    try {
      await recordSpeech(wrapper)
      expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('一号无人机悬停')
      await vi.advanceTimersByTimeAsync(2000)
      await wrapper.findAll('button').find(button => button.text() === '重试原请求')!.trigger('click')
      await flushPromises()
      expect(adapter.transcribe).toHaveBeenCalledOnce()
      expect(parse).toHaveBeenCalledTimes(2)
      expect(parse.mock.calls[1]![0].requestId).toBe(parse.mock.calls[0]![0].requestId)
      expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
    } finally { wrapper.unmount(); vi.useRealTimers() }
  })

  it('only renders the refined controls and never claims an unverified Aliyun connection', async () => {
    const wrapper = mountRefined()
    expect(wrapper.props('compact')).toBe(false)
    expect(wrapper.classes()).not.toContain('is-compact')
    expect(wrapper.get('.refined-capture-title').text()).toBe('点击录音')
    expect(wrapper.text()).not.toContain('一句话，调度海空设备')
    expect(wrapper.text()).toContain('平台语音识别')
    expect(wrapper.text()).toContain('录音后确认来源')
    expect(wrapper.text()).not.toContain('阿里云')
    expect(wrapper.find('input[type="file"]').exists()).toBe(false)
    expect(wrapper.find('.result-details').exists()).toBe(false)
    expect(wrapper.find('.candidate').exists()).toBe(false)
    expect(wrapper.find('.refined-audio-level').exists()).toBe(false)
    expect(wrapper.findAll('.refined-examples button')).toHaveLength(3)
    expect(wrapper.get('.frozen-plan').exists()).toBe(true)
    expect(wrapper.get('.refined-preview').text()).toBe('预览指令')
    await wrapper.setProps({ proposalReady: true })
    expect(wrapper.find('.refined-preview').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps editable text while removing idle buttons and examples in compact mode', async () => {
    const wrapper = mountRefined()
    try {
      await wrapper.setProps({ compact: true })
      expect(wrapper.classes()).toContain('is-compact')
      expect(wrapper.get('.refined-capture-title').text()).toBe('点击录音')
      expect(wrapper.get('.refined-capture-note').text()).toContain('说完点击结束，自动执行')
      expect(wrapper.get('textarea').attributes('placeholder')).toBe('如：一号无人机悬停')
      expect(wrapper.findAll('.refined-examples button')).toHaveLength(0)
      expect(wrapper.find('.refined-preview').exists()).toBe(false)
      await wrapper.get('textarea').setValue('开始任务，然后让一号无人机悬停。')
      expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('开始任务，然后让一号无人机悬停。')
      expect(wrapper.get('.refined-capture-title').text()).toBe('文字已就绪')
      await wrapper.get('textarea').setValue('一号无人机悬停')
      await wrapper.get('.refined-preview').trigger('click')
      await flushPromises()
      expect(wrapper.emitted('candidate')).toEqual([['SINGLE_DEVICE_CONTROL', expect.any(String)]])
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
      expect(wrapper.get('.frozen-plan').text()).toBe('冻结的指令预览')
      expect(wrapper.get('.refined-status').text()).toContain('确认后执行')
    } finally { wrapper.unmount() }
  })

  it('shows recording, recognition and ready states without losing automatic speech execution in compact mode', async () => {
    installMicrophone()
    const adapter = speechAdapter('一号无人机悬停')
    let finishRecognition!: () => void
    const transcribe = vi.fn<VoiceIntelligenceAdapter['transcribe']>(input => new Promise(resolve => {
      finishRecognition = async () => resolve(await adapter.transcribe(input))
    }))
    const wrapper = mountRefined({ ...adapter, transcribe })
    try {
      await wrapper.setProps({ compact: true })
      await wrapper.get('button[aria-label="开始录音"]').trigger('click')
      await flushPromises()
      expect(wrapper.get('.refined-capture-title').text()).toBe('正在聆听你的指令')
      expect(wrapper.get('.refined-capture-note').text()).toContain('停顿不会截断')
      expect(wrapper.find('.refined-audio-level').exists()).toBe(true)
      await wrapper.get('button[aria-label="停止录音"]').trigger('click')
      await flushPromises()
      expect(wrapper.get('.refined-capture-title').text()).toBe('正在转换为文字')
      expect(wrapper.get('.refined-capture-note').text()).toContain('无需重复录音')
      finishRecognition()
      await flushPromises()
      expect(wrapper.get('.refined-capture-title').text()).toBe('文字已就绪')
      expect(wrapper.emitted('voiceCandidate')).toEqual([['SINGLE_DEVICE_CONTROL', expect.any(String), expect.any(Object)]])
      expect(wrapper.find('.refined-preview').exists()).toBe(false)
      await wrapper.get('textarea').setValue('一号无人机安全归队')
      expect(wrapper.find('.refined-preview').exists()).toBe(true)
      expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
    } finally { wrapper.unmount() }
  })

  it.each([
    ['aliyun-asr', '阿里云语音识别'],
    ['local-asr', '本地语音识别'],
    ['test-fixture', '语音测试样例'],
  ])('automates only real %s transcription while retaining editable text', async (provider, label) => {
    const { stopTrack } = installMicrophone()
    const parse = vi.fn(parseCandidate)
    const transcribe = vi.fn<VoiceIntelligenceAdapter['transcribe']>(async input => ({
      requestId: input.requestId, text: '一号无人机悬停', locale: 'zh-CN', durationMs: 1000,
      provider, model: 'test-model',
    }))
    const wrapper = mountRefined({ name: 'platform-backend', mode: 'BACKEND', parse, transcribe })
    await wrapper.get('button[aria-label="开始录音"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('.refined-audio-level').exists()).toBe(true)
    expect(wrapper.emitted('draftChange')).toHaveLength(1)
    await wrapper.get('button[aria-label="停止录音"]').trigger('click')
    await flushPromises()
    expect(transcribe).toHaveBeenCalledOnce()
    if (provider === 'test-fixture') expect(parse).not.toHaveBeenCalled()
    else expect(parse).toHaveBeenCalledOnce()
    expect(stopTrack).toHaveBeenCalled()
    expect(wrapper.get('.refined-provider').text()).toContain(label)
    expect(wrapper.find('.refined-audio-level').exists()).toBe(false)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('一号无人机悬停')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)
    if (provider === 'test-fixture') {
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
      expect(wrapper.find('.refined-preview').exists()).toBe(true)
    } else {
      expect(wrapper.emitted('voiceCandidate')).toEqual([['SINGLE_DEVICE_CONTROL', expect.any(String), expect.any(Object)]])
      expect(wrapper.find('.refined-preview').exists()).toBe(false)
      await wrapper.get('textarea').setValue('二号无人艇驻留')
      expect(wrapper.find('.refined-preview').exists()).toBe(true)
    }
    expect(wrapper.emitted('candidate')).toBeUndefined()
    wrapper.unmount()
  })

  it.each([
    ['开始任务', 'START', 'MISSION_START'], ['暂停任务', 'PAUSE', 'MISSION_PAUSE'],
    ['继续任务', 'RESUME', 'MISSION_RESUME'], ['停止任务', 'STOP', 'MISSION_STOP'],
  ] as const)('automatically emits the explicit speech command %s exactly once', async (text, action, intent) => {
    installMicrophone()
    const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(async input => ({
      status: 'CANDIDATE', requestId: input.requestId, normalizedText: input.text,
      action, intent, confidence: 1, provider: 'local-rules', model: 'rules-v1',
    }))
    const wrapper = mountRefined(speechAdapter(text, parse))
    await recordSpeech(wrapper)
    expect(wrapper.emitted('voiceCandidate')).toEqual([[intent, parse.mock.calls[0]![0].requestId, expect.any(Object)]])
    expect(wrapper.emitted('candidate')).toBeUndefined()
    expect(wrapper.find('.refined-preview').exists()).toBe(false)
    await flushPromises()
    expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
    expect(wrapper.text()).toContain('说完点击结束，自动执行')
    expect(wrapper.text()).not.toContain('返航、降落、停止需确认')
    wrapper.unmount()
  })

  it.each([
    ['一号无人机悬停', 'UAV-001', 'UAV_HOVER'], ['一号无人机归队', 'UAV-001', 'UAV_RESUME'],
    ['二号无人艇驻留', 'USV-002', 'USV_HOLD'], ['二号无人艇继续', 'USV-002', 'USV_RESUME'],
  ] as const)('accepts the explicitly targeted device command %s', async (text, targetDeviceCode, deviceCommandType) => {
    installMicrophone()
    const wrapper = mountRefined(speechAdapter(text, async input => ({
      ...await parseCandidate(input), targetDeviceCode, deviceCommandType,
    })))
    await recordSpeech(wrapper)
    expect(wrapper.emitted('voiceCandidate')).toEqual([['SINGLE_DEVICE_CONTROL', expect.any(String), expect.any(Object)]])
    wrapper.unmount()
  })

  it.each([
    ['一号无人机返航', 'UAV-001', 'UAV_RETURN'], ['一号无人机降落', 'UAV-001', 'UAV_LAND'],
    ['二号无人艇返航', 'USV-002', 'USV_RETURN'], ['二号无人艇停止', 'USV-002', 'USV_STOP'],
  ] as const)('automatically submits the clearly targeted simulation voice command %s', async (text, targetDeviceCode, deviceCommandType) => {
    installMicrophone()
    const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(async input => ({
      ...await parseCandidate(input), targetDeviceCode, deviceCommandType,
    }))
    const wrapper = mountRefined(speechAdapter(text, parse))
    await recordSpeech(wrapper)
    expect(wrapper.emitted('voiceCandidate')).toEqual([
      ['SINGLE_DEVICE_CONTROL', parse.mock.calls[0]![0].requestId, expect.any(Object)],
    ])
    expect(wrapper.emitted('candidate')).toBeUndefined()
    await flushPromises()
    expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
    wrapper.unmount()
  })

  it.each([
    null,
    { executionBackend: 'REAL_DEVICE' }, { runtimeScope: 'GATEWAY' }, { runtimeKind: 'GATEWAY' },
    { runtimeRef: 'another-run' }, { runtimeGeneration: 'another-generation' }, { protocolVersion: 'legacy' },
  ])('retains return confirmation when simulation scope does not match: %j', async changed => {
    installMicrophone()
    const wrapper = mountRefined(speechAdapter('一号无人机返航', async input => ({
      ...await parseCandidate(input), deviceCommandType: 'UAV_RETURN',
    })))
    await wrapper.setProps({ automationRuntime: changed ? { ...wrapper.props('automationRuntime')!, ...changed } : null })
    await recordSpeech(wrapper)
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(wrapper.emitted('candidate')).toEqual([['SINGLE_DEVICE_CONTROL', expect.any(String)]])
    wrapper.unmount()
  })

  it.each(['不要让一号无人机返航', '一号无人机是否返航', '一号无人机或者二号无人机返航'])(
    'never automates a negated or ambiguous return: %s', async text => {
      installMicrophone()
      const wrapper = mountRefined(speechAdapter(text, async input => ({
        ...await parseCandidate(input), deviceCommandType: 'UAV_RETURN',
      })))
      await recordSpeech(wrapper)
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
      expect(wrapper.emitted('candidate')).toBeUndefined()
      wrapper.unmount()
    },
  )

  describe.each([false, true])('negation guard against an older permissive backend (refined=%s)', refined => {
    it.each([
      ['speech', '一号无人机暂不返航'], ['speech', '一号无人机不返航'], ['speech', '一号无人机不降落'],
      ['speech', '开始任务，然后一号无人机不返航'], ['speech', '暂不让一号无人鸡返航'],
      ['manual', '一号无人机暂不返航'], ['manual', '一号无人机不返航'], ['manual', '一号无人机不降落'],
      ['manual', '开始任务，然后一号无人机不返航'], ['manual', '暂不让一号无人鸡返航'],
    ])('blocks %s negation before calling the parser: %s', async (origin, text) => {
      installMicrophone()
      const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(async input => ({
        ...await parseCandidate(input), deviceCommandType: 'UAV_RETURN', normalizedText: '一号无人机返航',
      }))
      const wrapper = mountRefined(speechAdapter(text!, parse))
      await wrapper.setProps({ refined })
      try {
        if (origin === 'speech') {
          await wrapper.findAll('button').find(button => button.attributes('aria-label') === '开始录音' || button.text() === '开始录音')!.trigger('click')
          await flushPromises()
          await wrapper.findAll('button').find(button => button.attributes('aria-label') === '停止录音' || button.text() === '停止录音')!.trigger('click')
          await flushPromises()
        } else {
          await wrapper.get('textarea').setValue(text!)
          await wrapper.findAll('button').find(button => button.text() === (refined ? '预览指令' : '解析指令'))!.trigger('click')
          await flushPromises()
        }
        expect(parse).not.toHaveBeenCalled()
        expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
        expect(wrapper.emitted('candidate')).toBeUndefined()
        expect(wrapper.find('.candidate').exists()).toBe(false)
        expect(wrapper.text()).toContain('检测到否定表达，整条指令未下发')
        expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(text)
      } finally { wrapper.unmount() }
    })
  })

  it.each(['一号无人机请返航', '一号无人机马上返航', '请让一号无人机返航'])(
    'preserves affirmative simulation speech automation: %s', async text => {
      installMicrophone()
      const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(async input => ({
        ...await parseCandidate(input), deviceCommandType: 'UAV_RETURN',
      }))
      const wrapper = mountRefined(speechAdapter(text, parse))
      await recordSpeech(wrapper)
      expect(parse).toHaveBeenCalledOnce()
      expect(wrapper.emitted('voiceCandidate')).toEqual([['SINGLE_DEVICE_CONTROL', expect.any(String), expect.any(Object)]])
      expect(wrapper.emitted('candidate')).toBeUndefined()
      wrapper.unmount()
    },
  )

  it('keeps simulation policy copy consistent when an ended run has no parser context', async () => {
    const wrapper = mountRefined()
    await wrapper.setProps({ runtimeContext: null, submissionDisabled: true })
    expect(wrapper.text()).toContain('说完点击结束，自动执行')
    expect(wrapper.text()).toContain('仿真语音自动执行 · 编辑后需确认')
    expect(wrapper.text()).not.toContain('高风险')
    expect(wrapper.text()).not.toContain('返航、降落、停止需确认')
    wrapper.unmount()
  })

  it('keeps manually edited return text in the confirmation flow after automatic speech', async () => {
    installMicrophone()
    const wrapper = mountRefined(speechAdapter('一号无人机返航', async input => ({
      ...await parseCandidate(input), deviceCommandType: 'UAV_RETURN',
    })))
    await recordSpeech(wrapper)
    expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
    await wrapper.get('textarea').setValue('请让一号无人机返航')
    await wrapper.get('.refined-preview').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
    expect(wrapper.emitted('candidate')).toEqual([['SINGLE_DEVICE_CONTROL', expect.any(String)]])
    expect(wrapper.text()).toContain('确认后执行')
    wrapper.unmount()
  })

  it('does not automatically parse or execute a return after the recording limit cuts off audio', async () => {
    vi.useFakeTimers()
    installMicrophone()
    const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(async input => ({
      ...await parseCandidate(input), deviceCommandType: 'UAV_RETURN',
    }))
    const wrapper = mountRefined(speechAdapter('一号无人机返航', parse))
    try {
      await wrapper.get('button[aria-label="开始录音"]').trigger('click')
      await flushPromises()
      await vi.advanceTimersByTimeAsync(SPEECH_RECORDING_MAX_MS)
      await flushPromises()
      expect(wrapper.text()).toContain('指令可能未说完')
      expect(parse).not.toHaveBeenCalled()
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
      expect(wrapper.emitted('candidate')).toBeUndefined()
      await wrapper.get('.refined-preview').trigger('click')
      await flushPromises()
      expect(wrapper.emitted('candidate')).toEqual([['SINGLE_DEVICE_CONTROL', expect.any(String)]])
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    } finally { wrapper.unmount(); vi.useRealTimers() }
  })

  it.each([['speech', 5], ['manual', 5], ['speech', 9], ['manual', 9]] as const)('explains rejected %s input of %s steps without proposing another oversized batch', async (origin, stepCount) => {
    installMicrophone()
    const text = '开始任务，然后让一号无人机悬停，等待5秒，然后归队，然后二号无人艇驻留' + '，然后等待一秒'.repeat(stepCount - 5)
    const parse: VoiceIntelligenceAdapter['parse'] = async input => ({
      status: 'NEEDS_CLARIFICATION', reason: 'AMBIGUOUS_ACTION', requestId: input.requestId,
      normalizedText: input.text, provider: 'local-rules', model: 'rules-sequence-v1',
      message: '请一次说明 2–4 个顺序动作。',
    })
    const wrapper = mountRefined(speechAdapter(text, parse))
    if (origin === 'speech') await recordSpeech(wrapper)
    else {
      await wrapper.get('textarea').setValue(text)
      await wrapper.get('.refined-preview').trigger('click')
      await flushPromises()
    }
    expect(wrapper.text()).toContain(`本句识别为 ${stepCount} 步，当前最多支持 4 步`)
    expect(wrapper.text()).toContain('整条指令未下发')
    expect(wrapper.text()).toContain('每次最多 4 步')
    expect(wrapper.text()).toContain('等待前一条执行完成后再说下一条')
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(wrapper.emitted('candidate')).toBeUndefined()
    wrapper.unmount()
  })

  it('safely auto-submits an ASR device-name transposition normalized by the backend', async () => {
    installMicrophone()
    const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(async input => ({
      status: 'CANDIDATE', action: 'DEVICE_COMMAND', intent: 'SINGLE_DEVICE_CONTROL',
      requestId: input.requestId, normalizedText: '让二号无人艇驻留',
      targetDeviceCode: 'USV-002', deviceCommandType: 'USV_HOLD',
      provider: 'local-rules', model: 'rules-v1', confidence: null,
    }))
    const wrapper = mountRefined(speechAdapter('让二号艇无人驻留。', parse))
    await recordSpeech(wrapper)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('让二号艇无人驻留。')
    expect(wrapper.emitted('voiceCandidate')).toEqual([
      ['SINGLE_DEVICE_CONTROL', parse.mock.calls[0]![0].requestId, expect.any(Object)],
    ])
    wrapper.unmount()
  })

  it.each([
    ['一号无人挺住。留。', 'USV-001', 'USV_HOLD', true],
    ['一号无人挺住。留。', 'UAV-001', 'UAV_HOVER', false],
    ['一号无人挺住。留。', 'USV-002', 'USV_HOLD', false],
    ['一号无人挺住。留。', 'USV-001', 'USV_RETURN', false],
    ['不要一号无人挺住。留。', 'USV-001', 'USV_HOLD', false],
    ['一号无人挺住。留？', 'USV-001', 'USV_HOLD', false],
    ['一号无人挺住留返航', 'USV-001', 'USV_HOLD', false],
    ['开始任务，然后一号无人挺住。留。', 'USV-001', 'USV_HOLD', false],
  ])('bounds contextual repair and automatic dispatch: %s / %s / %s', async (text, targetDeviceCode, deviceCommandType, automatic) => {
    installMicrophone()
    const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(async input => ({
      status: 'CANDIDATE', action: 'DEVICE_COMMAND', intent: 'SINGLE_DEVICE_CONTROL',
      requestId: input.requestId, normalizedText: '一号无人艇驻留',
      targetDeviceCode, deviceCommandType,
      provider: 'local-rules', model: 'rules-v1', confidence: null,
    }))
    const wrapper = mountRefined(speechAdapter(text, parse))
    await wrapper.setProps({ deviceCodes: ['UAV-001', 'USV-001', 'USV-002'] })
    await recordSpeech(wrapper)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(text)
    if (automatic) {
      expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
      expect(wrapper.text()).toContain('已按完整驻留口令纠错')
      expect(wrapper.text()).toContain('USV-001')
    } else {
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    }
    wrapper.unmount()
  })

  describe('bounded USV ASR repair', () => {
    it.each([
      ['二号无人船主流。', '二号无人艇驻留', 'USV_HOLD'],
      ['二号无人听驻流', '二号无人艇驻留', 'USV_HOLD'],
      ['二号无人停驻流', '二号无人艇驻留', 'USV_HOLD'],
      ['请让第二号无人船注。流！', '请让第二号无人艇驻留', 'USV_HOLD'],
      ['二号无人听归队', '二号无人艇归队', 'USV_RESUME'],
      ['二号无人听返航', '二号无人艇返航', 'USV_RETURN'],
      ['二号无人船归队', '二号无人艇归队', 'USV_RESUME'],
      ['二号无人船返航', '二号无人艇返航', 'USV_RETURN'],
    ] as const)('submits only the corrected USV-002 intent while retaining the original: %s', async (text, normalizedText, deviceCommandType) => {
      installMicrophone()
      const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(async input => ({
        ...await parseCandidate(input), normalizedText, targetDeviceCode: 'USV-002', deviceCommandType,
      }))
      const wrapper = mountRefined(speechAdapter(text, parse))
      try {
        await recordSpeech(wrapper)
        expect(parse).toHaveBeenCalledOnce()
        expect(parse.mock.calls[0]![0].text).toBe(text)
        expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(text)
        expect(wrapper.emitted('interpretation')).toEqual([[expect.objectContaining({
          text, origin: 'speech', result: expect.objectContaining({
            normalizedText, targetDeviceCode: 'USV-002', deviceCommandType,
          }),
        })]])
        expect(wrapper.emitted('voiceCandidate')).toEqual([
          ['SINGLE_DEVICE_CONTROL', parse.mock.calls[0]![0].requestId, expect.any(Object)],
        ])
        expect(wrapper.emitted('candidate')).toBeUndefined()
        await flushPromises()
        expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
      } finally { wrapper.unmount() }
    })

    it.each([
      ['USV-001', 'USV_HOLD'], ['UAV-002', 'UAV_HOVER'], ['USV-002', 'USV_RETURN'],
    ] as const)('rejects a corrected transcript paired with the wrong target or action: %s / %s', async (targetDeviceCode, deviceCommandType) => {
      installMicrophone()
      const text = '二号无人船主流。'
      const wrapper = mountRefined(speechAdapter(text, async input => ({
        ...await parseCandidate(input), normalizedText: '二号无人艇驻留', targetDeviceCode, deviceCommandType,
      })))
      try {
        // All proposed devices exist: this must be a semantic rejection, not
        // an accidental unknown-device rejection masking the mismatch.
        await wrapper.setProps({ deviceCodes: ['USV-001', 'USV-002', 'UAV-002'] })
        await recordSpeech(wrapper)
        expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
        expect(wrapper.emitted('candidate')).toBeUndefined()
        expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(text)
      } finally { wrapper.unmount() }
    })

    it.each([
      '报告二号无人船主流。', '不要二号无人听驻流', '二号无人停驻流吗？',
      '无人船主流。', '一号二号无人船主流。', '一号无人船和二号无人船主流。',
      '二号无人船主流然后返航', '二号无人听归队然后返航',
      '开始任务，然后二号无人船主流。', '今天讨论无人船主流技术。',
    ])('does not turn a fragment, report or multi-action utterance into a single command: %s', async text => {
      installMicrophone()
      const wrapper = mountRefined(speechAdapter(text, async input => ({
        ...await parseCandidate(input), normalizedText: '二号无人艇驻留',
        targetDeviceCode: 'USV-002', deviceCommandType: 'USV_HOLD',
      })))
      try {
        await recordSpeech(wrapper)
        expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
        expect(wrapper.emitted('candidate')).toBeUndefined()
        expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(text)
      } finally { wrapper.unmount() }
    })

    it.each(['二号无人船主流。', '二号无人听驻流', '二号无人停驻流'])(
      'keeps manually entered repairable text in the confirmation flow: %s', async text => {
        const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(async input => ({
          ...await parseCandidate(input), normalizedText: '二号无人艇驻留',
          targetDeviceCode: 'USV-002', deviceCommandType: 'USV_HOLD',
        }))
        const wrapper = mountRefined(speechAdapter(text, parse))
        try {
          await wrapper.get('textarea').setValue(text)
          await wrapper.get('.refined-preview').trigger('click')
          await flushPromises()
          expect(parse.mock.calls[0]![0].text).toBe(text)
          expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
          expect(wrapper.emitted('candidate')).toEqual([['SINGLE_DEVICE_CONTROL', expect.any(String)]])
          expect(wrapper.text()).toContain('确认后执行')
          expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(text)
        } finally { wrapper.unmount() }
      },
    )

    it.each(['开始任务，然后二号无人船主流', '开始任务，然后二号无人听归队'])(
      'does not repair a fragment a second time after a permissive backend splits a sequence: %s', async text => {
        installMicrophone()
        const wrapper = mountRefined(speechAdapter(text, async input => ({
          status: 'CANDIDATE', action: 'SEQUENCE', intent: 'COMMAND_SEQUENCE', requestId: input.requestId,
          normalizedText: input.text, provider: 'local-rules', model: 'rules-sequence-v1', confidence: null,
          steps: [{ index: 0, action: 'START' }, { index: 1, action: 'DEVICE_COMMAND',
            targetDeviceCode: 'USV-002', deviceCommandType: text.endsWith('归队') ? 'USV_RESUME' : 'USV_HOLD' }],
        })))
        try {
          await recordSpeech(wrapper)
          expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
          expect(wrapper.emitted('candidate')).toBeUndefined()
          expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe(text)
        } finally { wrapper.unmount() }
      },
    )
  })

  const sequenceParse = (targetDeviceCode = 'UAV-001', deviceCommandType: 'UAV_HOVER' | 'USV_HOLD' | 'UAV_RETURN' | 'UAV_LAND' | 'USV_RETURN' | 'USV_STOP' = 'UAV_HOVER'): VoiceIntelligenceAdapter['parse'] => async input => ({
    status: 'CANDIDATE', action: 'SEQUENCE', intent: 'COMMAND_SEQUENCE', requestId: input.requestId,
    normalizedText: input.text, provider: 'local-rules', model: 'rules-sequence-v1', confidence: null,
    steps: [{ index: 0, action: 'START' }, { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode, deviceCommandType }],
  })
  it.each([
    ['开始任务，然后让一号无人机悬停。', 'UAV-001', 'UAV_HOVER'],
    ['开始任务后二号无人艇驻留', 'USV-002', 'USV_HOLD'],
    ['开始任务后二号无人船驻留', 'USV-002', 'USV_HOLD'],
    ['开始任务，然后让二号无人船返航', 'USV-002', 'USV_RETURN'],
    ['开始任务让二号无人停驻留', 'USV-002', 'USV_HOLD'],
    ['开始任务，一号无人机悬停', 'UAV-001', 'UAV_HOVER'],
    ['开始任务一号无人机悬停', 'UAV-001', 'UAV_HOVER'],
    ['开始任务，然后一号无人机返航', 'UAV-001', 'UAV_RETURN'],
    ['开始任务，然后一号无人机降落', 'UAV-001', 'UAV_LAND'],
    ['开始任务，然后二号无人艇返航', 'USV-002', 'USV_RETURN'],
    ['开始任务，然后二号无人艇停止', 'USV-002', 'USV_STOP'],
  ] as const)('automatically emits the controlled two-step command %s', async (text, target, command) => {
    installMicrophone()
    const wrapper = mountRefined(speechAdapter(text, sequenceParse(target, command)))
    await recordSpeech(wrapper)
    expect(wrapper.emitted('voiceCandidate')).toEqual([['COMMAND_SEQUENCE', expect.any(String), expect.any(Object)]])
    expect(wrapper.emitted('candidate')).toBeUndefined()
    expect(wrapper.text()).toContain('不同设备可并行')
    expect(wrapper.text()).toContain('失败后不再下发剩余动作')
    wrapper.unmount()
  })

  it('auto-submits the UAV return and boat hold screenshot without requiring a single-device utterance', async () => {
    installMicrophone()
    const text = '一号无人机返航，然后让二号无人船驻留'
    const wrapper = mountRefined(speechAdapter(text, async input => ({
      status: 'CANDIDATE', action: 'SEQUENCE', intent: 'COMMAND_SEQUENCE', requestId: input.requestId,
      normalizedText: '一号无人机返航，然后让二号无人艇驻留', provider: 'local-rules', model: 'rules-sequence-v1', confidence: null,
      steps: [
        { index: 0, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_RETURN' },
        { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'USV-002', deviceCommandType: 'USV_HOLD' },
      ],
    })))
    try {
      await recordSpeech(wrapper)
      expect(wrapper.emitted('voiceCandidate')).toEqual([['COMMAND_SEQUENCE', expect.any(String), expect.any(Object)]])
      expect(wrapper.emitted('candidate')).toBeUndefined()
    } finally { wrapper.unmount() }
  })

  it('auto-submits the screenshot utterance as START then USV-001 HOLD after normalization', async () => {
    installMicrophone()
    const parse: VoiceIntelligenceAdapter['parse'] = async input => ({
      ...await sequenceParse('USV-001', 'USV_HOLD')(input),
      normalizedText: '开始任务让1号无人艇驻留',
    })
    const wrapper = mountRefined(speechAdapter('开始任务让1号无人停驻留', parse))
    await wrapper.setProps({ deviceCodes: ['UAV-001', 'USV-001'] })
    await recordSpeech(wrapper)
    expect(wrapper.emitted('voiceCandidate')).toEqual([['COMMAND_SEQUENCE', expect.any(String), expect.any(Object)]])
    expect(wrapper.emitted('candidate')).toBeUndefined()
    wrapper.unmount()
  })

  it.each([
    ['unknown ASR', 'unknown-asr', 'BACKEND'], ['mock claiming real ASR', 'aliyun-asr', 'MOCK'],
  ] as const)('never automatically parses %s', async (_name, provider, mode) => {
    installMicrophone()
    const parse = vi.fn(parseCandidate)
    const wrapper = mountRefined(speechAdapter('一号无人机悬停', parse, provider, mode))
    await recordSpeech(wrapper)
    expect(parse).not.toHaveBeenCalled()
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    wrapper.unmount()
  })

  it.each([
    ['test parser', '一号无人机悬停', { provider: 'test-fixture' }],
    ['unknown parser', '一号无人机悬停', { provider: 'remote-llm' }],
    ['LLM inferred device', '一号无人机悬停', { provider: 'local-llm' }],
    ['unknown rule revision', '一号无人机悬停', { model: 'unknown-rules' }],
    ['unknown target', '一号无人机悬停', { targetDeviceCode: 'UAV-999' }],
    ['missing target', '一号无人机悬停', { targetDeviceCode: undefined }],
    ['wrong type', '一号无人机悬停', { deviceCommandType: 'USV_HOLD' }],
    ['wrong action', '一号无人机悬停', { deviceCommandType: 'UAV_LAND' }],
    ['wrong intent', '一号无人机悬停', { intent: 'MISSION_START' }],
    ['wrong request', '一号无人机悬停', { requestId: 'another-request' }],
    ['negated command', '不要让一号无人机悬停', {}],
    ['question', '一号无人机能不能悬停', {}],
    ['ambiguous target', '一号无人机或者二号无人机悬停', {}],
    ['missing spoken target', '无人机悬停', {}],
    ['changed spoken target', '二号无人机悬停', {}],
    ['multiple actions', '一号无人机悬停再降落', {}],
    ['dropped start task', '开始任务让一号无人机悬停', {}],
    ['dropped pause task', '暂停任务让一号无人机悬停', {}],
    ['rewritten transcript', '不要让一号无人机悬停', { normalizedText: '一号无人机悬停' }],
  ])('blocks automatic execution for %s even if parsing returns CANDIDATE', async (_name, text, changed) => {
    installMicrophone()
    const wrapper = mountRefined(speechAdapter(text as string, async input => ({
      ...await parseCandidate(input), ...changed,
    }) as Awaited<ReturnType<VoiceIntelligenceAdapter['parse']>>))
    await recordSpeech(wrapper)
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(wrapper.emitted('candidate')).toBeUndefined()
    expect(wrapper.find('.refined-preview').exists()).toBe(false)
    await wrapper.get('textarea').setValue('三号无人机悬停')
    expect(wrapper.find('.refined-preview').exists()).toBe(true)
    wrapper.unmount()
  })

  it('automatically accepts four matched steps including a bounded wait and same-sentence target', async () => {
    installMicrophone()
    const parse: VoiceIntelligenceAdapter['parse'] = async input => ({
      status: 'CANDIDATE', requestId: input.requestId, normalizedText: input.text,
      provider: 'local-rules', model: 'rules-sequence-v1', confidence: null,
      action: 'SEQUENCE', intent: 'COMMAND_SEQUENCE', steps: [
        { index: 0, action: 'START' },
        { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_HOVER' },
        { index: 2, action: 'WAIT', waitSeconds: 5 },
        { index: 3, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_RESUME' },
      ],
    })
    const wrapper = mountRefined(speechAdapter('开始任务，让一号机悬停，等待5秒，然后归队', parse))
    await recordSpeech(wrapper)
    expect(wrapper.emitted('voiceCandidate')).toEqual([['COMMAND_SEQUENCE', expect.any(String), expect.objectContaining({ sequenceFirstAction: 'START' })]])
    expect(wrapper.emitted('candidate')).toBeUndefined()
    wrapper.unmount()
  })

  it('rejects extra unspoken steps or a sequence without its spoken first action', async () => {
    installMicrophone()
    const parse: VoiceIntelligenceAdapter['parse'] = async input => {
      const parsed = await sequenceParse()(input)
      if (parsed.status === 'CANDIDATE') parsed.steps!.push({ index: 2, action: 'START' })
      return parsed
    }
    const wrapper = mountRefined(speechAdapter('开始任务，然后让一号无人机悬停。', parse))
    await recordSpeech(wrapper)
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    wrapper.unmount()
    const withoutStart = mountRefined(speechAdapter('一号无人机悬停', sequenceParse()))
    await recordSpeech(withoutStart)
    expect(withoutStart.emitted('voiceCandidate')).toBeUndefined()
    withoutStart.unmount()
    const droppedThirdStep = mountRefined(speechAdapter('开始任务让一号无人机悬停然后拍照', sequenceParse()))
    await recordSpeech(droppedThirdStep)
    expect(droppedThirdStep.emitted('voiceCandidate')).toBeUndefined()
    droppedThirdStep.unmount()
  })

  it('recovers a speech parse only on explicit request with its original key and speech origin', async () => {
    installMicrophone()
    const parse = vi.fn().mockRejectedValueOnce(new Error('network lost')).mockImplementation(parseCandidate)
    const wrapper = mountRefined(speechAdapter('一号无人机悬停', parse))
    await recordSpeech(wrapper)
    expect(parse).toHaveBeenCalledOnce()
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    await wrapper.findAll('button').find(button => button.text() === '使用原请求恢复查询')!.trigger('click')
    await flushPromises()
    const [first, second] = parse.mock.calls.map(call => call[0])
    expect(second.requestId).toBe(first.requestId)
    expect(second.text).toBe(first.text)
    expect(second.runtimeContext).toEqual(first.runtimeContext)
    expect(wrapper.emitted('voiceCandidate')).toEqual([['SINGLE_DEVICE_CONTROL', first.requestId, expect.any(Object)]])
    expect(wrapper.emitted('candidate')).toBeUndefined()
    expect(wrapper.find('.refined-preview').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each([false, true])('rejects mismatched speech interpretation without offering an executable candidate (refined=%s)', async refined => {
    installMicrophone()
    const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(async input => ({ ...await parseCandidate(input), requestId: 'wrong-request' }))
    const wrapper = mountRefined(speechAdapter('一号无人机悬停', parse))
    try {
      await recordSpeech(wrapper)
      await wrapper.setProps({ refined })
      expect(wrapper.text()).toContain('请求编号不一致')
      expect(wrapper.find('.candidate').exists()).toBe(false)
      expect(wrapper.emitted('candidate')).toBeUndefined()
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
      expect(wrapper.emitted('interpretation')).toBeUndefined()
      expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('一号无人机悬停')
    } finally { wrapper.unmount() }
  })

  it('keeps edited speech in manual confirmation when switching to the legacy panel', async () => {
    installMicrophone()
    const parse = vi.fn().mockRejectedValueOnce(new Error('network lost')).mockImplementation(parseCandidate)
    const wrapper = mountRefined(speechAdapter('一号无人机悬停', parse))
    try {
      await recordSpeech(wrapper)
      await wrapper.findAll('button').find(button => button.text() === '重新编辑文字')!.trigger('click')
      await wrapper.setProps({ refined: false })
      await wrapper.get('textarea').setValue('请让一号无人机悬停')
      await wrapper.findAll('button').find(button => button.text() === '解析指令')!.trigger('click')
      await flushPromises()
      expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
      expect(wrapper.emitted('candidate')).toBeUndefined()
      await wrapper.get('.candidate button').trigger('click')
      expect(wrapper.emitted('candidate')).toEqual([['SINGLE_DEVICE_CONTROL', parse.mock.calls[1]![0].requestId]])
    } finally { wrapper.unmount() }
  })

  it('demotes abandoned speech parsing to a new manual preview after editing', async () => {
    installMicrophone()
    const parse = vi.fn().mockRejectedValueOnce(new Error('network lost')).mockImplementation(parseCandidate)
    const wrapper = mountRefined(speechAdapter('一号无人机悬停', parse))
    await recordSpeech(wrapper)
    await wrapper.findAll('button').find(button => button.text() === '重新编辑文字')!.trigger('click')
    await wrapper.get('textarea').setValue('请让一号无人机悬停')
    await wrapper.get('.refined-preview').trigger('click')
    await flushPromises()
    expect(parse.mock.calls[1]![0].requestId).not.toBe(parse.mock.calls[0]![0].requestId)
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(wrapper.emitted('candidate')).toEqual([['SINGLE_DEVICE_CONTROL', parse.mock.calls[1]![0].requestId]])
    wrapper.unmount()
  })

  it('locks the microphone before recording and discards a capture if execution locks during recording', async () => {
    const { getUserMedia, stopTrack } = installMicrophone()
    const adapter = speechAdapter('一号无人机悬停', vi.fn(parseCandidate))
    const wrapper = mountRefined(adapter)
    await wrapper.setProps({ submissionDisabled: true })
    expect((wrapper.get('button[aria-label="开始录音"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('button[aria-label="开始录音"]').trigger('click')
    expect(getUserMedia).not.toHaveBeenCalled()
    await wrapper.setProps({ submissionDisabled: false })
    await wrapper.get('button[aria-label="开始录音"]').trigger('click')
    await flushPromises()
    await wrapper.setProps({ submissionDisabled: true })
    await flushPromises()
    expect(stopTrack).toHaveBeenCalled()
    expect(adapter.transcribe).not.toHaveBeenCalled()
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    wrapper.unmount()
  })

  it.each(['asr', 'parse'] as const)('ignores late %s results after a transient submission lock', async stage => {
    installMicrophone()
    let resolve!: () => void
    const adapter = speechAdapter('一号无人机悬停', vi.fn(parseCandidate))
    if (stage === 'asr') {
      const transcribe = adapter.transcribe
      adapter.transcribe = vi.fn(input => new Promise(done => { resolve = async () => done(await transcribe(input)) }))
    } else {
      adapter.parse = vi.fn(input => new Promise(done => { resolve = async () => done(await parseCandidate(input)) }))
    }
    const wrapper = mountRefined(adapter)
    await recordSpeech(wrapper)
    await wrapper.setProps({ submissionDisabled: true })
    await wrapper.setProps({ submissionDisabled: false })
    resolve()
    await flushPromises()
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(wrapper.emitted('candidate')).toBeUndefined()
    if (stage === 'asr') expect(adapter.parse).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('ignores a cancelled speech parse and recovers explicitly with the same interpretation ID', async () => {
    installMicrophone()
    let finishOld!: () => void
    const parse = vi.fn<VoiceIntelligenceAdapter['parse']>()
      .mockImplementationOnce(input => new Promise(done => { finishOld = async () => done(await parseCandidate(input)) }))
      .mockImplementation(parseCandidate)
    const wrapper = mountRefined(speechAdapter('一号无人机悬停', parse))
    await recordSpeech(wrapper)
    await wrapper.findAll('button').find(button => button.text() === '停止等待（不保证后端取消）')!.trigger('click')
    finishOld()
    await flushPromises()
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    await wrapper.findAll('button').find(button => button.text() === '使用原请求恢复查询')!.trigger('click')
    await flushPromises()
    expect(parse.mock.calls[1]![0].requestId).toBe(parse.mock.calls[0]![0].requestId)
    expect(wrapper.emitted('voiceCandidate')).toHaveLength(1)
    expect(wrapper.emitted('candidate')).toBeUndefined()
    wrapper.unmount()
  })

  it('emits a backend candidate directly after an explicit preview, never a dispatch event', async () => {
    const parse = vi.fn(parseCandidate)
    const wrapper = mountRefined({ name: 'platform-backend', mode: 'BACKEND', transcribe: vi.fn(), parse })
    await wrapper.get('textarea').setValue('一号无人机悬停')
    await wrapper.get('.refined-preview').trigger('click')
    await flushPromises()
    expect(parse).toHaveBeenCalledOnce()
    expect(parse.mock.calls[0]![0].runtimeContext).toEqual({ runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', contextVersion: 1 })
    expect(wrapper.emitted('candidate')).toEqual([['SINGLE_DEVICE_CONTROL', parse.mock.calls[0]![0].requestId]])
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(wrapper.text()).not.toContain('生成待确认提案')
    expect(wrapper.find('.result-details').exists()).toBe(false)
    wrapper.unmount()
  })

  it('invalidates on editing and context reset while submission locking leaves text editable', async () => {
    const parse = vi.fn(parseCandidate)
    const wrapper = mountRefined({ name: 'platform-backend', mode: 'BACKEND', transcribe: vi.fn(), parse })
    await wrapper.setProps({ submissionDisabled: true, proposalReady: true })
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)
    await wrapper.get('textarea').setValue('新指令')
    expect(wrapper.emitted('draftChange')).toHaveLength(1)
    await wrapper.setProps({ proposalReady: false })
    expect((wrapper.get('.refined-preview').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('.refined-preview').trigger('click')
    expect(parse).not.toHaveBeenCalled()
    await wrapper.setProps({ runtimeContext: { runtimeRef: 'runtime-1', runtimeGeneration: 'generation-2', contextVersion: 0 } })
    expect(wrapper.emitted('draftChange')!.length).toBeGreaterThan(1)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    wrapper.unmount()
  })

  it('uses examples only to fill editable text, not to parse or execute', async () => {
    const parse = vi.fn(parseCandidate)
    const wrapper = mountRefined({ name: 'platform-backend', mode: 'BACKEND', transcribe: vi.fn(), parse })
    await wrapper.findAll('.refined-examples button')[0]!.trigger('click')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('开始任务，然后让一号无人机悬停。')
    expect(parse).not.toHaveBeenCalled()
    expect(wrapper.emitted('draftChange')).toHaveLength(1)
    expect(wrapper.emitted('candidate')).toBeUndefined()
    wrapper.unmount()
  })

  it.each(['typed', 'example'])('clears obsolete runtime warnings when a new %s draft is entered without leaving empty status spacing', async source => {
    const wrapper = mountRefined()
    await wrapper.setProps({ runtimeContext: { runtimeRef: 'runtime-2', runtimeGeneration: 'generation-2', contextVersion: 0 } })
    if (source === 'typed') await wrapper.get('textarea').setValue('一号无人机悬停')
    else await wrapper.findAll('.refined-examples button')[0]!.trigger('click')
    expect(wrapper.text()).not.toContain('运行上下文变化')
    expect(wrapper.find('.refined-status').exists()).toBe(false)
    expect((wrapper.get('.refined-preview').element as HTMLButtonElement).disabled).toBe(false)
    wrapper.unmount()
  })

  it('preserves the legacy status-message behavior when refined mode is disabled', async () => {
    const wrapper = mountRefined()
    await wrapper.setProps({ refined: false, runtimeContext: { runtimeRef: 'runtime-2', runtimeGeneration: 'generation-2', contextVersion: 0 } })
    await wrapper.get('textarea').setValue('一号无人机悬停')
    expect(wrapper.get('.status').text()).toContain('运行上下文变化')
    wrapper.unmount()
  })

  it('blocks a mock parser from the real control flow and only permits explicitly enabled mock proposals', async () => {
    const wrapper = mountRefined(createMockVoiceIntelligenceAdapter())
    await wrapper.get('textarea').setValue('暂停任务')
    await wrapper.get('.refined-preview').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('当前是本地演示，不能向实际控制服务提交指令')
    expect(wrapper.get('.refined-provider').text()).toContain('非真实识别')
    expect(wrapper.emitted('candidate')).toBeUndefined()
    await wrapper.setProps({ allowMockSubmission: true })
    await wrapper.get('.refined-preview').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('candidate')).toEqual([['MISSION_PAUSE']])
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    wrapper.unmount()
  })

  it('restores an unknown interpretation using its exact original body and key', async () => {
    const parse = vi.fn().mockRejectedValueOnce(new Error('network lost')).mockImplementation(parseCandidate)
    const wrapper = mountRefined({ name: 'platform-backend', mode: 'BACKEND', transcribe: vi.fn(), parse })
    await wrapper.get('textarea').setValue(' 一号无人机悬停 ')
    await wrapper.get('.refined-preview').trigger('click')
    await flushPromises()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(true)
    await wrapper.findAll('button').find(button => button.text() === '使用原请求恢复查询')!.trigger('click')
    await flushPromises()
    const [first, second] = parse.mock.calls.map(call => call[0])
    expect(second.requestId).toBe(first.requestId)
    expect(second.text).toBe(first.text)
    expect(second.runtimeContext).toEqual(first.runtimeContext)
    expect(wrapper.emitted('candidate')).toEqual([['SINGLE_DEVICE_CONTROL', first.requestId]])
    wrapper.unmount()
  })

  it('does not emit a candidate when submissions become locked during parsing', async () => {
    let resolve!: () => void
    const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(input => new Promise(done => {
      resolve = async () => done(await parseCandidate(input))
    }))
    const wrapper = mountRefined({ name: 'platform-backend', mode: 'BACKEND', transcribe: vi.fn(), parse })
    await wrapper.get('textarea').setValue('一号无人机悬停')
    await wrapper.get('.refined-preview').trigger('click')
    await wrapper.setProps({ submissionDisabled: true })
    resolve()
    await flushPromises()
    expect(wrapper.emitted('candidate')).toBeUndefined()
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    wrapper.unmount()
  })

  it('ignores a late interpretation after runtime change and cancels the old wait', async () => {
    let resolve!: () => void
    const parse = vi.fn<VoiceIntelligenceAdapter['parse']>(input => new Promise(done => {
      resolve = async () => done(await parseCandidate(input))
    }))
    const wrapper = mountRefined({ name: 'platform-backend', mode: 'BACKEND', transcribe: vi.fn(), parse })
    await wrapper.get('textarea').setValue('一号无人机悬停')
    await wrapper.get('.refined-preview').trigger('click')
    await wrapper.setProps({ runtimeContext: { runtimeRef: 'runtime-2', runtimeGeneration: 'generation-2', contextVersion: 0 } })
    resolve()
    await flushPromises()
    expect(parse.mock.calls[0]![0].signal!.aborted).toBe(true)
    expect(wrapper.emitted('candidate')).toBeUndefined()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    wrapper.unmount()
  })

  it('restores text entry after microphone permission is denied', async () => {
    const { getUserMedia } = installMicrophone()
    getUserMedia.mockRejectedValueOnce(new DOMException('denied', 'NotAllowedError'))
    const wrapper = mountRefined()
    await wrapper.get('button[aria-label="开始录音"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('麦克风权限被拒绝')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).disabled).toBe(false)
    expect(wrapper.find('.refined-audio-level').exists()).toBe(false)
    wrapper.unmount()
  })
})

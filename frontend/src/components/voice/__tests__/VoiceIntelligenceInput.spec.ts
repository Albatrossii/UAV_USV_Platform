import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { ApiClientError } from '@/api/http'
import type { VoiceIntelligenceAdapter } from '@/types/voiceIntelligence'

import VoiceIntelligenceInput from '@/components/voice/VoiceIntelligenceInput.vue'
import { createMockVoiceIntelligenceAdapter } from '@/services/voiceIntelligence'

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

  it('preserves automatic execution for the exact local-rules STOP phrase', async () => {
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

    expect(wrapper.emitted('voiceCandidate')).toEqual([['MISSION_STOP', expect.any(String), expect.objectContaining({
      startedAt: expect.any(Number), asrRequestMs: expect.any(Number), parseMs: expect.any(Number),
    })]])
    expect(wrapper.find('.candidate').exists()).toBe(false)
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
    expect(wrapper.text()).toContain('正在校验目标和动作并自动提交至本地仿真')
    expect(wrapper.find('.candidate').exists()).toBe(false)
    wrapper.unmount()
  })

  it('automatically submits a validated single-device text command in local simulation', async () => {
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
    expect(wrapper.emitted('voiceCandidate')).toEqual([['SINGLE_DEVICE_CONTROL', expect.any(String), expect.objectContaining({
      asrRequestMs: 0, parseMs: expect.any(Number),
    })]])
    expect(wrapper.text()).toContain('正在校验目标和动作并自动提交至本地仿真')
    expect(wrapper.find('.candidate').exists()).toBe(false)
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

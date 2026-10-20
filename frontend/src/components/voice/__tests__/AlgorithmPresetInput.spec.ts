import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi } from 'vitest'
import VoiceIntelligenceInput from '../VoiceIntelligenceInput.vue'
import { validPreset } from '@/services/algorithmPresets'
import { sequenceStateValid } from '@/utils/voiceSequence'
import type { VoiceIntelligenceAdapter } from '@/types/voiceIntelligence'

describe('algorithm preset input', () => {
  it('shows numbered speech examples only for the command-enabled algorithm', async () => {
    const wrapper = mount(VoiceIntelligenceInput, { props: { allowedActions: ['START'], deviceCodes: ['UAV-001'], refined: true, algorithmPresetsEnabled: true } })
    expect(wrapper.text()).toContain('开始 → 第1号指令')
    const example = wrapper.findAll('.refined-examples button').find(button => button.text().includes('第13号'))!
    await example.trigger('click')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('执行第十三号指令。')
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    expect(wrapper.emitted('candidate')).toBeUndefined()
    await wrapper.setProps({ algorithmPresetsEnabled: false })
    expect(wrapper.text()).toContain('开始任务 → 一号机悬停')
    expect(wrapper.text()).not.toContain('第13号：无人机分组')
    wrapper.unmount()
  })

  it('routes a numeric button through parsing and the existing confirmation path', async () => {
    const adapter: VoiceIntelligenceAdapter = {
      name: 'platform-backend', mode: 'BACKEND', transcribe: vi.fn(),
      parse: async input => ({status:'CANDIDATE',requestId:input.requestId,normalizedText:input.text,
        intent:'ALGORITHM_PRESET',action:'ALGORITHM_PRESET',parameters:{presetId:13,presetVersion:'escort-20261020'},
        confidence:null,provider:'local-rules',model:'rules-v1'}),
    }
    const wrapper = mount(VoiceIntelligenceInput,{props:{adapter,allowedActions:['START','PAUSE','RESUME','STOP'],deviceCodes:['UAV-001'],refined:true}})
    await wrapper.vm.presetText('执行13号指令')
    await flushPromises()
    expect(wrapper.emitted('candidate')?.[0]?.[0]).toBe('ALGORITHM_PRESET')
    expect(wrapper.emitted('voiceCandidate')).toBeUndefined()
    wrapper.unmount()
  })
  it('rejects unknown versions and refuses algorithm steps in paused or stopped runs', () => {
    expect(validPreset({presetId:20,presetVersion:'escort-20261020'})).toBe(true)
    expect(validPreset({presetId:'20',presetVersion:'escort-20261020'})).toBe(false)
    expect(validPreset({presetId:20,presetVersion:'other'})).toBe(false)
    const steps = [{index:0,action:'START' as const},{index:1,action:'ALGORITHM_PRESET' as const,parameters:{presetId:13,presetVersion:'escort-20261020' as const}}]
    expect(sequenceStateValid(steps,'PREPARED')).toBe(true)
    expect(sequenceStateValid([{index:0,action:'PAUSE'},steps[1]!],'RUNNING')).toBe(false)
  })
})

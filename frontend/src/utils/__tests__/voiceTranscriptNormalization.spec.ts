import { describe, expect, it } from 'vitest'
import { normalizeVoiceDeviceTerms, repairContextualUsvHold } from '@/utils/voiceTranscriptNormalization'

describe('voice transcript device-term normalization', () => {
  it.each([
    ['一号无人机返航，然后让二号无人船驻留', '一号无人机返航，然后让二号无人艇驻留'],
    ['一号无人船返航，二号无人船驻留', '一号无人艇返航，二号无人艇驻留'],
    ['开始任务后二号无人船驻留', '开始任务后二号无人艇驻留'],
    ['先让一号无人船返航，再让二号无人船驻留', '先让一号无人艇返航，再让二号无人艇驻留'],
    ['一号无人船返航，等待三秒，然后二号无人船驻留', '一号无人艇返航，等待三秒，然后二号无人艇驻留'],
  ])('repairs complete boat clauses while preserving scheduling: %s', (text, expected) => {
    expect(normalizeVoiceDeviceTerms(text)).toBe(expected)
    expect(normalizeVoiceDeviceTerms(expected)).toBe(expected)
  })
  it.each([
    ['让一号艇无人驻留。', '让一号无人艇驻留。'],
    ['二号机无人悬停', '二号无人机悬停'],
    ['一号无人庭归队', '一号无人艇归队'],
    ['一号无人廷返航', '一号无人艇返航'],
    ['一号无人停驻留', '一号无人艇驻留'],
    ['三号无人鸡悬停', '三号无人机悬停'],
    ['三号无人基归队', '三号无人机归队'],
  ])('normalizes %s', (input, expected) => {
    expect(normalizeVoiceDeviceTerms(input)).toBe(expected)
  })

  it('does not broaden unrelated or already canonical text', () => {
    expect(normalizeVoiceDeviceTerms('一号无人艇驻留，二号无人机继续')).toBe('一号无人艇驻留，二号无人机继续')
    expect(normalizeVoiceDeviceTerms('请停止当前任务')).toBe('请停止当前任务')
  })

  it.each([
    ['一号无人挺住。留。', '一号无人艇驻留。'],
    ['请让二号无人艇住，留。', '请让二号无人艇驻留。'],
    ['让第3号无人挺驻留', '让第3号无人艇驻留'],
    ['一号无人艇驻。 留。', '一号无人艇驻留。'],
    ['一号无人挺住留', '一号无人艇驻留'],
    ['二号无人船主流。', '二号无人艇驻留。'],
    ['二号无人听驻流', '二号无人艇驻留'],
    ['二号无人停驻流', '二号无人艇驻留'],
    ['请让第二号无人听注。流。', '请让第二号无人艇驻留。'],
    ['让第3号无人艇住流', '让第3号无人艇驻留'],
    ['2号无人船驻留', '2号无人艇驻留'],
  ])('repairs only a complete numbered boat hold: %s', (input, expected) => {
    expect(normalizeVoiceDeviceTerms(input)).toBe(expected)
    expect(normalizeVoiceDeviceTerms(expected)).toBe(expected)
  })

  it.each([
    ['二号无人听归队', '二号无人艇归队'],
    ['请让2号无人船返航。', '请让2号无人艇返航。'],
    ['一号无人听停止', '一号无人艇停止'],
    ['让第3号无人船待命', '让第3号无人艇待命'],
  ])('repairs a boat noun only within a complete explicit command: %s', (input, expected) => {
    expect(normalizeVoiceDeviceTerms(input)).toBe(expected)
    expect(normalizeVoiceDeviceTerms(expected)).toBe(expected)
  })

  it.each([
    '这是无人船主流技术', '二号无人船主流技术', '现在无人听我说话', '无人听归队',
    '无人船主流', '二号设备主流', '二号无人机主流', '二号无人听',
    '不要让二号无人船主流', '不要让二号无人听归队', '二号无人船主流？',
    '二号无人听归队吗', '如果二号无人听返航', '报告二号无人船主流',
    '二号和三号无人船主流', '二号无人船主流返航', '二号无人听驻留或者归队',
    '开始任务，然后二号无人船主流', '开始任务，然后二号无人听归队',
  ])('does not expand ambiguous or conversational new aliases: %s', input => {
    expect(normalizeVoiceDeviceTerms(input)).toBe(input)
  })

  it('never turns a boat stop into a whole-mission stop or a UAV command', () => {
    expect(normalizeVoiceDeviceTerms('二号无人停停止')).toBe('二号无人艇停止')
    expect(normalizeVoiceDeviceTerms('二号无人听停止')).toBe('二号无人艇停止')
    expect(normalizeVoiceDeviceTerms('停止任务')).toBe('停止任务')
  })

  it.each([
    '挺住', '无人挺住。留。', '一号设备住留', '一号无人机住。留。',
    '不要让一号无人挺住。留。', '一号无人挺住。留？', '一号无人挺住留吗',
    '如果一号无人挺住留', '一号无人挺住留或者返航', '一号无人挺住留返航',
    '一号无人挺归队', '一号无人挺返航', '一号无人挺住',
    '开始任务，然后一号无人挺住。留。', '一号无人挺住留，然后二号无人艇驻留',
    '报告一号无人挺住留', '一号和二号无人挺住留', '一号无人挺住。不要。留。',
  ])('never repairs an ambiguous or out-of-context fragment: %s', input => {
    expect(repairContextualUsvHold(input)).toBe(input)
  })
})

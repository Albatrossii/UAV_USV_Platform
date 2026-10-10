import { describe, expect, it } from 'vitest'
import { normalizeVoiceDeviceTerms, repairContextualUsvHold } from '@/utils/voiceTranscriptNormalization'

describe('voice transcript device-term normalization', () => {
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
  ])('repairs only a complete numbered boat hold: %s', (input, expected) => {
    expect(normalizeVoiceDeviceTerms(input)).toBe(expected)
    expect(normalizeVoiceDeviceTerms(expected)).toBe(expected)
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

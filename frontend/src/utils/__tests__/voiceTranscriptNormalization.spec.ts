import { describe, expect, it } from 'vitest'
import { normalizeVoiceDeviceTerms } from '@/utils/voiceTranscriptNormalization'

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
})

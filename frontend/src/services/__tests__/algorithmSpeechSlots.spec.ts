import { afterEach, describe, expect, it } from 'vitest'
import { algorithmPresets, presetMatchesText, repairAlgorithmSpeech } from '../algorithmPresets'

const nouns = ['的船', '的穿', '的喘', '地船', '底川', '敌穿', '滴船', 'd穿', 'D船', 'di喘', 'di chuan', 'ＤＩＣＨＵＡＮ', '敌chuan', '机船']
const params = (presetId: number) => ({ presetId, presetVersion: 'escort-20261020' })

describe('numbered command target speech slots', () => {
  afterEach(() => { algorithmPresets.value = [] })

  it.each(nouns)('resolves %s for all twelve chases without changing identity', noun => {
    const ids = { 无人机: [[1, 7], [9, 3], [5, 11]], 无人艇: [[2, 8], [10, 4], [12, 6]] }
    for (const [type, table] of Object.entries(ids)) {
      for (let unit = 1; unit <= 3; unit++) for (let target = 1; target <= 2; target++) {
        const text = `请让第${unit}号${type}追击第${target}号${noun}。`
        expect(repairAlgorithmSpeech(text)).toBe(`请让第${unit}号${type}追击第${target}号敌船。`)
        for (let id = 0; id <= 20; id++) {
          expect(presetMatchesText(text, params(id)), `${text} -> ${id}`).toBe(id === table[unit - 1]![target - 1])
        }
      }
    }
  })

  it.each(nouns)('resolves %s in all six binding groups', noun => {
    const labels = ['将一二三号无人机分配给一号敌船', '将一二三号无人艇分配给二号敌船',
      '将一二三号无人艇分配给一号敌船', '将一二三号无人机分配给二号敌船',
      '将一二号无人机和一号无人艇分配给一号敌船', '将二三号无人艇和三号无人机分配给二号敌船']
    algorithmPresets.value = labels.map((label, index) => ({ id: 13 + index, label, command: '', effect: '' }))
    labels.forEach((label, index) => {
      const text = label.replace('敌船', noun)
      expect(repairAlgorithmSpeech(text)).toBe(label)
      expect(presetMatchesText(text, params(13 + index))).toBe(true)
      expect(presetMatchesText(text, params(13 + (index + 1) % 6))).toBe(false)
    })
  })

  it.each(['不要一号无人机追击一号d穿', '一号无人机追击一号d穿吗？', '如果一号无人机追击一号d穿',
    '一号无人机追击一号d穿然后停止', '一号无人机追击一号d穿或者二号敌船',
    '一号无人机追击三号d穿', '四号无人机追击一号d穿', '一号无人机追击d穿',
    '一号无人机追击一号客船', '一号无人机追击一号货船', '一号无人机追击一号护航船',
    '一号无人机追击一号地图', '一号无人机追击一号d', '一号无人鸡追击一号d穿',
    '将一二三号无人艇分配给三号d穿', '不要将一二三号无人艇分配给二号d穿'])('does not guess unresolved meaning: %s', text => {
    expect(repairAlgorithmSpeech(text)).toBe(text.normalize('NFKC'))
    for (let id = 0; id <= 20; id++) expect(presetMatchesText(text, params(id))).toBe(false)
  })
})

import { shallowRef } from 'vue'
import { http } from '@/api/http'

export interface AlgorithmPresetParameters { presetId: number; presetVersion: 'escort-20261020' }
export interface AlgorithmPreset { id: number; command: string; label: string; effect: string }
export const algorithmPresets = shallowRef<AlgorithmPreset[]>([])
export async function loadAlgorithmPresets() {
  const response = await http.get('/voice/algorithm-presets')
  const data = response.data.data
  if (data.version !== 'escort-20261020' || !Array.isArray(data.items)) throw new Error('指令版本不匹配')
  algorithmPresets.value = data.items
}
export function validPreset(value: unknown): value is AlgorithmPresetParameters {
  if (!value || typeof value !== 'object') return false
  const p = value as Record<string, unknown>
  return Object.keys(p).length === 2 && p.presetVersion === 'escort-20261020'
    && Number.isInteger(p.presetId) && Number(p.presetId) >= 0 && Number(p.presetId) <= 20
}
export function presetLabel(p?: AlgorithmPresetParameters) {
  return algorithmPresets.value.find(item => item.id === p?.presetId)?.label ?? '算法动作'
}

/** Friendly nodes addressed by the fixed algorithm command, not the whole receipt fleet. */
export function presetDeviceCodes(parameters: unknown): string[] {
  if (!validPreset(parameters)) return []
  const id = parameters.presetId
  const air = ['UAV-001', 'UAV-002', 'UAV-003']
  const sea = ['USV-001', 'USV-002', 'USV-003']
  const chase = ['UAV-001', 'USV-001', 'UAV-002', 'USV-002', 'UAV-003', 'USV-003',
    'UAV-001', 'USV-001', 'UAV-002', 'USV-002', 'UAV-003', 'USV-003']
  if (id >= 1 && id <= 12) return [chase[id - 1]!]
  if (id === 13 || id === 16) return air
  if (id === 14 || id === 15) return sea
  if (id === 17) return ['UAV-001', 'USV-001', 'UAV-002']
  if (id === 18) return ['USV-002', 'UAV-003', 'USV-003']
  return [...air, ...sea]
}

// Two spoken syllables, including mixed Chinese/pinyin ASR output. Only use
// inside a complete numbered target slot; never fuzzy-match IDs or unit types.
// Keep aligned with AlgorithmPresets.ENEMY_VESSEL_SPEECH.
const enemyVesselSpeech = '(?:(?:[敌的地底低滴迪笛狄堤帝弟递抵谛]|[dD](?:[iI])?)(?:[船穿川传傳喘串]|[cC][hH][uU][aA][nN])|机船)'
const chaseSpeech = new RegExp(`^((?:请)?(?:让)?第?[一二三123]号(?:无人机|无人艇)追击第?[一二12]号)${enemyVesselSpeech}([。！!，,]*)$`, 'u')
const bindingSpeech = new RegExp(`^((?:请)?将(?:一二三号无人[机艇]|一二号无人机和一号无人艇|二三号无人艇和三号无人机)分配给[一二]号)${enemyVesselSpeech}([。！!，,]*)$`, 'u')

/** Resolve the target noun only after the complete command structure matches. */
export function repairChaseSpeech(text: string): string {
  // User-confirmed full ASR phrase only: do not turn arbitrary aircraft counts into IDs.
  return text.normalize('NFKC').replace(/\s+/gu, '')
    .replace(/^(?:请)?让一架无人机追击一号敌船([。！!，,]*)$/u, '一号无人机追击一号敌船$1').replace(
    chaseSpeech,
    '$1敌船$2',
  )
}

/** Whole-command repairs shared by matching and automatic response validation. */
export function repairAlgorithmSpeech(text: string): string {
  return repairChaseSpeech(text)
    .replace(bindingSpeech, '$1敌船$2')
    .replace(/^(请)?恢复首位([。！!，,]*)$/u, '$1恢复守卫$2')
}

/** Verify a spoken preset against its full clause before automatic submission. */
export function presetMatchesText(text: string, parameters: unknown): boolean {
  if (!validPreset(parameters)) return false
  const clean = (value: string) => value.normalize('NFKC').replace(/\s+/gu, '').replace(/[。!，,]+$/u, '')
  const raw = clean(repairAlgorithmSpeech(text)).replace(/^(?:请)?(?:让)?/u, '')
  // Keep this full-utterance grammar aligned with AlgorithmPresets.parse.
  if (/^(?:启动|执行|开始)?围捕[,;。]?(?:并|然后)?(?:观察|查看|观看)(?:一下)?(?:全过程|整个过程|全程)$/u.test(raw)) {
    return parameters.presetId === 20
  }
  if (algorithmPresets.value.some(item => item.id === parameters.presetId
    && (raw === clean(item.label) || raw === clean(item.label).replace(/，/gu, '')))) return true
  const aliases: Record<string, number> = {
    恢复护航: 0, 全体撤退: 0, 撤回护航: 0, 恢复守卫: 19, 自动守卫: 19, 形成守卫阵型: 19,
    启动围捕: 20, 执行围捕: 20, 开始围捕: 20, 形成守卫队形: 19,
  }
  if (Object.prototype.hasOwnProperty.call(aliases, raw)) return aliases[raw] === parameters.presetId
  // Match the same device/target variants accepted by the backend, so ASR's
  // Arabic digits or “目标” do not force a correctly parsed action to manual review.
  const chase = raw.match(/^第?([一二三123])号(无人机|无人艇)追击第?([一二12])号(?:敌船|目标|敌方目标)$/u)
  if (chase) {
    const number = (value: string) => /^[123]$/u.test(value) ? Number(value) : '零一二三'.indexOf(value)
    const unit = number(chase[1]!)
    const target = number(chase[3]!)
    const ids = chase[2] === '无人机' ? [[1, 7], [9, 3], [5, 11]] : [[2, 8], [10, 4], [12, 6]]
    return ids[unit - 1]?.[target - 1] === parameters.presetId
  }
  const numbered = raw.replace(/^(?:麻烦|劳驾)?(?:请)?(?:帮我|帮忙)?(?:现在|立即|马上)?(?:开始执行|执行|运行|启动|下达)?/u, '')
    .replace(/(?:一下)?(?:吧)?$/u, '')
  const numeral = '[零〇一二两兩三四五六七八九十壹贰貳叁參肆伍陆陸柒捌玖拾IVXLCDMivxlcdm0-9]+'
  const match = numbered.match(new RegExp(`^(?:第(${numeral})(?:号|条|个)?|(${numeral})(?:号|条)?)(?:指令|命令)?$`, 'u'))
    ?? numbered.match(new RegExp(`^(?:指令|命令)(?:编号)?(?:为|是|:)?第?(${numeral})(?:号|条|个)?$`, 'u'))
    ?? numbered.match(new RegExp(`^编号(?:为|是|:)?第?(${numeral})(?:号|条|个)?(?:的)?(?:指令|命令)$`, 'u'))
  if (!match) return false
  const from = '〇两兩壹贰貳叁參肆伍陆陸柒捌玖拾'
  const to = '零二二一二二三三四五六六七八九十'
  let token = [...(match[1] ?? match[2]!)].map(char => from.includes(char) ? to[from.indexOf(char)] : char).join('')
  const roman = ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X',
    'XI', 'XII', 'XIII', 'XIV', 'XV', 'XVI', 'XVII', 'XVIII', 'XIX', 'XX']
  let id = -1
  if (/^\d{1,3}$/u.test(token)) id = Number(token)
  else if (roman.includes(token.toUpperCase())) id = roman.indexOf(token.toUpperCase())
  else if (token === '二十') id = 20
  else {
    token = token.replace(/^一十/u, '十')
    if (/^十[一二三四五六七八九]?$/u.test(token)) id = 10 + (token.length === 1 ? 0 : '零一二三四五六七八九'.indexOf(token[1]!))
    else if (token.length === 1) id = '零一二三四五六七八九'.indexOf(token)
  }
  return id === parameters.presetId
}

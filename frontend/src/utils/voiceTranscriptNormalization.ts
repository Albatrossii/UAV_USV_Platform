/**
 * Conservative ASR repairs for unmistakable UAV/USV device nouns.
 * Keep this list aligned with IntentService.normalize so the browser's
 * automatic-execution guard validates the same text the backend parsed.
 */
const DEVICE_TERM_CORRECTIONS: ReadonlyArray<readonly [string, string]> = [
  ['艇无人', '无人艇'],
  ['机无人', '无人机'],
  ['无人庭', '无人艇'],
  ['无人廷', '无人艇'],
  ['无人停', '无人艇'],
  ['无人鸡', '无人机'],
  ['无人基', '无人机'],
]

// Whole, affirmative, single-device HOLD command only. Never repair fragments
// inside negations, questions, conversation, sequences or route-changing actions.
const CONTEXTUAL_USV_HOLD = /^((?:请)?(?:让)?第?(?:[一二三四五六七八九十]|[0-9]{1,3})号)无人[艇挺庭廷停][驻住][。．.,，]{0,2}留([。！!，,]*)$/u

export function repairContextualUsvHold(text: string) {
  const match = text.replace(/\s+/gu, '').match(CONTEXTUAL_USV_HOLD)
  return match ? `${match[1]}无人艇驻留${match[2]}` : text
}

export function normalizeVoiceDeviceTerms(text: string) {
  return DEVICE_TERM_CORRECTIONS.reduce(
    (value, [source, replacement]) => value.split(source).join(replacement),
    repairContextualUsvHold(text),
  ).replace(/(第?[一二三四五六七八九十\d]+)(?:号|架)(?:飞机|机)(?!无人)/gu, '$1号无人机')
    .replace(/(第?[一二三四五六七八九十\d]+)(?:号|艘)(?:船|艇)(?!无人)/gu, '$1号无人艇')
}

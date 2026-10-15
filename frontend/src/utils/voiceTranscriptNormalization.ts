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
const CONTEXTUAL_USV_HOLD = /^((?:请)?(?:让)?第?(?:[一二三四五六七八九十]|[0-9]{1,3})号)无人[艇挺庭廷停听船][驻住注主][。．.,，]{0,2}[留流]([。！!，,]*)$/u

// “无人听” is also ordinary language, and “主流” is not inherently an action.
// Repair new boat aliases only when a complete numbered command identifies
// both its type and action. Do not guess a missing action, number or clause.
const CONTEXTUAL_USV_COMMAND = /^((?:请)?(?:让)?第?(?:[一二三四五六七八九十]|[0-9]{1,3})号)无人[听船](驻留|待命|保持|暂停|归队|继续|返航|返回|停止|停船)([。！!，,]*)$/u

// Exact numbered boat clauses may appear in single or multi-step commands.
// Keep separators and unsupported text intact; do not broaden “无人听/主流”.
const CONTEXTUAL_BOAT_CLAUSE = /(^|[，,。；;]|然后|接着|之后|以后|再|(?:开始(?:执行|当前)?任务|启动(?:当前)?任务|执行任务)后?)((?:请)?(?:先)?(?:让)?第?(?:[一二三四五六七八九十]|[0-9]{1,3})号)无人船(驻留|待命|保持|暂停|归队|继续|返航|返回|停止|停船)(?=$|[。！!，,；;]|然后|接着|之后|以后|再)/gu

export function repairContextualUsvHold(text: string) {
  const match = text.replace(/\s+/gu, '').match(CONTEXTUAL_USV_HOLD)
  return match ? `${match[1]}无人艇驻留${match[2]}` : text
}

export function normalizeVoiceDeviceTerms(text: string) {
  const boatClauses = text.replace(/\s+/gu, '').replace(CONTEXTUAL_BOAT_CLAUSE, '$1$2无人艇$3')
  const repairedHold = repairContextualUsvHold(boatClauses)
  const command = repairedHold.replace(/\s+/gu, '').match(CONTEXTUAL_USV_COMMAND)
  const contextual = command ? `${command[1]}无人艇${command[2]}${command[3]}` : repairedHold
  return DEVICE_TERM_CORRECTIONS.reduce(
    (value, [source, replacement]) => value.split(source).join(replacement),
    contextual,
  ).replace(/(第?[一二三四五六七八九十\d]+)(?:号|架)(?:飞机|机)(?!无人)/gu, '$1号无人机')
    .replace(/(第?[一二三四五六七八九十\d]+)(?:号|艘)(?:船|艇)(?!无人)/gu, '$1号无人艇')
}

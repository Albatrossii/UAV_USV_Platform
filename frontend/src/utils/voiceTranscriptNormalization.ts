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

export function normalizeVoiceDeviceTerms(text: string) {
  return DEVICE_TERM_CORRECTIONS.reduce(
    (value, [source, replacement]) => value.split(source).join(replacement),
    text,
  )
}

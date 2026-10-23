/**
 * The caller owns end-of-speech: pauses and microphone volume are not reliable
 * evidence that a command is complete. Only an explicit stop or this hard limit
 * may submit a recording to ASR.
 */
// Aliyun rejects decoded audio longer than 60 seconds. Reserve one second for
// recorder encoding and ordinary event-loop scheduling overhead.
export const SPEECH_RECORDING_MAX_MS = 59_000
export const SPEECH_RECORDING_HINT = '请完整说出指令，说完后点击停止录音；停顿不会自动结束，单次最长 59 秒。'

export type SpeechRecordingStopReason = 'MANUAL' | 'MAX_DURATION'

export interface SpeechRecordingDecision {
  stopReason: SpeechRecordingStopReason | null
  remainingMs: number
}

export function evaluateSpeechRecording(input: {
  elapsedMs: number
  manualStopRequested?: boolean
}): SpeechRecordingDecision {
  const elapsedMs = Number.isNaN(input.elapsedMs) ? 0 : Math.max(0, input.elapsedMs)
  const remainingMs = Math.max(0, SPEECH_RECORDING_MAX_MS - elapsedMs)

  return {
    stopReason: input.manualStopRequested
      ? 'MANUAL'
      : elapsedMs >= SPEECH_RECORDING_MAX_MS ? 'MAX_DURATION' : null,
    remainingMs,
  }
}

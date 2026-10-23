import { describe, expect, it } from 'vitest'
import {
  evaluateSpeechRecording,
  SPEECH_RECORDING_HINT,
  SPEECH_RECORDING_MAX_MS,
} from '../speechRecordingPolicy'

describe('speech recording policy', () => {
  it('reserves one second of encoding overhead below the strict 60 second ASR limit', () => {
    expect(SPEECH_RECORDING_MAX_MS).toBe(59_000)
    expect(60_000 - SPEECH_RECORDING_MAX_MS).toBe(1_000)
    expect(evaluateSpeechRecording({ elapsedMs: 0 })).toEqual({
      stopReason: null,
      remainingMs: 59_000,
    })
  })

  it.each([700, 1_500, 5_000, 6_000, 12_000, 30_000, 58_999])(
    'does not infer end-of-speech from time or a pause at %i ms',
    (elapsedMs) => {
      // Deliberately no speech-level/silence input: soft speech, natural pauses,
      // and thinking between two command steps must never stop the recording.
      expect(evaluateSpeechRecording({ elapsedMs })).toEqual({
        stopReason: null,
        remainingMs: 59_000 - elapsedMs,
      })
    },
  )

  it('keeps recording through a 700 ms pause and a subsequent long pause', () => {
    const elapsedSamples = [0, 1_000, 1_700, 2_000, 14_000, 15_000]
    expect(elapsedSamples.map(elapsedMs => evaluateSpeechRecording({ elapsedMs }).stopReason))
      .toEqual(elapsedSamples.map(() => null))
  })

  it.each([59_000, 59_001, 75_000, Number.POSITIVE_INFINITY])(
    'stops at or after the hard limit (%i ms), even without a manual stop',
    (elapsedMs) => {
      expect(evaluateSpeechRecording({ elapsedMs })).toEqual({
        stopReason: 'MAX_DURATION',
        remainingMs: 0,
      })
    },
  )

  it.each([0, 700, 6_000, 12_000, 58_999, 59_000])(
    'honors an explicit manual stop at %i ms, including both time boundaries',
    (elapsedMs) => {
      expect(evaluateSpeechRecording({ elapsedMs, manualStopRequested: true })).toEqual({
        stopReason: 'MANUAL',
        remainingMs: Math.max(0, 59_000 - elapsedMs),
      })
    },
  )

  it.each([-1, Number.NaN, Number.NEGATIVE_INFINITY])(
    'does not produce an invalid countdown for elapsed value %s',
    (elapsedMs) => {
      expect(evaluateSpeechRecording({ elapsedMs })).toEqual({
        stopReason: null,
        remainingMs: 59_000,
      })
    },
  )

  it('tells the user how to finish and explains the time limit', () => {
    expect(SPEECH_RECORDING_HINT).toContain('点击停止录音')
    expect(SPEECH_RECORDING_HINT).toContain('停顿不会自动结束')
    expect(SPEECH_RECORDING_HINT).toContain('59 秒')
  })
})

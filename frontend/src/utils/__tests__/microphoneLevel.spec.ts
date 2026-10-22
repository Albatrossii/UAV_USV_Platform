import { afterEach, describe, expect, it, vi } from 'vitest'
import { monitorMicrophoneLevel } from '@/utils/microphoneLevel'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

function audioContext(state = 'running') {
  let amplitude = 0
  const source = { connect: vi.fn(), disconnect: vi.fn() }
  const analyser = {
    fftSize: 0, disconnect: vi.fn(),
    getFloatTimeDomainData: vi.fn((data: Float32Array) => data.fill(amplitude)),
  }
  const context = {
    state, createMediaStreamSource: vi.fn(() => source), createAnalyser: vi.fn(() => analyser),
    resume: vi.fn(async () => { context.state = 'running' }),
    close: vi.fn(async () => { context.state = 'closed' }),
  }
  vi.stubGlobal('AudioContext', class { constructor() { return context } })
  return { context, analyser, source, volume: (value: number) => { amplitude = value } }
}

describe('microphone volume diagnostics', () => {
  it('measures silence and sound, without connecting capture to speakers or stopping tracks', () => {
    vi.useFakeTimers()
    const audio = audioContext()
    const track = { stop: vi.fn() }
    const stream = { getTracks: () => [track] } as unknown as MediaStream
    const update = vi.fn()
    const stop = monitorMicrophoneLevel(stream, update)
    expect(update).toHaveBeenLastCalledWith(0)
    audio.volume(.1)
    vi.advanceTimersByTime(100)
    expect(update).toHaveBeenLastCalledWith(67)
    audio.volume(1)
    vi.advanceTimersByTime(100)
    expect(update).toHaveBeenLastCalledWith(100)
    expect(audio.source.connect).toHaveBeenCalledExactlyOnceWith(audio.analyser)
    stop()
    stop()
    const calls = update.mock.calls.length
    vi.advanceTimersByTime(1000)
    expect(update).toHaveBeenCalledTimes(calls)
    expect(audio.context.close).toHaveBeenCalledOnce()
    expect(audio.source.disconnect).toHaveBeenCalledOnce()
    expect(track.stop).not.toHaveBeenCalled()
  })

  it('reports unavailable rather than silence when Web Audio is unsupported', () => {
    vi.stubGlobal('AudioContext', undefined)
    vi.stubGlobal('webkitAudioContext', undefined)
    const update = vi.fn()
    expect(() => monitorMicrophoneLevel({} as MediaStream, update)()).not.toThrow()
    expect(update).toHaveBeenCalledExactlyOnceWith(null)
  })

  it('resumes a suspended analyser without reporting false silence', async () => {
    vi.useFakeTimers()
    const audio = audioContext('suspended')
    const update = vi.fn()
    const stop = monitorMicrophoneLevel({} as MediaStream, update)
    expect(update).toHaveBeenLastCalledWith(null)
    expect(audio.context.resume).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(100)
    expect(update).toHaveBeenLastCalledWith(0)
    stop()
  })

  it('degrades gracefully if the audio context cannot be created', () => {
    vi.stubGlobal('AudioContext', class { constructor() { throw new Error('unsupported device') } })
    const update = vi.fn()
    expect(() => monitorMicrophoneLevel({} as MediaStream, update)()).not.toThrow()
    expect(update).toHaveBeenCalledExactlyOnceWith(null)
  })
})

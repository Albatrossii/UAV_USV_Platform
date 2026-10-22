/** Observe capture volume locally; never gate, stop or upload the recording. */
export function monitorMicrophoneLevel(stream: MediaStream, update: (level: number | null) => void): () => void {
  const Context = globalThis.AudioContext
    ?? (globalThis as typeof globalThis & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Context) {
    update(null)
    return () => {}
  }
  let context: AudioContext | undefined
  let source: MediaStreamAudioSourceNode | undefined
  let analyser: AnalyserNode | undefined
  let timer: ReturnType<typeof setInterval> | undefined
  let stopped = false
  const stop = () => {
    if (stopped) return
    stopped = true
    clearInterval(timer)
    // Diagnostics must never prevent the recorder from releasing or submitting.
    try { source?.disconnect() } catch { /* Node already detached. */ }
    try { analyser?.disconnect() } catch { /* Node already detached. */ }
    try {
      if (context && context.state !== 'closed') void context.close().catch(() => {})
    } catch { /* Context already disposed. */ }
  }
  try {
    context = new Context()
    source = context.createMediaStreamSource(stream)
    analyser = context.createAnalyser()
    analyser.fftSize = 1024
    source.connect(analyser) // Do not connect to speakers: no microphone feedback.
    const samples = new Float32Array(analyser.fftSize)
    const sample = () => {
      if (stopped) return
      if (context!.state !== 'running') { update(null); return }
      analyser!.getFloatTimeDomainData(samples)
      const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length)
      // Map -60..0 dBFS to 0..100. This is a volume meter, not speech detection.
      update(Math.round(Math.max(0, Math.min(100, (20 * Math.log10(Math.max(rms, 1e-6)) + 60) / 60 * 100))))
    }
    sample()
    timer = setInterval(sample, 100)
    if (context.state === 'suspended') void context.resume().catch(() => {
      if (!stopped) { stop(); update(null) }
    })
    return stop
  } catch {
    stop()
    update(null)
    return () => {}
  }
}

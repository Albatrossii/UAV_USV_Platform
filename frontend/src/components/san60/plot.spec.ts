import { describe, expect, it } from 'vitest'
import { createView, scenePlot, spectrumPlot } from './plot'
import type { San60State } from '@/types/san60'

const state = {
  band: [2460, 2464],
  latest: { time: 123, frequencies: [2460, 2461], powers: [-90, -70] },
  spatial: { voxel_size: .01, span: [.01, .02, .01],
    track: [{ p: [.01, .02, .03], dbm: -70, time: 123 }], cells: [[.01, .02, .03, -70]], field: [[.02, .02, .03, -75]],
    hotspot: { p: [.01, .02, .03], dbm: -70 }, source: null, reason: '覆盖不足' },
} as San60State
describe('SAN60 plot adaptation', () => {
  it('converts metres to centimetres for rendering without modifying the measurement snapshot', () => {
    const before = JSON.stringify(state)
    const plot = scenePlot(state, { measured: true, field: true, track: true, opacity: .2 }, createView())
    const track = plot.traces.find(trace => trace.uid === 'measurement-track')!
    expect(track.x).toEqual([1]); expect(track.y).toEqual([2]); expect(track.z).toEqual([3])
    const measured = plot.traces.find(trace => trace.name === '实测强度体素')!
    expect((measured.x as number[])[0]).toBeCloseTo(.66)
    expect(plot.traces.some(trace => trace.name === '近邻插值体素')).toBe(true)
    expect(plot.traces.some(trace => trace.name === '模型源位置估计')).toBe(false)
    expect(JSON.stringify(state)).toBe(before)
  })
  it('keeps user camera and centimetre bounds through repeated updates and resets only explicitly', () => {
    const view = createView(), camera = { eye: { x: .9, y: -1.4, z: .6 } }
    view.capture({ 'scene.camera': camera, 'scene.xaxis.range': [-123, 456] })
    for (let i = 0; i < 100; i++) {
      const scene = view.scene() as Record<string, any>
      expect(scene.camera).toEqual(camera)
      expect(scene.xaxis.range[0]).toBeCloseTo(-123, 10); expect(scene.xaxis.range[1]).toBeCloseTo(456, 10)
      view.capture({ 'scene.xaxis.range': scene.xaxis.range })
    }
    view.fit([[0, 0, 0], [.01, .02, .01]])
    expect((view.scene() as Record<string, any>).xaxis.range[1]).toBeLessThan(10)
  })
  it('honours layer controls and preserves original spectrum MHz and dBm', () => {
    const plot = scenePlot(state, { measured: false, field: false, track: false, opacity: .2 }, createView())
    expect(plot.traces.map(trace => trace.name)).toEqual(['测量热点', '当前探测仪'])
    expect(spectrumPlot(state).traces[0]!.x).toEqual([2460, 2461])
    expect(spectrumPlot(state).traces[0]!.y).toEqual([-90, -70])
  })
  it('labels synthetic traces as simulation data', () => {
    const plot = scenePlot({ ...state, mode: 'demo' }, { measured: true, field: true, track: true, opacity: .2 }, createView())
    expect(plot.traces.find(trace => trace.uid === 'measurement-track')!.name).toBe('模拟轨迹（合成）')
    expect(plot.traces.some(trace => trace.name === '实测强度体素')).toBe(false)
    const stopped = scenePlot({ ...state, mode: 'idle', latest: { ...state.latest!, source: 'simulation' } },
      { measured: true, field: true, track: true, opacity: .2 }, createView())
    expect(stopped.traces.find(trace => trace.uid === 'measurement-track')!.name).toBe('模拟轨迹（合成）')
  })
})

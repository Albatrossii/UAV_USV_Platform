// Adapted from the supplied app/ui_app.js and view_state.js. No field computation here.
import type { San60State } from '@/types/san60'

export type PlotObject = Record<string, unknown>
export interface PlotlyApi {
  react(target: HTMLElement, traces: PlotObject[], layout: PlotObject, config: PlotObject): Promise<void>
  purge(target: HTMLElement): void
  downloadImage(target: HTMLElement, options: PlotObject): Promise<unknown>
  Plots: { resize(target: HTMLElement): void }
}
declare global { interface Window { Plotly?: PlotlyApi } }
let loading: Promise<PlotlyApi> | undefined
export function loadPlotly(): Promise<PlotlyApi> {
  if (window.Plotly) return Promise.resolve(window.Plotly)
  if (!loading) loading = new Promise<PlotlyApi>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = '/api/san60/plotly.min.js'
    const fail = () => { script.remove(); reject(new Error('绘图库加载失败，请确认 SAN60 服务在线后点击重试')) }
    script.onerror = fail
    script.onload = () => window.Plotly ? resolve(window.Plotly) : fail()
    document.head.append(script)
  }).catch(error => { loading = undefined; throw error })
  return loading
}

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value))
export function createView() {
  let camera: Record<string, unknown> = { eye: { x: 1.55, y: 1.65, z: 1.1 }, center: { x: 0, y: 0, z: 0 }, up: { x: 0, y: 0, z: 1 } }
  let bounds = [[-30, 30], [-30, 30], [-10, 20]]
  let revision = 0
  return {
    capture(event: Record<string, unknown>) {
      if (event['scene.camera']) camera = clone(event['scene.camera']) as Record<string, unknown>
      for (const [key, value] of Object.entries(event)) {
        if (!key.startsWith('scene.camera.')) continue
        const parts = key.slice('scene.camera.'.length).split('.')
        let target = camera
        for (const part of parts.slice(0, -1)) target = (target[part] ||= {}) as Record<string, unknown>
        target[parts[parts.length - 1]!] = clone(value)
      }
      ;['xaxis', 'yaxis', 'zaxis'].forEach((axis, index) => {
        const range = event[`scene.${axis}.range`]
        if (Array.isArray(range)) bounds[index] = range.map(value => Number(value) / 100)
        for (let edge = 0; edge < 2; edge++) {
          const value = event[`scene.${axis}.range[${edge}]`]
          if (typeof value === 'number' && Number.isFinite(value)) bounds[index]![edge] = value / 100
        }
      })
    },
    fit(points: number[][], minSpan = 0.05, minVertical = minSpan) {
      const valid = points.filter(point => point.length >= 3 && point.slice(0, 3).every(Number.isFinite))
      if (valid.length) {
        const low = [0, 1, 2].map(i => Math.min(...valid.map(point => point[i]!)))
        const high = [0, 1, 2].map(i => Math.max(...valid.map(point => point[i]!)))
        const horizontal = Math.max(minSpan, high[0]! - low[0]!, high[1]! - low[1]!) * 1.3
        bounds = [horizontal, horizontal, Math.max(minVertical, high[2]! - low[2]!) * 1.3].map((length, i) => {
          const center = (low[i]! + high[i]!) / 2
          return [center - length / 2, center + length / 2]
        })
      } else bounds = [[-30, 30], [-30, 30], [-10, 20]]
      camera = { eye: { x: 1.55, y: 1.65, z: 1.1 }, center: { x: 0, y: 0, z: 0 }, up: { x: 0, y: 0, z: 1 } }
      revision++
    },
    scene() {
      const lengths = bounds.map(range => range[1]! - range[0]!)
      const longest = Math.max(...lengths)
      const axes = Object.fromEntries(['xaxis', 'yaxis', 'zaxis'].map((axis, i) => [axis, {
        title: ['东 E / cm', '北 N / cm', '上 U / cm'][i], range: bounds[i]!.map(value => value * 100),
        autorange: false, tickformat: '.0f', hoverformat: '.0f', gridcolor: '#25434d',
        zerolinecolor: '#3b6971', color: '#9ab8c1', backgroundcolor: '#081e28', showbackground: true,
      }]))
      return { ...axes, camera: clone(camera), aspectmode: 'manual',
        aspectratio: { x: lengths[0]! / longest * 1.6, y: lengths[1]! / longest * 1.6, z: lengths[2]! / longest * 1.6 },
        uirevision: 'free-view-' + revision }
    },
  }
}

const thermalScale = [[0, '#30123b'], [.15, '#4668d7'], [.3, '#29b6e9'], [.45, '#40e9a0'], [.6, '#bbe638'], [.75, '#f9ad26'], [.9, '#e1450f'], [1, '#7a0403']]
const font = { color: '#abc1c9', family: 'Microsoft YaHei,sans-serif', size: 11 }
const transparent = 'rgba(0,0,0,0)'
export const plotConfig = { responsive: true, displaylogo: false, scrollZoom: true }
export interface Layers { measured: boolean; field: boolean; track: boolean; opacity: number }

function mesh(points: number[][], size: number, name: string, opacity: number, cmin: number, cmax: number): PlotObject {
  const x: number[] = [], y: number[] = [], z: number[] = [], i: number[] = [], j: number[] = [], k: number[] = [], intensity: number[] = []
  const offsets = [[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]]
  const faces = [[0,1,2],[0,2,3],[4,6,5],[4,7,6],[0,4,5],[0,5,1],[1,5,6],[1,6,2],[2,6,7],[2,7,3],[3,7,4],[3,4,0]]
  for (const point of points) {
    const offset = x.length
    for (const vertex of offsets) {
      x.push((point[0]! + vertex[0]! * size / 2) * 100)
      y.push((point[1]! + vertex[1]! * size / 2) * 100)
      z.push((point[2]! + vertex[2]! * size / 2) * 100)
      intensity.push(point[3]!)
    }
    for (const face of faces) { i.push(offset + face[0]!); j.push(offset + face[1]!); k.push(offset + face[2]!) }
  }
  return { type: 'mesh3d', uid: name, x, y, z, i, j, k, intensity, name, colorscale: thermalScale,
    cmin, cmax, opacity, flatshading: true, showscale: false,
    hovertemplate: 'E %{x:.0f}, N %{y:.0f}, U %{z:.0f} cm<br>%{intensity:.2f} dBm<extra>' + name + '</extra>' }
}

export function scenePlot(state: San60State, layers: Layers, view: ReturnType<typeof createView>) {
  const traces: PlotObject[] = []
  const simulated = state.mode === 'demo' || state.latest?.source === 'simulation'
  const space = state.spatial
  const cells = space?.cells || [], track = space?.track || [], field = space?.field || []
  const values = cells.map(cell => cell[3]!).sort((a, b) => a - b)
  const cmin = values.length ? values[Math.floor((values.length - 1) * .05)]! : -100
  const cmax = values.length ? Math.max(cmin + .5, values[Math.floor((values.length - 1) * .98)]!) : -50
  if (layers.field && field.length) traces.push(mesh(field, (space!.voxel_size || 2) * .78, '近邻插值体素', layers.opacity, cmin, cmax))
  if (layers.measured && cells.length) traces.push(mesh(cells, (space!.voxel_size || 2) * .68, simulated ? '模拟强度体素' : '实测强度体素', .8, cmin, cmax))
  if (track.length && layers.track) traces.push({ type: 'scatter3d', uid: 'measurement-track', mode: 'lines+markers',
    x: track.map(sample => sample.p[0]! * 100), y: track.map(sample => sample.p[1]! * 100), z: track.map(sample => sample.p[2]! * 100),
    line: { width: 4, color: '#5684b6' }, name: simulated ? '模拟轨迹（合成）' : '实际测量轨迹',
    marker: { size: 3, color: track.map(sample => sample.dbm), colorscale: thermalScale, cmin, cmax, showscale: true,
      colorbar: { title: '接收功率<br>dBm', len: .65, thickness: 12, tickfont: font, titlefont: font } },
    customdata: track.map(sample => [sample.dbm, sample.latitude, sample.longitude, sample.altitude_m, new Date(sample.time * 1000).toLocaleTimeString()]),
    hovertemplate: '%{customdata[4]}<br>E %{x:.0f}, N %{y:.0f}, U %{z:.0f} cm<br>接收功率 %{customdata[0]:.2f} dBm<br>纬度 %{customdata[1]:.7f}<br>经度 %{customdata[2]:.7f}<br>海拔 %{customdata[3]:.1f} m<extra></extra>' })
  function marker(point: number[], name: string, color: string, symbol: string) {
    traces.push({ type: 'scatter3d', uid: name, mode: 'markers+text', x: [point[0]! * 100], y: [point[1]! * 100], z: [point[2]! * 100], name,
      text: [name], textposition: 'top center', textfont: { color, size: 11 },
      marker: { size: 8, color, symbol, line: { width: 1.5, color: '#fff' } },
      hovertemplate: name + '<br>E %{x:.0f}, N %{y:.0f}, U %{z:.0f} cm<extra></extra>' })
  }
  if (space?.hotspot) marker(space.hotspot.p, '测量热点', '#f2bd63', 'diamond')
  if (space?.source) marker(space.source.p, '模型源位置估计', '#ef80a7', 'diamond')
  if (track.length) marker(track[track.length - 1]!.p, '当前探测仪', '#68e7d6', 'circle')
  if (!traces.length) traces.push({ type: 'scatter3d', uid: 'empty-scene', mode: 'markers', x: [], y: [], z: [], showlegend: false })
  return { traces, layout: { paper_bgcolor: transparent, font, margin: { l: 0, r: 10, t: 0, b: 0 }, showlegend: true,
    legend: { x: .01, y: .99, bgcolor: '#09212bdd', font: { size: 10 } }, scene: view.scene() } }
}

export function spectrumPlot(state: San60State) {
  const live = state.latest
  return { traces: live ? [{ type: 'scattergl', mode: 'lines', x: live.frequencies, y: live.powers,
    line: { color: '#63dfd0', width: 1.4 }, hovertemplate: '%{x:.3f} MHz<br>%{y:.2f} dBm<extra></extra>' }] : [],
  layout: { paper_bgcolor: transparent, plot_bgcolor: transparent, font, margin: { l: 50, r: 18, t: 12, b: 42 },
    xaxis: { title: '频率 MHz', gridcolor: '#23414a' }, yaxis: { title: 'dBm', range: [-125, -20], gridcolor: '#23414a' },
    shapes: [{ type: 'rect', xref: 'x', yref: 'paper', x0: state.band[0], x1: state.band[1], y0: 0, y1: 1, fillcolor: '#63dfd015', line: { width: 0 } }],
    uirevision: 'spectrum' } }
}

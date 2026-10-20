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
  return algorithmPresets.value.find(item => item.id === p?.presetId)?.label ?? `算法指令 ${p?.presetId ?? '—'}`
}

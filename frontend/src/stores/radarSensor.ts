import { defineStore } from 'pinia'

import { fetchRadarOverview } from '@/api/sensor'
import type { RadarOverview } from '@/types/sensor'

interface RadarSensorState {
  generation: number
  overview: RadarOverview | null
  loading: boolean
  error: string
}

export const useRadarSensorStore = defineStore('radar-sensor', {
  state: (): RadarSensorState => ({
    generation: 0,
    overview: null,
    loading: false,
    error: '',
  }),
  actions: {
    async refresh(_silent = false) {
      // Polling must not queue overlapping requests while the backend is slow.
      if (this.loading) return
      this.loading = true
      const generation = this.generation
      try {
        const overview = await fetchRadarOverview()
        if (generation !== this.generation) return
        this.overview = overview
        this.error = ''
      } catch (error) {
        if (generation !== this.generation) return
        this.error = error instanceof Error ? error.message : '雷达数据加载失败'
        this.overview = null
      } finally {
        if (generation === this.generation) this.loading = false
      }
    },
    clearSession() {
      this.generation += 1
      this.overview = null
      this.loading = false
      this.error = ''
    },
  },
})

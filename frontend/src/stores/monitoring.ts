import { defineStore } from 'pinia'

import { fetchRuntimeNodes, fetchRuntimeSummary } from '@/api/monitoring'
import type { DeviceStatus, DeviceType } from '@/types/device'
import type { RuntimeNode, RuntimeNodeQuery, RuntimeSummary } from '@/types/monitoring'

let runtimeEvents: EventSource | null = null
let refreshTimer: number | null = null
let refreshInFlight = false
let pendingRefresh: { overrides: RuntimeNodeQuery; silent: boolean } | null = null
let sessionGeneration = 0

interface MonitoringState {
  summary: RuntimeSummary | null
  nodes: RuntimeNode[]
  type?: DeviceType
  status?: DeviceStatus
  loading: boolean
  error: string
}

export const useMonitoringStore = defineStore('monitoring', {
  state: (): MonitoringState => ({
    summary: null,
    nodes: [],
    type: undefined,
    status: undefined,
    loading: false,
    error: '',
  }),
  actions: {
    async refresh(overrides: RuntimeNodeQuery = {}, silent = false) {
      if (refreshInFlight) {
        pendingRefresh = { overrides, silent }
        return
      }
      refreshInFlight = true
      const generation = sessionGeneration
      const query: RuntimeNodeQuery = {
        type: overrides.type ?? this.type,
        status: overrides.status ?? this.status,
      }

      if (!silent) this.loading = true
      this.error = ''
      try {
        const [summary, nodes] = await Promise.all([fetchRuntimeSummary(), fetchRuntimeNodes(query)])
        if (generation !== sessionGeneration) return
        this.summary = summary
        this.nodes = nodes
      } catch (error) {
        if (generation !== sessionGeneration) return
        this.summary = null
        this.nodes = []
        this.error = error instanceof Error ? error.message : '运行监控数据加载失败'
      } finally {
        if (generation !== sessionGeneration) return
        if (!silent) this.loading = false
        refreshInFlight = false
        const pending = pendingRefresh
        pendingRefresh = null
        if (pending) void this.refresh(pending.overrides, pending.silent)
      }
    },
    connectEvents() {
      if (runtimeEvents) return
      const events = new EventSource('/api/monitoring/events')
      runtimeEvents = events
      events.addEventListener('runtime-change', () => {
        if (runtimeEvents !== events) return
        if (refreshTimer !== null) return
        refreshTimer = window.setTimeout(() => {
          refreshTimer = null
          void this.refresh({}, true)
        }, 3000)
      })
      events.onerror = () => {
        if (runtimeEvents !== events) return
        this.error = '实时状态连接中断，正在自动重连'
      }
      events.onopen = () => {
        if (runtimeEvents !== events) return
        if (this.error === '实时状态连接中断，正在自动重连') this.error = ''
        void this.refresh({}, true)
      }
    },
    disconnectEvents() {
      runtimeEvents?.close()
      runtimeEvents = null
      if (refreshTimer !== null) window.clearTimeout(refreshTimer)
      refreshTimer = null
    },
    clearSession() {
      this.disconnectEvents()
      sessionGeneration += 1
      refreshInFlight = false
      pendingRefresh = null
      this.summary = null
      this.nodes = []
      this.loading = false
      this.error = ''
    },
  },
})

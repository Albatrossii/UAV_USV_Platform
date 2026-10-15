import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'

import { fetchRuntimeNodes } from '@/api/monitoring'
import type { RuntimeNode } from '@/types/monitoring'

export const NODE_MONITOR_POLL_MS = 5_000
export const NODE_MONITOR_SNAPSHOT_TTL_MS = 15_000

/** A local, read-only snapshot. Never changes filters or connections in the shared monitoring store. */
export function useNodeMonitoring() {
  const nodes = ref<RuntimeNode[]>([])
  const observedAt = ref<number | null>(null)
  const now = ref(Date.now())
  const loading = ref(false)
  const error = ref('')
  const active = ref(false)
  let generation = 0
  let pendingGeneration: number | null = null
  let timer: number | undefined
  let nextRefreshAt = 0

  const available = computed(() => active.value && !error.value && observedAt.value !== null
    && now.value >= observedAt.value
    && now.value - observedAt.value <= NODE_MONITOR_SNAPSHOT_TTL_MS)

  async function refresh() {
    if (!active.value || pendingGeneration === generation) return
    const requestGeneration = generation
    const requestStartedAt = Date.now()
    pendingGeneration = requestGeneration
    loading.value = true
    try {
      const result = await fetchRuntimeNodes()
      if (!active.value || generation !== requestGeneration) return
      nodes.value = result
      // The DTO's age can predate slow database/network work. Include the full
      // request duration so a late response cannot renew old online evidence.
      observedAt.value = requestStartedAt
      now.value = Date.now()
      error.value = ''
    } catch {
      if (!active.value || generation !== requestGeneration) return
      // Failed monitoring means the state is unknown, not that every device is offline.
      error.value = '节点监测暂不可用，等待重新获取；不据此判断设备离线。'
    } finally {
      if (generation === requestGeneration) {
        pendingGeneration = null
        loading.value = false
        nextRefreshAt = Date.now() + NODE_MONITOR_POLL_MS
      }
    }
  }

  function onVisibilityChange() {
    now.value = Date.now()
    if (document.visibilityState !== 'hidden') void refresh()
  }

  function start() {
    if (active.value) return
    active.value = true
    generation += 1
    observedAt.value = null
    now.value = Date.now()
    void refresh()
    timer = window.setInterval(() => {
      now.value = Date.now()
      if (document.visibilityState !== 'hidden' && now.value >= nextRefreshAt) void refresh()
    }, 1_000)
    document.addEventListener('visibilitychange', onVisibilityChange)
  }

  function stop() {
    active.value = false
    generation += 1
    pendingGeneration = null
    loading.value = false
    if (timer !== undefined) window.clearInterval(timer)
    timer = undefined
    document.removeEventListener('visibilitychange', onVisibilityChange)
  }

  onMounted(start)
  onActivated(start)
  onDeactivated(stop)
  onBeforeUnmount(stop)

  return { nodes, observedAt, now, loading, error, available, refresh }
}

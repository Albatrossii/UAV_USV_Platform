<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import WorkspaceHeader from '@/components/layout/WorkspaceHeader.vue'
import type { WorkspaceHeaderState } from '@/components/layout/workspaceHeader'
import UnifiedWorkspaceShell from '@/components/workspace/UnifiedWorkspaceShell.vue'
import DashboardView from '@/views/DashboardView.vue'
import VirtualFleetConfigView from '@/views/VirtualFleetConfigView.vue'

const route = useRoute()
const simulationActive = computed(() => route.query.workspace === 'simulation')
const simulationMounted = ref(simulationActive.value)
const overviewHeader = ref<WorkspaceHeaderState>({ deviceCode: '', deviceContext: '全局态势', statuses: [] })
const simulationHeader = ref<WorkspaceHeaderState>({ deviceCode: '', deviceContext: '', statuses: [] })
const activeHeader = computed(() => simulationActive.value ? simulationHeader.value : overviewHeader.value)

let prewarmTimer: number | null = null
let settleTimer: number | null = null
let switchGeneration = 0

function finishViewportSwitch(generation: number) {
  window.requestAnimationFrame(() => {
    window.requestAnimationFrame(() => {
      if (generation !== switchGeneration) return
      window.dispatchEvent(new Event('resize'))
      window.dispatchEvent(new CustomEvent('unity-runtime-track', {
        detail: { duration: 220 },
      }))
      if (settleTimer !== null) window.clearTimeout(settleTimer)
      settleTimer = window.setTimeout(() => {
        if (generation !== switchGeneration) return
        settleTimer = null
        window.dispatchEvent(new Event('resize'))
        window.dispatchEvent(new CustomEvent('unity-runtime-track', {
          detail: { duration: 120 },
        }))
      }, 140)
    })
  })
}

watch(simulationActive, async (active) => {
  const generation = ++switchGeneration
  if (active) simulationMounted.value = true
  await nextTick()
  finishViewportSwitch(generation)
}, { flush: 'pre' })

onMounted(() => {
  // Warm the isolated virtual-fleet iframe after the overview has settled.
  // It stays mounted afterwards, so switching workspaces never rebuilds Unity.
  if (!simulationMounted.value) {
    prewarmTimer = window.setTimeout(() => {
      prewarmTimer = null
      simulationMounted.value = true
    }, 1200)
  }
  finishViewportSwitch(++switchGeneration)
})

onBeforeUnmount(() => {
  if (prewarmTimer !== null) window.clearTimeout(prewarmTimer)
  if (settleTimer !== null) window.clearTimeout(settleTimer)
})
</script>

<template>
  <ConsoleLayout title="系统总览" eyebrow="UAV-USV" :show-refresh="false" :default-sidebar-collapsed="true" workspace-navigation immersive>
  <WorkspaceHeader :active-workspace="simulationActive ? 'simulation' : 'overview'" :state="activeHeader" />
  <UnifiedWorkspaceShell
    :active-workspace="simulationActive ? 'simulation' : 'overview'"
    :simulation-mounted="simulationMounted"
  >
    <template #overview>
      <DashboardView embedded shared-shell :active="!simulationActive" @workspace-status="overviewHeader = $event" />
    </template>

    <template #simulation>
      <VirtualFleetConfigView embedded shared-shell :active="simulationActive" @workspace-status="simulationHeader = $event" />
    </template>
  </UnifiedWorkspaceShell>
  </ConsoleLayout>
</template>

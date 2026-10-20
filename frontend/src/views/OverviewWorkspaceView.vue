<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import WorkspaceHeader from '@/components/layout/WorkspaceHeader.vue'
import type { WorkspaceHeaderState } from '@/components/layout/workspaceHeader'
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
  <div class="overview-workspace-stack" :class="{ 'simulation-active': simulationActive }">
    <section
      class="overview-workspace-pane overview-pane"
      :class="simulationActive ? 'is-inactive' : 'is-active'"
      :aria-hidden="simulationActive"
      :inert="simulationActive ? true : undefined"
    >
      <DashboardView embedded @workspace-status="overviewHeader = $event" />
    </section>

    <section
      v-if="simulationMounted"
      class="overview-workspace-pane simulation-pane"
      :class="simulationActive ? 'is-active' : 'is-inactive'"
      :aria-hidden="!simulationActive"
      :inert="!simulationActive ? true : undefined"
    >
      <VirtualFleetConfigView embedded @workspace-status="simulationHeader = $event" />
    </section>
  </div>
  </ConsoleLayout>
</template>

<style scoped>
:global(html:has(.workspace-navigation)) {
  /* Both workspaces share the same width even when only one needs scrolling. */
  scrollbar-gutter: stable;
}

.overview-workspace-stack {
  position: relative;
  min-width: 0;
  margin-top: 10px;
  overflow: hidden;
  background: #061113;
  isolation: isolate;
}

.overview-workspace-pane {
  width: 100%;
  min-width: 0;
  backface-visibility: hidden;
  contain: layout paint style;
}

.overview-workspace-pane.is-active {
  position: relative;
  z-index: 2;
  visibility: visible;
  opacity: 1;
  pointer-events: auto;
  transform: translate3d(0, 0, 0);
}

.overview-workspace-pane.is-inactive {
  position: absolute;
  inset: 0;
  z-index: 0;
  visibility: hidden;
  opacity: 0;
  pointer-events: none;
  transform: translate3d(-110%, 0, 0);
}
</style>

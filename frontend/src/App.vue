<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterView } from 'vue-router'
import { useRoute, useRouter } from 'vue-router'
import UnityRuntimeHost from '@/components/unity/UnityRuntimeHost.vue'
import SimulationRuntimeHost from '@/components/unity/SimulationRuntimeHost.vue'
import { simulationRuntime } from '@/composables/simulationRuntime'
import { useAuthStore } from '@/stores/auth'
import { useConnectivityStore } from '@/stores/connectivity'
import { useRealtimeStore } from '@/stores/realtime'
import { useUnityBridgeStore } from '@/stores/unityBridge'
import { useUnityViewportStore } from '@/stores/unityViewport'
import { useVisualSensorStore } from '@/stores/visualSensor'
import { useMonitoringStore } from '@/stores/monitoring'
import { useRadarSensorStore } from '@/stores/radarSensor'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const connectivityStore = useConnectivityStore()
watch(() => connectivityStore.backend, (state) => {
  if (state !== 'AUTH_EXPIRED' || !authStore.isAuthenticated) return
  const redirect = route.fullPath
  authStore.expireSession()
  if (route.meta.requiresAuth) void router.replace({ name: 'login', query: { redirect } })
}, { flush: 'sync' })
const realtimeStore = useRealtimeStore()
const unityBridgeStore = useUnityBridgeStore()
const unityViewportStore = useUnityViewportStore()

// ASR first visit must not initialize Unity; preserve an already-loaded P0 session.
const overviewUnityRequested = ref(false)
watch(() => [route.name, route.query.workspace, route.meta.requiresAuth], () => {
  if (!route.meta.requiresAuth) overviewUnityRequested.value = false
  else if ((route.name === 'dashboard' && route.query.workspace !== 'simulation')
    || route.name === 'optical-vision') overviewUnityRequested.value = true
}, { immediate: true })
const mountSystemOverviewUnity = computed(() => Boolean(route.meta.requiresAuth) && overviewUnityRequested.value)
const systemOverviewUnityActive = computed(() =>
  (route.name === 'dashboard' && route.query.workspace !== 'simulation')
  || route.name === 'optical-vision',
)
const systemOverviewUnityViewport = computed(() =>
  route.name === 'optical-vision'
    ? 'visual-sensors-live'
    : 'dashboard',
)
const showMissionCenterUnity = computed(
  () =>
    route.meta.requiresAuth
    && route.name === 'situation'
    && unityViewportStore.target === 'mission-execution',
)

watch(
  () => authStore.isAuthenticated,
  (authenticated) => {
    if (authenticated) realtimeStore.connect()
    else {
      realtimeStore.disconnect()
      useVisualSensorStore().clearSession()
      useMonitoringStore().clearSession()
      useRadarSensorStore().clearSession()
    }
  },
  { immediate: true },
)

const visualSubscriptionActive = computed(() =>
  systemOverviewUnityActive.value && route.name === 'optical-vision',
)

watch(visualSubscriptionActive, (active, wasActive) => {
  if (active || !wasActive) return
  unityBridgeStore.sendFor('SYSTEM_OVERVIEW', 'visualSensorSubscribe', {
    enabled: false,
    displayMode: 'off',
    gpuDirect: false,
    jpegFallback: false,
  })
})
</script>

<template>
  <SimulationRuntimeHost
    v-if="simulationRuntime.requested.value && route.meta.requiresAuth"
    :active="route.name === 'dashboard' && route.query.workspace === 'simulation'"
  />
  <UnityRuntimeHost
    v-if="mountSystemOverviewUnity"
    :viewport="systemOverviewUnityViewport"
    runtime-scope="SYSTEM_OVERVIEW"
    runtime-instance-id="overview-unity-01"
    :active="systemOverviewUnityActive"
    :layer="route.name === 'optical-vision' ? 3 : 20"
  />
  <UnityRuntimeHost
    v-if="showMissionCenterUnity"
    iframe-src="/unity-overview-test/index.html?embedded=1"
    viewport="mission-execution"
    runtime-scope="MISSION_CENTER"
    :runtime-instance-id="unityViewportStore.missionInstanceId"
    :mission-id="unityViewportStore.missionId || undefined"
    :run-id="unityViewportStore.runId || undefined"
    active
    :layer="95"
  />
  <RouterView v-slot="{ Component }">
    <KeepAlive :key="authStore.user?.username ?? 'anonymous'" include="OverviewWorkspaceView,MissionWorkspaceView">
      <component :is="Component" />
    </KeepAlive>
  </RouterView>
</template>

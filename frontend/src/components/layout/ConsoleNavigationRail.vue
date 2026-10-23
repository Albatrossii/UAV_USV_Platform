<script setup lang="ts">
import {
  Activity,
  Boxes,
  Camera,
  Crosshair,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Route,
  UserRound,
} from '@lucide/vue'
import { RouterLink, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useMonitoringStore } from '@/stores/monitoring'

defineProps<{ collapsed: boolean }>()
defineEmits<{ toggle: [] }>()

const router = useRouter()
const authStore = useAuthStore()
const monitoringStore = useMonitoringStore()

async function logout() {
  monitoringStore.disconnectEvents()
  await authStore.logout()
  await router.replace({ name: 'login' })
}
</script>

<template>
  <div class="console-navigation-rail">
    <button
      class="rail-icon rail-toggle"
      type="button"
      :title="collapsed ? '展开总控' : '收起总控'"
      :aria-label="collapsed ? '展开总控' : '收起总控'"
      :aria-expanded="!collapsed"
      @click="$emit('toggle')"
    >
      <PanelLeftOpen v-if="collapsed" :size="18" />
      <PanelLeftClose v-else :size="18" />
    </button>

    <nav class="rail-navigation" aria-label="主导航">
      <RouterLink class="rail-icon" active-class="active" exact-active-class="active" to="/" title="系统总览" aria-label="系统总览">
        <Activity :size="18" />
      </RouterLink>
      <RouterLink class="rail-icon" active-class="active" to="/situation" title="协同态势" aria-label="协同态势">
        <Route :size="18" />
      </RouterLink>
      <RouterLink class="rail-icon" active-class="active" to="/vision" title="光电视觉" aria-label="光电视觉">
        <Camera :size="18" />
      </RouterLink>
      <RouterLink class="rail-icon" active-class="active" to="/radar" title="雷达态势" aria-label="雷达态势">
        <Crosshair :size="18" />
      </RouterLink>
      <RouterLink class="rail-icon" active-class="active" to="/devices" title="设备管理" aria-label="设备管理">
        <Boxes :size="18" />
      </RouterLink>
    </nav>

    <div class="rail-footer">
      <div
        class="rail-icon rail-user"
        role="img"
        :title="`${authStore.user?.username || 'admin'} · ${authStore.user?.role || 'ADMIN'}`"
        :aria-label="`当前用户：${authStore.user?.username || 'admin'}`"
      >
        <UserRound :size="19" />
      </div>
      <button
        class="rail-icon rail-logout"
        type="button"
        title="退出登录"
        aria-label="退出登录"
        :disabled="authStore.loading"
        @click="logout"
      >
        <LogOut :size="17" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.console-navigation-rail {
  display: flex;
  flex: 0 0 48px;
  flex-direction: column;
  align-items: center;
  align-self: stretch;
  box-sizing: border-box;
  width: 48px;
  min-width: 0;
  height: 100%;
  min-height: 0;
  padding: 9px 4px;
  gap: 14px;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: none;
  color: #9ebfba;
  background: #081619;
  border-right: 1px solid rgba(108, 228, 213, 0.16);
}

.console-navigation-rail::-webkit-scrollbar {
  display: none;
}

.rail-icon {
  display: grid;
  flex: 0 0 auto;
  box-sizing: border-box;
  width: 36px;
  height: 36px;
  margin: 0;
  padding: 0;
  place-items: center;
  color: inherit;
  text-decoration: none;
  cursor: pointer;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  transition: color 160ms ease, background-color 160ms ease, border-color 160ms ease;
}

.rail-icon > svg {
  display: block;
}

.rail-icon:hover:not(:disabled),
.rail-icon.active {
  color: #baf7ee;
  background: rgba(108, 228, 213, 0.12);
  border-color: rgba(108, 228, 213, 0.26);
}

.rail-icon:focus-visible {
  outline: 2px solid #6ce4d5;
  outline-offset: 1px;
}

.rail-toggle {
  color: #baf7ee;
  border-color: rgba(108, 228, 213, 0.2);
}

.rail-navigation,
.rail-footer {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  align-items: center;
  gap: 7px;
}

.rail-footer {
  margin-top: auto;
  padding-top: 8px;
  border-top: 1px solid rgba(108, 228, 213, 0.14);
}

.rail-user {
  color: #7eaaa3;
  cursor: default;
}

.rail-logout:hover:not(:disabled) {
  color: #ff8c85;
  background: rgba(255, 91, 83, 0.08);
  border-color: rgba(255, 105, 96, 0.36);
}

.rail-icon:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
</style>

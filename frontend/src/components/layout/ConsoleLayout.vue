<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import {
  Activity,
  Boxes,
  Camera,
  Crosshair,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Plane,
  RefreshCw,
  Route,
  UserRound,
} from '@lucide/vue'
import { useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'
import { useMonitoringStore } from '@/stores/monitoring'

const props = withDefaults(defineProps<{
  title: string
  eyebrow: string
  showRefresh?: boolean
  immersive?: boolean
  collapsibleSidebar?: boolean
  defaultSidebarCollapsed?: boolean
}>(), {
  showRefresh: true,
  immersive: false,
  collapsibleSidebar: true,
  defaultSidebarCollapsed: false,
})

const SIDEBAR_STORAGE_KEY = 'uav-usv:sidebar-collapsed'
const router = useRouter()
const authStore = useAuthStore()
const monitoringStore = useMonitoringStore()

function readSidebarPreference() {
  if (!props.collapsibleSidebar || typeof window === 'undefined') return false
  try {
    const stored = window.localStorage.getItem(SIDEBAR_STORAGE_KEY)
    return stored === null ? props.defaultSidebarCollapsed : stored === 'true'
  } catch {
    return props.defaultSidebarCollapsed
  }
}

const sidebarCollapsed = ref(readSidebarPreference())
let sidebarTransitionTimer: number | null = null

watch(sidebarCollapsed, (collapsed) => {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(collapsed))
  } catch {
    // The layout still works when browser storage is unavailable.
  }
})

async function toggleSidebar() {
  if (!props.collapsibleSidebar) return
  window.dispatchEvent(new CustomEvent('uav-usv:viewport-transition-start'))
  sidebarCollapsed.value = !sidebarCollapsed.value
  await nextTick()
  if (sidebarTransitionTimer !== null) window.clearTimeout(sidebarTransitionTimer)
  sidebarTransitionTimer = window.setTimeout(() => {
    sidebarTransitionTimer = null
    window.dispatchEvent(new CustomEvent('uav-usv:viewport-transition-end'))
  }, 220)
}

async function logout() {
  monitoringStore.disconnectEvents()
  await authStore.logout()
  await router.replace({ name: 'login' })
}
</script>

<template>
  <div class="app-shell console-shell" :class="{ 'sidebar-collapsed': sidebarCollapsed }">
    <aside class="sidebar console-sidebar" :aria-label="sidebarCollapsed ? '折叠导航' : '展开导航'">
      <div class="brand console-brand">
        <div class="brand-mark console-brand-mark"><Plane :size="22" /></div>
        <div class="console-brand-copy">
          <strong>UAV-USV</strong>
          <span>协同仿真平台</span>
        </div>
      </div>

      <button
        v-if="props.collapsibleSidebar"
        class="console-sidebar-toggle"
        type="button"
        :title="sidebarCollapsed ? '展开菜单' : '折叠菜单'"
        :aria-label="sidebarCollapsed ? '展开菜单' : '折叠菜单'"
        :aria-pressed="sidebarCollapsed"
        @click="toggleSidebar"
      >
        <PanelLeftOpen v-if="sidebarCollapsed" :size="16" />
        <PanelLeftClose v-else :size="16" />
      </button>

      <nav class="navigation console-navigation" aria-label="主导航">
        <RouterLink class="nav-item" active-class="active" exact-active-class="active" to="/" title="系统总览">
          <Activity :size="18" />
          <span class="console-nav-label">系统总览</span>
        </RouterLink>
        <RouterLink class="nav-item" active-class="active" to="/situation" title="协同态势">
          <Route :size="18" />
          <span class="console-nav-label">协同态势</span>
        </RouterLink>
        <RouterLink class="nav-item" active-class="active" to="/vision" title="光电视觉">
          <Camera :size="18" />
          <span class="console-nav-label">光电视觉</span>
        </RouterLink>
        <RouterLink class="nav-item" active-class="active" to="/radar" title="雷达态势">
          <Crosshair :size="18" />
          <span class="console-nav-label">雷达态势</span>
        </RouterLink>
        <RouterLink class="nav-item" active-class="active" to="/devices" title="设备管理">
          <Boxes :size="18" />
          <span class="console-nav-label">设备管理</span>
        </RouterLink>
      </nav>

      <div class="sidebar-footer console-sidebar-footer">
        <div class="console-sidebar-user" :title="authStore.user?.username || 'admin'">
          <UserRound :size="21" />
          <span>
            <strong>{{ authStore.user?.username || 'admin' }}</strong>
            <small>{{ authStore.user?.role || 'ADMIN' }}</small>
          </span>
        </div>
        <button type="button" title="退出登录" aria-label="退出登录" :disabled="authStore.loading" @click="logout">
          <LogOut :size="17" />
        </button>
      </div>
    </aside>

    <main class="workspace console-workspace">
      <header v-if="!immersive" class="topbar console-topbar">
        <div class="console-title">
          <p class="eyebrow">{{ eyebrow }}</p>
          <h1>{{ title }}</h1>
        </div>
        <div class="topbar-actions console-topbar-actions">
          <slot name="actions" />
          <button v-if="showRefresh" class="console-icon-button" type="button" @click="monitoringStore.refresh({}, true)">
            <RefreshCw :size="16" />
            刷新
          </button>
          <div class="current-user console-current-user">
            <UserRound :size="17" />
            <span>
              <strong>{{ authStore.user?.username || 'admin' }}</strong>
              <small>{{ authStore.user?.role || 'ADMIN' }}</small>
            </span>
          </div>
          <button class="console-icon-button" type="button" :disabled="authStore.loading" @click="logout">
            <LogOut :size="16" />
            退出
          </button>
        </div>
      </header>

      <slot />
    </main>
  </div>
</template>

<style scoped>
.console-shell {
  --console-sidebar-width: 228px;
  grid-template-columns: var(--console-sidebar-width) minmax(0, 1fr);
  min-height: 100vh;
  background:
    linear-gradient(rgba(108, 228, 213, 0.035) 1px, transparent 1px),
    linear-gradient(90deg, rgba(108, 228, 213, 0.03) 1px, transparent 1px),
    linear-gradient(180deg, #071719, #071113 58%, #060d0f);
  background-size: 42px 42px, 42px 42px, auto;
  transition: grid-template-columns 180ms ease;
}

.console-shell.sidebar-collapsed {
  --console-sidebar-width: 76px;
}

.console-sidebar {
  position: sticky;
  color: #dff8f4;
  background: rgba(8, 20, 23, 0.98);
  border-right: 1px solid rgba(108, 228, 213, 0.18);
  transition: padding 180ms ease, width 180ms ease;
}

.console-brand {
  min-width: 0;
  border-bottom-color: rgba(108, 228, 213, 0.18);
}

.console-brand-copy,
.console-nav-label,
.console-sidebar-user span {
  overflow: hidden;
  white-space: nowrap;
  transition: max-width 150ms ease, opacity 120ms ease;
}

.console-brand-copy {
  max-width: 140px;
}

.console-brand-mark {
  flex: 0 0 auto;
  background: #6ce4d5;
}

.console-sidebar-toggle {
  position: absolute;
  top: 84px;
  right: -14px;
  z-index: 80;
  display: grid;
  width: 28px;
  height: 28px;
  padding: 0;
  color: #baf7ee;
  cursor: pointer;
  place-items: center;
  background: #092027;
  border: 1px solid rgba(108, 228, 213, 0.5);
  border-radius: 50%;
  box-shadow: 0 0 14px rgba(56, 211, 205, 0.14);
}

.console-sidebar-toggle:hover {
  color: #061113;
  background: #6ce4d5;
  border-color: #6ce4d5;
}

.console-navigation :deep(.nav-item) {
  color: #9ebfba;
  font-weight: 800;
  white-space: nowrap;
  transition: padding 180ms ease, background-color 160ms ease;
}

.console-navigation :deep(.nav-item svg) {
  flex: 0 0 auto;
}

.console-navigation :deep(.nav-item.active) {
  color: #f2fffd;
  background: rgba(108, 228, 213, 0.13);
  box-shadow: inset 3px 0 #6ce4d5;
}

.console-sidebar-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #6f918c;
  border-top-color: rgba(108, 228, 213, 0.14);
}

.console-sidebar-user {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 9px;
}

.console-sidebar-user > svg {
  flex: 0 0 auto;
}

.console-sidebar-user strong,
.console-sidebar-user small {
  display: block;
}

.console-sidebar-user strong {
  color: #dff8f4;
  font-size: 12px;
}

.console-sidebar-user small {
  margin-top: 2px;
  color: #6f918c;
  font-size: 9px;
}

.console-sidebar-footer > button {
  display: grid;
  flex: 0 0 auto;
  width: 30px;
  height: 30px;
  padding: 0;
  color: #88aaa5;
  cursor: pointer;
  place-items: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 5px;
}

.console-sidebar-footer > button:hover:not(:disabled) {
  color: #ff7a73;
  border-color: rgba(255, 105, 96, 0.36);
  background: rgba(255, 91, 83, 0.08);
}

.sidebar-collapsed .console-sidebar {
  padding-right: 10px;
  padding-left: 10px;
}

.sidebar-collapsed .console-brand {
  justify-content: center;
  padding-right: 0;
  padding-left: 0;
}

.sidebar-collapsed .console-brand-copy,
.sidebar-collapsed .console-nav-label,
.sidebar-collapsed .console-sidebar-user span {
  max-width: 0;
  opacity: 0;
}

.sidebar-collapsed .console-navigation :deep(.nav-item) {
  justify-content: center;
  gap: 0;
  padding-right: 0;
  padding-left: 0;
}

.sidebar-collapsed .console-sidebar-user {
  justify-content: center;
  gap: 0;
}

.sidebar-collapsed .console-sidebar-footer {
  flex-direction: column;
  padding-right: 0;
  padding-left: 0;
}

.console-workspace {
  color: #e9fffb;
  padding: 16px clamp(12px, 1.4vw, 32px) 20px;
}

.console-topbar {
  align-items: flex-start;
}

.console-title :deep(.eyebrow),
.console-title .eyebrow {
  color: #6ce4d5;
  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0.08em;
}

.console-title h1 {
  color: #f3fffd;
  font-size: 34px;
}

.console-topbar-actions {
  flex-wrap: wrap;
  justify-content: flex-end;
}

.console-icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 36px;
  padding: 0 13px;
  color: #dff8f4;
  background: rgba(108, 228, 213, 0.08);
  border: 1px solid rgba(108, 228, 213, 0.24);
  border-radius: 5px;
  cursor: pointer;
}

.console-icon-button:hover:not(:disabled) {
  color: #061113;
  background: #6ce4d5;
  border-color: #6ce4d5;
}

.console-icon-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.console-current-user {
  color: #dff8f4;
  border-right-color: rgba(108, 228, 213, 0.18);
}

.console-current-user small {
  color: #87aaa5;
}

@media (max-width: 860px) {
  .console-topbar {
    flex-direction: column;
  }

  .console-topbar-actions {
    justify-content: flex-start;
  }
}

/* Compact navigation is driven by available width, not the device brand/DPR. */
@media (max-width: 1100px) {
  .console-shell { --console-sidebar-width: 64px; }
  .console-shell.sidebar-collapsed { --console-sidebar-width: 64px; }
  .console-sidebar { padding: 16px 8px; }
  .console-brand { justify-content: center; padding-inline: 0; }
  .console-brand-copy, .console-nav-label, .console-sidebar-user span { display: none; }
  .console-navigation :deep(.nav-item) { justify-content: center; padding-inline: 0; }
  .console-sidebar-footer { flex-direction: column; padding-inline: 0; }
  .console-sidebar-toggle { display: none; }
}
@media (max-width: 600px) {
  .console-shell, .console-shell.sidebar-collapsed { --console-sidebar-width: 52px; }
  .console-workspace { padding: 12px 8px; }
  .console-title h1 { font-size: 24px; }
  .console-topbar { gap: 12px; margin-bottom: 16px; }
  .console-sidebar { padding-inline: 5px; }
  .console-brand-mark { width: 34px; height: 34px; }
  .console-topbar-actions { gap: 8px; }
}
@media (max-height: 650px) {
  .console-sidebar { height: 100dvh; overflow-y: auto; }
}
</style>

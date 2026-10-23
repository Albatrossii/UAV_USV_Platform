<script setup lang="ts">
import type { WorkspaceHeaderState } from './workspaceHeader'

defineProps<{
  activeWorkspace: 'overview' | 'simulation'
  state: WorkspaceHeaderState
}>()
</script>

<template>
  <header class="workspace-header" aria-label="工作空间状态">
    <div class="workspace-observer">
      <strong>当前观察设备</strong>
      <span>{{ state.deviceCode || '—' }}</span>
      <small>{{ state.deviceContext }}</small>
    </div>
    <nav class="workspace-switch" aria-label="工作空间切换">
      <RouterLink
        :to="{ name: 'dashboard' }"
        :class="{ active: activeWorkspace === 'overview' }"
        :aria-current="activeWorkspace === 'overview' ? 'page' : undefined"
      >系统总览</RouterLink>
      <RouterLink
        :to="{ name: 'dashboard', query: { workspace: 'simulation' } }"
        :class="{ active: activeWorkspace === 'simulation' }"
        :aria-current="activeWorkspace === 'simulation' ? 'page' : undefined"
      >算法仿真</RouterLink>
    </nav>
    <div class="workspace-link-status" aria-label="当前工作空间连接状态">
      <b v-for="status in state.statuses" :key="status.label" :class="status.tone">
        <i aria-hidden="true"></i>{{ status.label }}
      </b>
    </div>
  </header>
</template>

<style scoped>
.workspace-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  min-width: 0;
  height: 56px;
  min-height: 56px;
  padding: 7px 14px;
  color: #dceef3;
  background: linear-gradient(90deg, #061927, #04111d);
  border: 1px solid rgba(46, 145, 190, .26);
  border-radius: 8px;
}
.workspace-observer { display: flex; align-items: center; gap: 12px; min-width: 0; white-space: nowrap; }
.workspace-observer strong { font-size: 12px; }
.workspace-observer > span { color: #ffcf72; font-size: 16px; font-weight: 800; }
.workspace-observer small { color: #81aab2; font-size: 11px; overflow: hidden; text-overflow: ellipsis; }
.workspace-switch { display: flex; align-items: center; padding: 3px; background: #06171c; border: 1px solid rgba(108, 228, 213, .14); border-radius: 6px; }
.workspace-switch a { display: flex; align-items: center; justify-content: center; min-width: 108px; min-height: 32px; padding: 7px 14px; color: #789d9b; font-size: 12px; font-weight: 800; text-decoration: none; white-space: nowrap; border-radius: 4px; }
.workspace-switch a:hover { color: #cafff8; background: rgba(108, 228, 213, .06); }
.workspace-switch a.active { color: #effffd; background: rgba(108, 228, 213, .12); box-shadow: inset 0 -2px #6ce4d5; }
.workspace-link-status { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 5px 14px; min-width: 0; }
.workspace-link-status b { display: inline-flex; align-items: center; gap: 6px; color: #81a1ac; font-size: 11px; white-space: nowrap; }
.workspace-link-status i { width: 6px; height: 6px; flex: 0 0 auto; border-radius: 50%; background: #6f8993; }
.workspace-link-status .online { color: #bff9ef; }
.workspace-link-status .online i { background: #6ce4d5; box-shadow: 0 0 8px rgba(108, 228, 213, .4); }
.workspace-link-status .info { color: #67cce8; }
.workspace-link-status .info i { background: #39c7ea; }
@container workspace (max-width: 1100px) {
  .workspace-header { height: 72px; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 10px; }
  .workspace-observer { gap: 7px; }
  .workspace-observer strong { font-size: 11px; }
  .workspace-link-status { grid-column: 1 / -1; justify-content: flex-end; }
}
@container workspace (max-width: 600px) {
  .workspace-header { height: 112px; grid-template-columns: minmax(0, 1fr); gap: 5px; padding: 7px 10px; }
  .workspace-observer { justify-content: center; }
  .workspace-switch { justify-self: center; }
  .workspace-switch a { min-height: 30px; padding-block: 5px; }
  .workspace-link-status { justify-content: center; gap: 4px 10px; }
  .workspace-link-status b { font-size: 10px; }
}
</style>

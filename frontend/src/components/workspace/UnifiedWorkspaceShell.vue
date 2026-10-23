<script setup lang="ts">
import { ref } from 'vue'

import TaskConsoleShell from '@/components/workspace/TaskConsoleShell.vue'
import WorkspaceBottomPanelShell from '@/components/workspace/WorkspaceBottomPanelShell.vue'
import WorkspaceToolbarShell from '@/components/workspace/WorkspaceToolbarShell.vue'
import WorkspaceViewportShell from '@/components/workspace/WorkspaceViewportShell.vue'

withDefaults(defineProps<{
  activeWorkspace: 'overview' | 'simulation'
  simulationMounted?: boolean
}>(), {
  simulationMounted: false,
})

const consoleCollapsed = ref(false)
</script>

<template>
  <div
    class="unified-workspace-shell unified-workspace-contract overview-workspace-stack"
    :class="{
      'simulation-active': activeWorkspace === 'simulation',
      'console-collapsed': consoleCollapsed,
    }"
    data-workspace-shell-contract="v1"
  >
    <div class="unified-workspace-grid">
      <aside class="shared-console-drawer" :class="{ collapsed: consoleCollapsed }">
        <TaskConsoleShell
          :collapsed="consoleCollapsed"
          @collapse="consoleCollapsed = true"
          @expand="consoleCollapsed = false"
        >
          <div id="shared-workspace-console-content" class="shared-console-content-target" />
        </TaskConsoleShell>
      </aside>

      <main class="shared-workspace-main">
        <WorkspaceToolbarShell>
          <div id="shared-workspace-toolbar-content" class="shared-toolbar-content-target" />
        </WorkspaceToolbarShell>

        <WorkspaceViewportShell>
          <div id="shared-workspace-viewport-content" class="shared-viewport-content-target" />
        </WorkspaceViewportShell>

        <WorkspaceBottomPanelShell>
          <div id="shared-workspace-bottom-content" class="shared-bottom-content-target" />
        </WorkspaceBottomPanelShell>

        <div class="shared-workspace-content-stack">
          <section
            class="unified-workspace-pane overview-workspace-pane overview-pane"
            :class="activeWorkspace === 'overview' ? 'is-active' : 'is-inactive'"
            :aria-hidden="activeWorkspace !== 'overview'"
            :inert="activeWorkspace !== 'overview' ? true : undefined"
          >
            <slot name="overview" />
          </section>

          <section
            v-if="simulationMounted"
            class="unified-workspace-pane overview-workspace-pane simulation-pane"
            :class="activeWorkspace === 'simulation' ? 'is-active' : 'is-inactive'"
            :aria-hidden="activeWorkspace !== 'simulation'"
            :inert="activeWorkspace !== 'simulation' ? true : undefined"
          >
            <slot name="simulation" />
          </section>
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
:global(html:has(.workspace-navigation)) {
  /* Both workspaces share the same width even when only one needs scrolling. */
  scrollbar-gutter: stable;
}

.unified-workspace-shell {
  position: relative;
  box-sizing: border-box;
  width: 100%;
  height: var(--workspace-frame-height);
  min-width: 0;
  min-height: var(--workspace-frame-min-height);
  margin-top: 10px;
  overflow: hidden;
  background: var(--workspace-page-background);
  isolation: isolate;
}

.unified-workspace-grid {
  display: grid;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  grid-template-columns: var(--workspace-console-width) minmax(0, 1fr);
  grid-template-rows: minmax(0, 1fr);
  gap: var(--workspace-column-gap);
  transition: grid-template-columns 240ms cubic-bezier(.22,.8,.3,1);
}

.unified-workspace-shell.console-collapsed .unified-workspace-grid {
  grid-template-columns: 48px minmax(0, 1fr);
}

.shared-console-drawer {
  position: relative;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--workspace-console-background);
  border-radius: var(--workspace-panel-radius);
  contain: layout paint;
}

.shared-console-content-target { min-width: 0; }

.shared-console-drawer.collapsed :deep(.task-console-panel) {
  visibility: hidden;
  opacity: 0;
  pointer-events: none;
  transform: translateX(-12px);
}

.shared-workspace-main {
  position: relative;
  display: grid;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  grid-template-rows:
    var(--workspace-stage-toolbar-height)
    minmax(0, 1fr)
    calc(var(--workspace-footer-height) + var(--workspace-panel-gap));
  overflow: hidden;
}

.shared-toolbar-content-target { width: 100%; height: 100%; min-width: 0; min-height: 0; }
.shared-viewport-content-target,
.shared-bottom-content-target { width: 100%; height: 100%; min-width: 0; min-height: 0; }

:deep(.workspace-toolbar-shell) { z-index: 2; grid-row: 1; }
:deep(.workspace-viewport-shell) { z-index: 2; grid-row: 2; }
:deep(.workspace-bottom-panel-shell) { z-index: 2; grid-row: 3; }

.shared-workspace-content-stack {
  position: absolute;
  inset: var(--workspace-stage-toolbar-height) 0 0;
  z-index: 0;
  width: 100%;
  height: auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  opacity: 0;
  pointer-events: none;
}

.unified-workspace-pane {
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  backface-visibility: hidden;
  contain: layout paint style;
}

.unified-workspace-pane.is-active {
  position: relative;
  z-index: 2;
  visibility: visible;
  opacity: 1;
  pointer-events: auto;
  transform: translate3d(0, 0, 0);
}

.unified-workspace-pane.is-inactive {
  position: absolute;
  inset: 0;
  z-index: 0;
  visibility: hidden;
  opacity: 0;
  pointer-events: none;
  transform: translate3d(-110%, 0, 0);
}

@container workspace (max-width: 760px) {
  .unified-workspace-shell { height: auto; min-height: 0; overflow: visible; }
  .unified-workspace-grid,
  .unified-workspace-shell.console-collapsed .unified-workspace-grid {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: auto auto;
    overflow: visible;
  }
  .shared-console-drawer { height: 520px; }
  .shared-console-drawer.collapsed { height: 48px; }
  .shared-workspace-main {
    height: max(720px, calc(100dvh - 24px));
    grid-template-rows: auto minmax(320px, 1fr) auto;
  }
  :deep(.workspace-bottom-panel-shell) { height: auto; min-height: 206px; margin-top: var(--workspace-panel-gap); }
  .shared-workspace-content-stack { inset: 0; }
  .unified-workspace-pane { height: 100%; }
}
</style>

<script setup lang="ts">
import { ChevronLeft, ChevronRight } from '@lucide/vue'

withDefaults(defineProps<{
  collapsed?: boolean
  title?: string
  protocolLabel?: string
  reopenLabel?: string
}>(), {
  collapsed: false,
  title: '任务控制台',
  protocolLabel: 'V3 PROTOCOL',
  reopenLabel: '任务控制台',
})

const emit = defineEmits<{
  collapse: []
  expand: []
}>()
</script>

<template>
  <button
    v-if="collapsed"
    class="task-console-reopen vf-drawer-reopen"
    type="button"
    aria-label="展开总控"
    :title="`展开${reopenLabel}`"
    @click="emit('expand')"
  >
    <ChevronRight :size="17" />
    <span>{{ reopenLabel }}</span>
  </button>

  <section
    class="task-console-panel vf-config-panel"
    aria-label="任务控制台"
    :inert="collapsed ? true : undefined"
  >
    <div class="task-console-head vf-panel-head">
      <div>
        <h3>{{ title }}</h3>
        <span>{{ protocolLabel }}</span>
      </div>
      <button type="button" title="收起总控" aria-label="收起总控" @click="emit('collapse')">
        <ChevronLeft :size="17" />
      </button>
    </div>
    <slot />
  </section>
</template>

<style scoped>
.task-console-panel {
  display: flex;
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  box-sizing: border-box;
  flex-direction: column;
  gap: 10px;
  padding: 0 2px 2px 0;
  overflow: auto;
  color: #dff8f4;
  scrollbar-width: thin;
  scrollbar-color: #365451 transparent;
  transition: opacity 150ms ease, transform 220ms cubic-bezier(.22,.8,.3,1), visibility 0s linear 0s;
}

.task-console-panel > :deep(*) { flex-shrink: 0; }

.task-console-head {
  display: flex;
  min-height: 47px;
  box-sizing: border-box;
  padding: 0 10px;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #dff8f4;
  background: #0b2025;
  border: 1px solid rgba(108, 228, 213, .18);
  border-radius: 7px;
}

.task-console-head h3 { margin: 0; font-size: 13px; font-weight: 600; }
.task-console-head span { display: block; margin-top: 2px; color: #789ca3; font-size: 9px; letter-spacing: .08em; }
.task-console-head button {
  display: inline-flex;
  width: 30px;
  height: 30px;
  padding: 0;
  align-items: center;
  justify-content: center;
  color: #93b8ba;
  background: #10282c;
  border: 1px solid #294b50;
  border-radius: 5px;
  cursor: pointer;
}
.task-console-head button:hover { color: #dffbf6; border-color: #5ca99f; }

.task-console-reopen {
  display: flex;
  position: absolute;
  inset: 0;
  z-index: 2;
  min-width: 0;
  min-height: 0;
  padding: 8px 5px;
  align-items: center;
  justify-content: flex-start;
  flex-direction: column;
  gap: 8px;
  color: #9cc5c5;
  background: #0b2025;
  border: 1px solid rgba(108, 228, 213, .18);
  border-radius: 7px;
  cursor: pointer;
}
.task-console-reopen span { writing-mode: vertical-rl; font-size: 11px; letter-spacing: .08em; }

:deep(.task-console-section) {
  min-width: 0;
  overflow: hidden;
  color: #d8efeb;
  background: #0b2025;
  border: 1px solid #2e5358;
  border-radius: 7px;
}
:deep(.task-console-section > summary) {
  display: flex;
  min-height: 46px;
  padding: 10px;
  box-sizing: border-box;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #d8efeb;
  background: #10282c;
  cursor: pointer;
  list-style: none;
}
:deep(.task-console-section > summary::-webkit-details-marker) { display: none; }
:deep(.task-console-section[open] > summary) { border-bottom: 1px solid #2b4b51; }
:deep(.task-console-section .task-console-section-title) { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; }
:deep(.task-console-section .task-console-section-meta) { color: #91b0b5; font-size: 10px; text-align: right; }
:deep(.task-console-section .task-console-section-body) { padding: 10px; }
:deep(.task-console-preparation-note) { margin: 0; color: #91b0b5; font-size: 10px; line-height: 1.75; }

@media (max-width: 760px) {
  .task-console-panel { position: relative; height: auto; max-height: 450px; }
  .task-console-reopen { position: relative; height: 48px; flex-direction: row; justify-content: center; }
  .task-console-reopen span { writing-mode: horizontal-tb; }
}
</style>

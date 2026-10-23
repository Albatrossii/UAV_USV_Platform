<template>
  <section class="workspace-toolbar-shell" aria-label="任务工具栏">
    <slot />
  </section>
</template>

<style scoped>
.workspace-toolbar-shell {
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  color: var(--workspace-text-primary);
  background: var(--workspace-toolbar-background);
  border: 1px solid var(--workspace-panel-border);
  border-radius: var(--workspace-panel-radius) var(--workspace-panel-radius) 0 0;
}
</style>

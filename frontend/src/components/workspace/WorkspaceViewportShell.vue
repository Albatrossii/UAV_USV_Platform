<template>
  <section class="workspace-viewport-shell" aria-label="Unity 任务画面">
    <slot />
  </section>
</template>

<style scoped>
.workspace-viewport-shell {
  position: relative;
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  color: var(--workspace-text-primary);
  background: var(--workspace-panel-background);
  border: 1px solid var(--workspace-panel-border);
  border-top: 0;
  border-radius: 0 0 var(--workspace-panel-radius) var(--workspace-panel-radius);
}
</style>

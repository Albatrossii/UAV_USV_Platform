<template>
  <section class="workspace-bottom-panel-shell" aria-label="工作空间辅助面板">
    <slot />
  </section>
</template>

<style scoped>
.workspace-bottom-panel-shell {
  position: relative;
  box-sizing: border-box;
  width: 100%;
  height: var(--workspace-footer-height);
  min-width: 0;
  min-height: 0;
  align-self: end;
  overflow: hidden;
  color: var(--workspace-text-primary);
  background: var(--workspace-footer-background);
  border: 1px solid var(--workspace-strong-border);
  border-radius: var(--workspace-footer-radius);
}
</style>

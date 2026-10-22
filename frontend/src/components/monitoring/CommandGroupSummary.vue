<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ metrics: Record<string, unknown> }>()
const groups = computed(() => {
  const assignments = props.metrics.fixedAssignments
  if (!assignments || typeof assignments !== 'object' || Array.isArray(assignments)) return []
  return ['THREAT-001', 'THREAT-002'].map((target, index) => ({
    target, label: `${index + 1}号敌船`,
    members: Object.entries(assignments).filter(([code, value]) =>
      /^(UAV|USV)-00[123]$/.test(code) && value === target).map(([code]) => code).sort(),
  })).filter(group => group.members.length)
})
const active = computed(() => props.metrics.algorithmMode === 'CAPTURE')
</script>

<template>
  <div v-if="groups.length" class="command-groups" aria-label="已保存的围捕编组">
    <span class="group-heading">围捕编组 <small>{{ active ? '已启用' : '待围捕 · 当前任务继续' }}</small></span>
    <span v-for="group in groups" :key="group.target" class="group-team">
      <strong>{{ group.label }}</strong><span>← {{ group.members.join(' / ') }}</span>
    </span>
  </div>
</template>

<style scoped>
.command-groups { display: flex; align-items: center; gap: 14px; height: 36px; flex: 0 0 36px; padding: 0 12px; overflow-x: auto; white-space: nowrap; color: #b9d9d7; background: #0b252b; border-top: 1px solid #23474e; font-size: 11px; }
.group-heading { color: #72dfcf; }
.group-heading small { margin-left: 6px; color: #91b3b5; font-size: 10px; }
.group-team { display: inline-flex; gap: 6px; }
.group-team strong { color: #f6ca70; font-weight: 500; }
</style>

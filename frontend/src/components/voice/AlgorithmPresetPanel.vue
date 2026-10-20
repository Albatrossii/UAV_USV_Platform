<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { algorithmPresets, loadAlgorithmPresets } from '@/services/algorithmPresets'
defineProps<{ disabled: boolean }>()
defineEmits<{ select: [text: string] }>()
const error = ref('')
onMounted(async () => { try { await loadAlgorithmPresets() } catch { error.value = '无法加载算法指令，请刷新重试。' } })
</script>
<template>
  <details class="preset-panel">
    <summary>算法指令 · 0–20</summary>
    <p>3 架无人机 · 3 艘无人艇 · 1–2 艘敌船。点击后预览确认，也可朗读完整指令。</p>
    <p>13–18 设置分组，20 启动围捕；0 撤回护航，不停止任务。</p>
    <p>建议说“执行第十三号指令”；追击可说“一号无人机追击二号目标”。若编号识别有误，修改文字后重新预览。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <button v-for="item in algorithmPresets" :key="item.id" type="button" :disabled="disabled"
      :title="item.effect" @click="$emit('select', `执行${item.id}号指令`)">
      <b>{{ item.id }}</b><span>{{ item.label }}</span>
    </button>
  </details>
</template>
<style scoped>
.preset-panel { padding:10px; margin:8px 0; border:1px solid #30575b; border-radius:6px; color:#b8dcd7; }
summary { cursor:pointer; font-size:12px; } p { font-size:11px; line-height:1.6; }
button { display:flex; gap:8px; width:100%; text-align:left; padding:7px; margin:3px 0; color:inherit; background:#102a30; border:1px solid #28484e; border-radius:4px; cursor:pointer; font-size:11px; }
button b { color:#7be1d3; min-width:20px; } button:disabled { opacity:.4; cursor:not-allowed; }
</style>

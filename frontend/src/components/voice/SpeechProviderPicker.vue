<script setup lang="ts">
import { computed } from 'vue'
import { Cloud, Cpu } from '@lucide/vue'
import type { VoiceSpeechProvider } from '@/types/voiceIntelligence'

const props = withDefaults(defineProps<{
  modelValue: VoiceSpeechProvider
  disabled?: boolean
  recognizing?: boolean
  resultProvider?: string
  resultModel?: string
  errorProvider?: VoiceSpeechProvider | null
}>(), { disabled: false, recognizing: false, resultProvider: '', resultModel: '', errorProvider: null })
const emit = defineEmits<{ 'update:modelValue': [value: VoiceSpeechProvider] }>()
const expected = computed(() => props.modelValue === 'local' ? 'local-asr' : 'aliyun-asr')
const matched = computed(() => props.resultProvider === expected.value)
const failed = computed(() => props.errorProvider === props.modelValue)
const status = computed(() => props.recognizing ? '正在识别'
  : failed.value ? '最近识别失败' : matched.value ? '最近识别成功' : '待识别验证')
function select(provider: VoiceSpeechProvider) {
  if (!props.disabled) emit('update:modelValue', provider)
}
</script>

<template>
  <section class="speech-provider-picker" aria-label="语音识别来源">
    <div class="picker-heading"><strong>识别来源</strong><small>{{ disabled ? '本次来源已固定' : '切换后用于下一次录音' }}</small></div>
    <div class="provider-options" role="group" aria-label="选择识别模型">
      <button type="button" :class="{ selected: modelValue === 'local' }" :aria-pressed="modelValue === 'local'"
        :disabled="disabled" @click="select('local')">
        <span><Cpu :size="15" aria-hidden="true" />本地模型<i aria-hidden="true" /></span>
        <small>Whisper Small · 本地部署</small>
      </button>
      <button type="button" :class="{ selected: modelValue === 'aliyun' }" :aria-pressed="modelValue === 'aliyun'"
        :disabled="disabled" @click="select('aliyun')">
        <span><Cloud :size="15" aria-hidden="true" />阿里云模型<i aria-hidden="true" /></span>
        <small>云端计算 · 需要互联网</small>
      </button>
    </div>
    <div class="provider-details">
      <div><span>{{ modelValue === 'local' ? '本地语音服务' : '阿里云语音服务' }}</span>
        <span class="provider-status" :class="{ success: matched && !failed, failed }" role="status">{{ status }}</span></div>
      <div><small>实际模型</small><small>{{ matched ? resultModel : '识别后显示' }}</small></div>
    </div>
    <p v-if="failed" class="provider-error">请检查所选服务。需要更换来源时，先结束本次请求，再手动切换。</p>
    <p class="provider-note">默认本地识别；服务不可用时不会自动转到云端。</p>
  </section>
</template>

<style scoped>
.speech-provider-picker{display:grid;gap:10px;min-width:0;color:#d5e9e3;font-size:11px;margin-bottom:14px}
.picker-heading,.provider-details>div{display:flex;justify-content:space-between;align-items:center;gap:8px}.picker-heading strong{font-size:11px;font-weight:500}.picker-heading small{font-size:9px;color:#91aaa6}
.provider-options{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px}.provider-options button{width:100%;min-width:0;padding:11px 10px;background:#0b2427;border:1px solid #2a4b4d;border-radius:7px;text-align:left;color:#cfe4df;cursor:pointer;font:inherit;transition:background .15s,border-color .15s}.provider-options button.selected{background:#163a33;border-color:#71dcbc}.provider-options button>span{display:flex;gap:6px;align-items:center;font-size:12px;white-space:nowrap}.provider-options button small{display:block;font-size:9px;color:#91b0a8;margin-top:5px}.provider-options i{margin-left:auto;flex:none;width:10px;height:10px;border:1px solid #587771;border-radius:50%}.provider-options .selected i{border:3px solid #71dcbc}.provider-options button:disabled{opacity:.55;cursor:not-allowed}.provider-options button:focus-visible{outline:2px solid #71dcbc;outline-offset:3px}
.provider-details{display:grid;gap:7px;background:#081d20;padding:10px;border-radius:6px}.provider-details small{font-size:9px;color:#8eaaa5;overflow-wrap:anywhere}.provider-details small:last-child{text-align:right}.provider-status{font-size:9px;color:#a3b6b2}.provider-status.success{color:#7ae1b9}.provider-status.failed,.provider-error{color:#f1a39b}.provider-note,.provider-error{margin:0;font-size:9px;line-height:1.7}.provider-note{color:#92aaa5}
</style>

<script setup lang="ts">
import { Crosshair, Radio, Timer, Wifi } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
import RadarPpiCanvas from '@/components/sensor/RadarPpiCanvas.vue'
import { useActiveExperimentStore } from '@/stores/activeExperiment'
import { useRadarSensorStore } from '@/stores/radarSensor'
import type { RadarItem, RadarOverview } from '@/types/sensor'

const store=useRadarSensorStore(),experiment=useActiveExperimentStore(),selected=ref<RadarItem|null>(null)
let timer:number|undefined
const clockNow = ref(Date.now())
let freshnessTimer: number | undefined
const overview=computed<RadarOverview>(()=>store.overview??({connected:false,onlineCount:0,totalCount:0,updatedAt:0,obstacleCount:0,detectionCount:0,nearestObstacleRange:null,latestTargetId:'',items:[],spectrumConnected:false,spectrumVehicleId:'',spectrumSensorId:'',spectrumStreamId:'',spectrumGatewaySequence:null,spectrumSequence:null,spectrumCapturedAt:null,spectrumStartHz:null,spectrumStopHz:null,spectrumBinHz:null,spectrumRbwHz:null,spectrumRefLevelDbm:null,spectrumPeakHz:null,spectrumPeakDbm:null,spectrumTemperatureC:null,spectrumPowersDbm:[]}))
const freshness=computed(()=>overview.value.updatedAt?Math.max(0,clockNow.value-overview.value.updatedAt):null)
watch(() => overview.value.items, (items) => {
  const previous = selected.value
  if (previous) selected.value = items.find(item => item.id === previous.id && item.deviceId === previous.deviceId) ?? null
})
const latestEvents=computed(()=>[...overview.value.items].sort((a,b)=>b.timestampMs-a.timestampMs).slice(0,4))
const fmt=(v:number|null,d=1)=>v==null?'--':v.toFixed(d)
const time=(v:number)=>v?new Intl.DateTimeFormat('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date(v)):'--'
onMounted(()=>{void store.refresh();timer=window.setInterval(()=>void store.refresh(true),2000);freshnessTimer=window.setInterval(()=>{clockNow.value=Date.now()},1000)})
onBeforeUnmount(()=>{if(timer)clearInterval(timer);if(freshnessTimer)clearInterval(freshnessTimer)})
</script>

<template>
  <ConsoleLayout title="基地雷达仿真回波" eyebrow="RADAR ECHO" :show-refresh="false">
    <div v-if="store.error" class="radar-error" role="alert">{{ store.error }}；连接恢复后自动重新获取数据。</div>
    <template #actions><span class="chip" :class="{online:overview.connected}"><Wifi :size="14"/>base_radar {{ overview.onlineCount }}/{{ overview.totalCount||'--' }}</span><span class="chip"><Crosshair :size="14"/>扫描点 {{ overview.items.length }}</span><span class="chip"><Timer :size="14"/>{{ freshness==null?'--':`${freshness} ms` }}</span></template>
    <section class="radar-stage">
      <div class="ppi"><RadarPpiCanvas :items="overview.items" :selected-id="selected?.id" @select="selected=$event"/></div>
      <article class="island link"><span>RADAR LINK</span><b>{{ overview.connected?'仿真回波在线':'等待 base_radar 数据' }}</b><dl><div><dt>在线源</dt><dd>{{ overview.onlineCount }}/{{ overview.totalCount||'--' }}</dd></div><div><dt>刷新周期</dt><dd>2 秒</dd></div><div><dt>最近回波</dt><dd>{{ fmt(overview.nearestObstacleRange) }} m</dd></div></dl></article>
      <article class="island evidence" :class="{empty:!selected}"><span>SCAN SAMPLE</span><template v-if="selected"><b>{{ selected.id }}</b><dl><div><dt>类型</dt><dd>{{ selected.kind }}</dd></div><div><dt>距离</dt><dd>{{ fmt(selected.range) }} m</dd></div><div><dt>方位</dt><dd>{{ fmt(selected.bearing) }}°</dd></div><div><dt>强度</dt><dd>{{ selected.confidence==null?'--':`${Math.round(selected.confidence*100)}%` }}</dd></div></dl></template><p v-else>点击扫描点查看回波字段</p></article>
      <article class="island legend"><span>图例与量程</span><div><i class="target"/>扫描回波 / 点云</div><div><i class="obstacle"/>兼容障碍点</div><div><i class="selected"/>当前选中</div><small>圆形量程环、方位刻度和扫描扇区始终保留；支持点云聚焦、滚轮缩放、拖拽平移及全局复位。</small></article>
      <article class="island events"><span>RECENT ECHOES</span><div v-for="item in latestEvents" :key="`${item.deviceId}-${item.id}-${item.timestampMs}`"><time>{{ time(item.timestampMs) }}</time><b>{{ item.id }}</b><em>{{ item.kind }}</em></div><p v-if="!latestEvents.length">暂无 base_radar 扫描回波</p></article>
      <div class="run"><Radio :size="13"/>{{ experiment.label }} · {{ experiment.algorithmCode||'等待算法任务' }}</div>
      <div class="counter"><span>障碍 {{ overview.obstacleCount }}</span><span>扫描点 {{ overview.detectionCount }}</span><span>最新航迹 {{ overview.latestTargetId||'--' }}</span></div>
    </section>
  </ConsoleLayout>
</template>

<style scoped>
.radar-error{margin-bottom:12px;padding:12px;border:1px solid #b87c32;border-radius:6px;color:#ffd38a;background:#322514;}
.chip{display:inline-flex;align-items:center;gap:6px;padding:7px 10px;border:1px solid #24505a;border-radius:6px;background:#061a21;color:#83a6aa;font-size:11px}.chip.online{color:#56e6b3}.radar-stage{position:relative;height:calc(100vh - 132px);min-height:670px;overflow:hidden;border:1px solid #17424d;border-radius:12px;background:radial-gradient(circle at 50% 49%,#0b3938 0,#03171e 42%,#020c12 76%),linear-gradient(#3eb4be09 1px,transparent 1px),linear-gradient(90deg,#3eb4be09 1px,transparent 1px);background-size:auto,42px 42px,42px 42px;box-shadow:inset 0 0 100px #0009}.ppi{position:absolute;left:50%;top:50%;width:min(72vh,780px);height:min(72vh,780px);transform:translate(-50%,-50%)}.island{position:absolute;z-index:3;width:220px;padding:14px;border:1px solid #285a65;border-radius:9px;background:#041820e8;box-shadow:0 16px 35px #0008;backdrop-filter:blur(7px)}.island>span{color:#53d8e8;font-size:9px;font-weight:900;letter-spacing:.13em}.island>b{display:block;margin:5px 0 12px;color:#ecfffd;font-size:16px}.island dl{display:grid;gap:7px;margin:0}.island dl div{display:flex;justify-content:space-between;padding-top:7px;border-top:1px solid #24505a55}.island dt{color:#71989d;font-size:10px}.island dd{margin:0;color:#dff7f5;font-size:10px}.link{left:2%;top:4%}.evidence{right:2%;top:4%}.evidence p,.events p{color:#73999d;font-size:10px}.legend{left:2%;bottom:4%}.legend>div{display:flex;align-items:center;gap:8px;margin-top:9px;color:#b8d4d3;font-size:10px}.legend i{width:7px;height:7px;border-radius:50%;background:#61e7bb;box-shadow:0 0 7px currentColor}.legend i.obstacle{background:#ffae4b}.legend i.selected{border:2px solid #ffd15a;background:transparent}.legend small{display:block;margin-top:11px;color:#60858a;line-height:1.5}.events{right:2%;bottom:4%;width:250px}.events>div{display:grid;grid-template-columns:58px 1fr auto;gap:7px;padding:7px 0;border-top:1px solid #24505a44;font-size:9px}.events time{color:#688c90}.events b{color:#e8f9f7}.events em{color:#55e5b1;font-style:normal}.run{position:absolute;top:3%;left:50%;display:flex;align-items:center;gap:6px;transform:translateX(-50%);color:#57e5b2;font-size:10px}.counter{position:absolute;left:50%;bottom:3%;display:flex;gap:8px;transform:translateX(-50%)}.counter span{padding:7px 10px;border:1px solid #24505a;border-radius:999px;background:#041820dc;color:#a2c2c2;font-size:9px}@media(max-width:1200px){.ppi{width:min(64vh,650px);height:min(64vh,650px)}.island{width:190px}.events{width:220px}}@media(max-width:900px){.radar-stage{min-height:800px}.ppi{width:540px;height:540px}.island{width:170px}.counter{display:none}}
.radar-stage{height:calc(100dvh - 174px);min-height:610px}.ppi{width:min(62vh,690px);height:min(62vh,690px)}
@media(max-height:760px){.radar-stage{min-height:560px}.ppi{width:min(57vh,600px);height:min(57vh,600px)}}
.radar-stage { height: max(500px, calc(100dvh - 156px)); min-height: 500px; }
.ppi { width: min(60dvh, 48cqw); height: auto; aspect-ratio: 1; }
@container workspace (max-width: 1100px) {
  .radar-stage { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); height: auto; min-height: 0; gap: 12px; padding: 14px; }
  .ppi { position: relative; inset: auto; grid-column: 1 / -1; justify-self: center; transform: none; width: min(100%, 60dvh); height: auto; aspect-ratio: 1; }
  .island { position: relative; inset: auto; width: auto; min-width: 0; }
  .run, .counter { position: relative; inset: auto; grid-column: 1 / -1; transform: none; flex-wrap: wrap; overflow-wrap: anywhere; }
  .counter { display: flex; }
  .events b { overflow-wrap: anywhere; }
}
@container workspace (max-width: 480px) {
  .radar-stage { grid-template-columns: minmax(0, 1fr); }
}
</style>

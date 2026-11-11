<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import ConsoleLayout from '@/components/layout/ConsoleLayout.vue'
const canvas=ref<HTMLCanvasElement|null>(null), connected=ref(false), error=ref(''), receiveHz=ref(0), sourceHz=ref(0), renderHz=ref(0), points=ref(0), sourcePoints=ref(0), kbps=ref(0), age=ref(0), sequence=ref(0)
const range=ref(25), pointSize=ref(3), history=ref(.5), grid=ref(true), paused=ref(false)
const status=computed(()=>error.value||(!connected.value?'正在连接点云通道':age.value>1500?'雷达数据中断':'实时点云在线'))
let socket:WebSocket|null=null, retry=0, animation=0, timer=0, disposed=false, lastFrame=0, received:number[]=[], bytes:{t:number,n:number}[]=[]
let yaw=-.8,pitch=.65,distance=22,panX=0,panY=0,panZ=0,drag=false,lastX=0,lastY=0,button=0
let gl:WebGLRenderingContext|null=null, program:WebGLProgram|null=null, buffer:WebGLBuffer|null=null, resize:ResizeObserver|null=null
let clouds:{t:number,xyz:Float32Array}[]=[], dirty=true, vertexCount=0, gridCount=0, renderTimes:number[]=[]
let decoding=false,pending:ArrayBuffer|null=null
function reset(){yaw=-.8;pitch=.65;distance=22;panX=0;panY=0;panZ=0}
function top(){pitch=1.56;yaw=0;panX=0;panY=0;panZ=0}
async function decode(data:ArrayBuffer){
 if(decoding){pending=data;return} decoding=true
 try{
  const view=new DataView(data)
  if(data.byteLength<32||view.getUint32(0,true)!==0x31444350)throw Error('无效点云协议')
  const n=view.getUint32(8,true),seq=view.getUint32(4,true)
  if(n<1||n>100000)throw Error('无效点数')
  let raw:ArrayBuffer
  if(view.getUint32(28,true)===1){raw=await new Response(new Blob([data.slice(32)]).stream().pipeThrough(new DecompressionStream('deflate'))).arrayBuffer()}else raw=data.slice(32)
  if(raw.byteLength!==n*6)throw Error('点云长度不匹配')
  const input=new DataView(raw),xyz=new Float32Array(n*3)
  for(let i=0;i<n*3;i++)xyz[i]=input.getInt16(i*2,true)*.01
  const now=performance.now();lastFrame=now;received.push(now);bytes.push({t:now,n:data.byteLength});sequence.value=seq;sourceHz.value=view.getFloat32(12,true);sourcePoints.value=view.getUint32(16,true)
  if(!paused.value){clouds.push({t:now,xyz});dirty=true} error.value=''
 }catch(e){error.value=e instanceof Error?e.message:'点云解码失败'}
 finally{decoding=false;const next=pending;pending=null;if(next&&!disposed)void decode(next)}
}
function connect(){
 if(disposed)return
 socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/lidar/stream`);socket.binaryType='arraybuffer'
 socket.onopen=()=>{connected.value=true;error.value=''}
 socket.onmessage=e=>{if(e.data instanceof ArrayBuffer)void decode(e.data)}
 socket.onerror=()=>{error.value='点云链路连接失败，正在重连'}
 socket.onclose=()=>{connected.value=false;if(!disposed)retry=window.setTimeout(connect,2000)}
}
function shader(type:number,source:string){const s=gl!.createShader(type)!;gl!.shaderSource(s,source);gl!.compileShader(s);if(!gl!.getShaderParameter(s,gl!.COMPILE_STATUS))throw Error(gl!.getShaderInfoLog(s)||'着色器错误');return s}
function setup(){
 gl=canvas.value!.getContext('webgl',{antialias:true,alpha:false});if(!gl){error.value='浏览器未启用 WebGL';return}
 program=gl.createProgram()!
 gl.attachShader(program,shader(gl.VERTEX_SHADER,`attribute vec3 aPosition;uniform vec3 uTarget;uniform vec4 uCamera;uniform float uAspect,uSize,uRange,uGrid;varying vec3 color;
 void main(){vec3 p=aPosition-uTarget;float cy=cos(uCamera.x),sy=sin(uCamera.x),cp=cos(uCamera.y),sp=sin(uCamera.y);float x=cy*p.x-sy*p.y,y=sy*p.x+cy*p.y;float z=uCamera.z-cp*y-sp*p.z;float v=-sp*y+cp*p.z;gl_Position=vec4(x*1.9/uAspect,v*1.9,1.0002*z-.20002,z);gl_PointSize=clamp(uSize*14.0/max(z,1.0),1.0,9.0);float t=clamp(length(aPosition)/uRange,0.0,1.0);color=t<.5?mix(vec3(.1,.8,1.),vec3(.5,1.,.3),t*2.):mix(vec3(.5,1.,.3),vec3(1.,.25,.12),(t-.5)*2.);if(uGrid>.5)color=vec3(.08,.25,.28);}`))
 gl.attachShader(program,shader(gl.FRAGMENT_SHADER,'precision mediump float;varying vec3 color;void main(){gl_FragColor=vec4(color,1.0);}'))
 gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw Error('WebGL程序链接失败')
 buffer=gl.createBuffer();gl.useProgram(program);gl.bindBuffer(gl.ARRAY_BUFFER,buffer);const pos=gl.getAttribLocation(program,'aPosition');gl.enableVertexAttribArray(pos);gl.vertexAttribPointer(pos,3,gl.FLOAT,false,0,0);gl.enable(gl.DEPTH_TEST)
 resize=new ResizeObserver(()=>{const c=canvas.value!;const ratio=Math.min(devicePixelRatio,2);c.width=Math.max(1,c.clientWidth*ratio);c.height=Math.max(1,c.clientHeight*ratio);gl!.viewport(0,0,c.width,c.height)});resize.observe(canvas.value!)
 draw()
}
function draw(){
 if(disposed||!gl||!program)return
 animation=requestAnimationFrame(draw);const now=performance.now();renderTimes.push(now)
 if(!paused.value){const count=clouds.length;clouds=clouds.filter(c=>now-c.t<history.value*1000);if(count!==clouds.length)dirty=true}
 if(dirty){const ground:number[]=[];for(let i=-20;i<=20;i+=2)ground.push(-20,i,0,20,i,0,i,-20,0,i,20,0);gridCount=ground.length/3;const total=clouds.reduce((n,c)=>n+c.xyz.length,0);const vertices=new Float32Array(ground.length+total);vertices.set(ground);let offset=ground.length;for(const c of clouds){vertices.set(c.xyz,offset);offset+=c.xyz.length}vertexCount=total/3;points.value=vertexCount;gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,vertices,gl.DYNAMIC_DRAW);dirty=false}
 gl.clearColor(.015,.035,.05,1);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.useProgram(program)
 gl.uniform3f(gl.getUniformLocation(program,'uTarget'),panX,panY,panZ);gl.uniform4f(gl.getUniformLocation(program,'uCamera'),yaw,pitch,distance,0);gl.uniform1f(gl.getUniformLocation(program,'uAspect'),canvas.value!.width/canvas.value!.height);gl.uniform1f(gl.getUniformLocation(program,'uSize'),pointSize.value);gl.uniform1f(gl.getUniformLocation(program,'uRange'),range.value)
 if(grid.value){gl.uniform1f(gl.getUniformLocation(program,'uGrid'),1);gl.drawArrays(gl.LINES,0,gridCount)}gl.uniform1f(gl.getUniformLocation(program,'uGrid'),0);gl.drawArrays(gl.POINTS,gridCount,vertexCount)
}
function down(e:PointerEvent){drag=true;button=e.button;lastX=e.clientX;lastY=e.clientY;canvas.value?.setPointerCapture(e.pointerId)}
function move(e:PointerEvent){if(!drag)return;const x=e.clientX-lastX,y=e.clientY-lastY;lastX=e.clientX;lastY=e.clientY;if(button===2||e.shiftKey){panX-=x*distance*.0015;panY+=y*distance*.0015}else{yaw+=x*.007;pitch=Math.max(-1.5,Math.min(1.56,pitch+y*.007))}}
function wheel(e:WheelEvent){distance=Math.max(1,Math.min(250,distance*Math.exp(e.deltaY*.001)))}
onMounted(()=>{try{setup();connect()}catch(e){error.value=String(e)}timer=window.setInterval(()=>{const now=performance.now();received=received.filter(t=>now-t<2000);renderTimes=renderTimes.filter(t=>now-t<1000);bytes=bytes.filter(b=>now-b.t<2000);receiveHz.value=received.length>1?(received.length-1)*1000/(received[received.length-1]!-received[0]!):0;renderHz.value=renderTimes.length;kbps.value=bytes.reduce((n,b)=>n+b.n,0)*8/2/1000;age.value=lastFrame?now-lastFrame:99999;if(now-lastFrame>2000)receiveHz.value=0},500)})
onBeforeUnmount(()=>{disposed=true;socket?.close();clearTimeout(retry);clearInterval(timer);cancelAnimationFrame(animation);resize?.disconnect();if(gl){gl.deleteBuffer(buffer);gl.deleteProgram(program)}})
</script>
<template>
 <ConsoleLayout title="三维激光点云" eyebrow="MID-360S · LIVE POINT CLOUD" :show-refresh="false">
 <template #actions><span class="badge" :class="{online:connected&&age<1500}">{{ connected&&age<1500?'● LIVE':'○ WAITING' }} · {{status}}</span></template>
 <section class="viewer">
  <header class="toolbar"><div><b>LIVOX MID-360S</b><small>机载坐标系 · livox_frame · 距离着色</small></div><div class="buttons"><button @click="paused=!paused">{{paused?'继续显示':'冻结视图'}}</button><button @click="top">俯视</button><button @click="reset">重置视角</button></div></header>
  <div class="stage"><canvas ref="canvas" aria-label="MID360S实时三维点云，按距离着色" @pointerdown="down" @pointermove="move" @pointerup="drag=false" @pointercancel="drag=false" @wheel.prevent="wheel" @contextmenu.prevent @dblclick="reset"/>
   <aside class="stats"><span class="kicker">REALTIME TELEMETRY</span><dl><div><dt>雷达发布</dt><dd>{{sourceHz.toFixed(1)}} <small>Hz</small></dd></div><div><dt>接收帧率</dt><dd :class="{good:receiveHz>30}">{{receiveHz.toFixed(1)}} <small>Hz</small></dd></div><div><dt>画面渲染</dt><dd>{{renderHz}} <small>FPS</small></dd></div><div><dt>当前帧有效点数</dt><dd>{{sourcePoints.toLocaleString()}}</dd></div><div><dt>累计显示点数</dt><dd>{{points.toLocaleString()}}</dd></div><div><dt>压缩数据速率</dt><dd>{{(kbps/1000).toFixed(2)}} <small>Mbps</small></dd></div><div><dt>最后接收距今</dt><dd>{{age>9999?'--':Math.round(age)}} <small>ms</small></dd></div></dl><span class="frame">FRAME {{sequence}}</span></aside>
   <div v-if="age>2000" class="empty">{{lastFrame?'点云数据已中断':'等待真实雷达点云'}}<small>机载电脑 → 31 → 语义链路 → 32 → 地面端</small></div>
   <div class="axis"><b style="color:#ff6c62">X</b> · <b style="color:#91ef76">Y</b> · <b style="color:#63c5ff">Z↑</b><small>单位 m · 网格间隔 2 m</small></div>
   <div class="legend"><span>距离 / RANGE</span><i/><div><b>0 m</b><b>{{range}} m</b></div></div>
  </div>
  <footer class="controls"><label>点大小 <input v-model.number="pointSize" type="range" min="1" max="8" step=".5"/>{{pointSize}}</label><label>距离色标 <select v-model.number="range"><option :value="10">10 m</option><option :value="25">25 m</option><option :value="50">50 m</option><option :value="100">100 m</option></select></label><label>点云保留 <select v-model.number="history"><option :value=".1">0.1秒</option><option :value=".5">0.5秒</option><option :value="1">1秒</option><option :value="2">2秒</option></select></label><label><input v-model="grid" type="checkbox"/>地面网格</label><span>左键旋转 · 右键平移 · 滚轮缩放 · 双击复位</span></footer>
 </section><p class="note">真实 PointCloud2 · 厘米坐标量化 + DEFLATE 压缩 · 每帧最多传输 600 点 · 仅保留最新待传帧 · 接收帧率与渲染帧率分别计量</p>
 </ConsoleLayout>
</template>
<style scoped>
.viewer{height:calc(100vh - 175px);min-height:520px;border:1px solid #21444c;border-radius:16px;overflow:hidden;background:#061018;display:flex;flex-direction:column}.toolbar{display:flex;justify-content:space-between;align-items:center;padding:18px 22px;background:#0a1c24;border-bottom:1px solid #203b43}.toolbar b{font-size:17px;letter-spacing:1px;color:#ddf6f5}.toolbar small{display:block;color:#73979e;margin-top:5px}.buttons{display:flex;gap:8px}button,select{background:#102b34;color:#bde9e8;border:1px solid #30535c;border-radius:6px;padding:8px 12px;cursor:pointer}button:hover{border-color:#4bdac8}.stage{position:relative;flex:1;min-height:0}canvas{width:100%;height:100%;display:block;touch-action:none;cursor:grab}.stats{position:absolute;top:20px;left:20px;width:206px;padding:17px;background:#071b24df;border:1px solid #22454d;border-radius:10px;pointer-events:none}.kicker{font-size:10px;letter-spacing:1.8px;color:#6fe1dc}.stats dl{margin:14px 0}.stats dl div{display:flex;justify-content:space-between;margin:11px 0;align-items:center}.stats dt{font-size:12px;color:#86a6ad}.stats dd{margin:0;color:#e2eeee;font-variant-numeric:tabular-nums}.stats small{font-size:10px;color:#78949b}.stats .good{color:#62ecc1}.frame{font-size:10px;letter-spacing:1.5px;color:#6b9099}.axis{position:absolute;bottom:20px;left:22px;font-size:15px}.axis small{display:block;color:#62858e;font-size:11px;margin-top:6px}.legend{position:absolute;bottom:22px;right:22px;width:190px;font-size:10px;letter-spacing:1px;color:#b6d4da}.legend i{display:block;height:7px;margin:8px 0;border-radius:4px;background:linear-gradient(90deg,#1accff,#80ff4c,#ff401f)}.legend div{display:flex;justify-content:space-between;font-weight:normal}.controls{display:flex;align-items:center;gap:20px;flex-wrap:wrap;padding:14px 20px;background:#0a1c24;border-top:1px solid #203b43;color:#9ab6bc;font-size:12px}.controls label{display:flex;align-items:center;gap:8px}.controls input[type=range]{width:80px;accent-color:#4edccc}.controls span{margin-left:auto;font-size:11px;color:#62838c}.controls select{padding:5px}.empty{position:absolute;top:48%;left:50%;transform:translate(-50%,-50%);color:#9eb8c0;text-align:center;pointer-events:none}.empty small{display:block;margin-top:12px;font-size:11px;color:#51747e}.badge{color:#e1b86d;padding:8px 12px;border:1px solid #28454e;border-radius:7px;font-size:12px}.badge.online{color:#67e4b6}.note{font-size:11px;color:#658790;margin:10px 2px}@media(max-width:1000px){.controls{gap:10px}.stats{width:170px;padding:12px}.viewer{height:calc(100vh - 160px)}.controls span{display:none}}
</style>

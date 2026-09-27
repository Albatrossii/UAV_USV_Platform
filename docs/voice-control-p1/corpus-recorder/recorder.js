const speaker = document.querySelector('#speaker')
const directoryButton = document.querySelector('#directory')
const directoryState = document.querySelector('#directoryState')
const progress = document.querySelector('#progress')
const position = document.querySelector('#position')
const group = document.querySelector('#group')
const filename = document.querySelector('#filename')
const prompt = document.querySelector('#prompt')
const instruction = document.querySelector('#instruction')
const previous = document.querySelector('#previous')
const next = document.querySelector('#next')
const record = document.querySelector('#record')
const stop = document.querySelector('#stop')
const save = document.querySelector('#save')
const preview = document.querySelector('#preview')
const status = document.querySelector('#status')

let allTasks = [], tasks = [], index = 0, directoryHandle = null
let recorder = null, stream = null, chunks = [], blob = null, previewUrl = null, timer = null

function completedKey() { return `e1-corpus-completed:${speaker.value}` }
function completed() { try { return new Set(JSON.parse(localStorage.getItem(completedKey()) || '[]')) } catch { return new Set() } }
function setCompleted(values) { localStorage.setItem(completedKey(), JSON.stringify([...values])) }
function task() { return tasks[index] }
function groupText(value) { return ({ quiet:'安静组', noise:'噪声组', terminology:'术语组' })[value] || value }
function groupInstruction(value) {
  if (value === 'noise') return '请准备句中描述的可控背景声，确保人声仍清晰可辨。'
  if (value === 'terminology') return '请按原文朗读术语，不要自行替换同义词。'
  return '请在安静环境中，以自然语速完整朗读。'
}
function cleanupPreview() {
  if (previewUrl) URL.revokeObjectURL(previewUrl)
  previewUrl = null; blob = null; preview.hidden = true; preview.removeAttribute('src'); save.disabled = true
}
function render() {
  const current = task()
  if (!current) return
  const done = completed()
  progress.value = done.size
  position.textContent = `${index + 1} / ${tasks.length}（已保存 ${done.size}）`
  group.textContent = groupText(current.group)
  filename.textContent = `${current.id}.webm`
  prompt.textContent = current.reference
  instruction.textContent = groupInstruction(current.group)
  previous.disabled = index === 0 || recorder?.state === 'recording'
  next.disabled = index === tasks.length - 1 || recorder?.state === 'recording'
  status.textContent = done.has(current.id) ? '这条已保存，可以重新录制覆盖。' : ''
}
function selectSpeaker() {
  tasks = allTasks.filter(item => item.speakerId === speaker.value)
  index = 0; cleanupPreview(); render()
}
async function chooseDirectory() {
  if (!window.showDirectoryPicker) { status.textContent = '当前浏览器不支持目录写入，将在保存时下载单个文件。'; return }
  try {
    directoryHandle = await window.showDirectoryPicker({ mode:'readwrite' })
    directoryState.textContent = `保存到：${directoryHandle.name}`
  } catch (error) {
    if (error?.name !== 'AbortError') status.textContent = '无法使用所选目录，请检查权限。'
  }
}
async function startRecording() {
  cleanupPreview(); status.textContent = '正在请求麦克风权限…'
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio:{ channelCount:1, echoCancellation:false, noiseSuppression:false, autoGainControl:false } })
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm'
    chunks = []; recorder = new MediaRecorder(stream, { mimeType, audioBitsPerSecond:64000 })
    recorder.addEventListener('dataavailable', event => { if (event.data.size) chunks.push(event.data) })
    recorder.addEventListener('stop', () => {
      clearTimeout(timer); stream?.getTracks().forEach(track => track.stop()); stream = null
      blob = new Blob(chunks, { type:mimeType }); previewUrl = URL.createObjectURL(blob)
      preview.src = previewUrl; preview.hidden = false; save.disabled = blob.size === 0
      record.disabled = false; stop.disabled = true; previous.disabled = index === 0; next.disabled = index === tasks.length - 1
      status.textContent = blob.size ? '请先试听，确认无误后保存。' : '没有录到有效音频，请重新录制。'
    })
    recorder.start(); record.disabled = true; stop.disabled = false; previous.disabled = true; next.disabled = true
    status.textContent = '录音中…'; timer = setTimeout(stopRecording, 15000)
  } catch (error) {
    stream?.getTracks().forEach(track => track.stop()); stream = null
    status.textContent = error?.name === 'NotAllowedError' ? '麦克风权限被拒绝，请在浏览器地址栏重新授权。' : '无法开始录音，请检查麦克风。'
  }
}
function stopRecording() { if (recorder?.state === 'recording') recorder.stop() }
async function saveRecording() {
  if (!blob || !task()) return
  const name = `${task().id}.webm`
  try {
    if (directoryHandle) {
      const file = await directoryHandle.getFileHandle(name, { create:true })
      const writable = await file.createWritable(); await writable.write(blob); await writable.close()
    } else {
      const link = document.createElement('a'); link.href = previewUrl; link.download = name; link.click()
    }
    const done = completed(); done.add(task().id); setCompleted(done)
    status.textContent = `${name} 已保存。`; cleanupPreview()
    if (index < tasks.length - 1) index += 1
    render()
  } catch { status.textContent = '保存失败，请重新选择目录或使用浏览器下载。' }
}
function move(delta) { if (recorder?.state === 'recording') return; cleanupPreview(); index = Math.max(0, Math.min(tasks.length - 1, index + delta)); render() }

speaker.addEventListener('change', selectSpeaker)
directoryButton.addEventListener('click', chooseDirectory)
record.addEventListener('click', startRecording); stop.addEventListener('click', stopRecording); save.addEventListener('click', saveRecording)
previous.addEventListener('click', () => move(-1)); next.addEventListener('click', () => move(1))
window.addEventListener('beforeunload', () => { stream?.getTracks().forEach(track => track.stop()); if (previewUrl) URL.revokeObjectURL(previewUrl) })

try {
  const response = await fetch('../E1-60条录音任务表.json', { cache:'no-store' })
  if (!response.ok) throw new Error('task data unavailable')
  allTasks = await response.json(); selectSpeaker()
} catch { prompt.textContent = '任务表加载失败'; status.textContent = '请从 localhost 启动该工具，不要直接双击 HTML。' }

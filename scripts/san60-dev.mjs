// Run the platform and the supplied SAN60 package without copying its runtime or BAG.
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import fs from 'node:fs'
import net from 'node:net'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const packageRoot = path.resolve(process.env.SAN60_PACKAGE_ROOT || path.join(root, '..', 'SAN60', 'SAN60_Windows_Offline_20261019'))
const port = Number(process.env.SAN60_PORT || 8775)
const backendPort = Number(process.env.SERVER_PORT || 8081)
const frontendPort = Number(process.env.VITE_DEV_PORT || 5177)
if (!Number.isInteger(port) || port < 1024 || port > 65515) throw new Error('SAN60_PORT must be 1024–65515')
if (!Number.isInteger(backendPort) || backendPort < 1024 || backendPort > 65535) throw new Error('SERVER_PORT must be 1024–65535')
if (!Number.isInteger(frontendPort) || frontendPort < 1024 || frontendPort > 65535) throw new Error('VITE_DEV_PORT must be 1024–65535')
if (new Set([port, backendPort, frontendPort]).size !== 3) throw new Error('SAN60, backend and frontend ports must differ')
const serviceUrl = `http://127.0.0.1:${port}`
const python = path.join(packageRoot, 'runtime', 'python.exe')
const algorithmPython = path.join(root, 'algorithm-service', '.venv', 'Scripts', 'python.exe')
if (!fs.existsSync(python) || !fs.existsSync(path.join(packageRoot, 'launcher.py'))) throw new Error('Set SAN60_PACKAGE_ROOT to the fully extracted Windows package')
if (process.platform !== 'win32') throw new Error('The supplied portable runtime requires Windows x64')
const env = {
  ...process.env,
  SAN60_SERVICE_URL: serviceUrl,
  PYTHONIOENCODING: 'utf-8',
  SERVER_PORT: String(backendPort),
  COMMAND_DISPATCH_MODE: process.env.COMMAND_DISPATCH_MODE || 'browser-unity',
  ROS_GATEWAY_V1_ENABLED: process.env.ROS_GATEWAY_V1_ENABLED || 'false',
  VISUAL_SENSOR_WEBSOCKET_ENABLED: process.env.VISUAL_SENSOR_WEBSOCKET_ENABLED || 'false',
  VOICE_CONTROL_ENABLED: process.env.VOICE_CONTROL_ENABLED || 'true',
  APP_GIMBAL_CAMERA_STREAM_ENABLED: process.env.APP_GIMBAL_CAMERA_STREAM_ENABLED || 'false',
  ...(process.env.APP_ALGORITHM_PYTHON_COMMAND
    ? { APP_ALGORITHM_PYTHON_COMMAND: process.env.APP_ALGORITHM_PYTHON_COMMAND }
    : fs.existsSync(algorithmPython) ? { APP_ALGORITHM_PYTHON_COMMAND: algorithmPython } : {}),
  VITE_DEV_PORT: String(frontendPort),
  VITE_BACKEND_TARGET: process.env.VITE_BACKEND_TARGET || `http://127.0.0.1:${backendPort}`,
  VITE_VOICE_P1_PREPARATION: process.env.VITE_VOICE_P1_PREPARATION || 'true',
  VITE_VOICE_P1_BACKEND: process.env.VITE_VOICE_P1_BACKEND || 'true',
  VITE_VOICE_P1_MOCK: process.env.VITE_VOICE_P1_MOCK || 'false',
  VITE_VOICE_P0_MOCK: process.env.VITE_VOICE_P0_MOCK || 'false',
  VITE_VOICE_UNITY_PRESENTATION_V1: process.env.VITE_VOICE_UNITY_PRESENTATION_V1 || 'true',
  VITE_VOICE_ASR_ONLY: process.env.VITE_VOICE_ASR_ONLY || 'false',
}
const children = []
let stopping = false
function start(name, command, args, cwd = root) {
  const child = spawn(command, args, { cwd, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] })
  children.push(child)
  child.stdout.on('data', data => process.stdout.write(`[${name}] ${data}`))
  child.stderr.on('data', data => process.stderr.write(`[${name}] ${data}`))
  child.on('error', error => { console.error(`[${name}] ${error.message}`); stop(1) })
  child.on('exit', code => { if (!stopping) { console.error(`[${name}] exited (${code})`); stop(code || 1) } })
  return child
}
function stop(code = 0) {
  if (stopping) return
  stopping = true
  for (const child of children) if (child.pid) spawn('taskkill', ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true, stdio: 'ignore' })
  setTimeout(() => process.exit(code), 600)
}
process.on('SIGINT', () => stop()); process.on('SIGTERM', () => stop())
// An optional parent process can request the same cleanup without Windows' forceful child.kill().
process.on('message', message => { if (message === 'stop') stop() })
async function available(checkPort) {
  await new Promise((resolve, reject) => {
    const socket = net.createServer()
    socket.once('error', () => reject(new Error(`Port ${checkPort} is occupied; stop the previous service before starting this stack`)))
    socket.listen(checkPort, '127.0.0.1', () => socket.close(resolve))
  })
}
try {
  for (const checkPort of [port, backendPort, frontendPort]) await available(checkPort)
  start('san60', python, [path.join(packageRoot, 'launcher.py'), '--mode', 'manual', '--port', String(port), '--no-browser'], packageRoot)
  let online = false
  for (let attempt = 0; attempt < 40; attempt++) {
    try { const response = await fetch(`${serviceUrl}/api/state`, { signal: AbortSignal.timeout(1500) }); if (response.ok) { online = true; break } } catch { /* wait for engine startup */ }
    if (stopping) process.exit(1)
    await new Promise(resolve => setTimeout(resolve, 250))
  }
  if (!online) throw new Error('SAN60 did not start on the configured port')
  start('backend', 'cmd.exe', ['/d', '/s', '/c', 'npm run dev:backend'])
  start('frontend', 'cmd.exe', ['/d', '/s', '/c', 'npm run dev:frontend'])
  console.log(`\nOpen http://127.0.0.1:${frontendPort}/login after the backend is ready.`)
  console.log(`The SAN60 page is at http://127.0.0.1:${frontendPort}/situational-awareness.`)
  console.log('Local backend and voice defaults match the previous startup flow; explicit environment settings take precedence.')
  console.log('Ctrl+C stops only this script\'s child processes.\n')
} catch (error) { console.error(error.message); stop(1) }

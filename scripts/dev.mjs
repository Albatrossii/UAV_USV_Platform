import { spawn, spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { createDemoControl } from './demo-control.mjs'
import { stopOwnedChildren } from './demo-processes.mjs'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const isWindows = process.platform === 'win32'
const backendPort = Number(process.env.SERVER_PORT ?? 8081)
const resolvedBackendPort = Number.isFinite(backendPort) ? backendPort : 8081
const healthUrl = `http://127.0.0.1:${resolvedBackendPort}/actuator/health`

const processes = []
let stopping = false
let shutdownRequested = false

function resolvePythonCommand() {
  const candidates = [
    process.env.APP_ALGORITHM_PYTHON_COMMAND,
    process.env.PYTHON_EXECUTABLE,
    path.join(
      root,
      'algorithm-service',
      isWindows ? path.join('.venv', 'Scripts', 'python.exe') : path.join('.venv', 'bin', 'python'),
    ),
    ...(['Python313', 'Python312', 'Python311', 'Python310', 'Python39'].map(version => (
      process.env.LOCALAPPDATA
        ? path.join(process.env.LOCALAPPDATA, 'Programs', 'Python', version, 'python.exe')
        : ''
    ))),
    isWindows ? 'python.exe' : 'python3',
    'python',
    process.env.USERPROFILE
      ? path.join(
        process.env.USERPROFILE,
        '.cache', 'codex-runtimes', 'codex-primary-runtime',
        'dependencies', 'python', 'python.exe',
      )
      : '',
  ].filter(Boolean).filter((candidate, index, all) => all.indexOf(candidate) === index)
  const rejected = []
  for (const candidate of candidates) {
    const check = spawnSync(candidate, ['-c', 'import numpy, scipy, sklearn, matplotlib'], {
      cwd: root,
      encoding: 'utf8',
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 30000,
    })
    if (!check.error && check.status === 0) {
      const resolved = spawnSync(candidate, ['-c', 'import sys; print(sys.executable)'], {
        cwd: root,
        encoding: 'utf8',
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'ignore'],
        timeout: 5000,
      })
      return resolved.status === 0 && resolved.stdout.trim()
        ? resolved.stdout.trim()
        : candidate
    }
    rejected.push(candidate)
  }
  throw new Error(
    'No Python runtime satisfies algorithm-service/requirements.txt. '
    + `Checked: ${rejected.join(', ')}. `
    + 'Install the requirements or set APP_ALGORITHM_PYTHON_COMMAND to a compatible interpreter.',
  )
}

const pythonCommand = resolvePythonCommand()
console.log(`Algorithm Python: ${pythonCommand}`)
const demoControl = await createDemoControl(root, () => stopAll())
console.log('Managed demo: scripts/demo.ps1 -Action Status / Stop. Ctrl+C also stops owned services.')

const backend = spawn(
  path.join(root, 'backend', isWindows ? 'mvnw.cmd' : 'mvnw'),
  ['-f', path.join(root, 'backend', 'pom.xml'), 'spring-boot:run'],
  {
    cwd: root,
    shell: isWindows,
    windowsHide: true,
    stdio: 'inherit',
    env: {
      ...process.env,
      APP_ALGORITHM_PYTHON_COMMAND: pythonCommand,
    },
  },
)
processes.push(backend)

async function waitForBackend(timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs
  while (!shutdownRequested && Date.now() < deadline) {
    try {
      const response = await fetch(healthUrl, { signal: AbortSignal.timeout(1500) })
      if (response.ok && (await response.json()).status === 'UP') return true
    } catch {
      // Spring Boot is still compiling or initializing its database pool.
    }
    await new Promise(resolve => setTimeout(resolve, 500))
  }
  return false
}

async function startFrontendWhenReady() {
  if (!await waitForBackend()) {
    if (shutdownRequested) return
    console.error(`Backend did not become healthy within 90 seconds: ${healthUrl}`)
    stopAll(1)
    return
  }
  if (shutdownRequested) return
  console.log(`Backend is healthy: ${healthUrl}`)
  const frontend = spawn(
    isWindows ? 'npm.cmd' : 'npm',
    ['--prefix', path.join(root, 'frontend'), 'run', 'dev'],
    {
      cwd: root,
      shell: isWindows,
      windowsHide: true,
      stdio: 'inherit',
      env: {
        ...process.env,
        VITE_BACKEND_TARGET: process.env.VITE_BACKEND_TARGET ?? `http://127.0.0.1:${resolvedBackendPort}`,
      },
    },
  )
  processes.push(frontend)
  watchChild(frontend)
}

async function stopAll(exitCode = 0) {
  // A concurrent Ctrl+C/API attempt must not permanently consume the API's
  // retry latch if the original cleanup later fails.
  if (stopping) return false
  stopping = true
  shutdownRequested = true
  try {
    stopOwnedChildren(processes)
    console.log('Managed demo stopped. Database and unrelated services were not stopped.')
  } catch (error) {
    console.error(error.message)
    for (const failure of error.errors ?? []) console.error(failure.message)
    console.error('Shutdown incomplete. Launcher ownership is retained; run Stop again after resolving the failure.')
    stopping = false
    return false
  }
  try { await demoControl.close() } catch (error) {
    console.error(`Stopped services, but ownership record cleanup failed: ${error.message}`)
    exitCode = 1
  }
  setTimeout(() => process.exit(exitCode), 300)
}

function watchChild(child) {
  child.on('error', error => { console.error(`Demo child failed to start: ${error.message}`); void stopAll(1) })
  child.on('exit', (code) => {
    if (!shutdownRequested) void stopAll(code ?? 1)
  })
}

watchChild(backend)
void startFrontendWhenReady()

process.on('SIGINT', () => stopAll())
process.on('SIGTERM', () => stopAll())

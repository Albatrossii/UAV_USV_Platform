import { spawn } from 'node:child_process'
import { createConnection } from 'node:net'
import { existsSync } from 'node:fs'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
export const manualChecks = [
  ['human-asr', '真人麦克风、驻留纠错及口音/噪声', '需要实际录音；不以文本测试代替，不自动调用收费 ASR。'],
  ['browser-save', '浏览器导出文件实际保存', '需核对下载文件；页面点击或组件测试不能证明文件落盘。'],
  ['managed-start-stop', '完整统一启停部署', '需本机配置及部署窗口；本报告不接管或停止已有服务。'],
]
const voiceEnv = {
  VITE_VOICE_P1_PREPARATION: 'true', VITE_VOICE_P1_BACKEND: 'true', VITE_VOICE_P1_MOCK: 'false',
  VITE_VOICE_P0_MOCK: 'false', VITE_VOICE_UNITY_PRESENTATION_V1: 'true', VITE_VOICE_ASR_ONLY: 'false',
}

export function overallStatus(checks) {
  return checks.some(c => c.status === 'FAILED') ? 'FAILED'
    : checks.some(c => c.status !== 'PASSED') ? 'INCOMPLETE' : 'PASSED'
}
export function parseCounts(id, output) {
  const text = output.replace(/\x1b\[[0-9;]*m/g, '')
  if (id === 'frontend-tests' || id === 'python-tests') {
    const line = id === 'frontend-tests' ? text.split(/\r?\n/).findLast(line => /^\s*Tests\s/.test(line))
      : text.split(/\r?\n/).findLast(line => /\d+ (?:passed|failed|skipped|error).* in [\d.]+s/.test(line))
    if (!line) return undefined
    const count = name => Number(line.match(new RegExp(`(\\d+) ${name}\\b`))?.[1] ?? 0)
    const counts = { passed: count('passed'), failed: count('failed') + count('errors?'), skipped: count('skipped') + count('todo') + count('xfailed') + count('xpassed') + count('deselected') }
    return { ...counts, total: id === 'frontend-tests' ? Number(line.match(/\((\d+)\)/)?.[1] ?? 0) : counts.passed + counts.failed + counts.skipped }
  }
  const match = id === 'backend-tests' ? [...text.matchAll(/Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)/g)].at(-1)
        : id === 'launcher-tests' ? text.match(/tests (\d+)[\s\S]*?pass (\d+)[\s\S]*?fail (\d+)[\s\S]*?skipped (\d+)/) : null
  if (!match) return undefined
  if (id === 'backend-tests') return { total: +match[1], failed: +match[2] + +match[3], skipped: +match[4] }
  if (id === 'launcher-tests') return { total: +match[1], passed: +match[2], failed: +match[3] + Number(text.match(/cancelled (\d+)/)?.[1] ?? 0), skipped: +match[4] + Number(text.match(/todo (\d+)/)?.[1] ?? 0) }
  return undefined
}

// Capture bounded output only for numeric summaries. Never persist raw logs,
// environment variables, configuration values, tokens, or exception messages.
export function runCommand({ file, args, cwd, env, timeoutMs = 600_000 }) {
  return new Promise(resolve => {
    let output = '', timedOut = false, cleanupTimer
    // Batch entry points need a Windows shell. Use PowerShell literal strings
    // instead of shell:true, which concatenates unescaped command arguments.
    const batch = process.platform === 'win32' && /\.(cmd|bat)$/i.test(file)
    const literal = value => `'${String(value).replaceAll("'", "''")}'`
    const executable = batch ? 'powershell.exe' : file
    const argv = batch ? ['-NoProfile', '-NonInteractive', '-Command', `$ErrorActionPreference='Stop'; & ${[file, ...args].map(literal).join(' ')}; exit $LASTEXITCODE`] : args
    const child = spawn(executable, argv, { cwd, env: { ...process.env, ...env }, windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'] })
    for (const stream of [child.stdout, child.stderr]) stream?.on('data', chunk => {
      output = (output + String(chunk)).slice(-2_000_000)
    })
    const timer = setTimeout(() => {
      timedOut = true
      // Only the child launched here, never a service found by port or name.
      if (process.platform === 'win32') {
        const killer = spawn('taskkill.exe', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
        killer.on('error', () => child.kill())
        killer.on('exit', code => { if (code !== 0) child.kill() })
      } else child.kill('SIGTERM')
      cleanupTimer = setTimeout(() => child.kill('SIGKILL'), 5000)
      cleanupTimer.unref()
    }, timeoutMs)
    child.on('error', () => { clearTimeout(timer); clearTimeout(cleanupTimer); resolve({ code: null, unavailable: true, output: '' }) })
    child.on('close', code => { clearTimeout(timer); clearTimeout(cleanupTimer); resolve({ code, timedOut, output }) })
  })
}

async function probeHttp(url) {
  try { const response = await fetch(url, { signal: AbortSignal.timeout(5000) }); await response.body?.cancel(); return response.status === 200 }
  catch { return false }
}
async function probeTcp(port) {
  return new Promise(resolve => {
    const socket = createConnection({ host: '127.0.0.1', port })
    const finish = result => { socket.destroy(); resolve(result) }
    socket.setTimeout(2000)
    socket.once('connect', () => finish(true)); socket.once('error', () => finish(false)); socket.once('timeout', () => finish(false))
  })
}

export async function runAcceptance(options = {}, dependencies = {}) {
  const root = options.root ?? projectRoot
  const run = dependencies.run ?? runCommand
  const http = dependencies.http ?? probeHttp
  const tcp = dependencies.tcp ?? probeTcp
  const exists = dependencies.exists ?? existsSync
  const checks = []
  const add = check => { checks.push(check); options.progress?.(check); return check }
  const startedAt = new Date().toISOString()
  const ports = { backend: options.backendPort ?? 8083, frontend: options.frontendPort ?? 5177, mysql: options.mysqlPort ?? 3307 }
  for (const port of Object.values(ports)) if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid local port')
  const configFile = path.join(root, 'config', 'aliyun-demo.local.psd1')
  if (!exists(configFile)) add({ id: 'local-config', label: '本机启动配置', status: 'BLOCKED', detail: '缺少 config/aliyun-demo.local.psd1；按 example 填写，勿分享密钥。' })
  else {
    // The PowerShell parser returns field names only, never field values.
    const script = `$ErrorActionPreference='Stop'; try { $c=Import-PowerShellDataFile -LiteralPath '${configFile.replaceAll("'", "''")}'; $missing=@('AliyunAppKey','AliyunAccessKeyId','AliyunAccessKeySecret','MysqlUrl','MysqlUsername','BootstrapAdminUsername','BootstrapAdminPassword') | Where-Object { [string]::IsNullOrWhiteSpace([string]$c[$_]) }; $invalid=@('BackendPort','FrontendPort') | Where-Object { $c.ContainsKey($_) -and ([string]$c[$_] -notmatch '^\\d+$' -or [int]$c[$_] -lt 1 -or [int]$c[$_] -gt 65535) }; @{missing=@($missing); invalid=@($invalid)} | ConvertTo-Json -Compress } catch { exit 1 }`
    const result = await run({ file: 'powershell.exe', args: ['-NoProfile', '-NonInteractive', '-Command', script], cwd: root, timeoutMs: 15000, id: 'config-probe' })
    let valid = false
    try { const value = JSON.parse(result.output); valid = result.code === 0 && Array.isArray(value.missing) && !value.missing.length && Array.isArray(value.invalid) && !value.invalid.length } catch { /* No raw output in report. */ }
    add({ id: 'local-config', label: '本机启动配置', status: valid ? 'PASSED' : 'BLOCKED', detail: valid ? '必填字段与端口格式通过；未验证云凭据有效性或数据库权限。' : '配置无法解析、缺少必填字段或端口无效；请在本机检查。' })
  }
  const health = await Promise.all([
    http(`http://127.0.0.1:${ports.backend}/api/auth/csrf`), http(`http://127.0.0.1:${ports.frontend}/`),
    http(`http://127.0.0.1:${ports.frontend}/api/auth/csrf`), tcp(ports.mysql),
  ])
  ;['后端 HTTP', '前端 HTTP', '前端代理', 'MySQL TCP'].forEach((label, index) => add({ id: `health-${index}`, label,
    status: health[index] ? 'PASSED' : 'FAILED', detail: '仅检查连接；不代表登录、数据库查询、云识别或 Unity 场景已通过。' }))
  const pythonEnv = { PYTHONPATH: [path.join(root, '.local-tools', 'python-test-deps'), path.join(root, 'algorithm-service'), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) }
  const stages = [
    { id: 'launcher-tests', label: '启动器与验收报告单元测试', file: process.execPath, args: ['--test', 'scripts/__tests__/demo-control.test.mjs', 'scripts/__tests__/demo-processes.test.mjs', 'scripts/__tests__/acceptance-report.test.mjs'], cwd: root },
    { id: 'frontend-tests', label: '前端全量测试', file: process.platform === 'win32' ? 'npm.cmd' : 'npm', args: ['test'], cwd: path.join(root, 'frontend') },
    { id: 'frontend-build', label: '真实语音开关生产构建', file: process.platform === 'win32' ? 'npm.cmd' : 'npm', args: ['run', 'build'], env: voiceEnv, cwd: path.join(root, 'frontend') },
    { id: 'backend-tests', label: '后端语义与顺序执行专项（非全量）', file: path.join(root, 'backend', process.platform === 'win32' ? 'mvnw.cmd' : 'mvnw'), args: ['-Dtest=IntentServiceTests,VoiceSequenceExecutorTests', 'test'], cwd: path.join(root, 'backend') },
    { id: 'python-tests', label: 'Python 算法全量测试', file: options.python ?? (process.platform === 'win32' ? 'python' : 'python3'), args: ['-m', 'pytest', 'tests', '-q'], cwd: path.join(root, 'algorithm-service'), env: pythonEnv, timeoutMs: 900_000 },
  ]
  for (const stage of stages) {
    if (options.quick) { add({ id: stage.id, label: stage.label, status: 'SKIPPED', detail: '--quick 明确跳过测试/构建；不是通过。' }); continue }
    options.progress?.({ id: stage.id, label: stage.label, status: 'RUNNING' })
    const start = Date.now()
    let result
    try { result = await run(stage) } catch { result = { code: null, unavailable: true, output: '' } }
    const counts = parseCounts(stage.id, result.output ?? '')
    const inconsistentCounts = counts?.passed !== undefined && counts.total !== counts.passed + (counts.failed ?? 0) + (counts.skipped ?? 0)
    const status = result.unavailable ? 'BLOCKED' : result.code !== 0 || result.timedOut || counts?.failed ? 'FAILED'
      : counts?.skipped || inconsistentCounts ? 'INCOMPLETE' : stage.id !== 'frontend-build' && (!counts || (counts.total ?? counts.passed) < 1) ? 'INCOMPLETE' : 'PASSED'
    add({ id: stage.id, label: stage.label, status, counts, seconds: Math.round((Date.now() - start) / 1000),
      detail: result.unavailable ? '命令不可用，请检查本机工具链/PATH。' : result.timedOut ? '执行超时；未记为通过。'
        : result.code !== 0 ? '命令失败，请单独运行相应测试排查；报告不收集原始日志。'
          : status === 'INCOMPLETE' ? '存在跳过项或无法核实测试数量。' : '命令成功；仅覆盖本行声明范围。' })
  }
  for (const [id, label, detail] of manualChecks) add({ id, label, status: 'MANUAL_PENDING', detail })
  return { version: 1, startedAt, finishedAt: new Date().toISOString(), mode: options.quick ? 'quick' : 'full', status: overallStatus(checks), checks }
}

const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
export function reportHtml(report) {
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>本地验收报告</title><style>body{font:16px/1.6 system-ui;max-width:1100px;margin:40px auto;padding:0 20px;color:#163438}table{width:100%;border-collapse:collapse}td,th{border:1px solid #b6cecc;padding:10px;text-align:left}th{background:#e7f2ef}code{white-space:pre-wrap}</style><h1>本地验收报告 · ${escape(report.status)}</h1><p>${escape(report.startedAt)} — ${escape(report.finishedAt)} · ${escape(report.mode)}</p><p>通过 PASSED / 失败 FAILED / 阻塞 BLOCKED / 跳过 SKIPPED / 待核实 INCOMPLETE / 人工待测 MANUAL_PENDING。人工项不会自动标记通过。不包含密钥或原始日志，不调用云 ASR、不启停业务服务。</p><table><thead><tr><th>项目</th><th>结果</th><th>证据与限制</th></tr></thead><tbody>${report.checks.map(c => `<tr><td>${escape(c.label)}</td><td>${escape(c.status)}</td><td>${escape(c.detail)}${c.counts ? `<br><code>${escape(JSON.stringify(c.counts))}</code>` : ''}${c.seconds !== undefined ? `<br>${c.seconds} 秒` : ''}</td></tr>`).join('')}</tbody></table></html>`
}
export async function saveReport(report, root = projectRoot) {
  const directory = path.join(root, '.logs', 'acceptance')
  await mkdir(directory, { recursive: true })
  const basename = `acceptance-${new Date().toISOString().replace(/[:.]/g, '-')}-${randomUUID().slice(0, 8)}`
  const json = path.join(directory, `${basename}.json`), html = path.join(directory, `${basename}.html`)
  await writeFile(json, JSON.stringify(report, null, 2), { encoding: 'utf8', flag: 'wx' })
  await writeFile(html, reportHtml(report), { encoding: 'utf8', flag: 'wx' })
  return { json, html }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2), options = {}
  const values = { '--python': 'python', '--backend-port': 'backendPort', '--frontend-port': 'frontendPort', '--mysql-port': 'mysqlPort' }
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--quick') options.quick = true
    else if (values[args[i]] && args[i + 1] && !args[i + 1].startsWith('--')) {
      const name = values[args[i]], value = args[++i]; options[name] = name === 'python' ? value : Number(value)
    } else throw new Error('Supported: --quick, --python PATH, --backend-port N, --frontend-port N, --mysql-port N')
  }
  const report = await runAcceptance({ ...options, progress: row => console.log(`[${row.status}] ${row.label}`) })
  console.log(JSON.stringify(await saveReport(report)))
  process.exitCode = report.status === 'FAILED' ? 1 : report.status === 'PASSED' ? 0 : 2
}

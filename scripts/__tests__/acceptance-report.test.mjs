import test from 'node:test'
import assert from 'node:assert/strict'
import { overallStatus, parseCounts, reportHtml, runAcceptance, runCommand } from '../acceptance-report.mjs'

const outputs = {
  'launcher-tests': '# tests 6\n# pass 6\n# fail 0\n# skipped 0',
  'frontend-tests': ' Tests  745 passed (745)',
  'frontend-build': 'built',
  'backend-tests': 'Tests run: 21, Failures: 0, Errors: 0, Skipped: 0\nTests run: 62, Failures: 0, Errors: 0, Skipped: 0',
  'python-tests': '243 passed in 337.02s',
}
const dependencies = overrides => ({ exists: () => false, http: async () => true, tcp: async () => true,
  run: async stage => ({ code: 0, output: outputs[stage.id] ?? '' }), ...overrides })

test('summary parsing counts skips, failures and the Maven aggregate', () => {
  assert.deepEqual(parseCounts('backend-tests', outputs['backend-tests']), { total: 62, failed: 0, skipped: 0 })
  assert.deepEqual(parseCounts('frontend-tests', ' Tests  12 passed | 2 skipped (14)'), { passed: 12, failed: 0, skipped: 2, total: 14 })
  assert.deepEqual(parseCounts('python-tests', '2 failed, 12 passed, 3 skipped, 1 deselected, 2 warnings in 4.12s'), { passed: 12, failed: 2, skipped: 4, total: 18 })
  assert.equal(parseCounts('python-tests', 'function test_243_passed collected'), undefined)
})
test('quick mode never runs test commands or treats a skip as passed', async () => {
  const report = await runAcceptance({ quick: true }, dependencies({ run: () => { throw new Error('must not run') } }))
  assert.equal(report.status, 'INCOMPLETE')
  assert.equal(report.checks.filter(row => row.status === 'SKIPPED').length, 5)
  assert.equal(report.checks.filter(row => row.status === 'MANUAL_PENDING').length, 3)
  assert.equal(report.checks.find(row => row.id === 'local-config').status, 'BLOCKED')
})
test('full report continues after a failed stage and excludes raw output', async () => {
  const ran = []
  const report = await runAcceptance({}, dependencies({ run: async stage => {
    ran.push(stage.id)
    return { code: stage.id === 'frontend-tests' ? 1 : 0, output: `${outputs[stage.id]}\nsecret-canary` }
  } }))
  assert.equal(ran.length, 5)
  assert.equal(report.status, 'FAILED')
  assert.equal(report.checks.find(row => row.id === 'python-tests').status, 'PASSED')
  assert.ok(!JSON.stringify(report).includes('secret-canary'))
})
test('successful automation still cannot certify manual checks', async () => {
  const report = await runAcceptance({}, dependencies({ exists: () => true, run: async stage => ({ code: 0, output: stage.id === 'config-probe' ? '{"missing":[],"invalid":[]}' : outputs[stage.id] }) }))
  assert.equal(report.checks.filter(row => row.status === 'PASSED').length, 10)
  assert.equal(report.status, 'INCOMPLETE')
})
test('unknown counts, skipped tests and missing toolchains are not passed', async () => {
  const report = await runAcceptance({}, dependencies({ run: async stage => stage.id === 'launcher-tests'
    ? { code: 0, output: '' } : stage.id === 'frontend-tests' ? { code: 0, output: ' Tests  2 passed | 1 skipped (3)' }
      : { code: null, unavailable: true } }))
  assert.equal(report.checks.find(row => row.id === 'launcher-tests').status, 'INCOMPLETE')
  assert.equal(report.checks.find(row => row.id === 'frontend-tests').status, 'INCOMPLETE')
  assert.equal(report.checks.find(row => row.id === 'backend-tests').status, 'BLOCKED')
  const mismatch = await runAcceptance({}, dependencies({ run: async () => ({ code: 0, output: ' Tests  2 passed (3)' }) }))
  assert.equal(mismatch.checks.find(row => row.id === 'frontend-tests').status, 'INCOMPLETE')
})
test('timeouts and health failures remain failures', async () => {
  const report = await runAcceptance({}, dependencies({ http: async () => false, run: async () => ({ code: 0, timedOut: true, output: '' }) }))
  assert.equal(report.status, 'FAILED')
  assert.equal(report.checks.find(row => row.id === 'frontend-build').status, 'FAILED')
})
test('invalid ports are rejected before running any command', async () => {
  await assert.rejects(runAcceptance({ backendPort: 0 }, dependencies({ run: () => { assert.fail('must not run') } })), /Invalid local port/)
})
test('invalid config is blocked without publishing parser content', async () => {
  const report = await runAcceptance({ quick: true }, dependencies({ exists: () => true, run: async () => ({ code: 1, output: 'secret-canary' }) }))
  assert.equal(report.checks[0].status, 'BLOCKED')
  assert.ok(!JSON.stringify(report).includes('secret-canary'))
})
test('HTML escapes fields and status priority cannot hide failures', () => {
  assert.equal(overallStatus([{ status: 'MANUAL_PENDING' }, { status: 'FAILED' }]), 'FAILED')
  assert.equal(overallStatus([{ status: 'PASSED' }]), 'PASSED')
  const html = reportHtml({ checks: [{ label: '<script>bad()</script>', status: 'FAILED', detail: '"unsafe"' }] })
  assert.ok(!html.includes('<script>'))
  assert.ok(html.includes('&lt;script&gt;'))
})
test('command runner captures exit status and handles missing executable', async () => {
  const success = await runCommand({ file: process.execPath, args: ['-e', 'process.stdout.write("ok")'] })
  assert.equal(success.code, 0)
  assert.equal(success.output, 'ok')
  const missing = await runCommand({ file: 'nonexistent-acceptance-command-987654', args: [] })
  assert.equal(missing.unavailable, true)
})

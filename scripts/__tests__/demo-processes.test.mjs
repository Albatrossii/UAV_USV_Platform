import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { stopOwnedChildren } from '../demo-processes.mjs'

test('attempts every owned child even after a failure and reports incomplete shutdown', () => {
  const attempts = []
  const children = [101, 102].map(pid => ({ pid, exitCode: null, signalCode: null }))
  assert.throws(() => stopOwnedChildren(children, {
    platform: 'win32',
    run: (_exe, args) => { attempts.push(args[1]); return { status: args[1] === '101' ? 1 : 0 } },
    probe: () => {},
  }), error => error instanceof AggregateError && error.errors.length === 1)
  assert.deepEqual(attempts, ['101', '102'])
})

test('permission failure is not mistaken for an already-exited process', () => {
  const child = { pid: 101, exitCode: null, signalCode: null }
  const options = { platform: 'win32', run: () => ({ status: 1 }),
    probe: () => { throw Object.assign(new Error('permission denied'), { code: 'EPERM' }) } }
  assert.throws(() => stopOwnedChildren([child], options), AggregateError)
  options.probe = () => { throw Object.assign(new Error('no process'), { code: 'ESRCH' }) }
  assert.doesNotThrow(() => stopOwnedChildren([child], options))
})

test('stops its own Windows child tree but leaves an unrelated child running', { skip: process.platform !== 'win32', timeout: 15000 }, async () => {
  const unrelated = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { windowsHide: true })
  const owned = spawn(process.execPath, ['-e', `
    const child = require('node:child_process').spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { windowsHide: true });
    console.log(child.pid);
    setInterval(() => {}, 1000);
  `], { windowsHide: true })
  try {
    const [data] = await once(owned.stdout, 'data')
    const grandchild = Number(String(data).trim())
    assert.ok(Number.isInteger(grandchild) && grandchild > 0)
    const exited = once(owned, 'exit')
    stopOwnedChildren([owned])
    await exited
    assert.throws(() => process.kill(grandchild, 0), { code: 'ESRCH' })
    assert.doesNotThrow(() => process.kill(unrelated.pid, 0))
    stopOwnedChildren([owned]) // already exited is a no-op, not a reused PID kill
  } finally {
    stopOwnedChildren([owned, unrelated])
  }
})

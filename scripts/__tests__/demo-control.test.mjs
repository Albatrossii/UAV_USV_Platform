import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createDemoControl } from '../demo-control.mjs'

for (const failure of ['false', 'throw']) {
  test(`an async ${failure} shutdown permits a later explicit retry, never a concurrent duplicate`, async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), 'uav-demo-control-test-'))
    let attempts = 0
    let release
    const control = await createDemoControl(root, async () => {
      attempts++
      if (attempts > 1) return
      await new Promise(resolve => { release = resolve })
      if (failure === 'throw') throw new Error('simulated stop failure')
      return false
    })
    try {
      const record = JSON.parse(await readFile(path.join(root, '.local-tools/demo-control.json'), 'utf8'))
      const stop = () => fetch(`http://127.0.0.1:${control.port}/stop`, { method: 'POST', headers: { 'X-Demo-Token': record.token } })
      await stop()
      await new Promise(resolve => setImmediate(resolve))
      await stop()
      assert.equal(attempts, 1)
      release()
      await new Promise(resolve => setImmediate(resolve))
      await stop()
      await new Promise(resolve => setImmediate(resolve))
      assert.equal(attempts, 2)
    } finally {
      release?.()
      await control.close()
      assert.equal(path.dirname(root), os.tmpdir())
      assert.ok(path.basename(root).startsWith('uav-demo-control-test-'))
      await rm(root, { recursive: true })
    }
  })
}

test('only an authenticated local POST stops the owning supervisor once', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'uav-demo-control-test-'))
  let stops = 0
  const control = await createDemoControl(root, () => { stops++ })
  try {
    const record = JSON.parse(await readFile(path.join(root, '.local-tools/demo-control.json'), 'utf8'))
    const url = `http://127.0.0.1:${control.port}/stop`
    assert.equal(record.root, root)
    assert.equal((await fetch(url, { method: 'POST' })).status, 403)
    assert.equal((await fetch(url, { method: 'POST', headers: { 'X-Demo-Token': 'z'.repeat(64) } })).status, 403)
    assert.equal((await fetch(url, { headers: { 'X-Demo-Token': record.token } })).status, 404)
    assert.equal(stops, 0)
    assert.equal((await fetch(url, { method: 'POST', headers: { 'X-Demo-Token': record.token } })).status, 202)
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(stops, 1)
    assert.equal((await fetch(url, { method: 'POST', headers: { 'X-Demo-Token': record.token } })).status, 202)
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(stops, 1)
    await assert.rejects(createDemoControl(root, () => {}), /Cannot claim demo control file/)
  } finally {
    await control.close()
    await assert.rejects(readFile(path.join(root, '.local-tools/demo-control.json')), { code: 'ENOENT' })
    // Only the uniquely created test directory is removed, never the repo.
    assert.equal(path.dirname(root), os.tmpdir())
    assert.ok(path.basename(root).startsWith('uav-demo-control-test-'))
    await rm(root, { recursive: true })
  }
})

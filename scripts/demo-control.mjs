import { createServer } from 'node:http'
import { randomBytes, timingSafeEqual } from 'node:crypto'
import { mkdir, readFile, unlink, writeFile } from 'node:fs/promises'
import path from 'node:path'

// The supervisor, not a guessed PID, owns shutdown. No cloud credentials here.
export async function createDemoControl(root, onStop) {
  const directory = path.join(path.resolve(root), '.local-tools')
  const filename = path.join(directory, 'demo-control.json')
  await mkdir(directory, { recursive: true })
  const token = randomBytes(32).toString('hex')
  let stopping = false
  const server = createServer((request, response) => {
    const supplied = request.headers['x-demo-token']
    if (typeof supplied !== 'string' || !/^[a-f0-9]{64}$/.test(supplied)
      || !timingSafeEqual(Buffer.from(supplied), Buffer.from(token))) {
      response.writeHead(403).end('Forbidden'); return
    }
    if (request.method !== 'POST' || request.url !== '/stop') {
      response.writeHead(404).end('Not found'); return
    }
    response.writeHead(202, { 'Content-Type': 'application/json' }).end('{"stopping":true}')
    if (!stopping) {
      stopping = true
      setImmediate(async () => {
        try { if (await onStop() === false) stopping = false }
        catch { stopping = false } // A failed shutdown may be explicitly retried.
      })
    }
  })
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const port = server.address().port
  try {
    // Do not overwrite another supervisor's ownership record, even if stale.
    await writeFile(filename, JSON.stringify({ root: path.resolve(root), pid: process.pid, port, token }), { flag: 'wx', mode: 0o600 })
  } catch (error) {
    server.close()
    throw new Error(`Cannot claim demo control file (${error.code}); check existing launcher before removing a stale .local-tools/demo-control.json.`)
  }
  return {
    port,
    async close() {
      server.closeAllConnections()
      await new Promise(resolve => server.close(resolve))
      try {
        const current = JSON.parse(await readFile(filename, 'utf8'))
        if (current.token === token) await unlink(filename)
      } catch (error) { if (error.code !== 'ENOENT') throw error }
    },
  }
}

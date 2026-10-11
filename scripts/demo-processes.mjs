import { spawnSync } from 'node:child_process'

/** Only accepts child handles returned by this launcher's spawn calls. */
export function stopOwnedChildren(children, { platform = process.platform, run = spawnSync, probe = process.kill } = {}) {
  const failures = []
  for (const child of children) {
    if (child.exitCode !== null || child.signalCode !== null || !child.pid) continue
    try {
      if (platform === 'win32') {
        const result = run('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {
          windowsHide: true, stdio: 'ignore', timeout: 10000,
        })
        if (result.status !== 0) {
          try { probe(child.pid, 0) } catch (error) { if (error.code === 'ESRCH') continue; throw error }
          throw new Error(`Could not stop owned child PID ${child.pid}; inspect the launcher terminal before restarting.`)
        }
      } else if (!child.killed && !child.kill()) {
        throw new Error(`Could not signal owned child PID ${child.pid}.`)
      }
    } catch (error) { failures.push(error) }
  }
  if (failures.length) throw new AggregateError(failures, `${failures.length} owned service(s) could not be stopped; retry shutdown or inspect the launcher terminal.`)
}

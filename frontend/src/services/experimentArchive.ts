import { ref } from 'vue'
import type { AlgorithmRuntimeFrame } from '@/types/mission'

export interface ExperimentEvent { id: string; at: string; kind: string; detail: Record<string, unknown> }
export interface ExperimentRecord {
  id: string; owner: string; runId: number; startedAt: string; updatedAt: string
  config: Record<string, unknown>; frames: AlgorithmRuntimeFrame[]; events: ExperimentEvent[]
  truncated: boolean; version: 1
}
export const archiveError = ref('')
export const archiveLimits = { runs: 20, frames: 1800, events: 500 }
let database: Promise<IDBDatabase> | undefined
let writes: Promise<unknown> = Promise.resolve()
const sampled = new Map<string, number>()
function db() {
  if (!database) database = new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('uav-usv-experiments', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('runs', { keyPath: 'id' }).createIndex('owner', 'owner')
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => { database = undefined; reject(request.error) }
  })
  return database
}
async function transact<T>(mode: IDBTransactionMode, operation: (store: IDBObjectStore) => IDBRequest<T>) {
  const database = await db()
  return new Promise<T>((resolve, reject) => {
    const tx = database.transaction('runs', mode)
    const request = operation(tx.objectStore('runs'))
    tx.oncomplete = () => resolve(request.result)
    tx.onabort = () => reject(tx.error ?? new Error('Local archive transaction aborted'))
    tx.onerror = () => reject(tx.error)
  })
}
const key = (owner: string, runId: number) => JSON.stringify([owner, runId])
const copy = <T>(value: T): T => JSON.parse(JSON.stringify(value))
async function update(owner: string, runId: number, mutate: (record: ExperimentRecord) => void) {
  if (!owner || !Number.isSafeInteger(runId) || runId < 1) return
  // One queue prevents concurrent frame/receipt writes from losing each other.
  writes = writes.then(async () => {
    const id = key(owner, runId)
    let record = await transact<ExperimentRecord | undefined>('readonly', store => store.get(id))
    const isNew = !record
    record ??= { id, owner, runId, startedAt: new Date().toISOString(), updatedAt: '', config: {}, frames: [], events: [], truncated: false, version: 1 }
    mutate(record)
    record.updatedAt = new Date().toISOString()
    await transact('readwrite', store => store.put(copy(record)))
    if (isNew) {
      const records = await transact<ExperimentRecord[]>('readonly', store => store.index('owner').getAll(owner))
      for (const old of records.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).slice(archiveLimits.runs)) {
        await transact('readwrite', store => store.delete(old.id))
      }
    }
    archiveError.value = ''
  }).catch(() => { archiveError.value = '本地实验记录保存失败（存储不可用或空间不足）；仿真不受影响。' })
  await writes
}
export function appendExperimentFrame(owner: string, frame: AlgorithmRuntimeFrame, config: Record<string, unknown>) {
  const id = key(owner, frame.runId)
  const now = Date.now()
  if (!frame.terminalStatus && now - (sampled.get(id) ?? 0) < 1000) return
  sampled.set(id, now)
  if (sampled.size > 40) sampled.delete(sampled.keys().next().value!)
  const snapshot = copy(frame)
  const settings = copy(config)
  void update(owner, frame.runId, record => {
    record.config = settings
    if (record.frames[record.frames.length - 1]?.sequence === frame.sequence) return
    record.frames.push(snapshot)
    if (record.frames.length > archiveLimits.frames) { record.frames.shift(); record.truncated = true }
  })
}
export function appendExperimentEvent(owner: string, runId: number, event: ExperimentEvent) {
  const snapshot = copy(event)
  void update(owner, runId, record => {
    if (record.events.some(e => e.id === snapshot.id)) return
    record.events.push(snapshot)
    if (record.events.length > archiveLimits.events) { record.events.shift(); record.truncated = true }
  })
}
export async function listExperiments(owner: string) {
  await writes
  if (!owner) return []
  const records = await transact<ExperimentRecord[]>('readonly', store => store.index('owner').getAll(owner))
  return records.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
}
export function csvCell(value: unknown) {
  let text = value == null ? '' : String(value)
  if (/^[\s]*[=+\-@]/u.test(text)) text = `'${text}`
  return `"${text.replace(/"/g, '""')}"`
}
export function experimentCsv(record: ExperimentRecord) {
  const rows: unknown[][] = [['kind', 'time', 'sequence', 'device', 'x', 'y', 'z', 'status', 'detail']]
  for (const frame of record.frames) for (const agent of frame.agents) {
    rows.push(['frame', frame.timestamp, frame.sequence, agent.code, agent.x, agent.y, agent.z, agent.status, ''])
  }
  for (const event of record.events) rows.push([event.kind, event.at, '', '', '', '', '', '', JSON.stringify(event.detail)])
  return '\uFEFF' + rows.map(row => row.map(csvCell).join(',')).join('\r\n')
}

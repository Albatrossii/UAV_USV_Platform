import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { prepareExperimentExport, saveExperimentExport } from '../experimentExport'
import type { ExperimentRecord } from '@/services/experimentArchive'

const record: ExperimentRecord = { id: 'r', owner: 'local', runId: 42, startedAt: '', updatedAt: '', config: {}, frames: [], events: [], truncated: false, version: 1 }
const file = prepareExperimentExport(record, 'json')
beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal('showSaveFilePicker', undefined) })
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
describe('truthful experiment export', () => {
  it('serializes JSON and names CSV without mutating the record', () => {
    expect(JSON.parse(file.text)).toEqual(record)
    expect(file.filename).toBe('experiment-42.json')
    expect(prepareExperimentExport(record, 'csv').filename).toBe('experiment-42.csv')
  })
  it('reports saved only after the selected file has closed', async () => {
    let finish!: () => void
    const close = vi.fn(() => new Promise<void>(resolve => { finish = resolve }))
    const write = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('showSaveFilePicker', vi.fn().mockResolvedValue({ createWritable: async () => ({ write, close }) }))
    let settled = false
    const pending = saveExperimentExport(file).then(result => { settled = true; return result })
    await vi.waitFor(() => expect(close).toHaveBeenCalled())
    expect(settled).toBe(false)
    expect(write).toHaveBeenCalledWith(expect.any(Blob))
    finish()
    expect((await pending).state).toBe('saved')
  })
  it('treats cancelling the picker as cancellation, not a successful download', async () => {
    vi.stubGlobal('showSaveFilePicker', vi.fn().mockRejectedValue(new DOMException('cancel', 'AbortError')))
    expect((await saveExperimentExport(file)).state).toBe('cancelled')
  })
  it('aborts failed writes and never leaks raw error details', async () => {
    const abort = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('showSaveFilePicker', vi.fn().mockResolvedValue({ createWritable: async () => ({ write: async () => {}, close: async () => { throw new Error('private-path') }, abort }) }))
    const result = await saveExperimentExport(file)
    expect(result.state).toBe('failed')
    expect(result.message).not.toContain('private-path')
    expect(abort).toHaveBeenCalledOnce()
  })
  it('fallback reports only requested and removes the temporary anchor and URL', async () => {
    const create = vi.fn().mockReturnValue('blob:export'), revoke = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const result = await saveExperimentExport(file)
    expect(result.state).toBe('requested')
    expect(result.message).toContain('无法确认文件已保存')
    expect(click).toHaveBeenCalledOnce()
    expect(document.querySelector('a[download]')).toBeNull()
    expect(revoke).not.toHaveBeenCalled()
    vi.advanceTimersByTime(60_000)
    expect(revoke).toHaveBeenCalledWith('blob:export')
  })
  it('a blocked anchor click is a failure and still releases its URL', async () => {
    const revoke = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: () => 'blob:blocked', revokeObjectURL: revoke })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => { throw new Error('blocked') })
    expect((await saveExperimentExport(file)).state).toBe('failed')
    vi.advanceTimersByTime(60_000)
    expect(revoke).toHaveBeenCalledWith('blob:blocked')
  })
})

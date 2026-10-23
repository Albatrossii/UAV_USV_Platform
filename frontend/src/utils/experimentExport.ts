import { experimentCsv, type ExperimentRecord } from '@/services/experimentArchive'

export function prepareExperimentExport(record: ExperimentRecord, format: 'json' | 'csv') {
  return {
    filename: `experiment-${record.runId}.${format}`,
    mime: format === 'json' ? 'application/json' : 'text/csv;charset=utf-8',
    text: format === 'json' ? JSON.stringify(record, null, 2) : experimentCsv(record),
  }
}

type WritableFile = { write: (data: Blob) => Promise<void>; close: () => Promise<void>; abort?: () => Promise<void> }
type SavePicker = (options: { suggestedName: string }) => Promise<{ createWritable: () => Promise<WritableFile> }>
export type ExportResult = { state: 'saved' | 'requested' | 'cancelled' | 'failed'; message: string }

/** A download click cannot prove disk persistence. Only closing a selected file can. */
export async function saveExperimentExport(file: ReturnType<typeof prepareExperimentExport>): Promise<ExportResult> {
  const picker = (window as Window & { showSaveFilePicker?: SavePicker }).showSaveFilePicker
  let writable: WritableFile | undefined
  try {
    const blob = new Blob([file.text], { type: file.mime })
    if (picker) {
      const handle = await picker.call(window, { suggestedName: file.filename })
      writable = await handle.createWritable()
      await writable.write(blob)
      await writable.close()
      return { state: 'saved', message: `已保存：${file.filename}（文件写入已完成）。` }
    }
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    try {
      link.href = url
      link.download = file.filename
      link.hidden = true
      document.body.appendChild(link)
      link.click()
    } finally {
      link.remove()
      // Keep the blob alive long enough for browser download handling.
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    }
    return { state: 'requested', message: `已生成 ${file.filename} 并请求下载；网页无法确认文件已保存。若没有下载，请使用下方“显示导出内容”手动保存。` }
  } catch (error) {
    try { await writable?.abort?.() } catch { /* Preserve the original failure. */ }
    // DOMException may come from a different realm and is not always an Error.
    if (typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError') {
      return { state: 'cancelled', message: '已取消保存，未确认文件落盘。可重新导出或显示内容手动保存。' }
    }
    return { state: 'failed', message: '保存未完成或浏览器不支持下载。请显示导出内容手动保存，或在支持下载的浏览器中重试。' }
  }
}

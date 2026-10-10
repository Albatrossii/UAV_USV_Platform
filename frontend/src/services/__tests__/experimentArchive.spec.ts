import 'fake-indexeddb/auto'
import { describe, expect, it } from 'vitest'
import { appendExperimentEvent, appendExperimentFrame, csvCell, experimentCsv, listExperiments } from '../experimentArchive'
import type { AlgorithmRuntimeFrame } from '@/types/mission'

describe('local experiment archive', () => {
  it('persists frames and deduplicated events and isolates account records', async () => {
    appendExperimentFrame('archive-alice', { runId: 1, sequence: 1, timestamp: 100, agents: [{ code: 'UAV-001', x: 2, y: 3, z: 20, status: 'HOLDING' }], metrics: {} } as AlgorithmRuntimeFrame, { algorithm: 'GB_SFLA_CS' })
    const event = { id: 'one', at: new Date().toISOString(), kind: '语义解析', detail: { text: '一号机悬停' } }
    appendExperimentEvent('archive-alice', 1, event)
    appendExperimentEvent('archive-alice', 1, event)
    const records = await listExperiments('archive-alice')
    expect(records).toHaveLength(1)
    expect(records[0]?.events).toHaveLength(1)
    expect(records[0]?.frames).toHaveLength(1)
    expect(records[0]?.config.algorithm).toBe('GB_SFLA_CS')
    expect(await listExperiments('archive-bob')).toEqual([])
    expect(experimentCsv(records[0]!)).toContain('UAV-001')
    expect(experimentCsv(records[0]!)).toContain('一号机悬停')
  })
  it('bounds retained runs and neutralizes spreadsheet formulas', async () => {
    for (let i = 1; i <= 22; i++) appendExperimentEvent('archive-retention', i, { id: String(i), at: '', kind: 'test', detail: {} })
    expect(await listExperiments('archive-retention')).toHaveLength(20)
    expect(csvCell('=HYPERLINK("x")')).toBe('"\'=HYPERLINK(""x"")"')
  })
})

import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ExperimentArchive from '../ExperimentArchive.vue'
import { listExperiments, type ExperimentRecord } from '@/services/experimentArchive'
import { saveExperimentExport, type ExportResult } from '@/utils/experimentExport'

vi.mock('@/services/experimentArchive', async importOriginal => ({ ...await importOriginal<object>(), listExperiments: vi.fn() }))
vi.mock('@/utils/experimentExport', async importOriginal => ({ ...await importOriginal<object>(), saveExperimentExport: vi.fn() }))
const record: ExperimentRecord = { id: 'r', owner: 'alice', runId: 42, startedAt: '', updatedAt: '', config: {}, frames: [], events: [], truncated: false, version: 1 }
let wrapper: VueWrapper
const button = (text: string) => wrapper.findAll('button').find(b => b.text() === text)!
beforeEach(() => {
  vi.mocked(listExperiments).mockResolvedValue([record, { ...record, id: 's', runId: 43 }])
  vi.mocked(saveExperimentExport).mockResolvedValue({ state: 'requested', message: '网页无法确认文件已保存' })
  wrapper = mount(ExperimentArchive, { props: { owner: 'alice' }, global: { stubs: { Teleport: true } } })
})
afterEach(() => { wrapper.unmount(); vi.resetAllMocks() })
async function open() { await button('实验记录').trigger('click'); await flushPromises() }
describe('archive export feedback', () => {
  it('shows loading rather than an empty result until storage responds', async () => {
    let resolve!: (records: ExperimentRecord[]) => void
    vi.mocked(listExperiments).mockImplementation(() => new Promise(r => { resolve = r }))
    await button('实验记录').trigger('click')
    expect(wrapper.text()).toContain('正在读取')
    expect(wrapper.text()).not.toContain('暂无记录')
    resolve([]); await flushPromises()
    expect(wrapper.text()).toContain('暂无记录')
  })
  it('offers the exact JSON as readonly manual fallback without claiming saved', async () => {
    await open(); await button('导出 JSON').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('无法确认文件已保存')
    await button('显示导出内容').trigger('click')
    const area = wrapper.get('textarea')
    expect(area.attributes('readonly')).toBeDefined()
    expect(JSON.parse((area.element as HTMLTextAreaElement).value)).toEqual(record)
    expect(wrapper.text()).toContain('experiment-42.json')
  })
  it.each(['selection', 'owner', 'close'] as const)('ignores a late export result after %s changes', async change => {
    let resolve!: (result: ExportResult) => void
    vi.mocked(saveExperimentExport).mockImplementation(() => new Promise(r => { resolve = r }))
    await open(); await button('导出 JSON').trigger('click')
    expect(button('导出 CSV').attributes('disabled')).toBeDefined()
    await button('显示导出内容').trigger('click')
    expect(JSON.parse((wrapper.get('textarea').element as HTMLTextAreaElement).value)).toEqual(record)
    if (change === 'selection') await wrapper.get('select').setValue('s')
    else if (change === 'owner') await wrapper.setProps({ owner: 'bob' })
    else await button('关闭').trigger('click')
    resolve({ state: 'saved', message: 'OLD-SAVED' }); await flushPromises()
    expect(wrapper.text()).not.toContain('OLD-SAVED')
    expect(wrapper.find('textarea').exists()).toBe(false)
  })
  it('does not reveal an old account storage response', async () => {
    let resolve!: (records: ExperimentRecord[]) => void
    vi.mocked(listExperiments).mockImplementationOnce(() => new Promise(r => { resolve = r }))
    await button('实验记录').trigger('click'); await wrapper.setProps({ owner: 'bob' })
    resolve([record]); await flushPromises()
    expect(wrapper.find('select').exists()).toBe(false)
  })
  it('keeps the record and fallback available after a failed save', async () => {
    vi.mocked(saveExperimentExport).mockResolvedValue({ state: 'failed', message: '保存未完成' })
    await open(); await button('导出 CSV').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('保存未完成')
    expect(wrapper.get('select').findAll('option')).toHaveLength(2)
    await button('显示导出内容').trigger('click')
    expect(wrapper.text()).toContain('experiment-42.csv')
  })
})

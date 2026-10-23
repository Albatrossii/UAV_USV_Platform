import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import CommandGroupSummary from '../CommandGroupSummary.vue'

describe('saved capture groups', () => {
  it('keeps both groups visible and distinguishes stored membership from active motion', async () => {
    const air = Object.fromEntries([1, 2, 3].map(i => [`UAV-00${i}`, 'THREAT-001']))
    const sea = Object.fromEntries([1, 2, 3].map(i => [`USV-00${i}`, 'THREAT-002']))
    const wrapper = mount(CommandGroupSummary, { props: { metrics: { algorithmMode: 'GUARD', fixedAssignments: air } } })
    expect(wrapper.text()).toContain('待围捕 · 当前任务继续')
    expect(wrapper.findAll('.group-team')).toHaveLength(1)
    await wrapper.setProps({ metrics: { algorithmMode: 'GUARD', lastPresetId: 14, fixedAssignments: { ...air, ...sea } } })
    expect(wrapper.findAll('.group-team')).toHaveLength(2)
    expect(wrapper.findAll('.group-team')[0]!.text()).toContain('1号敌船← UAV-001 / UAV-002 / UAV-003')
    expect(wrapper.findAll('.group-team')[1]!.text()).toContain('2号敌船← USV-001 / USV-002 / USV-003')
    await wrapper.setProps({ metrics: { algorithmMode: 'CAPTURE', fixedAssignments: { ...air, ...sea } } })
    expect(wrapper.text()).toContain('已启用')
    expect(wrapper.text()).not.toContain('待围捕')
    await wrapper.setProps({ metrics: {} })
    expect(wrapper.find('.command-groups').exists()).toBe(false)
  })
})

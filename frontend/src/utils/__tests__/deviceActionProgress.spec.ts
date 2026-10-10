import { describe, expect, it } from 'vitest'
import { deviceActionProgress } from '../deviceActionProgress'
import type { AlgorithmRuntimeFrame } from '@/types/mission'

function frame(motion: string, stage = '', kind = 'HELIPAD') {
  return { sequence: 2, agents: [{ code: 'UAV-001', status: motion }], metrics: { deviceControlStates: {
    'UAV-001': { motionState: motion, returnStage: stage, parkingKind: kind },
  } } } as unknown as AlgorithmRuntimeFrame
}
describe('physical action progress', () => {
  it('does not mistake an accepted command or old frame for physical completion', () => {
    expect(deviceActionProgress(frame('RETURNED'), 'UAV-001', 'UAV_RETURN', 2)?.complete).toBe(false)
    expect(deviceActionProgress(frame('RETURNING', 'DESCENDING'), 'UAV-001', 'UAV_LAND', 1)).toMatchObject({ complete: false, label: '对准停机坪下降' })
    expect(deviceActionProgress(frame('RETURNED'), 'UAV-001', 'UAV_LAND', 1)).toMatchObject({ complete: true, label: '已到停机坪，着陆完成' })
  })
  it('keeps device types distinct and never calls a stopped offshore aircraft landed', () => {
    expect(deviceActionProgress(frame('STOPPED'), 'UAV-001', 'UAV_LAND', 1)?.complete).toBe(false)
    expect(deviceActionProgress(frame('RETURNED'), 'USV-001', 'USV_RETURN', 1)?.complete).toBe(false)
    expect(deviceActionProgress(frame('ACTIVE'), 'UAV-001', 'UAV_RESUME', 1)?.complete).toBe(true)
  })
})

import { describe, expect, it } from 'vitest'
import { deviceActionProgress } from '../deviceActionProgress'
import type { AlgorithmRuntimeFrame } from '@/types/mission'

function frame(motion: string, stage = '', kind = 'HELIPAD') {
  return { sequence: 2, agents: [{ code: 'UAV-001', status: motion }], metrics: { deviceControlStates: {
    'UAV-001': { motionState: motion, returnStage: stage, parkingKind: kind },
  } } } as unknown as AlgorithmRuntimeFrame
}
describe('physical action progress', () => {
  it.each([NaN, Infinity, -1, 1.5])('does not infer completion from invalid frame sequence %s', sequence => {
    const sample = frame('RETURNED')
    sample.sequence = sequence
    expect(deviceActionProgress(sample, 'UAV-001', 'UAV_RETURN', 0)?.complete).toBe(false)
  })
  it.each(['USV_RETURN', 'UAV_UNKNOWN'])('rejects a wrong-type or unsupported action %s', command => {
    expect(deviceActionProgress(frame('RETURNED'), 'UAV-001', command)?.complete).toBe(false)
  })
  it.each(['BERTH', ''])('does not claim an aircraft landed at an invalid or unknown parking kind %s', kind => {
    expect(deviceActionProgress(frame('RETURNED', '', kind), 'UAV-001', 'UAV_RETURN')).toMatchObject({ complete: false, stage: 'VERIFY_PARKING' })
  })
  it('requires parking identity and arrival evidence to agree', () => {
    const sample = frame('RETURNED')
    const states = sample.metrics.deviceControlStates as Record<string, Record<string, unknown>>
    states['UAV-001']!.parkingSlot = 'UAV-002'
    expect(deviceActionProgress(sample, 'UAV-001', 'UAV_LAND')?.complete).toBe(false)
    states['UAV-001']!.parkingSlot = 'UAV-001'
    states['UAV-001']!.returnedAtHome = false
    expect(deviceActionProgress(sample, 'UAV-001', 'UAV_LAND')?.complete).toBe(false)
    states['UAV-001']!.returnedAtHome = true
    expect(deviceActionProgress(sample, 'UAV-001', 'UAV_LAND')?.complete).toBe(true)
  })
  it('never resolves duplicate device identities by choosing the first match', () => {
    const sample = frame('RETURNED')
    sample.agents.push({ ...sample.agents[0]!, code: 'uav1', status: 'RETURNING' })
    expect(deviceActionProgress(sample, 'UAV-001', 'UAV_RETURN')).toMatchObject({ complete: false, stage: 'CONFLICT' })
  })
  it('does not let stale control metrics override a conflicting agent status', () => {
    const sample = frame('RETURNED')
    sample.agents[0]!.status = 'RETURNING'
    expect(deviceActionProgress(sample, 'UAV-001', 'UAV_RETURN')).toMatchObject({ complete: false, stage: 'CONFLICT' })
  })
  it.each([
    ['TAKING_OFF', 'TAKING_OFF', '从停机坪起飞'],
    ['ASSEMBLING', 'REJOINING', '正在归队'],
  ])('accepts a detailed published departure stage %s without hiding logical control state', (status, stage, label) => {
    const sample = frame('REJOINING', stage)
    sample.agents[0]!.status = status
    expect(deviceActionProgress(sample, 'UAV-001', 'UAV_RESUME')).toMatchObject({
      complete: false,
      stage,
      label,
    })
  })
  it('waits for operator control to be released before announcing rejoin completion', () => {
    const sample = frame('ACTIVE')
    const states = sample.metrics.deviceControlStates as Record<string, Record<string, unknown>>
    states['UAV-001']!.controlAuthority = 'OPERATOR'
    expect(deviceActionProgress(sample, 'UAV-001', 'UAV_RESUME')?.complete).toBe(false)
    states['UAV-001']!.controlAuthority = 'ALGORITHM'
    expect(deviceActionProgress(sample, 'UAV-001', 'UAV_RESUME')?.complete).toBe(true)
    // The Python adapter removes the override entry after a successful rejoin.
    delete states['UAV-001']
    expect(deviceActionProgress(sample, 'UAV-001', 'UAV_RESUME')?.complete).toBe(true)
  })
  it('confirms a boat only at its berth, not a helipad', () => {
    const sample = frame('RETURNED')
    sample.agents[0]!.code = 'USV-001'
    sample.agents[0]!.type = 'USV'
    sample.metrics.deviceControlStates = { 'USV-001': { motionState: 'RETURNED', parkingKind: 'HELIPAD' } }
    expect(deviceActionProgress(sample, 'USV-001', 'USV_RETURN')?.complete).toBe(false)
    sample.metrics.deviceControlStates = { 'USV-001': { motionState: 'RETURNED', parkingKind: 'BERTH' } }
    expect(deviceActionProgress(sample, 'USV-001', 'USV_RETURN')).toMatchObject({ complete: true, label: '已到岸边泊位，靠泊完成' })
  })
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

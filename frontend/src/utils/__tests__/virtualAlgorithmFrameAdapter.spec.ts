import { describe, expect, it } from 'vitest'
import { adaptVirtualAlgorithmFrame } from '../virtualAlgorithmFrameAdapter'
import type { AlgorithmRuntimeFrame } from '@/types/mission'
import { buildReturnInfrastructure, toGlobalReturnInfrastructure } from '../virtualReturnInfrastructure'
const frame = (coordinateFrame: 'FLEET_LOCAL_ENU' | 'GLOBAL_ENU') => ({
  runId: 991001, sequence: 12, timestamp: 1000, coordinateFrame,
  agents: [{ code: 'UAV-001', type: 'UAV', x: 10, y: 20, z: 25, heading: 90, status: 'ACTIVE' },
    { code: 'USV-001', type: 'USV', x: -2, y: 3, z: 0, heading: 180, status: 'ACTIVE' }],
  targets: [{ code: 'PROTECTED-001', type: 'ESCORT_TARGET', x: 1, y: 2, z: 0, heading: 0, visible: true },
    { code: 'THREAT-001', type: 'THREAT_TARGET', x: 5, y: 6, z: 0, heading: 270, visible: true }],
} as AlgorithmRuntimeFrame)
describe('original algorithm pose compatibility', () => {
  it('converts local ENU once and preserves run, frame and all target identities', () => {
    const input=frame('FLEET_LOCAL_ENU')
    const origin={ eastM: -360, northM: -285, upM: 0 }
    const first=adaptVirtualAlgorithmFrame(input,new Map(),{fleetOrigin:origin})
    expect(first.payload).toMatchObject({runId:991001,sequence:12,sampleTime:1000,coordinateFrame:'GLOBAL_ENU'})
    expect(first.payload.vehicles[0]).toMatchObject({deviceCode:'UAV-001',eastM:-350,northM:-265,upM:25,headingDeg:90})
    expect(first.payload.targets.map(x=>x.deviceCode)).toEqual(['PROTECTED-001','THREAT-001'])
    const replay=adaptVirtualAlgorithmFrame(input,first.nextState,{fleetOrigin:origin})
    expect(replay.payload).toEqual(first.payload)
    expect(input.agents[0]?.x).toBe(10)
  })
  it('does not add fleet origin to already global coordinates', () => {
    const result=adaptVirtualAlgorithmFrame(frame('GLOBAL_ENU'),new Map(),{fleetOrigin:{eastM:-360,northM:-285,upM:2}})
    expect(result.payload.vehicles[0]).toMatchObject({eastM:10,northM:20,upM:25,headingDeg:90})
    expect(result.payload.vehicles[1]).toMatchObject({eastM:-2,northM:3,upM:0,headingDeg:180})
  })
})

describe('return infrastructure frame compatibility', () => {
  const origin = { eastM: -360, northM: -285, upM: 11 }
  const localLayout = () => buildReturnInfrastructure({ uavCount: 3, usvCount: 3, worldHeight: 280 })

  it.each(['FLEET_LOCAL_ENU', 'GLOBAL_ENU'] as const)('converts local metadata using its own frame even when vehicle coordinates use %s', coordinateFrame => {
    const input = frame(coordinateFrame)
    const infrastructure = localLayout()
    input.metrics = { returnInfrastructure: infrastructure }
    const original = structuredClone(input)
    const result = adaptVirtualAlgorithmFrame(input, new Map(), { fleetOrigin: origin })
    expect(result.payload.returnInfrastructure).toEqual(toGlobalReturnInfrastructure(infrastructure, origin))
    expect(input).toEqual(original)
    expect(adaptVirtualAlgorithmFrame(input, result.nextState, { fleetOrigin: origin }).payload.returnInfrastructure)
      .toEqual(result.payload.returnInfrastructure)
  })

  it.each(['FLEET_LOCAL_ENU', 'GLOBAL_ENU'] as const)('does not double-shift global metadata in a %s frame', coordinateFrame => {
    const input = frame(coordinateFrame)
    const global = toGlobalReturnInfrastructure(localLayout(), origin)
    input.metrics = { returnInfrastructure: global }
    expect(adaptVirtualAlgorithmFrame(input, new Map(), { fleetOrigin: origin }).payload.returnInfrastructure).toEqual(global)
  })

  it('accepts a fully global frame and global metadata without a fleet origin', () => {
    const input = frame('GLOBAL_ENU')
    const global = toGlobalReturnInfrastructure(localLayout(), origin)
    input.metrics = { returnInfrastructure: global }
    expect(adaptVirtualAlgorithmFrame(input).payload.returnInfrastructure).toEqual(global)
  })

  it('requires an explicit origin for local metadata even when the poses are already global', () => {
    const input = frame('GLOBAL_ENU')
    input.metrics = { returnInfrastructure: localLayout() }
    expect(() => adaptVirtualAlgorithmFrame(input)).toThrow(/origin/i)
  })

  it.each([undefined, {}, { returnInfrastructure: null }])('leaves legacy frames unchanged when return metadata is absent: %o', metrics => {
    const input = frame('FLEET_LOCAL_ENU')
    const expected = adaptVirtualAlgorithmFrame(input, new Map(), { fleetOrigin: origin })
    if (metrics !== undefined) input.metrics = metrics
    const adapted = adaptVirtualAlgorithmFrame(input, new Map(), { fleetOrigin: origin })
    expect(adapted.payload).toEqual(expected.payload)
    expect(adapted.nextState).toEqual(expected.nextState)
    expect(adapted.payload).not.toHaveProperty('returnInfrastructure')
  })

  it('rejects malformed return metadata without mutating the previous pose state', () => {
    const input = frame('GLOBAL_ENU')
    input.metrics = { returnInfrastructure: { version: 'bad' } }
    const previous = new Map([['UAV-001', { eastM: 5, northM: 6, upM: 7, timestamp: 900, headingDeg: 90 }]])
    const original = new Map(previous)
    expect(() => adaptVirtualAlgorithmFrame(input, previous)).toThrow()
    expect(previous).toEqual(original)
  })
})

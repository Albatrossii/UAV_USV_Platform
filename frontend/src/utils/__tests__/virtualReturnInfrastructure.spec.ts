import { describe, expect, it } from 'vitest'
import {
  buildReturnInfrastructure,
  readReturnInfrastructure,
  toGlobalReturnInfrastructure,
  type ReturnInfrastructure,
} from '../virtualReturnInfrastructure'

const origin = { eastM: -360, northM: -285, upM: 7.5 }
const defaultOrigin = { eastM: -360, northM: -285, upM: 0 }
const layout = () => buildReturnInfrastructure({ uavCount: 3, usvCount: 3, worldHeight: 280 })

describe('original scene return anchors', () => {
  it.each([1, 2, 3, 8, 9, 16, 17, 64, 128])('reuses at most three disjoint original anchors for %i of each device', count => {
    const result = buildReturnInfrastructure({ uavCount: count, usvCount: count, worldHeight: 280 })
    const supported = Math.min(3, count)
    expect(result.version).toBe('scene-existing-v1')
    expect(result.coordinateFrame).toBe('FLEET_LOCAL_ENU')
    expect(result.slots).toHaveLength(supported * 2)
    expect(new Set(result.slots.map(slot => slot.deviceCode)).size).toBe(supported * 2)
    expect(new Set(result.slots.map(slot => `${slot.eastM},${slot.northM},${slot.upM}`)).size).toBe(supported * 2)
    expect(readReturnInfrastructure(result)).toEqual(result)
    const helipads = result.slots.filter(slot => slot.kind === 'HELIPAD')
    const berths = result.slots.filter(slot => slot.kind === 'BERTH')
    for (let index = 0; index < supported; index++) {
      const pad = helipads[index]!
      const berth = berths[index]!
      expect(pad.deviceCode).toBe(`UAV-${String(index + 1).padStart(3, '0')}`)
      expect(berth.deviceCode).toBe(`USV-${String(index + 1).padStart(3, '0')}`)
      expect(pad.eastM).toBeGreaterThan(result.shore.eastMinM)
      expect(pad.eastM).toBeLessThan(result.shore.eastMaxM)
      expect(pad.northM).toBeGreaterThan(result.shore.northMinM)
      expect(pad.northM).toBeLessThan(result.shore.northMaxM)
      expect(pad.upM).toBe(result.shore.surfaceUpM)
      expect(pad.approachEastM).toBe(pad.eastM)
      expect(pad.approachNorthM).toBe(pad.northM)
      expect(pad.approachUpM).toBeGreaterThan(pad.upM)
      expect(pad.approachUpM - pad.upM).toBeCloseTo(20)
      expect(berth.approachEastM).toBeLessThan(berth.eastM)
      expect(berth.approachNorthM).toBeLessThan(berth.northM)
      expect(berth.upM).toBeCloseTo(0)
      expect(berth.approachUpM).toBeCloseTo(0)
    }
    expect(result.slots.some(slot => /-(?:004|009|128)$/.test(slot.deviceCode))).toBe(false)
  })

  it('does not create extra rows or columns when the fleet exceeds the original scene capacity', () => {
    const result = buildReturnInfrastructure({ uavCount: 17, usvCount: 25, worldHeight: 400 })
    expect(result).toEqual(layout())
  })

  it('returns the same layout for identical counts and world size without shared mutable objects', () => {
    const first = layout()
    const second = layout()
    expect(first).toEqual(second)
    expect(first).not.toBe(second)
    expect(first.shore).not.toBe(second.shore)
    expect(first.slots[0]).not.toBe(second.slots[0])
  })

  it.each([
    [0, 1], [-1, 1], [Number.NaN, 1], [3.9, 3], [129, 3], [999, 3],
  ])('clamps a count of %s to %s existing anchors', (requested, expected) => {
    const result = buildReturnInfrastructure({ uavCount: requested, usvCount: requested, worldHeight: 280 })
    expect(result.slots.filter(slot => slot.kind === 'HELIPAD')).toHaveLength(expected)
    expect(result.slots.filter(slot => slot.kind === 'BERTH')).toHaveLength(expected)
  })

  it.each([0, 80, 400, 2000, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY])('does not move the original scene anchors when world height is %s', height => {
    expect(buildReturnInfrastructure({ uavCount: 3, usvCount: 3, worldHeight: height })).toEqual(layout())
  })

  it.each([[1, 3], [2, 1], [3, 2], [128, 1], [1, 128]])('retains each device anchor when counts change independently to %i UAV and %i USV', (uavCount, usvCount) => {
    const result = buildReturnInfrastructure({ uavCount, usvCount, worldHeight: 1200 })
    for (const slot of result.slots) expect(slot).toEqual(layout().slots.find(item => item.deviceCode === slot.deviceCode))
    expect(result.slots.filter(slot => slot.kind === 'HELIPAD')).toHaveLength(Math.min(3, uavCount))
    expect(result.slots.filter(slot => slot.kind === 'BERTH')).toHaveLength(Math.min(3, usvCount))
  })

  it('matches the original island pads and invisible southwest shore station-keeping anchors in global ENU', () => {
    const global = toGlobalReturnInfrastructure(layout(), defaultOrigin)
    const pads = global.slots.filter(slot => slot.kind === 'HELIPAD')
    for (let index = 0; index < pads.length; index++) {
      const offset = (index - 1) * 14
      expect(pads[index]!.eastM).toBeCloseTo(-75 + Math.cos(.559) * offset, 8)
      expect(pads[index]!.northM).toBeCloseTo(-215 + Math.sin(.559) * offset, 8)
      expect(pads[index]!.upM).toBeCloseTo(19.65, 8)
      expect(pads[index]!.headingDeg).toBeCloseTo(90 - .559 * 180 / Math.PI, 8)
      expect(pads[index]!.approachUpM).toBeCloseTo(39.65, 8)
    }
    const berths = global.slots.filter(slot => slot.kind === 'BERTH')
    expect(berths.map(slot => [slot.eastM, slot.northM, slot.upM])).toEqual([[-92, -180, 0], [-108, -160, 0], [-108, -140, 0]])
    expect(berths.map(slot => [slot.approachEastM, slot.approachNorthM, slot.approachUpM])).toEqual([[-240, -210, 0], [-240, -190, 0], [-240, -170, 0]])
    expect(global.shore).toEqual({ eastMinM: -110, eastMaxM: -35, northMinM: -245, northMaxM: -185, surfaceUpM: 19.65 })
  })
})

describe('return infrastructure coordinate conversion', () => {
  it.each([
    { eastM: -360, northM: -285, upM: 7.5 },
    { eastM: 120, northM: -800, upM: -30 },
    { eastM: 0, northM: 0, upM: 0 },
  ])('keeps physical global anchors fixed when building relative to origin %o', fleetOrigin => {
    const local = buildReturnInfrastructure({ uavCount: 3, usvCount: 3, worldHeight: 999, fleetOrigin })
    const global = toGlobalReturnInfrastructure(local, fleetOrigin)
    const expected = toGlobalReturnInfrastructure(layout(), defaultOrigin)
    expect(local.shore.surfaceUpM).toBeCloseTo(19.65 - fleetOrigin.upM, 8)
    for (let index = 0; index < global.slots.length; index++) {
      const actualSlot = global.slots[index]!
      const expectedSlot = expected.slots[index]!
      expect(actualSlot.deviceCode).toBe(expectedSlot.deviceCode)
      for (const field of ['eastM', 'northM', 'upM', 'approachEastM', 'approachNorthM', 'approachUpM', 'headingDeg'] as const) {
        expect(actualSlot[field]).toBeCloseTo(expectedSlot[field], 8)
      }
    }
    const original = structuredClone(local)
    toGlobalReturnInfrastructure(local, fleetOrigin)
    expect(local).toEqual(original)
  })

  it('uses the deployed default fleet origin only when no origin is supplied', () => {
    expect(buildReturnInfrastructure({ uavCount: 3, usvCount: 3, worldHeight: 280, fleetOrigin: defaultOrigin })).toEqual(layout())
  })

  it('offsets every position and height once without mutating local metadata', () => {
    const local = layout()
    const original = structuredClone(local)
    const global = toGlobalReturnInfrastructure(local, origin)
    expect(global.coordinateFrame).toBe('GLOBAL_ENU')
    expect(global.shore).toEqual({
      eastMinM: local.shore.eastMinM + origin.eastM,
      eastMaxM: local.shore.eastMaxM + origin.eastM,
      northMinM: local.shore.northMinM + origin.northM,
      northMaxM: local.shore.northMaxM + origin.northM,
      surfaceUpM: local.shore.surfaceUpM + origin.upM,
    })
    local.slots.forEach((slot, index) => {
      expect(global.slots[index]).toEqual({
        ...slot,
        eastM: slot.eastM + origin.eastM,
        northM: slot.northM + origin.northM,
        upM: slot.upM + origin.upM,
        approachEastM: slot.approachEastM + origin.eastM,
        approachNorthM: slot.approachNorthM + origin.northM,
        approachUpM: slot.approachUpM + origin.upM,
      })
    })
    expect(local).toEqual(original)
    const repeated = toGlobalReturnInfrastructure(global, { eastM: 123, northM: 456, upM: 789 })
    expect(repeated).toEqual(global)
    expect(repeated).not.toBe(global)
    expect(repeated.shore).not.toBe(global.shore)
    expect(repeated.slots[0]).not.toBe(global.slots[0])
    expect(toGlobalReturnInfrastructure(global)).toEqual(global)
  })

  it.each([
    undefined,
    { eastM: Number.NaN, northM: 0, upM: 0 },
    { eastM: 0, northM: Number.POSITIVE_INFINITY, upM: 0 },
    { eastM: 0, northM: 0, upM: Number.NEGATIVE_INFINITY },
  ])('rejects a missing or nonfinite origin for local metadata: %o', badOrigin => {
    expect(() => toGlobalReturnInfrastructure(layout(), badOrigin)).toThrow(/origin/i)
  })

  it.each([
    { eastM: Number.NaN, northM: 0, upM: 0 },
    { eastM: 0, northM: Number.POSITIVE_INFINITY, upM: 0 },
    { eastM: 0, northM: 0, upM: Number.NEGATIVE_INFINITY },
  ])('rejects building scene anchors relative to a nonfinite origin: %o', fleetOrigin => {
    expect(() => buildReturnInfrastructure({ uavCount: 3, usvCount: 3, worldHeight: 280, fleetOrigin })).toThrow(/origin/i)
  })
})

describe('return infrastructure metadata validation', () => {
  it.each([null, undefined, {}, [], 1, 'fixed-shore-v1'])('rejects malformed metadata %j', value => {
    expect(() => readReturnInfrastructure(value)).toThrow()
  })

  it.each(['version', 'coordinateFrame', 'missing-shore', 'empty-slots', 'too-many-slots', 'duplicate-code', 'wrong-kind', 'wrong-code', 'reversed-east', 'reversed-north'])(
    'rejects %s metadata', fault => {
      const value = layout()
      if (fault === 'version') Object.assign(value, { version: 'unsupported' })
      if (fault === 'coordinateFrame') Object.assign(value, { coordinateFrame: 'UNITY_WORLD' })
      if (fault === 'missing-shore') Object.assign(value, { shore: null })
      if (fault === 'empty-slots') value.slots = []
      if (fault === 'too-many-slots') value.slots = Array.from({ length: 257 }, () => ({ ...value.slots[0]! }))
      if (fault === 'duplicate-code') value.slots[1]!.deviceCode = value.slots[0]!.deviceCode
      if (fault === 'wrong-kind') Object.assign(value.slots[0]!, { kind: 'UNKNOWN' })
      if (fault === 'wrong-code') value.slots[0]!.deviceCode = 'USV-001'
      if (fault === 'reversed-east') value.shore.eastMaxM = value.shore.eastMinM
      if (fault === 'reversed-north') value.shore.northMaxM = value.shore.northMinM - 1
      expect(() => readReturnInfrastructure(value)).toThrow()
      expect(() => toGlobalReturnInfrastructure(value, origin)).toThrow()
    },
  )

  it.each(['eastMinM', 'eastMaxM', 'northMinM', 'northMaxM', 'surfaceUpM'] as const)('rejects a nonfinite shore %s', field => {
    const value = layout()
    value.shore[field] = Number.NaN
    expect(() => readReturnInfrastructure(value)).toThrow()
  })

  it.each(['eastM', 'northM', 'upM', 'headingDeg', 'approachEastM', 'approachNorthM', 'approachUpM'] as const)('rejects a nonfinite slot %s', field => {
    const value = layout()
    value.slots[0]![field] = Number.POSITIVE_INFINITY
    expect(() => readReturnInfrastructure(value)).toThrow()
  })

  it('does not coerce numeric strings or accept a null slot', () => {
    const value = layout()
    Object.assign(value.slots[0]!, { eastM: '123' })
    expect(() => readReturnInfrastructure(value)).toThrow()
    value.slots = [null] as unknown as ReturnInfrastructure['slots']
    expect(() => readReturnInfrastructure(value)).toThrow()
  })
})

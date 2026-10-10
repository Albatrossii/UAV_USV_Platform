import type { AlgorithmCoordinateFrame, EnuOrigin } from './virtualAlgorithmFrameAdapter'

export interface ReturnParkingSlot {
  deviceCode: string
  kind: 'HELIPAD' | 'BERTH'
  eastM: number
  northM: number
  upM: number
  headingDeg: number
  approachEastM: number
  approachNorthM: number
  approachUpM: number
}

export interface ReturnInfrastructure {
  version: 'fixed-shore-v1' | 'scene-existing-v1'
  coordinateFrame: AlgorithmCoordinateFrame
  shore: {
    eastMinM: number
    eastMaxM: number
    northMinM: number
    northMaxM: number
    surfaceUpM: number
  }
  slots: ReturnParkingSlot[]
}

/** Anchors reference the original Unity scene; this contract creates no geometry. */
export function buildReturnInfrastructure(options: {
  uavCount: number
  usvCount: number
  worldHeight: number
  fleetOrigin?: EnuOrigin
}): ReturnInfrastructure {
  const count = (value: number) => Math.max(1, Math.min(128, Math.floor(Number(value) || 1)))
  const uavs = Math.min(3, count(options.uavCount))
  const usvs = Math.min(3, count(options.usvCount))
  const origin = options.fleetOrigin ?? { eastM: -360, northM: -285, upM: 0 }
  if (![origin.eastM, origin.northM, origin.upM].every(Number.isFinite)) {
    throw new Error('Existing scene anchors require a finite fleet origin')
  }
  // Descriptive bounds of the existing island base only: neither a new shore
  // mesh nor a water-domain constraint, and never an overview camera target.
  const shore = {
    eastMinM: -110 - origin.eastM, eastMaxM: -35 - origin.eastM,
    northMinM: -245 - origin.northM, northMaxM: -185 - origin.northM,
    surfaceUpM: 19.65 - origin.upM,
  }
  const slots: ReturnParkingSlot[] = []
  for (let index = 0; index < uavs; index++) {
    // SimulationBootstrap.BuildIslandUavBase: center (-75,-215), yaw .559
    // radians, original pad offsets -14/0/+14. H-cross top is 19.65m.
    const offset = (index - 1) * 14
    const eastM = -75 + Math.cos(.559) * offset - origin.eastM
    const northM = -215 + Math.sin(.559) * offset - origin.northM
    slots.push({
      deviceCode: `UAV-${String(index + 1).padStart(3, '0')}`, kind: 'HELIPAD',
      eastM, northM, upM: shore.surfaceUpM, headingDeg: 90 - .559 * 180 / Math.PI,
      approachEastM: eastM, approachNorthM: northM, approachUpM: shore.surfaceUpM + 20,
    })
  }
  // Original Catalina southwest shoreline. Each center remains in water;
  // Editor mesh validation samples the 12m-wide approach corridor every 1m.
  // These are invisible station-keeping anchors, not newly built piers.
  const berths = [
    { east: -92, north: -180, approachNorth: -210 },
    { east: -108, north: -160, approachNorth: -190 },
    { east: -108, north: -140, approachNorth: -170 },
  ]
  for (let index = 0; index < usvs; index++) {
    const berth = berths[index]!
    slots.push({
      deviceCode: `USV-${String(index + 1).padStart(3, '0')}`, kind: 'BERTH',
      eastM: berth.east - origin.eastM, northM: berth.north - origin.northM,
      upM: -origin.upM, headingDeg: 90,
      approachEastM: -240 - origin.eastM, approachNorthM: berth.approachNorth - origin.northM,
      approachUpM: -origin.upM,
    })
  }
  return { version: 'scene-existing-v1', coordinateFrame: 'FLEET_LOCAL_ENU', shore, slots }
}

export function readReturnInfrastructure(value: unknown): ReturnInfrastructure {
  const layout = value as ReturnInfrastructure | null
  if (!layout || !['fixed-shore-v1', 'scene-existing-v1'].includes(layout.version)
    || !['FLEET_LOCAL_ENU', 'GLOBAL_ENU'].includes(layout.coordinateFrame)
    || !layout.shore || !Array.isArray(layout.slots) || !layout.slots.length || layout.slots.length > 256) {
    throw new Error('Invalid return infrastructure metadata')
  }
  const shore = layout.shore
  if (![shore.eastMinM, shore.eastMaxM, shore.northMinM, shore.northMaxM, shore.surfaceUpM].every(Number.isFinite)
    || shore.eastMinM >= shore.eastMaxM || shore.northMinM >= shore.northMaxM) {
    throw new Error('Invalid return shore bounds')
  }
  const codes = new Set<string>()
  for (const slot of layout.slots) {
    const prefix = slot?.kind === 'HELIPAD' ? 'UAV' : slot?.kind === 'BERTH' ? 'USV' : ''
    if (!prefix || !new RegExp(`^${prefix}-[0-9]{3}$`).test(slot.deviceCode) || codes.has(slot.deviceCode)
      || ![slot.eastM, slot.northM, slot.upM, slot.headingDeg,
        slot.approachEastM, slot.approachNorthM, slot.approachUpM].every(Number.isFinite)) {
      throw new Error('Invalid or duplicated return parking slot')
    }
    codes.add(slot.deviceCode)
  }
  return layout
}

/** Convert once according to the metadata's own coordinate frame, never mutate it. */
export function toGlobalReturnInfrastructure(value: ReturnInfrastructure, origin?: EnuOrigin): ReturnInfrastructure {
  const layout = readReturnInfrastructure(value)
  const local = layout.coordinateFrame === 'FLEET_LOCAL_ENU'
  if (local && (!origin || ![origin.eastM, origin.northM, origin.upM].every(Number.isFinite))) {
    throw new Error('Local return infrastructure requires a finite fleet origin')
  }
  const east = local ? origin!.eastM : 0
  const north = local ? origin!.northM : 0
  const up = local ? origin!.upM : 0
  return {
    version: layout.version, coordinateFrame: 'GLOBAL_ENU',
    shore: {
      eastMinM: layout.shore.eastMinM + east, eastMaxM: layout.shore.eastMaxM + east,
      northMinM: layout.shore.northMinM + north, northMaxM: layout.shore.northMaxM + north,
      surfaceUpM: layout.shore.surfaceUpM + up,
    },
    slots: layout.slots.map(slot => ({
      ...slot, eastM: slot.eastM + east, northM: slot.northM + north, upM: slot.upM + up,
      approachEastM: slot.approachEastM + east, approachNorthM: slot.approachNorthM + north,
      approachUpM: slot.approachUpM + up,
    })),
  }
}

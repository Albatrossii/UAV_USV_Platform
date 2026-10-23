import type {
  AlgorithmAgentFrame,
  AlgorithmRuntimeFrame,
  AlgorithmTargetFrame,
} from '../types/mission'
import { readReturnInfrastructure, toGlobalReturnInfrastructure, type ReturnInfrastructure } from './virtualReturnInfrastructure'

export const VIRTUAL_SIMULATION_RUNTIME_MODE = 'VIRTUAL_SIMULATION' as const
export const UAV_MAX_SPEED_MPS = 15
export const USV_MAX_SPEED_MPS = 4
export type AlgorithmCoordinateFrame = 'FLEET_LOCAL_ENU' | 'GLOBAL_ENU'

export interface EnuOrigin {
  eastM: number
  northM: number
  upM: number
}

export interface VirtualAlgorithmFrameAdapterOptions {
  coordinateFrame?: AlgorithmCoordinateFrame
  fleetOrigin?: EnuOrigin
}

export interface VirtualPoseInput {
  deviceCode: string
  deviceType?: 'UAV' | 'USV'
  targetType?: string
  eastM: number
  northM: number
  upM: number
  headingDeg: number
  speedMps: number
  state: string
  valid: boolean
}

export interface VirtualPoseBatchPayload {
  runtimeMode: typeof VIRTUAL_SIMULATION_RUNTIME_MODE
  coordinateFrame: 'GLOBAL_ENU'
  runId: number
  sequence: number
  sampleTime: number
  vehicles: VirtualPoseInput[]
  targets: VirtualPoseInput[]
  returnInfrastructure?: ReturnInfrastructure
}

export interface VirtualPoseState {
  eastM: number
  northM: number
  upM: number
  timestamp: number
  headingDeg: number
}

export type VirtualPoseStateMap = Map<string, VirtualPoseState>

export interface VirtualAlgorithmFrameAdapterResult {
  payload: VirtualPoseBatchPayload
  nextState: VirtualPoseStateMap
  sourceCoordinateFrame: AlgorithmCoordinateFrame
  fleetOrigin: EnuOrigin
}

function finiteOr(value: unknown, fallback: number) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function normalizeHeading(value: number) {
  return ((value % 360) + 360) % 360
}

function canonicalDeviceCode(code: string, type: 'UAV' | 'USV') {
  const normalized = code.trim().toUpperCase()
  const match = normalized.match(/(\d+)$/)
  const number = match ? Number(match[1]) : 1
  return `${type}-${String(Math.max(1, number)).padStart(3, '0')}`
}

function canonicalVehicleType(agent: AlgorithmAgentFrame) {
  return agent.type === 'USV' ? 'USV' : 'UAV'
}

function resolveCoordinateContext(
  frame: AlgorithmRuntimeFrame,
  options: VirtualAlgorithmFrameAdapterOptions,
) {
  const coordinateFrame = frame.coordinateFrame ?? options.coordinateFrame
  if (!coordinateFrame) {
    throw new Error('Algorithm frame coordinateFrame must be declared')
  }
  if (coordinateFrame === 'GLOBAL_ENU') {
    return {
      coordinateFrame,
      fleetOrigin: { eastM: 0, northM: 0, upM: 0 },
    }
  }

  const origin = options.fleetOrigin
  if (
    !origin
    || !Number.isFinite(origin.eastM)
    || !Number.isFinite(origin.northM)
    || !Number.isFinite(origin.upM)
  ) {
    throw new Error('FLEET_LOCAL_ENU requires a finite fleetOrigin')
  }
  return {
    coordinateFrame,
    fleetOrigin: { ...origin },
  }
}

function speedFromDelta(
  code: string,
  eastM: number,
  northM: number,
  upM: number,
  timestamp: number,
  previous: VirtualPoseStateMap,
) {
  const prior = previous.get(code)
  if (!prior) return 0
  const elapsedSeconds = (timestamp - prior.timestamp) / 1000
  if (elapsedSeconds <= 0) return 0
  const distance = Math.hypot(
    eastM - prior.eastM,
    northM - prior.northM,
    upM - prior.upM,
  )
  return distance / elapsedSeconds
}

function poseFromAgent(
  agent: AlgorithmAgentFrame,
  frame: AlgorithmRuntimeFrame,
  previous: VirtualPoseStateMap,
  origin: EnuOrigin,
): VirtualPoseInput {
  const deviceType = canonicalVehicleType(agent)
  const deviceCode = canonicalDeviceCode(agent.code, deviceType)
  const eastM = finiteOr(agent.x, 0) + origin.eastM
  const northM = finiteOr(agent.y, 0) + origin.northM
  const upM = finiteOr(agent.z, deviceType === 'UAV' ? 25 : 0) + origin.upM
  const prior = previous.get(deviceCode)
  const deltaEast = prior ? eastM - prior.eastM : 0
  const deltaNorth = prior ? northM - prior.northM : 0
  // Algorithm heading is authoritative. It may intentionally differ from
  // the instantaneous displacement near an interception or final ring slot
  // (for example, a USV moving tangentially while turning its bow inward).
  // Only derive a heading when the algorithm did not provide a finite value.
  // GLOBAL_ENU uses 0=east and positive angles toward north.
  const algorithmHeading = finiteOr(agent.heading, Number.NaN)
  const heading = Number.isFinite(algorithmHeading)
    ? algorithmHeading
    : Math.hypot(deltaEast, deltaNorth) > 0.0001
      ? Math.atan2(deltaNorth, deltaEast) * 180 / Math.PI
      : prior?.headingDeg ?? 0
  const maxSpeed = deviceType === 'UAV' ? UAV_MAX_SPEED_MPS : USV_MAX_SPEED_MPS
  const speedMps = Math.min(
    maxSpeed,
    Math.max(0, speedFromDelta(deviceCode, eastM, northM, upM, frame.timestamp, previous)),
  )

  return {
    deviceCode,
    deviceType,
    eastM,
    northM,
    upM,
    headingDeg: normalizeHeading(heading),
    speedMps,
    state: agent.status || (deviceType === 'UAV' ? 'AIRBORNE' : 'SAILING'),
    valid: true,
  }
}

function poseFromTarget(
  target: AlgorithmTargetFrame,
  targetIndex: number,
  frame: AlgorithmRuntimeFrame,
  previous: VirtualPoseStateMap,
  origin: EnuOrigin,
): VirtualPoseInput {
  // Runtime target identity must match the codes used when Unity initialized
  // the scene. Escort missions create PROTECTED-* and THREAT-* objects, while
  // capture missions use TARGET-*. Re-numbering escort targets to TARGET-* made
  // Unity update a different object (or reject the pose), visually leaving the
  // protected vessel outside while a threat appeared at the formation centre.
  const canonical = target.code.trim().toUpperCase()
  const supportedCode = /^(TARGET|PROTECTED|THREAT)-\d+$/.test(canonical)
  const deviceCode = supportedCode
    ? `${canonical.split('-')[0]}-${String(Number(canonical.split('-')[1])).padStart(3, '0')}`
    : `TARGET-${String(targetIndex + 1).padStart(3, '0')}`
  const eastM = finiteOr(target.x, 0) + origin.eastM
  const northM = finiteOr(target.y, 0) + origin.northM
  const upM = finiteOr(target.z, 0) + origin.upM
  const prior = previous.get(deviceCode)
  const deltaEast = prior ? eastM - prior.eastM : 0
  const deltaNorth = prior ? northM - prior.northM : 0
  const algorithmHeading = finiteOr(target.heading, Number.NaN)
  const heading = Number.isFinite(algorithmHeading)
    ? algorithmHeading
    : Math.hypot(deltaEast, deltaNorth) > 0.0001
      ? Math.atan2(deltaNorth, deltaEast) * 180 / Math.PI
      : prior?.headingDeg ?? 0
  const speedMps = Math.min(
    USV_MAX_SPEED_MPS,
    Math.max(0, speedFromDelta(deviceCode, eastM, northM, upM, frame.timestamp, previous)),
  )

  return {
    deviceCode,
    targetType: target.type,
    eastM,
    northM,
    upM,
    headingDeg: normalizeHeading(heading),
    speedMps,
    state: target.visible === false ? 'HIDDEN' : (target.state || target.type),
    valid: true,
  }
}

function rememberPose(
  state: VirtualPoseStateMap,
  pose: VirtualPoseInput,
  timestamp: number,
) {
  state.set(pose.deviceCode, {
    eastM: pose.eastM,
    northM: pose.northM,
    upM: pose.upM,
    timestamp,
    headingDeg: pose.headingDeg,
  })
}

export function adaptVirtualAlgorithmFrame(
  frame: AlgorithmRuntimeFrame,
  previous: VirtualPoseStateMap = new Map(),
  options: VirtualAlgorithmFrameAdapterOptions = {},
): VirtualAlgorithmFrameAdapterResult {
  if (!Number.isInteger(frame.runId) || frame.runId <= 0) {
    throw new Error('Algorithm frame runId must be a positive integer')
  }
  if (!Number.isInteger(frame.sequence) || frame.sequence <= 0) {
    throw new Error('Algorithm frame sequence must be a positive integer')
  }
  if (!Number.isFinite(frame.timestamp)) {
    throw new Error('Algorithm frame timestamp must be finite')
  }

  const { coordinateFrame, fleetOrigin } = resolveCoordinateContext(frame, options)
  const nextState = new Map(previous)
  const vehicles = frame.agents.map(
    agent => poseFromAgent(agent, frame, previous, fleetOrigin),
  )
  const targets = frame.targets.map(
    (target, index) => poseFromTarget(target, index, frame, previous, fleetOrigin),
  )

  for (const pose of [...vehicles, ...targets]) rememberPose(nextState, pose, frame.timestamp)
  const infrastructure = frame.metrics?.returnInfrastructure
  const returnInfrastructure = infrastructure == null ? undefined
    : toGlobalReturnInfrastructure(readReturnInfrastructure(infrastructure), options.fleetOrigin)

  return {
    payload: {
      runtimeMode: VIRTUAL_SIMULATION_RUNTIME_MODE,
      coordinateFrame: 'GLOBAL_ENU',
      runId: frame.runId,
      sequence: frame.sequence,
      sampleTime: frame.timestamp,
      vehicles,
      targets,
      ...(returnInfrastructure ? { returnInfrastructure } : {}),
    },
    nextState,
    sourceCoordinateFrame: coordinateFrame,
    fleetOrigin,
  }
}

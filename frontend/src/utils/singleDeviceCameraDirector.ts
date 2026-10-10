import type { VoiceExecution, VoiceExecutionState, VoiceProposal, VoiceRuntimeContext } from '@/types/voiceControl'

export interface SingleDeviceCameraObservation {
  proposal: VoiceProposal | null
  execution: VoiceExecution | null
  context: VoiceRuntimeContext | null
  algorithmRunId: string
  deviceCodes: string[]
  sceneKey: string
  ready: boolean
}

export interface SingleDeviceCameraDirectorOptions {
  focus: (deviceCode: string) => void
  overview: () => void
  focusDurationMs?: number
  onFocusChange?: (active: boolean) => void
}

const dispatchedStates = new Set<VoiceExecutionState>(['DISPATCHED', 'ACCEPTED', 'EXECUTING', 'SUCCEEDED'])
const rejectedStates = new Set<VoiceExecutionState>(['REJECTED', 'FAILED', 'INVALIDATED', 'TIMED_OUT'])
const maxExecutionAgeMs = 30_000
const maxRememberedExecutions = 128

function executionKey(execution: VoiceExecution | null) {
  return execution?.executionId
    ? JSON.stringify([execution.runtimeRef, execution.runtimeGeneration, execution.executionId])
    : ''
}

function scopeKey(input: SingleDeviceCameraObservation) {
  return JSON.stringify([input.algorithmRunId, input.context?.runtimeRef, input.context?.runtimeGeneration, input.sceneKey])
}

function sceneIsReady(input: SingleDeviceCameraObservation) {
  return Boolean(input.ready && input.sceneKey && input.algorithmRunId && input.context?.sceneReady
    && input.context.runtimeRef && input.context.runtimeGeneration
    && input.context.algorithmRunId === input.algorithmRunId)
}

function executionMatchesPlan(input: SingleDeviceCameraObservation) {
  const { proposal, execution, context } = input
  return Boolean(proposal && execution && context
    && proposal.status === 'CONFIRMED'
    && proposal.proposalId === execution.proposalId
    && proposal.executionId === execution.executionId
    && proposal.plan.action === execution.action
    && proposal.plan.runtimeRef === context.runtimeRef
    && execution.runtimeRef === context.runtimeRef
    && proposal.plan.runtimeGeneration === context.runtimeGeneration
    && execution.runtimeGeneration === context.runtimeGeneration)
}

function dispatchedTarget(input: SingleDeviceCameraObservation): string | null {
  const { proposal, execution } = input
  if (!proposal || !execution || !dispatchedStates.has(execution.state)) return null
  let target: string | undefined
  if (execution.action === 'DEVICE_COMMAND') {
    target = proposal.plan.targetDeviceCode
    if (!proposal.plan.deviceCommandType) return null
  } else if (execution.action === 'SEQUENCE') {
    // The currently supported sequence is a frozen START -> device command pair.
    const planSteps = proposal.plan.steps
    const steps = execution.steps
    if (planSteps?.length !== 2 || steps?.length !== 2) return null
    const [startPlan, devicePlan] = planSteps
    const [start, device] = steps
    if (!startPlan || !devicePlan || !start || !device
      || startPlan.index !== 0 || startPlan.action !== 'START'
      || devicePlan.index !== 1 || devicePlan.action !== 'DEVICE_COMMAND'
      || start.index !== 0 || start.action !== 'START' || start.state !== 'SUCCEEDED'
      || device.index !== 1 || device.action !== 'DEVICE_COMMAND'
      || !device.executionId || device.state === 'PENDING' || !dispatchedStates.has(device.state)
      || !devicePlan.deviceCommandType || device.deviceCommandType !== devicePlan.deviceCommandType
      || device.targetDeviceCode !== devicePlan.targetDeviceCode) return null
    target = devicePlan.targetDeviceCode
  }
  return target && input.deviceCodes.includes(target) && proposal.plan.explicitDeviceCodes.includes(target) ? target : null
}

/** Briefly identify a dispatched device, then return to the fleet overview.
 * Cancellation deliberately does not move the camera: manual navigation wins.
 */
export function createSingleDeviceCameraDirector(options: SingleDeviceCameraDirectorOptions) {
  const duration = Number.isFinite(options.focusDurationMs) && (options.focusDurationMs ?? 0) > 0
    ? options.focusDurationMs! : 2_500
  const remembered = new Set<string>()
  let timer: ReturnType<typeof setTimeout> | null = null
  let active: { executionKey: string; deviceCode: string } | null = null
  let lastScope: string | null = null
  let lastExecutionKey = ''
  let disposed = false

  function remember(key: string) {
    if (!key || remembered.has(key)) return
    remembered.add(key)
    if (remembered.size > maxRememberedExecutions) {
      const oldest = remembered.values().next().value
      if (oldest) remembered.delete(oldest)
    }
  }

  function stopFocus() {
    if (timer !== null) clearTimeout(timer)
    timer = null
    if (active) {
      active = null
      options.onFocusChange?.(false)
    }
  }

  function cancel() {
    remember(lastExecutionKey)
    if (active) remember(active.executionKey)
    stopFocus()
  }

  function observe(input: SingleDeviceCameraObservation) {
    if (disposed) return
    const key = executionKey(input.execution)
    const scope = scopeKey(input)
    const changedScene = lastScope !== null && scope !== lastScope
    if (changedScene || !sceneIsReady(input)) {
      remember(lastExecutionKey)
      remember(key)
      stopFocus()
    }
    lastScope = scope
    lastExecutionKey = key
    if (changedScene || !sceneIsReady(input)) return
    if (active && !input.deviceCodes.includes(active.deviceCode)) stopFocus()
    if (!key || !input.execution || remembered.has(key)) return
    if (!executionMatchesPlan(input)) {
      remember(key)
      return
    }
    const execution = input.execution
    const createdAt = Date.parse(execution.createdAt)
    const age = Date.now() - createdAt
    // Polling or restoring an old command must not replay its camera animation.
    if (!Number.isFinite(createdAt) || age < -5_000 || age > maxExecutionAgeMs
      || rejectedStates.has(execution.state)
      || execution.steps?.some(step => step.state !== 'PENDING' && rejectedStates.has(step.state))) {
      remember(key)
      return
    }
    const target = dispatchedTarget(input)
    if (!target) return
    remember(key)
    stopFocus()
    active = { executionKey: key, deviceCode: target }
    options.onFocusChange?.(true)
    options.focus(target)
    timer = setTimeout(() => {
      timer = null
      if (!active || disposed) return
      active = null
      options.onFocusChange?.(false)
      options.overview()
    }, duration)
  }

  return {
    observe,
    cancel,
    dispose() {
      if (disposed) return
      cancel()
      disposed = true
    },
  }
}

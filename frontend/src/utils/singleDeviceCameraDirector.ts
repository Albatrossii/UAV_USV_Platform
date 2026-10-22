import type { VoiceExecution, VoiceExecutionState, VoiceProposal, VoiceRuntimeContext } from '@/types/voiceControl'
import { presetDeviceCodes } from '@/services/algorithmPresets'

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
  focusGroup?: (deviceCodes: string[]) => void
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

function dispatchedDevice(input: SingleDeviceCameraObservation): { target: string; targets?: string[]; key: string; startedAt: string } | null {
  const { proposal, execution } = input
  if (!proposal || !execution || !dispatchedStates.has(execution.state)) return null
  let target: string | undefined
  let targets: string[] | undefined
  let key = executionKey(execution)
  let startedAt = execution.createdAt
  if (execution.action === 'DEVICE_COMMAND') {
    target = proposal.plan.targetDeviceCode
    if (!proposal.plan.deviceCommandType) return null
  } else if (execution.action === 'ALGORITHM_PRESET') {
    if (!['ACCEPTED', 'EXECUTING', 'SUCCEEDED'].includes(execution.state)
      || ['FAILED', 'SUPERSEDED', 'CANCELLED'].includes(execution.presetExecution?.status ?? '')) return null
    targets = presetDeviceCodes(proposal.plan.parameters)
    target = targets[0]
  } else if (execution.action === 'SEQUENCE') {
    const planSteps = proposal.plan.steps
    const steps = execution.steps
    if (!planSteps?.length || steps?.length !== planSteps.length) return null
    if (steps.some((step, index) => {
      const plan = planSteps[index]
      return !plan || step.index !== index || plan.index !== index
        || step.action !== plan.action || step.targetDeviceCode !== plan.targetDeviceCode
        || step.deviceCommandType !== plan.deviceCommandType || step.waitSeconds !== plan.waitSeconds
        || Boolean(step.waitForPrevious) !== Boolean(plan.waitForPrevious)
        || JSON.stringify(step.parameters) !== JSON.stringify(plan.parameters)
    })) return null
    // Each dispatched device step has its own camera identity. A later WAIT
    // must not replay an earlier focus, and a long sequence can focus a fresh
    // step even after its parent command is older than thirty seconds.
    let index = steps.length - 1
    while (index >= 0 && (steps[index]!.state === 'PENDING' || steps[index]!.state === 'QUEUED')) index--
    const device = steps[index]
    if (!device || !['DEVICE_COMMAND', 'ALGORITHM_PRESET'].includes(device.action) || !device.executionId
      || (device.action === 'DEVICE_COMMAND' && !device.deviceCommandType) || device.state === 'PENDING' || !dispatchedStates.has(device.state)
      || steps.slice(0, index).some(step => step.state !== 'SUCCEEDED')) return null
    target = device.targetDeviceCode
    if (device.action === 'ALGORITHM_PRESET') {
      targets = presetDeviceCodes(device.parameters)
      target = targets[0]
    }
    key = JSON.stringify([key, index, device.executionId])
    startedAt = device.startedAt ?? execution.createdAt
  }
  return target && input.deviceCodes.includes(target) && proposal.plan.explicitDeviceCodes.includes(target)
    && (!targets || targets.every(code => input.deviceCodes.includes(code) && proposal.plan.explicitDeviceCodes.includes(code)))
    ? { target, targets, key, startedAt } : null
}

/** Briefly identify dispatched devices, then invoke the caller's camera restoration.
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
    if (rejectedStates.has(execution.state)
      || execution.steps?.some(step => step.state !== 'PENDING' && rejectedStates.has(step.state))) {
      remember(key)
      return
    }
    const device = dispatchedDevice(input)
    if (!device || remembered.has(device.key)) return
    const createdAt = Date.parse(device.startedAt)
    const age = Date.now() - createdAt
    // Polling or restoring an old receipt must not replay its animation.
    if (!Number.isFinite(createdAt) || age < -5_000 || age > maxExecutionAgeMs) {
      remember(device.key)
      return
    }
    remember(device.key)
    stopFocus()
    active = { executionKey: device.key, deviceCode: device.target }
    options.onFocusChange?.(true)
    if (device.targets && device.targets.length > 1 && options.focusGroup) options.focusGroup(device.targets)
    else options.focus(device.target)
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

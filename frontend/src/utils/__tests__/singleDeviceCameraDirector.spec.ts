import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createSingleDeviceCameraDirector, type SingleDeviceCameraObservation } from '@/utils/singleDeviceCameraDirector'
import type { VoiceExecutionState } from '@/types/voiceControl'

function input(id = 'execution-1', target = 'UAV-001'): SingleDeviceCameraObservation {
  const now = new Date().toISOString()
  return {
    ready: true,
    sceneKey: 'unity-instance:1',
    algorithmRunId: 'run-1',
    deviceCodes: ['UAV-001', 'USV-001'],
    context: {
      runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', algorithmRunId: 'run-1',
      contextVersion: 3, stateVersion: 4, runtimeScope: 'MISSION_CENTER',
      runtimeKind: 'STANDALONE_ALGORITHM', executionBackend: 'PYTHON_SIMULATION',
      missionId: null, missionRunId: null, state: 'RUNNING', protocolVersion: 'algorithm.command.v1',
      capabilities: ['DEVICE_COMMAND'], lastHeartbeatReceivedAt: now, latestFrameSequence: 7, sceneReady: true,
    },
    proposal: {
      proposalId: `proposal-${id}`, status: 'CONFIRMED', planVersion: 1, planHash: 'plan-hash',
      requiresConfirmation: true, executionId: id, createdAt: now,
      expiresAt: new Date(Date.now() + 30_000).toISOString(),
      plan: {
        runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', contextVersion: 3, stateVersion: 3,
        action: 'DEVICE_COMMAND', targetDeviceCode: target,
        deviceCommandType: target.startsWith('UAV') ? 'UAV_HOVER' : 'USV_HOLD',
        explicitDeviceCodes: [target], policyVersion: 'voice-p0.v1',
      },
    },
    execution: {
      executionId: id, proposalId: `proposal-${id}`, commandId: `command-${id}`,
      runtimeRef: 'runtime-1', runtimeGeneration: 'generation-1', action: 'DEVICE_COMMAND',
      state: 'DISPATCHED', outcome: 'UNKNOWN', errorCode: null, timedOutAt: null,
      presentationStatus: 'NOT_REQUIRED', createdAt: now, updatedAt: now,
    },
  }
}

function sequence(): SingleDeviceCameraObservation {
  const result = input()
  result.proposal!.plan = {
    ...result.proposal!.plan,
    action: 'SEQUENCE', targetDeviceCode: undefined, deviceCommandType: undefined,
    steps: [
      { index: 0, action: 'START' },
      { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_HOVER' },
    ],
  }
  result.execution!.action = 'SEQUENCE'
  result.execution!.state = 'EXECUTING'
  result.execution!.sequenceStatus = 'WAITING_STEP_PRECONDITION'
  result.execution!.steps = [
    { index: 0, action: 'START', state: 'SUCCEEDED', executionId: 'start-1', errorCode: null },
    { index: 1, action: 'DEVICE_COMMAND', targetDeviceCode: 'UAV-001', deviceCommandType: 'UAV_HOVER',
      state: 'PENDING', executionId: null, errorCode: null },
  ]
  return result
}

function rig(focusDurationMs?: number) {
  const focus = vi.fn()
  const overview = vi.fn()
  const onFocusChange = vi.fn()
  const director = createSingleDeviceCameraDirector({ focus, overview, onFocusChange, focusDurationMs })
  return { director, focus, overview, onFocusChange }
}

describe('single device command camera director', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-09T09:00:00.000Z'))
  })

  afterEach(() => {
    vi.clearAllTimers()
    vi.useRealTimers()
  })

  it('focuses a dispatched device once, then shows the fleet after 2500 ms', () => {
    const { director, focus, overview, onFocusChange } = rig()
    director.observe(input())
    expect(focus).toHaveBeenCalledExactlyOnceWith('UAV-001')
    expect(onFocusChange).toHaveBeenCalledExactlyOnceWith(true)
    vi.advanceTimersByTime(2499)
    expect(overview).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(overview).toHaveBeenCalledTimes(1)
    expect(onFocusChange.mock.calls).toEqual([[true], [false]])
  })

  it.each(['DISPATCHED', 'ACCEPTED', 'EXECUTING', 'SUCCEEDED'] as VoiceExecutionState[])(
    'accepts a fresh direct execution in %s', state => {
      const { director, focus } = rig()
      const observation = input('execution-1', 'USV-001')
      observation.execution!.state = state
      director.observe(observation)
      expect(focus).toHaveBeenCalledExactlyOnceWith('USV-001')
    },
  )

  it('ignores previews and waits for a queued command to actually dispatch', () => {
    const { director, focus } = rig()
    const observation = input()
    const execution = observation.execution!
    observation.proposal!.status = 'AWAITING_CONFIRMATION'
    observation.proposal!.executionId = null
    observation.execution = null
    director.observe(observation)
    expect(focus).not.toHaveBeenCalled()
    observation.proposal!.status = 'CONFIRMED'
    observation.proposal!.executionId = execution.executionId
    observation.execution = execution
    execution.state = 'QUEUED'
    director.observe(observation)
    expect(focus).not.toHaveBeenCalled()
    execution.state = 'DISPATCHED'
    director.observe(observation)
    expect(focus).toHaveBeenCalledTimes(1)
  })

  it('does not restart the timer or replay the animation on execution polling', () => {
    const { director, focus, overview } = rig()
    director.observe(input())
    vi.advanceTimersByTime(1500)
    const poll = input()
    poll.execution!.state = 'SUCCEEDED'
    director.observe(poll)
    vi.advanceTimersByTime(1000)
    expect(overview).toHaveBeenCalledTimes(1)
    director.observe(poll)
    vi.advanceTimersByTime(5000)
    expect(focus).toHaveBeenCalledTimes(1)
    expect(overview).toHaveBeenCalledTimes(1)
  })

  it('replaces the previous return timer when a new device command dispatches', () => {
    const { director, focus, overview, onFocusChange } = rig()
    director.observe(input())
    vi.advanceTimersByTime(1500)
    director.observe(input('execution-2', 'USV-001'))
    vi.advanceTimersByTime(1000)
    expect(overview).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1500)
    expect(focus.mock.calls).toEqual([['UAV-001'], ['USV-001']])
    expect(overview).toHaveBeenCalledTimes(1)
    expect(onFocusChange.mock.calls).toEqual([[true], [false], [true], [false]])
  })

  it.each(['DISPATCHED', 'ACCEPTED', 'EXECUTING', 'SUCCEEDED'] as VoiceExecutionState[])(
    'waits for sequence step 2 before focusing its %s device command', state => {
      const { director, focus } = rig()
      const observation = sequence()
      director.observe(observation)
      const deviceStep = observation.execution!.steps![1]!
      deviceStep.state = 'QUEUED'
      deviceStep.executionId = 'device-step-1'
      director.observe(observation)
      expect(focus).not.toHaveBeenCalled()
      deviceStep.state = state
      director.observe(observation)
      expect(focus).toHaveBeenCalledExactlyOnceWith('UAV-001')
    },
  )

  it('requires the sequence device receipt to match the frozen step and a successful START', () => {
    const { director, focus } = rig()
    const observation = sequence()
    const [start, device] = observation.execution!.steps!
    device!.state = 'DISPATCHED'
    device!.executionId = 'device-step-1'
    device!.targetDeviceCode = 'USV-001'
    director.observe(observation)
    device!.targetDeviceCode = 'UAV-001'
    start!.state = 'EXECUTING'
    director.observe(observation)
    expect(focus).not.toHaveBeenCalled()
    start!.state = 'SUCCEEDED'
    director.observe(observation)
    expect(focus).toHaveBeenCalledTimes(1)
  })

  it.each(['REJECTED', 'FAILED', 'INVALIDATED', 'TIMED_OUT'] as VoiceExecutionState[])(
    'does not replay an initially %s execution even if a late success arrives', state => {
      const { director, focus } = rig()
      const observation = input()
      observation.execution!.state = state
      director.observe(observation)
      observation.execution!.state = 'SUCCEEDED'
      director.observe(observation)
      expect(focus).not.toHaveBeenCalled()
    },
  )

  it.each(['REJECTED', 'FAILED', 'INVALIDATED', 'TIMED_OUT'] as VoiceExecutionState[])(
    'still returns to overview if an already focused execution becomes %s', state => {
      const { director, overview } = rig()
      const observation = input()
      director.observe(observation)
      observation.execution!.state = state
      director.observe(observation)
      vi.advanceTimersByTime(2500)
      expect(overview).toHaveBeenCalledTimes(1)
    },
  )

  it('ignores a sequence whose device step has timed out before observation', () => {
    const { director, focus } = rig()
    const observation = sequence()
    observation.execution!.steps![1]!.state = 'TIMED_OUT'
    director.observe(observation)
    observation.execution!.steps![1]!.state = 'SUCCEEDED'
    observation.execution!.steps![1]!.executionId = 'device-step-1'
    director.observe(observation)
    expect(focus).not.toHaveBeenCalled()
  })

  it.each([
    ['another algorithm run', (value: SingleDeviceCameraObservation) => { value.algorithmRunId = 'old-run' }],
    ['another runtime reference', (value: SingleDeviceCameraObservation) => { value.execution!.runtimeRef = 'old-runtime' }],
    ['another runtime generation', (value: SingleDeviceCameraObservation) => { value.execution!.runtimeGeneration = 'old-generation' }],
    ['another frozen runtime', (value: SingleDeviceCameraObservation) => { value.proposal!.plan.runtimeRef = 'old-runtime' }],
    ['another frozen generation', (value: SingleDeviceCameraObservation) => { value.proposal!.plan.runtimeGeneration = 'old-generation' }],
    ['another proposal', (value: SingleDeviceCameraObservation) => { value.execution!.proposalId = 'old-proposal' }],
    ['another confirmed execution', (value: SingleDeviceCameraObservation) => { value.proposal!.executionId = 'old-execution' }],
    ['an unconfirmed proposal', (value: SingleDeviceCameraObservation) => { value.proposal!.status = 'AWAITING_CONFIRMATION' }],
    ['a cancelled proposal', (value: SingleDeviceCameraObservation) => { value.proposal!.status = 'CANCELLED' }],
    ['a different action', (value: SingleDeviceCameraObservation) => { value.execution!.action = 'STOP' }],
    ['a missing context', (value: SingleDeviceCameraObservation) => { value.context = null }],
    ['a missing proposal', (value: SingleDeviceCameraObservation) => { value.proposal = null }],
    ['an unknown scene device', (value: SingleDeviceCameraObservation) => { value.deviceCodes = ['USV-001'] }],
    ['an unfrozen device', (value: SingleDeviceCameraObservation) => { value.proposal!.plan.explicitDeviceCodes = ['USV-001'] }],
  ] as const)('ignores receipts from %s', (_label, mutate) => {
    const { director, focus, overview } = rig()
    const observation = input()
    mutate(observation)
    director.observe(observation)
    vi.advanceTimersByTime(2500)
    expect(focus).not.toHaveBeenCalled()
    expect(overview).not.toHaveBeenCalled()
  })

  it.each([-30_001, 5001, Number.NaN])('does not replay a stale or invalid createdAt offset %s', offset => {
    const { director, focus } = rig()
    const observation = input()
    observation.execution!.createdAt = Number.isNaN(offset) ? 'invalid' : new Date(Date.now() + offset).toISOString()
    director.observe(observation)
    expect(focus).not.toHaveBeenCalled()
  })

  it('does not use changing state versions or expired confirmation deadlines to cancel a valid dispatched command', () => {
    const { director, focus, overview } = rig()
    const observation = input()
    observation.proposal!.expiresAt = new Date(Date.now() - 1).toISOString()
    director.observe(observation)
    observation.context!.contextVersion++
    observation.context!.stateVersion++
    director.observe(observation)
    vi.advanceTimersByTime(2500)
    expect(focus).toHaveBeenCalledTimes(1)
    expect(overview).toHaveBeenCalledTimes(1)
  })

  it('never defers a command received while Unity is not ready', () => {
    const { director, focus, overview } = rig()
    const observation = input()
    observation.ready = false
    director.observe(observation)
    observation.ready = true
    director.observe(observation)
    vi.advanceTimersByTime(2500)
    expect(focus).not.toHaveBeenCalled()
    expect(overview).not.toHaveBeenCalled()
  })

  it.each(['scene', 'generation', 'ready', 'context-ready', 'target-removed'])(
    'cancels the return timer when %s changes and does not replay the old execution', change => {
      const { director, focus, overview, onFocusChange } = rig()
      const observation = input()
      director.observe(observation)
      vi.advanceTimersByTime(1000)
      if (change === 'scene') observation.sceneKey = 'unity-instance:2'
      if (change === 'generation') observation.context!.runtimeGeneration = 'generation-2'
      if (change === 'ready') observation.ready = false
      if (change === 'context-ready') observation.context!.sceneReady = false
      if (change === 'target-removed') observation.deviceCodes = ['USV-001']
      director.observe(observation)
      vi.advanceTimersByTime(2500)
      director.observe(input())
      expect(focus).toHaveBeenCalledTimes(1)
      expect(overview).not.toHaveBeenCalled()
      expect(onFocusChange.mock.calls).toEqual([[true], [false]])
    },
  )

  it('honors manual camera cancellation without moving the camera or replaying the command', () => {
    const { director, focus, overview, onFocusChange } = rig()
    director.observe(input())
    director.cancel()
    director.cancel()
    director.observe(input())
    vi.advanceTimersByTime(5000)
    expect(focus).toHaveBeenCalledTimes(1)
    expect(overview).not.toHaveBeenCalled()
    expect(onFocusChange.mock.calls).toEqual([[true], [false]])
    director.observe(input('execution-2'))
    expect(focus).toHaveBeenCalledTimes(2)
  })

  it('honors manual cancellation before the waiting sequence device step dispatches', () => {
    const { director, focus } = rig()
    const observation = sequence()
    director.observe(observation)
    director.cancel()
    observation.execution!.steps![1]!.state = 'DISPATCHED'
    observation.execution!.steps![1]!.executionId = 'device-step-1'
    director.observe(observation)
    expect(focus).not.toHaveBeenCalled()
  })

  it('never moves the camera again after dispose', () => {
    const { director, focus, overview, onFocusChange } = rig()
    director.observe(input())
    director.dispose()
    director.dispose()
    director.observe(input('execution-2'))
    vi.advanceTimersByTime(5000)
    expect(focus).toHaveBeenCalledTimes(1)
    expect(overview).not.toHaveBeenCalled()
    expect(onFocusChange.mock.calls).toEqual([[true], [false]])
  })

  it('allows a configured focus duration', () => {
    const { director, overview } = rig(1000)
    director.observe(input())
    vi.advanceTimersByTime(1000)
    expect(overview).toHaveBeenCalledTimes(1)
  })
})

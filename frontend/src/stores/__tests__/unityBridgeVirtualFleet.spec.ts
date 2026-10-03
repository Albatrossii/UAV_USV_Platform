import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useUnityBridgeStore } from '@/stores/unityBridge'

const scope = 'VIRTUAL_FLEET' as const
const runId = '1790992914400'

beforeEach(() => setActivePinia(createPinia()))

describe('virtual fleet Unity pose receipts', () => {
  it('replays only the newest applyPoseBatch after scene ready and accepts its receipt', () => {
    const bridge = useUnityBridgeStore()
    bridge.sendFor(scope, 'loadScenario', { runId, algorithmCode: 'ESCORT_GUARD' })
    bridge.removeNextFor(scope)
    bridge.sendFor(scope, 'applyPoseBatch', { runId, sequence: 1, vehicles: [{ deviceCode: 'UAV-001' }] })
    bridge.sendFor(scope, 'applyPoseBatch', { runId, sequence: 2, vehicles: [{ deviceCode: 'UAV-001' }] })

    expect(bridge.channels[scope].outbox).toHaveLength(0)
    bridge.markScenarioReadyFor(scope, { runId })
    expect(bridge.channels[scope].outbox.map(message => message.payload.sequence)).toEqual([2])

    const frame = bridge.peekNextFor(scope)!
    bridge.markPoseSentFor(scope, frame.payload)
    bridge.removeNextFor(scope)
    bridge.markPoseAppliedFor(scope, { runId, sequence: 2, success: true, appliedCount: 6 })

    expect(bridge.channels[scope].sentRunId).toBe(runId)
    expect(bridge.channels[scope].appliedRunId).toBe(Number(runId))
    expect(bridge.channels[scope].appliedSequence).toBe(2)
    expect(bridge.channels[scope].rejectReason).toBe('')
  })

  it('coalesces live applyPoseBatch frames and clears them for a different run', () => {
    const bridge = useUnityBridgeStore()
    bridge.sendFor(scope, 'loadScenario', { runId, algorithmCode: 'ESCORT_GUARD' })
    bridge.removeNextFor(scope)
    bridge.markScenarioReadyFor(scope, { runId })
    bridge.sendFor(scope, 'applyPoseBatch', { runId, sequence: 1 })
    bridge.sendFor(scope, 'applyPoseBatch', { runId, sequence: 2 })
    expect(bridge.channels[scope].outbox.map(message => message.payload.sequence)).toEqual([2])

    const nextRunId = '1790992914401'
    bridge.sendFor(scope, 'loadScenario', { runId: nextRunId, algorithmCode: 'ESCORT_GUARD' })
    expect(bridge.channels[scope].outbox.map(message => message.type)).toEqual(['loadScenario'])
    expect(bridge.channels[scope].latestPoseFrame).toBeNull()
    expect(bridge.channels[scope].sentRunId).toBeNull()
  })
})

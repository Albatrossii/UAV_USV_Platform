import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { buildReturnInfrastructure, toGlobalReturnInfrastructure } from '../virtualReturnInfrastructure'

function hostBridge() {
  const html = readFileSync(resolve(process.cwd(), 'public/unity-virtual-fleet/index.html'), 'utf8')
  const names = ['sendPlatformBridge', 'finiteNumber', 'normalizePose', 'normalizePoseBatch', 'normalizeMissionState', 'normalizeCommand', 'dispatchToUnity']
  const source = names.map(name => {
    const match = html.match(new RegExp(`function ${name}\\([^]*?\\n      \\}`))?.[0]
    expect(match, `deployed WebGL bridge function ${name}`).toBeTruthy()
    return match
  }).join('\n')
  const SendMessage = vi.fn()
  const window = { uavUsvUnityInstance: { SendMessage } }
  const bridge = new Function('window', `
    var runId = '', activeScenarioReadyRunId = '', latestAppliedPoseSequence = 0;
    ${source}
    return { normalizePoseBatch, dispatchToUnity };
  `)(window) as {
    normalizePoseBatch: (payload: Record<string, unknown>) => Record<string, unknown>
    dispatchToUnity: (message: { type: string; requestId?: string; payload: Record<string, unknown> }) => boolean
  }
  return { ...bridge, SendMessage }
}

const infrastructure = () => toGlobalReturnInfrastructure(
  buildReturnInfrastructure({ uavCount: 9, usvCount: 17, worldHeight: 400 }),
  { eastM: -360, northM: -285, upM: 6 },
)

describe('deployed Unity return infrastructure bridge', () => {
  it('preserves complete return metadata when normalizing a pose batch', () => {
    const bridge = hostBridge()
    const returnInfrastructure = infrastructure()
    const payload = { runId: 7001, sequence: 12, vehicles: [{ deviceCode: 'UAV-001', eastM: -360, northM: -285, upM: 30 }], returnInfrastructure }
    const original = structuredClone(payload)
    const normalized = bridge.normalizePoseBatch(payload)
    expect(normalized.returnInfrastructure).toEqual(returnInfrastructure)
    expect(normalized.poses).toHaveLength(1)
    expect(payload).toEqual(original)
  })

  it.each(['loadScenario', 'regenerateScenario', 'applyPoseBatch'])('sends %s infrastructure unchanged through the actual serialized PlatformBridge payload', type => {
    const bridge = hostBridge()
    const returnInfrastructure = infrastructure()
    const payload = { runId: 7001, sequence: 12, uavCount: 9, usvCount: 17, returnInfrastructure }
    const original = structuredClone(payload)
    expect(bridge.dispatchToUnity({ type, requestId: 'port-metadata-test', payload })).toBe(true)
    expect(bridge.SendMessage).toHaveBeenCalledOnce()
    const [objectName, method, serialized] = bridge.SendMessage.mock.calls[0]!
    expect(objectName).toBe('PlatformBridge')
    expect(method).toBe(type === 'loadScenario' ? 'LoadScenario' : type === 'regenerateScenario' ? 'RegenerateScenario' : 'ApplyPoseBatch')
    expect(JSON.parse(serialized)).toMatchObject({ requestId: 'port-metadata-test', returnInfrastructure })
    expect(payload).toEqual(original)
  })

  it.each(['loadScenario', 'regenerateScenario', 'applyPoseBatch'])('retains legacy %s compatibility without fabricating an infrastructure layout', type => {
    const bridge = hostBridge()
    expect(bridge.dispatchToUnity({ type, payload: { runId: 7001, sequence: 12 } })).toBe(true)
    const serialized = bridge.SendMessage.mock.calls[0]![2]
    const payload = JSON.parse(serialized)
    expect(payload.returnInfrastructure == null).toBe(true)
  })
})

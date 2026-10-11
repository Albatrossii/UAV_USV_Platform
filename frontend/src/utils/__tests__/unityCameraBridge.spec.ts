import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it, vi } from 'vitest'

function hostBridge() {
  const html = readFileSync(resolve(process.cwd(), 'public/unity-virtual-fleet/index.html'), 'utf8')
  const source = ['sendPlatformBridge', 'normalizeCommand', 'dispatchToUnity'].map(name => {
    const match = html.match(new RegExp(`function ${name}\\([^]*?\\n      \\}`))?.[0]
    expect(match, `deployed WebGL bridge function ${name}`).toBeTruthy()
    return match
  }).join('\n')
  const received: { type: string; requestId: string; deviceCode?: string; mode?: string }[] = []
  // The deployed Unity GameObject has both VirtualFleetPlatformBridge and
  // WebCommandBridge, with colliding SelectDevice/SetCameraMode method names.
  // Only WebCommandBridge owns ReceiveFromVue and parses its nested payload.
  const SendMessage = vi.fn((objectName: string, method: string, json: string) => {
    if (objectName !== 'PlatformBridge' || method !== 'ReceiveFromVue') {
      throw new Error('Camera message reached an ambiguous Unity method')
    }
    const envelope = JSON.parse(json)
    received.push({ type: envelope.type, requestId: envelope.requestId, ...envelope.payload })
  })
  const window = { uavUsvUnityInstance: { SendMessage } }
  const dispatchToUnity = new Function('window', `${source}; return dispatchToUnity;`)(window) as (
    message: { type: string; requestId?: string; payload: Record<string, unknown> },
  ) => boolean
  return { dispatchToUnity, SendMessage, received, window }
}

describe('deployed Unity camera message routing', () => {
  it.each(['UAV-001', 'USV-003'])('selects %s once through the complete command envelope', deviceCode => {
    const bridge = hostBridge()
    expect(bridge.dispatchToUnity({ type: 'selectDevice', requestId: 'select-test', payload: { deviceCode } })).toBe(true)
    expect(bridge.SendMessage).toHaveBeenCalledOnce()
    expect(bridge.received).toEqual([{ type: 'selectDevice', requestId: 'select-test', deviceCode }])
  })

  it('focuses a named device with one command, preserving deviceId compatibility', () => {
    const bridge = hostBridge()
    bridge.dispatchToUnity({ type: 'focusDevice', requestId: 'focus-test', payload: { deviceId: 'USV-002' } })
    expect(bridge.SendMessage).toHaveBeenCalledOnce()
    expect(bridge.received).toEqual([{ type: 'focusDevice', requestId: 'focus-test', deviceCode: 'USV-002' }])
  })

  it.each(['setCameraMode', 'switchCamera'])('routes %s to a single overview command without overloaded methods', type => {
    const bridge = hostBridge()
    bridge.dispatchToUnity({ type, requestId: 'overview-test', payload: { mode: 'overview' } })
    expect(bridge.SendMessage).toHaveBeenCalledOnce()
    expect(bridge.received).toEqual([{ type: 'switchCamera', requestId: 'overview-test', mode: 'overview', deviceCode: '' }])
  })

  it('does not claim to have sent a camera message before Unity exists', () => {
    const bridge = hostBridge()
    bridge.window.uavUsvUnityInstance = undefined as unknown as typeof bridge.window.uavUsvUnityInstance
    expect(bridge.dispatchToUnity({ type: 'selectDevice', payload: { deviceCode: 'UAV-001' } })).toBe(false)
    expect(bridge.SendMessage).not.toHaveBeenCalled()
  })
})

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useGimbalMoveStopGuard } from '@/composables/useGimbalMoveStopGuard'

describe('useGimbalMoveStopGuard', () => {
  let visibility: DocumentVisibilityState

  beforeEach(() => {
    visibility = 'visible'
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('starts once and sends only one stop for repeated release events', () => {
    const sendStop = vi.fn()
    const guard = useGimbalMoveStopGuard(sendStop)

    expect(guard.start()).toBe(true)
    expect(guard.start()).toBe(false)
    guard.stop()
    guard.stop()

    expect(sendStop).toHaveBeenCalledOnce()
    expect(guard.active.value).toBe(false)
  })

  it('stops a held move when the browser window loses focus', () => {
    const sendStop = vi.fn()
    const guard = useGimbalMoveStopGuard(sendStop)
    guard.attach()
    guard.start()

    window.dispatchEvent(new Event('blur'))

    expect(sendStop).toHaveBeenCalledOnce()
    expect(guard.active.value).toBe(false)
    guard.dispose()
  })

  it('stops only when the document becomes hidden', () => {
    const sendStop = vi.fn()
    const guard = useGimbalMoveStopGuard(sendStop)
    guard.attach()
    guard.start()

    document.dispatchEvent(new Event('visibilitychange'))
    expect(sendStop).not.toHaveBeenCalled()

    visibility = 'hidden'
    document.dispatchEvent(new Event('visibilitychange'))

    expect(sendStop).toHaveBeenCalledOnce()
    expect(guard.active.value).toBe(false)
    guard.dispose()
  })

  it('stops on disposal and detaches interruption listeners', () => {
    const sendStop = vi.fn()
    const guard = useGimbalMoveStopGuard(sendStop)
    guard.attach()
    guard.start()

    guard.dispose()
    expect(sendStop).toHaveBeenCalledOnce()

    guard.start()
    window.dispatchEvent(new Event('blur'))
    expect(sendStop).toHaveBeenCalledOnce()
    guard.stop()
  })
})

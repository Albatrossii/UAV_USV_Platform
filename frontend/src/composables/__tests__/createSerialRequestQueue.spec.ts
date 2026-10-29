import { describe, expect, it } from 'vitest'

import { createSerialRequestQueue } from '@/composables/createSerialRequestQueue'

describe('createSerialRequestQueue', () => {
  it('executes requests sequentially in enqueue order', async () => {
    const queue = createSerialRequestQueue()
    const events: string[] = []
    let releaseFirst!: () => void
    const firstGate = new Promise<void>(resolve => { releaseFirst = resolve })

    const first = queue.run(async () => {
      events.push('move-start')
      await firstGate
      events.push('move-end')
    })
    const stop = queue.run(async () => { events.push('stop') })

    await Promise.resolve()
    expect(events).toEqual(['move-start'])
    releaseFirst()
    await Promise.all([first, stop])
    expect(events).toEqual(['move-start', 'move-end', 'stop'])
  })

  it('continues processing after a request fails', async () => {
    const queue = createSerialRequestQueue()
    const failed = queue.run(async () => { throw new Error('request failed') })
    const following = queue.run(async () => 'stop sent')

    await expect(failed).rejects.toThrow('request failed')
    await expect(following).resolves.toBe('stop sent')
  })
})

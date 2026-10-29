/** Runs async requests one at a time in enqueue order. */
export function createSerialRequestQueue() {
  let tail: Promise<void> = Promise.resolve()

  return {
    run<T>(request: () => Promise<T>): Promise<T> {
      const result = tail.then(request, request)
      tail = result.then(() => undefined, () => undefined)
      return result
    },
  }
}

import { ref } from 'vue'

/** Tracks a held movement command and sends one stop on release/interruption. */
export function useGimbalMoveStopGuard(sendStop: () => void) {
  const active = ref(false)

  function stop() {
    if (!active.value) return
    active.value = false
    sendStop()
  }

  function onWindowBlur() {
    stop()
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'hidden') stop()
  }

  function attach() {
    window.addEventListener('blur', onWindowBlur)
    document.addEventListener('visibilitychange', onVisibilityChange)
  }

  function dispose() {
    stop()
    window.removeEventListener('blur', onWindowBlur)
    document.removeEventListener('visibilitychange', onVisibilityChange)
  }

  return { active, start: () => {
    if (active.value) return false
    active.value = true
    return true
  }, stop, attach, dispose }
}

import { defineStore } from 'pinia'

import {
  fetchCsrfToken,
  fetchCurrentUser,
  login as requestLogin,
  logout as requestLogout,
} from '@/api/auth'
import type { CurrentUser, LoginPayload } from '@/types/auth'

interface AuthState {
  user: CurrentUser | null
  initialized: boolean
  loading: boolean
}

export const useAuthStore = defineStore('auth', {
  state: (): AuthState => ({
    user: null,
    initialized: false,
    loading: false,
  }),
  getters: {
    isAuthenticated: (state) => state.user !== null,
  },
  actions: {
    expireSession() {
      // Keep recovery journals so reauthentication can resume the same workspace.
      this.user = null
      this.initialized = true
    },
    async initialize(force = false) {
      if (this.initialized && !force) return

      try {
        await fetchCsrfToken()
        this.user = await fetchCurrentUser()
      } catch {
        this.user = null
      } finally {
        this.initialized = true
      }
    },
    async login(payload: LoginPayload) {
      this.loading = true
      try {
        this.user = await requestLogin(payload)
        this.initialized = true
      } finally {
        this.loading = false
      }
    },
    async logout() {
      this.loading = true
      const previousUsername = this.user?.username
      try {
        await requestLogout()
      } catch {
        // Local logout should still complete if the session already expired
        // or CSRF renewal fails during navigation.
      } finally {
        if (previousUsername) {
          try {
            localStorage.removeItem(`voice-p0.active-command.v2:${previousUsername}`)
            localStorage.removeItem(`voice-p0.operation-journal.v1:${previousUsername}`)
            localStorage.removeItem(`voice-p0.presentation-journal.v1:${previousUsername}`)
            sessionStorage.removeItem(`virtual-fleet.runtime-recovery.v1:${previousUsername}`)
            localStorage.removeItem('voice-p0.active-command.v1')
          } catch {
            // Logout still completes when browser storage is unavailable.
          }
        }
        this.user = null
        this.initialized = true
        this.loading = false
      }
    },
  },
})

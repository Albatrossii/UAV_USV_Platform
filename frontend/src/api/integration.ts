import { http } from './http'
import { fetchCsrfToken } from './auth'
import type { ApiResponse } from '@/types/api'

export type IntegrationState = 'ONLINE' | 'RUNNING' | 'STOPPED' | 'OFFLINE' | 'FAILED'

export interface IntegrationHeartbeatPayload {
  componentCode: 'unity-client-01'
  instanceId: string
  state: IntegrationState
  detail: string
  rosConnectionStatus: string
  runtimeScope?: 'SYSTEM_OVERVIEW' | 'MISSION_CENTER' | 'VIRTUAL_FLEET'
  missionId?: number
  runId?: number
  controlsReady?: boolean
  deviceCodes?: string[]
  trajectorySequence?: number
}

export async function sendIntegrationHeartbeat(payload: IntegrationHeartbeatPayload): Promise<void> {
  const csrf = await fetchCsrfToken()
  await http.post<ApiResponse<{ accepted: boolean }>>('/monitoring/unity-heartbeat', payload, {
    headers: {
      [csrf.headerName]: csrf.token,
    },
  })
}

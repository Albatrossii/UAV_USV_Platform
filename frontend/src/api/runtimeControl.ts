import { fetchCsrfToken } from './auth'
import { http } from './http'
import type { ApiResponse } from '@/types/api'
import type { RuntimeControlState } from '@/types/runtimeControl'

export type RuntimeCommandType =
  | 'START'
  | 'STOP'
  | 'TAKEOFF'
  | 'LAND'
  | 'UAV_TAKEOFF'
  | 'UAV_HOVER'
  | 'UAV_RESUME'
  | 'UAV_RETURN'
  | 'UAV_LAND'
  | 'UAV_EMERGENCY_LAND'
  | 'USV_DEPART'
  | 'USV_HOLD'
  | 'USV_RESUME'
  | 'USV_RETURN'
  | 'USV_STOP'
  | 'USV_EMERGENCY_STOP'
  | 'START_MISSION'
  | 'STOP_MISSION'
  | 'PAUSE_MISSION'
  | 'RESUME_MISSION'
  | 'COMPLETE_MISSION'
  | 'FAIL_MISSION'
  | 'CANCEL_MISSION'
  | 'SELECT_DEVICE'
  | 'FOCUS_DEVICE'
  | 'SWITCH_CAMERA'
  | 'TOGGLE_TRAJECTORY'

export type VehicleCommandType =
  | 'UAV_TAKEOFF'
  | 'UAV_HOVER'
  | 'UAV_RESUME'
  | 'UAV_RETURN'
  | 'UAV_LAND'
  | 'UAV_EMERGENCY_LAND'
  | 'USV_DEPART'
  | 'USV_HOLD'
  | 'USV_RESUME'
  | 'USV_RETURN'
  | 'USV_STOP'
  | 'USV_EMERGENCY_STOP'

export interface RuntimeCommandPayload {
  commandType: RuntimeCommandType
  runId?: number
  deviceCode?: string
  payload?: string
  detail?: string
  runtimeScope?: 'SYSTEM_OVERVIEW' | 'MISSION_CENTER'
  runtimeInstanceId?: string
}

export interface RuntimeCommandResult {
  id: number
  commandKey: string
  commandType: RuntimeCommandType
  runtimeScope: 'SYSTEM_OVERVIEW' | 'MISSION_CENTER'
  runtimeInstanceId: string | null
  status: RuntimeCommandStatus
  detail: string
  errorCode: string | null
  acceptedAt: string
}

export interface RuntimeCommandLog {
  id: number
  sessionId: number | null
  runId: number | null
  deviceId: number | null
  commandKey: string
  commandType: RuntimeCommandType
  runtimeScope: 'SYSTEM_OVERVIEW' | 'MISSION_CENTER'
  runtimeInstanceId: string | null
  status: RuntimeCommandStatus
  requestedBy: string
  requestedAt: string
  dispatchedAt: string | null
  acknowledgedAt: string | null
  completedAt: string | null
  detail: string
  errorCode: string | null
}

export type RuntimeCommandStatus =
  | 'PENDING' | 'DISPATCHED' | 'ACCEPTED' | 'EXECUTING'
  | 'SUCCEEDED' | 'REJECTED' | 'FAILED' | 'TIMEOUT' | 'CANCELLED' | 'EXPIRED'

export async function fetchRuntimeControlStatus(): Promise<RuntimeControlState> {
  const response = await http.get<ApiResponse<RuntimeControlState>>('/runtime-control/status')
  return response.data.data
}

export async function fetchRuntimeCommandLogs(params: { runId?: number; limit?: number } = {}): Promise<RuntimeCommandLog[]> {
  const response = await http.get<ApiResponse<RuntimeCommandLog[]>>('/runtime-control/commands/recent', { params })
  return response.data.data
}

export async function startRuntime(): Promise<RuntimeControlState> {
  await fetchCsrfToken()
  const response = await http.post<ApiResponse<RuntimeControlState>>('/runtime-control/start', undefined, {
    timeout: 30000,
  })
  return response.data.data
}

export async function stopRuntime(): Promise<RuntimeControlState> {
  await fetchCsrfToken()
  const response = await http.post<ApiResponse<RuntimeControlState>>('/runtime-control/stop', undefined, {
    timeout: 30000,
  })
  return response.data.data
}

export async function issueRuntimeCommand(payload: RuntimeCommandPayload): Promise<RuntimeCommandResult> {
  console.debug('[runtime-control] issue command payload', payload)
  const csrf = await fetchCsrfToken()
  const response = await http.post<ApiResponse<RuntimeCommandResult>>('/runtime-control/commands', payload, {
    headers: { [csrf.headerName]: csrf.token },
  })
  return response.data.data
}

export async function acknowledgeRuntimeCommand(
  commandKey: string,
  success: boolean,
  detail: string,
): Promise<RuntimeCommandResult> {
  await fetchCsrfToken()
  const response = await http.post<ApiResponse<RuntimeCommandResult>>(
    `/runtime-control/commands/${encodeURIComponent(commandKey)}/ack`,
    { success, detail, errorCode: success ? null : 'UNITY_EXECUTION_FAILED', source: 'UNITY_WEBGL' },
  )
  return response.data.data
}
